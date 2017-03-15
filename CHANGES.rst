Mullvad 62 (2017-03-14)
=======================
- Make fewer connection attempts when fetching the server list before setting
  up a tunnel. Should improve the time it takes to connect the VPN tunnel.
- Add fallback for failed string decoding. Removes some crashes on systems with
  certain languages. Experienced on Chinese Windows.
- Make account expired dialog non-modal, thus not blocking the entire client.
  Helps keep the leak protection active for cases where account time runs out
  during active usage of the tunnel.
- Add Turkish translation.
- Add revoked server certificates to CRL.

Windows specific
----------------
- Try to detect Windows shutdown and stop trying to change routes in that state.
- Upgrade OpenVPN to 2.4.0 and OpenSSL to 1.0.2k.

MacOS specific
--------------
- Upgrade OpenVPN to 2.4.0.

Linux specific
--------------
- Add MULLVAD_USE_GTK3 environment variable to handle wxPython 3 differently.
  Should make the client work on Fedora 25. Set the environment variable to
  "yes" or "no" to toggle between the modes.


Mullvad 61 (2016-11-23)
=======================
- Add more country and region names for better display of server location.
- Adapt links in client to URL structure of new Mullvad website.
- Remove possibility to create trial account from the client. Use the homepage,
  https://mullvad.net instead.
- Change the way our account server is contacted. Helps people with DNS
  problems.

Windows specific
----------------
- Upgrade bundled OpenVPN to 2.3.13 and OpenSSL to 1.0.1u.
- Fix bug that prevented the GUI from showing up.
- Handle errors while setting DNS in a better way so client does not crash.
- Turn 'Stop DNS leaks' on by default. This works better for most users.
- Fix bug that sometimes added incorrect gateway when restoring settings on
  exit.

Mac OS X specific
-----------------
- Upgrade bundled OpenVPN to 2.3.12.


Mullvad 60 (2016-08-08)
=======================
- Check settings for errors on load. Prevents some crashes when settings.ini
  is malformed. Fixes rare 'Is not a boolean'-error.
- Fix bug that prevented connection details from being shown when connected to
  a server in a previously unknown country.
- Add advanced option for controlling if the client should automatically
  connect when starting. Default is still to connect automatically.
- Add support for more specific server regions than just countries.
- Change default country from any (random selection) to Sweden.
- Clean out unused OpenVPN directives from the configuration.

Windows specific
----------------
- Fix bug that would restore persistent routing table entries as non-persistent.


Mullvad 59 (2016-05-25)
=======================
- Show UI before doing any network requests for faster client startup.
- Better description of some UI elements.
- Small UI bug fixes.
- Fall back to UTF-8 on systems without a default encoding/locale.
- Remove code tests from releases.
- Remove logging in obfsproxy to be more portable between obfsproxy versions.
- Add possibility to specify network socket buffer sizes in the advanced
  settings. Can increase throughput in some cases. Mainly on high latency UDP.

Linux specific
--------------
- Add python-package-resources as a dependency for deb package.
- Bundle OpenVPN's DNS setup script for systems lacking a built in one.

Windows specific
----------------
- Greatly speed up DNS management by not waiting for a network timeout.
- Upgrade OpenVPN to 2.3.11 and OpenSSL to 1.0.1t.
- Upgrade TAP network driver to 9.21.2.
- Increase default socket buffer sizes from 8 kiB to 128 kiB on Windows 7
  for better throughput on high latency connections over UDP.

Mac OS X specific
-----------------
- Upgrade OpenVPN to 2.3.10
- Allow incoming UDP from LAN even if block_incoming_udp is on. Improves
  stability and possibility to communicate with printers etc.
- Fix bug where connectivity problems could result in an error dialog and block
  further connection attempts.


Mullvad 58 (2016-01-20)
=======================
- Fix bug preventing client from connecting if no firewall is active. Mainly
  affects Windows but could potentially occur on other platforms as well.


Mullvad 57 (2016-01-18)
=======================
- Add new setting to block incoming UDP traffic.
- Fix a bug where the help button in non-major client versions leads to an
  invalid page url.

Windows specific
----------------
- Fix bug caused by unexpected output when parsing routing tables.
- Fix bug in the parsing of network interface lists.
- Upgrade the bundled OpenVPN binary to 2.3.9.
- Enable the new '--block-outside-dns' feature in OpenVPN.
- Bundle and enable the 'block-incoming-udp' plugin in OpenVPN.
- Remove the DNS-leak warning message from the GUI since '--block-outside-dns'
  fixes the problem.


Mullvad 56 (2015-12-14)
=======================
- Include platform information in the log to help debugging.
- Include more platform information in problem report such as OS architecture
  and locale.

Windows specific
--------------
- Fix bug caused by unexpected output when parsing routing tables.
- Include information about client and OS version in installer log.
- Fix bug caused by non-ascii characters in OS error messages.
- Correctly report OS version on Windows 10.


Mullvad 55 (2015-11-30)
=======================
- Clarify installation instructions in README.
- Remove all direct configuration calls from the GUI controls.

Windows specific
--------------
- Handle encoding problems triggered by OS error output.
- Refactor route management class for handling multiple interfaces properly.
- Add a warning about potential DNS leaks on Windows 8 and 10 and recommend
  enabling 'Block internet on connection failure' to fully prevent leaks.
- Fix bug caused by storing IPv6 DNS server addresses in an incorrect format.

Linux specific
--------------
- Support versions of python-psutil older than 2.0.
- Fix bug where a new redundant flag would be added to an iptables command
  for every instantiation of the LinuxFirewall class.
- Add some extra logging to facilitate debugging of issues with the
  inter-process communication.


Mullvad 54 (2015-11-03)
=======================
- Let clientversion be a string to allow point-versions.
- Simplify default gateway monitor scheduler.
- Fix bugs triggered by OS error ouput with non-ascii characters.
- Update the IP address in the included DNS backup file to the current one.
- Fix bug causing client to get stuck with non-matching key and certificate.
- Improve handling and killing of OpenVPN processes.
- Fix issue with locked OpenVPN log files by using an incrementing counter
  in the file name.
- Limit the connect timeout to only apply to setting up the tunnel.

Linux Specific
--------------
- Avoid using the --wait flag in iptables if the available version does not
  support it.


Mullvad 53 (2015-09-22)
=======================
- More effective and cleaner shutdown of OpenVPN.
- Do not depend on output in specific language for executed commands.
  Fixes problems with non English operating systems.
- Remove the exclude_swedish feature.

Windows specific
----------------
- Check if Windows Firewall service is running. If not then disable the
  block_local_network option and require tunneling of IPv6.
- Fix small GUI glitch around version numbers.
- Make the installer check if Mullvad is running, if so tell the user
  to quit Mullvad before upgrading.

Mac OS X specific
-----------------
- Make it impossible to run Mullvad directly from the dmg image.
- Fix bug related to setting DNS on inactive network services.


Mullvad 52 (2015-09-16)
=======================
- Better error logging and problem reports.
- Correctly verify master cert, not a security issue but checked cert too often.
- Remove disable_ipv6 option. Now not having tunnel_ipv6 is the same thing.
- Client can recover from a corrupted settings file.
- Fix bug with lock file and making sure only one client is running.
- Improved server selection.
- Fix bug where changing settings during an active VPN connection
  sometimes created problems.

Windows specific
----------------
- Fix text encoding bug for users with non ASCII letters in their username.
- Drop support for Windows XP. The client will no longer run on Windows XP.
- Upgraded TAP driver.
- Fix bug preventing tunneling IPv6 traffic when blocking local network
- Better management of file locks, getting rid of a common Windows 10 bug.

Mac OS X specific
-----------------
- Correctly set DNS on all interfaces when using 'Stop DNS leaks'.
- Connection status shown in docker icon again, as in versions before 51.
- Fix bug that triggered segmentation faults on some mac computers.

Linux specific
--------------
- Allow IPv6 loopback traffic when IPv6 is blocked.
- Fix bug to allow account id to be changed in the client on Debian.
- Fix bug that made the client crash if IPv6 was not present in the kernel.
- More stable usage of iptables in the client.


Mullvad 51 (2015-08-03)
=======================
- Settings and logs moved to platform standard directories.
- Improved problem reports and their content.
- Output warnings and errors in the terminal.
- All settings are included in settings.conf, none are hidden.
- Fix rare bug on machines that can't resolve "localhost".

Windows specific
----------------
- Support for Windows 10.
- Fix bug with Stop DNS leaks that some users experienced.

Mac OS X specific
-----------------
- Fix bug that filled /etc/pf.conf with many more anchors than needed.
- The account number does not have to be reentered for every upgrade/reinstall.

Linux specific
--------------
- Fix bug that always blocked IPv6 when block_local_network was on.


Mullvad 50 (2015-06-29)
=======================
- Add option to block the local network using firewall rules to prevent the DNS
  hijack exploit.
- Fix a bug which made it impossible to use obfsproxy.

Linux specific
--------------
- Use wxPython3.0 instead of 2.8 to support Debian Jessie.
- GUI-thread no longer starts as root, which fixes som app indicator issues.
- Fix a bug where settings would reset on restart.


Mullvad 49 (2015-03-04)
=======================
- Limit range of possible TLS cipher-suites by adding tls-cipher list to
  OpenVPN client configuration files to protect against FREAK.

Mac OS X specific
-----------------
- Upgrade to OpenVPN 2.3.6 and OpenSSL 1.0.1k.
- Fix DNS setting monitoring.

Windows specific
----------------
- Upgrade to OpenSSL 1.0.2.


Mullvad 48 (2015-02-25)
=======================
- Include file with nameserver to be used by Stop DNS leaks if connection to
master fails.

Mac OS X specific
-----------------
- Fixed a corner-case where Stop DNS leaks would not work correctly
if master was not reachable.

Windows specific
----------------
- Stop DNS leaks should now work with any system language.


Mullvad 47 (2015-02-20)
=======================
- Ability to use both AES-256-CBC and BF-CBC.
- Reorganized the structure of the client source directories.
- Updated the build process to work with the new package structure.

Mac OS X specific
-----------------
- Stop DNS leaks should now work on interfaces with a space in their name.
- Automated the DMG installer build process in the setup.py script.

Windows specific
----------------
- Updated OpenVPN to 2.3.6, which among other things resolves issues with adding.
  routes for interfaces with special characters in their name.
- Stop DNS leaks now works on interfaces with special characters in their name.


Mullvad 46 (2014-12-05)
=======================
- Added current and latest version number display in GUI.
- Fixed bug which caused the same alert message to be displayed multiple times.
- Refactored and extended documentation of OpenVPN config files.


Mullvad 45 (2014-10-24)
=======================
- Fixed bug where GUI would hang and show green check mark while disconnected.

Mac OS X specific
-----------------
- Removed calls to deprecated firewall to support OS X Yosemite.


Mullvad 44 (2014-09-01)
=======================
- Added support for tunneling IPv6 traffic.
- Display IPv6 exit address in status tab.
- Handle communication with master over IPv6.
- Added checkbox to toggle tunneling of IPv6 traffic.
- Change communication with OpenVPN management interface to use one continuous
  connection.
- Removed "Exclude Swedish Traffic" checkbox.
- Removed the DEFAULT section from the settings file.
- Added option to change OpenVPN connection timeout.

Windows specific
----------------
- Updated detection of missing TAP drivers.

Mac OS X specific
-----------------
- Re-enabled the "Stop DNS leaks" functionality.


Mullvad 43 (2014-04-09)
=======================
- Added certificate revocation list to prevent potential abusers of the nasty
  openssl heartbleed bug from pretending to be servers signed by us.

Mac OS X specific
-----------------
- Updated tunnelblick which contains fix for openssl heartbleed bug.

Windows specific
----------------
- Updated bundled openvpn with fix for the heartbleed bug.


Mullvad 42 (2014-03-26)
=======================
- Relevant windows are now frames, not dialogs.
- UI overhaul. There are now two tabs in the settings window; status and
  settings.
- The status tab shows time left, connection status, current IP, country and the
  server which you are connected to.
- You are able to connect, disconnect and quit from the status tab.
- Settings window is now opened if trying to start mullvad when it's already
  running.
- Improved shutdown of the client.
- Always run as python2 to work without change on more system configurations
- Now correctly removes all IPv6 blocks.
- Not being able to connect to our master server should less likely result in
  DNS problems when "Stop DNS leaks" is enabled.
- No more blocking of connection attempts to master through our vpn servers when
  "Block internet on connection failure" is enabled.
- Various code cleanups and bugfixes.

Linux specific
--------------
- Will not try to drop root unless it's possible.
- Now correctly uses fallback if python-appindicator is not present.
- Now sets the correct user-id when dropping root.
- Startup script now works when only su is available.

Mac OS X specific
-----------------
- Settings window can now be opened by left-clicking mullvad in the dock.
- Updated tunnelblick to 3.4beta20 to work correctly on mavericks.
- Now correctly shows "Stop DNS leaks" as disabled in mavericks since it's not
  working correctly there.

Windows specific
----------------
- Added autostart feature.
- Forcefully kill openvpn if failing to close with telnet.
- Now restores multiple DNS servers if "Block DNS leaks" is enabled, not just
  one.
- Updated openvpn and TAP-drivers to version 2.3.2 and build version 07/02/2012
  respectively.
- The UI overhaul (described above) should improve the experience on windows 8
  when using modern UI.
