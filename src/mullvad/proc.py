#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import locale
import os
import platform
import re
import subprocess

import psutil

from mullvad import logger

"""Module for executing system commands through subprocess"""

_proc_instance = None
_proc_manager_instance = None

# Create a keyword argument to Popen that will hide ugly black
# console windows on Windows (instead of setting shell=True) or
# an empty one that will do nothing on other platforms.
if platform.system() == 'Windows':
    su = subprocess.STARTUPINFO()
    su.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    su.wShowWindow = subprocess.SW_HIDE
    hide_window = dict()
    hide_window['startupinfo'] = su
else:
    hide_window = dict()


def open(args, stream_target=subprocess.PIPE):
    return _get_proc().open(args, stream_target)


def run(args, stdin=None):
    return _get_proc().run(args, stdin)


def run_get_exit(args, stdin=None):
    return _get_proc().run_get_exit(args, stdin)


def run_assert_ok(args, stdin=None):
    return _get_proc().run_assert_ok(args, stdin)


def try_run(args, stdin=None):
    return _get_proc().try_run(args, stdin)


def kill_procs_by_name(name, timeout=3):
    return _get_proc_manager().kill_procs_by_name(name, timeout)


def format_args(args):
    """Format argument list into string.

    Example: format_args(['./a.out', '-f', 'x y']) would return:
             u'./a.out -f "x y"'

    Args:
        args: list of arguments.

    Returns:
        A string joined from the args with every arg containing a whitespace
        enclosed in quotes.
    """
    string = u''
    for arg in args:
        quote = u''
        if re.search(r'\s', arg) is not None:
            quote = u'"'
        string += u'{0}{1}{0} '.format(quote, arg)
    return string.strip()


def get_proc_name(p):
    """Get the process name from a psutil Process object.

    This method has to do some special stuff as old psutils have name as a
    str and in newer versions it's a method.
    """
    name = p.name
    if not isinstance(name, basestring):
        name = name()  # Is method in newer psutil, str in old
    return name


def _get_proc():
    """Return singleton instance of Cmd."""
    global _proc_instance
    if _proc_instance is None:
        _proc_instance = Proc()
    return _proc_instance


def _get_proc_manager():
    global _proc_manager_instance
    if _proc_manager_instance is None:
        _proc_manager_instance = ProcManager()
    return _proc_manager_instance


class Proc(object):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)
        self.encode_encoding = self._get_encode_encoding()
        self.decode_encoding = self._get_decode_encoding()
        self.log.debug('Encoding with %s, decoding with %s',
                       self.encode_encoding, self.decode_encoding)

    def open(self, args, stream_target=subprocess.PIPE):
        """Starts a subprocess and returns the handle to it.

        Args:
            args: The list of args to execute, same as for subprocess.Popen.
                  These args are getting encoded to the platforms preffered
                  encoding before being sent to subprocess.
            stream_target: What to attach stdin, stdout and stderr to. Default
                           is subprocess.PIPE.

        Returns:
            a handle to the subprocess, just like subprocess.Popen.
        """
        assert len(args) > 0, 'No command given'
        self.log.debug('Executing: %s', format_args(args))
        exec_args = [self._encode(arg) for arg in args]
        close_fds = platform.system() == 'Windows' and stream_target is None
        try:
            return subprocess.Popen(exec_args,
                                    stdin=stream_target,
                                    stdout=stream_target,
                                    stderr=stream_target,
                                    close_fds=close_fds,
                                    **hide_window)
        except Exception as e:
            if not e.args:
                arg0 = ''
            else:
                arg0 = e.args[0]
            msg = 'Unable to run "{}", because: {}'.format(args[0], arg0)
            e.args = (msg,) + e.args[1:]
            raise

    def run(self, args, stdin=None):
        """Executes a command and return exit code, stdout & stderr.

        Args:
            args: a list of arguments, the first one being the program to run.
            stdin: a string that will be passed to stdin of the program.

        Returns:
            A tuple with three values. The first one is the exit code, the
            second value is the stdout string and the third value is the stderr
            string. stdout and stderr are being decoded to unicode.

        Raises:
            Same exceptions as subprocess.Popen. Also encode/decode can raise
        """
        proc = self.open(args)
        (out, err) = proc.communicate(stdin)
        out = self._decode(out)
        err = self._decode(err)
        return (proc.returncode, out, err)

    def run_get_exit(self, args, stdin=None):
        """Simple wrapper for cmd.run to execute and return exit code only.
        """
        code, __, __ = self.run(args, stdin)
        return code

    def run_assert_ok(self, args, stdin=None):
        """Wrapper for cmd.run that checks return code.

        uses cmd.run internally and raises error if exit code is not zero.

        Raises:
            RuntimeError: When exit code of command is not zero.
            Same exceptions as cmd.run.
        """
        (code, stdout, stderr) = self.run(args, stdin)
        if code != 0:
            msg = u'"{}" exited with code {}\nstderr: {}\n\nstdout: {}'.format(
                format_args(args), code, stderr, stdout)
            raise RuntimeError(msg)
        return stdout

    def try_run(self, args, stdin=None):
        """Try to execute a command, will always return a string.

        Uses run_assert_ok in the background. Will never throw an exception,
        rather a string with stdout or some error message. Useful for
        displaying command output directly to users or in problem reports.

        Returns:
            A string with the content of stdout on success (exit code 0),
            or an error message string on failure (exit code != 0 or not even
            able to start the command)
        """
        try:
            stdout = self.run_assert_ok(args, stdin)
        except Exception as e:
            if len(e.args) > 0:
                return str(e.args[0])
            else:
                return str(e)
        else:
            return stdout

    def _encode(self, text):
        return text.encode(self.encode_encoding)

    def _decode(self, text):
        return text.decode(self.decode_encoding)

    def _get_encode_encoding(self):
        encoding = locale.getdefaultlocale()[1]
        if encoding is None:
            encoding = 'UTF-8'
        return encoding

    def _get_decode_encoding(self):
        encoding = self._get_encode_encoding()
        if platform.system() == 'Windows':
            encoding = self._win_get_codepage()
        return encoding

    def _win_get_codepage(self):
        windows_codepage = locale.getpreferredencoding()
        try:
            codepage_proc = subprocess.Popen('chcp',
                                             stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE,
                                             shell=True,
                                             **hide_window)
            codepage_out = codepage_proc.communicate()[0]
        except OSError:
            self.log.error('Unable to execute "chcp", using fallback codepage')
        else:
            codepage_find = re.search(r'\d+', codepage_out)
            if codepage_find is not None:
                windows_codepage = 'cp' + codepage_find.group()
            else:
                self.log.error('chcp command did not return a valid codepage')
        return windows_codepage


class ProcManager(object):
    def __init__(self):
        self.log = logger.create_logger(self.__class__.__name__)

    def get_procs_by_name(self, name, include_self=True):
        """Get a list of all processes with a given name.

        Args:
            name: A string to match against process names.
            include_self: if False the current process will not be included
                          even if it matches name.

        """
        procs = []
        for p in psutil.process_iter():
            if (get_proc_name(p) == name and
               (os.getpid() != p.pid or include_self)):
                procs.append(p)
        return procs

    def kill_procs_by_name(self, name, timeout=3):
        """Kill all processes with a given name in the system.

        First sending SIGTERM/TerminateProcess to all matching processes.
        If the system is not windows and there are any processes left after
        waiting for the timeout it will send SIGKILL to the remaining prcesses.

        The current process will not be killed even if it matches name.

        Args:
            name: A string to match processes on.
            timeout: The timeout (seconds) to wait after SIGTERM and SIGKILL.
        """
        procs = self.get_procs_by_name(name, include_self=False)
        if len(procs) > 0:
            self.log.info('%s process alive, terminating %s',
                          name, str(self._procs_to_pids(procs)))
            for p in procs:
                p.terminate()
            _, procs = psutil.wait_procs(procs, timeout)

        if len(procs) > 0 and platform.system() != 'Windows':
            self.log.warning('%s process alive, killing %s',
                             name, str(self._procs_to_pids(procs)))
            for p in procs:
                p.kill()
            _, procs = psutil.wait_procs(procs, timeout)

        if len(procs) > 0:
            self.log.error('Failed to kill these %s processes: %s',
                           name, str(self._procs_to_pids(procs)))

    def _procs_to_pids(self, procs):
        return map(lambda p: p.pid, procs)
