#!/usr/bin/env python2

"""Send problem reports."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import locale
import os
import platform
import urllib

from mullvad import logger
from mullvad import proc
from mullvad import util
from mullvad import version


class ProblemReport:

    def __init__(self, settings):
        self.user_message = ''
        self.email_address = ''
        self.settings = unicode(str(settings), errors='replace')
        self.debug_log = self._read_file(logger.get_debug_log_path())
        self.debug_log_old = self._read_file(
            logger.get_debug_log_backup_path())
        self.openvpn_log = self._filtered_openvpn_log(
            logger.get_openvpn_path())
        self.openvpn_log_old = self._filtered_openvpn_log(
            logger.get_openvpn_backup_path())
        self.routes = self._get_routes()
        self.dns = self._get_dns()

    def set_user_message(self, message):
        self.user_message = message

    def set_email_address(self, address):
        self.email_address = address

    def get_report_text(self):
        report = self.email_address
        report += '\n-----------------------\n'
        report += self.user_message
        report += '\n-----------------------\n'
        report += 'CLIENT_VERSION: ' + unicode(version.CLIENT_VERSION)
        report += '\n-----------------------\n'
        report += self.platform_info()
        report += '\n-----------------------\n'
        report += self.settings
        report += '\n-----------------------\n'
        report += 'Debug log:\n'
        report += self.debug_log
        report += '\n-----------------------\n'
        report += 'Backup debug log:\n'
        report += self.debug_log_old
        report += '\n-----------------------\n'
        report += 'OpenVPN log:\n'
        report += self.openvpn_log
        report += '\n-----------------------\n'
        report += 'Backup OpenVPN log:\n'
        report += self.openvpn_log_old
        report += '\n-----------------------\n'
        report += self.routes
        report += '\n-----------------------\n'
        report += self.dns
        return report

    def platform_info(self):
        result = ""
        result += "Platform: {}\n".format(util.get_platform())
        result += "System: {}\n".format(platform.system())
        result += "Release: {}\n".format(platform.release())
        result += "Version: {}\n".format(platform.version())
        result += "Machine: {}\n".format(platform.machine())
        result += "Processor: {}\n".format(platform.processor())

        arch = ', '.join(platform.architecture())
        if platform.system() == 'Windows':
            if 'PROGRAMFILES(X86)' in os.environ:
                arch = '64bit'
        result += "Architecture: {}\n".format(arch)

        result += "Locale: {}".format(
            ', '.join(locale.getdefaultlocale()))
        return result

    def send(self):
        """Send the report to the web server. Throw IOError if the
        connection to the server fails."""
        report_str = self.get_report_text().encode('utf-8')
        data = urllib.urlencode({'report': report_str})
        urllib.urlopen(
            'https://problemreports.mullvad.net/problemreport/', data)

    def _read_file(self, filename):
        try:
            with open(filename, 'r') as f:
                data = f.read()
        except IOError, e:
            data = str(e)
        return unicode(data, errors='replace')

    def _filtered_openvpn_log(self, filename):
        """Return the contents of an openvpn log file with management
        interface state checks filtered out."""
        data = ''
        if filename is not None:
            try:
                with open(filename) as f:
                    for line in f:
                        line = unicode(line, errors='replace')
                        if 'MANAGEMENT: CMD \'state\'' not in line:
                            data += line
            except IOError, e:
                data = unicode(e)
        else:
            data = 'Log not existing'
        return data

    def _get_routes(self):
        return proc.try_run(['netstat', '-r', '-n'])

    def _get_dns(self):
        if platform.system() == 'Windows':
            return proc.try_run('netsh interface ip show dns'.split())
        elif platform.system() == 'Darwin':
            return proc.try_run(['scutil', '--dns'])
        else:
            return self._read_file('/etc/resolv.conf')


if __name__ == '__main__':
    report = ProblemReport()
    report.set_user_message('Foo!')
    # print report.get_report_text().encode('utf-8')
    # print report.send()
    print(report._filteredOpenVPNlog('openvpn.log.testcase'))
