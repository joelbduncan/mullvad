#!/usr/bin/env python2

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from mullvad import proc


def get_services():
    """Return a list of the network services."""
    out = proc.run_assert_ok(['networksetup', '-listallnetworkservices'])
    services = out.splitlines()[1:]
    services = [s if not s.startswith('*') else s[1:] for s in services]
    return services
