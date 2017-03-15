#!/usr/bin/env python2
# -*- coding: utf-8 -*-

"""Mullvad UI."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from SimpleXMLRPCServer import SimpleXMLRPCServer
from threading import Thread
import atexit
import ConfigParser
import gettext
import locale
import optparse
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import wx
import wx.lib.hyperlink
import wx.lib.newevent
import xmlrpclib

from mullvad import config
from mullvad import dnsconfig
from mullvad import exceptioncatcher
from mullvad import lockfile
from mullvad import logger
from mullvad import mtunnel
from mullvad import mullvadclient
from mullvad import paths
from mullvad import problemreport
from mullvad import proc
from mullvad import serverinfo
from mullvad import tunnelcontroller
from mullvad import util
from mullvad import version

if platform.system() == 'Windows':
    from mullvad import mwinreg  # Our local module for win10 tap fix
else:
    class WindowsError(object):
        """For catching WindowsError on non Windows machines"""
        pass

try:
    if platform.linux_distribution()[0] == 'Fedora':
        print("Using GTK3")
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('AppIndicator3', '0.1')
        from gi.repository import Gtk as gtk
        from gi.repository import Gdk
        from gi.repository import AppIndicator3 as appindicator
    else:
        print("Using GTK2")
        import gtk
        gtk.remove_log_handlers()
        import appindicator

    got_appindicator = True
except ImportError:
    got_appindicator = False


_locale_dir = 'locale'

_locale = 'en'
_user_env = '/tmp/mullvad_user_env'

COMMAND_PORT = 14158
_SUBSCRIPTION_MANAGEMENT_URL = 'https://mullvad.net/'
_CLIENT_HELP_URL = 'https://mullvad.net/guides/category/mullvad-client/'

options = None


def set_mullvad_icon_on(frame):
    if platform.system() == 'Windows':
        ico = wx.Icon('mullvad.ico', wx.BITMAP_TYPE_ICO)
    else:
        ico = wx.Icon('mullvad.xpm', wx.BITMAP_TYPE_XPM)
    frame.SetIcon(ico)


class TrayUI():

    def __init__(self, parentWindow, tunnel, settings):
        self.log = logger.create_logger(self.__class__.__name__)
        self.settings = settings
        self.parentWindow = parentWindow
        self.settingsWindow = None
        self.tunnel = tunnel
        self.raised_error_messages = []
        # Start command server
        try:
            self.command_server = CommandServer(self)
        except Exception as e:
            self.command_server = None
            self.log.error('Failed to start command server: %s' % e)

        # Add listeners to tunnel
        self.tunnel.add_connection_listener(self.onConnectionChange)
        self.tunnel.add_error_listener(self._error_message)

    def _error_message(self, exception):
        message = None
        dialog = _error_dialog
        if type(exception) == mtunnel.SubscriptionExpiredError:
            message = _('The account has expired.\n\nManage account?')
            dialog = self.subscriptionDialog
        elif type(exception) == mtunnel.TAPMissingError:
            message = _('The TAP-Windows virtual network adapter is missing.'
                        '\n\nRestore it by running the installation '
                        'program again. (Quit the program first.)')
        elif type(exception) == mtunnel.ObfsproxyMissingError:
            message = _('Obfsproxy is not installed.\n\n'
                        'You can install it with:\n'
                        'apt-get install obfsproxy\n')
        else:
            details = unicode(exception)
            message = _('Connection failed.\n\nDetails:\n%s') % details
            self.log.debug(self.raised_error_messages)
        if message is not None and message not in self.raised_error_messages:
            self.raised_error_messages.append(message)
            wx.CallAfter(dialog, None, message)

    def subscriptionDialog(self, parent, message):
        self.log.debug("Showing subscription dialog")
        if options.quiet or options.startup:
            return
        dlg = NonModalDialog(parent, _('Mullvad'), message)
        dlg.Show(yes_callback=_open_subscription_management_webpage)

    def iconConnected(self):
        raise NotImplementedError

    def iconConnecting(self):
        raise NotImplementedError

    def iconDisconnected(self):
        raise NotImplementedError

    def showSettings(self):
        if self.settingsWindow is None:
            self.log.debug('Opening settings UI')
            self.settingsWindow = SettingsWindow(self.parentWindow, self,
                                                 self.settings)

    def safeShowSettings(self):
        """ Can be called outside of the main loop """
        wx.CallAfter(self.showSettings)
        return True

    def hideSettings(self):
        """ Close settings window """
        if self.settingsWindow is not None:
            try:
                self.settingsWindow.Close()
            except Exception:
                pass

    def close(self, event):
        wx.CallAfter(self.parentWindow.Close)

    def exit(self):
        self.tunnel.remove_connection_listener(self.onConnectionChange)
        self.tunnel.remove_error_listener(self._error_message)

        # Stop command server
        try:
            if self.command_server is not None:
                self.command_server.shutdown()
                self.command_server.join(4)
        except Exception, e:
            self.log.error('Failed closing command server: %s', e)

        # Destroy the setting window
        self.hideSettings()

        try:
            # Ask tunnel to shut down
            self.tunnel.shutDown()

            # Wait for status checker to die
            self.log.debug('Shut down')
        except Exception, e:
            self.log.error('Exception from tunnel: %s', e)

        # Destroy remaining parts of tunnel
        self.tunnel.destroy()

    def OnTaskBarConnect(self, evt):
        self.popupsAllowed = True
        self.connect()

    def OnTaskBarSettings(self, event):
        self.showSettings()

    def connect(self):
        # Clear raised error messages since user asked
        # to reconnect
        self.raised_error_messages = []
        self.tunnel.connect()
        options.startup = False

    def OnTaskBarDisconnect(self, evt):
        self.disconnect()
        self.log.debug('Disconnected from menu')

    def disconnect(self):
        self.tunnel.disconnect()

    def enableConnectMenu(self, enable):
        pass

    def enableDisconnectMenu(self, enable):
        pass

    def onConnectionChange(self, conState):
        if conState == mtunnel.ConState.connected:
            wx.CallAfter(self.iconConnected)
            self.enableConnectMenu(False)
            self.enableDisconnectMenu(True)
            if self.newInstallation:
                self.newInstallation = False
                timeLeft = self.tunnel.timeLeft()
                wx.CallAfter(WelcomeWindow,
                             self.parentWindow,
                             timeLeft,
                             self.settings)
        elif conState == mtunnel.ConState.connecting:
            wx.CallAfter(self.iconConnecting)
            self.enableConnectMenu(False)
            self.enableDisconnectMenu(True)
        else:
            wx.CallAfter(self.iconDisconnected)
            self.enableConnectMenu(True)
            self.enableDisconnectMenu(False)

    def onError(self, error):
        # If the user has actively choosen to connect and
        # there is an error, show it.
        if self.popupsAllowed:
            if error is not None:
                self.popupsAllowed = False
                self._error_message(error)


class AppIndicator(TrayUI):

    def __init__(self, parentWindow, tunnel, settings):
        TrayUI.__init__(self, parentWindow, tunnel, settings)
        self.settings = settings

        self.newInstallation = not self.settings.has_option('id')
        self.popupsAllowed = True

        # The menu
        menu = gtk.Menu()

        self.connectItem = gtk.MenuItem(_('Connect'))
        self.connectItem.connect('activate', self.OnTaskBarConnect)
        self.connectItem.show()
        menu.append(self.connectItem)

        self.disconnectItem = gtk.MenuItem(_('Disconnect'))
        self.disconnectItem.connect('activate', self.OnTaskBarDisconnect)
        self.disconnectItem.show()
        menu.append(self.disconnectItem)

        sep = gtk.SeparatorMenuItem()
        sep.show()
        menu.append(sep)

        settingsItem = gtk.MenuItem(_('Settings'))
        settingsItem.connect('activate', self.OnTaskBarSettings)
        settingsItem.show()
        menu.append(settingsItem)

        sep = gtk.SeparatorMenuItem()
        sep.show()
        menu.append(sep)

        quitItem = gtk.MenuItem(_('Quit'))
        quitItem.connect('activate', self.close)
        quitItem.show()
        menu.append(quitItem)

        # The appindicator
        # This needs improving, want to remove if statements per not compatible function
        if platform.linux_distribution()[0] == 'Fedora':
            self.ind = appindicator.Indicator.new('mullvad', 'mullvadr',
                                          appindicator.IndicatorCategory.COMMUNICATIONS)
            self.ind.set_menu(menu)
            self.ind.set_status(appindicator.IndicatorStatus.ACTIVE)

        else:
            self.ind = appindicator.Indicator('mullvad', 'mullvadr',
                                              appindicator.CATEGORY_COMMUNICATIONS)
            self.ind.set_menu(menu)
            self.ind.set_status(appindicator.STATUS_ACTIVE)

    def enableConnectMenu(self, enable):
        self.connectItem.set_sensitive(enable)

    def enableDisconnectMenu(self, enable):
        self.disconnectItem.set_sensitive(enable)

    def exit(self):
        TrayUI.exit(self)
        gtk.main_quit()

    def iconConnected(self):
        self.ind.set_icon('mullvadg')

    def iconConnecting(self):
        self.ind.set_icon('mullvady')

    def iconDisconnected(self):
        self.ind.set_icon('mullvadr')


class TunnelTaskBarIcon(wx.TaskBarIcon, TrayUI):
    TBMENU_CONNECT = wx.NewId()
    TBMENU_DISCONNECT = wx.NewId()
    TBMENU_SETTINGS = wx.NewId()
    TBMENU_EXIT = wx.NewId()
    ConnectVPNEvent, EVT_CONNECT_VPN = wx.lib.newevent.NewEvent()

    def __init__(self, parentWindow, tunnel, settings):
        wx.TaskBarIcon.__init__(self)

        self.osx_dock_icon = None
        if 'wxMac' in wx.PlatformInfo:
            self.osx_dock_icon = wx.TaskBarIcon(iconType=wx.TBI_DOCK)

        self.settings = settings
        self.newInstallation = not self.settings.has_option('id')
        self.popupsAllowed = True

        # Create the icons
        self.onIcon = self.MakeIcon(wx.Image('gdot.png'))
        self.offIcon = self.MakeIcon(wx.Image('rdot.png'))
        self.connectingIcon = self.MakeIcon(wx.Image('ydot.png'))

        # Bind some events
        self.Bind(wx.EVT_TASKBAR_LEFT_DCLICK, self.OnTaskBarSettings)
        self.Bind(wx.EVT_MENU, self.OnTaskBarConnect, id=self.TBMENU_CONNECT)
        self.Bind(wx.EVT_MENU, self.OnTaskBarDisconnect,
                  id=self.TBMENU_DISCONNECT)
        self.Bind(wx.EVT_MENU, self.OnTaskBarSettings, id=self.TBMENU_SETTINGS)
        self.Bind(wx.EVT_MENU, self.close, id=self.TBMENU_EXIT)
        self.Bind(self.EVT_CONNECT_VPN, self.OnTaskBarConnect)
        self.Bind(wx.EVT_CLOSE, self.close)

        # Set icon
        self.iconDisconnected()
        if 'wxGTK' in wx.PlatformInfo:
            # Else the icon sometimes gets too little space on the
            # Gnome notification area.
            time.sleep(0.1)
            self.iconDisconnected()

        TrayUI.__init__(self, parentWindow, tunnel, settings)

    def CreatePopupMenu(self):
        """
        This method is called by the base class when it needs to popup
        the menu for the default EVT_RIGHT_DOWN event.  Just create
        the menu how you want it and return it from this function,
        the base class takes care of the rest.
        """
        menu = wx.Menu()
        menu.Append(self.TBMENU_CONNECT, _('Connect'))
        menu.Append(self.TBMENU_DISCONNECT, _('Disconnect'))
        menu.AppendSeparator()
        menu.Append(self.TBMENU_SETTINGS, _('Settings'))
        if 'wxMac' not in wx.PlatformInfo:
            menu.AppendSeparator()
            menu.Append(self.TBMENU_EXIT, _('Quit'))
        desired = self.tunnel.desiredConnectionState()
        actual = self.tunnel.connectionState()
        if desired == mtunnel.ConState.connected and \
                actual != mtunnel.ConState.unrecoverable:
            menu.Enable(self.TBMENU_DISCONNECT, True)
            menu.Enable(self.TBMENU_CONNECT, False)
        else:
            menu.Enable(self.TBMENU_DISCONNECT, False)
            menu.Enable(self.TBMENU_CONNECT, True)
        return menu

    def MakeIcon(self, img):
        """The various platforms have different requirements for the
        icon size..."""
        if 'wxGTK' in wx.PlatformInfo:
            try:
                img = img.Scale(22, 22, quality=wx.IMAGE_QUALITY_HIGH)
            except AttributeError:
                img = img.Scale(22, 22)
        else:
            try:
                img = img.Scale(128, 128, quality=wx.IMAGE_QUALITY_HIGH)
            except AttributeError:
                img = img.Scale(128, 128)
        icon = wx.IconFromBitmap(img.ConvertToBitmap())
        return icon

    def iconConnected(self):
        self.SetIcon(self.onIcon, _('Mullvad: connected'))

    def iconConnecting(self):
        self.SetIcon(self.connectingIcon, _('Mullvad: connecting'))

    def iconDisconnected(self):
        self.SetIcon(self.offIcon, _('Mullvad: disconnected'))

    def exit(self):
        self.RemoveIcon()
        TrayUI.exit(self)
        wx.CallAfter(self.Destroy)

    def SetIcon(self, icon, tooltip):
        super(TunnelTaskBarIcon, self).SetIcon(icon, tooltip)
        if self.osx_dock_icon:
            self.osx_dock_icon.SetIcon(icon, tooltip)

    def RemoveIcon(self):
        super(TunnelTaskBarIcon, self).RemoveIcon()
        if self.osx_dock_icon:
            self.osx_dock_icon.RemoveIcon()

    def Destroy(self):
        super(TunnelTaskBarIcon, self).Destroy()
        if self.osx_dock_icon:
            self.osx_dock_icon.Destroy()


def _error_dialog(parent, message):
    _message_dialog(parent, message, _('Error'), wx.ICON_ERROR)


def _message_dialog(parent, message, title=None, type=wx.ICON_INFORMATION):
    if options.quiet or options.startup:
        return
    if title is None:
        title = _('Mullvad')
    dlg = wx.MessageDialog(parent, message, title, wx.OK | type)
    dlg.ShowModal()
    dlg.Destroy()


def _parse_options():
    parser = optparse.OptionParser()
    parser.add_option('-s', '--startup', dest='startup',
                      action='store_true', default=False,
                      help='long timeout and no error windows at '
                      'the first attempt')
    parser.add_option('-q', '--quiet', dest='quiet',
                      action='store_true', default=False,
                      help='don\'t pop up error windows')
    (options, args) = parser.parse_args()
    return options


class WelcomeWindow(wx.Frame):

    def __init__(self, parent, timeLeft, settings):
        wx.Frame.__init__(self, parent, title=_('Welcome to Mullvad'),
                          style=(wx.DEFAULT_FRAME_STYLE | wx.RESIZE_BORDER
                                 | wx.MINIMIZE_BOX | wx.MAXIMIZE_BOX))
        self.settings = settings

        set_mullvad_icon_on(self)

        self.Bind(wx.EVT_CLOSE, self.onClose)

        panel = wx.Panel(self)

        self._timeLeft = timeLeft

        bitmap = wx.Image('mullvad.png').ConvertToBitmap()
        image = wx.StaticBitmap(panel, bitmap=bitmap)

        welcomeText = wx.StaticText(panel, label=_('Welcome to Mullvad'))
        font = wx.Font(18, wx.SWISS, wx.NORMAL, wx.NORMAL)
        welcomeText.SetFont(font)

        descrText = wx.StaticText(
            panel, label=_('Your internet traffic is now encrypted \n'
                           'and anonymized using the Mullvad servers \n'
                           'in order to deny third parties access to \n'
                           'your communication.'))

        subscribeText = wx.StaticText(
            panel, label=_(
                'The account expires in {}.\n'
                'After that, time must be added\n'
                'to the account for continued\n'
                'use of the service.').format(self.timeLeft()))

        subscribeLink = wx.lib.hyperlink.HyperLinkCtrl(
            panel,
            wx.ID_ANY,
            _('Add time to the account'),
            URL=_SUBSCRIPTION_MANAGEMENT_URL)

        if platform.system() == 'Windows':
            # The label does not get localised automatically
            closeButton = wx.Button(panel, wx.ID_CLOSE, _('Close'))
        else:
            closeButton = wx.Button(panel, wx.ID_CLOSE)
        closeButton.Bind(wx.EVT_BUTTON, self.onClose)

        sizer = wx.BoxSizer(wx.VERTICAL)
        border = 5
        sizer.Add(image, flag=wx.ALL | wx.ALIGN_CENTER, border=border)
        sizer.Add(welcomeText, flag=wx.ALL | wx.ALIGN_CENTER, border=border)
        sizer.Add(descrText, flag=wx.ALL | wx.ALIGN_LEFT, border=border)
        sizer.Add(subscribeText, flag=wx.ALL | wx.ALIGN_LEFT, border=border)
        sizer.Add(subscribeLink, flag=wx.ALL | wx.ALIGN_LEFT, border=border)
        sizer.Add((0, 30))
        sizer.Add(closeButton, flag=wx.ALL | wx.ALIGN_CENTER, border=border)

        borderSizer = wx.BoxSizer(wx.VERTICAL)
        borderSizer.Add(sizer, flag=wx.ALL, border=15)

        panel.SetSizer(borderSizer)
        borderSizer.Fit(self)
        self.SetMinSize(borderSizer.GetMinSize())
        self.Show()

    def timeLeft(self):
        tl = self._timeLeft
        if tl < 3600 * 24:
            h = int(round(tl / float(3600)))
            text = str(h) + ' ' + _('hours')
        else:
            d = int(round(tl / float(3600 * 24)))
            if d == 1:
                text = str(d) + ' ' + _('day')
            else:
                text = str(d) + ' ' + _('days')
        return text

    def onClose(self, event):
        self.Destroy()


class GetStartedWindow(wx.Frame):
    def __init__(self, parent, tray_ui, settings):
        wx.Frame.__init__(self, parent, title='Mullvad',
                          style=(wx.DEFAULT_FRAME_STYLE | wx.RESIZE_BORDER
                                 | wx.MINIMIZE_BOX | wx.MAXIMIZE_BOX))
        self.settings = settings
        self.tray_ui = tray_ui

        set_mullvad_icon_on(self)

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.create_get_started_panel()
        self.Show()
        self.cid_number.SetFocus()

    def create_get_started_panel(self):
        panel = wx.Panel(self)

        mullvad_image = self.create_mullvad_image_in(panel)
        login_form = self.create_login_form_in(panel)

        border = 8
        hsizer = wx.BoxSizer(wx.HORIZONTAL)
        hsizer.Add(mullvad_image, flag=wx.ALL | wx.ALIGN_CENTER, border=border)
        hsizer.Add(login_form, flag=wx.ALL, border=border)

        border_sizer = wx.BoxSizer(wx.VERTICAL)
        border_sizer.Add(hsizer, flag=wx.ALL, border=15)

        panel.SetSizer(border_sizer)
        border_sizer.Fit(self)
        panel.SetMinSize(border_sizer.GetMinSize())
        self.SetMinSize(panel.GetMinSize())
        return panel

    def create_mullvad_image_in(self, parent):
        bitmap = wx.Image('mullvad.png').ConvertToBitmap()
        return wx.StaticBitmap(parent, bitmap=bitmap)

    def create_login_form_in(self, parent):
        title = wx.StaticText(parent, label=_('Get started'))
        title_font = wx.Font(18, wx.SWISS, wx.NORMAL, wx.NORMAL)
        title.SetFont(title_font)

        cid_text = _('Please enter your account number:')
        cid_label = wx.StaticText(parent, label=cid_text)

        self.cid_number = wx.TextCtrl(parent, style=wx.TE_PROCESS_ENTER)
        self.cid_number.Bind(wx.EVT_TEXT_ENTER, self.on_connect)

        connect_button = wx.Button(parent, label=_('Connect'))
        connect_button.Bind(wx.EVT_BUTTON, self.on_connect)
        connect_button.SetFocus()

        create_account_text = _('Don\'t have an account? Create one\n'
                                'on our website:')
        create_account_label = wx.StaticText(parent, label=create_account_text)

        link = wx.lib.hyperlink.HyperLinkCtrl(
            parent,
            wx.ID_ANY,
            'https://mullvad.net',
            URL='https://mullvad.net/')

        free_time_text = _('Your new account will include three\n'
                           'free hours of VPN time.')
        free_time_label = wx.StaticText(parent, label=free_time_text)

        controls_sizer = wx.BoxSizer(wx.VERTICAL)
        controls_sizer.Add(title,
                           flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTER,
                           border=5)
        controls_sizer.Add((0, 5))
        controls_sizer.Add(cid_label, flag=wx.BOTTOM, border=10)
        controls_sizer.Add(self.cid_number,
                           flag=wx.BOTTOM | wx.EXPAND,
                           border=5)
        controls_sizer.Add(connect_button, flag=wx.ALIGN_RIGHT)

        controls_sizer.Add((0, 15))
        controls_sizer.Add(create_account_label)
        controls_sizer.Add(link, flag=wx.TOP | wx.BOTTOM, border=5)
        controls_sizer.Add(free_time_label)
        return controls_sizer

    def on_connect(self, event):
        try:
            cid = int(self.cid_number.GetValue())
        except ValueError:
            _error_dialog(self, _('Bad account number.'))
        else:
            self.settings.set('id', cid)
            self.tray_ui.connect()
            self.tray_ui.safeShowSettings()
            self.Destroy()

    def on_close(self, event):
        self.GetParent().Close()  # Close the entire program
        try:
            self.Destroy()
        except wx.PyDeadObjectError:
            # We've already been here and done that
            pass


class ProblemReportWindow(wx.Frame):

    def __init__(self, parent, settings):
        wx.Frame.__init__(self, parent, title=_('Error report'))

        set_mullvad_icon_on(self)

        self.Bind(wx.EVT_CLOSE, self.onClose)

        self.report = problemreport.ProblemReport(settings)

        initialSize = wx.Size(500, 650)
        panel = wx.Panel(self)
        descriptionText = wx.StaticText(
            panel, label=_('To facilitate problem solving you can '
                           'send debug information to mullvad.net.'))
        descriptionText.Wrap(initialSize.width - 10)  # Some margin for error
        emailText = wx.StaticText(panel, label=_('Your email address:'))
        self.emailEntry = wx.TextCtrl(panel)
        typeMessageText = wx.StaticText(
            panel, label=_('Write a description of the problem:'))
        self.messageEntry = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        thisInfoText = wx.StaticText(
            panel, label=_('This information will be sent:'))
        example = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        example.SetValue(self.report.get_report_text())
        example.SetEditable(False)
        cancelButton = wx.Button(panel, label=_('Cancel'))
        cancelButton.Bind(wx.EVT_BUTTON, self.onClose)
        sendButton = wx.Button(panel, label=_('Send'))
        sendButton.Bind(wx.EVT_BUTTON, self.onSend)
        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
        buttonSizer.Add((0, 0), 1)
        buttonSizer.Add(cancelButton, 0)
        buttonSizer.Add((0, 0), 1)
        buttonSizer.Add(sendButton, 0)
        buttonSizer.Add((0, 0), 1)

        sizer = wx.BoxSizer(wx.VERTICAL)
        border = 5
        sizer.Add(descriptionText, 0, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(emailText, 0, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(self.emailEntry, 0, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(typeMessageText, 0, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(self.messageEntry, 1, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(thisInfoText, 0, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(example, 2, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(buttonSizer, 0, flag=wx.ALL | wx.EXPAND, border=border)

        panel.SetSizer(sizer)
        panel.SetSize(initialSize)
        self.emailEntry.SetFocus()
        self.Fit()
        self.Show()

    def onClose(self, event):
        self.Destroy()

    def onSend(self, event):
        self.report.set_email_address(self.emailEntry.GetValue())
        self.report.set_user_message(self.messageEntry.GetValue())
        try:
            self.report.send()
        except IOError, e:
            wx.CallAfter(_error_dialog, self,
                         _('Sending failed.\n\nDetails:\n') +
                         unicode(str(e), errors='replace'))
        else:
            self.Destroy()


class ConfigEditorWindow(wx.Dialog):

    def __init__(self, parent, settings):
        wx.Dialog.__init__(self, parent, title=_('Configuration file'))
        self.settings = settings

        set_mullvad_icon_on(self)

        initialSize = wx.Size(500, 650)
        panel = wx.Panel(self)
        self.editor = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.editor.SetValue(str(self.settings))
        cancelButton = wx.Button(panel, wx.ID_CANCEL)
        cancelButton.Bind(wx.EVT_BUTTON, self.onClose)
        okButton = wx.Button(panel, wx.ID_OK)
        okButton.Bind(wx.EVT_BUTTON, self.onOk)
        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
        buttonSizer.Add((0, 0), 1)
        buttonSizer.Add(cancelButton, 0)
        buttonSizer.Add((0, 0), 1)
        buttonSizer.Add(okButton, 0)
        buttonSizer.Add((0, 0), 1)

        sizer = wx.BoxSizer(wx.VERTICAL)
        border = 5
        sizer.Add(self.editor, 1, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(buttonSizer, 0, flag=wx.ALL | wx.EXPAND, border=border)

        panel.SetSizer(sizer)
        panel.SetSize(initialSize)
        self.editor.SetFocus()
        self.Fit()

    def onClose(self, event):
        self.EndModal(0)

    def onOk(self, event):
        new_settings = self.editor.GetValue()
        try:
            self.settings.bulk_set(new_settings)
        except (ConfigParser.Error, IOError, WindowsError) as e:
            wx.CallAfter(_error_dialog, self,
                         unicode(str(e), errors='replace'))
        else:
            self.EndModal(0)


class PageCommand(wx.Panel):

    def __init__(self, parent, countryNames, settings):
        wx.Panel.__init__(self, parent)
        self.log = logger.create_logger(self.__class__.__name__)
        self.settings = settings
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.server = None

        border = 5

        self.countryNames = countryNames

        self.connectionState = {
            mtunnel.ConState.connected: _('Connected'),
            mtunnel.ConState.disconnected: _('Disconnected'),
            mtunnel.ConState.connecting: _('Connecting'),
            mtunnel.ConState.off: _('Shutting down'),
            mtunnel.ConState.unrecoverable: _('Disconnected')}

        # Account box
        ab = wx.StaticBox(self, label=_('Account'))
        accountSizer = wx.StaticBoxSizer(ab, wx.VERTICAL)
        self.timeText = wx.StaticText(
            self, label=_('Time left: {}').format(_('fetching ...')))
        accountSizer.Add(self.timeText, flag=wx.ALL | wx.EXPAND)

        # Connection box
        cb = wx.StaticBox(self, label=_('Connection'))
        connectionSizer = wx.StaticBoxSizer(cb, wx.VERTICAL)

        # Adding row of spaces to widen the window to accomodate IPv6
        self.statusText = wx.StaticText(
            self, label=_('Status: {}').format(70 * ' '))
        connectionSizer.Add(self.statusText, flag=wx.ALL | wx.EXPAND)

        self.countryText = wx.StaticText(
            self, label=_('Country: {}').format(''))
        connectionSizer.Add(self.countryText, flag=wx.ALL | wx.EXPAND)

        connectionSizer.Add((-1, 10))
        # Display exit IP addresses in readonly TextCtrls without borders and
        # backgrounds in order to enable selecting and copying while
        # maintaining a consistent look
        self.ip4Text = wx.StaticText(self, label=_('IPv4: '))
        self.ip4Field = wx.TextCtrl(self, style=wx.TE_READONLY)
        self.ip4Field.SetBackgroundColour(self.ip4Text.GetBackgroundColour())
        ip4Sizer = wx.BoxSizer(wx.HORIZONTAL)
        ip4Sizer.Add(self.ip4Text, flag=wx.ALIGN_CENTER_VERTICAL)
        ip4Sizer.Add(self.ip4Field, 1, wx.ALIGN_CENTER_VERTICAL)
        connectionSizer.Add(ip4Sizer, flag=wx.ALL | wx.EXPAND)

        self.ip6Text = wx.StaticText(self, label=_('IPv6: '))
        self.ip6Field = wx.TextCtrl(self, style=wx.TE_READONLY)
        self.ip6Field.SetBackgroundColour(self.ip6Text.GetBackgroundColour())
        ip6Sizer = wx.BoxSizer(wx.HORIZONTAL)
        ip6Sizer.Add(self.ip6Text, flag=wx.ALIGN_CENTER_VERTICAL)
        ip6Sizer.Add(self.ip6Field, 1, wx.ALIGN_CENTER_VERTICAL)
        connectionSizer.Add(ip6Sizer, flag=wx.ALL | wx.EXPAND)
        connectionSizer.Add((-1, 10))

        # UGLY OSX GUI HACK
        # Create a hidden text field that steals focus from the IP address
        # fields in order to get a more consistent look in OSX
        self.secretField = wx.TextCtrl(self)
        connectionSizer.Add(self.secretField, flag=wx.ALL | wx.EXPAND)
        self.secretField.SetFocus()
        self.secretField.Hide()

        self.connectButton = wx.Button(self, label=_('Connect'))
        self.disconnectButton = wx.Button(self, label=_('Disconnect'))
        connButtonSizer = wx.BoxSizer(wx.HORIZONTAL)
        connButtonSizer.Add((0, 0), 1, flag=wx.ALL | wx.EXPAND)
        connButtonSizer.Add(self.disconnectButton, flag=wx.ALL | wx.EXPAND)
        connButtonSizer.Add((0, 0), 1, flag=wx.ALL | wx.EXPAND)
        connButtonSizer.Add(self.connectButton, flag=wx.ALL | wx.EXPAND)
        connButtonSizer.Add((0, 0), 1, flag=wx.ALL | wx.EXPAND)
        connectionSizer.Add(
            connButtonSizer, flag=wx.ALL | wx.EXPAND, border=border)

        # Server box
        serverBox = wx.StaticBox(self, label=_('Server'))
        serverSizer = wx.StaticBoxSizer(serverBox, wx.VERTICAL)
        self.serverText = wx.StaticText(
            self, label=_('Address: {}').format(''))
        serverSizer.Add(self.serverText, flag=wx.ALL | wx.EXPAND)
        self.portText = wx.StaticText(self, label=_('Port: {}').format(''))
        serverSizer.Add(self.portText, flag=wx.ALL | wx.EXPAND)
        self.protocolText = wx.StaticText(
            self, label=_('Protocol: {}').format(''))
        serverSizer.Add(self.protocolText, flag=wx.ALL | wx.EXPAND)

        # Version box
        vb = wx.StaticBox(self, label=_('Version'))
        versionSizer = wx.StaticBoxSizer(vb, wx.VERTICAL)
        self.currentVersionText = wx.StaticText(
            self,
            label=_('Current version: {}').format(version.CLIENT_VERSION))
        versionSizer.Add(self.currentVersionText, flag=wx.ALL | wx.EXPAND)
        self.latestVersionText = wx.StaticText(
            self, label=_('Latest version: {}').format(''))
        versionSizer.Add(self.latestVersionText, flag=wx.ALL | wx.EXPAND)

        self.quitButton = wx.Button(self, label=_('Quit'))

        quitSizer = wx.BoxSizer(wx.HORIZONTAL)
        quitSizer.Add((0, 0), 1, flag=wx.ALL | wx.EXPAND)
        quitSizer.Add(self.quitButton, 2, flag=wx.ALL | wx.EXPAND)
        quitSizer.Add((0, 0), 1, flag=wx.ALL | wx.EXPAND)

        sizer.Add(accountSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(connectionSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(serverSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(versionSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add((0, 10), 1, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(quitSizer, flag=wx.ALL | wx.EXPAND, border=border)

        self.SetSizer(sizer)

    def timeLeft(self, timeLeft):
        tl = timeLeft
        if tl < 3600 * 24:
            h = int(round(tl / float(3600)))
            if h == 1:
                text = str(h) + ' ' + _('hour')
            else:
                text = str(h) + ' ' + _('hours')
        else:
            d = int(round(tl / float(3600 * 24)))
            if d == 1:
                text = str(d) + ' ' + _('day')
            else:
                text = str(d) + ' ' + _('days')
        return text

    def setTimeLeft(self, time):
        try:
            if time is None:
                wx.CallAfter(
                    self.timeText.SetLabel,
                    _('Time left: {}').format(_('fetching ...')))
            else:
                time_left = self.timeLeft(max(0, time))
                wx.CallAfter(
                    self.timeText.SetLabel,
                    _('Time left: {}').format(time_left))
        except Exception as e:
            self.log.error('Unable to update gui: %s', e)

    def setLatestVersion(self, latestVersion):
        try:
            if latestVersion is None:
                wx.CallAfter(
                    self.latestVersionText.SetLabel,
                    _('Latest version: {}').format(_('unknown')))
            else:
                wx.CallAfter(
                    self.latestVersionText.SetLabel,
                    _('Latest version: {}').format(str(latestVersion)))
        except Exception as e:
            self.log.error('Unable to update gui: %s', e)

    def setServer(self, server):
        try:
            if server is not None:
                # Save the server object in order to access the name when
                # updating the exit IP address
                self.server = server
                country = self.countryNames.get(server.location,
                                                server.location.upper())
                wx.CallAfter(self.countryText.SetLabel,
                             _('Country: {}').format(country))
                wx.CallAfter(self.serverText.SetLabel,
                             _('Address: {}').format(server.name))
                wx.CallAfter(self.portText.SetLabel,
                             _('Port: {}').format(server.port))
                wx.CallAfter(self.protocolText.SetLabel,
                             _('Protocol: {}').format(server.protocol))
            else:
                wx.CallAfter(self.countryText.SetLabel,
                             _('Country: {}').format(''))
                wx.CallAfter(self.serverText.SetLabel,
                             _('Address: {}').format(''))
                wx.CallAfter(self.portText.SetLabel,
                             _('Port: {}').format(''))
                wx.CallAfter(self.protocolText.SetLabel,
                             _('Protocol: {}').format(''))
        except Exception as e:
            self.log.error('Unable to update gui: %s', e)

    def _set_exit_address(self):
        fail_msg = _('Unable to fetch address')
        ipv4 = self._get_exit_address(socket.AF_INET)
        self.log.info('Got IPv4 exit address: %s', ipv4)
        wx.CallAfter(self.ip4Field.SetValue, ipv4 or fail_msg)

        if self.settings.getboolean('tunnel_ipv6'):
            ipv6 = self._get_exit_address(socket.AF_INET6)
            self.log.info('Got IPv6 exit address: %s', ipv6)
            wx.CallAfter(self.ip6Field.SetValue, ipv6 or fail_msg)

    def _get_exit_address(self, family):
        exit_addr = None
        family_name = 'IPv4' if family == socket.AF_INET else 'IPv6'
        try:
            master = mullvadclient.MullvadClient('ipaddress.mullvad.net',
                                                 family=family,
                                                 timeout=7)
            master.version()
            exit_addr = master.getExitAddress()
            master.quit()
        except socket.error:
            self.log.error('Failed to retrieve %s address from master',
                           family_name)

        # Fall back to running a DNS lookup to get the IP
        if exit_addr is None and self.server:
            try:
                xname = self.server.name.split('.')[0] + 'x.mullvad.net'
                ainfo = socket.getaddrinfo(xname, 1234)
                ips = [a for a in ainfo if a[0] == family]
                if ips:
                    exit_addr = ips[0][4][0]
            except socket.error:
                self.log.error(
                    'Unable to get %s address through DNS lookup of x-host',
                    family_name)
        return exit_addr

    def setConnectionState(self, state):
        try:
            wx.CallAfter(self.statusText.SetLabel,
                         _('Status: {}').format(self.connectionState[state]))
        except Exception:
            try:
                wx.CallAfter(self.statusText.SetLabel,
                             _('error {}').format(str(state)))
            except Exception:
                pass

        if state == mtunnel.ConState.connected:
            # Start new thread to avoid blocking the tunnel thread monitoring
            # connection states
            threading.Thread(target=self._set_exit_address).start()
        else:
            # Clear the IP field if not connected
            wx.CallAfter(self.ip4Field.SetValue, '')
            wx.CallAfter(self.ip6Field.SetValue, '')


class PageCommandController:

    def __init__(self, parent, tray_ui, countryNames, settings):
        self.log = logger.create_logger(self.__class__.__name__)
        self.settings = settings
        self.updateTimerLock = threading.Lock()
        self.timerThreadRunning = True
        self.tray_ui = tray_ui
        self.view = PageCommand(parent, countryNames, settings)
        self.view.quitButton.Bind(wx.EVT_BUTTON, self.exit)
        self.view.connectButton.Bind(wx.EVT_BUTTON, self.connect)
        self.view.disconnectButton.Bind(wx.EVT_BUTTON, self.disconnect)

        # Display the state of the connection
        self.setConnectionState(self.tray_ui.tunnel.connectionState())

        # Display the server that tunnel is connected to
        self.onServerChange(self.tray_ui.tunnel.serverInfo())

        # Update graphics according to connection state
        self.tray_ui.tunnel.add_connection_listener(self.setConnectionState)

        # Display server info if tunnel connects to new server
        self.tray_ui.tunnel.add_server_listener(self.onServerChange)

        # Update the time left
        self.updateTimerLock.acquire()
        self.updateTimerThread = threading.Timer(0.1, self.updateTimer)
        self.updateTimerThread.start()
        self.updateTimerLock.release()

    def connect(self, event):
        self.updateTimer()
        self.log.debug('Connected from command page')
        self.tray_ui.connect()

    def disconnect(self, event):
        self.log.debug('Disconnected from command page')
        self.tray_ui.disconnect()

    def exit(self, event):
        self.log.debug('Exit from command page')
        self.tray_ui.close(None)

    def onServerChange(self, server):
        try:
            self.view.setServer(server)
        except Exception:
            pass

    def updateTimer(self):
        """Acquire timer lock and update client
        information accordingly."""
        self.updateTimerLock.acquire()
        if self.timerThreadRunning:
            # Shut down timer if already running
            try:
                self.updateTimerThread.cancel()
            except Exception:
                pass
            timeLeft = self.updateVersionAndTimeLeft()
            if timeLeft is not None:
                # Update in 30 minutes if recieve timeleft
                nextUpdate = 1800
            else:
                # Update in 1 minutes if failed to recieve timeleft
                nextUpdate = 60
            self.updateTimerThread = threading.Timer(
                nextUpdate,
                self.updateTimer)
            self.updateTimerThread.start()
        self.updateTimerLock.release()

    def updateVersionAndTimeLeft(self):
        """Get the forwarded latest client version and
        time left from master."""
        try:
            self.view.setTimeLeft(None)
            self.view.setLatestVersion(None)
        except Exception:
            # The SettingsWindow is gone. It doesn't matter if we
            # can't update it then!
            return None

        account = self.settings.get_or_none('id')
        try:
            master = mullvadclient.MullvadClient('master.mullvad.net', timeout=7)
            master.version()
            timeLeft = master.getSubscriptionTimeLeft(account)
            latestVersion = master.getLatestVersion()
            master.quit()
            self.view.setTimeLeft(timeLeft)
            self.view.setLatestVersion(latestVersion)
        except wx.PyDeadObjectError:
            # The SettingsWindow is gone. It doesn't matter if we
            # can't update it then!
            return None
        except Exception:
            return None
        return timeLeft

    def destroy(self):
        try:
            self.tray_ui.tunnel.removeServerListener(self.onServerChange)
            self.tray_ui.tunnel.removeConnectionListener(
                self.setConnectionState)
        except Exception:
            pass
        self.updateTimerLock.acquire()
        try:
            self.timerThreadRunning = False
            self.updateTimerThread.cancel()
        except Exception:
            pass
        self.updateTimerLock.release()

    def setConnectionState(self, state):
        self.log.debug('Setting connection state: %s', state)
        try:
            if state == mtunnel.ConState.connected:
                wx.CallAfter(self.view.connectButton.Disable)
                wx.CallAfter(self.view.disconnectButton.Enable)
            elif state == mtunnel.ConState.connecting:
                wx.CallAfter(self.view.connectButton.Disable)
                wx.CallAfter(self.view.disconnectButton.Enable)
            else:
                wx.CallAfter(self.view.connectButton.Enable)
                wx.CallAfter(self.view.disconnectButton.Disable)
            self.view.setConnectionState(state)
        except Exception:
            pass


class PageSettings(wx.Panel):

    def __init__(self, parent, countryNames, settings):
        wx.Panel.__init__(self, parent)
        self.settings = settings
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.ports = []
        self.maxPorts = 0  # We don't know yet

        border = 5

        # Account box
        ab = wx.StaticBox(self, label=_('Account'))
        accountSizer = wx.StaticBoxSizer(ab, wx.VERTICAL)

        cid = self.settings.get_or_none('id')
        if cid is None:
            cidTxt = _('none')
        else:
            cidTxt = str(cid)
        cidText = wx.StaticText(self, label=_('Account number: '))
        self.cidNumber = wx.TextCtrl(self, value=cidTxt)
        self.cidNumber.SetEditable(False)
        self.cidNumber.SetBackgroundColour(cidText.GetBackgroundColour())
        cidSizer = wx.BoxSizer(wx.HORIZONTAL)
        cidSizer.Add(cidText, flag=wx.ALIGN_CENTER_VERTICAL)
        cidSizer.Add(self.cidNumber, 1, wx.ALIGN_CENTER_VERTICAL)

        # Change customer ID
        self.changeIdButton = wx.Button(self, label=_('Change account number'))

        # Subscription management
        self.manageButton = wx.Button(self, label=_('Account management'))
        if cid is None:
            self.manageButton.Enable(False)

        accountSizer.Add(cidSizer, flag=wx.ALL | wx.EXPAND, border=border)
        accountSizer.Add(self.changeIdButton, flag=wx.ALL, border=border)
        accountSizer.Add(self.manageButton, flag=wx.ALL, border=border)

        # Network box
        nb = wx.StaticBox(self, label=_('Network'))
        networkSizer = wx.StaticBoxSizer(nb, wx.VERTICAL)

        # Forwarded port
        portstr = _('fetching ...')
        portText = wx.StaticText(self, label=_('Ports: '))
        self.portNumber = wx.TextCtrl(self, value=portstr)
        self.portNumber.SetEditable(False)
        self.portNumber.SetBackgroundColour(portText.GetBackgroundColour())
        self.newPortButton = wx.Button(self, label=_('Manage'))
        portSizer = wx.BoxSizer(wx.HORIZONTAL)
        portSizer.Add(portText, flag=wx.ALIGN_CENTER_VERTICAL)
        portSizer.Add(self.portNumber, 1, wx.ALL | wx.EXPAND)
        portSizer.Add((10, 0))
        portSizer.Add(self.newPortButton, flag=wx.ALIGN_CENTER_VERTICAL)

        # Delete default route
        block_internet_text = _('Block the internet on connection failure')
        self.defaultRouteCheck = wx.CheckBox(self, label=block_internet_text)
        ddfState = self.settings.getboolean('delete_default_route')
        self.defaultRouteCheck.SetValue(ddfState)

        # Tunnel IPv6
        self.tunnel_ipv6 = wx.CheckBox(self, label=_('Tunnel IPv6'))
        ipv6_tunnel_setting = self.settings.getboolean('tunnel_ipv6')
        self.tunnel_ipv6.SetValue(ipv6_tunnel_setting)

        # Stop DNS leaks
        self.leakCheck = wx.CheckBox(self, label=_('Stop DNS leaks'))
        self.leakCheck.SetValue(self.settings.getboolean('stop_dns_leaks'))

        self.countryCodes = dict((v, k) for k, v in countryNames.iteritems())
        currentCountry = self.settings.get_or_none('location')
        if currentCountry is None:
            currentCountry = 'xx'  # Any

        availableCountries = self.getCountries()
        countries = []
        for countryCode in availableCountries:
            # Show the country name if we know it, otherwise the country code
            countries.append(countryNames.get(countryCode, countryCode))
        countries.sort()
        countries.insert(0, _('Any'))
        countryText = wx.StaticText(self, label=_('Country: '))
        countrySelector = wx.ComboBox(
            self,
            value=countryNames.get(currentCountry, currentCountry),
            choices=countries,
            style=wx.CB_READONLY)
        countrySizer = wx.BoxSizer(wx.HORIZONTAL)
        countrySizer.Add(countryText, flag=wx.ALIGN_CENTER_VERTICAL)
        countrySizer.Add(countrySelector, 1, wx.ALL | wx.EXPAND)
        self.countrySelector = countrySelector

        # Add components to network box
        networkSizer.Add(portSizer, flag=wx.ALL | wx.EXPAND, border=border)
        networkSizer.Add(self.defaultRouteCheck, flag=wx.ALL, border=border)
        networkSizer.Add(self.leakCheck, flag=wx.ALL, border=border)

        networkSizer.Add(self.tunnel_ipv6, flag=wx.ALL, border=border)
        networkSizer.Add(countrySizer, flag=wx.ALL | wx.EXPAND, border=border)

        # Problem report and Advanced button sizer
        probadvSizer = wx.BoxSizer(wx.HORIZONTAL)

        # Autostart
        if platform.system() == 'Windows':
            self.cbxAutostart = wx.CheckBox(self, label=_('Auto start'))
            self.cbxAutostart.SetValue(self.getAutostart())
            probadvSizer.Add(self.cbxAutostart)
            probadvSizer.Add((1, 1), 1)  # Align the views to the edges

        # Advanced settings button
        self.advancedButton = wx.Button(self, label=_('Advanced'))
        probadvSizer.Add(self.advancedButton)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(accountSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(networkSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(probadvSizer, flag=wx.ALL | wx.EXPAND, border=border)

        self.SetSizer(sizer)

    def getCountries(self):
        with open('backupservers.txt', 'r') as f:
            servers = [serverinfo.ServerInfo(line) for line in f]
        countries = set(server.location for server in servers)
        return countries

    def getAutostart(self):
        if platform.system() == 'Windows':
            command = ['schtasks', '/Query', '/tn', 'Mullvad']
            try:
                return proc.run_get_exit(command) == 0
            except Exception as e:
                self.log.error('shtasks failed to query: %s' % e)
            return False
        else:
            return False


class PageSettingsController:

    def __init__(self, parent, caller, countryNames, settings):
        self.log = logger.create_logger(self.__class__.__name__)
        self.settings = settings
        self.ports = []
        self.maxPorts = 0  # We don't know yet

        self.parent = parent
        self.caller = caller

        self.accountListeners = []

        self.view = PageSettings(parent, countryNames, settings)
        self.view.manageButton.Bind(
            wx.EVT_BUTTON, self._open_subscription_management_webpage
        )
        self.view.advancedButton.Bind(wx.EVT_BUTTON,
                                      self.on_advanced_button)
        self.view.tunnel_ipv6.Bind(wx.EVT_CHECKBOX, self.onTunnelIPv6)
        self.view.leakCheck.Bind(wx.EVT_CHECKBOX, self.onLeakCheck)
        self.view.defaultRouteCheck.Bind(wx.EVT_CHECKBOX,
                                         self.onDefaultRouteCheck)
        self.view.countrySelector.Bind(wx.EVT_COMBOBOX,
                                       self.onCountrySelection)

        # Try to add hooks to autostart
        try:
            self.view.cbxAutostart.Bind(wx.EVT_CHECKBOX, self.onAutostart)
        except Exception:
            pass

        self.view.changeIdButton.Bind(wx.EVT_BUTTON, self.onChangeCustomerId)
        self.view.newPortButton.Bind(wx.EVT_BUTTON, self.onManagePorts)

        # Fetch port numbers from master
        self.updateTimerThread = threading.Timer(0.1, self.updateTimer)
        self.updateTimerThread.start()

    def _open_subscription_management_webpage(self, event):
        _open_subscription_management_webpage()

    def updateTimer(self):
        """Get the forwarded time left from master."""
        self.getPorts()

    def on_advanced_button(self, event):
        config_editor = ConfigEditorWindow(self.parent, self.settings)
        config_editor.ShowModal()

    def onAutostart(self, event):
        try:
            self.setAutostart(event.IsChecked())
        except Exception as e:
            self.log.error('Autostart failed %s' % e)

    def onTunnelIPv6(self, event):
        self.settings.set('tunnel_ipv6', event.IsChecked())

    def onLeakCheck(self, event):
        self.settings.set('stop_dns_leaks', event.IsChecked())

    def onDefaultRouteCheck(self, event):
        self.settings.set('delete_default_route', event.IsChecked())

    def onManagePorts(self, event):
        pd = PortsDialog(self.parent, self.ports, self.maxPorts,
                         self.settings.get('id'))
        pd.ShowModal()
        self.ports = pd.ports
        pd.Destroy()
        self.refreshPortsField()

    def onChangeCustomerId(self, event):
        dlg = wx.TextEntryDialog(
            self.parent, _('Enter the account number'),
            _('Change account number'))
        try:
            cid = str(self.settings.get('id'))
        except ConfigParser.NoOptionError:
            cid = ''
        dlg.SetValue(cid)
        id = None
        if dlg.ShowModal() == wx.ID_OK:
            try:
                id = int(dlg.GetValue())
            except ValueError:
                id = None
            if id is None:
                self.view.cidNumber.SetValue(_('Bad'))
            else:
                self.settings.set('id', id)
                self.view.cidNumber.SetValue(str(id))
        dlg.Destroy()
        self.refreshPortsField()

        # Notify listeners that id has changed
        try:
            self.updateAccountListeners(id)
        except Exception:
            pass

    def onCountrySelection(self, event):
        description = self.view.countrySelector.GetValue(
        )  # Name or country code
        countryCode = self.view.countryCodes.get(description, description)
        self.settings.set('location', countryCode)

    def refreshPortsField(self):
        portStr = _('unknown')
        if self.ports is None:
            portStr = _('unknown')
        elif self.ports == []:
            portStr = _('none [port]')
        else:
            portStr = str(self.ports)[1:-1]  # Just chop off the brackets
        try:
            wx.CallAfter(self.view.portNumber.SetValue, portStr)
        except wx.PyDeadObjectError:
            # The dialog has been closed
            pass

    def getPorts(self):
        """Get the forwarded port numbers from master."""
        try:
            master = mullvadclient.MullvadClient('master.mullvad.net', timeout=7)
            master.version()
            customerId = None
            if self.settings.has_option('id'):
                customerId = self.settings.get('id')
            self.ports = master.getPorts(customerId)
            self.maxPorts = master.getMaxPorts()
            master.quit()
        except Exception, e:
            self.log.error('Failed to get ports: %s', e)
            self.ports = None
        try:
            self.refreshPortsField()
        except wx.PyDeadObjectError:
            # The SettingsWindow is gone. It doesn't matter if we
            # can't update it then!
            pass

    def addAccountListener(self, listener):
        self.accountListeners.append(listener)

    def removeAccountListener(self, listener):
        self.accountListeners.remove(listener)

    def updateAccountListeners(self, account):
        for l in self.accountListeners:
            l(account)

    def destroy(self):
        try:
            self.updateTimerThread.cancel()
        except Exception:
            pass

    def setAutostartWindows(self, enable):
        self.log.info('Setting autostart to: %s',
                      'Enabled' if enable else 'Disabled')
        if platform.system() == 'Windows':
            if enable:
                try:
                    config = ''
                    with open('autostart_config_template.xml', 'r') as f:
                        config = f.read()

                    with open('autostart_config.xml', 'wb+') as f:
                        f.write(
                            config.format(os.getenv('USERNAME'),
                                          os.getcwd() + '\\mullvad.exe'))

                    command = ['schtasks', '/Create', '/tn', 'Mullvad',
                               '/xml', 'autostart_config.xml', '/F']
                    proc.run_assert_ok(command)
                except Exception, e:
                    self.log.error('Failed to add schtask: %s', e)
            else:
                command = ['schtasks', '/Delete', '/tn', 'Mullvad', '/F']
                try:
                    proc.run_assert_ok(command)
                except Exception, e:
                    self.log.error('Failed to delete schtask: %s', e)

    def setAutostart(self, enable):
        if os.name == 'nt':
            self.setAutostartWindows(enable)
        else:
            pass


class SettingsWindow(wx.Frame):

    def __init__(self, parent, caller, settings):
        self.log = logger.create_logger(self.__class__.__name__)
        self.settings = settings
        wx.Frame.__init__(self, parent, title=_('Settings for Mullvad'))

        set_mullvad_icon_on(self)

        # Exit country
        countrynames = {
            'at': _('Austria'),
            'au': _('Australia'),
            'be': _('Belgium'),
            'bg': _('Bulgaria'),
            'br': _('Brazil'),
            'ca': _('Canada'),
            'ca-ab': _('Canada - Alberta'),
            'ca-bc': _('Canada - British Columbia'),
            'ca-on': _('Canada - Ontario'),
            'ca-qc': _('Canada - Quebec'),
            'ch': _('Switzerland'),
            'cz': _('Czech Republic'),
            'de': _('Germany'),
            'dk': _('Denmark'),
            'es': _('Spain'),
            'fi': _('Finland'),
            'fr': _('France'),
            'gb': _('United Kingdom'),
            'gb-eng': _('United Kingdom - England'),
            'gb-nir': _('United Kingdom - Northern Ireland'),
            'gb-sct': _('United Kingdom - Scotland'),
            'gb-wls': _('United Kingdom - Wales'),
            'gr': _('Greece'),
            'hk': _('Hong Kong'),
            'hu': _('Hungary'),
            'il': _('Israel'),
            'is': _('Iceland'),
            'it': _('Italy'),
            'jp': _('Japan'),
            'kr': _('South Korea'),
            'lt': _('Lithuania'),
            'ma': _('Morocco'),
            'mx': _('Mexico'),
            'nl': _('Netherlands'),
            'no': _('Norway'),
            'nz': _('New Zealand'),
            'pl': _('Poland'),
            'pt': _('Portugal'),
            'ro': _('Romania'),
            'se': _('Sweden'),
            'se-hel': _('Sweden - Helsingborg'),
            'se-got': _('Sweden - Gothenburg'),
            'se-mma': _(u'Sweden - Malmö'),
            'se-sto': _('Sweden - Stockholm'),
            'sg': _('Singapore'),
            'tw': _('Taiwan'),
            'ua': _('Ukraine'),
            'us': _('USA'),
            'us-al': _('USA - Alabama'),
            'us-ak': _('USA - Alaska'),
            'us-az': _('USA - Arizona'),
            'us-ar': _('USA - Arkansas'),
            'us-ca': _('USA - California'),
            'us-co': _('USA - Colorado'),
            'us-ct': _('USA - Connecticut'),
            'us-de': _('USA - Delaware'),
            'us-fl': _('USA - Florida'),
            'us-ga': _('USA - Georgia'),
            'us-hi': _('USA - Hawaii'),
            'us-id': _('USA - Idaho'),
            'us-il': _('USA - Illinois'),
            'us-in': _('USA - Indiana'),
            'us-ia': _('USA - Iowa'),
            'us-ks': _('USA - Kansas'),
            'us-ky': _('USA - Kentucky'),
            'us-la': _('USA - Louisiana'),
            'us-me': _('USA - Maine'),
            'us-md': _('USA - Maryland'),
            'us-mi': _('USA - Michigan'),
            'us-mn': _('USA - Minnesota'),
            'us-mo': _('USA - Missouri'),
            'us-mt': _('USA - Montana'),
            'us-ne': _('USA - Nebraska'),
            'us-nv': _('USA - Nevada'),
            'us-nh': _('USA - New Hampshire'),
            'us-nj': _('USA - New Jersey'),
            'us-ny': _('USA - New York'),
            'us-nc': _('USA - North Carolina'),
            'us-nd': _('USA - North Dakota'),
            'us-oh': _('USA - Ohio'),
            'us-ok': _('USA - Oklahoma'),
            'us-or': _('USA - Oregon'),
            'us-pa': _('USA - Pennsylvania'),
            'us-ri': _('USA - Rhode Island'),
            'us-sc': _('USA - South Carolina'),
            'us-sd': _('USA - South Dakota'),
            'us-tn': _('USA - Tennessee'),
            'us-tx': _('USA - Texas'),
            'us-ut': _('USA - Utah'),
            'us-vt': _('USA - Vermont'),
            'us-va': _('USA - Virginia'),
            'us-wa': _('USA - Washington'),
            'us-wv': _('USA - West Virginia'),
            'us-wi': _('USA - Wisconsin'),
            'us-wy': _('USA - Wyoming'),
            'xx': _('Any')
        }

        self.Bind(wx.EVT_CLOSE, self.onOK)

        self.caller = caller

        border = 5
        panel = wx.Panel(self)

        # Tabs
        tabs = wx.Notebook(panel, style=wx.BK_DEFAULT)
        self.command_controller = PageCommandController(tabs, caller,
                                                        countrynames,
                                                        settings)
        page_command = self.command_controller.view
        tabs.AddPage(page_command, _('Status'))
        self.settings_controller = PageSettingsController(tabs, caller,
                                                          countrynames,
                                                          settings)
        page_settings = self.settings_controller.view
        tabs.AddPage(page_settings, _('Settings'))

        tabSizer = wx.BoxSizer(wx.VERTICAL)
        tabSizer.Add(tabs, 1, wx.ALL | wx.EXPAND)

        # Report problems
        reportProblemButton = wx.Button(panel, label=_('Error report'))
        reportProblemButton.Bind(wx.EVT_BUTTON, self.reportProblem)

        # Help and OK buttons
        if platform.system() == 'Windows':
            # The label does not get localised automatically
            helpButton = wx.Button(panel, wx.ID_HELP, _('Help'))
        else:
            helpButton = wx.Button(panel, wx.ID_HELP)

        helpSizer = wx.BoxSizer(wx.HORIZONTAL)
        helpSizer.Add(reportProblemButton)
        # Align the buttons to the edges by grabbing the most possible space
        # between them
        helpSizer.Add((1, 1), 1)
        helpSizer.Add(helpButton)
        helpButton.Bind(wx.EVT_BUTTON, self.on_help)

        # Update time when new account it selected
        self.settings_controller.addAccountListener(
            self.command_controller.updateVersionAndTimeLeft)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(tabSizer, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add((0, 10), 1, flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(helpSizer, flag=wx.ALL | wx.EXPAND, border=border)

        panel.SetSizer(sizer)
        sizer.Fit(self)
        self.SetMinSize(sizer.GetMinSize())
        self.Show()

    def reportProblem(self, event):
        self.reportWindow = ProblemReportWindow(self, self.settings)

    def onOK(self, event):
        self.Close()

    def Close(self, event=None):
        self.log.debug('Closing SettingsWindow')
        self.Hide()

        # Destroy subcontrollers
        self.command_controller.destroy()
        self.settings_controller.destroy()

        self.caller.settingsWindow = None
        try:
            self.reportWindow.Hide()
            self.reportWindow.destroy()
        except Exception:
            pass
        self.Destroy()

    def on_help(self, event):
        webbrowser.open(_CLIENT_HELP_URL)

    def setServer(self, server):
        self.command_controller.setServer(server)


class PortsDialog(wx.Dialog):

    def __init__(self, parent, ports, maxPorts, customerId):
        wx.Dialog.__init__(self, parent, title=_('Port manager'))
        self.ports = ports
        self.maxPorts = maxPorts
        self.customerId = customerId
        border = 5

        reconnectMsg = _('Manage which public ports will be forwarded to\n'
                         'your computer through the VPN tunnel.\n'
                         'Only use this if you have a network service on\n'
                         'your computer that needs to be accessible through\n'
                         'the exit IP of the server you connect to.\n'
                         '\n'
                         'Example:\n'
                         'If you get port 1234 forwarded and are connected\n'
                         'to server se5.mullvad.net, then you will be\n'
                         'reachable on se5x.mullvad.net:1234\n'
                         '\n'
                         'Changes take effect after reconnecting.')
        reconnectText = wx.StaticText(self, label=reconnectMsg)

        self.listbox = wx.ListBox(self, size=(-1, 150), style=wx.LB_SINGLE)
        self.Bind(wx.EVT_LISTBOX, self.onPortSelected, self.listbox)

        self.addButton = wx.Button(self, label=_('Add'))
        self.addButton.Bind(wx.EVT_BUTTON, self.onAdd)
        self.removeButton = wx.Button(self, label=_('Remove'))
        self.removeButton.Enable(False)
        self.removeButton.Bind(wx.EVT_BUTTON, self.onRemove)
        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
        buttonSizer.Add(self.addButton, flag=wx.ALL, border=border)
        # Align the buttons to the edges by grabbing the most possible space
        # between them
        buttonSizer.Add((1, 1), 1)
        buttonSizer.Add(self.removeButton, flag=wx.ALL, border=border)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(reconnectText, flag=wx.ALL, border=border)
        sizer.Add(self.listbox, 1, wx.ALL | wx.EXPAND, border=border)
        sizer.Add(buttonSizer, flag=wx.EXPAND)

        self.refreshPortList()
        if self.ports is not None and len(self.ports) >= 1:
            self.listbox.Select(0)
            self.removeButton.Enable(True)

        borderSizer = wx.BoxSizer(wx.VERTICAL)
        borderSizer.Add(sizer, flag=wx.ALL | wx.EXPAND, border=10)
        self.SetSizer(borderSizer)
        self.Fit()

    def refreshPortList(self):
        if self.ports is not None:
            self.addButton.Enable(len(self.ports) < self.maxPorts)
            self.listbox.Clear()
            for port in sorted(self.ports):
                self.listbox.Append(str(port))
        else:
            self.addButton.Enable(False)
            self.removeButton.Enable(False)

    def onPortSelected(self, event):
        index = self.listbox.GetSelection()
        self.removeButton.Enable(index != wx.NOT_FOUND)

    def onAdd(self, event):
        try:
            master = mullvadclient.MullvadClient('master.mullvad.net', timeout=7)
            master.version()
            self.ports = master.getNewPort(self.customerId)
            master.quit()
        except Exception, e:
            _error_dialog(self, unicode(str(e), errors='replace'))
        self.refreshPortList()

    def onRemove(self, event):
        index = self.listbox.GetSelection()
        if index == wx.NOT_FOUND:
            return
        port = int(self.listbox.GetString(index))
        try:
            master = mullvadclient.MullvadClient('master.mullvad.net', timeout=7)
            master.version()
            self.ports = master.removePort(self.customerId, port)
            master.quit()
        except Exception, e:
            _error_dialog(self, unicode(str(e), errors='replace'))
        self.removeButton.Enable(False)
        self.refreshPortList()


class MullvadApp(wx.App):

    def MacReopenApp(self):
        """ Open settings window when pressing the Mullvad icon in the dock.
        """
        try:
            self.tray_ui.showSettings()
        except Exception:
            pass


class CommandServer(Thread):

    def __init__(self, tray_ui, port=COMMAND_PORT):
        Thread.__init__(self)
        self.log = logger.create_logger(self.__class__.__name__)

        self.command_server = SimpleXMLRPCServer(
            ('localhost', port),
            logRequests=False,
            bind_and_activate=False)
        self.command_server.allow_reuse_address = True
        self.command_server.server_bind()
        self.command_server.server_activate()
        self.command_server.register_function(
            tray_ui.safeShowSettings, 'showSettings')
        self.start()

    def run(self):
        self.command_server.serve_forever()
        self.log.debug('CommandServer dying')

    def shutdown(self):
        try:
            self.command_server.shutdown()
        except Exception as e:
            self.log.error('Failed to stop command server: %s' % e)


class KillerWindow(wx.Frame):

    """The Quit menu on the mac dock won't get a close signal to the
    TaskBarIcon, but a Frame will receive one."""

    def __init__(self):
        wx.Frame.__init__(self, None)
        self.victim = None
        self.Bind(wx.EVT_CLOSE, self.onClose)

    def onClose(self, event):
        if self.victim is not None:
            wx.CallAfter(self.victim.exit)
        self.Destroy()


class NonModalDialog(wx.Frame):
    def __init__(self, parent, title, message):
        wx.Frame.__init__(
            self,
            parent,
            title=title,
            style=wx.CAPTION | wx.FRAME_TOOL_WINDOW)
        self.log = logger.create_logger(self.__class__.__name__)

        self.yes_callback = None
        self.no_callback = None

        self._create_gui(message)
        self.yesbutton.Bind(wx.EVT_BUTTON, self._on_yes)
        self.nobutton.Bind(wx.EVT_BUTTON, self._on_no)
        self.Bind(wx.EVT_CLOSE, self._on_no)

    def _create_gui(self, message):
        panel = wx.Panel(self)

        mainsizer = wx.BoxSizer(wx.VERTICAL)

        ctrlsizer = wx.BoxSizer(wx.HORIZONTAL)
        ctrlsizer.Add(wx.StaticText(panel, label=message),
                      flag=wx.ALL, border=10)

        mainsizer.Add(ctrlsizer, 1, wx.EXPAND)

        buttonsizer = wx.BoxSizer(wx.HORIZONTAL)
        self.nobutton = wx.Button(panel, wx.ID_NO, _("No"))
        self.yesbutton = wx.Button(panel, wx.ID_YES, _("Yes"))

        buttonsizer.Add(self.yesbutton, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        buttonsizer.Add(self.nobutton, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        mainsizer.Add(buttonsizer)

        panel.SetSizer(mainsizer)
        panel.Layout()
        mainsizer.Fit(self)
        self.yesbutton.SetDefault()

    def Show(self, yes_callback=None, no_callback=None):
        self.yes_callback = yes_callback
        self.no_callback = no_callback

        wx.Frame.Show(self)
        self.Raise()

    def _on_yes(self, event):
        self.log.debug("Yes clicked")
        try:
            if self.yes_callback:
                self.yes_callback()
        finally:
            self.Destroy()

    def _on_no(self, event):
        self.log.debug("No clicked")
        try:
            if self.no_callback:
                self.no_callback()
        finally:
            self.Destroy()


def _open_subscription_management_webpage():
    webbrowser.open(_SUBSCRIPTION_MANAGEMENT_URL)


def l10n():
    """Localize."""
    mopath = gettext.find('mullvad', _locale_dir)
    if mopath is None:
        # $LANG probably not set. Try the default language.
        try:
            lang = locale.getdefaultlocale()[0][:2]
        except (ValueError, TypeError):
            # getdefaultlocale() doesn't work on Mac :-(
            translation = gettext.translation('mullvad', _locale_dir, ['en'])
            lang = 'en'
        else:
            try:
                translation = gettext.translation(
                    'mullvad', _locale_dir, [lang])
            except IOError:
                # No translation for that language, fall back to English
                translation = gettext.translation(
                    'mullvad', _locale_dir, ['en'])
                lang = 'en'
    else:
        translation = gettext.translation('mullvad', _locale_dir)
        langCodePos = len(_locale_dir) + 1
        lang = mopath[langCodePos: langCodePos + 2]
    translation.install(unicode=True)
    return lang


def _chdir():
    """Chdir to program directory"""
    prog_dir = os.path.dirname(sys.argv[0])
    if platform.system() not in ('Windows', 'Darwin'):
        prog_dir = os.path.dirname(__file__)
    print("changing directory to", prog_dir)
    os.chdir(prog_dir)


def _elevate():
    """Elevate user permissions if needed"""
    if platform.system() == 'Darwin':
        try:
            os.setuid(0)
        except OSError:
            _mac_elevate()


def _mac_elevate():
    """Relaunch asking for root privileges."""
    print('Relaunching Mullvad with root permissions')
    error_log = logger.get_error_log_path()
    print('Output will be written to:', error_log)
    applescript = ('do shell script "../MacOS/Mullvad > \\"{}\\" 2>&1"'
                   ' with administrator privileges'.format(error_log))
    subprocess.Popen(['osascript', '-e', applescript])
    sys.exit()


def _create_lockfile():
    lfile = lockfile.LockFile()
    if not lfile.lock():
        # Open settings UI of running process through command server
        timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(1)
        try:
            xmlrpc_url = 'http://localhost:{}'.format(COMMAND_PORT)
            server = xmlrpclib.ServerProxy(xmlrpc_url)
            server.showSettings()
        except Exception as e:
            print('Failed to connect to CommandServer:', e)
        finally:
            socket.setdefaulttimeout(timeout)
        return None
    return lfile


def _release_lockfile(lfile):
    lfile.release()


def _create_unix_pipes():
    pipe_dir = os.path.join('/run', 'user', str(os.getuid()))
    if not os.path.exists(pipe_dir):
        pipe_dir = paths.get_config_dir()

    pipes = [
        os.path.join(pipe_dir, 'request_pipe'),
        os.path.join(pipe_dir, 'reply_pipe'),
        os.path.join(pipe_dir, 'update_pipe'),
    ]
    for pipe in pipes:
        if os.path.exists(pipe):
            os.remove(pipe)
        os.mkfifo(pipe)
    return pipe_dir, pipes


def _release_unix_pipes(pipes):
    for pipe in pipes:
        os.remove(pipe)


def _create_settings(log):
    """Init a settings object.

    Returns:
        The settings instance. None or path to backup settings file if it was
        corrupt and reset.
    """
    try:
        settings = config.Settings()
    except (ConfigParser.ParsingError,
            ConfigParser.MissingSectionHeaderError):
        log.error('Error during settings parsing. Backing up corrupt settings'
                  ' and resetting the default settings.')
        backup_settings_path = config.backup_reset_settings()
        settings = config.Settings()
        wx.CallAfter(
            _error_dialog,
            None,
            _('Your settings.ini file was corrupt.\n'
              'A backup of your settings have been saved to:\n') +
            backup_settings_path +
            _('\n\nYour settings have been reset to default values.'))
    return settings


def _start(app, root_window):
    if not _startup_checks():
        root_window.Close()
    else:
        lfile = _create_lockfile()
        if lfile is None:
            _error_dialog(None,
                          _('It seems that Mullvad is already running.\n'
                            'Only one instance of Mullvad is allowed.\n\n'))
            root_window.Close()
        else:
            atexit.register(_release_lockfile, lfile)
            logger.backup_reset_debug_log()
            log = logger.create_logger('mullvad_main')
            log.info('Starting Mullvad version %s', version.CLIENT_VERSION)
            log.info('Platform: %s', util.get_platform())
            exceptioncatcher.activate(log)
            settings = _create_settings(log)

            _startup_fixes(log)
            tunnel = _create_tunnel(settings)
            _start_gui(app, root_window, log, settings, tunnel)


def _startup_checks():
    """Check system state early, show errors and return status.

    Returns:
        True if everything is fine, False if the application should abort.

    """
    if platform.system() == 'Windows' and platform.release() == 'XP':
        _error_dialog(None,
                      _('This version of Mullvad does not support '
                        'Windows XP. Please upgrade your operating system, '
                        'or use a plain OpenVPN connection.'))
        return False
    if platform.system() == 'Darwin' and os.getcwd().startswith('/Volumes/'):
        _error_dialog(None,
                      _('You are running Mullvad from the install image.\n'
                        'This is not supported, you have to first copy the'
                        ' program to the Applications directory and run it'
                        ' from there.'))
        return False
    return True


def _startup_fixes(log):
    """Perform general fixes at an early startup phase."""
    if platform.system() == 'Windows':
        mwinreg.fix_win10_tap(log)

    # If there are unrestored DNS server settings, restore them
    try:
        dnsconfig.get_dnsconfig().restore()
    except Exception:
        pass


def _create_tunnel(settings):
    if platform.system() == 'Linux':
        pipe_dir, pipes = _create_unix_pipes()
        atexit.register(_release_unix_pipes, pipes)
        tunnel = tunnelcontroller.TunnelController(pipe_dir)
    else:
        tunnel = mtunnel.Tunnel(settings)
    return tunnel


def _start_gui(app, root_window, log, settings, tunnel):
    if got_appindicator:
        tray_ui = AppIndicator(root_window, tunnel, settings)
        if platform.linux_distribution()[0] == 'Fedora':
            Gdk.threads_init()
        else:
            gtk.gdk.threads_init()

    else:
        tray_ui = TunnelTaskBarIcon(root_window, tunnel, settings)
    app.tray_ui = tray_ui

    root_window.victim = tray_ui

    if tray_ui.newInstallation:
        GetStartedWindow(root_window, tray_ui, settings)
    else:
        if settings.getboolean('autoconnect_on_start'):
            tray_ui.connect()
        tray_ui.safeShowSettings()


def main():
    global _locale, options
    _chdir()
    logger.init()
    _elevate()
    _locale = l10n()
    options = _parse_options()

    app = MullvadApp(False)
    root_window = KillerWindow()

    wx.CallAfter(_start, app, root_window)
    app.MainLoop()


if __name__ == '__main__':
    main()
