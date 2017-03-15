#!/usr/bin/env python2

"""Send strings from client to server."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
# from __future__ import unicode_literals

import socket
import sys

defaultPort = 51678

sys.setrecursionlimit(10000)


def _getBytes(length, sock):
    """Read an exact number of bytes from a socket."""
    data = ''
    while len(data) < length:
        newData = sock.recv(length - len(data))
        if len(newData) == 0:
            raise socket.error, 'Remote end closed.'
        data += newData
    return data


class Client:

    def __init__(self, server, port=defaultPort, family=socket.AF_INET,
                 timeout=60, connectTimeout=None):
        if connectTimeout is None:
            connectTimeout = timeout
        self.socket = socket.socket(family, socket.SOCK_STREAM)
        self.socket.settimeout(connectTimeout)
        self.socket.connect((server, port))
        self.socket.settimeout(timeout)

    def send(self, blob):
        self.socket.sendall('%08X' % len(blob))  # Size of the object
        self.socket.sendall(blob)

        # Wait for a reply
        hexSize = _getBytes(8, self.socket)
        assert len(hexSize) > 0
        try:
            size = int(hexSize, 16)
        except ValueError:
            return None

        blob = _getBytes(size, self.socket)
        return blob

    def close(self):
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close()


class Listener:

    """Listen for connections and create Server objects to handle
    them."""

    def __init__(self, port=defaultPort):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(('', port))
        self.socket.listen(1)

    def accept(self):
        serverSocket, addr = self.socket.accept()
        serverSocket.settimeout(60)
        return Server(serverSocket)


class Server:

    def __init__(self, socket):
        self.client = socket

    def get(self):
        # Get the object size
        hexSize = _getBytes(8, self.client)
        assert len(hexSize) > 0
        try:
            size = int(hexSize, 16)
        except ValueError:
            return None

        blob = _getBytes(size, self.client)
        return blob

    def send(self, blob):
        """Send a reply."""
        self.client.sendall('%08X' % len(blob))  # Size of the object
        self.client.sendall(blob)

    def close(self):
        self.client.shutdown(socket.SHUT_RDWR)
        self.client.close()


class StringSequence(list):

    """Convenience class to pack several strings into one and back."""

    def __init__(self, seed=None):
        if isinstance(seed, basestring):
            list.__init__(self)
            self += self.parse(seed)
        else:
            list.__init__(self, seed)

    def parse(self, string):
        if string == '':
            return []
        else:
            done = False
            pos = 0
            s = ''
            while not done:
                if string[pos] == '\\':
                    if string[pos + 1] == '\\':
                        s += '\\'
                        pos += 1
                    elif string[pos + 1] == '%':
                        s += '%'
                        pos += 1
                    else:
                        s += '\\'
                elif string[pos] == '%':
                    done = True
                else:
                    s += string[pos]
                pos += 1
            return [s] + self.parse(string[pos:])

    def escape(self, string):
        string = string.replace('\\', '\\\\')
        string = string.replace('%', '\\%')
        return string

    def dump(self):
        result = ''
        for s in self:
            result += self.escape(s) + '%'
        return result
