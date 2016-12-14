#!/usr/bin/env python2

"""A simple netstring library.

A netstring is a self-delimiting encoding of a string.  Netstrings are
very easy to generate and to parse.  Netstrings are especially useful for
building network protocols.  See http://cr.yp.to/proto/netstrings.txt for
details.  Two quick examples:

    "hello world!" is encoded as "12:hello world!,"
    "" is encoded as "0:,"

May 2001
Neil Schemenauer <nas@arctrix.com>
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

__revision__ = '$Id: netstring.py,v 1.3 2001/05/01 21:05:32 nascheme Exp $'

import os

def _read_size(input):
    size = ''
    while 1:
        c = input.read(1)
        if c == ':':
            break
        elif not c:
            raise IOError, 'short netstring read'
        size = size + c
    return long(size)

def write_file(input, output, blocksize=4096):
    """write a file to file object encoded as a netstring"""
    size = os.fstat(input.fileno())[6]
    output.write('%lu:' % size)
    while 1:
        data = input.read(blocksize)
        if not data:
            break
        output.write(data)
    output.write(',')

def write_string(s, output):
    output.write('%lu:' % len(s))
    output.write(s)
    output.write(',')

def read_string(input):
    size = _read_size(input)
    data = ''
    while size > 0:
        s = input.read(size)
        if not s:
            raise IOError, 'short netstring read'
        data = data + s
        size = size - len(s)
    if input.read(1) != ',':
        raise IOError, 'missing netstring terminator'
    return data

def read_file(input, output, blocksize=4096):
    raise NotImplementedError
    # this is untested
    size = _read_size(input)
    while size > 0:
        s = input.read(min(blocksize, size))
        if not s:
            raise IOError, 'short netstring read'
        output.write(s)
        size = size - len(s)
    if input.read(1) != ',':
        raise IOError, 'missing netstring terminator'
