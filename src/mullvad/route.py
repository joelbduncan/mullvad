#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import platform
import re

import netifaces

from mullvad import proc
from mullvad import logger
if platform.system() == 'Windows':
    from mullvad import mwinreg

default_gw_filename = 'defaultgw'


_IPV6_BLOCK_NETS = ['0000::/1', '8000::/1']
_WIN_LO_INTERFACE = 1


def get_route_manager():
    if platform.system() == 'Windows':
        return WindowsRouteManager()
    elif platform.system() == 'Darwin':
        return RouteManager()
    elif platform.system() == 'Linux':
        return RouteManager()
    else:
        raise OSError(
            'No route manager implementation for ' + platform.system())


class WindowsRouteManager(object):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.default_gateways = []
        self.remove_old_format_backup()
        self._load_gateways()
        self.update_default_gateways()

    def update_default_gateways(self):
        """Update the local cache of default routes.

        Scan the routing table for any default routes and update the local
        cache with any routes that was not already stored.
        """
        for gw in self._find_default_gateways():
            if gw not in self.default_gateways:
                self.log.debug(
                    'Found new default gateway: {}, if: {}'.format(
                        gw[0], gw[1]))
                self.default_gateways.append(gw)
        self._save_gateways()

    @staticmethod
    def remove_old_format_backup():
        """Removes any remaining gateway backup file of the old format."""
        if os.path.exists(default_gw_filename):
            invalid = False
            with open(default_gw_filename, 'r') as f:
                for line in f.readlines():
                    if len(line.split()) != 3:
                        invalid = True
                        break
            if invalid:
                os.remove(default_gw_filename)

    def _load_gateways(self):
        """Load default gateways from backup if such a file exists."""
        if os.path.exists(default_gw_filename):
            with open(default_gw_filename, 'r') as f:
                for line in f.readlines():
                    gw, idx, persistent = line.split()
                    self.default_gateways.append((gw, int(idx),
                                                  persistent == 'True'))

    def _save_gateways(self):
        """Write all default gateways kept in memory to a backup file."""
        with open(default_gw_filename, 'w') as f:
            for gw, idx, persistent in self.default_gateways:
                f.write('{} {} {}\n'.format(gw, idx, persistent))

    @staticmethod
    def _get_ip2idx_mapping():
        """Create an IP to Idx mapping.

        Produce a mapping between IP addresses and the interface ID of the
        interface that uses those addresses.
        """
        out = proc.run_assert_ok(
            'netsh interface ipv4 show ipaddresses'.split())
        mapping = {}
        current_interface = 0
        for line in out.splitlines():
            if not line or line.startswith('-'):
                continue
            header_match = re.match(r'^.* (\d+)\s*:(.*)$', line)
            if header_match is not None:
                current_interface = int(header_match.group(1))
            else:
                ip_match = re.match(
                    r'^(\d+)\.(\d+)\.(\d+)\.(\d+)$', line.split()[-1])
                if ip_match is not None:
                    mapping[ip_match.group(0)] = current_interface
        return mapping

    def _find_default_gateways(self):
        """Return a list of default gateways.

        Produce a list of tuples (gw, idx, persistent) where 'gw' is an IP
        address to a gateway, 'idx' is the interface index (Idx) of the
        interface on which 'gw' is reachable and lastly persistent is a
        boolean indicating if the route is persistent or not.
        """
        self.log.debug('Parsing default routes')
        gws = netifaces.gateways().get(netifaces.AF_INET) or []

        ip2idx = self._get_ip2idx_mapping()
        result = []
        for gw, iface_guid, __ in gws:
            iface_ip, dhcp = mwinreg.get_iface_conf(iface_guid, self.log)
            iface_idx = ip2idx.get(iface_ip)
            if iface_ip is not None and iface_idx is not None:
                result.append((gw, iface_idx, not dhcp))
        return result

    def route_add(self, net, mask='255.255.255.255', dest='default'):
        # TODO(simonasker) This method is only kept as long as the old
        # interface needs to be maintained.
        if dest == 'default':
            # TODO(simonasker) Not sure it's sensible to just use the first.
            # Seems to work so far though.
            gw, idx, __ = self.default_gateways[0]
            self.add_route(net, mask, gw, idx)
        elif dest == 'reject':
            self.add_route(net, mask, '0.0.0.0', _WIN_LO_INTERFACE)
        else:
            self.add_route(net, mask, dest)

    def route_del(self, net, mask='255.255.255.255', dest='default'):
        # TODO(simonasker) This method is only kept as long as the old
        # interface needs to be maintained.
        if dest == 'default':
            # TODO(simonasker) Choosing an empty string here will result in all
            # routes to the given net being removed. Just picking the first
            # gw in the list however might not give the correct one.
            self.delete_route(net, mask, '')
        elif dest == 'reject':
            self.delete_route(net, mask, '')
        else:
            self.delete_route(net, mask, dest)

    def add_route(self, destination, mask, gateway, interface=None,
                  persistent=False):
        """Add a route to the routing table.

        Args:
            destination (str): The routes target IP address.
            mask (str): The subnet mask for the route.
            gateway (str): The routes gateway.
            interface (Optional[int]): The interface index for the route.
            persistent (Optional[boolean]): If the route should survive reboots

        """
        command = ['route']
        if persistent:
            command += ['-p']
        command += ['add', destination, 'mask', mask, gateway]
        if interface:
            command += ['if', str(interface)]
        proc.run_assert_ok(command)

    def delete_route(self, destination, mask, gateway, interface=None):
        """Delete a route from the routing table.

        Args:
            destination (str): The routes target IP address.
            mask (str): The subnet mask for the route.
            gateway (str): The routes gateway.
            interface (Optional[int]): The interface index for the route.

        """
        command = 'route delete {} mask {} {}'.format(
            destination, mask, gateway)
        if interface:
            command += ' if {}'.format(interface)
        proc.run_assert_ok(command.split())

    def save_default_gateway(self):
        # TODO(simonasker) This method is only kept as long as the old
        # interface needs to be maintained.
        pass

    def restore_saved_default_gateway(self):
        # TODO(simonasker) This method is only kept as long as the old
        # interface needs to be maintained.
        pass

    def get_default_gateway(self):
        # TODO(simonasker) This method is only kept as long as the old
        # interface needs to be maintained.
        pass

    def delete_default_gateway(self):
        """Remove all default gatways from the routing table."""
        self.update_default_gateways()
        for gw, idx, __ in self.default_gateways:
            self.delete_route('0.0.0.0', '0.0.0.0', gw, idx)

    def restore_default_gateway(self):
        """Restore all stored default gateways to the routing table."""
        for gw, idx, persistent in self.default_gateways:
            self.add_route('0.0.0.0', '0.0.0.0', gw, idx, persistent)
        if os.path.exists(default_gw_filename):
            os.remove(default_gw_filename)

    def block_ipv6(self):
        """Add routes that blocks all IPv6 traffic."""
        command = 'route add {} ::0 if {}'
        for net in _IPV6_BLOCK_NETS:
            proc.run_assert_ok(command.format(net, _WIN_LO_INTERFACE).split())

    def unblock_ipv6(self):
        """Remove routes blocking IPv6 traffic."""
        command = 'route delete {} ::0 if {}'
        for net in _IPV6_BLOCK_NETS:
            proc.run_assert_ok(command.format(net, _WIN_LO_INTERFACE).split())


class RouteManager:
    def __init__(self, gateway=None):
        self.log = logger.create_logger(self.__class__.__name__)
        if gateway is None:
            self.gw = _find_default_gateway()
        else:
            self.gw = gateway

    def _gw(self):
        if self.gw is None:
            self.gw = _find_default_gateway()
        return self.gw

    def _gw_found(self):
        if self._gw() is None:
            self.log.error('Default gateway not found.')
        return self.gw is not None

    def route_add(self, net, mask='255.255.255.255', dest='default'):
        if 'Darwin' in platform.platform():
            flag = ''
            if dest == 'default':
                if not self._gw_found():
                    return
                dst = self._gw()
            elif dest == 'reject':
                dst = '127.0.0.1'
                flag = '-reject'
            else:
                dst = sanitise_addr(dest)
            command = 'route add -net %s %s %s %s' % (net, dst, mask, flag)
        else:
            if dest == 'default':
                if not self._gw_found():
                    return
                dst = 'gw ' + self._gw()
            elif dest == 'reject':
                dst = 'reject'
            else:
                dst = 'gw ' + sanitise_addr(dest)
            command = 'route add -net %s netmask %s %s' % (net, mask, dst)
        self._run_route_add(command.split())

    def route_del(self, net, mask='255.255.255.255', dest='default'):
        if 'Darwin' in platform.platform():
            flag = ''
            if dest == 'default':
                if not self._gw_found():
                    return
                dst = self._gw()
            elif dest == 'reject':
                dst = '127.0.0.1'
                flag = '-reject'
            else:
                dst = sanitise_addr(dest)
            command = 'route delete -net %s %s %s %s' % (net, dst, mask, flag)
        else:
            if dest == 'default':
                if not self._gw_found():
                    return
                dst = 'gw ' + self._gw()
            elif dest == 'reject':
                dst = 'reject'
            else:
                dst = 'gw ' + sanitise_addr(dest)
            command = 'route del -net %s netmask %s %s' % (net, mask, dst)
        self._run_route_del(command.split())

    def save_default_gateway(self):
        if self._gw() is None:
            self.log.warning('Gateway not found')
        else:
            with open(default_gw_filename, 'w') as f:
                f.write(self._gw())

    def restore_saved_default_gateway(self):
        try:
            with open(default_gw_filename) as f:
                gw = f.read()
        except IOError, e:
            self.log.warning(str(e))
        else:
            self.route_add('0.0.0.0', '0.0.0.0', gw)

    def get_default_gateway(self):
        return self._gw()

    def delete_default_gateway(self):
        self.save_default_gateway()
        self.route_del('0.0.0.0', '0.0.0.0')

    def restore_default_gateway(self):
        self.route_add('0.0.0.0', '0.0.0.0')

    def block_ipv6(self):
        if 'Darwin' in platform.platform():
            command = 'route add -inet6 -net {} ::1 -reject'
            # Only blocks the first half of the address space which includes
            # the Global Unicast addresses
            proc.run_assert_ok(command.format('0000::/1').split())
        else:
            command = 'route -6 add {} dev lo'
            for net in _IPV6_BLOCK_NETS:
                self._run_route_add(command.format(net).split())

    def unblock_ipv6(self):
        if 'Darwin' in platform.platform():
            command = 'route delete -inet6 -net {} ::1 -reject'
            proc.run_assert_ok(command.format('0000::/1').split())
        else:
            command = 'route -6 del {} dev lo'
            for net in _IPV6_BLOCK_NETS:
                self._run_route_del(command.format(net).split())

    def _run_route_add(self, command):
        if platform.system() == 'Linux':
            # TODO(linus) make sure this works. Sadly we can't rely on
            # exit code or output. Maybe get a routing lib for python?
            proc.run(command)
        else:
            proc.run_assert_ok(command)

    def _run_route_del(self, command):
        if platform.system() == 'Linux':
            # TODO(linus) Same as _run_route_add, better handling.
            proc.run(command)
        else:
            proc.run_assert_ok(command)


def _find_default_gateway_mac(routing_table):
    for line in routing_table.split('\n'):
        columns = line.split()
        if len(columns) >= 2 and columns[0] == 'default':
            return columns[1]
    return None


def _find_default_gateway_linux(routing_table):
    for line in routing_table.split('\n'):
        columns = line.split()
        if len(columns) >= 3 and \
                columns[0] == '0.0.0.0' and \
                columns[2] == '0.0.0.0':
            return columns[1]
    return None


def _find_default_gateway():
    routing_table = proc.run_assert_ok(['netstat', '-r', '-n'])
    if 'Darwin' in platform.platform():
        return _find_default_gateway_mac(routing_table)
    else:
        return _find_default_gateway_linux(routing_table)


def sanitise_addr(ip_addr):
    quad = [int(byte) for byte in ip_addr.split('.')]
    return '%d.%d.%d.%d' % tuple(quad)


if __name__ == '__main__':
    rm = RouteManager()
    rm.delete_default_gateway()
    rm.restore_default_gateway()
