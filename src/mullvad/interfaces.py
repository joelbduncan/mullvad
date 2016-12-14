#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import platform
import re

from mullvad import proc
from mullvad import logger


def get_parser():
    if platform.system() == 'Darwin':
        return OSXInterfaces()
    elif platform.system() == 'Linux':
        return LinuxInterfaces()
    else:
        raise OSError('No interfaces implementation for ' + platform.system())


class Interfaces(object):
    def __init__(self):
        pass

    def get_interfaces(self, starts_with=None):
        pass

    def get_loopback_interfaces(self):
        pass

    def get_tunnel_interfaces(self):
        pass


class UnixInterfaces(Interfaces):
    _COMMAND = ['ifconfig']
    _REGEX = re.compile('^(\w+)', re.MULTILINE)

    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)

    def get_interfaces(self, starts_with=None):
        if starts_with is None:
            starts_with = ''
        out = proc.run_assert_ok(self._COMMAND)
        interfaces = self._REGEX.findall(out)
        return [i for i in interfaces if i.startswith(starts_with)]

    def get_loopback_interfaces(self):
        return self.get_interfaces('lo')

    def get_tunnel_interfaces(self):
        return self.get_interfaces('tun')


class OSXInterfaces(UnixInterfaces):
    def __init__(self):
        super(OSXInterfaces, self).__init__()

    def get_tunnel_interfaces(self):
        return self.get_interfaces('utun')


class LinuxInterfaces(UnixInterfaces):
    def __init__(self):
        super(LinuxInterfaces, self).__init__()
