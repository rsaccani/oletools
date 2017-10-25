#!/usr/bin/env python
"""
msodde.py

msodde is a script to parse MS Office documents
(e.g. Word, Excel), to detect and extract DDE links.

Supported formats:
- Word 97-2003 (.doc, .dot), Word 2007+ (.docx, .dotx, .docm, .dotm)

Author: Philippe Lagadec - http://www.decalage.info
License: BSD, see source code or documentation

msodde is part of the python-oletools package:
http://www.decalage.info/python/oletools
"""

# === LICENSE ==================================================================

# msodde is copyright (c) 2017 Philippe Lagadec (http://www.decalage.info)
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from __future__ import print_function

#------------------------------------------------------------------------------
# CHANGELOG:
# 2017-10-18 v0.52 PL: - first version
# 2017-10-20       PL: - fixed issue #202 (handling empty xml tags)
# 2017-10-25       CH: - add json output
# 2017-10-25       CH: - parse doc

__version__ = '0.52dev3'

#------------------------------------------------------------------------------
# TODO: detect beginning/end of fields, to separate each field
# TODO: test if DDE links can also appear in headers, footers and other places
# TODO: add xlsx support

#------------------------------------------------------------------------------
# REFERENCES:


#--- IMPORTS ------------------------------------------------------------------

# import lxml or ElementTree for XML parsing:
try:
    # lxml: best performance for XML processing
    import lxml.etree as ET
except ImportError:
    import xml.etree.cElementTree as ET

import argparse
import zipfile
import os
import sys
import json

from oletools.thirdparty import olefile

# === PYTHON 2+3 SUPPORT ======================================================

if sys.version_info[0] >= 3:
    unichr = chr

# === CONSTANTS ==============================================================


NS_WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

# XML tag for 'w:instrText'
TAG_W_INSTRTEXT = '{%s}instrText' % NS_WORD
TAG_W_FLDSIMPLE = '{%s}fldSimple' % NS_WORD
TAG_W_INSTRATTR= '{%s}instr' % NS_WORD

# banner to be printed at program start
BANNER = """msodde %s - http://decalage.info/python/oletools
THIS IS WORK IN PROGRESS - Check updates regularly!
Please report any issue at https://github.com/decalage2/oletools/issues
""" % __version__

BANNER_JSON = dict(type='meta', version=__version__, name='msodde',
                   link='http://decalage.info/python/oletools',
                   message='THIS IS WORK IN PROGRESS - Check updates regularly! '
                            'Please report any issue at '
                            'https://github.com/decalage2/oletools/issues')

# === ARGUMENT PARSING =======================================================

class ArgParserWithBanner(argparse.ArgumentParser):
    """ Print banner before showing any error """
    def error(self, message):
        print(BANNER)
        super(ArgParserWithBanner, self).error(message)


def existing_file(filename):
    """ called by argument parser to see whether given file exists """
    if not os.path.exists(filename):
        raise argparse.ArgumentTypeError('File {0} does not exist.'
                                         .format(filename))
    return filename


def process_args(cmd_line_args=None):
    parser = ArgParserWithBanner(description='A python tool to detect and extract DDE links in MS Office files')
    parser.add_argument("filepath", help="path of the file to be analyzed",
                        type=existing_file, metavar='FILE')
    parser.add_argument("--json", '-j', action='store_true',
                        help="Output in json format")

    return parser.parse_args(cmd_line_args)


# === FUNCTIONS ==============================================================

# from [MS-DOC], section 2.8.25 (PlcFld):
# A field consists of two parts: field instructions and, optionally, a result. All fields MUST begin with
# Unicode character 0x0013 with sprmCFSpec applied with a value of 1. This is the field begin
# character. All fields MUST end with a Unicode character 0x0015 with sprmCFSpec applied with a value
# of 1. This is the field end character. If the field has a result, then there MUST be a Unicode character
# 0x0014 with sprmCFSpec applied with a value of 1 somewhere between the field begin character and
# the field end character. This is the field separator. The field result is the content between the field
# separator and the field end character. The field instructions are the content between the field begin
# character and the field separator, if one is present, or between the field begin character and the field
# end character if no separator is present. The field begin character, field end character, and field
# separator are collectively referred to as field characters.


def process_ole_field(data):
    """ check if field instructions start with DDE

    expects unicode input, returns unicode output (empty if not dde) """
    #print('processing field \'{0}\''.format(data))

    if data.lstrip().lower().startswith(u'dde'):
        #print('--> is DDE!')
        return data
    else:
        return u''


OLE_FIELD_START = 0x13
OLE_FIELD_SEP = 0x14
OLE_FIELD_END = 0x15
OLE_FIELD_MAX_SIZE = 1000   # max field size to analyze, rest is ignored


def process_ole_stream(stream):
    """ find dde links in single ole stream

    since ole file stream are subclasses of io.BytesIO, they are buffered, so
    reading char-wise is not that bad performanc-wise """

    have_start = False
    have_sep = False
    field_contents = None
    result_parts = []
    max_size_exceeded = False
    idx = -1
    while True:
        idx += 1
        char = stream.read(1)    # loop over every single byte
        if len(char) == 0:
            break
        else:
            char = ord(char)

        if char == OLE_FIELD_START:
            #print('DEBUG: have start at {}'.format(idx))
            #if have_start:
            #    print("DEBUG: dismissing previous contents of length {}"
            #          .format(len(field_contents)))
            have_start = True
            have_sep = False
            max_size_exceeded = False
            field_contents = u''
            continue
        elif not have_start:
            continue

        # now we are after start char but not at end yet
        if char == OLE_FIELD_SEP:
            #print('DEBUG: have sep at {}'.format(idx))
            have_sep = True
        elif char == OLE_FIELD_END:
            #print('DEBUG: have end at {}'.format(idx))

            # have complete field now, process it
            result_parts.append(process_ole_field(field_contents))

            # re-set variables for next field
            have_start = False
            have_sep = False
            field_contents = None
        elif not have_sep:
            # check that array does not get too long by accident
            if max_size_exceeded:
                pass
            elif len(field_contents) > OLE_FIELD_MAX_SIZE:
                #print('DEBUG: exceeded max size')
                max_size_exceeded = True

            # appending a raw byte to a unicode string here. Not clean but
            # all we do later is check for the ascii-sequence 'DDE' later...
            elif char < 128:
                field_contents += unichr(char)
                #print('DEBUG: at idx {:4d}: add byte {} ({})'
                #      .format(idx, unichr(char), char))
            else:
                field_contents += u'?'
                #print('DEBUG: at idx {:4d}: add byte ? ({})'
                #      .format(idx, char))
    #print('\nstream len = {}'.format(idx))

    # copy behaviour of process_xml: Just concatenate unicode strings
    return u''.join(result_parts)


def process_ole_storage(ole):
    """ process a "directory" inside an ole stream """
    results = []
    for st in ole.listdir(streams=True, storages=True):
        st_type = ole.get_type(st)
        if st_type == olefile.STGTY_STREAM:      # a stream
            stream = None
            links = ''
            try:
                stream = ole.openstream(st)
                #print('Checking stream {0}'.format(st))
                links = process_ole_stream(stream)
            except:
                raise
            finally:
                if stream:
                    stream.close()
            if links:
                results.append(links)
        elif st_type == olefile.STGTY_STORAGE:   # a storage
            #print('Checking storage {0}'.format(st))
            links = process_ole_storage(st)
            if links:
                results.extend(links)
        else:
            #print('Warning: unexpected type {0} for entry {1}. Ignore it'
            #      .format(st_type, st))
            continue
    return results


def process_ole(filepath):
    """ find dde links in ole file

    like process_xml, returns a concatenated unicode string of dde links or
    empty if none were found. dde-links will still being with the dde[auto] key
    word (possibly after some whitespace)
    """
    #print('Looks like ole')
    ole = olefile.OleFileIO(filepath, path_encoding=None)
    text_parts = process_ole_storage(ole)
    return u'\n'.join(text_parts)


def process_xml(filepath):
    z = zipfile.ZipFile(filepath)
    data = z.read('word/document.xml')
    z.close()
    # parse the XML data:
    root = ET.fromstring(data)
    text = u''
    # find all the tags 'w:instrText':
    # (each is a chunk of a DDE link)
    for elem in root.iter(TAG_W_INSTRTEXT):
        # concatenate the text of the field, if present:
        if elem.text is not None:
            text += elem.text

    for elem in root.iter(TAG_W_FLDSIMPLE):
        # concatenate the attribute of the field, if present:
        if elem.attrib is not None:
            text += elem.attrib[TAG_W_INSTRATTR]

    return text


def process_file(filepath):
    """ decides to either call process_xml or process_ole """
    if olefile.isOleFile(filepath):
        return process_ole(filepath)
    else:
        return process_xml(filepath)


#=== MAIN =================================================================

def main(cmd_line_args=None):
    """ Main function, called if this file is called as a script

    Optional argument: command line arguments to be forwarded to ArgumentParser
    in process_args. Per default (cmd_line_args=None), sys.argv is used. Option
    mainly added for unit-testing
    """
    args = process_args(cmd_line_args)

    if args.json:
        jout = []
        jout.append(BANNER_JSON)
    else:
        # print banner with version
        print(BANNER)

    if not args.json:
        print('Opening file: %s' % args.filepath)

    text = ''
    return_code = 1
    try:
        text = process_file(args.filepath)
        return_code = 0
    except Exception as exc:
        if args.json:
            jout.append(dict(type='error', error=type(exc).__name__,
                             message=str(exc)))  # strange: str(exc) is enclosed in ""
        else:
            raise

    if args.json:
        for line in text.splitlines():
            jout.append(dict(type='dde-link', link=line.strip()))
        json.dump(jout, sys.stdout, check_circular=False, indent=4)
        print()   # add a newline after closing "]"
        return return_code  # required if we catch an exception in json-mode
    else:
        print ('DDE Links:')
        print(text)

    return return_code


if __name__ == '__main__':
    sys.exit(main())
