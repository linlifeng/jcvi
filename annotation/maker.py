#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Utility script for annotations based on MAKER.
"""

import os.path as op
import sys

from optparse import OptionParser

from jcvi.apps.base import ActionDispatcher, debug
debug()


def main():

    actions = (
        ('datastore', 'generate a list of gff filenames to merge'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def datastore(args):
    """
    %prog datastore datastore.log > gfflist.log

    Generate a list of gff filenames to merge. The `datastore.log` file can be
    generated by something like:

    $ find
    /usr/local/scratch/htang/EVM_test/gannotation/maker/1132350111853_default/i1/
    -maxdepth 4 -name "*datastore*.log" > datastore.log
    """
    p = OptionParser(datastore.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    ds, = args
    fp = open(ds)
    for row in fp:
        fn = row.strip()
        assert op.exists(fn)
        pp, logfile = op.split(fn)
        flog = open(fn)
        for row in flog:
            ctg, folder, status = row.split()
            if status != "FINISHED":
                continue

            gff_file = op.join(pp, folder, ctg + ".gff")
            assert op.exists(gff_file)
            print gff_file


if __name__ == '__main__':
    main()