#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import os
import re
import shutil
import sys

from mullvad import paths

_DEBUG_LOG_FILE = 'mullvad.debug.log'
_ERROR_LOG_FILE = 'mullvad.error.log'

_OPENVPN_LOG_PRE = 'openvpn'
_OPENVPN_LOG_POST = 'log'
_OPENVPN_LOG_TEMPLATE = _OPENVPN_LOG_PRE + '.{}.' + _OPENVPN_LOG_POST
_OPENVPN_LOG_REGEX = r'{}\.(\d+)\.{}'.format(_OPENVPN_LOG_PRE,
                                             _OPENVPN_LOG_POST)

_BACKUP_LOG_SUFFIX = '.backup'

_FILE_LOGGING_FORMAT = (
    '%(asctime)s:%(levelname)s: '
    '%(process)s.%(module)s.%(name)s.%(funcName)s: '
    '%(message)s'
)
_TERMINAL_LOGGING_FORMATTER = logging.Formatter(
    '%(levelname)s: %(message)s'
)

_log = None
_log_dir = None
_log_terminal_handler = None


def init(directory=None):
    """Initiate the logging facility.

    Must be run before any other functions in this module.

    Args:
        directory: the directory where the logs should be stored. If None:
                   default platform directory"""
    global _log_dir
    if _log_dir is not None:
        raise RuntimeError('Logger already initiated, can only call init once')
    if directory is None:
        directory = paths.get_log_dir()
    _log_dir = directory
    print('Setting logging directory to %s' % _log_dir)
    paths.create_dir(_log_dir)  # Make sure it exists


def create_logger(class_name):
    """Create a logger that can be used to log events.

    The first call to this function will create logging handlers.

    Args:
        class_name: the name of the class that will own and use the logger.
                    self.log = logger.create_logger(self.__class__.__name__)
    """
    global _log
    _assert_dir_initiated()
    if _log_terminal_handler is None:
        _init_handlers()
        _log = create_logger('logger')
    log = logging.getLogger(class_name)
    log.addHandler(_log_terminal_handler)
    return log


def _assert_dir_initiated():
    assert _log_dir is not None, ('Logging not initialized. Please call '
                                  'logger.init() first')


def _init_handlers():
    global _log_terminal_handler
    logging.basicConfig(
        level=logging.DEBUG,
        format=_FILE_LOGGING_FORMAT,
        filename=get_debug_log_path(),
        filemode='a'
    )
    # TODO(linus) logging should happen to sys.stderr on warning and up,
    # but this creates problems in windows so use stdout for now
    _log_terminal_handler = logging.StreamHandler(sys.stdout)
    _log_terminal_handler.setLevel(logging.WARNING)
    _log_terminal_handler.setFormatter(_TERMINAL_LOGGING_FORMATTER)


def get_debug_log_path():
    _assert_dir_initiated()
    return os.path.join(_log_dir, _DEBUG_LOG_FILE)


def get_debug_log_backup_path():
    return get_debug_log_path() + _BACKUP_LOG_SUFFIX


def get_error_log_path():
    _assert_dir_initiated()
    return os.path.join(_log_dir, _ERROR_LOG_FILE)


def get_new_openvpn_path():
    """Returns a path to a non-existing openvpn log."""
    _assert_dir_initiated()
    num = 1
    existing_logs = _get_openvpn_logs()
    if existing_logs:
        num = existing_logs[-1][1] + 1
    filename = _create_openvpn_filename(num)
    return os.path.join(_log_dir, filename)


def get_openvpn_path():
    """Returns the newest existing openvpn log. None if no log exists."""
    _assert_dir_initiated()
    existing_logs = _get_openvpn_logs()
    if existing_logs:
        return os.path.join(_log_dir, existing_logs[-1][0])
    else:
        return None


def get_openvpn_backup_path():
    """Returns the second newest existing openvpn log. None if <2 logs exist.
    """
    _assert_dir_initiated()
    existing_logs = _get_openvpn_logs()
    if len(existing_logs) > 1:
        return os.path.join(_log_dir, existing_logs[-2][0])
    else:
        return None


def _get_openvpn_logs():
    """Returns a list of (filename, num) tuples, oldest first."""
    _assert_dir_initiated()
    files = [f for f in os.listdir(_log_dir)
             if os.path.isfile(os.path.join(_log_dir, f))]
    openvpn_logs = []
    for f in files:
        match = re.match(_OPENVPN_LOG_REGEX, f)
        if match is not None:
            num = int(match.group(1))
            openvpn_logs.append((f, num))
    openvpn_logs.sort(key=lambda (__, num): num)
    return openvpn_logs


def _create_openvpn_filename(num):
    return _OPENVPN_LOG_TEMPLATE.format(num)


def remove_old_openvpn_logs():
    """Deletes all except the two newest openvpn logs."""
    _assert_dir_initiated()
    for log in _get_openvpn_logs()[:-2]:
        path = os.path.join(_log_dir, log[0])
        try:
            os.remove(path)
        except (WindowsError, OSError) as e:
            err = unicode(str(e), errors='replace')
            _try_log_error('Unable to delete: %s (%s)' % (path, err))


def backup_reset_debug_log():
    path = get_debug_log_path()
    backup_path = get_debug_log_backup_path()
    return _backup_reset_log(path, backup_path)


def _backup_reset_log(path, backup_path):
    success = True
    if os.path.exists(path):
        try:
            shutil.copy(path, backup_path)
        except (IOError, WindowsError) as e:
            err = unicode(str(e), errors='replace')
            _try_log_error('Could not overwrite: %s (%s)' % (backup_path, err))
            success = False
    try:
        open(path, 'w').close()
    except (IOError, WindowsError) as e:
        err = unicode(str(e), errors='replace')
        _try_log_error('Failed to empty: %s (%s)' % (path, err))
        success = False
    return success


def _try_log_error(message):
    if _log is not None:
        _log.error(message)
    else:
        print(message, file=sys.stderr)
