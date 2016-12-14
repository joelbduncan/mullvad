#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import threading

from mullvad import bins
from mullvad import proc
from mullvad import logger

_PORT = 10194


class Obfsproxy(object):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.process = None

    def start(self):
        if self.process is not None:
            raise RuntimeError('Obfsproxy is already running')
        args = ['obfs2', 'socks', '127.0.0.1:%d' % _PORT]
        self.process = proc.open([bins.obfsproxy] + args, stream_target=None)
        threading.Thread(target=self._monitor_obfsproxy).start()

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait()

    def local_port(self):
        return _PORT

    def _monitor_obfsproxy(self):
        self.log.info('Monitoring Obfsproxy in separate thread')
        self.process.communicate()
        self.process = None
        self.log.info('Obfsproxy process has died')
