#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import platform

if platform.system() != 'Windows':
    raise OSError('mwinreg can only be imported on Windows')

import _winreg

from mullvad import logger

_WIN10_TAP_KEY_BASE = _winreg.HKEY_LOCAL_MACHINE
_WIN10_TAP_KEY_PARENT = (r'SYSTEM\CurrentControlSet\Control'
                         '\Class\{4d36e972-e325-11ce-bfc1-08002be10318}')
_WIN10_TAP_VALUE_NAMES = ['MatchingDeviceID', 'ComponentId']
_WIN10_TAP_VALUE = 'tap0901'

_IFACE_PARAM_BASE = _winreg.HKEY_LOCAL_MACHINE
_IFACE_PARAM_PARENT = (r'SYSTEM\CurrentControlSet\services'
                       '\Tcpip\Parameters\Interfaces')
_IFACE_PARAM_STATIC_IP = 'IPAddress'
_IFACE_PARAM_DHCP_IP = 'DhcpIPAddress'


class WinReg(object):
    def __init__(self, key_base, sub_key):
        """Windows registry manipulating class.

        Args:
            key_base: A base key of the windows registry, for example:
                      _winreg.HKEY_CURRENT_USER
            sub_key: A string that is the key location, for example:
                     'Software\Microsoft\Windows\CurrentVersion\Run'
        """
        self.log = logger.create_logger(self.__class__.__name__)
        self.key_base = key_base
        self.sub_key = sub_key
        try:
            self.key_handle = _winreg.OpenKey(
                key_base, sub_key, 0,
                _winreg.KEY_QUERY_VALUE |
                _winreg.KEY_SET_VALUE |
                _winreg.KEY_ENUMERATE_SUB_KEYS)
        except WindowsError as e:
            err = unicode(str(e), errors='replace')
            self.log.error('Unable to open registry key %s, because: %s',
                           self, err)
            raise e

    def get(self, value_name):
        """Read the value and type of a registry key.

        Args:
            value_name: A string that is the value name to fetch the value for
                        in the given key.

        Returns:
            A two value tuple with the first element being the value of the
            registry entry and the second element being the value type
        """
        try:
            value, value_type = _winreg.QueryValueEx(self.key_handle,
                                                     value_name)
            return (value, value_type)
        except WindowsError as e:
            err = unicode(str(e), errors='replace')
            self.log.error('Failed to read registry value: %s, because: %s',
                           self, err)
            raise e

    def set(self, value_name, value_type, value):
        """Write and flush a value to a registry key.

        Args:
            value_name: A string that is the value name to fetch the value for
                        in the given key.
            value_type: The type of the value to write. Can be obtained from
                        WinReg.read or _winreg.KEY_*
            value: The value to be written
        """
        try:
            _winreg.SetValueEx(self.key_handle, value_name,
                               0, value_type, value)
            _winreg.FlushKey(self.key_handle)
        except WindowsError as e:
            err = unicode(str(e), errors='replace')
            self.log.error('Failed to set registry value: %s, because: %s',
                           self, err)
            raise e

    def list_subkeys(self):
        """List all subkeys of this key.
        """
        subkeys = []
        num_subkeys, __, __ = _winreg.QueryInfoKey(self.key_handle)
        for i in range(num_subkeys):
            subkey = _winreg.EnumKey(self.key_handle, i)
            subkeys.append(subkey)
        return subkeys

    def __str__(self):
        return concat_keys([self.key_base, self.sub_key])


def concat_keys(parts):
    return '\\'.join([str(part) for part in parts])


def fix_win10_tap(log):
    """Fix the bug in Windows 10 TAP driver bug.

    Does nothing on platforms where the problem has already been fixed.
    """
    try:
        iface_key_parent = WinReg(_WIN10_TAP_KEY_BASE,
                                  _WIN10_TAP_KEY_PARENT)
        for subkey in iface_key_parent.list_subkeys():
            try:
                int(subkey)
            except ValueError:
                continue  # Skip subkeys not on the form 0001, 0002 etc
            iface_key_path = concat_keys([_WIN10_TAP_KEY_PARENT, subkey])
            tap_key = WinReg(_WIN10_TAP_KEY_BASE, iface_key_path)
            for value_name in _WIN10_TAP_VALUE_NAMES:
                value, value_type = tap_key.get(value_name)
                if value != _WIN10_TAP_VALUE and \
                   value.lower() == _WIN10_TAP_VALUE:
                    log.info('Win10: Fixing registry value for TAP '
                             'devices. Rewriting %s',
                             concat_keys([tap_key, value_name]))
                    tap_key.set(value_name, value_type, _WIN10_TAP_VALUE)
    except WindowsError as e:
        err = unicode(str(e), errors='replace')
        log.debug('Unable to fix win10 tap registry bug. Probably not'
                  ' needed: %s', err)


def get_iface_conf(iface_guid, log):
    """Returns a tuple (ip, dhcp) for the given interface.
    """
    iface_reg_path = concat_keys([_IFACE_PARAM_PARENT, iface_guid])
    iface_reg = WinReg(_IFACE_PARAM_BASE, iface_reg_path)
    try:
        ip, __ = iface_reg.get(_IFACE_PARAM_DHCP_IP)
        assert len(ip) > 0
        dhcp = True
    except (WindowsError, AssertionError) as e:
        log.error('Unable to get dynamic IP for iface %s: %s',
                  iface_guid, str(e))
        try:
            ip, __ = iface_reg.get(_IFACE_PARAM_STATIC_IP)
            # REG_MULTI_SZ is a list, get first IP
            ip = ip[0]
            dhcp = False
        except (WindowsError, IndexError) as e:
            log.error('Unable to get static IP for iface %s: %s',
                      iface_guid, str(e))
            ip, dhcp = (None, None)
    return (ip, dhcp)
