#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import ctypes
import errno
import itertools
import os
import platform
import random
import socket
import sys
import threading
import time
import traceback
import netifaces

import ipaddr

from mullvad import bins
from mullvad import dnsconfig
from mullvad import firewall
from mullvad import logger
from mullvad import mullvadclient
from mullvad import obfsproxy
from mullvad import proc
from mullvad import route
from mullvad import serverinfo
from mullvad import ssl_keys
from mullvad import util


backup_server_file = 'backupservers.txt'
harddns_backup_file = 'harddnsbackup.txt'

SYSTEM_UPDOWN_SCRIPT = '/etc/openvpn/update-resolv-conf'
BUNDLED_UPDOWN_SCRIPT = 'update-resolv-conf'

_OPENVPN_MANAGEMENT_ADDR = '127.0.0.1'
_OPENVPN_MANAGEMENT_PORT = 7505

_GW_CHECK_INTERVAL = 30

_MASTER_VIA_RELAY_PORT = 53
_MASTER_PORT = 51678
_MASTER_IP = '193.138.219.42'

_SEND_RECV_BUFFERS_MIN = 8192
_SEND_RECV_BUFFERS_MAX = 67108864


class ConState:
    connected = 1
    disconnected = 2
    connecting = 3
    off = 4
    unrecoverable = 5


class ConnectError(mullvadclient.MullvadClientError):
    pass


class SubscriptionExpiredError(mullvadclient.UnrecoverableError):
    pass


class TAPMissingError(mullvadclient.UnrecoverableError):
    pass


class ObfsproxyMissingError(mullvadclient.MullvadClientError):
    pass


class OpenVPNManagement:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect((_OPENVPN_MANAGEMENT_ADDR, _OPENVPN_MANAGEMENT_PORT))
        self.sock.recv(500)

    def connection_state(self):
        result = self.connection_info()
        return result.split(',')[1]

    def connection_info(self):
        self.sock.sendall('state' + '\n')
        result = ''
        while not result.endswith('END\r\n'):
            new = self.sock.recv(2 ** 16)
            if new == '':
                raise socket.error('closed')
            result += new
        return result

    def kill(self):
        self.sock.sendall('signal SIGINT' + '\n')
        result = ''
        while len(result) < 8:
            new = self.sock.recv(2 ** 16)
            if new == '':
                raise socket.error('closed')
            result += new
        return result.startswith('SUCCESS:')

    def close(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()


class Tunnel:
    def __init__(self, settings, conf_dir=None):
        self.log = logger.create_logger(self.__class__.__name__)
        self.settings = settings
        self.rw_settings = settings
        self.ssl_keys = ssl_keys.SSLKeys(conf_dir)
        self.dnsconfig = dnsconfig.get_dnsconfig()

        self.backup_server_file = backup_server_file
        self.harddns_backup_file = harddns_backup_file

        self.conState = ConState.disconnected
        self.desiredConState = ConState.disconnected
        self.connection_listeners = []
        self.server_listeners = []
        self.error_listeners = []
        self.maybeBlockedByFirewall = False
        self.dpiOpenvpnFiltering = 0
        self.connectTimeout = 35
        self.openvpn_proc = None  # Process handle to openvpn when running
        self.obfsproxy = None  # Handle to Obfsproxy instance if used.
        self.server = None
        self.customerId = None
        self.master_address_cache = None
        self.current_master_address = None

        # Set when to update route check and monitor_default_gw
        self.time_route_check = time.time() + 15
        self.time_monitor_default_gw = self.time_route_check + 15

        self.logged_first_next_hop = False

        self.firewall = None

        self.route_manager = route.get_route_manager()
        if self.route_manager.get_default_gateway() is None:
            self.route_manager.restore_saved_default_gateway()
        self.machine = threading.Thread(target=self._machine)
        self.machine.start()

    def _cleanupRoutes(self):
        if self.current_master_address is not None:
            self.route_manager.route_del(self.current_master_address)
        self.route_manager.route_del('0.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.route_del('64.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.route_del('128.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.route_del('192.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.restore_default_gateway()

        self.log.debug('Unblocking IPv6')
        self.route_manager.unblock_ipv6()

    def connect(self):
        self.desiredConState = ConState.connected
        if self.conState == ConState.unrecoverable:
            self.conState = ConState.disconnected

    def disconnect(self):
        self.desiredConState = ConState.disconnected
        if self.settings.getboolean('delete_default_route'):
            self._cleanupRoutes()

    def shutDown(self):
        self.desiredConState = ConState.off
        if self.settings.getboolean('delete_default_route'):
            self._cleanupRoutes()

    def destroy(self):
        self.desiredConState = ConState.off
        self.machine.join()
        return True

    def finished(self):
        return not self.machine.isAlive()

    def connectionState(self):
        return self.conState

    def desiredConnectionState(self):
        return self.desiredConState

    def _machine(self):
        self.log.debug('Starting state machine')
        self.time_route_check = time.time() + 15
        self.time_monitor_default_gw = self.time_route_check + 15
        while self.conState != ConState.off:
            # Connected
            if self.conState == ConState.connected:
                if not self._monitor():
                    self.log.warning('Unable to monitor tunnel, disconnecting')
                    self._disconnect()
                    self.conState = ConState.disconnected
                    self.update_connection(self.conState)
                elif self.desiredConState == ConState.disconnected:
                    self.log.info('Instructed to close tunnel, disconnecting')
                    self._disconnect()
                    self.conState = ConState.disconnected
                    self.update_connection(self.conState)
                elif self.desiredConState == ConState.off:
                    self.log.info('Instructed to shut down, disconnecting')
                    self._disconnect()
                    self.conState = ConState.off
                    self.update_connection(self.conState)
                else:
                    self._monitor_default_gw()
                    time.sleep(1)
            # Disconnected
            elif self.conState == ConState.disconnected:
                if self.desiredConState == ConState.connected:
                    self.log.info('Starting to connect')
                    self.conState = ConState.connecting
                    self.update_connection(self.conState)
                    self.conState = self._connect()
                    if self.conState == ConState.disconnected:
                        time.sleep(5)
                    self.update_connection(self.conState)
                elif self.desiredConState == ConState.off:
                    self.log.info('Instructed to shut down,'
                                  ' making sure disconnected')
                    self._disconnect()
                    self.conState = ConState.off
                    self.update_connection(self.conState)
                else:
                    time.sleep(1)
            # Unrecoverable error
            elif self.conState == ConState.unrecoverable:
                self.log.info('Tunnel state machine in unrecoverable state')
                if self.desiredConState == ConState.off:
                    self.conState = ConState.off
                    self.update_connection(self.conState)
                time.sleep(1)
        self.log.debug('Tunnel manager dying')

    def _monitor(self):
        try:
            good = self.openvpnManagement.connection_state() == 'CONNECTED'
        except Exception as e:
            good = False
            err = unicode(str(e), errors='replace')
            self.log.debug(err)
        # Verify that traffic is correctly routed every 30 s
        currentTime = time.time()
        # Check route when time has come or clock has changed
        if (currentTime - self.time_route_check > 0.0 or
                currentTime - self.time_route_check < -200.0):
            # Allow update in 30 seconds
            self.time_route_check = ((int(currentTime) / 30) * 30 +
                                     self.time_route_check % 30)
            if self.time_route_check <= currentTime:
                self.time_route_check += 30

            good = good and self._routeCheck()
        return good

    def _monitor_default_gw(self):
        current_time = time.time()

        # Check default gateway when time has come or clock has changed
        if (current_time - self.time_monitor_default_gw > 0.0 or
                current_time - self.time_monitor_default_gw < -200.0):
            self.time_monitor_default_gw = current_time + _GW_CHECK_INTERVAL

            # Make sure there is no default route if there shouldn't be
            if self.settings.getboolean('delete_default_route'):
                self.route_manager.delete_default_gateway()

    def _routeCheck(self):
        """Verify that traffic is correctly routed."""
        ok = True
        try:
            nh = self.nextHop()
        except socket.error, e:
            self.log.debug('nextHop failed: %s', e)
        else:
            if not nh.startswith('10.') and not nh.startswith('*'):
                self.log.error('Routing error, %s', nh)
                ok = False
            elif not self.logged_first_next_hop:
                self.logged_first_next_hop = True
                self.log.info('Success, packet went through the tunnel')
        return ok

    def _masterFailure(self, operation, error):
        message = 'master: %s: %s' % (operation, error)
        if not self.settings.has_option('id'):
            self.log.error(message)
            raise ConnectError(message)
        else:
            self.log.warning(message)

    def _connectMaster(self):
        master = None
        for address, port in self._ordered_master_connection_addresses():
            # Add route to master/proxy if Stop DNS leaks enabled
            if self.settings.getboolean('delete_default_route'):
                self.route_manager.route_add(address)
            self.log.debug('Connecting to master at %s:%d', address, port)
            try:
                master = mullvadclient.MullvadClient(
                    address, self.ssl_keys,
                    port=port, timeout=10, connectTimeout=4)
                v = master.version()
                self.log.debug('Version reply from master: %s', v)
                # Set address of successful connection as
                # master for this tunnel instance
                self.current_master_address = address
                break
            except socket.error as e:
                master = None
                self.log.debug('Connection to master failed: %s', e)
                # Delete route for master/proxy if it fails
                if self.settings.getboolean('delete_default_route'):
                    self.route_manager.route_del(address)
        return master

    def _ordered_master_connection_addresses(self):
        """Create a list of (ip, port) tuples for reaching mullvadm."""
        preferred_servers = set()
        other_servers = set()
        for server in self._get_servers():
            if self._is_match(server):
                preferred_servers.add((server.address, _MASTER_VIA_RELAY_PORT))
            else:
                other_servers.add((server.address, _MASTER_VIA_RELAY_PORT))
        preferred_servers = list(preferred_servers)
        other_servers = list(other_servers)

        random.shuffle(preferred_servers)
        random.shuffle(other_servers)

        custom_server = self._custom_server()
        if custom_server is not None:
            servers_to_try = [(custom_server.address, _MASTER_VIA_RELAY_PORT)]
        else:
            servers_to_try = preferred_servers[:3]

        # Try connecting through the real master if the preferred fails
        servers_to_try.append((_MASTER_IP, _MASTER_PORT))
        # Add a few totally random servers to the list of servers to try.
        # Do this in case all preferred servers are down. Say the customer
        # has selected one country where we have only few servers, and that
        # data center goes down.
        servers_to_try += other_servers[:2]

        return servers_to_try

    def _connectOpenVPN(self, server, port, proto, cipher, useObfsp=False):
        customerId = self.settings.getint('id')
        result = ConState.disconnected
        client_cert = self.ssl_keys.get_client_cert_path(customerId)
        client_key = self.ssl_keys.get_client_key_path(customerId)
        if platform.system() == 'Windows':
            ovpn_conf = 'client.conf.windows'
        elif platform.system() == 'Darwin':
            ovpn_conf = 'client.conf.mac'
        else:
            ovpn_conf = 'client.conf.linux'
        if cipher == 'bf128':
            cipher = 'BF-CBC'
        if cipher == 'aes256':
            cipher = 'AES-256-CBC'

        ovpn_log = logger.get_new_openvpn_path()  # This log does not exist yet
        open(ovpn_log, 'w').close()  # Create and empty the log
        logger.remove_old_openvpn_logs()  # Delete old logs
        ovpn_args = [
            (bins.openvpn,),
            ('--config', ovpn_conf),
            ('--log', ovpn_log),
            ('--remote', server, str(port)),
            ('--cert', client_cert),
            ('--key', client_key),
            ('--management',
                _OPENVPN_MANAGEMENT_ADDR, str(_OPENVPN_MANAGEMENT_PORT)),
            ('--cipher', cipher),
        ]

        ovpn_version = self._get_openvpn_version()

        if self.settings.getboolean('tunnel_ipv6'):
            ovpn_args.append(('--tun-ipv6',))
        elif ovpn_version[0] == 2 and ovpn_version[1] >= 4:
            if proto in ['tcp', 'udp'] and not proto.endswith('4'):
                proto = proto + '4'
            ovpn_args.append(('--pull-filter', 'ignore', 'ifconfig-ipv6 '))
            ovpn_args.append(('--pull-filter', 'ignore', 'route-ipv6 '))

        ovpn_args.append(('--proto', proto))

        send_recv_buffers = self.settings.get('send_recv_buffers')
        if send_recv_buffers != 'auto':
            try:
                test = int(send_recv_buffers)
                assert test >= _SEND_RECV_BUFFERS_MIN
                assert test <= _SEND_RECV_BUFFERS_MAX
            except (ValueError, AssertionError):
                raise RuntimeError('send_recv_buffers must be an '
                                   'integer between %s and %s' %
                                   (_SEND_RECV_BUFFERS_MIN,
                                    _SEND_RECV_BUFFERS_MAX))
            ovpn_args.append(('--sndbuf', send_recv_buffers))
            ovpn_args.append(('--rcvbuf', send_recv_buffers))

        if useObfsp:
            obfsproxyOpt = '--socks-proxy'
            obfsAddr = '127.0.0.1'
            try:
                self.obfsproxy = obfsproxy.Obfsproxy()
                self.obfsproxy.start()
                obfsPort = self.obfsproxy.local_port()
                ovpn_args.append((obfsproxyOpt, obfsAddr, str(obfsPort)))
            except OSError as e:
                if e.errno == errno.ENOENT:
                    raise ObfsproxyMissingError()
                else:
                    raise

        custom_args = self.settings.get('custom_ovpn_args')
        if custom_args:
            self.log.debug('Using custom OpenVPN arguments: %s', custom_args)
            ovpn_args.extend(map(lambda x: (x,), custom_args.split(' ')))

        # Use the slightly modified up/down scripts provided by Tunnelblick to
        # configure the DNS settings and also restore them should the be
        # overwritten by DHCP. This is not needed if we manually set the DNS
        # through the 'Stop DNS Leaks' setting.
        if 'Darwin' in platform.platform() and (
                not self.settings.getboolean('stop_dns_leaks')):
            ovpn_args.append(
                ('--up', 'client.up.osx.sh -m -w -d -f -ptADGNWradsgnw'))
            ovpn_args.append(
                ('--down', 'client.down.osx.sh -m -w -d -f -ptADGNWradsgnw'))

        if platform.system() == 'Windows':
            if self.settings.getboolean('windows_block_outside_dns'):
                ovpn_args.append(('--block-outside-dns',))
            if self.settings.getboolean('block_incoming_udp'):
                ovpn_args.append(('--plugin', bins.block_udp_plugin))

        # The OpenVPN package for some Linux distributions do not come with an
        # update-resolv-conf script, thus we bundle one with the client and use
        # that in cases where the default one is missing.
        if platform.system() == 'Linux':
            if os.path.exists(SYSTEM_UPDOWN_SCRIPT):
                updown_script = SYSTEM_UPDOWN_SCRIPT
            else:
                updown_script = BUNDLED_UPDOWN_SCRIPT
            ovpn_args.append(('--up', updown_script))
            ovpn_args.append(('--down', updown_script))

        ovpn_command = list(itertools.chain(*ovpn_args))

        self.openvpn_proc = proc.open(ovpn_command, stream_target=None)
        self.openvpn_monitor = threading.Thread(target=self._monitor_openvpn)
        self.openvpn_monitor.start()

        finished_event_in = threading.Event()
        finished_event_out = threading.Event()
        threading.Thread(target=self._connectTimeout,
                         kwargs={'finished_in': finished_event_in,
                                 'finished_out': finished_event_out,
                                 'timeout': self.connectTimeout}).start()
        ovpnlog = open(ovpn_log, 'r')
        lookForFiltering = False
        line = unicode(ovpnlog.readline(), errors='replace')
        while self._is_alive() or line != '':
            sys.stdout.write(line)
            if 'Initialization Sequence Completed' in line and \
                    'With Errors' not in line:
                self.log.debug('Initialization Sequence Completed')
                result = ConState.connected
                break
            if 'There are no TAP-Windows adapters on this system' in line:
                raise TAPMissingError()
            if ('All TAP-Windows adapters on this '
                    'system are currently in use') in line:
                # Sometimes this message is shown instead of 'There
                # are no TAP-Win32 adapters ...'
                routing_info = proc.run_assert_ok(['netstat', '-r', '-n'])
                if 'TAP-Windows Adapter' not in routing_info:
                    raise TAPMissingError()
            # Detect deep packet inspection OpenVPN filtering
            if lookForFiltering and line != '':
                lookForFiltering = False
                if 'Connection reset' in line or \
                        'MANAGEMENT: Client connected from' in line:
                    # Try twice before going back to normal
                    self.dpiOpenvpnFiltering = 2
                    self.log.info('Deep packet inspection OpenVPN '
                                  'filtering detected')
            if ' link remote: ' in line or 'TLS: Initial packet from ' in line:
                lookForFiltering = True
            if line == '':
                time.sleep(0.05)
            line = unicode(ovpnlog.readline(), errors='replace')

        # Tell timeout thread to abort
        finished_event_in.set()
        # Wait for timeout thread to finish execution
        finished_event_out.wait()
        # Check if the timeout thread aborted the connection
        if self.server is None:
            result = ConState.disconnected

        self.log.debug('Done waiting for OpenVPN')
        if result != ConState.connected:
            self.log.debug('Not connected')
            self.maybeBlockedByFirewall = not self.maybeBlockedByFirewall
        else:
            self.maybeBlockedByFirewall = False
        ovpnlog.close()
        return result

    def _get_openvpn_version(self):
        stdout = proc.run([bins.openvpn, '--version'])[1]
        version_string = stdout.split(' ')[1]

        return map(int, version_string.split('.'))

    def _monitor_openvpn(self):
        self.log.debug('Monitoring OpenVPN in separate thread')
        self.openvpn_proc.communicate()
        self.log.info('OpenVPN process has died')
        self.openvpn_proc = None
        # TODO(linus) Probably inform the state machine or something here.

    def _connect(self):
        self._lock_settings()
        self.logged_first_next_hop = False
        result = ConState.disconnected
        try:
            result = self.__connect__()
        except mullvadclient.UnrecoverableError, e:
            self.update_error(e)
            self.log.error('Unrecoverable: %s', e)
            result = ConState.unrecoverable
        except Exception, e:
            self.update_error(e)
            self.log.error('Connection failed: %s, %s', e,
                           unicode(traceback.format_exc(), errors='replace'))
            result = ConState.disconnected
        try:
            self.openvpnManagement.close()
        except Exception:
            pass

        if result == ConState.connected:
            self.openvpnManagement = OpenVPNManagement()
            if platform.system() == 'Windows':
                self._attempt_to_set_lowest_metric()

        return result

    def _removeBlockAndGateway(self):
        self.log.debug('Removing blocking routes ...')
        self.route_manager.delete_default_gateway()
        # TODO(simonasker) This no longer makes any sense on Windows. I doubt
        # it ever did. Check if the other platforms has any use for this.
        # Just in case there are two (has happened)
        self.route_manager.delete_default_gateway()
        self.route_manager.route_del('0.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.route_del('64.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.route_del('128.0.0.0', '192.0.0.0', 'reject')
        self.route_manager.route_del('192.0.0.0', '192.0.0.0', 'reject')

    def __connect__(self):
        if not self.settings.has_option('id'):
            raise mullvadclient.UnrecoverableError('No account id set')
        result = ConState.disconnected

        self.firewall = None
        try:
            self.firewall = firewall.get_firewall()
        except firewall.FirewallError as e:
            self.log.error('Firewall error: %s', e)

        block_local_network = self.settings.getboolean('block_local_network')
        if block_local_network and not self.firewall:
            raise mullvadclient.UnrecoverableError(
                'You have enabled the "block_local_network" setting but you '
                'do not have an active firewall. Please turn on you firewall '
                'or disable "block_local_network" in the advanced settings.'
            )
        if (self.settings.getboolean('block_incoming_udp') and
                not self.firewall and platform.system() != 'Windows'):
            raise mullvadclient.UnrecoverableError(
                'You have enabled the "block_incoming_udp" setting but you '
                'do not have an active firewall. Please turn on your firewall '
                ' or disable "block_incoming_udp" in the advanced settings.'
            )
        if (not self.settings.getboolean('tunnel_ipv6') and
                not self.firewall):
            raise mullvadclient.UnrecoverableError(
                'You have not enabled the "Tunnel IPv6" setting and you do '
                'not have an active firewall. Please turn on your firewall '
                'or enable "Tunnel IPv6" and reconnect.'
            )

        # Check for Windows admin privileges
        if platform.system() == 'Windows':
            if ctypes.windll.shell32.IsUserAnAdmin() == 0:
                raise mullvadclient.UnrecoverableError(
                    'Need Administrator privileges')

        # Set connect timeout
        self.connectTimeout = self.settings.getint('timeout')

        # Kill old openvpn instances
        self._kill_openvpn()

        # Make sure the DNS server configuration can be restored later
        self.dnsconfig.save()

        # Block the default gateway to prevent leaks around the tunnel
        # in case we are reconnecting after an error. OpenVPN needs to
        # look at the default route so we must wait until it has
        # connected before deleting it.
        if self.settings.getboolean('delete_default_route'):
            self.route_manager.route_add('0.0.0.0', '192.0.0.0', 'reject')
            self.route_manager.route_add('64.0.0.0', '192.0.0.0', 'reject')
            self.route_manager.route_add('128.0.0.0', '192.0.0.0', 'reject')
            self.route_manager.route_add('192.0.0.0', '192.0.0.0', 'reject')
            self.route_manager.restore_default_gateway()

            # Add blocking routes for IPv6
            # Since IPv6 traffic will be routed through the tunnel using four
            # destination blocks pushed from the OpenVPN server, the two
            # destination blocks added here can remain throughout the Mullvad
            # session thus removing the need to store the default gateway while
            # still blocking internet on connection failure
            self.log.debug('Blocking IPv6')
            self.route_manager.block_ipv6()

        # Connect to the master
        master = self._connectMaster()
        customerId = self.settings.getint('id')

        if master is None and not self._has_client_credentials():
            message = 'Unable to fetch account credentials.'
            self._masterFailure('bootstrap_failed', message)
            raise ConnectError(message)

        # Get a certificate
        if master is not None:
            try:
                self._refresh_master_cert(master)
                self._refresh_own_cert(master)
            except socket.error, e:
                self._masterFailure('refresh_Cert', str(e))
                master = None
            else:
                client_cert = self.ssl_keys.get_client_cert_path(customerId)
                with open(client_cert, 'r') as crt_f:
                    crt_f.read()
                self.log.debug('Got a certificate')

        # Check subscription expiry time
        if master is not None:
            try:
                self._timeLeft = master.getSubscriptionTimeLeft(customerId)
            except socket.error, e:
                self._masterFailure('getSubscriptionTimeLeft', str(e))
                master = None
            else:
                if self._timeLeft <= 0:
                    master.quit()
                    if self.settings.getboolean('delete_default_route'):
                        self._removeBlockAndGateway()
                    raise SubscriptionExpiredError()
                self.log.debug('Time left: %d', self._timeLeft)

        # Check connection count
        if master is not None:
            try:
                count, maxAllowed = master.connectionCount(customerId)
            except socket.error, e:
                self._masterFailure('connectionCount', str(e))
                master = None
            else:
                self.log.debug('Connections: %d/%d' % (count, maxAllowed))
                if count >= maxAllowed:
                    master.quit()
                    if self.settings.getboolean('delete_default_route'):
                        self._removeBlockAndGateway()
                    raise ConnectError(
                        'Too many connections: %d' % (count + 1))

        # Get a DNS server
        DNSserver = '10.8.0.1'

        # Get backed up DNS just in case, if there
        # is one, and we've asked for avoiding DNS leaks
        if os.path.exists(self.harddns_backup_file):
            DNSserver = self._getHardDNSBackup()

        if master is not None:
            try:
                DNSserver = master.getDNSserver()
            except mullvadclient.MullvadClientError, e:
                self.log.warning(e)
            except socket.error, e:
                self._masterFailure('getDNSserver', str(e))
                master = None

        # Find a server to connect to
        if master is not None:
            try:
                serverList = master.getVPNServers()
                master.quit()
            except socket.error, e:
                self._masterFailure('getVPNServers', str(e))
                master = None
            else:
                self._setBackupServers(serverList)

        if self.current_master_address is not None:
            if self.settings.getboolean('delete_default_route'):
                self.route_manager.route_del(self.current_master_address)
            self.current_master_address = None

        matches = [s for s in self._get_servers() if self._is_match(s)]
        if matches:
            self.server = self._select_server(matches)
        else:
            self.server = self._custom_server()

        if self.server:
            self.update_server(self.server)
        else:
            raise ConnectError('Found no servers matching your settings.')

        # TODO(simonasker) Ugly. We should probably have obfsproxy as a
        # separate parameter in the server specification to avoid switching
        # the value of protocol back and forth.
        obfsproxySetting = self.settings.get('obfsproxy')
        assert obfsproxySetting in ('auto', 'yes', 'no')
        useObfsproxy = (obfsproxySetting == 'yes') or \
            (obfsproxySetting == 'auto' and self.dpiOpenvpnFiltering)
        if useObfsproxy:
            self.server.protocol = 'tcp'  # obfsproxy requires TCP

        if self.settings.getboolean('delete_default_route'):
            # On Mac, if this is done without a network connection it will
            # go to the loopback interface. Unless deleted before the
            # next try it will block the correct route from ever being added.
            self.route_manager.route_del(self.server.address)
            self.route_manager.route_add(self.server.address)

        if self.firewall:
            self.firewall.set_allowed_ip(self.server.address)
            if self.settings.getboolean('block_incoming_udp'):
                self.firewall.block_incoming_udp()
            if block_local_network:
                self.log.debug('Blocking local network')
                self.firewall.block_local_network()

        # Bring up the VPN
        result = self._connectOpenVPN(self.server.address, self.server.port,
                                      self.server.protocol, self.server.cipher,
                                      useObfsproxy)

        if result == ConState.connected:
            if platform.system() == 'Darwin' and self.firewall:
                # Unblock utun ifaces that did not exist during first block.
                # A little ugly, but the setting of the ip will trigger a rule
                # rewrite which will detect the new utun interfaces.
                # When neither block_local_network nor block_incoming_udp
                # is active this will have no effect.
                self.firewall.set_allowed_ip(self.server.address)

            # Avoid DNS leaks
            stop_dns_leaks = self.settings.getboolean('stop_dns_leaks')
            if platform.system() == 'Windows' and block_local_network:
                # Since most DNS are set to local routers we must force a
                # public DNS if the local network is blocked.
                self.log.debug('Forcing \'Stop DNS Leaks\'')
                stop_dns_leaks = True

            if stop_dns_leaks:
                self.dnsconfig.set([DNSserver])
                # Backup DNS so that it may be used if master is not reachable
                # in the future
                self._setHardDNSBackup(DNSserver)

            # Disable IPv6 if not tunnelled
            if self.firewall:
                if not self.settings.getboolean('tunnel_ipv6'):
                    self.log.debug('Blocking IPv6 in firewall')
                    self.firewall.block_ipv6()
                else:
                    self.log.debug('Unblocking IPv6 in firewall')
                    self.firewall.unblock_ipv6()

        # Delete both the default route and the routes blocking its use
        if self.settings.getboolean('delete_default_route') and \
                self.desiredConState == ConState.connected:
            self._removeBlockAndGateway()

        self.log.debug('dying')
        return result

    def _attempt_to_set_lowest_metric(self):
        # Windows 10 Creators Update sends DNS queries sequentially to all
        # network interfaces. The order in which the interfaces are queried
        # is determined by their metrics. Since we only allow internet access
        # through our TAP interface every DNS query to an interface with a
        # metric lower than our TAP interface will timeout, making DNS extremely slow.
        # This method sets the metric of our TAP interface to 0 so that we will almost
        # always be asked first, thus removing the timeouts.

        try:
            tap_interface = self._get_tap_interface_name()
            if tap_interface is None:
                self.log.debug('Could not set interface metric, unable to find the TAP interface')
                return

            new_metric = 0
            self.log.debug('Setting the metric of %s to %s', tap_interface, new_metric)

            proc.run_assert_ok(['netsh', 'interface', 'ip', 'set', 'interface', tap_interface, 'metric=' + str(new_metric)])
        except Exception as e:
            self.log.debug('Failed to set metric: %s', e)

    def _get_tap_interface_name(self):
        raw = self.openvpnManagement.connection_info()
        ip = raw.split(',')[3]
        interface_uuid = self._ip_to_interface_uuid(ip)
        return self._interface_uuid_to_name(interface_uuid)

    def _ip_to_interface_uuid(self, ip):
        for iface in netifaces.interfaces():
            address_families = netifaces.ifaddresses(iface)
            for address_family, addresses in address_families.iteritems():
                ip_addresses = map(lambda a: a['addr'], addresses)

                if ip in ip_addresses:
                    return iface

        return None

    def _interface_uuid_to_name(self, uuid):
        raw = proc.run_assert_ok([bins.openvpn, '--show-adapters'])
        lines = raw.split('\n')
        for line in lines:
            if uuid in line:
                parts = line.rsplit(' ', 1)
                return parts[0].replace('\'', '')

        return None

    def _disconnect(self):
        self.server = None
        self.update_server(None)

        if self._is_alive():
            self._kill_openvpn()
        else:
            self.log.debug('openvpn not alive, killing not necessary')

        try:
            self.dnsconfig.restore()
        except WindowsError as e:
            if self.settings.getboolean('stop_dns_leaks'):
                # The user can have changed the setting after
                # connection making it fail without being an error
                # TODO(linus) The above comment is not valid any more since
                # settings can't change during an active connction. Evaluate
                # What to do here
                e_name = e.__class__.__name__
                # Assuming WindowsErrors are the only exceptions that come
                # with encoded strings in their arguments.
                msg = unicode(str(e), errors='replace')
                self.log.warning('dnsconfig.restore: %s: %s', e_name, msg)
        except Exception as e:
            if self.settings.getboolean('stop_dns_leaks'):
                e_name = e.__class__.__name__
                # Assuming all other exceptions can be turned into a unicode
                # string without worrying about encoding.
                msg = unicode(e)
                self.log.warning('dnsconfig.restore: %s: %s', e_name, msg)

        if self.firewall:
            self.log.debug('Unblocking IPv6 in firewall')
            self.firewall.unblock_ipv6()
        if self.obfsproxy is not None:
            try:
                self.obfsproxy.stop()
                self.obfsproxy = None
            except Exception as e:
                self.log.error('obfsproxy.stop(): %s', e)

        if self.firewall:
            if self.settings.getboolean('block_local_network'):
                self.log.debug('Unblocking local network')
                self.firewall.unblock_local_network()
            if self.settings.getboolean('block_incoming_udp'):
                self.firewall.unblock_incoming_udp()
            self.firewall.set_allowed_ip(None)
        self._unlock_settings()

    def _is_alive(self):
        return (self.openvpn_proc is not None and
                self.openvpn_proc.poll() is None)

    def _connectTimeout(self, finished_in, finished_out, timeout):
        for i in xrange(timeout):
            finished_in.wait(1)
            if self.desiredConState != ConState.connected:
                self.log.debug('Aborting connection due to user request')
                self._disconnect()
                break
            elif finished_in.isSet():
                self.log.debug('Connection established')
                break
        if (not finished_in.isSet()
           and self._is_alive()
           and self.conState != ConState.connected):
            self.log.error('Connect timeout expired, disconnecting')
            self._disconnect()
        finished_out.set()

    def _kill_openvpn(self):
        """Kill openvpn. Try both management interface and then all procs."""
        self.log.debug('Killing openvpn process')
        self._kill_openvpn_management()
        proc.kill_procs_by_name(bins.openvpn_name)

    def _kill_openvpn_management(self):
        """Use the management interface to try to kill openvpn."""
        wait = True
        try:
            assert self.openvpnManagement.kill()
            self.openvpnManagement.close()
        except Exception as e:
            # If there is no working management connection try making a new.
            err = unicode(str(e), errors='replace')
            self.log.debug('Killing via mgmt interface failed: %s', err)
            try:
                ovpn = OpenVPNManagement()
                assert ovpn.kill()
                ovpn.close()
            except Exception as e2:
                err2 = unicode(str(e2), errors='replace')
                self.log.debug('Connecting new mgmt failed: %s', err2)
                wait = False
        if wait:
            util.poll(self._is_alive, 0.45, 3)

    def timeLeft(self):
        return self._timeLeft

    def serverInfo(self):
        if self.conState == ConState.connected:
            return self.server
        return None

    def _verify_cert_file(self, cert_path, ca_path):
        """Verify a given certificate file"""
        with open(cert_path) as f:
            cert = f.read()
        return self._verify_cert_data(cert, ca_path)

    def _verify_cert_data(self, cert_data, ca_path):
        """Verify a given certificate against ca file.
        """
        __, stdout, __ = proc.run([bins.openssl, 'verify', '-CAfile',
                                  ca_path], stdin=cert_data)
        return 'stdin: OK' in stdout

    def _verify_cert_key_match(self, cert_path, key_path):
        """Compare the public part of a given certificate and private key."""
        __, cert_modulus, __ = proc.run(
            [bins.openssl, 'x509', '-in', cert_path, '-modulus', '-noout'])
        __, key_modulus, __ = proc.run(
            [bins.openssl, 'rsa', '-in', key_path, '-modulus', '-noout'])
        return cert_modulus == key_modulus

    def _has_client_credentials(self):
        cid = self.settings.getint('id')
        if cid is None:
            return False
        has_key = os.path.exists(self.ssl_keys.get_client_key_path(cid))
        has_cert = os.path.exists(self.ssl_keys.get_client_cert_path(cid))
        return has_key and has_cert

    def _refresh_master_cert(self, master):
        master_cert = master.getCertificate()
        ca_cert_path = self.ssl_keys.get_ca_cert_path()
        if self._verify_cert_data(master_cert, ca_cert_path):
            master_cert_path = self.ssl_keys.get_master_cert_path()
            try:
                with open(master_cert_path, 'w') as master_cert_f:
                    master_cert_f.write(master_cert)
            except IOError, e:
                self.log.error(
                    'Could not write master.mullvad.net certificate: %s', e)
        else:
            self.log.error('Master certificate verification failed')

    def _refresh_own_cert(self, master):
        cid = self.settings.getint('id')
        client_key_path = self.ssl_keys.get_client_key_path(cid)
        client_cert_path = self.ssl_keys.get_client_cert_path(cid)
        master_cert_path = self.ssl_keys.get_master_cert_path()
        need_new = False
        if not os.path.exists(client_cert_path):
            self.log.info('%s not found, creating', client_cert_path)
            need_new = True
        elif not self._verify_cert_file(client_cert_path, master_cert_path):
            self.log.info('%s no longer valid, recreating', client_cert_path)
            need_new = True
        elif os.path.getmtime(client_cert_path) < 1397006081:
            self.log.info('%s from heartbleed bug era, recreating',
                          client_cert_path)
            need_new = True
        elif not self._verify_cert_key_match(client_cert_path,
                                             client_key_path):
            self.log.info('%s and %s do not match, recreating',
                          client_key_path,
                          client_cert_path)
            need_new = True

        if need_new:
            # Generate and sign a key pair
            __, client_csr_path = self._generate_key(cid)
            with open(client_csr_path, 'r') as csr_f:
                client_csr = csr_f.read()
            client_cert = master.signCertificate(client_csr)
            with open(client_cert_path, 'w') as crt_f:
                crt_f.write(client_cert)

    def _generate_key(self, cid):
        """Create a new private key and a certificate signing request
        file. Returns a tuple of (key_path, signing_request_path)"""
        key = self.ssl_keys.get_client_key_path(cid)
        csr = self.ssl_keys.get_client_signing_request_path(cid)
        command = ([bins.openssl] +
                   ('req -text -batch -days 3650 -nodes -new -newkey '
                   'rsa:2048 -subj /CN=Mullvad%d' % cid).split() +
                   ['-keyout', key, '-out', csr, '-config', 'openssl.cnf'])
        proc.run_assert_ok(command)
        return (key, csr)

    def _setBackupServers(self, serverList):
        f = open(self.backup_server_file, 'w')
        for s in serverList:
            f.write(str(s) + '\n')
        f.close()

    def _setHardDNSBackup(self, DNSserver):
        with open(self.harddns_backup_file, 'w') as f:
            f.write(DNSserver + '\n')

    def _getHardDNSBackup(self):
        with open(self.harddns_backup_file, 'r') as f:
            DNSServer = f.readline()
        return DNSServer

    def _get_server_settings(self):
        """Return the user settings necessary for choosing a server."""
        location = self.settings.get('location')
        protocol = self.settings.get('protocol')
        name = self.settings.get('server')
        port = self.settings.get('port')
        cipher = self.settings.get('cipher')
        obfsproxy = self.settings.get('obfsproxy')

        # TODO(simonasker) Settings validation shouldn't be done here but
        # rather when the user tries to modify the settings. We should be able
        # to assume correct settings values at every other point in the code.
        assert protocol in ('udp', 'tcp', 'any')
        assert cipher in ('bf128', 'aes256', 'any')
        assert obfsproxy in ('auto', 'yes', 'no')

        # TODO(simonasker) I would prefer to not muck about with the filter
        # parameters that the user has set. Preferring a tcp connection when
        # a firewall is suspected should be done in the selection process
        # rather than the filtering process.
        if protocol == 'any' and self.maybeBlockedByFirewall:
            protocol = 'tcp'

        useObfsproxy = ((obfsproxy == 'yes') or
                        (obfsproxy == 'auto' and self.dpiOpenvpnFiltering))
        if useObfsproxy:
            self.dpiOpenvpnFiltering -= 1
            protocol = 'obfs2'

        if port != 'any':
            port = int(port)

        params = dict(
            port=port,
            protocol=protocol,
            name=name,
            location=location,
            cipher=cipher,
        )
        return params

    def _is_match(self, server):
        """Check a server against the users server filter parameters."""
        for key, val in self._get_server_settings().items():
            if val in ['any', 'xx']:
                continue
            if getattr(server, key) != val:
                return False
        return True

    def _get_servers(self):
        """Create a list of servers from the on disk backup."""
        servers = []
        with open(self.backup_server_file, 'r') as f:
            for line in f:
                servers.append(serverinfo.ServerInfo(line))
        return servers

    def _custom_server(self):
        """Define a custom server object from the user settings.

        If the user settings are enough to completely specify a server and
        contains either a valid IP address or a domain name that resolves to an
        IP adress, return a server object with those attributes.
        """
        server_params = self._get_server_settings()
        if 'any' in server_params.values():
            return None
        custom_server = serverinfo.ServerInfo()
        custom_server.location = server_params['location']
        custom_server.port = int(server_params['port'])
        custom_server.protocol = server_params['protocol']
        custom_server.cipher = server_params['cipher']
        # Check if 'name' is a valid IP address or a domain name that resolves
        # to an IP address
        try:
            ip_address = socket.gethostbyname(server_params['name'])
            ipaddr.IPAddress(ip_address)
        except (ValueError, socket.error):
            return None
        else:
            custom_server.name = server_params['name']
            custom_server.address = ip_address
        return custom_server

    def _select_server(self, servers):
        """Choose a server from a given list of servers.

        The choice is random but UDP connections are preferred over
        TCP. This is because all servers use AES, and TCP and AES is less
        optimal than UDP and AES.
        """
        udp_servers = filter(lambda s: s.protocol == 'udp', servers)
        if len(udp_servers) > 0:
            servers = udp_servers

        selected = random.choice(servers)
        self.log.debug('Selected server: %s', selected)
        return selected

    def add_connection_listener(self, listener):
        self.connection_listeners.append(listener)

    def remove_connection_listener(self, listener):
        self.connection_listeners.remove(listener)

    def update_connection(self, state):
        for l in self.connection_listeners:
            try:
                l(state)
            except Exception:
                pass

    def add_server_listener(self, listener):
        self.server_listeners.append(listener)

    def remove_server_listener(self, listener):
        self.server_listeners.remove(listener)

    def update_server(self, server):
        for l in self.server_listeners:
            try:
                l(server)
            except Exception:
                pass

    def add_error_listener(self, listener):
        self.error_listeners.append(listener)

    def remove_error_listener(self, listener):
        self.error_listeners.remove(listener)

    def update_error(self, error):
        for l in self.error_listeners:
            try:
                l(error)
            except Exception:
                pass

    def nextHop(self):
        """Return the IP address of the next hop gotten from a one-hop
        traceroute."""
        dest = '193.0.14.129'  # Doesn't really matter, we'll never get there
        port = 65501  # Only used to identify the reply
        load = 'Meaningless dummy data.'

        def sendProbe():
            s = socket.socket(socket.AF_INET,
                              socket.SOCK_DGRAM,
                              socket.IPPROTO_UDP)
            if sys.platform in ['win32']:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, 2)
            s.settimeout(2)
            s.setsockopt(socket.SOL_IP, socket.IP_TTL, 1)
            s.sendto(load, (dest, port))
            s.close()

        def parsePacket(data):
            def eatIPheader(data):
                length = (ord(data[0]) & 0xF) * 4
                return data[length:]

            try:
                icmpMessage = eatIPheader(data)
                # icmpType = ord(icmpMessage[0])
                # icmpCode = ord(icmpMessage[1])
                originalPacket = icmpMessage[8:]
                origDest = originalPacket[16:20]
                udpStart = eatIPheader(originalPacket)
                destPort = ord(udpStart[2]) << 8 | ord(udpStart[3])
                length = ord(udpStart[4]) << 8 | ord(udpStart[5])
                udpData = udpStart[8:]
            except IndexError:
                return False
            # if icmpType != 11 or icmpCode != 0:
            #    return False
            dStr = b''.join(chr(int(b)) for b in dest.split('.'))
            if origDest != dStr:
                return False
            if destPort != port or length - 8 != len(load):
                return False
            if not load.startswith(udpData):
                return False
            return True

        ls = socket.socket(socket.AF_INET,
                           socket.SOCK_RAW,
                           socket.IPPROTO_ICMP)
        ls.bind(('', port))
        if sys.platform in ['win32']:
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, 2)
        ls.settimeout(2)
        sendProbe()
        result = None
        t0 = time.time()
        while result is None:
            try:
                data, addr = ls.recvfrom(512)
            except socket.timeout:
                self.log.warning('recvfrom timed out')
                result = '*'
            else:
                if parsePacket(data):
                    result = addr[0]
                elif time.time() - t0 > 3:
                    self.log.warning('timed out')
                    result = '*'
        ls.close()
        return result

    def _lock_settings(self):
        self.log.debug('Locking settings')
        self.settings = self.rw_settings.get_read_only_clone()

    def _unlock_settings(self):
        self.log.debug('Unlocking settings')
        self.settings = self.rw_settings
