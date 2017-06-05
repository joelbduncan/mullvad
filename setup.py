import os
import sys
import platform
import glob
import subprocess
from setuptools import setup, find_packages


CLIENT_VERSION = None
execfile('src/mullvad/version.py')

base_dir = os.path.dirname(__file__)
with open(os.path.join(base_dir, 'README.rst')) as f:
    long_description = f.read()

with open(os.path.join(base_dir, 'CHANGES.rst')) as f:
    long_description = '\n'.join([long_description, f.read()])

common_args = dict(
    version=CLIENT_VERSION,
    description='The Mullvad VPN Client',
    long_description=long_description,
    url='https://www.mullvad.net/',
    author='Amagicom AB',
    author_email='admin@mullvad.net',
    license='GPL-2+',

    classifiers=[
        ('License :: OSI Approved :: '
            'GNU General Public License v2 or later (GPLv2+)'),
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: End Users/Desktop',
        'Natural Language :: English',
        'Natural Language :: French',
        'Natural Language :: Swedish',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft :: Windows',
        'Programming Language :: Python :: 2 :: Only',
        'Topic :: Internet',
        'Topic :: Security',
    ],

    keywords='vpn privacy anonymity security',

    package_dir={
        '': 'src',
    },

    packages=find_packages('src', exclude=['tests']),

    install_requires=[
        'appdirs',
        'ipaddr',
        'netifaces',
        'psutil',
        'wxPython',
    ],

    extras_require={
        'obfsproxy': ['obfsproxy'],
    },
)

MANIFEST_TEMPLATE = """
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity
    version="5.0.0.0"
    processorArchitecture="x86"
    name="%(prog)s"
    type="win32"
  />
  <description>%(prog)s</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v2">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel
            level="requireAdministrator"
            uiAccess="false">
        </requestedExecutionLevel>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity
            type="win32"
            name="Microsoft.VC90.CRT"
            version="9.0.21022.8"
            processorArchitecture="x86"
            publicKeyToken="1fc8b3b9a1e18e3b">
      </assemblyIdentity>
    </dependentAssembly>
  </dependency>
  <dependency>
    <dependentAssembly>
        <assemblyIdentity
            type="win32"
            name="Microsoft.Windows.Common-Controls"
            version="6.0.0.0"
            processorArchitecture="X86"
            publicKeyToken="6595b64144ccf1df"
            language="*"
        />
    </dependentAssembly>
  </dependency>
</assembly>
"""

APPLE_SCRIPT = """
tell application "Finder"
    tell disk "wc"
        open

        tell container window
            set current view to icon view
            set toolbar visible to false
            set statusbar visible to false
            set the bounds to {0, 0, 512, 256}
            -- set statusbar visible to false
        end tell

        set opts to the icon view options of container window

        tell opts
            set icon size to 128
            set arrangement to not arranged
        end tell

        -- TODO Uncomment when background image scaling problem is solved
        -- set background picture of opts to file ".background:mullvad_background.png"

        tell container window
            set position of item "Mullvad.app" to {128, 110}
            set position of item "Applications" to {384, 110}
        end tell

        update without registering applications
    end tell
end tell"""

# Helper functions =======================================================


def call(command, **kwargs):
    try:
        subprocess.check_call(command.split(), **kwargs)
    except subprocess.CalledProcessError as e:
        print 'Command "{}" exited with exit code {}'.format(
            command, e.returncode)
        sys.exit(e.returncode)


def create_pot(in_file, out_file, po_dir):
    """Generate a pot file from the given source file and merge with existing
    po files if any exist in the given directory.
    """
    call('xgettext -L Python --no-location {} -o {}'.format(in_file, out_file))

    call('mkdir -p {}'.format(po_dir))
    for po in glob.glob(os.path.join(po_dir, '*')):
        print 'Merging with existing po:', po
        call('msgmerge --update --backup=off {} {}'.format(po, out_file))
    os.remove(out_file)


def create_locale(locale_dir, po_dir, domain):
    """Compile all po files in the given source directory into mo files for the
    given domain and store thme in the given destination directory.
    """
    po_files = glob.glob(os.path.join(po_dir, '*'))

    for po in po_files:
        lang = os.path.splitext(os.path.basename(po))[0]

        dest_dir = os.path.join(locale_dir, lang, 'LC_MESSAGES')
        call('mkdir -p {}'.format(dest_dir))

        output = os.path.join(dest_dir, domain + '.mo')
        print 'Creating', output
        call('msgfmt -o {} {}'.format(output, po))


def list_tree(root, include_root=True, dest_root=''):
    """Generates a list of all files in the given directory tree formatted as
    an argument to the data_files parameter in setup().

    If include_root is True, the entire tree will be included in the
    distribution.  dest_root can be used to store the tree under a given path
    in the distribution.
    """
    result = []
    for dirpath, dirs, files in os.walk(root):
        dest = dirpath
        if not include_root:
            dest = os.path.relpath(dirpath, root)
            if dest == '.':
                dest = ''
        if files:
            files = [os.path.join(dirpath, f) for f in files]
            result.append((os.path.join(dest_root, dest), files))

    return result


def create_dmg(name):
    MASTER_DMG = '{}-{}.dmg'.format(name, CLIENT_VERSION)

    # Create an empty image
    call('mkdir -p template')
    call('hdiutil create -fs HFSX -layout SPUD -size 100m {} -srcfolder '
         'template -format UDRW -volname {}'.format('wc.dmg', name))

    # Create a mount point and mount the image
    call('mkdir -p wc')
    call('hdiutil attach {} -noautoopen -quiet -mountpoint {}'.format(
        'wc.dmg', 'wc'))

    # Copy the app to the image
    call('ditto -rsrc dist/Mullvad.app wc/Mullvad.app')

    # TODO Uncomment this when image scaling problem is solved
    # Create a hidden directory on the image which contains a background image
    # call('mkdir -p wc/.background')
    # call('ditto -rsrc mac/mullvad_background.png '
    #      'wc/.background/mullvad_background.png')

    # Create a shortcut to Applications on the image to make installation
    # easier
    call('ln -s /Applications wc/Applications')

    # Apply a script to the image which defines it's layout
    with open('tempscript', 'w') as f:
        f.write(APPLE_SCRIPT)
    call('osascript tempscript')

    # Unmount the disk image
    call('hdiutil detach wc -quiet -force')

    # Remove old MASTER_DMG if exists
    call('rm -f {}'.format(MASTER_DMG))

    call('hdiutil convert {} -quiet -format UDZO -imagekey '
         'zlib-level=9 -o {}'.format('wc.dmg', MASTER_DMG))

    # Cleanup
    call('rm -rf wc wc.dmg tempscript template')


# ========================================================================

if 'pot' in sys.argv:
    create_pot('src/mullvad/mui.py', 'msgs.pot', 'po')

elif 'locale' in sys.argv:
    create_locale('src/mullvad/locale', 'po', 'mullvad')
    create_locale('locale', 'po', 'mullvad')

elif 'dmg' in sys.argv and platform.system() == 'Darwin':
    create_dmg('Mullvad')

elif platform.system() == 'Windows':  # ==================================
    import py2exe
    import jinja2

    with open('winstaller.nsi.template', 'r') as f:
        nsi_template = jinja2.Template(f.read())
    with open('winstaller.nsi', 'w') as f:
        f.write(nsi_template.render(version=CLIENT_VERSION))

    windows_data_files = [
        ('ssl', glob.glob('src/mullvad/ssl/*')),
        ('', [
            'src/mullvad/client.conf.windows',
            'src/mullvad/backupservers.txt',
            'src/mullvad/harddnsbackup.txt',
            'src/mullvad/openssl.cnf',
            'src/mullvad/mullvad.png',
            'src/mullvad/rdot.png',
            'src/mullvad/ydot.png',
            'src/mullvad/gdot.png',
        ]),
    ]

    windows_data_files.extend(list_tree('locale'))
    windows_data_files.extend(list_tree('client-binaries/windows', False))

    setup(
        name='mullvad',
        windows=[{
            'script': 'client-binaries/windows/mullvad.py',
            'icon_resources': [
                (1, 'client-binaries/windows/mullvad.ico')
            ],
            'other_resources': [
                (24, 1, MANIFEST_TEMPLATE % dict(prog='Mullvad Client'))
            ],
        }],

        options={
            'py2exe': {
                'excludes': ['Tkinter'],
                'dll_excludes': [
                    'w9xpopen.exe',
                    'MSVCP90.dll',
                    # Pulled in by psutil
                    'IPHLPAPI.DLL',
                    'NSI.dll',
                    'WINNSI.DLL',
                    'WTSAPI32.dll',
                    'API-MS-Win-Core-DelayLoad-L1-1-0.dll',
                    'API-MS-Win-Core-ErrorHandling-L1-1-0.dll',
                    'API-MS-Win-Core-File-L1-1-0.dll',
                    'API-MS-Win-Core-Handle-L1-1-0.dll',
                    'API-MS-Win-Core-Heap-L1-1-0.dll',
                    'API-MS-Win-Core-Interlocked-L1-1-0.dll',
                    'API-MS-Win-Core-IO-L1-1-0.dll',
                    'API-MS-Win-Core-LibraryLoader-L1-1-0.dll',
                    'API-MS-Win-Core-LocalRegistry-L1-1-0.dll',
                    'API-MS-Win-Core-Misc-L1-1-0.dll',
                    'API-MS-Win-Core-ProcessThreads-L1-1-0.dll',
                    'API-MS-Win-Core-Profile-L1-1-0.dll',
                    'API-MS-Win-Core-String-L1-1-0.dll',
                    'API-MS-Win-Core-Synch-L1-1-0.dll',
                    'API-MS-Win-Core-SysInfo-L1-1-0.dll',
                    'API-MS-Win-Core-ThreadPool-L1-1-0.dll',
                    'API-MS-Win-Security-Base-L1-1-0.dll',
                ],
            },
        },

        data_files=windows_data_files,

        **common_args
    )

elif platform.system() == 'Darwin':  # ===================================
    osx_data_files = [
        ('', glob.glob('client-binaries/mac/include/*')),

        ('ssl', glob.glob('src/mullvad/ssl/*')),

        ('', [
            'src/mullvad/client.conf.mac',
            'src/mullvad/backupservers.txt',
            'src/mullvad/harddnsbackup.txt',
            'src/mullvad/openssl.cnf',
            'src/mullvad/mullvad.xpm',
            'src/mullvad/mullvad.png',
            'src/mullvad/rdot.png',
            'src/mullvad/ydot.png',
            'src/mullvad/gdot.png',
        ]),
    ]

    osx_data_files.extend(list_tree('locale'))

    setup(
        name='Mullvad',
        setup_requires=['py2app'],

        # Specify the script to be used as entry point for the application
        app=['client-binaries/mac/mullvad_mac.py'],

        options={
            'py2app': {
                'iconfile': 'client-binaries/mac/mullvad.icns',

                # Specify packages to include. Specifying the package in the
                # setup arguments is unfortunately not enough
                'packages': ['src/mullvad'],

                'argv_emulation': True,  # Enable file-drop
                'arch': 'i386',  # wxPython doesn't like 64bit which is default
            },
        },

        data_files=osx_data_files,

        **common_args
    )

    os.system('chmod a+x dist/mullvad.app/Contents/Resources/openvpn')
    os.system('chmod a+x dist/mullvad.app/Contents/Resources/obfsproxy')
    os.system('chmod a+x '
              'dist/mullvad.app/Contents/Resources/process-network-changes')
    os.system('chmod a+x '
              'dist/mullvad.app/Contents/Resources/client.up.osx.sh')
    os.system('chmod a+x '
              'dist/mullvad.app/Contents/Resources/client.down.osx.sh')

else:  # =================================================================
    # data_files only applies when creating a built distribution such as a
    # debian package.
    data_files = [
        ('share/pixmaps', ['src/mullvad/mullvad.png']),
        ('share/applications', ['src/mullvad/mullvad.desktop']),
    ]
    data_files.extend(list_tree('icons', dest_root='share'))

    setup(
        name='mullvad',
        include_package_data=True,
        data_files=data_files,
        entry_points={
            'gui_scripts': [
                'mullvad=mullvad.mui:main',
            ],
            'console_scripts': [
                'mtunnel=mullvad.tunnelprocess:main',
            ],
        },
        **common_args
    )
