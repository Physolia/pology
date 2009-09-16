#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Sieve messages in collections of PO files.

Frequently there is a need to visit every message, either in a single catalog
or collection of catalogs, and perform some operations on each. The operations
may be checks, modifications, data collection, etc. This script is intended
to ease this process, both from users' point of view and that of preparing
new custom operations.

The bundle of operations to be performed on a message is called a "sieve".
C{posieve} does conceptually a simple thing: it runs each message in each
catalog given to it, through sieves that the user has specified.

Pology comes with many internal sieves, but users can write their own too.
For example, here is how one could run the internal L{C{stats}<sieve.stats>}
sieve, to collect statistics on all PO files in C{frobaz} directory::

    $ posieve stats frobaz/
    ... (after some time, a table with stats appears) ...

Assuming that C{frobaz} contains a lot of PO files, user would wait some time
until all the messages are pushed through the sieve, and then C{stats} would
present its findings in a table.

After the sieve name, any number of directory or file paths can be specified.
C{posieve} will consider file paths as catalog files to open, and search
recursively through directory paths for all files ending with C{.po} or C{.pot}.

Sieves need not only collect data (such as the C{stats} above) or do checks,
but may also modify messages. Whenever a message is modified, the catalog
with changes will be saved over old catalog, and the user will be informed
by an exclamation mark followed by the catalog path. An example of such
internal sieve is L{tag-untranslated<sieve.tag_untranslated>}, which will add
the C{untranslated} flag to each untranslated message::

    $ posieve tag-untranslated frobaz/
    ! frobaz/alfa.po
    ! frobaz/bravo.po
    ! frobaz/charlie.po
    Total untranslated messages tagged: 42

C{posieve} itself monitors and informs about changed catalogs, whereas the
final line in the example above has been output by the C{tag-untranslated}
sieve. Sieves will frequently issue such final reports.

More than one sieve can be applied to the catalog collection in one pass.
This is called the "sieve chain", and is specified as comma-separated list
of sieve names instead of a lone sieve name. Each message is passed through
the sieves in the given order, in a pipeline fashion -- the output from the
previous sieve is input to the next -- before moving to the next message.
This order is important to bear in mind when two sieves in the chain can both
modify a message. For example::

    $ posieve stats,tag-untranslated frobaz/
    ! frobaz/alfa.po
    ! frobaz/bravo.po
    ! frobaz/charlie.po
    ... (table with stats) ...
    Total untranslated messages tagged: 42

If the order were C{tag-untranslated,stats} in this case the effect on the
catalogs would be the same, but number of tagged messages would be the first
in output, followed by the table with statistics.

Frequently it is necessary to modify a message before doing a check on it,
such that an earlier sieve in the chain does the modification,
and a later sieve does the check.
If the modifications are performed only for the sake of the checks,
C{--no-sync} option can be used to prevent actually writing out
any modifications back to catalogs on disk.
This option is likewise useful for making "dry runs" when testing sieves.

C{posieve} takes a few options, which you can list with the usual C{--help}
option. However, more interesting is that sieves themselves can be sent some
I{parameters}, using the C{-s} option, which takes as argument a
C{parameter:value} pair. As many of these as needed can be given.
For example, C{stats} sieve could be instructed to take into account only
messages with at most 5 words, like this::

    $ posieve stats -s maxwords:5 frobaz/

Sieve parameters can also be switches, when only the parameter name is given.
C{stats} can be instructed to show statistics in greater detail like this::

    $ posieve stats -s detail frobaz/

In case a sieve chain is specified, sieve parameters are routed to sieves as they
will accept them. If two sieves in the chain have a same-named parameters, when
given on the command line it will be sent to both.

Pology also collects language-specific internal sieves. These are run by
prefixing sieve name with the language code and a colon. For example, there is
a sieve for the French language that replaces ordinary with non-breaking spaces
in some interpunction scenarios, the L{setUbsp<l10n.fr.sieve.setUbsp>},
which is invoked like this::

    $ posieve fr:setUbsp frobaz-fr/

In case the user has written a custom sieve, it can be run by simply stating
its path as sieve name. For C{posieve} to acknowledge it as external sieve,
the file name has to end in C{.py}. Custom sieves can be chained as any other. For example::

    $ posieve ../custom/my_count.py frobaz/
    $ posieve stats,../custom/my_count.py frobaz/

The list of all internal sieves is given within the L{sieve} module, as well
as instructions on how to write custom sieves. The list of internal language-specific sieves can be found within C{l10n.<lang>.sieve} module of
the languages that have them.

If an internal sieve contains underscores in its name, they can be replaced
with dashes in the C{posieve} command line. The dashes will be converted back
to underscores before trying to resolve the location of the internal sieve.

The following user configuration fields are considered
(they may be overridden by command line options):
  - C{[posieve]/wrap}: whether to wrap message fields (default C{yes})
  - C{[posieve]/fine-wrap}: whether to wrap message fields more finely
        (default C{yes})
  - C{[posieve]/skip-on-error}: whether to skip current catalog on
        processing error and go to next, if possible (default C{yes})
  - C{[posieve]/msgfmt-check}: whether to check catalog file by C{msgfmt -c}
        before sieving (default C{no})
  - C{[posieve]/skip-obsolete}: whether to avoid sending obsolete messages
        to sieves (default C{no})
  - C{[posieve]/use-psyco}: whether to use Psyco specializing compiler,
        if available (default C{yes})

@warning: This module is a script for end-use. No exposed functionality
should be considered public API, it is subject to change without notice.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""

import fallback_import_paths

import sys
import os
import imp
import locale
import re
from optparse import OptionParser
import glob

from pology.misc.fsops import str_to_unicode
from pology.misc.wrap import select_field_wrapper
from pology.misc.fsops import collect_catalogs, collect_system
from pology.file.catalog import Catalog
from pology.misc.report import error, warning, report, encwrite
from pology.misc.report import init_file_progress
from pology.misc.msgreport import report_on_msg, warning_on_msg, error_on_msg
import pology.misc.config as pology_config
from pology import rootdir
from pology.misc.subcmd import ParamParser
from pology.sieve import SieveMessageError, SieveCatalogError
from pology.misc.colors import set_coloring_globals


def main ():

    locale.setlocale(locale.LC_ALL, "")

    # Get defaults for command line options from global config.
    cfgsec = pology_config.section("posieve")
    def_do_wrap = cfgsec.boolean("wrap", True)
    def_do_fine_wrap = cfgsec.boolean("fine-wrap", True)
    def_do_skip = cfgsec.boolean("skip-on-error", True)
    def_msgfmt_check = cfgsec.boolean("msgfmt-check", False)
    def_skip_obsolete = cfgsec.boolean("skip-obsolete", False)
    def_use_psyco = cfgsec.boolean("use-psyco", True)

    # Setup options and parse the command line.
    usage = u"""
%prog [options] sieve [POPATHS...]
""".strip()
    description = u"""
Apply sieves to PO paths, which may be either single PO files or directories
to search recursively for PO files. Some of the sieves only examine PO files,
while other can modify them. The first non-option argument is the sieve name;
a list of several comma-separated sieves can be given too.
""".strip()
    version = u"""
%prog (Pology) experimental
Copyright © 2007 Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
""".strip()

    opars = OptionParser(usage=usage, description=description, version=version)
    opars.add_option(
        "-l", "--list-sieves",
        action="store_true", dest="list_sieves", default=False,
        help="list available internal sieves")
    opars.add_option(
        "-H", "--help-sieves",
        action="store_true", dest="help_sieves", default=False,
        help="show help for applied sieves")
    opars.add_option(
        "-f", "--files-from", metavar="FILE",
        action="append", dest="files_from", default=[],
        help="get list of input files from FILE, which contains one file path "
             "per line; can be repeated to collect paths from several files")
    opars.add_option(
        "-s", "--sieve-param", metavar="NAME[:VALUE]",
        action="append", dest="sieve_params", default=[],
        help="pass a parameter to sieves")
    opars.add_option(
        "--force-sync",
        action="store_true", dest="force_sync", default=False,
        help="force rewrite of all messages, modified or not")
    opars.add_option(
        "--no-sync",
        action="store_false", dest="do_sync", default=True,
        help="do not write any modifications to catalogs")
    opars.add_option(
        "--no-wrap",
        action="store_false", dest="do_wrap", default=def_do_wrap,
        help="no basic wrapping (on column)")
    opars.add_option(
        "--no-fine-wrap",
        action="store_false", dest="do_fine_wrap", default=def_do_fine_wrap,
        help="no fine wrapping (on markup tags, etc.)")
    opars.add_option(
        "--no-psyco",
        action="store_false", dest="use_psyco", default=def_use_psyco,
        help="do not try to use Psyco specializing compiler")
    opars.add_option(
        "--no-skip",
        action="store_false", dest="do_skip", default=def_do_skip,
        help="do not try to skip catalogs which signal errors")
    opars.add_option(
        "-b", "--skip-obsolete",
        action="store_true", dest="skip_obsolete", default=def_skip_obsolete,
        help="do not sieve obsolete messages")
    opars.add_option(
        "-m", "--output-modified", metavar="FILE",
        action="store", dest="output_modified", default=None,
        help="output names of modified files into FILE")
    opars.add_option(
        "-c", "--msgfmt-check",
        action="store_true", dest="msgfmt_check", default=def_msgfmt_check,
        help="check catalogs by msgfmt and skip those which do not pass")
    opars.add_option(
        "-e", "--exclude-cat", metavar="REGEX",
        dest="exclude_cat",
        help="do not sieve files when their catalog name (file basename "
             "without .po* extension) matches the regular expression")
    opars.add_option(
        "-E", "--exclude-path", metavar="REGEX",
        dest="exclude_path",
        help="do not sieve files when their full path matches "
             "the regular expression")
    opars.add_option(
        "-i", "--include-cat", metavar="REGEX",
        dest="include_cat",
        help="sieve files only when their catalog name (file basename "
             "without .po* extension) matches the regular expression")
    opars.add_option(
        "-I", "--include-path", metavar="REGEX",
        dest="include_path",
        help="sieve files only when their full path matches "
             "the regular expression")
    opars.add_option(
        "-a", "--announce-entry",
        action="store_true", dest="announce_entry", default=False,
        help="announce that header or message is just about to be sieved")
    opars.add_option(
        "-v", "--verbose",
        action="store_true", dest="verbose", default=False,
        help="output more detailed progress info")
    opars.add_option(
        "-R", "--raw-colors",
        action="store_true", dest="raw_colors", default=False,
        help="coloring independent of output destination (terminal, file)")

    (op, free_args) = opars.parse_args(str_to_unicode(sys.argv[1:]))

    if len(free_args) < 1 and not op.list_sieves:
        opars.error("must provide sieve to apply")

    op.raw_sieves = []
    op.raw_paths = []
    if len(free_args) >= 1:
        op.raw_sieves = free_args[0]
        op.raw_paths = free_args[1:]

    # Could use some speedup.
    if op.use_psyco:
        try:
            import psyco
            psyco.full()
        except ImportError:
            pass

    if op.raw_colors:
        set_coloring_globals(outdep=False)

    # Parse sieve options.
    # FIXME: Temporary, until all sieves are switched to new style.
    class _Sieve_options (dict):
        def __init__ (self):
            self._accepted = []
        def accept (self, opt):
            # Sieves should call this method on each accepted option.
            self._accepted.append(opt)
        def unaccepted (self):
            noadm = {}
            for opt, val in dict.items(self):
                if not opt in self._accepted:
                    noadm[opt] = val
            return noadm
    sopts = _Sieve_options()
    for swspec in op.sieve_params:
        if swspec.find(":") >= 0:
            sopt, value = swspec.split(":", 1)
        else:
            sopt = swspec
            value = ""
        sopts[sopt] = value

    # Dummy-set all internal sieves as requested if sieve listing required.
    sieves_requested = []
    if op.list_sieves:
        # Global sieves.
        modpaths = glob.glob(os.path.join(rootdir(), "sieve", "[a-z]*.py"))
        modpaths.sort()
        for modpath in modpaths:
            sname = os.path.basename(modpath)[:-3] # minus .py
            sname = sname.replace("_", "-")
            sieves_requested.append(sname)
        # Language-specific sieves.
        modpaths = glob.glob(os.path.join(rootdir(),
                                          "l10n", "*", "sieve", "[a-z]*.py"))
        modpaths.sort()
        for modpath in modpaths:
            sname = os.path.basename(modpath)[:-3] # minus .py
            sname = sname.replace("_", "-")
            lang = os.path.basename(os.path.dirname(os.path.dirname(modpath)))
            sieves_requested.append(lang + ":" + sname)

    # Load sieve modules from supplied names in the command line.
    if not sieves_requested:
        sieves_requested = op.raw_sieves.split(",")
    sieve_modules = []
    for sieve_name in sieves_requested:
        # Resolve sieve file.
        if not sieve_name.endswith(".py"):
            # One of internal sieves.
            if ":" in sieve_name:
                # Language-specific internal sieve.
                lang, name = sieve_name.split(":")
                sieve_path_base = os.path.join("l10n", lang, "sieve", name)
            else:
                sieve_path_base = os.path.join("sieve", sieve_name)
            sieve_path_base = sieve_path_base.replace("-", "_") + ".py"
            sieve_path = os.path.join(rootdir(), sieve_path_base)
        else:
            # Sieve name is its path.
            sieve_path = sieve_name
        try:
            sieve_file = open(sieve_path)
        except IOError:
            error("cannot load sieve: %s" % sieve_path)
        # Load file into new module.
        sieve_mod_name = "sieve_" + str(len(sieve_modules))
        sieve_mod = imp.new_module(sieve_mod_name)
        exec sieve_file in sieve_mod.__dict__
        sieve_file.close()
        sys.modules[sieve_mod_name] = sieve_mod # to avoid garbage collection
        sieve_modules.append((sieve_name, sieve_mod))
        if not hasattr(sieve_mod, "Sieve"):
            error("module does not define Sieve class: %s" % sieve_path)
        # FIXME: Check that module has setup_sieve function,
        # once all sieves are switched to new style.

    # Define and parse sieve parameters.
    pp = ParamParser()
    snames = []
    for name, mod in sieve_modules:
        # FIXME: Remove when all sieves are switched to new style.
        if not hasattr(mod, "setup_sieve"):
            continue
        try:
            scview = pp.add_subcmd(name)
        except Exception, e:
            error(unicode(e))
        if hasattr(mod, "setup_sieve"):
            mod.setup_sieve(scview)
        snames.append(name)
    if op.list_sieves:
        report("Available internal sieves:")
        report(pp.listcmd(snames))
        sys.exit(0)
    if op.help_sieves:
        report("Help for sieves:")
        report("")
        report(pp.help(snames))
        sys.exit(0)
    try:
        sparams, nacc_params = pp.parse(op.sieve_params, snames)
    except Exception, e:
        error(unicode(e))
    # FIXME: Really abort when all sieves are switched to new style.
    #if nacc_params:
        #error("parameters not expected by any of the issued subcommands: %s"
              #% (" ".join(nacc_params)))

    # Assemble list of paths to be searched for catalogs.
    file_or_dir_paths = op.raw_paths
    if op.files_from:
        for paths_file in op.files_from:
            flines = open(paths_file, "r").readlines()
            for fline in flines:
                fline = fline.rstrip("\n")
                if fline:
                    file_or_dir_paths.append(fline)
    elif not file_or_dir_paths:
        file_or_dir_paths = ["."]

    # Prepare exclusion-inclusion test.
    exclude_cat_rx = None
    if op.exclude_cat:
        exclude_cat_rx = re.compile(op.exclude_cat, re.I|re.U)
    exclude_path_rx = None
    if op.exclude_path:
        exclude_path_rx = re.compile(op.exclude_path, re.I|re.U)
    include_cat_rx = None
    if op.include_cat:
        include_cat_rx = re.compile(op.include_cat, re.I|re.U)
    include_path_rx = None
    if op.include_path:
        include_path_rx = re.compile(op.include_path, re.I|re.U)

    def is_cat_included (fname):
        # Construct catalog name by stripping final .po* from file basename.
        cname = os.path.basename(fname)
        p = cname.rfind(".po")
        if p > 0:
            cname = cname[:p]
        included = True
        if included and exclude_cat_rx:
            included = exclude_cat_rx.search(cname) is None
        if included and exclude_path_rx:
            included = exclude_path_rx.search(fname) is None
        if included and include_cat_rx:
            included = include_cat_rx.search(cname) is not None
        if included and include_path_rx:
            included = include_path_rx.search(fname) is not None
        return included

    # Add as special parameter to each sieve:
    # - paths from which the catalogs are collected
    # - whether destination independent coloring is in effect
    # FIXME: Think of something less ugly.
    for p in sparams.values():
        p.root_paths = file_or_dir_paths[:]
        p.raw_colors = op.raw_colors
        p.is_cat_included = is_cat_included

    # Create sieves.
    sieves = []
    for name, mod in sieve_modules:
        try:
            # FIXME: Remove when all sieves are switched to new style.
            if not hasattr(mod, "setup_sieve"):
                sieves.append(mod.Sieve(sopts))
                continue
            sieves.append(mod.Sieve(sparams[name]))
        except Exception, e:
            error(unicode(e))

    # Old-style sieves will have marked options that they have accepted.
    # FIXME: Remove when all sieves are switched to new style.
    all_nacc_params = set(sopts.unaccepted().keys())
    all_nacc_params = all_nacc_params.intersection(set(nacc_params))
    if all_nacc_params:
        error("no sieve has accepted these parameters: %s"
              % ", ".join(all_nacc_params))

    # Get the message monitoring indicator from the sieves.
    # Monitor unless all sieves have requested otherwise.
    use_monitored = False
    for sieve in sieves:
        if getattr(sieve, "caller_monitored", True):
            use_monitored = True
            break
    if op.verbose and not use_monitored:
        report("--> Not monitoring messages")

    # Get the sync indicator from the sieves.
    # Sync unless all sieves have requested otherwise,
    # and unless syncing is disabled globally in command line.
    do_sync = False
    for sieve in sieves:
        if getattr(sieve, "caller_sync", True):
            do_sync = True
            break
    if not op.do_sync:
        do_sync = False
    if op.verbose and not do_sync:
        report("--> Not syncing after sieving")

    # Open in header-only mode if no sieve has message processor.
    # Categorize sieves by the presence of message/header processors.
    use_headonly = True
    header_sieves = []
    message_sieves = []
    for sieve in sieves:
        if hasattr(sieve, "process"):
            use_headonly = False
            message_sieves.append(sieve)
        if hasattr(sieve, "process_header"):
            header_sieves.append(sieve)
    if op.verbose and use_headonly:
        report("--> Opening catalogs in header-only mode")

    # Collect catalog paths.
    fnames = collect_catalogs(file_or_dir_paths)

    # Decide on wrapping policy for modified messages.
    wrap_func = select_field_wrapper(basic=op.do_wrap, fine=op.do_fine_wrap)

    if op.do_skip:
        errwarn = warning
        errwarn_on_msg = warning_on_msg
    else:
        errwarn = error
        errwarn_on_msg = error_on_msg

    # Eliminate or include specific catalogs.
    fnames_mod = []
    for fname in fnames:
        if not is_cat_included(fname):
            if op.verbose:
                report("skipping on request: %s" % fname)
        else:
            fnames_mod.append(fname)
    fnames = fnames_mod

    # Prepare inline progress indicator.
    update_progress = init_file_progress(fnames)

    # Sieve catalogs.
    modified_files = []
    for fname in fnames:
        if op.verbose:
            report("sieving %s ..." % fname)
        else:
            update_progress(fname)

        if op.msgfmt_check:
            # TODO: Make it more portable?
            d1, oerr, ret = collect_system("msgfmt -o/dev/null -c %s" % fname)
            if ret != 0:
                oerr = oerr.strip()
                errwarn(u"%s: msgfmt check failed:\n"
                        u"%s" % (fname, oerr))
                warning(u"skipping catalog due to check failure")
                continue

        try:
            cat = Catalog(fname, monitored=use_monitored, wrapf=wrap_func,
                          headonly=use_headonly)
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception, e:
            errwarn(u"%s: parsing failed: %s" % (fname, e))
            warning(u"skipping catalog due to parsing failure")
            continue

        skip = False
        # First run all header sieves.
        if header_sieves and op.announce_entry:
            report(u"sieving %s:header ..." % fname)
        for sieve in header_sieves:
            try:
                sieve.process_header(cat.header, cat)
            except SieveCatalogError, e:
                errwarn(u"%s:header: sieving failed: %s" % (fname, e))
                skip = True
                break
            except Exception, e:
                error(u"%s:header: sieving failed: %s" % (fname, e))
        if skip:
            warning(u"skipping catalog due to header sieving failure")
            continue
        # Then run all message sieves on each message,
        # unless processing only the header.
        if not use_headonly:
            for msg in cat:
                if op.skip_obsolete and msg.obsolete:
                    continue

                update_progress(fname)

                if op.announce_entry:
                    report(u"sieving %s:%d(#%d) ..."
                           % (fname, msg.refline, msg.refentry))

                for sieve in message_sieves:
                    try:
                        sieve.process(msg, cat)
                    except SieveMessageError, e:
                        errwarn_on_msg(u"sieving failed: %s" % e, msg, cat)
                        break
                    except SieveCatalogError, e:
                        errwarn_on_msg(u"sieving failed: %s" % e, msg, cat)
                        skip = True
                        break
                    except Exception, e:
                        error_on_msg(u"sieving failed: %s" % e, msg, cat)
                if skip:
                    break
        if skip:
            warning(u"skipping catalog due to message sieving failure")
            continue

        if do_sync and cat.sync(op.force_sync):
            if op.verbose:
                report("! (MODIFIED) %s" % fname)
            else:
                report("! %s" % fname)
            modified_files.append(fname)

    update_progress() # clear last progress line, if any

    for sieve in sieves:
        if hasattr(sieve, "finalize"):
            try:
                sieve.finalize()
            except Exception, e:
                warning(u"finalization failed: %s" % e)

    if op.output_modified:
        ofh = open(op.output_modified, "w")
        ofh.write("\n".join(modified_files) + "\n")
        ofh.close


if __name__ == '__main__':
    main()
