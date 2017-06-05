#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
# from __future__ import unicode_literals

import hashlib
import os
import re
import socket
import tempfile

from mullvad import bins
from mullvad import logger
from mullvad import netcom
from mullvad import proc
from mullvad import serverinfo
from mullvad import ssl_keys
from mullvad import version


class MullvadClientError(Exception):
    """Base class for errors in the mtunnel module."""
    def __str__(self):
        if len(self.args) >= 1:
            return self.args[0]
        else:
            return self.__class__.__name__


class UnrecoverableError(MullvadClientError):
    pass


class MullvadClient:
    def __init__(self, server, keys=None,
                 port=netcom.defaultPort, family=socket.AF_INET,
                 timeout=10, connectTimeout=None):
        self.log = logger.create_logger(self.__class__.__name__)
        if keys is None:
            keys = ssl_keys.SSLKeys()
        self.ssl_keys = keys
        if connectTimeout is None:
            connectTimeout = timeout
        self.master = netcom.Client(
            server, port, family, timeout, connectTimeout)

    def _verify(self, signature):
        sig_fd, sig_path = tempfile.mkstemp()
        os.write(sig_fd, signature)
        os.close(sig_fd)
        with open(self.ssl_keys.get_master_cert_path(), 'rb') as key_f:
            pubkey = key_f.read()
        command = [bins.openssl, 'rsautl', '-verify',
                   '-certin', '-in', sig_path]
        (exitcode, stdout, _) = proc.run(command, pubkey)
        os.remove(sig_path)
        return stdout if exitcode == 0 else None

    def _command(self, name, *data):
        stringData = [str(d) for d in data]
        return netcom.StringSequence([name] + stringData).dump()

    def _errorCheck(self, reply):
        if reply[0] == 'error':
            errorType = reply[1]
            description = reply[2]
            e = UnrecoverableError(description)
            e.errorType = errorType
            raise e

    def _send(self, command):
        reply = netcom.StringSequence(self.master.send(command))
        self._errorCheck(reply)
        return reply

    def close(self):
        try:
            self.master.close()
        except socket.error:
            # The other end probably already closed
            pass

    def version(self):
        vers = re.findall('(\d+)', version.CLIENT_VERSION)
        if len(vers) > 0:
            ver = vers[0]
        else:
            ver = '0'
        cmd = self._command('version', ver)
        reply = self._send(cmd)
        return reply[1]

    def getCertificate(self):
        """Get the master certificate."""
        cmd = self._command('get cert')
        reply = self._send(cmd)
        return reply[1]

    def signCertificate(self, csr):
        cmd = self._command('sign', csr)
        reply = self._send(cmd)
        return reply[1]

    def getVPNServers(self):
        cmd = self._command('get server')
        reply = self._send(cmd)
        serverStrings = reply[1:]
        servers = []
        for s in serverStrings:
            try:
                si = serverinfo.ServerInfo(s)
            except Exception:
                self.log.warning('Unknown server description')
            else:
                servers.append(si)
        return servers

    def getSubscriptionTimeLeft(self, customerId):
        # TODO(linus): Remove the fingerprint argument when mullvadm has been
        # updated to not parse the third argument
        cmd = self._command('subscription time',
                            str(customerId),
                            'fingerprint_deprecated')
        reply = self._send(cmd)
        return int(reply[1])

    def connectionCount(self, customerId):
        cmd = self._command('connections', str(customerId))
        reply = self._send(cmd)
        count = int(reply[1])
        maxAllowed = int(reply[2])
        return count, maxAllowed

    def getDNSserver(self):
        cmd = self._command('dns server')
        reply = self._send(cmd)
        dns = reply[1]
        hash = hashlib.sha256(dns).hexdigest()
        signature = reply[2]
        if hash == self._verify(signature):
            return dns
        else:
            raise MullvadClientError(
                'getDNSserver: signature verification failed')

    def getExitAddress(self):
        cmd = self._command('ip address')
        reply = self._send(cmd)
        return reply[1]

    def getLatestVersion(self):
        cmd = self._command('latest version')
        reply = self._send(cmd)
        return reply[1].strip()

    def getPort(self, customerId):
        cmd = self._command('forward port', str(customerId))
        reply = self._send(cmd)
        return int(reply[1])

    def getPorts(self, customerId):
        cmd = self._command('forward port', str(customerId))
        reply = self._send(cmd)
        return [int(port) for port in reply[1:]]

    def getNewPort(self, customerId):
        cmd = self._command('new port', str(customerId))
        reply = self._send(cmd)
        return [int(port) for port in reply[1:]]

    def removePort(self, customerId, port):
        cmd = self._command('remove port', str(customerId), str(port))
        reply = self._send(cmd)
        return [int(reply_port) for reply_port in reply[1:]]

    def getMaxPorts(self):
        cmd = self._command('max ports')
        reply = self._send(cmd)
        return int(reply[1])

    def quit(self):
        cmd = self._command('quit')
        self.master.send(cmd)
        self.close()
