Mullvad VPN Client Build Instructions
=====================================

Most of the instructions in this file is for internal use at Mullvad to create installers.
If you have downloaded our source distribution and read this because you want to be able to install that,
skip directly to **Installing from source distribution**.

Running unit tests
-------------------------------------------------------

Run all tests

::

    python -m unittest discover [-v] src/


To run specific test modules you can provide a path as the last argument to the above command

::

    python -m unittest discover [-v] src/ test_config.py
    python -m unittest discover [-v] src/ "test_*"

You can also run indicidual tests or test cases with the following command, if you are located in the package root, i.e. src

::

    python -m unittest tests.test_config
    python -m unittest tests.test_config.TestSettings
    python -m unittest tests.test_config.TestSettings.test_set

Generate new pot/po files
-------------------------------------------------------

::

    python setup.py pot

Compile locale directory from po files
-------------------------------------------------------

::

    sudo aptitude install python-setuptools gettext
    python setup.py locale

Source distribution
-------------------------------------------------------

::

    python setup.py sdist

Debian package
-------------------------------------------------------
Do the locale steps above and then issue these commands:

::

    sudo aptitude install python-stdeb
    python setup.py --command-packages=stdeb.command bdist_deb

OSX App
-------------------------------------------------------
Download and install the following:

* `wxPython 3.0.2`_ *(wxPython3.0-osx-3.0.2.0-cocoa-py2.7.dmg)*

Install the following Python dependencies using pip::

    pip install appdirs ipaddr netifaces psutil

.. _wxPython 3.0.2: http://downloads.sourceforge.net/wxpython/wxPython3.0-osx-3.0.2.0-cocoa-py2.7.dmg

Build Mullvad::

    python setup.py locale
    python setup.py py2app
    python setup.py dmg

Troubleshooting on OSX
''''''''''''''''''''''
If the locale step fails for OSX because of a missing "msgfmt" command it might be that you installed gettext from homebrew, which does not link the binaries to the standard path because of collisions with OSX native gettext library, in that case manually add it to your path in the terminal where you build it:

::

    export PATH=/usr/local/Cellar/gettext/<version>/bin:$PATH

Windows installer
-------------------------------------------------------

**Setup build environment**

Download and install the following:

* `Python 2.7.10`_ *(python-2.7.10.msi)*
* `wxPython 3.0`_ *(wxPython3.0-win32-3.0.2.0-py27.exe)*
* `py2exe 0.6.9`_ *(py2exe-0.6.9.win32-py2.7.exe)*
* `NSIS 2.46`_ *(nsis-2.46-setup.exe)*

NSIS also needs the following plug-ins to support the Windows installer script.
They are installed by placing the relevant files in the NSIS/Plugins directory.

* `FindProcDLL`_ *(FindProcDLL.dll)*

Install the following Python dependencies using pip::

    pip install appdirs ipaddr netifaces psutil jinja2

**Build the installer**

Generate the locale files (must be done on linux)::

    python setup.py locale

Run ``build.bat`` on the Windows build machine

.. _Python 2.7.10: https://www.python.org/ftp/python/2.7.10/python-2.7.10.msi
.. _wxPython 3.0: http://downloads.sourceforge.net/wxpython/wxPython3.0-win32-3.0.2.0-py27.exe
.. _py2exe 0.6.9: http://sourceforge.net/projects/py2exe/files/py2exe/0.6.9/py2exe-0.6.9.win32-py2.7.exe/download
.. _NSIS 2.46: http://prdownloads.sourceforge.net/nsis/nsis-2.46-setup.exe?download
.. _FindProcDLL: http://nsis.sourceforge.net/FindProcDLL_plug-in


Installing from source distribution
-------------------------------------------------------

Build the package from source::

    pip install mullvad-XX.tar.gz  # XX is the version that you have

Install the run-time dependencies::

    sudo aptitude install openvpn resolvconf
    sudo aptitude install python-wxgtk3.0 python-gtk2 python-appindicator

If you want to use obfsproxy you have to build it yourself from https://git.torproject.org/debian/obfsproxy-legacy.git as the newer versions currently don't work with Mullvad.
