#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import ConfigParser
import os
import platform
import shutil
import StringIO
import sys

from mullvad import logger
from mullvad import paths

_SETTINGS_FILE = 'settings.ini'

# Paths to settings in previous versions.
# Mullvad will try to migrate from these to the new path on start.
# Multiple old paths can be given in a list, newest first
_OLD_SETTINGS_FILES = {'Windows': ['settings.ini'],
                       'Darwin': ['settings.ini'],
                       'Linux': ['/usr/share/mullvad/settings.ini'],
                       }
_SETTINGS_SECTION = 'Client'

_DEFAULT_SETTINGS = {
    'delete_default_route': 'True',
    'tunnel_ipv6': 'False',
    'location': 'se',
    'protocol': 'any',
    'server': 'any',
    'port': 'any',
    'cipher': 'any',
    'stop_dns_leaks': 'True',
    'obfsproxy': 'auto',
    'block_local_network': 'False',
    'timeout': '35',
    'windows_block_outside_dns': 'True',
    'block_incoming_udp': 'True',
    'send_recv_buffers': 'auto',
    'autoconnect_on_start': 'True',
    'custom_ovpn_args': '',
}

# Increase socket buffer sizes on Windows 7 and earlier.
# Greatly increases speed over UDP sockets.
# On Win7, default is 8k, we set to 128k
if platform.system() == 'Windows':
    v = sys.getwindowsversion()
    if v[0] < 6 or (v[0] == 6 and v[1] <= 1):
        _DEFAULT_SETTINGS['send_recv_buffers'] = 131072  # 2^17

_BACKUP_FILE_SUFFIX = '.backup'


class ReadOnlySettings(object):
    """Read only representation of Settings. Not synced against any file."""
    def __init__(self, parser=None):
        """Create a new ReadOnlySettings.

        Args:
            parser: If given, this parser will be used to get values.
                    Will contain default values if not given.

        """
        self.log = logger.create_logger(self.__class__.__name__)
        if parser is None:
            self.parser = ReadOnlySettings._create_default_parser()
        else:
            self.parser = ReadOnlySettings._clone_parser(parser)

    @staticmethod
    def _create_default_parser():
        parser = ConfigParser.RawConfigParser()
        parser.add_section(_SETTINGS_SECTION)
        Settings._add_defaults(parser)
        return parser

    @staticmethod
    def _add_defaults(parser):
        for key, value in _DEFAULT_SETTINGS.iteritems():
            parser.set(_SETTINGS_SECTION, key, value)

    @staticmethod
    def _clone_parser(parser):
        clone = ConfigParser.RawConfigParser()
        settings_str = ReadOnlySettings._parser_to_str(parser)
        settings_io = StringIO.StringIO(settings_str)
        clone.readfp(settings_io)
        return clone

    def has_option(self, option):
        return self.parser.has_option(_SETTINGS_SECTION, option)

    def get(self, option):
        return self.parser.get(_SETTINGS_SECTION, option)

    def get_or_none(self, option):
        try:
            return self.get(option)
        except ConfigParser.NoOptionError:
            return None

    def getboolean(self, option):
        return self.parser.getboolean(_SETTINGS_SECTION, option)

    def getint(self, option):
        return self.parser.getint(_SETTINGS_SECTION, option)

    def __str__(self):
        return ReadOnlySettings._parser_to_str(self.parser)

    @staticmethod
    def _parser_to_str(parser):
        s = StringIO.StringIO()
        parser.write(s)
        data = s.getvalue()
        s.close()
        return data


class Settings(ReadOnlySettings):
    """Represents Mullvad settings. A key value dictionary with file sync"""
    def __init__(self, directory=None):
        """Create a new Settings instance.

        Args:
            directory: Where to read/write settings.ini. If not given the path
                       module is used to determine location.
        """
        ReadOnlySettings.__init__(self)
        self.log = logger.create_logger(self.__class__.__name__)
        if directory is None:
            directory = paths.get_config_dir()
        self.path = os.path.join(directory, _SETTINGS_FILE)
        self.file_mtime = -1  # To keep track of on disk changes
        self._init_file()

    def has_option(self, option):
        self._sync_file()
        return super(Settings, self).has_option(option)

    def get(self, option):
        self._sync_file()
        return super(Settings, self).get(option)

    def get_or_none(self, option):
        self._sync_file()
        return super(Settings, self).get_or_none(option)

    def getboolean(self, option):
        self._sync_file()
        return super(Settings, self).getboolean(option)

    def getint(self, option):
        self._sync_file()
        return super(Settings, self).getint(option)

    def set(self, option, value):
        """Changes an option to a new value.

        Args:
            option: option to change.
            value: new value, will be cast to str.

        """
        self._sync_file()
        self.parser.set(_SETTINGS_SECTION, option, str(value))
        self._remove_invalid_options()
        self._write()

    def bulk_set(self, new_settings):
        """Takes a string with all settings. Overwrites current settings."""
        new_settings_io = StringIO.StringIO(new_settings)
        parser = Settings._create_default_parser()
        parser.readfp(new_settings_io)  # Validate before switching
        self.parser = parser  # Switch to new
        self._remove_invalid_options()
        self._sanitize()
        self._write()

    def get_read_only_clone(self):
        """Create a clone of the current settings that is read only."""
        self._sync_file()
        return ReadOnlySettings(parser=self.parser)

    def _remove_invalid_options(self):
        """Removes all sections and options that are not valid.

        Used to clean out obsolete settings and keep users settings clean.
        """
        for option, __ in self.parser.items('DEFAULT'):
            self.log.debug('Removing invalid default option %s', option)
            self.parser.remove_option('DEFAULT', option)
        for section in self.parser.sections():
            if section != _SETTINGS_SECTION:
                self.log.debug('Removing invalid section %s', section)
                self.parser.remove_section(section)
        for option, __ in self.parser.items(_SETTINGS_SECTION):
            if option not in _DEFAULT_SETTINGS.keys() and option != 'id':
                self.log.debug('Removing invalid option %s', option)
                self.parser.remove_option(_SETTINGS_SECTION, option)

    def _sanitize(self):
        """
        Some users experienced strange configs where the value would span over
        multiple lines with the first line being their real value.
        This method simply removes extra lines from settings values.
        """
        for option, old_value in self.parser.items(_SETTINGS_SECTION):
            value = old_value.encode('ascii', 'ignore')
            if len(value) > 0:
                value = value.splitlines()[0]  # Remove strange other rows
            self.parser.set(_SETTINGS_SECTION, option, value)

    def _write(self):
        with open(self.path, 'w') as f:
            self.parser.write(f)

    def _init_file(self):
        """Reads settings from file. If file don't exist, create one with
        the content from this Settings instance"""
        if not os.path.exists(self.path):
            self.log.info('Creating settings file at %s', self.path)
            self._write()
            self._try_migrate_old_settings()
        else:
            self.log.info('Reading settings from %s', self.path)
            self._sync_file(True)

    def _sync_file(self, must_read=False):
        """Checks if the settings on disk have changed since last read,
        If so, reload from disk"""
        try:
            mtime = os.path.getmtime(self.path)
        except OSError as e:
            self.log.error(
                'Settings file disappeared, recreating (%s) (%s)',
                self.path, e)
            self._write()
        else:
            if must_read or mtime != self.file_mtime:
                self.file_mtime = mtime
                try:
                    with open(self.path, 'r') as settings_f:
                        settings_str = settings_f.read()
                    self.bulk_set(settings_str)
                except (ConfigParser.ParsingError,
                        ConfigParser.MissingSectionHeaderError) as e:
                    self.log.error('Corrupt settings file: %s', e)
                    raise

    def __str__(self):
        self._sync_file()
        return super(Settings, self).__str__()

    def _try_migrate_old_settings(self):
        current_platform = platform.system()
        if current_platform in _OLD_SETTINGS_FILES:
            for old_path in _OLD_SETTINGS_FILES[current_platform]:
                abs_old_path = os.path.realpath(old_path)
                if os.path.exists(abs_old_path):
                    self.log.debug('Migrating settings from %s', abs_old_path)
                    shutil.copyfile(abs_old_path, self.path)
                    self._sync_file(True)
                    break
        else:
            self.log.debug('Unknown platform to migrate settings on: %s',
                           current_platform)


def backup_reset_settings():
    directory = paths.get_config_dir()
    path = os.path.join(directory, _SETTINGS_FILE)
    backup_path = path + _BACKUP_FILE_SUFFIX
    shutil.move(path, backup_path)
    return backup_path
