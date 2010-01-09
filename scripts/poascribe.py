#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import fallback_import_paths

import codecs
from ConfigParser import SafeConfigParser
import datetime
import imp
import locale
from optparse import OptionParser
import os
import re
import sys
import time

from pology.file.catalog import Catalog
from pology.file.message import Message, MessageUnsafe
from pology.misc.colors import colors_for_file
from pology.misc.comments import parse_summit_branches
import pology.misc.config as pology_config
from pology.misc.diff import msg_diff, msg_ediff, msg_ediff_to_new
from pology.misc.diff import editprob
from pology.misc.fsops import str_to_unicode, unicode_to_str
from pology.misc.fsops import collect_catalogs, mkdirpath, join_ncwd
from pology.misc.langdep import get_hook_lreq
from pology.misc.monitored import Monlist, Monset
from pology.misc.msgreport import warning_on_msg, report_msg_content
from pology.misc.msgreport import report_msg_to_lokalize
from pology.misc.report import report, warning, error
from pology.misc.report import init_file_progress
from pology.misc.tabulate import tabulate
from pology.misc.vcs import make_vcs
from pology.sieve.find_messages import build_msg_fmatcher
from pology.sieve.update_header import update_header


ASCWRAPPING = ["fine"]
UFUZZ = "fuzzy"

# Flag used to mark diffed messages.
# NOTE: All diff flags should start with 'ediff', as some other scripts
# only need to check if any of them is present.
_diffflag = u"ediff"
_diffflag_tot = u"ediff-total"
_diffflag_ign = u"ediff-ignored"

# Flags used to explicitly mark messages as reviewed or unreviewed.
_revdflags = (u"reviewed", u"revd", u"rev") # synonyms
_urevdflags = (u"unreviewed", u"unrevd", u"unrev",
               u"urevd", u"urev", u"nrevd", u"nrev") # synonyms

# Comment used to show ascription chain in messages marked for review.
_achncmnt = "~ascto:"

_diffflags = (_diffflag, _diffflag_tot, _diffflag_ign)
_all_flags = _diffflags + _revdflags + _urevdflags
_all_cmnts = (_achncmnt,)


def main ():

    locale.setlocale(locale.LC_ALL, "")

    mode_spec = (
        ("status", "st"),
        ("modified", "mo"),
        ("reviewed", "re"),
        ("clear", "cl"),
        ("diff", "di"),
        ("history", "hi"),
    )
    mode_allnames = set(sum(mode_spec, ()))
    mode_tolong = dict(map(reversed, mode_spec))
    mode_tolong.update(dict([(x, x) for x, y in mode_spec]))

    known_editors = {
        "lokalize": report_msg_to_lokalize,
    }

    # Setup options and parse the command line.
    usage = (
        u"%prog [OPTIONS] [MODE] [PATHS...]")
    description = (
        u"Keep track of who, when, and how, has translated, modified, "
        u"or reviewed messages in a collection of PO files.")
    version = (
        u"%prog (Pology) experimental\n"
        u"Copyright © 2008 Chusslove Illich (Часлав Илић) "
        u"<caslav.ilic@gmx.net>\n")

    opars = OptionParser(usage=usage, description=description, version=version)
    opars.add_option(
        "-a", "--select-ascription", metavar="SELECTOR[:ARGS]",
        action="append", dest="aselectors", default=None,
        help="select message from ascription history by this selector "
             "(relevant in some modes). "
             "Can be repeated, AND-semantics.")
    opars.add_option(
        "-A", "--min-adjsim-diff",  metavar="RATIO",
        action="store", dest="min_adjsim_diff", default=None,
        help="Minimum adjusted similarity between two versions of a message "
             "needed to actually show the embedded difference. "
             "Range is 0.0-1.0, where 0 means to always show the difference, "
             "and 1 to never show it; a resonable threshold is 0.6-0.8. "
             "When the difference is not shown, the '%s' flag is still "
             "added to the message." % _diffflag_ign)
    opars.add_option(
        "-b", "--show-by-file",
        action="store_true", dest="show_by_file", default=False,
        help="next to global summary, also present results by file "
             "(relevant in some modes).")
    opars.add_option(
        "-C", "--no-commit",
        action="store_false", dest="commit", default=None,
        help="do not commit original and ascription catalogs "
             "when version control is used")
    opars.add_option(
        "-d", "--depth", metavar="LEVEL",
        action="store", dest="depth", default=None,
        help="consider ascription history up to this level into the past "
             "(relevant in some modes)")
    opars.add_option(
        "-D", "--diff-reduce-history", metavar="SPEC",
        action="store", dest="diff_reduce_history", default=None,
        help="Reduce each message in history to a part of the difference "
             "from the first earlier modification: to added, removed, or "
             "equal segments. "
             "The value begins with one of the characters 'a', 'r', or 'e', "
             "followed by substring that will be used to separate "
             "selected difference segments in resulting messages "
             "(if this substring is empty, space is used).")
    opars.add_option(
        "-E", "--po-editor", metavar="%s" % "|".join(sorted(known_editors)),
        action="store", dest="po_editor", default=None,
        help="Open selected messages in one of the supported PO editors.")
    opars.add_option(
        "-f", "--files-from", metavar="FILE",
        action="append", dest="files_from", default=[],
        help="get list of input files from FILE, which contains one file path "
             "per line; can be repeated to collect paths from several files")
    opars.add_option(
        "-F", "--filter", metavar="NAME",
        action="append", dest="filters", default=None,
        help="Pass relevant message text fields through a filter before "
             "matching or comparing them (relevant in some modes). "
             "Can be repeated to add several filters.")
    opars.add_option(
        "-G", "--show-filtered",
        action="store_true", dest="show_filtered", default=False,
        help="when operating under a filter, also show filtered versions "
             "of whatever is shown in original (e.g. in diffs)")
    opars.add_option(
        "-k", "--keep-flags",
        action="store_true", dest="keep_flags", default=False,
        help="do not remove ascription significant flags from messages "
             "(relevant in some modes)")
    opars.add_option(
        "-m", "--message", metavar="TEXT",
        action="store", dest="message", default=None,
        help="commit message for original catalogs, when %(option)s "
             "is in effect" % dict(option="-c"))
    opars.add_option(
        "-s", "--selector", metavar="SELECTOR[:ARGS]",
        action="append", dest="selectors", default=None,
        help="consider only messages matched by this selector. "
             "Can be repeated, AND-semantics.")
    opars.add_option(
        "-t", "--tag", metavar="TAG",
        action="store", dest="tag", default=None,
        help="tag to add or consider in ascription records "
             "(relevant in some modes)")
    opars.add_option(
        "-u", "--user", metavar="USER",
        action="store", dest="user", default=None,
        help="user in the focus of the operation "
             "(relevant in some modes)")
    opars.add_option(
        "-U", "--update-headers",
        action="store_true", dest="update_headers", default=None,
        help="Update headers in catalogs which contain modifications "
             "about to be ascribed (before committing the catalogs), "
             "with user's translator information.")
    opars.add_option(
        "-v", "--verbose",
        action="store_true", dest="verbose", default=False,
        help="output more detailed progress info")
    opars.add_option(
        "-w", "--write-modified", metavar="FILE",
        action="store", dest="write_modified", default=None,
        help="write paths of all original catalogs modified by "
             "ascription operations into the given file")
    opars.add_option(
        "-x", "--externals", metavar="PYFILE",
        action="append", dest="externals", default=[],
        help="collect optional functionality from an external Python file "
             "(selectors, etc.)")
    opars.add_option(
        "--no-psyco",
        action="store_false", dest="use_psyco", default=True,
        help="do not try to use Psyco specializing compiler")

    (options, free_args) = opars.parse_args(str_to_unicode(sys.argv[1:]))

    # Could use some speedup.
    if options.use_psyco:
        try:
            import psyco
            psyco.full()
        except ImportError:
            pass

    # Parse operation mode and its arguments.
    if len(free_args) < 1:
        error("Operation mode not given.")
    rawmodename = free_args.pop(0)
    modename = mode_tolong.get(rawmodename)
    if modename is None:
        error("Unknown operation mode '%(mode)s' (known modes: %(modes)s)."
              % dict(mode=rawmodename,
                     modes=", ".join(["%s/%s" % x for x in mode_spec])))

    # For options not issued, read values from user configuration.
    # Configuration values can also be issued by mode using
    # C{afield/amode = value} syntax, which takes precedence over
    # general fields (e.g. C{filters/review} vs. C{filters}).
    cfgsec = pology_config.section("poascribe")
    for optname, getvalf, defval in (
        ("aselectors", cfgsec.strdlist, []),
        ("commit", cfgsec.boolean, True),
        ("po-editor", cfgsec.string, None),
        ("filters", cfgsec.strslist, []),
        ("min-adjsim-diff", cfgsec.real, 0.0),
        ("selectors", cfgsec.strdlist, []),
        ("tag", cfgsec.string, ""),
        ("user", cfgsec.string, None),
        ("update-headers", cfgsec.boolean, False),
        ("diff-reduce-history", cfgsec.string, None),
    ):
        uoptname = optname.replace("-", "_")
        if getattr(options, uoptname) is None:
            for fldname in ("%s/%s" % (optname, modename), optname):
                fldval = getvalf(fldname, None)
                if fldval is not None:
                    break
            if fldval is None:
                fldval = defval
            setattr(options, uoptname, fldval)

    # Convert options to non-string types.
    def valconv_editor (edkey):
        msgrepf = known_editors.get(edkey)
        if msgrepf is None:
            error("PO editor '%(ed)s' is not among "
                  "the supported editors: %(eds)s."
                  % dict(ed=edkey, eds=", ".join(sorted(known_editors))))
        return msgrepf
    for optname, valconv in (
        ("min-adjsim-diff", float),
        ("po-editor", valconv_editor),
    ):
        uoptname = optname.replace("-", "_")
        valraw = getattr(options, uoptname, None)
        if valraw is not None:
            try:
                value = valconv(valraw)
            except TypeError:
                error("Value '%(val)s' to option '%(opt)s' is of wrong type."
                      % dict(val=valraw, opt=("--" + optname)))
            setattr(options, uoptname, value)

    # Collect any external functionality.
    for xmod_path in options.externals:
        collect_externals(xmod_path)

    # Create history filter if requested, store it in options.
    options.hfilter = None
    options.sfilter = None
    if options.filters:
        hfilters = []
        for hspec in options.filters:
            hfilters.append(get_hook_lreq(hspec, abort=True))
        def hfilter_composition (text):
            for hfilter in hfilters:
                text = hfilter(text)
            return text
        options.hfilter = hfilter_composition
        if options.show_filtered:
            options.sfilter = options.hfilter

    # Create specification for reducing historical messages to diffs.
    options.addrem = None
    if options.diff_reduce_history:
        options.addrem = options.diff_reduce_history
        if options.addrem[:1] not in ("a", "e", "r"):
            error("Value '%(val)s' to option '%(opt)s' must start "
                  "with '%(char1)s', '%(char2)s', or '%(char3)s'."
                  % dict(val=options.addrem, opt="--diff-reduce-history",
                         char1="a", char2="e", char3="r"))

    # Create selectors if any explicitly given.
    selector = None
    if options.selectors:
        selector = build_selector(options.selectors)
    aselector = None
    if options.aselectors:
        aselector = build_selector(options.aselectors, hist=True)

    # Assemble operation mode.
    needuser = False
    canselect = False
    canaselect = False
    class _Mode: pass
    mode = _Mode()
    mode.name = modename
    if 0: pass
    elif mode.name == "status":
        mode.execute = examine_state
        mode.selector = selector or build_selector(["any"])
        canselect = True
    elif mode.name == "modified":
        mode.execute = ascribe_modified
        mode.selector = selector or build_selector(["any"])
        needuser = True
    elif mode.name == "reviewed":
        mode.execute = ascribe_reviewed
        mode.selector = selector or build_selector(["any"])
        canselect = True
        needuser = True
    elif mode.name == "diff":
        mode.execute = diff_select
        mode.selector = selector or build_selector(["modar"])
        mode.aselector = aselector
        canselect = True
        canaselect = True
    elif mode.name == "clear":
        mode.execute = clear_review
        mode.selector = selector or build_selector(["any"])
        canselect = True
    elif mode.name == "history":
        mode.execute = show_history
        mode.selector = selector or build_selector(["any"])
        canselect = True
    else:
        error("Internal problem: unhandled operation mode '%s'." % mode.name)

    mode.user = None
    if needuser:
        if not options.user:
            error("operation mode requires a user to be specified")
        mode.user = options.user
    if not canselect and selector:
        error("operation mode does not accept selectors")
    if not canaselect and aselector:
        error("operation mode does not accept history selectors")

    # Collect list of raw paths supplied through command line.
    # If none supplied, assume current working directory.
    rawpaths = None
    if free_args:
        rawpaths = free_args
    if options.files_from:
        if rawpaths is None:
            rawpaths = []
        for fpath in options.files_from:
            lines = open(fpath).read().split("\n")
            rawpaths.extend(filter(lambda x: x, lines))
    if rawpaths is None:
        rawpaths = ["."]

    # Collect the config which covers each path, and all catalogs inside it.
    configs_catpaths = collect_configs_catpaths(rawpaths)

    # Execute operation.
    mode.execute(options, configs_catpaths, mode)

    # Write out list of modified original catalogs if requested.
    if options.write_modified and _modified_cats:
        lfpath = options.write_modified
        f = open(lfpath, "w")
        f.write(("\n".join(sorted(_modified_cats)) + "\n").encode("utf-8"))
        f.close()
        report("Written modified catalog paths to: %s" % lfpath)


# For each path:
# - determine its associated ascription config
# - collect all catalogs
# FIXME: Imported by others, factor out.
def collect_configs_catpaths (paths):

    paths = map(join_ncwd, paths)
    configs_loaded = {}
    configs_catpaths = []
    for path in paths:
        # Look for the first config file up the directory tree.
        parent = os.path.abspath(path)
        if os.path.isfile(parent):
            parent = os.path.dirname(parent)
        cfgpath = None
        while True:
            for cfgname in ("ascribe", "ascription-config"):
                test_cfgpath = os.path.join(parent, cfgname)
                if os.path.isfile(test_cfgpath):
                    cfgpath = test_cfgpath
                    break
            if cfgpath:
                break
            pparent = parent
            parent = os.path.dirname(parent)
            if parent == pparent:
                break
        if not cfgpath:
            error("cannot find ascription configuration for path: %s" % path)
        cfgpath = join_ncwd(cfgpath) # for nicer message output
        config = configs_loaded.get(cfgpath, None)
        if not config:
            # New config, load.
            config = Config(cfgpath)
            configs_loaded[cfgpath] = config

        # Collect PO files.
        if os.path.isdir(path):
            catpaths_raw = collect_catalogs(path)
        else:
            catpaths_raw = [path]
        # Determine paths of ascription catalogs.
        # Pack as (catpath, acatpath) tuples.
        catpaths = []
        absrootpath = os.path.abspath(config.catroot)
        lenarpath = len(absrootpath)
        lenarpathws = lenarpath + len(os.path.sep)
        for catpath_raw in catpaths_raw:
            abscatpath = os.path.abspath(catpath_raw)
            p = abscatpath.find(absrootpath)
            if p != 0 or abscatpath[lenarpath:lenarpathws] != os.path.sep:
                error("catalog not in the root given by configuration: %s"
                      % catpath_raw)
            acatpath = join_ncwd(config.ascroot, abscatpath[lenarpathws:])
            catpaths.append((join_ncwd(catpath_raw), acatpath))

        # Collect the config and corresponding catalogs.
        configs_catpaths.append((config, catpaths))

    return configs_catpaths


def commit_catalogs (configs_catpaths, user, message=None, onabortf=None):

    # Attach paths to each distinct config, to commit them all at once.
    configs = []
    catpaths_byconf = {}
    for config, catpaths in configs_catpaths:
        if config not in catpaths_byconf:
            catpaths_byconf[config] = []
            configs.append(config)
        for catpaths2 in catpaths:
            for catpath in catpaths2:
                if config.vcs.is_versioned(catpath):
                    catpaths_byconf[config].append(catpath)

    # Commit by config.
    for config in configs:
        cmsg = message
        cmsgfile = None
        if not cmsg:
            cmsg = config.commit_message
        if not cmsg:
            cmsgfile, cmsgfile_orig = get_commit_message_file_path(user)
        else:
            cmsg += " " + fmt_commit_user(user)
        if not config.vcs.commit(catpaths_byconf[config],
                                 message=cmsg, msgfile=cmsgfile):
            if onabortf:
                onabortf()
            if not cmsgfile:
                error("VCS reports that catalogs cannot be committed.")
            else:
                os.unlink(cmsgfile)
                error("VCS reports that catalogs cannot be committed "
                      "(commit message preserved in '%s')."
                      % cmsgfile_orig)
        if cmsgfile:
            os.unlink(cmsgfile)
            os.unlink(cmsgfile_orig)


def fmt_commit_user (user):

    return "[>%s]" % user


def get_commit_message_file_path (user):

    while True:
        tfmt = time.strftime("%Y-%m-%d-%H-%M-%S")
        prefix = "poascribe-commit-message"
        ext = "txt"
        fpath = "%s-%s.%s" % (prefix, tfmt, ext)
        fpath_asc = "%s-%s-asc.%s" % (prefix, tfmt, ext)
        if not os.path.isfile(fpath) and not os.path.isfile(fpath_asc):
            break

    edcmd = None
    if not edcmd:
        edcmd = os.getenv("ASC_EDITOR")
    if not edcmd:
        edcmd = pology_config.section("poascribe").string("editor")
    if not edcmd:
        edcmd = os.getenv("EDITOR")
    if not edcmd:
        edcmd = "/usr/bin/vi"

    cmd = "%s %s" % (edcmd, fpath)
    if os.system(cmd):
        error("error from editor command for commit message: %s" % cmd)
    if not os.path.isfile(fpath):
        error("editor command did not produce a file: %s" % cmd)

    cmsg = open(fpath, "r").read()
    if not cmsg.endswith("\n"):
        cmsg += "\n"
    fmt_user = unicode_to_str(fmt_commit_user(user))
    if cmsg.count("\n") == 1:
        cmsg = cmsg[:-1] + " " + fmt_user + "\n"
    else:
        cmsg += fmt_user + "\n"
    fh = open(fpath_asc, "w")
    fh.write(cmsg)
    fh.close()

    return fpath_asc, fpath


class Config:

    def __init__ (self, cpath):

        config = SafeConfigParser()
        ifl = codecs.open(cpath, "r", "UTF-8")
        config.readfp(ifl)
        ifl.close()

        self.path = cpath

        gsect = dict(config.items("global"))
        cpathdir = os.path.dirname(cpath)
        self.catroot = join_ncwd(cpathdir, gsect.get("catalog-root", ""))
        self.ascroot = join_ncwd(cpathdir, gsect.get("ascript-root", ""))
        if self.catroot == self.ascroot:
            error("%s: catalog root and ascription root "
                  "resolve to same path: %s" % (cpath, self.catroot))

        self.title = gsect.get("title", None)
        self.lang_team = gsect.get("language-team", None)
        self.team_email = gsect.get("team-email", None)
        self.lang_code = gsect.get("language", None)
        self.plural_header = gsect.get("plural-header", None)

        self.vcs = make_vcs(gsect.get("version-control", "noop"))

        self.commit_message = gsect.get("commit-message", None)

        cval = gsect.get("review-tags", None)
        if cval is not None:
            self.review_tags = set(cval.split())
        else:
            self.review_tags = set()
        self.review_tags.add("")

        class UserData: pass
        self.udata = {}
        self.users = []
        userst = "user-"
        for section in config.sections():
            if section.startswith(userst):
                user = section[len(userst):]
                usect = dict(config.items(section))
                if user in self.users:
                    error("%s: repeated user: %s" % (cpath, user))
                udat = UserData()
                self.udata[user] = udat
                self.users.append(user)
                if "name" not in usect:
                    error("%s: user '%s' misses the name" % (cpath, user))
                udat.name = usect.get("name")
                if udat.name == UFUZZ:
                    error("%s: user name '%s' is reserved" % (cpath, UFUZZ))
                udat.oname = usect.get("original-name")
                udat.email = usect.get("email")
        self.users.sort()

        # Create merging user.
        udat = UserData()
        self.udata[UFUZZ] = udat
        self.users.append(UFUZZ)
        udat.name = "UFUZZ"
        udat.oname = None
        udat.email = None


def assert_mode_user (configs_catpaths, mode, nousers=[]):

    if mode.user in nousers:
        error("User '%s' not allowed in mode '%s'." % (mode.user, mode.name))
    for config, catpaths in configs_catpaths:
        if mode.user not in config.users:
            error("User '%s' not defined in '%s'." % (mode.user, config.path))


def assert_review_tag (configs_catpaths, tag):

    for config, catpaths in configs_catpaths:
        if tag not in config.review_tags:
            error("Review tag '%s' not defined in '%s'." % (tag, config.path))


def assert_no_review (configs_catpaths):

    wrevs = []
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            if not may_have_reviews(catpath):
                continue
            cat = Catalog(catpath, monitored=False)
            if clear_review_cat_simple(cat):
                wrevs.append(catpath)
    if wrevs:
        error("Review elements found but not expected, "
              "in following catalogs:\n%s" % "\n".join(wrevs))


def setup_progress (configs_catpaths, addfmt):

    acps = [y[0] for x in configs_catpaths for y in x[1]]
    return init_file_progress(acps, addfmt=addfmt)


def examine_state (options, configs_catpaths, mode):

    # Count ascribed and unascribed messages through catalogs.
    counts_a = dict([(x, {}) for x in _all_states])
    counts_na = dict([(x, {}) for x in _all_states])

    upprog = setup_progress(configs_catpaths, "Examining state: %s")
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            # Open current and ascription catalog.
            cat = Catalog(catpath, monitored=False)
            clear_review_cat_simple(cat)
            acat = Catalog(acatpath, create=True, monitored=False)
            # Count non-ascribed by original catalog.
            for msg in cat:
                history = asc_collect_history(msg, acat, config,
                                              hfilter=options.hfilter,
                                              addrem=options.addrem)
                if history[0].user is None and not has_tracked_parts(msg):
                    continue # pristine
                if not mode.selector(msg, cat, history, config):
                    continue # not selected
                counts = history[0].user is None and counts_na or counts_a
                st = msg.state()
                if catpath not in counts[st]:
                    counts[st][catpath] = 0
                counts[st][catpath] += 1
            # Count non-ascribed by ascription catalog.
            for amsg in acat:
                if amsg not in cat:
                    ast = amsg.state()
                    st = None
                    if ast == _st_tran:
                        st = _st_otran
                    elif ast == _st_fuzzy:
                        st = _st_ofuzzy
                    elif ast == _st_untran:
                        st = _st_ountran
                    if st:
                        if catpath not in counts_na[st]:
                            counts_na[st][catpath] = 0
                        counts_na[st][catpath] += 1
    upprog()

    # Some general data for tabulation of output.
    coln = ["msg/t", "msg/f", "msg/u", "msg/ot", "msg/of", "msg/ou"]
    can_color = sys.stdout.isatty()
    none="-"

    # NOTE: When reporting, do not show anything if there are
    # neither ascribed nor non-ascribed messages selected.
    # If there are some ascribed and none non-ascribed,
    # show only the row for ascribed.
    # However, if there are some non-ascribed but none ascribed,
    # still show the row for ascribed, to not accidentally confuse
    # non-ascribed for ascribed.

    # Report totals.
    totals_a, totals_na = {}, {}
    for totals, counts in ((totals_a, counts_a), (totals_na, counts_na)):
        for st, cnt_per_cat in counts.items():
            totals[st] = sum(cnt_per_cat.values())
    # See previous NOTE.
    if sum(totals_a.values()) > 0 or sum(totals_na.values()) > 0:
        rown = ["ascribed"]
        data = [[totals_a[x] or None] for x in _all_states]
        if sum(totals_na.values()) > 0:
            rown.append("unascribed")
            for i in range(len(_all_states)):
                data[i].append(totals_na[_all_states[i]] or None)
        report(tabulate(data=data, coln=coln, rown=rown,
                        none=none, colorized=can_color))

    # Report counts per catalog if requested.
    if options.show_by_file:
        catpaths = set()
        for counts in (counts_a, counts_na):
            catpaths.update(sum([x.keys() for x in counts.values()], []))
        catpaths = sorted(catpaths)
        if catpaths:
            coln.insert(0, "catalog")
            coln.insert(1, "st")
            data = [[] for x in _all_states]
            for catpath in catpaths:
                cc_a = [counts_a[x].get(catpath, 0) for x in _all_states]
                cc_na = [counts_na[x].get(catpath, 0) for x in _all_states]
                # See previous NOTE.
                if sum(cc_a) > 0 or sum(cc_na) > 0:
                    data[0].append(catpath)
                    data[1].append("asc")
                    for datac, cc in zip(data[2:], cc_a):
                        datac.append(cc or None)
                    if sum(cc_na) > 0:
                        data[0].append("^^^")
                        data[1].append("nasc")
                        for datac, cc in zip(data[2:], cc_na):
                            datac.append(cc or None)
            if any(data):
                dfmt = ["%%-%ds" % max([len(x) for x in catpaths])]
                report("-")
                report(tabulate(data=data, coln=coln, dfmt=dfmt,
                                none=none, colorized=can_color))


def ascribe_modified (options, configs_catpaths, mode):

    assert_mode_user(configs_catpaths, mode)
    assert_no_review(configs_catpaths)

    if options.update_headers:
        update_headers_onmod(configs_catpaths, mode.user)

    ascribe_modified_w(options, configs_catpaths, mode)

    if options.commit:
        commit_catalogs(configs_catpaths, mode.user,
                        message=options.message)


def ascribe_modified_w (options, configs_catpaths, mode):

    upprog = setup_progress(configs_catpaths, "Ascribing modifications: %s")
    counts = dict([(x, 0) for x in _all_states])
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            ccounts = ascribe_modified_cat(options, config, mode.user,
                                           catpath, acatpath)
            for st, val in ccounts.items():
                counts[st] += val
    upprog()

    if counts[_st_tran] > 0:
        report("===! Translated: %d" % counts[_st_tran])
    if counts[_st_fuzzy] > 0:
        report("===! Fuzzy: %d" % counts[_st_fuzzy])
    if counts[_st_untran] > 0:
        report("===! Untranslated: %d" % counts[_st_untran])
    if counts[_st_otran] > 0:
        report("===! Obsolete translated: %d" % counts[_st_otran])
    if counts[_st_ofuzzy] > 0:
        report("===! Obsolete fuzzy: %d" % counts[_st_ofuzzy])
    if counts[_st_ountran] > 0:
        report("===! Obsolete untranslated: %d" % counts[_st_ountran])


def update_headers_onmod (configs_catpaths, user):

    upprog = setup_progress(configs_catpaths, "Updating headers: %s")
    nupdated = 0
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            cat = Catalog(catpath, monitored=False)
            acat = Catalog(acatpath, monitored=False, create=True)
            anymod = False
            for msg in cat:
                # Shallow history, need only to know if ascribed or not.
                history = asc_collect_history(msg, acat, config,
                                              shallow=True)
                # Message is modified if not ascribed
                # and has some ascription-relevant parts.
                if history[0].user is None and has_tracked_parts(msg):
                    anymod = True
                    break
            if anymod:
                # Must reopen monitored, but only header is needed.
                cat = Catalog(catpath, headonly=False)
                update_header(cat,
                              project=cat.name,
                              title=config.title,
                              name=config.udata[user].name,
                              email=config.udata[user].email,
                              teamemail=config.team_email,
                              langname=config.lang_team,
                              langcode=config.lang_code,
                              plforms=config.plural_header)
                if sync_and_rep(cat, shownmod=False):
                    nupdated += 1

    if nupdated > 0:
        report("===! Updated headers: %d" % nupdated)


def restore_reviews (configs_catpaths, revspecs_by_catmsg):

    upprog = setup_progress(configs_catpaths, "Restoring reviews: %s")
    nrestored = 0
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            revspecs_by_msg = revspecs_by_catmsg.get(catpath)
            if revspecs_by_msg:
                cat = Catalog(catpath, monitored=True)
                for msgref, revspec in sorted(revspecs_by_msg.items()):
                    msg = cat[msgref - 1]
                    revd, unrevd = revspec
                    restore_review_flags(msg, revd, unrevd)
                    nrestored += 1
                sync_and_rep(cat)

    if nrestored > 0:
        report("===! Restored reviews: %d" % nrestored)


def restore_review_flags (msg, revd, unrevd):

    if unrevd:
        msg.flag.add(_urevdflags[0])
    elif revd:
        msg.flag.add(_revdflags[0])

    return msg


def ascribe_reviewed (options, configs_catpaths, mode):

    assert_mode_user(configs_catpaths, mode, nousers=[UFUZZ])
    assert_review_tag(configs_catpaths, options.tag)

    # Remove any review diffs and flags from messages.
    # If any were actually removed, ascribe reviews only to those messages,
    # providing they pass the selector and were not tagged as unreviewed.
    # If there were no diffs and flags removed, ascribe reviews for all messages
    # that pass the selector, except those tagged as unreviewed.
    # In both cases, ascribe modifications to all modified messages.

    stest_orig = mode.selector
    stest_any = build_selector(["any"])

    mode.selector = stest_any
    options.keep_flags = False # deactivate this option if issued
    revspecs_by_catmsg = clear_review_w(options, configs_catpaths, mode)

    if options.update_headers:
        update_headers_onmod(configs_catpaths, mode.user)

    # Ascribe modifications.
    mode.selector = stest_any
    ascribe_modified_w(options, configs_catpaths, mode)

    # Check whether inclusive or exclusive mode applies for review.
    exclusive = True
    for revspecs in revspecs_by_catmsg.values():
        for revd, unrevd in revspecs.values():
            if revd:
                # Since there is at least one reviewed message,
                # inclusive mode is in effect.
                exclusive = False
                break
        if not exclusive:
            break

    if exclusive:
        def stest (msg, cat, hist, conf):
            # Exclude if tagged as unreviewed (overrides tagging as reviewed).
            revspec = revspecs_by_catmsg.get(cat.filename, {}).get(msg.refentry)
            if revspec:
                revd, unrevd = revspec
                if unrevd:
                    return False
            # Exclude if message does not pass selector.
            if not stest_orig(msg, cat, hist, conf):
                return False
            return True
        mode.selector = stest
    else:
        def stest (msg, cat, hist, conf):
            # Exclude if not among reviewed.
            revspec = revspecs_by_catmsg.get(cat.filename, {}).get(msg.refentry)
            if not revspec:
                return False
            # Exclude if tagged as unreviewed (overrides tagging as reviewed).
            revd, unrevd = revspec
            if unrevd:
                return False
            # Exclude if message does not pass selector.
            if not stest_orig(msg, cat, hist, conf):
                return False
            return True
        mode.selector = stest

    # Ascribe reviews.
    upprog = setup_progress(configs_catpaths, "Ascribing reviews: %s")
    nasc = 0
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            nasc += ascribe_reviewed_cat(options, config, mode.user,
                                         catpath, acatpath, mode.selector)
    upprog()
    if nasc > 0:
        report("===! Reviewed: %d" % nasc)

    if options.commit:
        onabortf = lambda: restore_reviews(configs_catpaths, revspecs_by_catmsg)
        commit_catalogs(configs_catpaths, mode.user,
                        message=options.message, onabortf=onabortf)


def diff_select (options, configs_catpaths, mode):

    upprog = setup_progress(configs_catpaths, "Diffing for review: %s")
    ndiffed = 0
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            ndiffed += diff_select_cat(options, config, catpath, acatpath,
                                       mode.selector, mode.aselector)
    upprog()
    if ndiffed > 0:
        report("===! Diffed for review: %d" % ndiffed)


def clear_review (options, configs_catpaths, mode):

    clear_review_w(options, configs_catpaths, mode)


def clear_review_w (options, configs_catpaths, mode):

    upprog = setup_progress(configs_catpaths, "Clearing reviews: %s")
    revspecs_by_catmsg = {}
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            revspecs_by_msg = clear_review_cat(options, config,
                                               catpath, acatpath,
                                               mode.selector)
            if revspecs_by_msg:
                revspecs_by_catmsg[catpath] = revspecs_by_msg
    upprog()

    ncleared = sum(map(len, revspecs_by_catmsg.values()))
    if ncleared > 0:
        if not options.keep_flags:
            report("===! Cleared reviews: %d" % ncleared)
        else:
            report("===! Cleared reviews (flags kept): %d" % ncleared)

    return revspecs_by_catmsg


def show_history (options, configs_catpaths, mode):

    upprog = setup_progress(configs_catpaths, "Computing histories: %s")
    nshown = 0
    for config, catpaths in configs_catpaths:
        for catpath, acatpath in catpaths:
            upprog(catpath)
            nshown += show_history_cat(options, config, catpath, acatpath,
                                       mode.selector)
    upprog()
    if nshown > 0:
        report("===> Computed histories: %d" % nshown)


def ascribe_modified_cat (options, config, user, catpath, acatpath):

    # Open current catalog and ascription catalog.
    cat = Catalog(catpath, monitored=False)
    acat = prep_write_asc_cat(acatpath, config)

    # Collect unascribed messages, but ignoring pristine ones
    # (those which are both untranslated and without history).
    toasc_msgs = []
    counts = dict([(x, 0) for x in _all_states])
    counts0 = counts.copy()
    for msg in cat:
        # Shallow history, need only to know if ascribed or not.
        history = asc_collect_history(msg, acat, config, shallow=True)
        if history[0].user is None and has_tracked_parts(msg):
            toasc_msgs.append(msg)
            counts[msg.state()] += 1

    # Collect non-obsolete ascribed messages that no longer have
    # original counterpart, to ascribe as obsolete.
    for amsg in acat:
        if amsg not in cat:
            ast = amsg.state()
            st = None
            if ast == _st_tran:
                st = _st_otran
            elif ast == _st_fuzzy:
                st = _st_ofuzzy
            elif ast == _st_untran:
                st = _st_ountran
            if st:
                msg = asc_collect_history_single(amsg, acat, config)[0].msg
                msg.obsolete = True
                toasc_msgs.append(msg)
                counts[st] += 1

    if not toasc_msgs:
        # No messages to ascribe.
        return counts0

    # Ascribe messages as modified.
    for msg in toasc_msgs:
        ascribe_msg_mod(msg, acat, user, config)

    if asc_sync_and_rep(acat):
        config.vcs.add(acat.filename)

    return counts


def ascribe_reviewed_cat (options, config, user, catpath, acatpath, stest):

    # Open current catalog and ascription catalog.
    # Monitored, for removal of review flags.
    cat = Catalog(catpath, monitored=True)
    acat = prep_write_asc_cat(acatpath, config)

    revd_msgs = []
    non_mod_asc_msgs = []
    for msg in cat:
        # Remove any review scaffolding.
        unrevd = clear_review_msg(msg)[3]
        if unrevd:
            # Message explicitly set as not reviewed.
            continue

        history = asc_collect_history(msg, acat, config,
                                      hfilter=options.hfilter,
                                      addrem=options.addrem)
        # Makes no sense to ascribe review to pristine messages.
        if history[0].user is None and not has_tracked_parts(msg):
            continue
        if not stest(msg, cat, history, config):
            continue
        # Message cannot be ascribed as reviewed if it has not been
        # already ascribed as modified.
        if history[0].user is None:
            # Collect to report later.
            non_mod_asc_msgs.append(msg)
            continue

        revd_msgs.append(msg)

    if non_mod_asc_msgs:
        fmtrefs = ", ".join(["%s(#%s)" % (x.refline, x.refentry)
                             for x in non_mod_asc_msgs])
        warning("%s: some messages cannot be ascribed as reviewed "
                "because they were not ascribed as modified: %s"
                % (cat.filename, fmtrefs))

    if not revd_msgs:
        # No messages to ascribe.
        if non_mod_asc_msgs:
            # May have had some reviews cleared.
            sync_and_rep(cat)
        return 0

    # Ascribe messages as reviewed.
    for msg in revd_msgs:
        ascribe_msg_rev(msg, acat, options.tag, user, config)

    sync_and_rep(cat)
    if asc_sync_and_rep(acat):
        config.vcs.add(acat.filename)

    return len(revd_msgs)


def diff_select_cat (options, config, catpath, acatpath, stest, aselect):

    cat = Catalog(catpath, monitored=True)
    clear_review_cat_simple(cat)
    acat = Catalog(acatpath, create=True, monitored=False)

    diffed_msgs = []
    for msg in cat:
        history = asc_collect_history(msg, acat, config,
                                      hfilter=options.hfilter,
                                      addrem=options.addrem)
        # Makes no sense to review pristine messages.
        if history[0].user is None and not has_tracked_parts(msg):
            continue
        sres = stest(msg, cat, history, config)
        if not sres:
            continue

        # Try to select ascription to differentiate from.
        # (Note that ascription indices returned by selectors are 1-based.)
        i_asc = None
        if aselect:
            asres = aselect(msg, cat, history, config)
            i_asc = (asres - 1) if asres else None
        elif not isinstance(sres, bool):
            # If there is no ascription selector, but basic selector returned
            # an ascription index, use first earlier non-fuzzy for diffing.
            i_asc = sres - 1
            i_asc = first_nfuzzy(history, i_asc + 1)

        # Differentiate and flag.
        amsg = i_asc is not None and history[i_asc].msg or None
        if amsg is not None:
            if editprob(amsg.msgid, msg.msgid) > options.min_adjsim_diff:
                msg_ediff(amsg, msg, emsg=msg, pfilter=options.sfilter)
                msg.flag.add(_diffflag)
            else:
                msg.flag.add(_diffflag_ign)
        else:
            # If no previous ascription selected, add special flag
            # to denote that the whole message is to be reviewed.
            msg.flag.add(_diffflag_tot)

        # Add ascription chain comment.
        ascfmts = []
        i_from = (i_asc - 1) if i_asc is not None else len(history) - 1
        for i in range(i_from, -1, -1):
            a = history[i]
            shtype = {ATYPE_MOD: "m", ATYPE_REV: "r"}[a.type]
            if a.tag:
                ascfmt = "%s:%s(%s)" % (a.user, shtype, a.tag)
            else:
                ascfmt = "%s:%s" % (a.user, shtype)
            ascfmts.append(ascfmt)
        achnfmt = _achncmnt + " " + " ".join(ascfmts)
        msg.auto_comment.append(achnfmt)

        diffed_msgs.append(msg)

    sync_and_rep(cat)

    # Open in the PO editor if requested.
    if options.po_editor:
        for msg in diffed_msgs:
            options.po_editor(msg, cat, report="Selected for review.")

    return len(diffed_msgs)


_subreflags = "|".join(_all_flags)
_subrecmnts = "|".join(_all_cmnts)
_any_to_clear_rx = re.compile(r"^\s*(#,.*\b(%s)|#\.\s*(%s))"
                              % (_subreflags, _subrecmnts),
                              re.M|re.U)

# Quickly check if it may be that some messages in the PO file
# have review states (diffs, flags).
def may_have_reviews (catpath):

    return bool(_any_to_clear_rx.search(open(catpath).read()))


def clear_review_cat (options, config, catpath, acatpath, stest):

    revspecs_by_msg = {}

    if not may_have_reviews(catpath):
        return revspecs_by_msg

    cat = Catalog(catpath, monitored=True)
    acat = Catalog(acatpath, create=True, monitored=False)

    for msg in cat:
        cmsg = MessageUnsafe(msg)
        clear_review_msg(cmsg)
        history = asc_collect_history(cmsg, acat, config,
                                      hfilter=options.hfilter,
                                      addrem=options.addrem)
        if not stest(cmsg, cat, history, config):
            continue
        clres = clear_review_msg(msg, keepflags=options.keep_flags)
        if any(clres):
            diffed, revd, unrevd = clres[1:4]
            revspecs_by_msg[msg.refentry] = (diffed or revd, unrevd)

    sync_and_rep(cat)

    return revspecs_by_msg


def clear_review_cat_simple (cat, keepflags=False):

    revspecs_by_msg = {}
    for msg in cat:
        clres = clear_review_msg(msg, keepflags=keepflags)
        if any(clres):
            diffed, revd, unrevd = clres[1:4]
            revspecs_by_msg[msg.refentry] = (diffed or revd, unrevd)

    return revspecs_by_msg


def show_history_cat (options, config, catpath, acatpath, stest):

    C = colors_for_file(sys.stdout)

    cat = Catalog(catpath, monitored=False)
    clear_review_cat_simple(cat)
    acat = Catalog(acatpath, create=True, monitored=False)

    nselected = 0
    for msg in cat:
        history = asc_collect_history(msg, acat, config,
                                      hfilter=options.hfilter,
                                      addrem=options.addrem,
                                      nomrg=True)
        if not stest(msg, cat, history, config):
            continue
        nselected += 1

        unasc = history[0].user is None
        if unasc:
            history.pop(0)

        hlevels = len(history)
        if options.depth is not None:
            hlevels = int(options.depth)
            if history[0].user is None:
                hlevels += 1
            if hlevels > len(history):
                hlevels = len(history)

        hinfo = []
        if hlevels > 0:
            hinfo += [C.GREEN + ">>> history follows:" + C.RESET]
            hfmt = "%%%dd" % len(str(hlevels))
        for i in range(hlevels):
            a = history[i]
            typewtag = a.type
            if a.tag != "":
                typewtag += "/" + a.tag
            ihead = C.BOLD + "#%d" % a.pos + C.RESET + " "
            anote_d = dict(usr=a.user, mod=typewtag, dat=a.date)
            anote = "%(mod)s by %(usr)s on %(dat)s" % anote_d
            hinfo += [ihead + anote]
            if not a.type == ATYPE_MOD:
                # Nothing more to show if this ascription is not modification.
                continue
            i_next = i + 1
            if i_next == len(history):
                # Nothing more to show at end of history.
                continue
            dmsg = MessageUnsafe(a.msg)
            nmsg = history[i_next].msg
            if dmsg != nmsg:
                msg_ediff(nmsg, dmsg, emsg=dmsg,
                          pfilter=options.sfilter, hlto=sys.stdout)
                dmsgfmt = dmsg.to_string(force=True,
                                         wrapf=cat.wrapf()).rstrip("\n")
                hindent = " " * (len(hfmt % 0) + 2)
                hinfo += [hindent + x for x in dmsgfmt.split("\n")]
        hinfo = "\n".join(hinfo)

        if unasc or msg.fuzzy:
            pmsg = None
            i_nfasc = first_nfuzzy(history)
            if i_nfasc is not None:
                pmsg = history[i_nfasc].msg
            elif msg.fuzzy and msg.msgid_previous is not None:
                pmsg = MessageUnsafe(msg)
                pmsg.unfuzzy()
                for fcurr, fprev in zip(_fields_current, _fields_previous):
                    setattr(pmsg, fcurr, msg.get(fprev))
            if pmsg is not None:
                for fprev in _fields_previous:
                    setattr(msg, fprev, None)
                msg_ediff(pmsg, msg, emsg=msg,
                          pfilter=options.sfilter, hlto=sys.stdout)
        report_msg_content(msg, cat,
                           note=(hinfo or None), delim=("-" * 20))

    return nselected


def clear_review_msg (msg, keepflags=False):

    # Clear possible review flags.
    diffed = False
    revd = False
    unrevd = False
    for flag in list(msg.flag): # modified inside
        if flag in _diffflags:
            diffed = True
            msg.flag.remove(flag)
        elif flag in _revdflags:
            revd = True
            msg.flag.remove(flag)
        elif flag in _urevdflags:
            unrevd = True
            msg.flag.remove(flag)

    # Clear possible review comments.
    i = 0
    commented = False
    while i < len(msg.auto_comment):
        cmnt = msg.auto_comment[i].strip()
        if cmnt.startswith(_all_cmnts):
            msg.auto_comment.pop(i)
            commented = True
        else:
            i += 1

    if diffed:
        msg_ediff_to_new(msg, rmsg=msg)

    if keepflags:
        restore_review_flags(msg, diffed or revd, unrevd)

    return commented, diffed, revd, unrevd


# Exclusive states of a message, as reported by Message.state().
# FIXME: These keywords better exported to pology.file.message
_st_tran = "T"
_st_fuzzy = "F"
_st_untran = "U"
_st_otran = "OT"
_st_ofuzzy = "OF"
_st_ountran = "OU"
_all_states = (
    _st_tran, _st_fuzzy, _st_untran,
    _st_otran, _st_ofuzzy, _st_ountran,
)


def first_nfuzzy (history, start=0):

    for i in range(start, len(history)):
        hmsg = history[i].msg
        if hmsg and not hmsg.fuzzy:
            return i

    return None


def prep_write_asc_cat (acatpath, config):

    if not os.path.isfile(acatpath):
        return init_asc_cat(acatpath, config)
    else:
        return Catalog(acatpath, monitored=True, wrapping=ASCWRAPPING)


def init_asc_cat (acatpath, config):

    acat = Catalog(acatpath, create=True, monitored=True, wrapping=ASCWRAPPING)
    ahdr = acat.header

    ahdr.title = Monlist([u"Ascription shadow for %s.po" % acat.name])

    translator = u"Ascriber"

    if config.team_email:
        author = u"%s <%s>" % (translator, config.team_email)
    else:
        author = u"%s" % translator
    ahdr.author = Monlist([author])

    ahdr.copyright = u"Copyright same as for the original catalog."
    ahdr.license = u"License same as for the original catalog."
    ahdr.comment = Monlist([u"===== DO NOT EDIT MANUALLY ====="])

    ahdr.set_field(u"Project-Id-Version", unicode(acat.name))
    ahdr.set_field(u"Report-Msgid-Bugs-To", unicode(config.team_email or ""))
    ahdr.set_field(u"PO-Revision-Date", unicode(format_datetime(_dt_start)))
    ahdr.set_field(u"Content-Type", u"text/plain; charset=UTF-8")
    ahdr.set_field(u"Content-Transfer-Encoding", u"8bit")

    if config.team_email:
        ltr = "%s <%s>" % (translator, config.team_email)
    else:
        ltr = translator
    ahdr.set_field(u"Last-Translator", unicode(ltr))

    if config.lang_team:
        if config.team_email:
            tline = u"%s <%s>" % (config.lang_team, config.team_email)
        else:
            tline = config.lang_team
        ahdr.set_field(u"Language-Team", unicode(tline))
    else:
        ahdr.remove_field("Language-Team")

    if config.lang_code:
        ahdr.set_field(u"Language", unicode(config.lang_code))
    else:
        ahdr.remove_field("Language")

    if config.plural_header:
        ahdr.set_field(u"Plural-Forms", unicode(config.plural_header))
    else:
        ahdr.remove_field(u"Plural-Forms")

    return acat


def update_asc_hdr (acat):

    acat.header.set_field(u"PO-Revision-Date",
                          unicode(format_datetime(_dt_start)))


_id_fields = (
    "msgctxt", "msgid",
)
_nonid_fields = (
    "msgid_plural", "msgstr",
)
_fields_previous = (
    "msgctxt_previous", "msgid_previous", "msgid_plural_previous",
)
_fields_current = (
    "msgctxt", "msgid", "msgid_plural",
)
_fields_comment = (
    "manual_comment", "auto_comment",
)
_nonid_fields_tracked = (()
    + _nonid_fields
    + _fields_previous
    + ("manual_comment",)
)
_multiple_fields = (()
    + ("msgstr",)
    + _fields_comment
)
_nonid_fields_eq_nonfuzzy = (()
    + _nonid_fields
    + ("manual_comment",)
)
_nonid_fields_eq_fuzzy = (()
    + _nonid_fields_eq_nonfuzzy
    + _fields_previous
)

_trsep_head = u"|"
_trsep_head_ext = u"~"
_trsep_mod_none = u"x"
_trsep_mod_eq = u"e"

def field_separator_head (length):

    return _trsep_head + _trsep_head_ext * length


def needed_separator_length (msg):

    goodsep = False
    seplen = 0
    while not goodsep:
        seplen += 1
        sephead = field_separator_head(seplen)
        goodsep = True
        for field in _nonid_fields_tracked:
            values = msg.get(field)
            if values is None:
                continue
            if isinstance(values, basestring):
                values = [values]
            for value in values:
                if sephead in value:
                    goodsep = False
                    break
            if not goodsep:
                break

    return seplen


def has_nonid_diff (pmsg, msg):

    for field in _nonid_fields_tracked:
        msg_value = msg.get(field)
        if not msg.fuzzy and field in _fields_previous:
            # Ignore previous values in messages with no fuzzy flag.
            msg_value = None
        pmsg_value = pmsg.get(field)
        if msg_value != pmsg_value:
            return True

    return False


def get_as_sequence (msg, field, asc=True):

    if not asc and not msg.fuzzy and field in _fields_previous:
        # Ignore previous fields on non-ascription messages without fuzzy flag.
        return []

    msg_seq = msg.get(field)
    if msg_seq is None:
        msg_seq = []
    elif field not in _multiple_fields:
        msg_seq = [msg_seq]
    elif field in _fields_comment:
        # Report comments as a single newline-delimited entry.
        if msg_seq:
            msg_seq = [u"\n".join(msg_seq)]

    return msg_seq


def set_from_sequence (msg_seq, msg, field):

    if field not in _multiple_fields:
        # Single entry; set to given, or to None if no elements.
        msg_val = None
        if msg_seq:
            msg_val = msg_seq[0]
        multiple = False
    elif field in _fields_comment:
        # Comments treated as single newline-delimited entries; split.
        msg_val = []
        if msg_seq:
            msg_val = msg_seq[0].split("\n")
        multiple = True
    else:
        # Straight sequence.
        msg_val = msg_seq
        multiple = True

    if multiple and isinstance(msg, Message):
        msg_val = Monlist(msg_val)

    setattr(msg, field, msg_val)


def add_nonid (amsg, msg, slen, rhistory):

    shead = field_separator_head(slen)
    nones = [field_separator_head(x.slen) + _trsep_mod_none
             for x in rhistory if x.slen]
    padnone = u"\n".join(nones)

    for field in _nonid_fields_tracked:

        msg_seq = get_as_sequence(msg, field, asc=False)
        amsg_seq = get_as_sequence(amsg, field)

        # Expand items to length in new message.
        for i in range(len(amsg_seq), len(msg_seq)):
            amsg_seq.append(padnone)

        # Add to items.
        for i in range(len(amsg_seq)):
            if i < len(msg_seq):
                nmod = 0
                i_eq = None
                for a in rhistory:
                    if not a.slen: # no modification in this ascription
                        continue
                    if i_eq is None:
                        msg_seq_p = get_as_sequence(a.msg, field)
                        if i < len(msg_seq_p) and msg_seq[i] == msg_seq_p[i]:
                            i_eq = nmod
                            # ...no break, need number of modifications.
                    nmod += 1
                if i_eq is None:
                    add = msg_seq[i] + shead
                else:
                    add = shead + _trsep_mod_eq + str(i_eq)
            else:
                add = shead + _trsep_mod_none
            if amsg_seq[i]:
                amsg_seq[i] += u"\n"
            amsg_seq[i] += add

        set_from_sequence(amsg_seq, amsg, field)


_atag_sep = u"/"
_mark_fuzz = u"f"
_mark_obs = u"o"

def ascribe_msg_any (msg, acat, atype, atags, user, config, dt=None):

    # Create or retrieve ascription message.
    if msg not in acat:
        # Copy ID elements of the original message.
        amsg = Message()
        for field in _id_fields:
            setattr(amsg, field, getattr(msg, field))
        # Append to the end of catalog.
        acat.add_last(amsg)
    else:
        # Retrieve existing ascription message.
        amsg = acat[msg]

    # Reconstruct historical messages, from first to last.
    rhistory = asc_collect_history_single(amsg, acat, config)
    rhistory.reverse()

    # Do any of non-ID elements differ to last historical message?
    if rhistory:
        hasdiff_state = rhistory[-1].msg.state() != msg.state()
        hasdiff_nonid = has_nonid_diff(rhistory[-1].msg, msg)
    else:
        hasdiff_nonid = True
        hasdiff_state = True
    hasdiff = hasdiff_nonid or hasdiff_state

    # Add ascription comment.
    modstr = user + " | " + format_datetime(dt, wsec=True)
    modstr_wsep = modstr
    if hasdiff:
        wsep = ""
        if hasdiff_nonid:
            seplen = needed_separator_length(msg)
            wsep += str(seplen)
        if msg.obsolete:
            wsep += _mark_obs
        if msg.fuzzy:
            wsep += _mark_fuzz
        if wsep:
            modstr_wsep += " | " + wsep
    first = True
    for atag in atags or [""]:
        field = atype
        if atag != "":
            field += _atag_sep + atag
        if first:
            asc_append_field(amsg, field, modstr_wsep)
            first = False
        else:
            asc_append_field(amsg, field, modstr)

    # Add non-ID fields.
    if hasdiff_nonid:
        add_nonid(amsg, msg, seplen, rhistory)

    # Update state.
    if msg.fuzzy:
        amsg.flag.add(u"fuzzy")
    else:
        amsg.flag.remove(u"fuzzy")
    if msg.obsolete:
        amsg.obsolete = True
    else:
        amsg.obsolete = False


# FIXME: Imported by others, factor out.
# NOTE: These string are written and read from ascription files.
ATYPE_MOD = "modified"
ATYPE_REV = "reviewed"


def ascribe_msg_mod (msg, acat, user, config):

    ascribe_msg_any(msg, acat, ATYPE_MOD, [], user, config, _dt_start)


def ascribe_msg_rev (msg, acat, tags, user, config):

    ascribe_msg_any(msg, acat, ATYPE_REV, tags, user, config, _dt_start)


# FIXME: Imported by others, factor out.
def asc_eq (msg1, msg2):
    """
    Whether two messages are equal from the ascription viewpoint.
    """

    if msg1.state() != msg2.state():
        return False
    if msg1.fuzzy:
        check_fields = _nonid_fields_eq_fuzzy
    else:
        check_fields = _nonid_fields_eq_nonfuzzy
    for field in check_fields:
        if msg1.get(field) != msg2.get(field):
            return False
    return True


def merge_modified (msg1, msg2):
    """
    Whether second message may be considered derived from first by merging.
    """

    # Manual comments do not change on merge.
    if msg1.manual_comment != msg2.manual_comment:
        return False

    # Current and previous original fields may have changed on merge,
    # depending on whether both messages are fuzzy, or only one, and which.
    if msg1.fuzzy == msg2.fuzzy:
        fields = msg1.fuzzy and _fields_previous or _fields_current
        for field in fields:
            if msg1.get(field) != msg2.get(field):
                return False
    else:
        fields = (msg1.fuzzy and zip(_fields_previous, _fields_current)
                              or zip(_fields_current, _fields_previous))
        for field1, field2 in fields:
            if msg1.get(field1) != msg2.get(field2):
                return False

    # Translation does not change on merge,
    # except for multiplication/reduction when plurality differs.
    if (msg1.msgid_plural is None) == (msg2.msgid_plural is None):
        if msg1.msgstr != msg2.msgstr:
            return False
    else:
        if not msg1.fuzzy and not msg2.fuzzy:
            # Plurality cannot change between two non-fuzzy messages.
            return False
        if msg1.msgid_plural is not None:
            # Reduction to non-plural.
            if msg1.msgstr[0] != msg2.msgstr[0]:
                return False
        else:
            # Multiplication to plural.
            for msgstr in msg2.msgstr:
                if msgstr != msg1.msgstr[0]:
                    return False

    return True


fld_sep = ":"

def asc_append_field (msg, field, value):

    stext = u"".join([field, fld_sep, " ", str(value)])
    msg.auto_comment.append(stext)


_asc_attrs = (
    "rmsg", "msg",
    "user", "type", ("tag", ""), "date",
    "slen", "fuzz", "obs",
    "pos"
)

class _Ascription (object):

    def __init__ (self, asc=None):

        for attr in _asc_attrs:
            if isinstance(attr, tuple):
                attr, dval = attr
            else:
                attr, dval = attr, None
            if asc is not None:
                self.__dict__[attr] = asc.__dict__[attr]
            else:
                self.__dict__[attr] = dval

    def __setattr__ (self, attr, val):

        if attr not in self.__dict__:
            raise KeyError("Trying to set unknown ascription attributed '%s'."
                           % attr)
        self.__dict__[attr] = val


def asc_collect_history (msg, acat, config,
                         nomrg=False, hfilter=None, shallow=False,
                         addrem=None):

    history = asc_collect_history_w(msg, acat, config, None, set(), shallow)

    # If the message is not ascribed,
    # add it in front as modified by unknown user.
    if not history or not asc_eq(msg, history[0].msg):
        a = _Ascription()
        a.type = ATYPE_MOD
        a.user = None
        a.msg = msg
        history.insert(0, a)

    # Equip ascriptions with position markers,
    # to be able to see gaps possibly introduced by removals.
    pos = 1
    for a in history:
        a.pos = pos
        pos += 1

    # Eliminate clean merges from history.
    if nomrg:
        history_r = []
        for i in range(len(history) - 1):
            a, ao = history[i], history[i + 1]
            if not a.user == UFUZZ or not merge_modified(ao.msg, a.msg):
                history_r.append(a)
        if history[-1].user != UFUZZ:
            history_r.append(history[-1])
        history = history_r

    # Eliminate contiguous chain of modifications equal under the filter,
    # except for the earliest in the chain.
    # (After elimination of clean merges.)
    if hfilter:
        def flt (msg):
            msg = MessageUnsafe(msg)
            msg.msgstr = map(hfilter, msg.msgstr)
            return msg
        history_r = []
        a_prevmod = None
        history.reverse()
        for a in history:
            if (   a.type != ATYPE_MOD or not a_prevmod
                or flt(a.msg).inv != a_prevmod.msg.inv
            ):
                history_r.append(a)
                if a.type == ATYPE_MOD:
                    a_prevmod = _Ascription(a)
                    a_prevmod.msg = flt(a.msg)
        history = history_r
        history.reverse()

    # Reduce history to particular segments of diffs between modifications.
    # (After filtering).
    if addrem:
        a_nextmod = None
        for a in history:
            if a.type == ATYPE_MOD:
                if a_nextmod is not None:
                    msg_ediff(a.msg, a_nextmod.msg, emsg=a_nextmod.msg,
                              addrem=addrem)
                a_nextmod = a

    return history


def asc_collect_history_w (msg, acat, config, before, seenmsg, shallow=False):

    history = []

    # Avoid circular paths.
    if msg.key in seenmsg:
        return history
    seenmsg.add(msg.key)

    # Collect history from current ascription message.
    if msg in acat:
        amsg = acat[msg]
        for a in asc_collect_history_single(amsg, acat, config):
            if not before or a.date <= before.date:
                history.append(a)

    if shallow:
        return history

    # Continue into the past by pivoting around earliest message if fuzzy.
    amsg = history[-1].msg if history else msg
    if amsg.fuzzy and amsg.msgid_previous:
        pmsg = MessageUnsafe()
        for field in _id_fields:
            setattr(pmsg, field, amsg.get(field + "_previous"))
        # All ascriptions beyond the pivot must be older than the oldest so far.
        after = history and history[-1] or before
        ct_history = asc_collect_history_w(pmsg, acat, config, after, seenmsg)
        history.extend(ct_history)

    return history


def amsg_step_value (aval, shead, stail, spos, pvals, i):

    if i >= len(spos):
        spos.extend([0] * (i - len(spos) + 1))
    if i >= len(pvals):
        pvals.extend([[] for x in range(i - len(pvals) + 1)])
    p0 = spos[i]
    p1 = aval.find(shead, p0)
    p2 = aval.find(stail, p1 + 1)
    if p2 < 0:
        p2 = len(aval)
    spos[i] = p2 + len(stail)
    mods = aval[p1 + len(shead):p2]
    if _trsep_mod_eq in mods:
        q1 = mods.find(_trsep_mod_eq) + len(_trsep_mod_eq)
        q2 = q1
        while q2 < len(mods) and mods[q2].isdigit():
            q2 += 1
        nrev = int(mods[q1:q2])
        pval = pvals[i][nrev]
    else:
        if _trsep_mod_none in mods:
            pval = None
        else:
            pval = aval[p0:p1]
    pvals[i].append(pval)
    return pval


def asc_collect_history_single (amsg, acat, config):

    history = []
    spos = dict([(field, [0]) for field in _nonid_fields_tracked])
    pvals = dict([(field, [[]]) for field in _nonid_fields_tracked])
    for asc in asc_parse_ascriptions(amsg, acat, config):
        a = _Ascription()
        a.user, a.type, a.tag, a.date, a.slen, a.fuzz, a.obs = asc
        if a.slen: # separator existing, reconstruct the fields
            shead = field_separator_head(a.slen)
            pmsg = MessageUnsafe()
            for field in _id_fields:
                setattr(pmsg, field, amsg.get(field))
            for field in _nonid_fields_tracked:
                amsg_seq = get_as_sequence(amsg, field)
                pmsg_seq = []
                for i in range(len(amsg_seq)):
                    aval = amsg_seq[i]
                    pval = amsg_step_value(aval, shead, u"\n",
                                           spos[field], pvals[field], i)
                    # ...do not break if None, has to roll all spos items
                    if pval is not None:
                        while i >= len(pmsg_seq):
                            pmsg_seq.append(u"")
                        pmsg_seq[i] = pval
                set_from_sequence(pmsg_seq, pmsg, field)
        else:
            pmsg = MessageUnsafe(history[-1].msg) # must exist
        if a.fuzz:
            pmsg.flag.add(u"fuzzy")
        elif u"fuzzy" in pmsg.flag:
            pmsg.flag.remove(u"fuzzy")
        pmsg.obsolete = a.obs
        a.rmsg, a.msg = amsg, pmsg
        history.append(a)

    # Sort history by date and put it in reverse.
    # If several ascriptions have same time stamps, preserve their order.
    history_ord = zip(history, range(len(history)))
    history_ord.sort(key=lambda x: (x[0].date, x[1]))
    history_ord.reverse()
    history = [x[0] for x in history_ord]

    return history


def asc_parse_ascriptions (amsg, acat, config):
    """
    Get ascriptions from given ascription message as list of tuples
    C{(user, type, tag, date, seplen, isfuzzy, isobsolete)},
    with date being a real C{datetime} object.
    """

    ascripts = []
    for cmnt in amsg.auto_comment:
        p = cmnt.find(":")
        if p < 0:
            warning_on_msg("malformed ascription comment '%s' "
                           "(no ascription type)" % cmnt, amsg, acat)
            continue
        atype = cmnt[:p].strip()
        atag = ""
        lst = atype.split(_atag_sep, 1)
        if len(lst) == 2:
            atype = lst[0].strip()
            atag = lst[1].strip()
        lst = cmnt[p+1:].split("|")
        if len(lst) < 2 or len(lst) > 3:
            warning_on_msg("malformed ascription comment '%s' "
                           "(wrong number of descriptors)" % cmnt, amsg, acat)
            continue

        auser = lst.pop(0).strip()
        if not auser:
            warning_on_msg("malformed ascription comment '%s' "
                           "(malformed user string)" % cmnt, amsg, acat)
            continue
        if auser not in config.users:
            warning_on_msg("malformed ascription comment '%s' "
                           "(unknown user)" % cmnt, amsg, acat)
            continue

        datestr = lst.pop(0).strip()
        try:
            date = parse_datetime(datestr)
        except:
            warning_on_msg("malformed ascription comment '%s' "
                           "(malformed date string)" % cmnt, amsg, acat)
            continue

        # States are reset only on modification ascriptions,
        # in order to keep them for the following review ascriptions.
        if atype == ATYPE_MOD:
            isfuzz = False
            isobs = False
        seplen = 0
        if lst:
            tmp = lst.pop(0).strip()
            if _mark_fuzz in tmp:
                isfuzz = True
                tmp = tmp.replace(_mark_fuzz, "", 1)
            if _mark_obs in tmp:
                isobs = True
                tmp = tmp.replace(_mark_obs, "", 1)
            if tmp:
                try:
                    seplen = int(tmp)
                except:
                    warning_on_msg("malformed ascription comment '%s' "
                                   "(malformed separator length)"
                                   % cmnt, amsg, acat)
                    continue

        ascripts.append((auser, atype, atag, date, seplen, isfuzz, isobs))

    return ascripts


_modified_cats = []

def sync_and_rep (cat, shownmod=True):

    if shownmod:
        nmod = 0
        for msg in cat:
            if msg.modcount:
                nmod += 1

    modified = cat.sync()
    if modified:
        if shownmod:
            report("!    %s  (%d)" % (cat.filename, nmod))
        else:
            report("!    %s" % cat.filename)
        _modified_cats.append(cat.filename)

    return modified


def asc_sync_and_rep (acat):

    if acat.modcount:
        update_asc_hdr(acat)
        mkdirpath(os.path.dirname(acat.filename))

    return sync_and_rep(acat)


def has_tracked_parts (msg):

    for part in _nonid_fields_tracked:
        pval = msg.get(part)
        if part not in _multiple_fields:
            if pval is not None:
                return True
        elif part == "msgstr":
            for pval1 in pval:
                if pval1:
                    return True
        else:
            if pval:
                return True

    return False


class _TZInfo (datetime.tzinfo):

    def __init__ (self, hours=None, minutes=None):

        self._isdst = time.localtime()[-1]
        if hours is None and minutes is None:
            tzoff_sec = -(time.altzone if self._isdst else time.timezone)
            tzoff_hr = tzoff_sec // 3600
            tzoff_min = (tzoff_sec - tzoff_hr * 3600) // 60
        else:
            tzoff_hr = hours or 0
            tzoff_min = minutes or 0

        self._dst = datetime.timedelta(0)
        self._utcoffset = datetime.timedelta(hours=tzoff_hr, minutes=tzoff_min)

    def utcoffset (self, dt):
        return self._utcoffset

    def dst (self, dt):
        return self._dst

    def tzname (self, dt):
        return time.tzname[self._isdst]


_dt_start = datetime.datetime(*(time.localtime()[:6] + (0, _TZInfo())))

_dt_fmt = "%Y-%m-%d %H:%M:%S%z"
_dt_fmt_nosec = "%Y-%m-%d %H:%M%z"


# FIXME: Imported by other scripts, move out of here.
def format_datetime (dt=None, wsec=False):

    if dt is not None:
        if wsec:
            dtstr = dt.strftime(_dt_fmt)
        else:
            dtstr = dt.strftime(_dt_fmt_nosec)
        # If timezone is not present, assume UTC.
        if dt.tzinfo is None:
            dtstr += "+0000"
    else:
        if wsec:
            dtstr = time.strftime(_dt_fmt)
        else:
            dtstr = time.strftime(_dt_fmt_nosec)

    return unicode(dtstr)


_parse_date_rxs = [re.compile(x) for x in (
    r"^ *(\d+)-(\d+)-(\d+) *(\d+):(\d+):(\d+) *([+-]\d+) *$",
    r"^ *(\d+)-(\d+)-(\d+) *(\d+):(\d+)() *([+-]\d+) *$",
    # ...needs empty group to differentiate from the next case.
    r"^ *(\d+)-(\d+)-(\d+) *(\d+):(\d+):(\d+) *$",
    r"^ *(\d+)-(\d+)-(\d+) *(\d+):(\d+) *$",
    r"^ *(\d+)-(\d+)-(\d+) *$",
    r"^ *(\d+)-(\d+) *$",
    r"^ *(\d+) *$",
)]

# FIXME: Imported by other scripts, move out of here.
def parse_datetime (dstr):

    for parse_date_rx in _parse_date_rxs:
        m = parse_date_rx.search(dstr)
        if m:
            break
    if not m:
        raise StandardError, "cannot parse date string '%s'" % dstr
    pgroups = list([int(x or 0) for x in m.groups()])
    pgroups.extend([1] * (3 - len(pgroups)))
    pgroups.extend([0] * (7 - len(pgroups)))
    year, month, day, hour, minute, second, off = pgroups
    offhr = off // 100
    offmin = off % 100
    dt = datetime.datetime(year=year, month=month, day=day,
                           hour=hour, minute=minute, second=second,
                           tzinfo=_TZInfo(hours=offhr, minutes=offmin))
    return dt


def parse_users (userstr, config, cid=None):
    """
    Parse users from comma-separated list, verifying that they exist.

    If the list starts with tilde (~), all users found in the config
    but for the listed will be selected (inverted selection).

    C{cid} is the string identifying the caller, for error report in
    case the a parsed user does not exist.
    """

    return parse_fixed_set(userstr, config, config.users,
                           "user '%s' not defined in '%s'", cid)


def parse_tags (tagstr, config, cid=None):
    """
    Parse tags from comma-separated list, verifying that they exist.

    If the list starts with tilde (~), all tags found in the config
    but for the listed will be selected (inverted selection).

    C{cid} is the string identifying the caller, for error report in
    case the a parsed user does not exist.
    """

    tags = parse_fixed_set(tagstr, config, config.review_tags,
                           "review tag '%s' not defined in '%s'", cid)
    if not tags:
        tags = set([""])

    return tags


def parse_fixed_set (elstr, config, knownels, errfmt, cid=None):

    if not elstr:
        return set()

    elstr = elstr.replace(" ", "")
    inverted = False
    if elstr.startswith("~"):
        inverted = True
        elstr = elstr[1:]

    els = set(elstr.split(","))
    for el in els:
        if el not in knownels:
            error(errfmt % (el, config.path), subsrc=cid)
    if inverted:
        els = set(knownels).difference(els)

    return els


# Build compound selector out of list of specifications.
# Selector specification is a string in format NAME:ARG1:ARG2:...
# (instead of colon, separator can be any non-alphanumeric excluding
# underscore and hyphen)
def build_selector (selspecs, hist=False):

    # Component selectors.
    selectors = []
    for selspec in selspecs:
        argsep = ":"
        for c in selspec:
            if not (c.isalpha() or c.isdigit() or c in ("_", "-")):
                argsep = c
                break
        lst = selspec.split(argsep)
        sname, sargs = lst[0], lst[1:]
        negated = False
        if sname.startswith("n"):
            sname = sname[1:]
            negated = True
        sfactory, can_hist = xm_selector_factories.get(sname, (None, False))
        if not sfactory:
            error("unknown selector '%s'" % sname)
        if hist:
            if not can_hist:
                error("selector '%s' cannot be used "
                      "as history selector" % sname)
            if negated:
                error("negated selectors (here '%s') cannot be used "
                      "as history selectors" % sname)
        selector = sfactory(*sargs)
        if negated:
            selector = negate_selector(selector)
        selectors.append(selector)

    # Compound selector.
    if hist:
        res0 = None
    else:
        res0 = False
    def cselector (*a):
        res = res0
        for selector in selectors:
            res = selector(*a)
            if not res:
                return res
        return res

    return cselector


def negate_selector (selector):

    def negative_selector (*args):
        return not selector(*args)

    return negative_selector


# -----------------------------------------------------------------------------
# Caching for selectors.

_cache = {}

def cached_matcher (expr, cid):

    key = ("matcher", expr)
    if key not in _cache:
        _cache[key] = build_msg_fmatcher(expr, abort=True)

    return _cache[key]


def cached_users (user_spec, config, cid, utype=None):

    key = ("users", user_spec, config, utype)
    if key not in _cache:
        _cache[key] = parse_users(user_spec, config, cid)

    return _cache[key]


def cached_tags (tag_spec, config, cid):

    key = ("tags", tag_spec, config)
    if key not in _cache:
        _cache[key] = parse_tags(tag_spec, config, cid)

    return _cache[key]


# -----------------------------------------------------------------------------
# Selector factories.
# Use build_selector() to create selectors.

# NOTE:
# Plain selectors should return True or False.
# History selectors should return 1-based index into ascription history
# when the appropriate historical message is found, and 0 otherwise.
# In this way, when it is only necessary to test if a message is selected,
# returns from both types of selectors can be tested for simple falsity/truth,
# and non-zero integer return always indicates history selection.

def selector_any ():
    cid = "selector:any"

    def selector (msg, cat, history, config):

        return True

    return selector


def selector_active ():
    cid = "selector:active"

    def selector (msg, cat, history, config):

        return msg.translated and not msg.obsolete

    return selector


def selector_current ():
    cid = "selector:current"

    def selector (msg, cat, history, config):

        return not msg.obsolete

    return selector


def selector_branch (branch=None):
    cid = "selector:branch"

    if not branch:
        error("branch ID not given", subsrc=cid)
    branches = set(branch.split(","))

    def selector (msg, cat, history, config):

        return bool(branches.intersection(parse_summit_branches(msg)))

    return selector


def selector_unasc ():
    cid = "selector:unasc"

    def selector (msg, cat, history, config):

        # Do not consider pristine messages as unascribed.
        return history[0].user is None and has_tracked_parts(msg)

    return selector


def selector_fexpr (expr=None):
    cid = "selector:fexpr"

    if not (expr or "").strip():
        error("matching expression cannot be empty", subsrc=cid)

    def selector (msg, cat, history, config):

        matcher = cached_matcher(expr, cid)
        return bool(matcher(msg, cat))

    return selector


def selector_e (entry=None):
    cid = "selector:e"

    if not entry or not entry.isdigit():
        error("message reference by entry must be a positive integer",
              subsrc=cid)
    refentry = int(entry)

    def selector (msg, cat, history, config):

        return msg.refentry == refentry

    return selector


def selector_l (line=None):
    cid = "selector:l"

    if not line or not line.isdigit():
        error("message reference by line must be a positive integer",
              subsrc=cid)
    refline = int(line)

    def selector (msg, cat, history, config):

        return abs(msg.refline - refline) <= 1

    return selector


# Select messages between and including first and last reference by entry.
# If first entry is not given, all messages to the last entry are selected.
# If last entry is not given, all messages from the first entry are selected.
def selector_espan (first=None, last=None):
    cid = "selector:espan"

    if not first and not last:
        error("at least one of the first and last reference by entry "
              "must be given", subsrc=cid)
    if first and not first.isdigit():
        error("first message reference by entry must be a positive integer",
              subsrc=cid)
    if last and not last.isdigit():
        error("last message reference by entry must be a positive integer",
              subsrc=cid)
    first_entry = (first and [int(first)] or [None])[0]
    last_entry = (last and [int(last)] or [None])[0]

    def selector (msg, cat, history, config):

        if first_entry is not None and msg.refentry < first_entry:
            return False
        if last_entry is not None and msg.refentry > last_entry:
            return False
        return True

    return selector


# Select messages between and including first and last reference by line.
# If first line is not given, all messages to the last line are selected.
# If last line is not given, all messages from the first line are selected.
def selector_lspan (first=None, last=None):
    cid = "selector:lspan"

    if not first and not last:
        error("at least one of the first and last reference by line "
              "must be given", subsrc=cid)
    if first and not first.isdigit():
        error("first message reference by line must be a positive integer",
              subsrc=cid)
    if last and not last.isdigit():
        error("last message reference by line must be a positive integer",
              subsrc=cid)
    first_line = (first and [int(first)] or [None])[0]
    last_line = (last and [int(last)] or [None])[0]

    def selector (msg, cat, history, config):

        if first_line is not None and msg.refline < first_line:
            return False
        if last_line is not None and msg.refline > last_line:
            return False
        return True

    return selector


def selector_hexpr (expr=None, user_spec=None, addrem=None):
    cid = "selector:hexpr"

    if not (expr or "").strip():
        error("matching expression cannot be empty", subsrc=cid)

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        matcher = cached_matcher(expr, cid)
        users = cached_users(user_spec, config, cid)

        if not addrem:
            i = 0
        else:
            i = first_nfuzzy(history, 0)
            if i is None:
                return 0

        while i < len(history):
            a = history[i]
            if users and a.user not in users:
                i += 1
                continue

            if not addrem:
                amsg = a.msg
                i_next = i + 1
            else:
                i_next = first_nfuzzy(history, i + 1)
                if i_next is not None:
                    amsg2 = history[i_next].msg
                else:
                    amsg2 = MessageUnsafe(a.msg)
                    for field in _nonid_fields_tracked:
                        amsg2_value = amsg2.get(field)
                        if amsg2_value is None:
                            pass
                        elif isinstance(amsg2_value, basestring):
                            setattr(amsg2, field, None)
                        else:
                            amsg2_value = [u""] * len(amsg2_value)
                    i_next = len(history)
                amsg = MessageUnsafe(a.msg)
                msg_ediff(amsg2, amsg, emsg=amsg, addrem=addrem)

            if matcher(amsg, cat):
                return i + 1

            i = i_next

        return 0

    return selector


# Select last ascription (any, or by users).
def selector_asc (user_spec=None):
    cid = "selector:asc"

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        users = cached_users(user_spec, config, cid)

        hi_sel = 0
        for i in range(len(history)):
            a = history[i]
            if not users or a.user in users:
                hi_sel = i + 1
                break

        return hi_sel

    return selector


# Select last modification (any or by users).
def selector_mod (user_spec=None):
    cid = "selector:mod"

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        users = cached_users(user_spec, config, cid)

        hi_sel = 0
        for i in range(len(history)):
            a = history[i]
            if not a.user:
                continue
            if a.type == ATYPE_MOD and (not users or a.user in users):
                hi_sel = i + 1
                break

        return hi_sel

    return selector


# Select first modification (any or by m-users, and not by r-users)
# after last review (any or by r-users, and not by m-users).
def selector_modar (muser_spec=None, ruser_spec=None, atag_spec=None):
    cid = "selector:modar"

    return w_selector_modax(cid, False, True,
                            muser_spec, ruser_spec, atag_spec)


# Select first modification (any or by m-users, and not by mm-users)
# after last modification (any or by mm-users, and not by m-users).
def selector_modam (muser_spec=None, mmuser_spec=None):
    cid = "selector:modam"

    return w_selector_modax(cid, True, False,
                            muser_spec, mmuser_spec)


# Select first modification (any or by m-users, and not by rm-users)
# after last review or modification (any or by m-users, and not by rm-users).
def selector_modarm (muser_spec=None, rmuser_spec=None, atag_spec=None):
    cid = "selector:modarm"

    return w_selector_modax(cid, True, True,
                            muser_spec, rmuser_spec, atag_spec)


# Select first modification of translation
# (any or by m-users, and not by r-users)
# after last review (any or by r-users, and not by m-users).
def selector_tmodar (muser_spec=None, ruser_spec=None, atag_spec=None):
    cid = "selector:tmodar"

    return w_selector_modax(cid, False, True,
                            muser_spec, ruser_spec, atag_spec,
                            True)


# Worker for builders of *moda* selectors.
def w_selector_modax (cid, amod, arev,
                      muser_spec=None, rmuser_spec=None, atag_spec=None,
                      tronly=False):

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        musers = cached_users(muser_spec, config, cid, utype="m")
        rmusers = cached_users(rmuser_spec, config, cid, utype="rm")
        atags = cached_tags(atag_spec, config, cid)

        hi_sel = 0
        for i in range(len(history)):
            a = history[i]

            # Check if this message cancels further modifications.
            if (    (   (amod and a.type == ATYPE_MOD)
                     or (arev and a.type == ATYPE_REV and a.tag in atags))
                and (not rmusers or a.user in rmusers)
                and (not musers or a.user not in musers)
            ):
                break

            # Check if this message is admissible modification.
            if (    a.type == ATYPE_MOD
                and (not musers or a.user in musers)
                and (not rmusers or a.user not in rmusers)
            ):
                # Cannot be a candidate if:
                # - made by fuzzy user and there are only
                #   merge-induced differences from earlier message
                # - translation-only mode, and there is no difference
                #   in translation from earlier message
                ae = history[i + 1] if i + 1 < len(history) else None
                if (    not (    a.user == UFUZZ
                             and ae and merge_modified(ae.msg, a.msg))
                    and not (    tronly
                             and ae and ae.msg.msgstr == a.msg.msgstr)
                ):
                    hi_sel = i + 1

        return hi_sel

    return selector


# Select last review (any or by users).
def selector_rev (user_spec=None, atag_spec=None):
    cid = "selector:rev"

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        users = cached_users(user_spec, config, cid)
        atags = cached_tags(atag_spec, config, cid)

        hi_sel = 0
        for i in range(len(history)):
            a = history[i]
            if (    a.type == ATYPE_REV and a.tag in atags
                and (not users or a.user in users)
            ):
                hi_sel = i + 1
                break

        return hi_sel

    return selector


# Select first review (any or by r-users, and not by m-users)
# before last modification (any or by m-users, and not by r-users).
def selector_revbm (ruser_spec=None, muser_spec=None, atag_spec=None):
    cid = "selector:revbm"

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        rusers = cached_users(ruser_spec, config, cid, utype="r")
        musers = cached_users(muser_spec, config, cid, utype="m")
        atags = cached_tags(atag_spec, config, cid)

        hi_sel = 0
        can_select = False
        for i in range(len(history)):
            a = history[i]
            if (     a.type == ATYPE_MOD
                and (not musers or a.user in musers)
                and (not rusers or a.user not in rusers)
            ):
                # Modification found, enable selection of review.
                can_select = True
            if (    a.type == ATYPE_REV and a.tag in atags
                and (not rusers or a.user in rusers)
                and (not musers or a.user not in musers)
            ):
                # Review found, select it if enabled, and stop anyway.
                if can_select:
                    hi_sel = i + 1
                break

        return hi_sel

    return selector


# Select first modification (any or by users) at or after given time.
def selector_modafter (time_spec=None, user_spec=None):
    cid = "selector:modafter"

    if not time_spec:
        error("time specification cannot be empty", subsrc=cid)

    date = parse_datetime(time_spec)

    def selector (msg, cat, history, config):

        if history[0].user is None:
            return 0

        users = cached_users(user_spec, config, cid)

        hi_sel = 0
        for i in range(len(history) - 1, -1, -1):
            a = history[i]
            if (    a.type == ATYPE_MOD and (not users or a.user in users)
                and a.date >= date
            ):
                hi_sel = i + 1
                break

        return hi_sel

    return selector


xm_selector_factories = {
    # key: (function, can_be_used_as_history_selector)
    "any": (selector_any, False),
    "active": (selector_active, False),
    "current": (selector_current, False),
    "branch": (selector_branch, False),
    "unasc": (selector_unasc, False),
    "fexpr": (selector_fexpr, False),
    "e": (selector_e, False),
    "l": (selector_l, False),
    "espan": (selector_espan, False),
    "lspan": (selector_lspan, False),
    "hexpr": (selector_hexpr, True),
    "asc": (selector_asc, True),
    "mod": (selector_mod, True),
    "modar": (selector_modar, True),
    "modam": (selector_modam, True),
    "modarm": (selector_modarm, True),
    "tmodar": (selector_tmodar, True),
    "rev": (selector_rev, True),
    "revbm": (selector_revbm, True),
    "modafter": (selector_modafter, True),
}

# -----------------------------------------------------------------------------

_external_mods = {}

def collect_externals (xmod_path):

    # Load external module.
    try:
        xmod_file = open(xmod_path)
    except IOError:
        error("cannot load external module: %s" % xmod_path)
    # Load file into new module.
    xmod_name = "xmod_" + str(len(_external_mods))
    xmod = imp.new_module(xmod_name)
    exec xmod_file in xmod.__dict__
    xmod_file.close()
    _external_mods[xmod_name] = xmod # to avoid garbage collection

    # Collect everything collectable from the module.

    xms = []

    xms.append("xm_selector_factories")
    selector_factories = getattr(xmod, xms[-1], None)
    if selector_factories is not None:
        xm_selector_factories.update(selector_factories)

    # Warn of unknown externals.
    known_xms = set(xms)
    for xm in filter(lambda x: x.startswith("xm_"), dir(xmod)):
        if xm not in known_xms:
            warning("unknown external resource '%s' in module '%s'"
                    % (xm, xmod_path))


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()

