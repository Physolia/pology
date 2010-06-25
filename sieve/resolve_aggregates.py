# -*- coding: UTF-8 -*-

"""
Resolve aggregate messages produced by C{msgcat}.

In default mode of operation, C{msgcat} produces an aggregate message when
in different catalogs it encounters a message with the same key,
but different translation or manual or automatic comments.
A general aggregate message looks like this::

    # #-#-#-#-#  po-file-name-1 (project-version-id-1)  #-#-#-#-#
    # manual-comments-1
    # #-#-#-#-#  po-file-name-2 (project-version-id-2)  #-#-#-#-#
    # manual-comments-2
    # ...
    # #-#-#-#-#  po-file-name-n (project-version-id-n)  #-#-#-#-#
    # manual-comments-n
    #. #-#-#-#-#  po-file-name-1 (project-version-id-1)  #-#-#-#-#
    #. automatic-comments-1
    #. #-#-#-#-#  po-file-name-2 (project-version-id-2)  #-#-#-#-#
    #. automatic-comments-2
    #. ...
    #. #-#-#-#-#  po-file-name-n (project-version-id-n)  #-#-#-#-#
    #. automatic-comments-n
    #: source-refs-1 source-refs-2 ... source-refs-n
    #, fuzzy, other-flags
    msgctxt "context"
    msgid "original-text"
    msgstr ""
    "#-#-#-#-#  po-file-name-1 (project-version-id-1)  #-#-#-#-#\\n"
    "translated-text-1\\n"
    "#-#-#-#-#  po-file-name-2 (project-version-id-2)  #-#-#-#-#\\n"
    "translated-text-2\\n"
    "..."
    "#-#-#-#-#  po-file-name-n (project-version-id-n)  #-#-#-#-#\\n"
    "translated-text-n"

Each message part is aggregated only if different in at least one message
in the group. E.g. automatic comments may be aggregated while translations not.

This sieve is used to resolve such aggregate messages into normal messages,
picking one variant from each aggregated part.

Sieve parameters:
  - C{first}: always pick the first variant
  - C{unfuzzy}: unfuzzy resolved messages (I{dangerous}, see below)
  - C{keepsrc}: keep all source references instead of removing them

By default, the variant picked is the one with most occurences,
or the first of the several with same number of occurences.
If C{first} is issued, the first variant is picked unconditionally.

Since there is no information to split the aggregated source references
into original groups, they are entirely removed unless requested otherwise
by issuing the C{keepsrc} parameter.

Aggregated messages are always made fuzzy, leaving no way to determine
if and which of the original messages were fuzzy.
Therefore, by default, the resolved message is left fuzzy too.
If, however, it is known beforehand that none of the original messages
were fuzzy, resolved messages can be unfuzzied by issuing
the C{unfuzzy} parameter.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

# DESIGN NOTE:
# If one of the messages is missing one of the parts that others have,
# that part is silently not added to the aggregation -- there is no explicit
# indicator to tell that it was missing.
# PO file names need not be unique either (if collected from a directory tree),
# so it is not possible to deduce this from file names; likewise for project ID.
# This means that there is no way to reconstruct complete original messages,
# so each part has to be resolved independently.

import re

from pology import _, n_
from pology.file.header import Header
from pology.file.message import Message
from pology.misc.report import report
from pology.sieve import SieveError


def setup_sieve (p):

    p.set_desc(_("@info sieve discription",
    "Resolve aggregate messages produced by '%(cmd)s'.",
    cmd="msgcat"
    ))

    p.add_param("first", bool, defval=False,
                desc=_("@info sieve parameter discription",
    "Always pick the first variant (by default, aggregate messages "
    "are resolved by taking the most frequent variant)."
    ))
    p.add_param("unfuzzy", bool, defval=False,
                desc=_("@info sieve parameter discription",
    "Unfuzzy resolved messages. "
    "DANGEROUS: Use only if all messages in aggregation can be guaranteed "
    "not to be fuzzy."
    ))
    p.add_param("keepsrc", bool, defval=False,
                desc=_("@info sieve parameter discription",
    "Keep source reference on resolved messages instead of removing them."
    ))


class Sieve (object):

    def __init__ (self, params):

        exclusive_picks = [params.first]
        if sum(exclusive_picks) > 2:
            raise SieveError(
                _("@info",
                  "Only one resolution criterion for "
                  "aggregate messages can be given."))

        if params.first:
            self.selvar = _selvar_first
        else:
            self.selvar = _selvar_frequent

        self.unfuzzy = params.unfuzzy
        self.keepsrc = params.keepsrc

        self.nresolved = 0
        self.nresolvedhdr = 0


    def process_header (self, hdr, cat):

        hmsg = Message(hdr.to_msg())
        if _resolve_msg(hmsg, self.selvar):
            self.nresolvedhdr += 1
        cat.header = Header(hmsg)


    def process (self, msg, cat):

        if _resolve_msg(msg, self.selvar):
            self.nresolved += 1
            if self.unfuzzy:
                msg.unfuzzy()
            if not self.keepsrc:
                msg.source[:] = []


    def finalize (self):

        if self.nresolvedhdr > 0:
            msg = n_("@info:progress",
                     "Resolved %(num)d aggregate header.",
                     "Resolved %(num)d aggregate headers.",
                     num=self.nresolvedhdr)
            report("===== %s" % msg)
        if self.nresolved > 0:
            msg = n_("@info:progress",
                     "Resolved %(num)d aggregate message.",
                     "Resolved %(num)d aggregate messages.",
                     num=self.nresolved)
            report("===== %s" % msg)


def _selvar_first (texts):

    return texts[0]


def _selvar_frequent (texts):

    tinds_by_text = {}
    for text, tind in zip(texts, range(len(texts))):
        if text not in tinds_by_text:
            tinds_by_text[text] = []
        tinds_by_text[text].append(tind)
    tinds = sorted(tinds_by_text.values(), key=lambda x: (-len(x), x))

    return texts[tinds[0][0]]


def _resolve_msg (msg, selvar):

    oldcount = msg.modcount

    if msg.manual_comment:
        aggtext = "\n".join(msg.manual_comment)
        msg.manual_comment[:] = _resolve_aggtext(aggtext, selvar).split("\n")

    if msg.auto_comment:
        aggtext = "\n".join(msg.auto_comment)
        msg.auto_comment[:] = _resolve_aggtext(aggtext, selvar).split("\n")

    # Separator swallows trailing newline, put it based on msgid.
    need_trailing_nl = msg.msgid.endswith("\n")
    for i in range(len(msg.msgstr)):
        nmsgstr = _resolve_aggtext(msg.msgstr[i], selvar)
        if need_trailing_nl and nmsgstr != msg.msgstr[i]:
            nmsgstr += "\n"
        msg.msgstr[i] = nmsgstr

    return msg.modcount > oldcount


_splitter_rx = re.compile(r"\n?(?:#-){3,}# .*? (?:#-){3,}#\n?")

def _resolve_aggtext (aggtext, selvar):

    texts = _splitter_rx.split(aggtext)[1:]
    return unicode(selvar(texts)) if texts else aggtext

