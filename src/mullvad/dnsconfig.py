#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import pickle
import platform
import re

from mullvad import logger
from mullvad import osx_net_services
from mullvad import proc


_DEFAULT_DNS_PICKLE_PATH = './dnsconfig.pickle'
_DEFAULT_SAVED_RESOLV_LINK_PATH = '/etc/resolv.conf.pre-mullvad'
_DEFAULT_RESOLV_CONF_PATH = '/etc/resolv.conf'


def get_dnsconfig():
    if platform.system() == 'Windows':
        return WindowsDNSConfig()
    elif platform.system() == 'Darwin':
        return OSXDNSConfig()
    elif platform.system() == 'Linux':
        return LinuxDNSConfig()
    else:
        raise OSError('No DNSConfig implementation for ' + platform.system())


class DNSConfig(object):
    def __init__(self):
        pass

    def save(self):
        """Save the current DNS configuration.

        Store the current DNS configuration in a file in order to be able to
        restore it when needed. Will only create the file if no such file
        already exists.
        """
        pass

    def restore(self):
        """Restore the original DNS configuration.

        Load the contents of the file created by save() (if one exists) which
        contains the hosts original DNS settings and use its contents to
        restore the DNS configuration.
        """
        pass

    def set(self, servers):
        """Configure the host to use the given list of DNS servers.

        The list may contain both IPv4 and IPv6 addresses which in the case of
        a host running Windows, will be configured separately.
        """
        pass


class WindowsDNSConfig(DNSConfig):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.dns_pickle_path = _DEFAULT_DNS_PICKLE_PATH
        self.ipv4_re = '((\d{1,3}\.){3}\d{1,3})'
        self.ipv6_re = '(([0-9a-f]{0,4}:)+[0-9a-f]{1,4})(%\d+)?'

        self.ip_regex = {
            'ip': self.ipv4_re,
            'ipv4': self.ipv4_re,
            'ipv6': self.ipv6_re,
        }

    def save(self):
        if not os.path.exists(self.dns_pickle_path):
            self.log.debug('Saving DNS settings')
            dns_state = {}
            dns_state['ipv4'] = self._win_get_dns_config('ipv4')
            dns_state['ipv6'] = self._win_get_dns_config('ipv6')
            with open(self.dns_pickle_path, 'wb') as f:
                pickle.dump(dns_state, f)

    def restore(self):
        if os.path.exists(self.dns_pickle_path):
            self.log.debug('Restoring DNS settings')
            with open(self.dns_pickle_path, 'rb') as f:
                dns_state = pickle.load(f)
                for ipv, config in dns_state.items():
                    self._win_set_dns_config(ipv, config)
            os.remove(self.dns_pickle_path)

    def set(self, servers):
        self.log.debug('Settings DNS servers to {}'.format(str(servers)))
        ipv4_servers = [s for s in servers if re.match(self.ipv4_re, s)]
        ipv6_servers = [s for s in servers if re.match(self.ipv6_re, s)]

        self._win_set_dns_servers('ipv4', ipv4_servers)
        self._win_set_dns_servers('ipv6', ipv6_servers)

    def _win_get_dns_config(self, ipv):
        """Return the DNS configuration for the given protocol family.

        Create a dictionary for the given IP version containing the hosts
        configured DNS servers for each interface supporting that version. Each
        entry will contain the type of DNS configuration for that interface
        (dhcp or static) as well as a list of the DNS servers which the
        interface is configured to use.
        """

        interfaces = self._win_get_interfaces(ipv)

        confs = {}
        for ifc in interfaces:
            command = u'netsh interface {0} show dns name={1}'.format(ipv, ifc)
            out = proc.run_assert_ok(command.split())

            conf = {'source': u'static', 'servers': []}
            for line in out.splitlines():
                if u'DHCP' in line:
                    conf['source'] = u'dhcp'

                match = re.search(self.ip_regex[ipv], line)
                if match is not None:
                    conf['servers'].append(match.group(1))

            confs[ifc] = conf

        return confs

    def _win_set_dns_config(self, ipv, config):
        """Configure the hosts DNS settings.

        Set the DNS configuration for the given IP version to match that which
        is given in the 'config' argument which is a dictionary of the same
        structure as the one returned by _win_get_dns_config().
        """
        commands = []
        for ifc, conf in config.items():
            if conf['source'] == u'dhcp':
                cmd = ('netsh interface {} set dns name={} '
                       'source=dhcp validate=no')
                commands.append(cmd.format(ipv, ifc))
            elif conf['source'] == u'static':
                cmd = ('netsh interface {} {} dns name={} '
                       'source=static addr={} validate=no')
                if len(conf['servers']) == 0:
                    commands.append(cmd.format(ipv, 'set', ifc, 'none'))
                else:
                    commands.append(
                        cmd.format(ipv, 'set', ifc, conf['servers'][0]))

                    cmd = ('netsh interface {} {} dns name={} '
                           'addr={} validate=no')
                    for server in conf['servers'][1:]:
                        commands.append(cmd.format(ipv, 'add', ifc, server))

        for command in commands:
            try:
                proc.run_assert_ok(command.split())
            except RuntimeError as e:
                # If the "DNS Client" service is off setting the dns will work,
                # but netsh will still quit with exit code 1
                self.log.warning('Setting DNS via netsh gave an error: %s', e)

    def _win_set_dns_servers(self, ipv, servers):
        """Configure the interfaces supporting the given IP version to use the
        given list of IP addresses as its DNS servers.
        """
        config = {}
        for ifc in self._win_get_interfaces(ipv):
            config[ifc] = {'source': u'static', 'servers': servers}
        self._win_set_dns_config(ipv, config)

    def _win_get_interfaces(self, ipv):
        """
        Get the index num of all interfaces supporting the given IP version.
        """
        ret = []
        command = u'netsh interface {} show interfaces'.format(ipv)
        out = proc.run_assert_ok(command.split())
        for line in out.splitlines():
            if 'isatap' not in line and 'Teredo' not in line:
                try:
                    if_idx = int(line.split()[0])
                except (IndexError, ValueError):
                    continue
                if if_idx != 1:
                    ret.append(if_idx)
        return ret


class OSXDNSConfig(DNSConfig):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.dns_pickle_path = _DEFAULT_DNS_PICKLE_PATH

    def save(self):
        if not os.path.exists(self.dns_pickle_path):
            self.log.debug('Saving DNS settings')
            dns_state = self._osx_get_config()
            with open(self.dns_pickle_path, 'wb') as f:
                pickle.dump(dns_state, f)

    def restore(self):
        if os.path.exists(self.dns_pickle_path):
            self.log.debug('Restoring DNS settings')
            with open(self.dns_pickle_path, 'rb') as f:
                dns_state = pickle.load(f)
            self._osx_set_config(dns_state)
            os.remove(self.dns_pickle_path)

    def set(self, servers):
        self.log.debug('Settings DNS servers to {}'.format(str(servers)))
        for service in osx_net_services.get_services():
            command = ['networksetup', '-setdnsservers', service]
            command += servers if len(servers) > 0 else ['empty']
            proc.run_assert_ok(command)

    def _osx_get_dns_servers(self, service):
        """
        Return a list of the DNS servers used by the given network service.
        """
        out = proc.run_assert_ok(['networksetup', '-getdnsservers', service])
        if 'DNS' in out:
            return ['empty']
        else:
            servers = out.splitlines()
            return [s for s in servers if 'currently disabled' not in s]

    def _osx_get_config(self):
        """Return the DNS configuration for all active network services."""
        confs = {}
        for service in osx_net_services.get_services():
            servers = self._osx_get_dns_servers(service)
            confs[service] = servers
        return confs

    def _osx_set_config(self, config):
        """Set the hosts DNS configuration to match the given configuration."""
        for service, servers in config.items():
            command = ['networksetup', '-setdnsservers', service] + servers
            proc.run_assert_ok(command)


class LinuxDNSConfig(DNSConfig):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.saved_resolv_link_path = _DEFAULT_SAVED_RESOLV_LINK_PATH
        self.resolv_conf_path = _DEFAULT_RESOLV_CONF_PATH

    def save(self):
        # No need to do anything here, the old resolv.conf is saved
        # when the new one is created in set()
        pass

    def restore(self):
        if os.path.exists(self.saved_resolv_link_path):
            self.log.debug('Restoring DNS settings')
            os.rename(self.saved_resolv_link_path, self.resolv_conf_path)

    def set(self, servers):
        self.log.debug('Settings DNS servers to {}'.format(str(servers)))
        if not os.path.exists(self.saved_resolv_link_path):
            os.rename(self.resolv_conf_path, self.saved_resolv_link_path)
        with open(self.resolv_conf_path, 'w') as f:
            for server in servers:
                f.write('nameserver {}\n'.format(server))
