# -*- coding: UTF-8 -*-

"""
Make all fuzzy entries untranslated.

For every fuzzy message the translation and fuzzy info (the flag itself,
previous fields) are removed. Manual (translator) comments are left in
by default, but can be removed as well.

Sieve options:
  - C{rmcomments}: also remove manual comments

Obsolete fuzzy messages are completely removed.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""


class Sieve (object):

    def __init__ (self, options):

        self.nemptied = 0

        # Remove manual comments when emptying?
        self.rmcomments = False
        if "rmcomments" in options:
            options.accept("rmcomments")
            self.rmcomments = True


    def process (self, msg, cat):

        if msg.fuzzy:
            if not msg.obsolete:
                msg.unfuzzy()
                msg.msgstr[:] = [u""] * len(msg.msgstr)
                if self.rmcomments:
                    msg.manual_comment[:] = []
                self.nemptied += 1
            else:
                cat.remove_on_sync(msg)


    def finalize (self):

        if self.nemptied > 0:
            print "Total fuzzy messages emptied: %d" % (self.nemptied,)

