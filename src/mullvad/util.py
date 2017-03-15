#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import platform
import time

from mullvad import proc

if platform.system() == 'Windows':
    import _winreg
    from mullvad import mwinreg


def file_content(path):
    """Read and return file content, None if file does not exist.
    """
    if os.path.exists(path):
        with open(path, 'r') as f:
            t = f.read()
        return t


def poll(cond, interval, timeout):
    """Run a given function at a given interval until a timeout is reached.

    Args:
        cond: The function to run.
        interval: the interval, in seconds, between calls to cond.
        timeout: The time, in seconds, to give up after.

    """
    end = time.time() + timeout
    while cond() and time.time() <= end:
        time.sleep(interval)


def get_platform():
    value = unicode(platform.platform())
    if platform.system() == 'Darwin':
        details = proc.try_run(['sw_vers', '-productVersion']).strip()
        value += ' ({})'.format(details)
    elif platform.system() == 'Windows':
        root_key = _winreg.HKEY_LOCAL_MACHINE
        sub_key = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
        product_name = None
        try:
            key = mwinreg.WinReg(root_key, sub_key)
            product_name = key.get('ProductName')[0]
        except Exception:
            pass
        if product_name:
            value = product_name
    return value
