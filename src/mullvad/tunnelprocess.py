#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import json
import os
import pickle
import sys

from mullvad import config
from mullvad import exceptioncatcher
from mullvad import logger
from mullvad import mtunnel
from mullvad import netstring
from mullvad import proc


REQUEST_PIPE = 'request_pipe'
REPLY_PIPE = 'reply_pipe'
UPDATE_PIPE = 'update_pipe'


class TunnelProcessError:
    """Encapsulate exceptions that should be raised at the other end
    of the pipe, rather than returned as from popError().

    (This class must live outside TunnelProcess because pickle can't
    handle nested classes.)"""

    def __init__(self, error):
        self.error = error


class TunnelProcess(object):
    def __init__(self, pipe_dir, settings, conf_dir=None):
        self.log = logger.create_logger(self.__class__.__name__)
        self.tunnel = mtunnel.Tunnel(settings, conf_dir)
        self.tunnel.add_connection_listener(self.update_connection)
        self.tunnel.add_server_listener(self.update_server)
        self.tunnel.add_error_listener(self.update_error)
        self.request_pipe = open(os.path.join(pipe_dir, REQUEST_PIPE), 'r')
        self.reply_pipe = open(os.path.join(pipe_dir, REPLY_PIPE), 'w')
        self.update_pipe = open(os.path.join(pipe_dir, UPDATE_PIPE), 'w')

    def update_connection(self, state):
        self.send_update('update_connection', state)

    def update_server(self, server):
        self.send_update('update_server', server)

    def update_error(self, error):
        self.send_update('update_error', error)

    def run(self):
        done = False
        while not done:
            meth, name, args, kwargs = self.receive_request()
            try:
                method = getattr(self.tunnel, name)
                result = method(*args, **kwargs)
            except Exception as e:
                self.send_reply(name, TunnelProcessError(e))
            else:
                self.send_reply(name, result)
            if name == 'destroy' and result is True:
                done = True
        self.request_pipe.close()
        self.reply_pipe.close()
        self.update_pipe.close()

    def send_reply(self, name, result):
        self.log.debug('{}, {}'.format(name, result))
        message = pickle.dumps(('reply', name, result))
        netstring.write_string(message, self.reply_pipe)
        self.reply_pipe.flush()

    def send_update(self, name, *args, **kwargs):
        self.log.debug('{}, {}, {}'.format(name, args, kwargs))
        message = pickle.dumps(('update', name, args, kwargs))
        netstring.write_string(message, self.update_pipe)
        self.update_pipe.flush()

    def receive_request(self):
        message = netstring.read_string(self.request_pipe)
        request = json.loads(message)
        self.log.debug('{}'.format(request))
        return request


def setup_file_paths():
    """Change the working directory to the install directory to find the
    data files that are stored there. Only used on Linux"""
    directory = os.path.dirname(os.path.realpath(__file__))
    os.chdir(directory)


def main_args(args):
    if args.pipedir is not None:
        pipe_dir = args.pipedir
    else:
        print('--pipedir arg must be given', file=sys.stderr)
        sys.exit(1)

    logger.init(args.logdir)
    log = logger.create_logger('mtunnel_main')
    exceptioncatcher.activate(log)

    proc.kill_procs_by_name('mtunnel')  # Make sure we are alone.

    setup_file_paths()
    settings = config.Settings(directory=args.confdir)  # Using default if None
    tp = TunnelProcess(pipe_dir, settings, args.confdir)
    tp.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--logdir', dest='logdir', help='Write logs here')
    parser.add_argument('--confdir', dest='confdir', help='Use this conf dir')
    parser.add_argument('--pipedir', dest='pipedir', help='Dir for pipes')
    args = parser.parse_args()
    main_args(args)

if __name__ == '__main__':
    main()
