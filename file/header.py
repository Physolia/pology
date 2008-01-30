# -*- coding: UTF-8 -*-

"""
Header entry in PO catalogs.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

from pology.misc.wrap import wrap_field
from pology.misc.monitored import Monitored, Monlist, Monpair
from message import Message

import time
import re

_Header_spec = {
    "title" : {"type" : Monlist,
               "spec" : {"*" : {"type" : unicode}}},
    "copyright" : {"type" : unicode},
    "license" : {"type" : unicode},
    "author" : {"type" : Monlist,
                "spec" : {"*" : {"type" : unicode}}},
    "comment" : {"type" : Monlist,
                 "spec" : {"*" : {"type" : unicode}}},
    "field" : {"type" : Monlist,
               "spec" : {"*" : {"type" : Monpair,
                                "spec" : {"first" : {"type" : unicode},
                                          "second" : {"type" : unicode}}}}},
    # Dummies for summary iteration in catalog:
    "obsolete" : {"type" : bool, "derived" : True},
    "key" : {"type" : bool, "derived" : True},
}

class Header (Monitored):
    """
    Header entry in PO catalogs.

    The PO header is syntactically just another entry in the catalog,
    but with different semantics. Therefore, instead operating on it using
    L{Message}, this class provides a different set of interface instance
    variables and methods.

    Like L{Message}, this class implements monitoring; the starred-types
    (e.g. C{list*}) are according to the same convention as for messages,
    and also the strings are assumed unicode unless otherwise noted.

    There is no lightweight alternative to the monitored header, like that of
    L{MessageUnsafe} for messages, because no performance demand is expected
    for the headers only.

    @ivar title: comment lines giving the title
    @type title: list* of strings

    @ivar copyright: comment line with the copyright statement
    @type copyright: string

    @ivar license: comment line with the license statement
    @type license: comment line with the license statement

    @ivar author: comment lines stating translators who worked on this catalog
    @type author: list* of strings

    @ivar comment: the free comment lines, being none of the specific ones
    @type comment: list* of strings

    @ivar field: parsed header fields as key-value string pairs
    @type field: list* of pairs*

    @see: L{Message}
    """

    def __init__ (self, msg=None):
        """
        Initializes the header by the given message.

        Тhe message object is stored and may be modified.

        @param msg: the PO entry containing the header
        @type msg: subclass of L{Message_base}
        """

        if msg: # parse header message
            # Comments.
            self._title = Monlist()
            self._copyright = u""
            self._license = u""
            self._author = Monlist()
            self._comment = Monlist()
            intitle = True
            for c in msg.manual_comment:
                if 0: pass
                elif not self._copyright and re.search("copyright", c, re.I):
                    self._copyright = c
                    intitle = False
                elif not self._license and re.search("license", c, re.I):
                    self._license = c
                    intitle = False
                elif re.search("<.*@.*>", c):
                    self._author.append(c)
                    intitle = False
                elif intitle:
                    self._title.append(c)
                else:
                    self._comment.append(c)

            # Header fields.
            self._field = Monlist()
            for field in msg.msgstr[0].split("\n"):
                m = re.match(r"(.*?): ?(.*)", field)
                if m: self._field.append(Monpair(*m.groups()))

            # Store the message.
            self._message = msg

        else: # create default fields
            self._title = Monlist([u"SOME DESCRIPTIVE TITLE."]);
            self._copyright = u"Copyright (C) YEAR THE PACKAGE'S COPYRIGHT HOLDER"
            self._license = u"This file is distributed under the same license as the PACKAGE package."
            self._author = Monlist([u"FIRST AUTHOR <EMAIL@ADDRESS>, YEAR."])
            self._comment = Monlist([u""])

            self._field = Monlist([
                Monpair(u"Project-Id-Version", u"PACKAGE VERSION"),
                Monpair(u"Report-Msgid-Bugs-To", u""),
                Monpair(u"POT-Creation-Date", unicode(time.strftime("%Y-%m-%d %H:%M%z"))),
                Monpair(u"PO-Revision-Date", u"YEAR-MO-DA HO:MI+ZONE"),
                Monpair(u"Last-Translator", u"FULL NAME <EMAIL@ADDRESS>"),
                Monpair(u"Language-Team", u"LANGUAGE <LL@li.org>"),
                Monpair(u"MIME-Version", u"1.0"),
                Monpair(u"Content-Type", u"text/plain; charset=CHARSET"),
                Monpair(u"Content-Transfer-Encoding", u"8bit"),
                Monpair(u"Plural-Forms", u"nplurals=INTEGER; plural=EXPRESSION;"),
            ])

            # Create the message.
            self._message = Message()
            self._remake_msg(force=True)

        self.assert_spec_init(_Header_spec)

        # Unmodify all monitored members.
        self.modcount = 0


    def __getattr__ (self, att):
        """
        Attribute getter.

        Processes read-only variables, and sends others to the base class.

        @param att: name of the attribute to get
        @returns: attribute value
        """

        if att == "obsolete":
            return False
        elif att == "key":
            return Message().key # key of an empty-msgid message
        else:
            return Monitored.__getattr__(self, att)


    def _remake_msg (self, force=False):

        m = self._message

        if force \
        or self.title_modcount or self.title.modcount \
        or self.copyright_modcount \
        or self.license_modcount \
        or self.author_modcount or self.author.modcount \
        or self.comment_modcount or self.comment.modcount:
            m.manual_comment = Monlist()
            for t in self.title:
                m.manual_comment.append(t)
            if self.copyright:
                m.manual_comment.append(self.copyright)
            if self.license:
                m.manual_comment.append(self.license)
            for a in self.author:
                m.manual_comment.append(a)
            for c in self.comment:
                m.manual_comment.append(c)

        if force or self.field_modcount or self.field.modcount:
            m.msgstr = Monlist([u""])
            for field in self.field:
                m.msgstr[0] += "%s: %s\\n" % tuple(field)


    def to_msg (self, force=False):
        """
        Convert the header into ordinary message object.

        The message object returned may be the modification of the one
        passed to the constructor. In that case, and if the message object
        has monitoring features, the force parameter will tell whether to
        modify all message elements, or to try to keep the changes minimal.

        @param force: whether to recreate all message elements
        @type force: bool

        @returns: header as message
        @rtype: the type that initialized the object
        """

        self._remake_msg(force)
        return self._message


    def to_lines (self, wrapf=wrap_field, force=False):
        """
        The line-representation of the header.

        Equivalent to the same-named method of message classes.

        @see: L{Message_base}
        """

        return self.to_msg(force).to_lines(wrapf, force)


    def to_string (self, wrapf=wrap_field, force=False):
        """
        The string-representation of the header.

        Equivalent to the same-named method of message classes.

        @see: L{Message_base}
        """

        return self.to_msg(force).to_string(wrapf, force)


    def select_fields (self, name):
        """
        Find header fields with the given name.

        Header fields need not be unique.

        @param name: look for the fields with this name
        @type name: string

        @returns: references to name-value pairs matching the field name
        @rtype: list of pairs*
        """

        fields = []
        for pair in self.field:
            if pair.first == name:
                fields.append(pair)
        return fields


    def replace_field_value (self, name, new_value, nth=0):
        """
        Replace the value of the n-th occurence of the named header field.

        Header fields need not be unique, hence the n-th qualification.

        @param name: name of the header field
        @type name: string

        @param new_value: new value for the field
        @type new_value: string

        @param nth: replace the value of this field among same-named fields
        @type nth: int

        @returns: True if the requested field was found, False otherwise
        @rtype: bool
        """

        nfound = 0
        for i in range(len(self._field)):
            if self._field[i][0] == name:
                nfound += 1
                if nfound - 1 == nth:
                    self._field[i] = Monpair(unicode(name), new_value)
                    break

        return nfound - 1 == nth

