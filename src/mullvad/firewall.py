#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import platform

import ipaddr
import netifaces

from mullvad import interfaces
from mullvad import logger
from mullvad import osx_net_services
from mullvad import proc
from mullvad import util


_PRIVATE_NETS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']


def get_firewall():
    if platform.system() == 'Windows':
        return WindowsFirewall()
    elif platform.system() == 'Darwin':
        return OSXFirewall()
    elif platform.system() == 'Linux':
        return LinuxFirewall()
    else:
        raise OSError('No firewall implementation for ' + platform.system())


def _cidr_notation(address, netmask):
    """Given an IPv4 address and a netmask in dotted notation, returns the
    IP range in CIDR notation.

    Examples:
        >>> _cidr_notation('192.168.1.0', '255.255.255.0')
        '192.168.1.0/24'
    """
    return str(ipaddr.IPv4Network(
        '{}/{}'.format(address, netmask)).masked())


class FirewallError(Exception):
    pass


class Firewall(object):
    def __init__(self):
        pass

    def set_allowed_ip(self, ip):
        pass

    def block_local_network(self):
        pass

    def unblock_local_network(self):
        pass

    def block_incoming_udp(self):
        pass

    def unblock_incoming_udp(self):
        pass

    def block_ipv6(self):
        pass

    def unblock_ipv6(self):
        pass


class WindowsFirewall(Firewall):
    def __init__(self):
        try:
            # Make sure firewall is enabled
            self.run_command('set currentprofile state on'.split())
        except RuntimeError as e:
            raise FirewallError(e)
        # Ublock local subnet initially
        self.unblock_local_network()

    def _find_local_subnets(self):
        """List all local IPv4 subnets except for loopback addresses."""
        subnets = []
        for iface in netifaces.interfaces():
            ifaddresses = netifaces.ifaddresses(iface)
            ipv4_addresses = ifaddresses.get(netifaces.AF_INET)
            for ipv4_address in ipv4_addresses or []:
                addr = ipv4_address.get('addr')
                netmask = ipv4_address.get('netmask')
                if addr and netmask:
                    subnets.append(_cidr_notation(addr, netmask))
        return [sn for sn in subnets if not sn.startswith('127')]

    def set_allowed_ip(self, ip):
        pass

    def block_local_network(self):
        subnets = self._find_local_subnets()
        self.add_rule('mullvad', 'out', ','.join(subnets), 'block')

    def unblock_local_network(self):
        self.delete_rule('mullvad')

    def block_incoming_udp(self):
        pass

    def unblock_incoming_udp(self):
        pass

    def block_ipv6(self):
        self.add_rule('BlockIPv6_low', 'out', '0000::/1', 'block')
        self.add_rule('BlockIPv6_high', 'out', '8000::/1', 'block')

    def unblock_ipv6(self):
        self.delete_rule('BlockIPv6_low')
        self.delete_rule('BlockIPv6_high')

    def add_rule(self, name, direction, remoteip, action):
        command = ['add', 'rule',
                   'name="%s"' % name,
                   'dir=%s' % direction,
                   'remoteip=%s' % remoteip,
                   'action=%s' % action]
        self.run_firewall_command(command)

    def delete_rule(self, name):
        command = ['delete', 'rule', 'name="{}"'.format(name)]
        self.run_firewall_command(command, must_work=False)

    def run_firewall_command(self, command, must_work=True):
        self.run_command(['firewall'] + command, must_work)

    def run_command(self, command, must_work=True):
        full_command = ['netsh', 'advfirewall'] + command
        code, out, __ = proc.run(full_command)
        if code != 0 and must_work:
            raise RuntimeError('{} gives stdout: {}'.format(full_command, out))


class OSXFirewall(Firewall):
    pf_conf_file = '/etc/pf.conf'

    def __init__(self):
        try:
            self.pfctl = PFCtl(self.pf_conf_file)
            self.pfconf = PFConf(self.pf_conf_file)
        except (OSError, RuntimeError) as e:
            raise FirewallError(e)
        self._init_anchor()
        self.pfctl.enable()

    def _init_anchor(self):
        """Initialize the firewall, on Mac this means adding a 'mullvad'
        anchor to the pf firewall rules in /etc/pf.conf and flushing
        the firewall rules with these new settings"""
        if not self.pfconf.has_mullvad_anchor():
            self.pfconf.insert_mullvad_anchor()
        self.pfctl.flush_pf_conf()

    def set_allowed_ip(self, ip):
        self.pfctl.set_allowed_ip(ip)

    def block_local_network(self):
        self.pfctl.block_traffic()

    def unblock_local_network(self):
        self.pfctl.unblock_traffic()

    def block_incoming_udp(self):
        self.pfctl.block_incoming_udp()

    def unblock_incoming_udp(self):
        self.pfctl.unblock_incoming_udp()

    def block_ipv6(self):
        for ns in osx_net_services.get_services():
            proc.run_assert_ok(['networksetup', '-setv6off', ns])

    def unblock_ipv6(self):
        for ns in osx_net_services.get_services():
            proc.run_assert_ok(['networksetup', '-setv6automatic', ns])


class LinuxFirewall(Firewall):
    _CHAIN_NAME = 'MULLVAD'
    iptables = ['iptables']
    ip6tables = ['ip6tables']

    # To wait for lock, stops random crashing
    # Available from iptables 1.4.20 and up
    iptables_wait_flag = '-w'

    _IPV6_MAC_IF_PATH = '/proc/net/if_inet6'
    _IPV6_DISABLE_PATH = '/proc/sys/net/ipv6/conf/all/disable_ipv6'

    _BLOCK_TRAFFIC_RULE = [_CHAIN_NAME, '-j', 'REJECT']
    _BLOCK_INCOMING_UDP_RULE = [_CHAIN_NAME, '-p', 'udp', '-j', 'DROP']

    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.has_ipv6 = self._has_ipv6()
        # Give the instance its own copies of the class variables to avoid
        # modifying the shared instance when adding the wait flag
        self.iptables = list(self.iptables)
        self.ip6tables = list(self.ip6tables)
        self.allowed_ip = None
        self.block_traffic_state = False
        self.block_ipv6_state = False
        try:
            self._check_commands()
            if self._supports_wait_flag():
                self.iptables.append(self.iptables_wait_flag)
                self.ip6tables.append(self.iptables_wait_flag)
            self._setup_chain()
        except OSError as e:
            raise FirewallError(e)

    def _check_commands(self):
        proc.run(self.iptables)
        if self.has_ipv6:
            proc.run(self.ip6tables)

    def _setup_chain(self):
        chain = LinuxFirewall._CHAIN_NAME
        cmds = [self.iptables]
        if self.has_ipv6:
            cmds.append(self.ip6tables)
        for cmd in cmds:
            if proc.run_get_exit(cmd + ['-F', chain]) != 0:  # Flush
                proc.run_assert_ok(cmd + ['-N', chain])  # Create new
            for default_chain in ['INPUT', 'FORWARD', 'OUTPUT']:
                while proc.run_get_exit(cmd + ['-D', default_chain,
                                        '-j', chain]) == 0:
                    pass
                proc.run_assert_ok(cmd + ['-I', default_chain,
                                          '-j', chain])
            default_allow_rules = [
                # Allow traffic within loopback interfaces
                '-i lo+ -j ACCEPT',
                '-o lo+ -j ACCEPT',
                # Allow traffic within VPN tunnels
                '-i tun+ -j ACCEPT',
                '-o tun+ -j ACCEPT',
            ]
            for rule in default_allow_rules:
                proc.run_assert_ok(cmd + ['-A', chain] + rule.split())

    def set_allowed_ip(self, ip):
        old = self.allowed_ip
        self.allowed_ip = ip
        if old == ip:
            return

        rule = '{} MULLVAD {} {} -j ACCEPT'
        for direction in ['-s', '-d']:
            if old is not None:
                self._run_iptables_until_fail(
                    rule.format('-D', direction, old).split(),
                    skip_ipv6=True)
            if ip is not None:
                self._run_iptables(rule.format('-I', direction, ip).split(),
                                   skip_ipv6=True)

    def block_local_network(self):
        self.block_traffic_state = True
        self._run_iptables(['-A'] + LinuxFirewall._BLOCK_TRAFFIC_RULE)

    def unblock_local_network(self):
        self.block_traffic_state = False
        self._run_iptables_until_fail(
            ['-D'] + LinuxFirewall._BLOCK_TRAFFIC_RULE,
            skip_ipv6=self.block_ipv6_state)

    def block_incoming_udp(self):
        self._run_iptables(['-A'] + LinuxFirewall._BLOCK_INCOMING_UDP_RULE)

    def unblock_incoming_udp(self):
        self._run_iptables_until_fail(
            ['-D'] + LinuxFirewall._BLOCK_INCOMING_UDP_RULE)

    def block_ipv6(self):
        self.block_ipv6_state = True
        if self.has_ipv6:
            proc.run_assert_ok(self.ip6tables + ['-A'] +
                               LinuxFirewall._BLOCK_TRAFFIC_RULE)

    def unblock_ipv6(self):
        self.block_ipv6_state = False
        if self.has_ipv6 and not self.block_traffic_state:
            while proc.run_get_exit(self.ip6tables + ['-D'] +
                                    LinuxFirewall._BLOCK_TRAFFIC_RULE) == 0:
                pass

    def _run_iptables(self, args, skip_ipv6=False):
        proc.run_assert_ok(self.iptables + args)
        if not skip_ipv6 and self.has_ipv6:
            proc.run_assert_ok(self.ip6tables + args)

    def _run_iptables_until_fail(self, args, skip_ipv6=False):
        while proc.run_get_exit(self.iptables + args) == 0:
            pass
        if not skip_ipv6 and self.has_ipv6:
            while proc.run_get_exit(self.ip6tables + args) == 0:
                pass

    def _has_ipv6(self):
        """Checks if the kernel has IPv6 activated.
        """
        iface_content = util.file_content(LinuxFirewall._IPV6_MAC_IF_PATH)
        if iface_content is not None and len(iface_content.strip()) > 0:
            return True
        disable_content = util.file_content(LinuxFirewall._IPV6_DISABLE_PATH)
        if disable_content is not None and '0' in disable_content:
            return True
        self.log.info('Found no IPv6 in kernel, not going to block')
        return False

    def _supports_wait_flag(self):
        try:
            exit = proc.run_get_exit(
                self.iptables + [self.iptables_wait_flag, '-L'])
        except OSError:
            return False
        else:
            return exit == 0


class PFCtl:
    """OSXFirewall helper class. Interface to the pfctl command"""
    _PASS_IFACE_TEMPLATE = 'pass quick on {}'
    _PASS_IP_TEMPLATE = 'pass out quick from any to {}'
    # Something in osx 10.7.5 require us to use block drop, not return.
    _BLOCK_ALL_RULE = 'block drop all'
    _PASS_IN_LOCAL_UDP_TEMPLATE = 'pass in quick proto udp from {}'
    _BLOCK_INCOMING_UDP_RULE = 'block drop in quick proto udp'

    pfctl = '/sbin/pfctl'
    pfctl_enable = '-e'
    pfctl_disable = '-d'
    pfctl_flush = '-f'
    pfctl_test_permission = '-sr'
    pfctl_update_mullvad_rules = ['-a', 'mullvad', '-f', '-']

    def __init__(self, pf_conf_file):
        if not os.path.exists(pf_conf_file):
            raise OSError('No pf config at {}'.format(pf_conf_file))
        proc.run_assert_ok([self.pfctl, self.pfctl_test_permission])
        self.pf_conf_file = pf_conf_file
        self.interfaces = interfaces.get_parser()
        self.allowed_ip = None
        self.block_incoming_udp_state = False
        self.block_traffic_state = False

    def enable(self):
        """Enable packet filtering."""
        proc.run([self.pfctl, self.pfctl_enable])

    def set_allowed_ip(self, ip):
        self.allowed_ip = ip
        self._apply_rules_to_pf()

    def unblock_traffic(self):
        """Clears the "mullvad" anchor to let traffic pass as usual"""
        self.block_traffic_state = False
        self._apply_rules_to_pf()

    def block_traffic(self):
        """Blocks ALL traffic in and out from this computer,
        except to IPs in the argument"""
        self.block_traffic_state = True
        self._apply_rules_to_pf()

    def block_incoming_udp(self):
        self.block_incoming_udp_state = True
        self._apply_rules_to_pf()

    def unblock_incoming_udp(self):
        self.block_incoming_udp_state = False
        self._apply_rules_to_pf()

    def _apply_rules_to_pf(self):
        """Compute and apply the correct pf rules."""
        if self.block_traffic_state or self.block_incoming_udp_state:
            rules = []
            for iface in self.interfaces.get_loopback_interfaces():
                rules.append(self._PASS_IFACE_TEMPLATE.format(iface))
            for iface in self.interfaces.get_tunnel_interfaces():
                rules.append(self._PASS_IFACE_TEMPLATE.format(iface))
            if self.allowed_ip is not None:
                rules.append(self._PASS_IP_TEMPLATE.format(self.allowed_ip))

            if self.block_traffic_state:
                rules.append(self._BLOCK_ALL_RULE)
            elif self.block_incoming_udp_state:
                for net in _PRIVATE_NETS:
                    rules.append(self._PASS_IN_LOCAL_UDP_TEMPLATE.format(net))
                rules.append(self._BLOCK_INCOMING_UDP_RULE)

            rules_str = '\n'.join(rules) + '\n'  # Need end \n
            proc.run_assert_ok([self.pfctl] + self.pfctl_update_mullvad_rules,
                               rules_str)
        else:
            # No block is active, clear all rules
            proc.run_assert_ok([self.pfctl] + self.pfctl_update_mullvad_rules)

    def flush_pf_conf(self):
        """Make pf flush its rules and re-read them from the config"""
        proc.run_assert_ok([self.pfctl, self.pfctl_flush, self.pf_conf_file])


class PFConf:
    """OSXFirewall helper class. Manipulate the pf config file"""
    anchor_rule = 'anchor mullvad'

    def __init__(self, pf_conf_file):
        if not os.access(pf_conf_file, os.W_OK):
            raise OSError('Unable to manipulate pf config')
        self.pf_conf_file = pf_conf_file

    def has_mullvad_anchor(self):
        """Checks if pf has the mullvad anchor in its config
        Deletes duplicates if found (bug in version 50 of the client)"""
        file_lines = []
        strip_blank = False  # Strip blanks between our anchors
        num_found = 0
        with open(self.pf_conf_file, 'r') as f:
            for line in f:
                if line.strip() == self.anchor_rule:
                    num_found += 1
                    strip_blank = True
                    if num_found > 1:
                        continue
                elif line.strip():
                    strip_blank = False
                if line.strip() or not strip_blank:
                    file_lines.append(line)
        if num_found > 1:
            self._write_clean_pf_conf(file_lines)
        return num_found > 0

    def insert_mullvad_anchor(self):
        """Inserts the mullvad anchor into the pf config"""
        with open(self.pf_conf_file, 'a') as f:
            f.write('\n' + self.anchor_rule + '\n')

    def _write_clean_pf_conf(self, file_lines):
        with open(self.pf_conf_file, 'w') as f:
            f.writelines(file_lines)
