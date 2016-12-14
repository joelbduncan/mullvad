#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import platform
import os

openssl_name = 'openssl'
openvpn_name = 'openvpn'
obfsproxy_name = 'obfsproxy'
if platform.system() == 'Windows':
    openssl_name += '.exe'
    openvpn_name += '.exe'
    obfsproxy_name += '.exe'

openssl = openssl_name
openvpn = openvpn_name
obfsproxy = obfsproxy_name
block_udp_plugin = 'block-incoming-udp.dll'
if platform.system() == 'Windows':
    bin_dir = os.path.join('openvpn', 'bin')
    openssl = os.path.join(bin_dir, openssl_name)
    openvpn = os.path.join(bin_dir, openvpn_name)
    block_udp_plugin = os.path.join(bin_dir, block_udp_plugin)
    obfsproxy = os.path.join('obfsproxy', obfsproxy_name)
elif platform.system() == 'Darwin':
    openvpn = './' + openvpn_name
    obfsproxy = './' + obfsproxy_name
