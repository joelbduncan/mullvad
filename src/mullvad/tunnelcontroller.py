#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import json
import os
import pickle
import threading

from mullvad import logger
from mullvad import mtunnel
from mullvad import netstring
from mullvad import paths
from mullvad import proc


REQUEST_PIPE = 'request_pipe'
REPLY_PIPE = 'reply_pipe'
UPDATE_PIPE = 'update_pipe'


class TunnelController(threading.Thread):
    def __init__(self, pipe_dir):
        super(TunnelController, self).__init__()
        self.log = logger.create_logger(self.__class__.__name__)
        self.call_lock = threading.Lock()
        self.running = True
        self.connection_listeners = []
        self.server_listeners = []
        self.error_listeners = []
        self.tp = proc.open(
            ['pkexec', 'mtunnel',
             '--logdir', paths.get_log_dir(),
             '--confdir', paths.get_config_dir(),
             '--pipedir', pipe_dir],
            stream_target=None)
        self.request_pipe = open(os.path.join(pipe_dir, REQUEST_PIPE), 'w')
        self.reply_pipe = open(os.path.join(pipe_dir, REPLY_PIPE), 'r')
        self.update_pipe = open(os.path.join(pipe_dir, UPDATE_PIPE), 'r')
        self.start()

    def run(self):
        while self.running:
            try:
                method, name, args, kwargs = self.receive_update()
            except IOError:
                continue
            else:
                getattr(self, name)(*args, **kwargs)

        self.request_pipe.close()
        self.reply_pipe.close()
        self.update_pipe.close()

    def call(self, name, *args, **kwargs):
        self.call_lock.acquire()
        self.send_request(name, *args, **kwargs)
        method, returned_name, result = self.receive_response()
        if returned_name == 'destroy' and result is True:
            self.running = False
        self.call_lock.release()
        return result

    def send_request(self, name, *args, **kwargs):
        self.log.debug('{}, {}, {}'.format(name, args, kwargs))
        message = json.dumps(('request', name, args, kwargs))
        netstring.write_string(message, self.request_pipe)
        self.request_pipe.flush()

    def receive_response(self):
        message = netstring.read_string(self.reply_pipe)
        response = pickle.loads(message)
        self.log.debug('{}'.format(response))
        return response

    def receive_update(self):
        message = netstring.read_string(self.update_pipe)
        update = pickle.loads(message)
        self.log.debug('{}'.format(update))
        return update

    def __getattr__(self, name):
        if hasattr(mtunnel.Tunnel, name):
            def f(*args, **kwargs):
                return self.call(name, *args, **kwargs)
            return f
        else:
            raise AttributeError('Tunnel has no attribute: ' + name)

    def add_connection_listener(self, listener):
        self.connection_listeners.append(listener)

    def remove_connection_listener(self, listener):
        self.connection_listeners.remove(listener)

    def update_connection(self, state):
        for l in self.connection_listeners:
            l(state)

    def add_server_listener(self, listener):
        self.server_listeners.append(listener)

    def remove_server_listener(self, listener):
        self.server_listeners.remove(listener)

    def update_server(self, server):
        for l in self.server_listeners:
            l(server)

    def add_error_listener(self, listener):
        self.error_listeners.append(listener)

    def remove_error_listener(self, listener):
        self.error_listeners.remove(listener)

    def update_error(self, error):
        for l in self.error_listeners:
            l(error)
