#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import sys
import traceback

_log = None


def activate(exc_log):
    """Activates the uncaught exception catcher.

    Hooks into python to handle all uncaught exceptions.

    Args:
        exc_log: The logger that the exceptions should be logged to."""
    global _log
    if _log is not None:
        raise RuntimeError('exceptioncatcher already activated, call once.')
    if exc_log is None:
        raise RuntimeError('Need a logger, got None')
    _log = exc_log
    sys.excepthook = _handle_exception


def _handle_exception(exctype, value, tb):
    try:
        message = ''.join(traceback.format_exception(exctype, value, tb))
        _log.critical('An uncaught exception occured: %s', message)
    except Exception as e:
        print('Exception during global exception cathing, this is bad: %s' %
              str(e), file=sys.stderr)
