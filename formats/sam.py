"""
SAM alignment format. There are other tools that handles better SAM and BAM.
This script simply parses the lines in SAM into human readable fields.

http://samtools.sourceforge.net/SAM1.pdf
"""

import os.path as op
import sys
import logging

from itertools import groupby
from optparse import OptionParser

from Bio import SeqIO
from jcvi.formats.base import LineFile
from jcvi.formats.fasta import Fasta
from jcvi.utils.cbook import fill
from jcvi.assembly.base import Astat
from jcvi.apps.base import ActionDispatcher, need_update, sh, debug, set_outfile
debug()


class SamLine (object):

    def __init__(self, row):

        args = row.strip().split("\t")
        self.qname = args[0]
        self.flag = args[1]
        self.rname = args[2]
        self.pos = args[3]
        self.mapq = args[4]
        self.cigar = args[5]
        self.mrnm = args[6]
        self.mpos = args[7]
        self.isize = args[8]
        self.seq = args[9]
        self.qual = args[10]

    @property
    def pairline(self):
        qpos = self.cigar.split('H', 1)[0]
        return "%s:%s\t%s:%s" % (self.qname, qpos, self.rname, self.pos)


class Sam (LineFile):

    def __init__(self, filename, callback=None):

        fp = open(filename)
        for row in fp:
            if row[0] == '@':
                continue
            s = SamLine(row)
            if callback:
                callback(s)


def main():

    actions = (
        ('pair', 'parse sam file and get pairs'),
        ('chimera', 'parse sam file from `bwasw` and list multi-hit reads'),
        ('ace', 'convert sam file to ace'),
        ('index', 'convert to bam, sort and then index'),
        ('bcf', 'run mpileup on a set of bam files'),
            )

    p = ActionDispatcher(actions)
    p.dispatch(globals())


def bcf(args):
    """
    %prog bcf fastafile bamfiles > bcffile

    Run mpileup on bam files.
    """
    from jcvi.apps.grid import Jobs

    p = OptionParser(bcf.__doc__)
    set_outfile(p)
    opts, args = p.parse_args(args)

    if len(args) < 2:
        sys.exit(not p.print_help())

    fastafile = args[0]
    bamfiles = args[1:]

    unsorted = [x for x in bamfiles if ".sorted." not in x]
    jargs = [[[x, "--unique"]] for x in unsorted]
    jobs = Jobs(index, args=jargs)
    jobs.run()

    bamfiles = [x.replace(".sorted.bam", ".bam") for x in bamfiles]
    bamfiles = [x.replace(".bam", ".sorted.bam") for x in bamfiles]
    cmd = "samtools mpileup -P ILLUMINA -E -ugDf"
    cmd += " {0} {1}".format(fastafile, " ".join(bamfiles))
    cmd += " | bcftools view -bcvg -"
    sh(cmd, outfile=opts.outfile)


def chimera(args):
    """
    %prog index samfile

    Parse SAM file from `bwasw` and list multi-hit reads.
    """
    p = OptionParser(chimera.__doc__)
    opts, args = p.parse_args(args)
    if len(args) != 1:
        sys.exit(p.print_help())

    samfile, = args
    fp = open(samfile)

    def key_fun(x):
        if x[0] == '@':
            return x[0]
        s = SamLine(x)
        return s.qname, s.rname

    for read, samlines in groupby(fp, key=key_fun):
        nlines = len(list(samlines))
        if nlines == 1:
            continue
        print read


def index(args):
    """
    %prog index samfile/bamfile

    If SAM file, convert to BAM, sort and then index, using SAMTOOLS
    """
    p = OptionParser(index.__doc__)
    p.add_option("--fasta", dest="fasta", default=None,
            help="add @SQ header to the BAM file [default: %default]")
    p.add_option("--unique", default=False, action="store_true",
            help="only retain uniquely mapped reads [default: %default]")

    opts, args = p.parse_args(args)
    if len(args) != 1:
        sys.exit(p.print_help())

    samfile, = args
    fastafile = opts.fasta
    if fastafile:
        assert op.exists(fastafile)

    bamfile = samfile.replace(".sam", ".bam")
    if fastafile:
        faifile = fastafile + ".fai"
        if need_update(fastafile, faifile):
            sh("samtools faidx {0}".format(fastafile))
        cmd = "samtools view -bt {0} {1} -F 4 -o {2}".\
                format(faifile, samfile, bamfile)
    else:
        cmd = "samtools view -bS {0} -F 4 -o {1}".\
                format(samfile, bamfile)

    if opts.unique:
        cmd += " -q 1"

    if samfile.endswith(".sam"):
        sh(cmd)

    # Already sorted?
    if bamfile.endswith(".sorted.bam"):
        sortedbamfile = bamfile
    else:
        prefix = bamfile.replace(".bam", "")
        sortedbamfile = prefix + ".sorted.bam"

    if need_update(bamfile, sortedbamfile):
        sh("samtools sort {0} {1}.sorted".format(bamfile, prefix))

    baifile = sortedbamfile + ".bai"
    if need_update(sortedbamfile, baifile):
        sh("samtools index {0}".format(sortedbamfile))

    return sortedbamfile


def pair(args):
    """
    %prog pair samfile

    Parses the sam file and retrieve in pairs format,
    query:pos ref:pos
    """
    p = OptionParser(pair.__doc__)

    opts, args = p.parse_args(args)
    if len(args) != 1:
        sys.exit(p.print_help())

    def callback(s):
        print s.pairline
    sam = Sam(args[0], callback=callback)


def cigar_to_seq(a, gap='*'):
    """
    Accepts a pysam row.

    cigar alignment is presented as a list of tuples (operation,length). For
    example, the tuple [ (0,3), (1,5), (0,2) ] refers to an alignment with 3
    matches, 5 insertions and another 2 matches.

    Op BAM Description
    M 0 alignment match (can be a sequence match or mismatch)
    I 1 insertion to the reference
    D 2 deletion from the reference
    N 3 skipped region from the reference
    S 4 soft clipping (clipped sequences present in SEQ)
    H 5 hard clipping (clipped sequences NOT present in SEQ)
    P 6 padding (silent deletion from padded reference)
    = 7 sequence match
    X 8 sequence mismatch

    convert the sequence based on the cigar string. For example:
    """
    seq, cigar = a.seq, a.cigar
    start = 0
    subseqs = []
    npadded = 0
    if cigar is None:
        return None, npadded

    for operation, length in cigar:
        end = start if operation == 2 else start + length

        if operation == 0:  # match
            subseq = seq[start:end]
        elif operation == 1:  # insertion
            subseq = ""
        elif operation == 2:  # deletion
            subseq = gap * length
            npadded += length
        elif operation == 3:  # skipped
            subseq = 'N' * length
        elif operation in (4, 5):  # clip
            subseq = ""
        else:
            raise NotImplementedError

        subseqs.append(subseq)
        start = end

    return "".join(subseqs), npadded


def ace(args):
    """
    %prog ace bamfile fastafile

    convert bam format to ace format. This often allows the remapping to be
    assessed as a denovo assembly format. bam file needs to be indexed. also
    creates a .mates file to be used in amos/bambus, and .astat file to mark
    whether the contig is unique or repetitive based on A-statistics in Celera
    assembler.
    """
    p = OptionParser(ace.__doc__)
    p.add_option("--splitdir", dest="splitdir", default="outRoot",
            help="split the ace per contig to dir [default: %default]")
    p.add_option("--unpaired", dest="unpaired", default=False,
            help="remove read pairs on the same contig [default: %default]")
    p.add_option("--minreadno", dest="minreadno", default=3, type="int",
            help="minimum read numbers per contig [default: %default]")
    p.add_option("--minctgsize", dest="minctgsize", default=100, type="int",
            help="minimum contig size per contig [default: %default]")
    p.add_option("--astat", default=False, action="store_true",
            help="create .astat to list repetitiveness [default: %default]")
    p.add_option("--readids", default=False, action="store_true",
            help="create file of mapped and unmapped ids [default: %default]")

    from pysam import Samfile

    opts, args = p.parse_args(args)
    unpaired = opts.unpaired
    minreadno = opts.minreadno
    minctgsize = opts.minctgsize

    if len(args) != 2:
        sys.exit(not p.print_help())

    bamfile, fastafile = args
    astat = opts.astat
    readids = opts.readids

    f = Fasta(fastafile)
    prefix = bamfile.split(".")[0]
    acefile = prefix + ".ace"
    readsfile = prefix + ".reads"
    astatfile = prefix + ".astat"

    logging.debug("Load {0}".format(bamfile))
    s = Samfile(bamfile, "rb")

    ncontigs = s.nreferences
    genomesize = sum(x for a, x in f.itersizes())
    logging.debug("Total {0} contigs with size {1} base".format(ncontigs,
        genomesize))
    qual = "20"  # default qual

    totalreads = sum(s.count(x) for x in s.references)
    logging.debug("Total {0} reads mapped".format(totalreads))

    fw = open(acefile, "w")
    if astat:
        astatfw = open(astatfile, "w")
    if readids:
        readsfw = open(readsfile, "w")

    print >> fw, "AS {0} {1}".format(ncontigs, totalreads)
    print >> fw

    for i, contig in enumerate(s.references):
        cseq = f[contig]
        nbases = len(cseq)

        mapped_reads = [x for x in s.fetch(contig) if not x.is_unmapped]
        nreads = len(mapped_reads)

        nsegments = 0
        print >> fw, "CO {0} {1} {2} {3} U".format(contig, nbases, nreads,
                nsegments)
        print >> fw, fill(str(cseq.seq))
        print >> fw

        if astat:
            astat = Astat(nbases, nreads, genomesize, totalreads)
            print >> astatfw, "{0}\t{1:.1f}".format(contig, astat)

        text = fill([qual] * nbases, delimiter=" ", width=30)
        print >> fw, "BQ\n{0}".format(text)
        print >> fw

        rnames = []
        for a in mapped_reads:
            readname = a.qname
            rname = readname

            if readids:
                print >> readsfw, readname
            rnames.append(rname)

            strand = "C" if a.is_reverse else "U"
            paddedstart = a.pos + 1  # 0-based to 1-based
            af = "AF {0} {1} {2}".format(rname, strand, paddedstart)
            print >> fw, af

        print >> fw

        for a, rname in zip(mapped_reads, rnames):
            aseq, npadded = cigar_to_seq(a)
            if aseq is None:
                continue

            ninfos = 0
            ntags = 0
            alen = len(aseq)
            rd = "RD {0} {1} {2} {3}\n{4}".format(rname, alen, ninfos, ntags,
                    fill(aseq))
            qs = "QA 1 {0} 1 {0}".format(alen)

            print >> fw, rd
            print >> fw
            print >> fw, qs
            print >> fw


if __name__ == '__main__':
    main()
