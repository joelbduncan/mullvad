#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import platform
import sys

import appdirs

from mullvad import version


def get_config_dir():
    """Returns the platform dependant directory for this programs configs"""
    directory = appdirs.user_config_dir(version._APP_NAME, version._AUTHOR)
    create_dir(directory)
    return directory


def get_log_dir():
    """Returns the platform dependant directory for this programs logs"""
    directory = appdirs.user_log_dir(version._APP_NAME, version._AUTHOR)
    create_dir(directory)
    return directory


def get_installation_dir():
    """Returns the absolute path to the installation directory"""
    if platform.system() == "Linux":
        return os.path.dirname(os.path.realpath(__file__))
    else:
        return os.path.realpath('.')


def create_dir(directory):
    """Creates a directory recursively if it does not exist"""
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except os.error, e:
            print('ERROR: Unable to create directory: %s (%s)',
                  (directory, e), sys.stderr)
            raise
