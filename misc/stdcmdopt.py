# -*- coding: UTF-8 -*-

"""
Standard command line options.

This module defines command lines options frequently used by various scripts,
in a form suitable for adding to their option lists.

All functions in this module take an C{optparse.OptionParser} instance,
possibly followed by some optional parameters, and return C{None}.

@author: Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
@license: GPLv3
"""


def add_cmdopt_incexc (opars, ormatch=False):
    """
    Regular expressions to include and exclude files and directories
    by matching names and paths.

    @param ormatch: whether multiple expressions are linked by OR operation
    @type ormatch: bool

    @see: L{build_path_selector()<misc.fsops.build_path_selector>}
    """

    if not ormatch:
        inclink = ("The option can be repeated, in which case a file "
                   "is included only if all expressions match it.")
        exclink = ("The option can be repeated, in which case a file "
                   "is excluded only if all expressions match it.")
    else:
        inclink = ("The option can be repeated, in which case a file "
                   "is included if at least one expression matches it.")
        exclink = ("The option can be repeated, in which case a file "
                   "is excluded if at least one expression matches it.")

    opars.add_option(
        "-e", "--exclude-name",
        metavar="REGEX",
        dest="exclude_names", action="append",
        help=("Exclude from processing files with names "
              "(base name without extension) "
              "matching given regular expression. %(sentence)s"
              % dict(sentence=exclink)))
    opars.add_option(
        "-E", "--exclude-path",
        metavar="REGEX",
        dest="exclude_paths", action="append",
        help=("Exclude from processing files with paths "
              "matching given regular expression. %(sentence)s"
              % dict(sentence=exclink)))
    opars.add_option(
        "-i", "--include-name",
        metavar="REGEX",
        dest="include_names", action="append",
        help=("Include into processing only files with names "
              "(base name without extension) "
              "matching given regular expression. %(sentence)s"
              % dict(sentence=inclink)))
    opars.add_option(
        "-I", "--include-path",
        metavar="REGEX",
        dest="include_paths", action="append",
        help=("Include into processing only files with paths "
              "matching given regular expression. %(sentence)s"
              % dict(sentence=inclink)))


def add_cmdopt_filesfrom (opars, cmnts=True, incexc=True):
    """
    File paths from which to collect list of file and directory paths.

    If C{cmnts} is set to C{True}, lines can be comments.
    If C{incexc} is set to C{True}, lines can be inclusion and exclusion
    directives.

    @param cmnts: whether to enable comments
    @type cmnts: bool
    @param incexc: whether to enable inclusion/exclusion regexes
    @type incexc: bool

    @see: L{collect_paths_from_file()<misc.fsops.collect_paths_from_file>}
    """

    shead = ("Collect paths of files and directories from given file, "
             "which contains one path per line. "
             "If a path is not absolute, it is considered relative "
             "to current working directory.")
    scmnts = ("Lines starting with '#' are treated as comments "
              "and skipped.")
    sincexc = ("Lines starting with ':' are treated as directives "
               "to include or exclude files/directories from processing, "
               "as follows: "
               ":-REGEX excludes by base name without extension; "
               ":/-REGEX excludes by full path; "
               ":+REGEX includes by base name without extension; "
               ":/+REGEX excludes by full path. "
               "If read directories are expanded into subpaths, "
               "these directives apply to those paths too.")
    stail = ("The option can be repeated to collect paths from several files.")

    vd = dict(head=shead, cmnts=scmnts, incexc=sincexc, tail=stail)
    if cmnts and incexc:
        help = ("%(head)s %(cmnts)s %(incexc)s %(tail)s" % vd)
    elif incexc:
        help = ("%(head)s %(incexc)s %(tail)s" % vd)
    elif cmnts:
        help = ("%(head)s %(cmnts)s %(tail)s" % vd)
    else:
        help = ("%(head)s %(tail)s" % vd)

    opars.add_option(
        "-f", "--files-from",
        metavar="FILE",
        dest="files_from", action="append",
        help=help)
