#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import shutil

from mullvad import logger
from mullvad import paths

# Controls both <installdir>/ssl and <confdir>/ssl
_SSL_KEY_DIR = 'ssl'

_CA_CERT = 'ca.crt'
_MASTER_CERT = 'master.mullvad.net.crt'


class SSLKeys:
    def __init__(self, conf_dir=None):
        self.log = logger.create_logger(self.__class__.__name__)
        if conf_dir is None:
            conf_dir = paths.get_config_dir()
        self.ssl_dir = os.path.join(conf_dir, _SSL_KEY_DIR)
        paths.create_dir(self.ssl_dir)

    def get_client_cert_path(self, customerId):
        """Client cert file path."""
        return os.path.join(self.ssl_dir, '%d.crt' % customerId)

    def get_client_signing_request_path(self, customerId):
        """Client cert signing request path."""
        return os.path.join(self.ssl_dir, '%d.csr' % customerId)

    def get_client_key_path(self, customerId):
        """Client key file path."""
        return os.path.join(self.ssl_dir, '%d.key' % customerId)

    def get_ca_cert_path(self):
        """Returns the absolute path to the CA cert. Will be located
        in the users config dir, copied from installation if not existing"""
        return self._get_cert_path(_CA_CERT)

    def get_master_cert_path(self):
        """Returns the absolute path to the master cert. Will be located
        in the users config dir, copied from installation if not existing"""
        return self._get_cert_path(_MASTER_CERT)

    def _get_cert_path(self, filename):
        path = os.path.join(self.ssl_dir, filename)
        if not os.path.exists(path):
            # Copy from installation directory
            install_dir = paths.get_installation_dir()
            src = os.path.join(install_dir, _SSL_KEY_DIR, filename)
            self.log.info("Installing ssl certificate: %s -> %s", src, path)
            shutil.copyfile(src, path)
        return path
