#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import platform

import psutil

from mullvad import paths
from mullvad import proc


class LockFile(object):
    """Create and manage lock files.

    Update them at regular intervals.
    Consider lock files not recently updated orphans and ignore them.
    """

    _FILENAME = 'lock'

    def __init__(self):
        self.filename = LockFile._FILENAME
        if platform.system() not in ('Windows', 'Darwin'):
            self.filename = os.path.join(paths.get_config_dir(), self.filename)

    def lock(self):
        """Lock the LockFile.

        Returns:
            True on success and False if already locked.
        """
        try:
            with open(self.filename, 'r') as f:
                pid_str, name = f.read().split(':', 1)
                pid = int(pid_str)
        except (IOError, ValueError):
            pass
        else:
            try:
                p = psutil.Process(pid)
            except psutil.NoSuchProcess:
                pass
            else:
                if proc.get_proc_name(p) == name:
                    return False
        self._write()
        return True

    def release(self):
        """Kill the updating thread and truncate the lock file.
        """
        os.remove(self.filename)

    def _write(self):
        with open(self.filename, 'w') as f:
            f.write('{}:{}'.format(os.getpid(), self._get_my_proc_name()))

    def _get_my_proc_name(self):
        p = psutil.Process(os.getpid())
        return proc.get_proc_name(p)
