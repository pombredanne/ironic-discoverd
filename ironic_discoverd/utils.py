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

import logging
import re

import eventlet
from ironicclient import client
from ironicclient import exceptions
from keystoneclient.v2_0 import client as keystone
import six

from ironic_discoverd import conf


LOG = logging.getLogger('ironic_discoverd.utils')
OS_ARGS = ('os_password', 'os_username', 'os_auth_url', 'os_tenant_name')
RETRY_COUNT = 10
RETRY_DELAY = 2


class DiscoveryFailed(Exception):
    def __init__(self, msg, code=400):
        super(DiscoveryFailed, self).__init__(msg)
        self.http_code = code


def get_client():  # pragma: no cover
    args = dict((k, conf.get('discoverd', k)) for k in OS_ARGS)
    return client.get_client(1, **args)


def get_keystone(token):  # pragma: no cover
    return keystone.Client(token=token, auth_url=conf.get('discoverd',
                                                          'os_auth_url'))


def is_valid_mac(address):
    m = "[0-9a-f]{2}(:[0-9a-f]{2}){5}$"
    return (isinstance(address, six.string_types)
            and re.match(m, address.lower()))


def retry_on_conflict(call, *args, **kwargs):
    for i in range(RETRY_COUNT):
        try:
            return call(*args, **kwargs)
        except exceptions.Conflict as exc:
            LOG.warning('Conflict on calling %s: %s, retry attempt %d',
                        getattr(call, '__name__', repr(call)), exc, i)
            if i == RETRY_COUNT - 1:
                raise
            eventlet.greenthread.sleep(RETRY_DELAY)

    raise RuntimeError('unreachable code')  # pragma: no cover
