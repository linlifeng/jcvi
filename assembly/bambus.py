#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Driver script to run BAMBUS to scaffold contigs.
"""

import os.path as op
import sys

from optparse import OptionParser

from jcvi.apps.base import ActionDispatcher, debug, sh, need_update
debug()


def main():

    actions = (
        ('scaffold', 'run bambus on set of contigs, reads and read mappings'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def scaffold(args):
    """
    %prog scaffold ctgfasta reads1.fasta mapping1.bed
                            reads2.fasta mapping2.bed ...

    Run BAMBUS on set of contigs, reads and read mappings.
    """

    from jcvi.formats.base import FileMerger
    from jcvi.formats.bed import mates
    from jcvi.formats.contig import frombed
    from jcvi.formats.fasta import join
    from jcvi.utils.iter import grouper

    p = OptionParser(scaffold.__doc__)
    p.add_option("--conf", help="BAMBUS configuration file [default: %default]")
    p.add_option("--prefix", default=False, action="store_true",
            help="Only keep links between IDs with same prefix [default: %default]")
    opts, args = p.parse_args(args)

    nargs = len(args)
    if nargs < 3 or nargs % 2 != 1:
        sys.exit(not p.print_help())

    ctgfasta = args[0]
    duos = list(grouper(2, args[1:]))
    trios = []
    for fastafile, bedfile in duos:
        prefix = bedfile.rsplit(".", 1)[0]
        matefile = prefix + ".mates"
        matebedfile = matefile + ".bed"
        if need_update(bedfile, [matefile, matebedfile]):
            matesopt = [bedfile, "--lib", "--nointra"]
            if opts.prefix:
                matesopt += ["--prefix"]
            matefile, matebedfile = mates(matesopt)
        trios.append((fastafile, matebedfile, matefile))

    # Merge the readfasta, bedfile and matefile
    bbfasta, bbbed, bbmate = "bambus.reads.fasta", "bambus.bed", "bambus.mates"

    for files, outfile in zip(zip(*trios), (bbfasta, bbbed, bbmate)):
        FileMerger(files, outfile=outfile).merge(checkexists=True)

    ctgfile = "bambus.contig"
    idsfile = "bambus.ids"
    frombedInputs = [bbbed, ctgfasta, bbfasta]
    if need_update(frombedInputs, ctgfile):
        frombed(frombedInputs)

    inputfasta = "bambus.contigs.fasta"
    singletonfasta = "bambus.singletons.fasta"
    cmd = "faSomeRecords {0} {1} ".format(ctgfasta, idsfile)
    sh(cmd + inputfasta)
    sh(cmd + singletonfasta + " -exclude")

    # Run bambus
    prefix = "bambus"
    cmd = "goBambus -c {0} -m {1} -o {2}".format(ctgfile, bbmate, prefix)
    if opts.conf:
        cmd += " -C {0}".format(opts.conf)
    sh(cmd)

    cmd = "untangle -e {0}.evidence.xml -s {0}.out.xml -o {0}.untangle.xml".\
            format(prefix)
    sh(cmd)

    final = "final"
    cmd = "printScaff -e {0}.evidence.xml -s {0}.untangle.xml -l {0}.lib " \
          "-merge -detail -oo -sum -o {1}".format(prefix, final)
    sh(cmd)

    oofile = final + ".oo"
    join([inputfasta, "--oo={0}".format(oofile)])


if __name__ == '__main__':
    main()
