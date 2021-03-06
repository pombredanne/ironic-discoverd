# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Handling discovery request."""

import logging
import time

import eventlet
from ironicclient import exceptions

from ironic_discoverd import conf
from ironic_discoverd import firewall
from ironic_discoverd import node_cache
from ironic_discoverd import utils


LOG = logging.getLogger("ironic_discoverd.discover")


def discover(uuids):
    """Initiate discovery for given node uuids."""
    if not uuids:
        raise utils.DiscoveryFailed("No nodes to discover")

    ironic = utils.get_client()
    LOG.debug('Validating nodes %s', uuids)
    nodes = []
    for uuid in uuids:
        try:
            node = ironic.node.get(uuid)
        except exceptions.NotFound:
            LOG.error('Node %s cannot be found', uuid)
            raise utils.DiscoveryFailed("Cannot find node %s" % uuid, code=404)
        except exceptions.HttpError as exc:
            LOG.exception('Cannot get node %s', uuid)
            raise utils.DiscoveryFailed("Cannot get node %s: %s" % (uuid, exc))

        _validate(ironic, node)
        nodes.append(node)

    LOG.info('Proceeding with discovery on node(s) %s',
             [n.uuid for n in nodes])
    for node in nodes:
        eventlet.greenthread.spawn_n(_background_start_discover, ironic, node)


def _validate(ironic, node):
    if node.instance_uuid:
        LOG.error('Refusing to discover node %s with assigned instance_uuid',
                  node.uuid)
        raise utils.DiscoveryFailed(
            'Refusing to discover node %s with assigned instance uuid' %
            node.uuid)

    power_state = node.power_state
    if (not node.maintenance and power_state is not None
            and power_state.lower() != 'power off'):
        LOG.error('Refusing to discover node %s with power_state "%s" '
                  'and maintenance mode off',
                  node.uuid, power_state)
        raise utils.DiscoveryFailed(
            'Refusing to discover node %s with power state "%s" and '
            'maintenance mode off' %
            (node.uuid, power_state))

    if not node.extra.get('ipmi_setup_credentials'):
        validation = utils.retry_on_conflict(ironic.node.validate, node.uuid)
        if not validation.power['result']:
            LOG.error('Failed validation of power interface for node %s, '
                      'reason: %s', node.uuid, validation.power['reason'])
            raise utils.DiscoveryFailed(
                'Failed validation of power interface for node %s' % node.uuid)


def _background_start_discover(ironic, node):
    patch = [{'op': 'add', 'path': '/extra/on_discovery', 'value': 'true'},
             {'op': 'add', 'path': '/extra/discovery_timestamp',
              'value': str(time.time())}]
    ironic.node.update(node.uuid, patch)

    # TODO(dtantsur): pagination
    macs = [p.address for p in ironic.node.list_ports(node.uuid, limit=0)]
    node_cache.add_node(node.uuid,
                        bmc_address=node.driver_info.get('ipmi_address'),
                        mac=macs)

    if macs:
        LOG.info('Whitelisting MAC\'s %s for node %s on the firewall',
                 macs, node.uuid)
        firewall.update_filters(ironic)

    if not node.extra.get('ipmi_setup_credentials'):
        try:
            utils.retry_on_conflict(ironic.node.set_boot_device,
                                    node.uuid, 'pxe', persistent=False)
        except Exception as exc:
            LOG.warning('Failed to set boot device to PXE for node %s: %s',
                        node.uuid, exc)

        try:
            utils.retry_on_conflict(ironic.node.set_power_state,
                                    node.uuid, 'reboot')
        except Exception as exc:
            LOG.error('Failed to power on node %s, check it\'s power '
                      'management configuration:\n%s', node.uuid, exc)
    else:
        LOG.info('Discovery environment is ready for node %s, '
                 'manual power on is required within %d seconds',
                 node.uuid, conf.getint('discoverd', 'timeout'))
