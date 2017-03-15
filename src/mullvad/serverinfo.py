#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals


class ServerInfo(object):
    address = None  # IPv4. Some day IPv6.
    port = None
    protocol = None  # 'udp', 'tcp' or 'obfs2'
    name = None  # e.g. 'server.mullvad.net'
    location = None  # Country code, e.g. 'se'
    cipher = None  # 'bf128' or 'aes256'

    def __init__(self, text=None):
        if text is not None:
            address, port, protocol, name, country, cipher = text.split()
            bytes = [int(byte) for byte in address.split('.')]
            self.address = '%d.%d.%d.%d' % tuple(bytes)
            self.port = int(port)
            assert protocol in ('udp', 'tcp', 'obfs2', None)
            self.protocol = protocol
            self.name = name
            self.location = country
            assert cipher in ('bf128', 'aes256', None)
            self.cipher = cipher

    def __str__(self):
        return '%s %d %s %s %s %s' % \
            (self.address, self.port, self.protocol,
             self.name, self.location, self.cipher)
