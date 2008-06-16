# -*- coding: UTF-8 -*-

"""
Splitting message fields into syntactical elements.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

import re


_split_rx = re.compile(r"[^\w]+|\w+", re.U)
_split_rx_markup = re.compile(r"[^\w]*(<.*?>|&[\w.:-]+;|&#x?\d+;)[^\w<&]*"
                              r"|[^\w]+|\w+", re.U)
_word_rx = re.compile(r"^\w", re.U)


def split_text (text, markup=False, format=None):
    """
    Split text into words and intersections.

    The text is split into lists of words and intersections (inter-word
    segments), such that there is always an intersection before the first and
    after the last word, even if empty. That is, there is always one more of
    interesections than of words.

    The text may contain C{<...>} tags, and be of certain format supported
    by Gettext (e.g. C{c-format}). If specified, these elements may influence
    splitting.

    @param text: the text to split
    @type text: string

    @param markup: whether text contains markup tags
    @type markup: bool

    @param format: Gettext format flag
    @type format: None or string

    @returns: words and intersections
    @rtype: list of strings, list of strings
    """

    if markup:
        split_rx = _split_rx_markup
        word_rx = _word_rx
    else:
        split_rx = _split_rx
        word_rx = _word_rx

    words = []
    intrs = []
    lastword = False
    for m in split_rx.finditer(text):
        seg = m.group(0)
        if word_rx.search(seg):
            if lastword and words:
                words[-1] += seg
            else:
                words.append(seg)
            lastword = True
        else:
            if not lastword and intrs:
                intrs[-1] += seg
            else:
                intrs.append(seg)
            lastword = False

    if lastword:
        intrs.append(u"")
    if len(intrs) == len(words):
        intrs.insert(0, u"")

    if format == "c-format":
        words, intrs = _mod_on_format_c(words, intrs)
    elif format == "qt-format":
        words, intrs = _mod_on_format_qt(words, intrs)

    return words, intrs


_mf_c_rx = re.compile(r"(?:^|[^%])(% ?)$")

def _mod_on_format_c (words, intrs):

    for i in range(len(words)):
        m = _mf_c_rx.search(intrs[i])
        if m:
            dirst = m.group(1)
            intrs[i] = intrs[i][:-len(dirst)]
            words[i] = dirst + words[i]

    return words, intrs


_mf_qt_rx = re.compile(r"^L?\d")

def _mod_on_format_qt (words, intrs):

    for i in range(len(words)):
        if intrs[i].endswith("%") and _mf_qt_rx.search(words[i]):
            intrs[i] = intrs[i][:-1]
            words[i] = "%" + words[i]

    return words, intrs


# Regexes for text removals to get proper words.
# Second member of the tuple is the replacement string.
_r_url_rx = (re.compile(r"\w+://[^\s]*"
                        r"|www\.[\w.-]+"
                        r"|[\w.-]+\.[a-z]{2,3}\b"
                       , re.I|re.U), "")
_r_email_rx = (re.compile(r"[\w.-]+@[\w.-]+", re.U), "")
_r_shvar_rx = (re.compile(r"\$(\w+|\{.*?\})", re.U), "")
_r_fmtd_rx = (re.compile(r"%($\d+)?[+ ]?(\d+)?\.?(\d+)?[a-z]" # c
                         r"|%\d+" # qt
                         r"|%\(\w+\)[a-z]" # python
                        ), "")
_r_shopt_rx = (re.compile(r"(^|[^\w])(--|-|/)[\w-]+", re.U), "")
_r_tags_rx = (re.compile(r"<.*?>"), " ")
_r_ents_rx = (re.compile(r"&[\w.:-]+;"), " ")
_r_numents_rx = (re.compile(r"&#x?\d+;"), " ")

_remove_xml_rxs = [
    _r_tags_rx, # before entities
    _r_ents_rx,
    _r_numents_rx,
]
_remove_rxs = [
    _r_fmtd_rx, # before others
    _r_email_rx, # before URLs
    _r_url_rx,
    _r_shvar_rx,
    _r_shopt_rx,
]
# Pass words which have only trailing digits and no underscores.
_word_ok_rx = re.compile(r"^[^0-9_]+[0-9]*$", re.U)


def proper_words (text, markup=False, accel=""):
    """
    Mine proper words out of the text.

    The proper words are those one would expect to find in a dictionary,
    or at least having that latent quality (jargon, etc.)
    As opposed to URLs, email addresses, shell variables, etc.

    The text may contain XML-like markup (C{<...>} tags, entities...),
    or keyboard accelerator markers.
    If specified, these elements may influence mining.

    @param text: the text to split
    @type text: string

    @param markup: whether text contains markup tags
    @type markup: bool

    @param accel: accelerator characters to ignore
    @type accel: string

    @returns: proper words
    @rtype: list of strings
    """

    # Remove markup.
    if markup:
        for rem_rx, sub in _remove_xml_rxs:
            text = rem_rx.sub(sub, text)

    # Remove general known non-words.
    for rem_rx, sub in _remove_rxs:
        text = rem_rx.sub(sub, text)

    # Remove accelerators (must come after other replacements.
    for ac in accel:
        text = text.replace(ac, "")

    rwords = split_text(text)[0]
    words = [x for x in rwords if _word_ok_rx.search(x)]

    return words

