"""
Wrapper for biopython Fasta, add option to parse sequence headers
"""

import sys
import os
import os.path as op
import shutil
import logging
import string

from random import sample
from optparse import OptionParser
from itertools import groupby, izip_longest

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from jcvi.formats.base import BaseFile, DictFile, must_open
from jcvi.utils.table import banner
from jcvi.apps.base import ActionDispatcher, debug, set_outfile, sh
from jcvi.apps.console import red, green
debug()


class Fasta (BaseFile, dict):

    def __init__(self, filename, index=True, key_function=None, lazy=False):
        super(Fasta, self).__init__(filename)
        self.key_function = key_function

        if lazy:  # do not incur the overhead
            return

        if index:
            self.index = SeqIO.index(filename, "fasta",
                    key_function=key_function)
        else:
            # SeqIO.to_dict expects a different key_function that operates on
            # the SeqRecord instead of the raw string
            _key_function = (lambda rec: key_function(rec.description)) if \
                    key_function else None
            self.index = SeqIO.to_dict(SeqIO.parse(must_open(filename), "fasta"),
                    key_function=_key_function)

    def _key_function(self, key):
        return self.key_function(key) if self.key_function else key

    def __len__(self):
        return len(self.index)

    def __contains__(self, key):
        key = self._key_function(key)
        return key in self.index

    def __getitem__(self, key):
        key = self._key_function(key)
        rec = self.index[key]
        return rec

    def keys(self):
        return self.index.keys()

    def iterkeys(self):
        for k in self.index.iterkeys():
            yield k

    def iteritems(self):
        for k in self.iterkeys():
            yield k, self[k]

    def itersizes(self):
        for k in self.iterkeys():
            yield k, len(self[k])

    def iteritems_ordered(self):
        for rec in SeqIO.parse(must_open(self.filename), "fasta"):
            yield rec.name, rec

    def iterdescriptions_ordered(self):
        for k, rec in self.iteritems_ordered():
            yield rec.description, rec

    def iterkeys_ordered(self):
        for k, rec in self.iteritems_ordered():
            yield k

    def itersizes_ordered(self):
        for k, rec in self.iteritems_ordered():
            yield k, len(rec)

    @property
    def totalsize(self):
        return sum(size for k, size in self.itersizes())

    @classmethod
    def subseq(cls, fasta, start=None, stop=None, strand=None):
        """
        Take Bio.SeqRecord and slice "start:stop" from it, does proper
        index and error handling
        """
        start = start - 1 if start is not None else 0
        stop = stop if stop is not None else len(fasta)

        assert start >= 0, "start (%d) must > 0" % (start + 1)

        assert stop <= len(fasta), \
                ("stop (%d) must be <= " + \
                "length of `%s` (%d)") % (stop, fasta.id, len(fasta))

        seq = fasta.seq[start:stop]

        if strand in (-1, '-1', '-'):
            seq = seq.reverse_complement()

        return seq

    def sequence(self, f, asstring=True):
        """
        Emulate brentp's pyfasta/fasta.py sequence() methods

        take a feature and use the start/stop or exon_keys to return
        the sequence from the assocatied fasta file:

        f: a feature
        asstring: if true, return the sequence as a string
                : if false, return as a biopython Seq

        >>> f = Fasta('tests/data/three_chrs.fasta')
        >>> f.sequence({'start':1, 'stop':2, 'strand':1, 'chr': 'chr1'})
        'AC'

        >>> f.sequence({'start':1, 'stop':2, 'strand': -1, 'chr': 'chr1'})
        'GT'
        """

        assert 'chr' in f, "`chr` field required"
        name = f['chr']

        assert name in self, "feature: %s not in `%s`" % \
                (f, self.filename)

        fasta = self[f['chr']]

        seq = Fasta.subseq(fasta,
                f.get('start'), f.get('stop'), f.get('strand'))

        if asstring:
            return str(seq)

        return seq


"""
Class derived from https://gist.github.com/933737
Original code written by David Winter (https://github.com/dwinter)

Code writted to answer this challenge at Biostar:
http://biostar.stackexchange.com/questions/5902/

(Code includes improvements from Brad Chapman)
"""
class ORFFinder:
    """Find the longest ORF in a given sequence
    "seq" is a string, if "start" is not provided any codon can be the start of
    and ORF. If muliple ORFs have the longest length the first one encountered
    is printed
    """
    def __init__(self, seq, start=[], stop=["TAG", "TAA", "TGA"]):
        self.seq = seq.tostring()
        self.start = start
        self.stop = stop
        self.result = ("+",0,0,0,0)
        self.longest = 0
        self.sequence = ""

    def _print_current(self):
        print "frame %s%s position %s:%s (%s nucleotides)" % self.result

    def reverse_comp(self, seq):
        swap = {"A":"T", "T":"A", "C":"G", "G":"C", "N":"N"}
        return "".join(swap[b] for b in seq)

    def codons(self, frame):
        """ A generator that yields DNA in one codon blocks
        "frame" counts for 0. This function yelids a tuple (triplet, index) with
        index relative to the original DNA sequence
        """
        start = frame
        while start + 3 <= len(self.sequence):
            yield (self.sequence[start:start+3], start)
            start += 3

    def scan_sequence(self, frame, direction):
        """ Search in one reading frame """
        orf_start = None
        for c, index in self.codons(frame):
            if (c not in self.stop and (c in self.start or not self.start)
                and orf_start is None):
                orf_start = index + 1       # return the result as 1-indexed
            elif c in self.stop and orf_start:
                self._update_longest(orf_start, index, direction, frame)
                orf_start = None
        if orf_start:
            self._update_longest(orf_start, index, direction, frame)

    def _update_longest(self, orf_start, index, direction, frame):
        orf_end = index + 3                 # index is relative to start of codons
        L = (orf_end - orf_start) + 1
        if L > self.longest:
            self.longest = L
            self.result = (direction, frame, orf_start, orf_end, L)

    def run_sixframe(self):
        dirs = ["+", "-"]
        for direction in dirs:
            self.sequence = self.seq
            if direction == "-":
                self.sequence = self.reverse_comp(self.sequence)
            for frame in xrange(3):
                self.scan_sequence(frame, direction)

    def get_longest_orf(self):
        self.run_sixframe()                 # run six frame translation

        self.sequence = self.seq
        peplen = len(self.sequence) / 3
        if(self.result[0] == "-"):
            self.sequence = self.reverse_comp(self.seq)

        orf = self.sequence[self.result[1] : self.result[1] + peplen * 3]
        orf = orf[self.result[2] - self.result[1] - 1 : self.result[3]]
        return orf


def longest_orf(seq):
    orf = ORFFinder(seq).get_longest_orf()
    return orf


def rc(s):
    _complement = string.maketrans('ATCGatcgNnXx', 'TAGCtagcNnXx')
    cs = s.translate(_complement)
    return cs[::-1]


def main():

    actions = (
        ('extract', 'given fasta file and seq id, retrieve the sequence ' + \
                    'in fasta format'),
        ('translate', 'translate CDS to proteins'),
        ('summary', "report the real no of bases and N's in fastafiles"),
        ('uniq', 'remove records that are the same'),
        ('ids', 'generate a list of headers'),
        ('format', 'trim accession id to the first space or switch id ' + \
                   'based on 2-column mapping file'),
        ('pool', 'pool a bunch of fastafiles together and add prefix'),
        ('random', 'randomly take some records'),
        ('diff', 'check if two fasta records contain same information'),
        ('trim', 'given a cross_match screened fasta, trim the sequence'),
        ('sort', 'sort the records by IDs, sizes, etc.'),
        ('filter', 'filter the records by size'),
        ('pair', 'sort paired reads to .pairs, rest to .fragments'),
        ('pairinplace', 'starting from fragment.fasta, find if ' +\
                "adjacent records can form pairs"),
        ('fastq', 'combine fasta and qual to create fastq file'),
        ('tidy', 'normalize gap sizes and remove small components in fasta'),
        ('sequin', 'generate a gapped fasta file for sequin submission'),
        ('gaps', 'print out a list of gap sizes within sequences'),
        ('join', 'concatenate a list of seqs and add gaps in between'),
        ('some', 'include or exclude a list of records (also performs on ' + \
                 '.qual file if available)'),
        ('clean', 'remove irregular chars in FASTA seqs'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def parse_fasta(infile):
    '''
    parse a fasta-formatted file and returns header
    can be a fasta file that contains multiple records.
    '''
    fp = open(infile)
    # keep header
    fa_iter = (x[1] for x in groupby(fp, lambda row: row[0] == '>'))
    for header in fa_iter:
        header = header.next()
        if header[0] != '>':
            continue
        # drop '>'
        header = header.strip()[1:]
        # stitch the sequence lines together and make into upper case
        seq = "".join(s.strip().upper() for s in fa_iter.next())
        yield header, seq


def clean(args):
    """
    %prog clean fastafile

    Remove irregular chars in FASTA seqs.
    """
    import string

    p = OptionParser(clean.__doc__)
    set_outfile(p)

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    fastafile, = args
    fw = must_open(opts.outfile, "w")
    for header, seq in parse_fasta(fastafile):
        seq = "".join(x for x in seq if x in string.letters or x == '*')
        seq = Seq(seq)
        s = SeqRecord(seq, id=header, description="")
        SeqIO.write([s], fw, "fasta")


def translate(args):
    """
    %prog translated cdsfasta

    Translate CDS to proteins. The tricky thing is that sometimes the CDS
    represents a partial gene, therefore disrupting the frame of the protein.
    Check all three frames to get a valid translation.
    """
    from collections import defaultdict
    from jcvi.utils.cbook import percentage

    p = OptionParser(translate.__doc__)
    p.add_option("--ids", default=False, action="store_true",
                 help="Create .ids file with the complete/partial/gaps "
                      "label [default: %default]")
    p.add_option("--longest", default=False, action="store_true",
                 help="Find the longest ORF from each input CDS [default: %default]")
    set_outfile(p)

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    cdsfasta, = args
    f = Fasta(cdsfasta, lazy=True)
    fw = must_open(opts.outfile, "w")

    if opts.ids:
        idsfile = cdsfasta.rsplit(".", 1)[0] + ".ids"
        ids = open(idsfile, "w")
    else:
        ids = None

    five_prime_missing = three_prime_missing = 0
    contain_ns = complete = cannot_translate = total = 0

    for name, rec in f.iteritems_ordered():
        cds = rec.seq
        cdslen = len(cds)
        peplen = cdslen / 3
        total += 1

        # if longest ORF is requested
        # try all six frames
        if opts.longest:
            orf = longest_orf(cds)
            if len(orf) == 0:
                continue
            newcds = Seq(orf)
            pep = newcds.translate()
        else:
            # Try all three frames
            for i in xrange(3):
                newcds = cds[i: i + peplen * 3]
                pep = newcds.translate()
                if "*" not in pep.rstrip("*"):
                    break

        labels = []
        if "*" in pep.rstrip("*"):
            logging.error("{0} cannot translate".format(name))
            cannot_translate += 1
            labels.append("cannot_translate")

        contains_start = pep.startswith("M")
        contains_stop = pep.endswith("*")
        contains_ns = "X" in pep
        start_ns = pep.startswith("X")
        end_ns = pep.endswith("X")

        if not contains_start:
            five_prime_missing += 1
            labels.append("five_prime_missing")
        if not contains_stop:
            three_prime_missing += 1
            labels.append("three_prime_missing")
        if contains_ns:
            contain_ns += 1
            labels.append("contain_ns")
        if contains_start and contains_stop:
            complete += 1
            labels.append("complete")
        if start_ns:
            labels.append("start_ns")
        if end_ns:
            labels.append("end_ns")

        if ids:
            print >> ids, "\t".join((name, ",".join(labels)))

        peprec = SeqRecord(pep, id=name, description=rec.description)
        SeqIO.write([peprec], fw, "fasta")
        fw.flush()

    print >> sys.stderr, "Complete gene models: {0}".\
                        format(percentage(complete, total))
    print >> sys.stderr, "Missing 5`-end: {0}".\
                        format(percentage(five_prime_missing, total))
    print >> sys.stderr, "Missing 3`-end: {0}".\
                        format(percentage(three_prime_missing, total))
    print >> sys.stderr, "Contain Ns: {0}".\
                        format(percentage(contain_ns, total))

    if cannot_translate:
        print >> sys.stderr, "Cannot translate: {0}".\
                        format(percentage(cannot_translate, total))


def filter(args):
    """
    %prog filter fastafile 100

    Filter the FASTA file to contain records with size >= or <= certain cutoff.
    """
    p = OptionParser(filter.__doc__)
    p.add_option("--less", default=False, action="store_true",
                 help="filter the sizes <= certain cutoff [default: >=]")

    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    fastafile, cutoff = args
    try:
        cutoff = int(cutoff)
    except ValueError:
        sys.exit(not p.print_help())

    f = Fasta(fastafile, lazy=True)

    fw = sys.stdout
    for name, rec in f.iteritems_ordered():

        if opts.less and len(rec) > cutoff:
            continue

        if (not opts.less) and len(rec) < cutoff:
            continue

        SeqIO.write([rec], fw, "fasta")
        fw.flush()


def pool(args):
    """
    %prog pool fastafiles

    Pool a bunch of FASTA files, and add prefix to each record based on
    filenames.
    """
    p = OptionParser(pool.__doc__)

    if len(args) < 1:
        sys.exit(not p.print_help())

    for fastafile in args:
        pf = op.basename(fastafile).split(".")[0].split("_")[0]
        prefixopt = "--prefix={0}_".format(pf)
        format([fastafile, "stdout", prefixopt])


def ids(args):
    """
    %prog ids fastafiles

    Generate the FASTA headers without the '>'.
    """
    p = OptionParser(ids.__doc__)
    p.add_option("--until", default=None,
             help="Truncate the name and description at words [default: %default]")
    p.add_option("--description", default=False, action="store_true",
             help="Generate a second column with description [default: %default]")
    set_outfile(p)

    opts, args = p.parse_args(args)

    if len(args) < 1:
        sys.exit(not p.print_help())

    until = opts.until
    fw = must_open(opts.outfile, "w")
    for row in must_open(args):
        if row[0] == ">":
            row = row[1:].rstrip()
            if until:
                row = row.split(until)[0]

            atoms = row.split(None, 1)
            if opts.description:
                outrow = "\t".join(atoms)
            else:
                outrow = atoms[0]
            print >> fw, outrow

    fw.close()


def sort(args):
    """
    %prog sort fastafile

    Sort a list of sequences and output with sorted IDs, etc.
    """
    p = OptionParser(sort.__doc__)
    p.add_option("--sizes", default=False, action="store_true",
                 help="Sort by decreasing size [default: %default]")

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(p.print_help())

    fastafile, = args
    sortedfastafile = fastafile.rsplit(".", 1)[0] + ".sorted.fasta"

    f = Fasta(fastafile, index=False)
    fw = must_open(sortedfastafile, "w")
    if opts.sizes:
        # Sort by decreasing size
        sortlist = sorted(f.itersizes(), key=lambda x: (-x[1], x[0]))
        logging.debug("Sort by size: max: {0}, min: {1}".\
                        format(sortlist[0], sortlist[-1]))
        sortlist = [x for x, s in sortlist]
    else:
        sortlist = sorted(f.iterkeys())

    for key in sortlist:
        rec = f[key]
        SeqIO.write([rec], fw, "fasta")

    logging.debug("Sorted file written to `{0}`.".format(sortedfastafile))
    fw.close()


def join(args):
    """
    %prog join fastafile [phasefile]

    Make AGP file for a bunch of sequences, and add gaps between, and then build
    the joined fastafile. This is useful by itself, but with --oo option this
    can convert the .oo (BAMBUS output) into AGP and a joined fasta.

    Phasefile is optional, but must contain two columns - BAC and phase (0, 1, 2, 3).
    """
    from jcvi.formats.agp import OO, Phases, build
    from jcvi.formats.sizes import Sizes

    p = OptionParser(join.__doc__)
    p.add_option("--newid", default=None,
            help="New sequence ID [default: `%default`]")
    p.add_option("--gapsize", default=100, type="int",
            help="Number of N's in between the sequences [default: %default]")
    p.add_option("--gaptype", default="contig",
            help="Gap type to use in the AGP file [default: %default]")
    p.add_option("--evidence", default="",
            help="Linkage evidence to report in the AGP file [default: %default]")
    p.add_option("--oo", help="Use .oo file generated by bambus [default: %default]")
    opts, args = p.parse_args(args)

    nargs = len(args)
    if nargs not in (1, 2):
        sys.exit(not p.print_help())

    if nargs == 2:
        fastafile, phasefile = args
        phases = DictFile(phasefile)
        phases = dict((a, Phases[int(b)]) for a, b in phases.items())
    else:
        fastafile, = args
        phases = {}

    sizes = Sizes(fastafile)
    prefix = fastafile.rsplit(".", 1)[0]
    agpfile = prefix + ".agp"
    newid = opts.newid
    oo = opts.oo

    o = OO(oo, sizes.mapping)

    if oo:
        seen = o.contigs
        # The leftover contigs not in the oo file
        logging.debug("A total of {0} contigs ({1} in `{2}`)".\
                    format(len(sizes), len(seen), oo))

        for ctg, size in sizes.iter_sizes():
            if ctg in seen:
                continue
            o.add(ctg, ctg, size)

    else:
        if newid:
            for ctg, size in sizes.iter_sizes():
                o.add(newid, ctg, size)
        else:
            for scaffold_number, (ctg, size) in enumerate(sizes.iter_sizes()):
                object_id = "scaffold{0:03d}".format(scaffold_number + 1)
                o.add(object_id, ctg, size)

    fw = open(agpfile, "w")
    o.write_AGP(fw, gapsize=opts.gapsize, gaptype=opts.gaptype,
                    evidence=opts.evidence, phases=phases)
    fw.close()

    joinedfastafile = prefix + ".joined.fasta"
    build([agpfile, fastafile, joinedfastafile])


def summary(args):
    """
    %prog summary *.fasta

    Report real bases and N's in fastafiles in a tabular report
    """
    from jcvi.utils.table import write_csv

    p = OptionParser(summary.__doc__)
    p.add_option("--suffix", default="Mb",
            help="make the base pair counts human readable [default: %default]")
    p.add_option("--ids",
            help="write the ids that have >= 50% N's [default: %default]")
    set_outfile(p)

    opts, args = p.parse_args(args)

    if len(args) == 0:
        sys.exit(not p.print_help())

    idsfile = opts.ids
    header = "Seqid Real N's Total %_real".split()
    if idsfile:
        idsfile = open(idsfile, "w")
        nids = 0

    data = []
    for fastafile in args:
        for rec in SeqIO.parse(fastafile, "fasta"):
            seqlen = len(rec)
            nns = rec.seq.count('n') + rec.seq.count('N')
            reals = seqlen - nns
            pct = reals * 100. / seqlen
            pctreal = "{0:.1f} %".format(pct)
            if idsfile and pct < 50:
                nids += 1
                print >> idsfile, rec.id

            data.append((rec.id, reals, nns, seqlen, pctreal))

    ids, reals, nns, seqlen, pctreal = zip(*data)
    reals = sum(reals)
    nns = sum(nns)
    seqlen = sum(seqlen)
    pctreal = "{0:.1f} %".format(reals * 100. / seqlen)
    data.append(("Total", reals, nns, seqlen, pctreal))

    write_csv(header, data, sep=" ", filename=opts.outfile)
    if idsfile:
        logging.debug("A total of {0} ids >= 50% N's written to {1}.".\
                      format(nids, idsfile.name))
        idsfile.close()

    return reals, nns, seqlen


def format(args):
    """
    %prog format infasta outfasta

    Reformat FASTA file and also clean up names.
    """
    p = OptionParser(format.__doc__)
    p.add_option("--pairs", default=False, action="store_true",
            help="Add trailing /1 and /2 for interleaved pairs [default: %default]")
    p.add_option("--sequential", default=False, action="store_true",
            help="Add sequential IDs [default: %default]")
    p.add_option("--pad0", default=6, type="int",
            help="Pad a few zeros in front of sequential [default: %default]")
    p.add_option("--gb", default=False, action="store_true",
            help="For Genbank ID, get the accession [default: %default]")
    p.add_option("--until", default=None,
            help="Get the names until certain symbol [default: %default]")
    p.add_option("--noversion", default=False, action="store_true",
            help="Remove the gb trailing version [default: %default]")
    p.add_option("--prefix", help="Prepend prefix to sequence ID")
    p.add_option("--suffix", help="Append suffix to sequence ID")
    p.add_option("--index", default=0, type="int",
            help="Extract i-th field in the description [default: %default]")
    p.add_option("--template", default=False, action="store_true",
            help="Extract `template=aaa dir=x library=m` to `m-aaa/x` [default: %default]")
    p.add_option("--switch", help="Switch ID from two-column file [default: %default]")
    p.add_option("--annotation", help="Add functional annotation from "
                        "two-column file ('ID <--> Annotation') [default: %default]")
    p.add_option("--ids", help="Generate ID conversion table [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    infasta, outfasta = args
    gb = opts.gb
    until = opts.until
    pairs = opts.pairs
    prefix = opts.prefix
    suffix = opts.suffix
    noversion = opts.noversion
    sequential = opts.sequential
    idx = opts.index
    mapfile = opts.switch
    annotfile = opts.annotation
    idsfile = opts.ids
    idsfile = open(idsfile, "w") if idsfile else None

    if mapfile:
        mapping = DictFile(mapfile, delimiter="\t")
    if annotfile:
        annotation = DictFile(annotfile, delimiter="\t")

    fw = must_open(outfasta, "w")
    fp = SeqIO.parse(must_open(infasta), "fasta")
    for i, rec in enumerate(fp):
        origid = rec.id
        description = rec.description
        if until:
            description = description.split(until, 1)[0]
            rec.id = description
        if idx:
            description = description.split()[idx]
            rec.id = description

        if gb:
            # gi|262233616|gb|GU123895.1| Coffea arabica clone BAC
            atoms = rec.id.split("|")
            if len(atoms) >= 3:
                rec.id = atoms[3]
            elif len(atoms) == 2:
                rec.id = atoms[1]
        if pairs:
            id = "/1" if (i % 2 == 0) else "/2"
            rec.id += id
        if noversion:
            rec.id = rec.id.rsplit(".", 1)[0]
        if sequential:
            rec.id = "{0:0{1}d}".format(i + 1, opts.pad0)
        if prefix:
            rec.id = prefix + rec.id
        if suffix:
            rec.id += suffix
        if opts.template:
            template, dir, lib = [x.split("=")[-1] for x in
                    rec.description.split()[1:4]]
            rec.id = "{0}-{1}/{2}".format(lib, template, dir)
        if mapfile:
            if origid in mapping:
                rec.id = mapping[origid]
            else:
                logging.error("{0} not found in `{1}`. ID unchanged.".\
                        format(origid, mapfile))
        rec.description = ""
        if annotfile:
            rec.description = annotation.get(origid, "")
        if idsfile:
            print >> idsfile, "\t".join((origid, rec.id))

        SeqIO.write(rec, fw, "fasta")

    if idsfile:
        logging.debug("Conversion table written to `{0}`.".\
                      format(idsfile.name))
        idsfile.close()


def print_first_difference(arec, brec, ignore_case=False, ignore_N=False,
        rc=False):
    """
    Returns the first different nucleotide in two sequence comparisons
    runs both Plus and Minus strand
    """
    plus_match = _print_first_difference(arec, brec, ignore_case=ignore_case,
            ignore_N=ignore_N)
    if rc and not plus_match:
        logging.debug("trying reverse complement of %s" % brec.id)
        brec.seq = brec.seq.reverse_complement()
        minus_match = _print_first_difference(arec, brec,
                ignore_case=ignore_case, ignore_N=ignore_N)
        return minus_match

    else:
        return plus_match


def _print_first_difference(arec, brec, ignore_case=False, ignore_N=False):
    """
    Returns the first different nucleotide in two sequence comparisons
    """
    aseq, bseq = arec.seq, brec.seq
    asize, bsize = len(aseq), len(bseq)

    matched = True
    for i, (a, b) in enumerate(izip_longest(aseq, bseq)):
        if ignore_case and None not in (a, b):
            a, b = a.upper(), b.upper()

        if ignore_N and ('N' in (a, b) or 'X' in (a, b)):
            continue

        if a != b:
            matched = False
            break

    if i + 1 == asize and matched:
        print green("Two sequences match")
        match = True
    else:
        print red("Two sequences do not match")

        snippet_size = 20  # show the context of the difference

        print red("Sequence start to differ at position %d:" % (i + 1))

        begin = max(i - snippet_size, 0)
        aend = min(i + snippet_size, asize)
        bend = min(i + snippet_size, bsize)

        print red(aseq[begin:i] + "|" + aseq[i:aend])
        print red(bseq[begin:i] + "|" + bseq[i:bend])
        match = False

    return match


def diff(args):
    """
    %prog diff afasta bfasta

    print out whether the records in two fasta files are the same
    """
    p = OptionParser(diff.__doc__)
    p.add_option("--ignore_case", default=False, action="store_true",
            help="ignore case when comparing sequences [default: %default]")
    p.add_option("--ignore_N", default=False, action="store_true",
            help="ignore N and X's when comparing sequences [default: %default]")
    p.add_option("--ignore_stop", default=False, action="store_true",
            help="ignore stop codon when comparing sequences [default: %default]")
    p.add_option("--rc", default=False, action="store_true",
            help="also consider reverse complement")

    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    afasta, bfasta = args

    afastan = len(Fasta(afasta))
    bfastan = len(Fasta(bfasta))

    if afastan == bfastan:
        print green("Two sets contain the same number of sequences ({0}, {1})".\
                format(afastan, bfastan))
    else:
        print red("Two sets contain different number of sequences ({0}, {1})".\
                format(afastan, bfastan))

    ah = SeqIO.parse(afasta, "fasta")
    bh = SeqIO.parse(bfasta, "fasta")

    problem_ids = []
    for arec, brec in zip(ah, bh):

        if opts.ignore_stop:
            arec.seq = arec.seq.rstrip("*")
            brec.seq = brec.seq.rstrip("*")

        print banner((arec, brec))
        asize, bsize = len(arec), len(brec)
        if asize == bsize:
            print green("Two sequence size match (%d)" % asize)
        else:
            print red("Two sequence size do not match (%d, %d)" % (asize, bsize))

        # print out the first place the two sequences diff
        fd = print_first_difference(arec, brec, ignore_case=opts.ignore_case,
                ignore_N=opts.ignore_N, rc=opts.rc)
        if not fd:
            logging.error("Two sets of sequences differ at `{0}`".format(arec.id))
            problem_ids.append("\t".join(str(x) for x in (arec.id, asize, bsize,
                    abs(asize - bsize))))

    if problem_ids:
        print red("A total of {0} records mismatch.".format(len(problem_ids)))
        fw = must_open("Problems.ids", "w")
        print >> fw, "\n".join(problem_ids)


QUALSUFFIX = ".qual"


def get_qual(fastafile, suffix=QUALSUFFIX, check=True):
    """
    Check if current folder contains a qual file associated with the fastafile
    """
    qualfile1 = fastafile.rsplit(".", 1)[0] + suffix
    qualfile2 = fastafile + suffix

    if check:
        if op.exists(qualfile1):
            logging.debug("qual file `{0}` found".format(qualfile1))
            return qualfile1
        elif op.exists(qualfile2):
            logging.debug("qual file `{0}` found".format(qualfile2))
            return qualfile2
        else:
            logging.warning("qual file not found")
            return None

    return qualfile1


def some(args):
    """
    %prog some fastafile listfile outfastafile

    generate a subset of fastafile, based on a list
    """
    p = OptionParser(some.__doc__)
    p.add_option("--exclude", default=False, action="store_true",
            help="Output sequences not in the list file [default: %default]")
    p.add_option("--uniprot", default=False, action="store_true",
            help="Header is from uniprot [default: %default]")

    opts, args = p.parse_args(args)

    if len(args) != 3:
        sys.exit(p.print_help())

    fastafile, listfile, outfastafile = args
    outfastahandle = must_open(outfastafile, "w")
    qualfile = get_qual(fastafile)

    names = set(x.strip() for x in open(listfile))
    if qualfile:
        outqualfile = outfastafile + ".qual"
        outqualhandle = open(outqualfile, "w")
        parser = iter_fasta_qual(fastafile, qualfile)
    else:
        parser = SeqIO.parse(fastafile, "fasta")

    num_records = 0
    for rec in parser:
        name = rec.id
        if opts.uniprot:
            name = name.split("|")[-1]

        if opts.exclude:
            if name in names:
                continue
        else:
            if name not in names:
                continue

        SeqIO.write([rec], outfastahandle, "fasta")
        if qualfile:
            SeqIO.write([rec], outqualhandle, "qual")

        num_records += 1

    logging.debug("A total of %d records written to `%s`" % \
            (num_records, outfastafile))


def fastq(args):
    """
    %prog fastq fastafile

    Generate fastqfile by combining fastafile and fastafile.qual.
    Also check --qv option to use a default qv score.
    """
    from jcvi.formats.fastq import FastqLite

    p = OptionParser(fastq.__doc__)
    p.add_option("--qv", type="int",
                 help="Use generic qv value [dafault: %default]")

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    fastafile, = args
    fastqfile = fastafile.rsplit(".", 1)[0] + ".fastq"
    fastqhandle = open(fastqfile, "w")
    num_records = 0

    if opts.qv is not None:
        qv = chr(ord('!') + opts.qv)
        logging.debug("QV char '{0}' ({1})".format(qv, opts.qv))
    else:
        qv = None

    if qv:
        f = Fasta(fastafile, lazy=True)
        for name, rec in f.iteritems_ordered():
            r = FastqLite("@" + name, str(rec.seq).upper(), qv * len(rec.seq))
            print >> fastqhandle, r
            num_records += 1

    else:
        qualfile = get_qual(fastafile)
        for rec in iter_fasta_qual(fastafile, qualfile):
            SeqIO.write([rec], fastqhandle, "fastq")
            num_records += 1

    fastqhandle.close()
    logging.debug("A total of %d records written to `%s`" % \
            (num_records, fastqfile))


def pair(args):
    """
    %prog pair fastafile

    Generate .pairs.fasta and .fragments.fasta by matching records
    into the pairs and the rest go to fragments.
    """
    p = OptionParser(pair.__doc__)
    p.add_option("-d", dest="separator", default=None,
            help="separater in the name field to reduce to the same clone " +\
                 "[e.g. GFNQ33242/1 use /, BOT01-2453H.b1 use .]" +\
                 "[default: trim until last char]")
    p.add_option("-m", dest="matepairs", default=False, action="store_true",
            help="generate .matepairs file [often used for Celera Assembler]")

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(p.print_help())

    fastafile, = args
    qualfile = get_qual(fastafile)

    prefix = fastafile.rsplit(".", 1)[0]
    pairsfile = prefix + ".pairs.fasta"
    fragsfile = prefix + ".frags.fasta"
    pairsfw = open(pairsfile, "w")
    fragsfw = open(fragsfile, "w")

    #TODO: need a class to handle coupled fasta and qual iterating and indexing
    if opts.matepairs:
        matepairsfile = prefix + ".matepairs"
        matepairsfw = open(matepairsfile, "w")

    if qualfile:
        pairsqualfile = pairsfile + ".qual"
        pairsqualhandle = open(pairsqualfile, "w")
        fragsqualfile = fragsfile + ".qual"
        fragsqualhandle = open(fragsqualfile, "w")

    f = Fasta(fastafile)
    if qualfile:
        q = SeqIO.index(qualfile, "qual")

    all_keys = list(f.iterkeys())
    all_keys.sort()
    sep = opts.separator

    if sep:
        key_fun = lambda x: x.split(sep, 1)[0]
    else:
        key_fun = lambda x: x[:-1]

    for key, variants in groupby(all_keys, key=key_fun):
        variants = list(variants)
        paired = (len(variants) == 2)

        if paired and opts.matepairs:
            print >> matepairsfw, "\t".join(("%s/1" % key, "%s/2" % key))

        fw = pairsfw if paired else fragsfw
        if qualfile:
            qualfw = pairsqualhandle if paired else fragsqualhandle

        for i, var in enumerate(variants):
            rec = f[var]
            if qualfile:
                recqual = q[var]
            newid = "%s/%d" % (key, i + 1)

            rec.id = newid
            rec.description = ""
            SeqIO.write([rec], fw, "fasta")
            if qualfile:
                recqual.id = newid
                recqual.description = ""
                SeqIO.write([recqual], qualfw, "qual")

    logging.debug("sequences written to `%s` and `%s`" % \
            (pairsfile, fragsfile))
    if opts.matepairs:
        logging.debug("mates written to `%s`" % matepairsfile)


def pairinplace(args):
    """
    %prog pairinplace bulk.fasta

    Pair up the records in bulk.fasta by comparing the names for adjancent
    records. If they match, print to bulk.pairs.fasta, else print to
    bulk.frags.fasta.
    """
    from jcvi.utils.iter import pairwise

    p = OptionParser(pairinplace.__doc__)
    p.add_option("-r", dest="rclip", default=1, type="int",
            help="pair ID is derived from rstrip N chars [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    fastafile, = args
    base = op.basename(fastafile).split(".")[0]

    frags = base + ".frags.fasta"
    pairs = base + ".pairs.fasta"
    if fastafile.endswith(".gz"):
        frags += ".gz"
        pairs += ".gz"

    fragsfw = must_open(frags, "w")
    pairsfw = must_open(pairs, "w")

    N = opts.rclip
    strip_name = lambda x: x[:-N] if N else str

    skipflag = False  # controls the iterator skip
    fastaiter = SeqIO.parse(fastafile, "fasta")
    for a, b in pairwise(fastaiter):

        aid, bid = [strip_name(x) for x in (a.id, b.id)]

        if skipflag:
            skipflag = False
            continue

        if aid == bid:
            SeqIO.write([a, b], pairsfw, "fasta")
            skipflag = True
        else:
            SeqIO.write([a], fragsfw, "fasta")

    # don't forget the last one, when b is None
    if not skipflag:
        SeqIO.write([a], fragsfw, "fasta")

    logging.debug("Reads paired into `%s` and `%s`" % (pairs, frags))


def extract(args):
    """
    %prog extract fasta query

    extract query out of fasta file, query needs to be in the form of
    "seqname", or "seqname:start-stop", or "seqname:start-stop:-"
    """
    p = OptionParser(extract.__doc__)
    p.add_option('--include', default=False, action="store_true",
            help="search description line for match [default: %default]")
    p.add_option('--exclude', default=False, action="store_true",
            help="exclude description that matches [default: %default]")
    set_outfile(p)

    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(p.print_help())

    fastafile, query = args

    atoms = query.split(":")
    key = atoms[0]

    assert len(atoms) <= 3, "cannot have more than two ':' in your query"

    pos = ""
    if len(atoms) in (2, 3):
        pos = atoms[1]

    strand = "+"
    if len(atoms) == 3:
        strand = atoms[2]

    assert strand in ('+', '-'), "strand must be either '+' or '-'"

    feature = dict(chr=key)

    if "-" in pos:
        start, stop = pos.split("-")
        try:
            start, stop = int(start), int(stop)
        except ValueError as e:
            logging.error(e)
            sys.exit(p.print_help())

        feature["start"] = start
        feature["stop"] = stop
    else:
        start, stop = None, None

    assert start < stop or None in (start, stop), \
            "start must be < stop, you have ({0}, {1})".format(start, stop)
    feature["strand"] = strand

    include, exclude = opts.include, opts.exclude
    # conflicting options, cannot be true at the same time
    assert not (include and exclude), "--include and --exclude cannot be "\
            "on at the same time"
    fw = must_open(opts.outfile, "w")

    if include or exclude:
        f = Fasta(fastafile, lazy=True)
        for k, rec in f.iterdescriptions_ordered():
            if include and key not in k:
                continue
            if exclude and key in k:
                continue

            seq = Fasta.subseq(rec, start, stop, strand)
            newid = rec.id
            if start is not None:
                newid += ":{0}-{1}:{2}".format(start, stop, strand)

            rec = SeqRecord(seq, id=newid, description=k)
            SeqIO.write([rec], fw, "fasta")
    else:
        f = Fasta(fastafile)
        try:
            seq = f.sequence(feature, asstring=False)
        except AssertionError as e:
            logging.error(e)
            return

        rec = SeqRecord(seq, id=query, description="")
        SeqIO.write([rec], fw, "fasta")


def _uniq_rec(fastafile):
    """
    Returns unique records
    """
    seen = set()
    for rec in SeqIO.parse(fastafile, "fasta"):
        name = rec.id
        if name in seen:
            logging.debug("ignore %s" % name)
            continue
        seen.add(name)
        yield rec


def uniq(args):
    """
    %prog uniq fasta uniq.fasta

    remove fasta records that are the same
    """
    p = OptionParser(uniq.__doc__)
    p.add_option("-t", "--trimname", dest="trimname",
            action="store_true", default=False,
            help="turn on the defline trim to first space [default: %default]")

    opts, args = p.parse_args(args)
    if len(args) != 2:
        sys.exit(p.print_help())

    fastafile, uniqfastafile = args
    fw = must_open(uniqfastafile, "w")

    for rec in _uniq_rec(fastafile):
        if opts.trimname:
            rec.description = ""
        SeqIO.write([rec], fw, "fasta")


def random(args):
    """
    %prog random fasta 100 > random100.fasta

    Take number of records randomly from fasta
    """
    p = OptionParser(random.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    fastafile, N = args
    N = int(N)
    assert N > 0

    f = Fasta(fastafile)
    fw = must_open("stdout", "w")

    for key in sample(f.keys(), N):
        rec = f[key]
        SeqIO.write([rec], fw, "fasta")

    fw.close()


XQUAL = -1000  # default quality for X
NQUAL = 5  # default quality value for N
QUAL = 10  # default quality value
OKQUAL = 15


def modify_qual(rec):
    qv = rec.letter_annotations['phred_quality']
    for i, (s, q) in enumerate(zip(rec.seq, qv)):
        if s == 'X' or s == 'x':
            qv[i] = XQUAL
        if s == 'N' or s == 'x':
            qv[i] = NQUAL
    return rec


def iter_fasta_qual(fastafile, qualfile, defaultqual=OKQUAL, modify=False):
    """
    used by trim, emits one SeqRecord with quality values in it
    """
    fastahandle = SeqIO.parse(fastafile, "fasta")

    if qualfile:
        qualityhandle = SeqIO.parse(qualfile, "qual")
        for rec, rec_qual in zip(fastahandle, qualityhandle):
            assert len(rec) == len(rec_qual)
            rec.letter_annotations['phred_quality'] = \
                rec_qual.letter_annotations['phred_quality']
            yield rec if not modify else modify_qual(rec)

    else:
        logging.warning("assume qual ({0})".format(defaultqual))
        for rec in fastahandle:
            rec.letter_annotations['phred_quality'] = [defaultqual] * len(rec)
            yield rec if not modify else modify_qual(rec)


def write_fasta_qual(rec, fastahandle, qualhandle):
    if fastahandle:
        SeqIO.write([rec], fastahandle, "fasta")
    if qualhandle:
        SeqIO.write([rec], qualhandle, "qual")


def trim(args):
    """
    %prog trim fasta.screen newfasta

    take the screen output from `cross_match` (against a vector db, for
    example), then trim the sequences to remove X's. Will also perform quality
    trim if fasta.screen.qual is found. The trimming algorithm is based on
    finding the subarray that maximize the sum
    """

    from jcvi.algorithms.maxsum import max_sum

    p = OptionParser(trim.__doc__)
    p.add_option("-c", dest="min_length", type="int", default=64,
            help="minimum sequence length after trimming")
    p.add_option("-s", dest="score", default=QUAL,
            help="quality trimming cutoff [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(p.print_help())

    fastafile, newfastafile = args
    qualfile = get_qual(fastafile)
    newqualfile = get_qual(newfastafile, check=False)

    logging.debug("Trim bad sequence from fasta file `%s` to `%s`" % \
            (fastafile, newfastafile))

    fw = must_open(newfastafile, "w")
    fw_qual = open(newqualfile, "w")

    dropped = trimmed = 0

    for rec in iter_fasta_qual(fastafile, qualfile, modify=True):
        qv = [x - opts.score for x in \
                rec.letter_annotations["phred_quality"]]
        msum, trim_start, trim_end = max_sum(qv)
        score = trim_end - trim_start + 1

        if score < opts.min_length:
            dropped += 1
            continue

        if score < len(rec):
            trimmed += 1
            rec = rec[trim_start:trim_end + 1]

        write_fasta_qual(rec, fw, fw_qual)

    print >>sys.stderr, "A total of %d sequences modified." % trimmed
    print >>sys.stderr, "A total of %d sequences dropped (length < %d)." % \
        (dropped, opts.min_length)

    fw.close()
    fw_qual.close()


def sequin(args):
    """
    %prog sequin inputfasta

    Generate a gapped fasta format with known gap sizes embedded. suitable for
    Sequin submission.

    A gapped sequence represents a newer method for describing non-contiguous
    sequences, but only requires a single sequence identifier. A gap is
    represented by a line that starts with >? and is immediately followed by
    either a length (for gaps of known length) or "unk100" for gaps of unknown
    length. For example, ">?200". The next sequence segment continues on the
    next line, with no separate definition line or identifier. The difference
    between a gapped sequence and a segmented sequence is that the gapped
    sequence uses a single identifier and can specify known length gaps.
    Gapped sequences are preferred over segmented sequences. A sample gapped
    sequence file is shown here:

    >m_gagei [organism=Mansonia gagei] Mansonia gagei NADH dehydrogenase ...
    ATGGAGCATACATATCAATATTCATGGATCATACCGTTTGTGCCACTTCCAATTCCTATTTTAATAGGAA
    TTGGACTCCTACTTTTTCCGACGGCAACAAAAAATCTTCGTCGTATGTGGGCTCTTCCCAATATTTTATT
    >?200
    GGTATAATAACAGTATTATTAGGGGCTACTTTAGCTCTTGC
    TCAAAAAGATATTAAGAGGGGTTTAGCCTATTCTACAATGTCCCAACTGGGTTATATGATGTTAGCTCTA
    >?unk100
    TCAATAAAACTATGGGGTAAAGAAGAACAAAAAATAATTAACAGAAATTTTCGTTTATCTCCTTTATTAA
    TATTAACGATGAATAATAATGAGAAGCCATATAGAATTGGTGATAATGTAAAAAAAGGGGCTCTTATTAC
    """
    p = OptionParser(sequin.__doc__)
    p.add_option("--mingap", dest="mingap", default=100, type="int",
            help="The minimum size of a gap to split [default: %default]")
    p.add_option("--unk", default=100, type="int",
            help="The size for unknown gaps [default: %default]")
    p.add_option("--newid", default=None,
            help="Use this identifier instead [default: %default]")
    p.add_option("--chromosome", default=None,
            help="Add [chromosome= ] to FASTA header [default: %default]")
    p.add_option("--clone", default=None,
            help="Add [clone= ] to FASTA header [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    inputfasta, = args
    unk = opts.unk

    outputfasta = inputfasta.rsplit(".", 1)[0] + ".split"
    rec = SeqIO.parse(must_open(inputfasta), "fasta").next()
    seq = ""
    unknowns, knowns = 0, 0
    for gap, gap_group in groupby(rec.seq, lambda x: x.upper() == 'N'):
        subseq = "".join(gap_group)
        if gap:
            gap_length = len(subseq)
            if gap_length == unk:
                subseq = "\n>?unk{0}\n".format(unk)
                unknowns += 1
            elif gap_length >= opts.mingap:
                subseq = "\n>?{0}\n".format(gap_length)
                knowns += 1
        seq += subseq

    fw = must_open(outputfasta, "w")
    id = opts.newid or rec.id
    fastaheader = ">{0}".format(id)
    if opts.chromosome:
        fastaheader += " [chromosome={0}]".format(opts.chromosome)
    if opts.clone:
        fastaheader += " [clone={0}]".format(opts.clone)

    print >> fw, fastaheader
    print >> fw, seq
    fw.close()
    logging.debug("Sequin FASTA written to `{0}` (gaps: {1} unknowns, {2} knowns).".\
            format(outputfasta, unknowns, knowns))

    return outputfasta, unknowns + knowns


def tidy(args):
    """
    %prog tidy fastafile

    Normalize gap sizes (default 100 N's) and remove small components (less than
    100 nucleotides).
    """
    p = OptionParser(tidy.__doc__)
    p.add_option("--gapsize", dest="gapsize", default=100, type="int",
            help="Set all gaps to the same size [default: %default]")
    p.add_option("--minlen", dest="minlen", default=100, type="int",
            help="Minimum component size [default: %default]")

    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    fastafile, = args
    gapsize = opts.gapsize
    minlen = opts.minlen
    tidyfastafile = fastafile.rsplit(".", 1)[0] + ".tidy.fasta"
    fw = must_open(tidyfastafile, "w")
    normalized_gap = "N" * gapsize

    for rec in SeqIO.parse(fastafile, "fasta"):
        newseq = ""
        dangle_gaps = 0
        for gap, seq in groupby(rec.seq, lambda x: x.upper() == 'N'):
            seq = "".join(seq)
            seqlen = len(seq)
            msg = None
            if gap:
                nsize = max(gapsize - dangle_gaps, 0)
                if seqlen < 10:
                    if nsize > seqlen:
                        nsize = seqlen
                    dangle_gaps += seqlen
                else:
                    if seqlen != gapsize:
                        msg = "Normalize gap size ({0}) to {1}" \
                                .format(seqlen, nsize)
                    dangle_gaps = gapsize

                newseq += nsize * 'N'
            else:
                if seqlen < minlen:
                    msg = "Discard component ({0})".format(seqlen)
                else:
                    newseq += seq
                    # Discarding components might cause flank gaps to merge
                    # should be handled in dangle_gaps, which is only reset when
                    # seeing an actual sequence
                    dangle_gaps = 0

            if msg:
                msg = rec.id + ": " + msg
                logging.info(msg)

        newseq = newseq.strip('N')
        ngaps = newseq.count(normalized_gap)

        rec.seq = Seq(newseq)

        SeqIO.write([rec], fw, "fasta")


def gaps(args):
    """
    %prog gaps fastafile

    Print out a list of gaps in BED format (.gaps.bed).
    """
    p = OptionParser(gaps.__doc__)
    p.add_option("--mingap", default=100, type="int",
            help="The minimum size of a gap to split [default: %default]")
    p.add_option("--agp", default=False, action="store_true",
            help="Generate AGP file to show components [default: %default]")
    p.add_option("--split", default=False, action="store_true",
            help="Generate .split.fasta [default: %default]")
    p.add_option("--log", default=False, action="store_true",
            help="Generate gap positions to .gaps.log [default: %default]")
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    inputfasta, = args
    mingap = opts.mingap
    prefix = inputfasta.rsplit(".", 1)[0]
    bedfile = prefix + ".gaps.bed"
    fwbed = open(bedfile, "w")
    logging.debug("Write gap locations to `{0}`.".format(bedfile))

    if opts.log:
        logfile = prefix + ".gaps.log"
        fwlog = must_open(logfile, "w")
        logging.debug("Write gap locations to `{0}`.".format(logfile))

    gapnum = 0
    for rec in SeqIO.parse(inputfasta, "fasta"):
        allgaps = []
        start = 0
        object = rec.id
        for gap, seq in groupby(rec.seq.upper(), lambda x: x == 'N'):
            seq = "".join(seq)
            current_length = len(seq)
            object_beg = start + 1
            object_end = start + current_length
            if gap and current_length >= opts.mingap:
                allgaps.append((current_length, start))
                gapnum += 1
                gapname = "gap.{0:05d}".format(gapnum)
                print >> fwbed, "\t".join(str(x) for x in (object,
                    object_beg - 1, object_end, gapname))

            start += current_length

        if opts.log:
            if allgaps:
                lengths, starts = zip(*allgaps)
                gap_description = ",".join(str(x) for x in lengths)
                starts = ",".join(str(x) for x in starts)
            else:
                gap_description = starts = "no gaps"

            print >> fwlog, "\t".join((rec.id, str(len(allgaps)),
                    gap_description, starts))

    fwbed.close()

    if opts.agp or opts.split:
        from jcvi.formats.sizes import agp
        from jcvi.formats.agp import mask

        agpfile = prefix + ".gaps.agp"
        sizesagpfile = agp([inputfasta])

        maskopts = [sizesagpfile, bedfile]
        if opts.split:
            maskopts += ["--split"]
        maskedagpfile = mask(maskopts)

        shutil.move(maskedagpfile, agpfile)
        os.remove(sizesagpfile)
        logging.debug("AGP file written to `{0}`.".format(agpfile))

    if opts.split:
        from jcvi.formats.agp import build

        splitfile = prefix + ".split.fasta"
        build([agpfile, inputfasta, splitfile])


if __name__ == '__main__':
    main()
