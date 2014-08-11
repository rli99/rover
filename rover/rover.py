#!/usr/bin/env python

from argparse import (ArgumentParser, FileType)
# from pyfaidx import Fasta
# from numpy import *
# import Gnuplot, Gnuplot.funcutils
import datetime
import logging
import sys
import pysam
import re
import os
import vcf
from operator import itemgetter
import csv
from version import rover_version
from itertools import (izip, chain, repeat)
from Bio import pairwise2

# proportion of block which must be overlapped by read 
default_minimum_read_overlap_block = 0.9
default_proportion_threshold = 0.05
default_absolute_threshold = 2
default_primer_threshold = 5
default_gap_penalty = 0

def parse_args():
    "Consider mapped reads to amplicon sites"

    parser = ArgumentParser(description="Consider mapped reads to amplicon sites")
    parser.add_argument('--reference', type=str, 
	 help='File name of reference DNA sequence in FASTA format.')
    parser.add_argument(
    '--version', action='version', version='%(prog)s ' + rover_version)
    parser.add_argument(
        '--primers', type=str, required=True,
        help='File name of primer coordinates in TSV format.')
    parser.add_argument(
        '--overlap', type=float, default=default_minimum_read_overlap_block,
        help='Minimum proportion of block which must be overlapped by a read. '
             'Defaults to {}.'.format(default_minimum_read_overlap_block))
    parser.add_argument(
        'bams', nargs='+', type=str, help='bam files containing mapped reads')
    parser.add_argument( '--log', metavar='FILE', type=str,
        help='Log progress in FILENAME, defaults to stdout.')
    parser.add_argument('--out', metavar='FILE', type=str,
        required=True, help='Name of output file containing called variants.')
    parser.add_argument('--proportionthresh', metavar='N', type=float,
        default=default_proportion_threshold,
        help='Keep variants which appear in this proportion of the read pairs for '
             'a given target region, and bin otherwise. '
             'Defaults to {}.'.format(default_proportion_threshold))
    parser.add_argument('--absthresh', metavar='N', type=int,
        default=default_absolute_threshold,
        help='Only keep variants which appear in at least this many read pairs. '
             'Defaults to {}.'.format(default_absolute_threshold))
    parser.add_argument('--qualthresh', metavar='N', type=int,
        help='Minimum base quality score (phred).')
    parser.add_argument('--primercheck', metavar='FILE', type=str, 
	help='Expected base sequences and locations of primers as determined by a primer generating program.')
    parser.add_argument('--primerthresh', metavar='N', type=int, default=default_primer_threshold, 
	help='Maximum allowed variance in base sequence of primers.')
    parser.add_argument('--gap_penalty', metavar='N', type=float, default=default_gap_penalty,
	help='Score deduction on gap in alignment.')
    parser.add_argument('--id_info', type=str, 
	help='File containing rs ID information')
    parser.add_argument('--coverdir',
        required=False,
        help='Directory to write coverage files, defaults to current working directory.')
    return parser.parse_args() 


def get_block_coords(primers_file):
    with open(primers_file) as primers:
        return list(csv.reader(primers, delimiter='\t'))


def get_primer_sequence(primers_coords_file):
    with open(primers_coords_file) as primer_coords:
	return list(csv.reader(primer_coords, delimiter='\t'))


def lookup_reads(min_overlap, bam, chr, start_col, end_col):
    # arguments are in zero-based indices
    total_reads = 0
    overlapping_reads = 0
    read_pairs = {}
    for read in bam.fetch(chr, start_col, end_col+1):
        total_reads += 1
        # only keep reads which overlap with the block region by a certain proportion
        overlap = proportion_overlap(start_col, end_col, read) 
        if overlap > min_overlap:
            overlapping_reads += 1
            if read.qname not in read_pairs:
                read_pairs[read.qname] = [read]
            else:
                read_pairs[read.qname].append(read)
    logging.info("number of reads intersecting block: {}".format(total_reads))
    logging.info("number of reads sufficiently overlapping block: {}".format(overlapping_reads))
    return read_pairs

def get_MD(read):
    for tag, val in read.tags:
        if tag == 'MD':
            return val
    return None

#M   BAM_CMATCH  0
#I   BAM_CINS    1
#D   BAM_CDEL    2
#N   BAM_CREF_SKIP   3
#S   BAM_CSOFT_CLIP  4
#H   BAM_CHARD_CLIP  5
#P   BAM_CPAD    6
#=   BAM_CEQUAL  7
#X   BAM_CDIFF   8

# find all the variants in a single read (SNVs, Insertions, Deletions)
def read_variants(args, name, chr, pos, aligned_bases, cigar, md):
    cigar_orig = cigar
    md_orig = md
    seq_index = 0
    result = []
    context = None    

#    while cigar and seq_index < len(aligned_bases):
#        cigar_code, cigar_segment_extent = cigar[0]
#	if cigar_code == 0:
#	    # Cigar Match
#	    if fasta[ref + 1].upper() == aligned_bases[seq_index].base:
#	    	pos += cigar_segment_extent
#		ref += cigar_segment_extent
#		seq_index += cigar_segment_extent
#		cigar = cigar[1:]
#	    else:
#		seq_base_qual = aligned_bases[seq_index]
#		seq_base = seq_base_qual.base
#		if (args.qualthresh is None) or (seq_base_qual.qual >= args.qualthresh):
#	            result.append(SNV(chr, pos, fasta[ref + 1], seq_base, seq_base_qual.qual, None))
#		else:
#		    result.append(SNV(chr, pos, fasta[ref + 1], seq_base, seq_base_qual.qual, ";qlt"))
#		cigar = [(cigar_code, cigar_segment_extent - 1)] + cigar[1:]
#		seq_index += 1
#		ref += 1
#		pos += 1
#	elif cigar_code == 1:
#	    extra_bases_quals = aligned_bases[(seq_index):(seq_index + cigar_segment_extent)]
#	    extra_bases = ''.join([b.base for b in extra_bases_quals])
#	    context = fasta[ref - 1]
#	    if (args.qualthresh is None) or all([b.qual >= args.qualthresh for b in extra_bases_quals]):
#	        result.append(Insertion(chr, pos, extra_bases, 15, None, context))
#	    else:
#		result.append(Insertion(chr, pos, extra_bases, 15, ";qlt", context))
#	    cigar = cigar[1:]
#	    seq_index += cigar_segment_extent
#	elif cigar_code == 2:
#	    deleted_bases = fasta[ref:(ref + cigar_segment_extent)]
#	    context = fasta[ref - 1]
#	    seq_base = aligned_bases[seq_index]
#	    if seq_base.qual >= args.qualthresh:
#               result.append(Deletion(chr, pos, deleted_bases, 15, None, context))
#	    else:
#		result.append(Deletion(chr, pos, deleted_bases, 15, ";qlt", context))
#	    pos += cigar_segment_extent
#	    ref += cigar_segment_extent
#	    cigar = cigar[1:]
#	else:
#	    logging.info("unexpected cigar code {}".format(cigar_orig))
#	    exit()
#    return result
    
    while cigar and md:
	cigar_code, cigar_segment_extent = cigar[0]
	next_md = md[0]
	if cigar_code == 0:
	    if isinstance(next_md, MD_match):
                # MD match
		if next_md.size >= cigar_segment_extent:
                    next_md.size -= cigar_segment_extent
                    if next_md.size  == 0:
		        md = md[1:]
                    context = aligned_bases[seq_index + cigar_segment_extent - 1].base
		    cigar = cigar[1:]
                    pos += cigar_segment_extent
                    seq_index += cigar_segment_extent
                else:
                    # next_md.size < cigar_segment_extent
                    cigar = [(cigar_code, cigar_segment_extent - next_md.size)] + cigar[1:]
		    context = aligned_bases[seq_index + next_md.size - 1].base
		    md = md[1:]
                    pos += next_md.size
                    seq_index += next_md.size
            elif isinstance(next_md, MD_mismatch):
		# MD mismatch
                seq_base_qual = aligned_bases[seq_index]
                # check if the read base is above the minimum quality score
                if (args.qualthresh is None) or (seq_base_qual.qual >= args.qualthresh):
                    seq_base = seq_base_qual.base
                    result.append(SNV(chr, pos, next_md.ref_base, seq_base, seq_base_qual.qual, None))
		else:
		    seq_base = seq_base_qual.base
		    result.append(SNV(chr, pos, next_md.ref_base, seq_base, seq_base_qual.qual, ";qlt"))
                cigar = [(cigar_code, cigar_segment_extent - 1)] + cigar[1:]
                context = next_md.ref_base
		md = md[1:]
                pos += 1
                seq_index += 1
            elif isinstance(next_md, MD_deletion):
                # MD deletion, should not happen in Cigar match
                logging.info("MD del in cigar match {} {}".format(md_orig, cigar_orig))
                exit()
            else:
                logging.info("unexpected MD code {}".format(md_orig))
                exit()
        elif cigar_code == 1:
	    # Insertion
	    seq_bases_quals = aligned_bases[seq_index:seq_index + cigar_segment_extent]
            seq_bases = ''.join([b.base for b in seq_bases_quals])
            # check that all the bases are above the minimum quality threshold
            if (args.qualthresh is None) or all([b.qual >= args.qualthresh for b in seq_bases_quals]):
                result.append(Insertion(chr, pos, seq_bases, '-', None, context))
	    else:
	        result.append(Insertion(chr, pos, seq_bases, '-', ";qlt", context))
	    cigar = cigar[1:]
            seq_index += cigar_segment_extent
	    # pos does not change
        elif cigar_code == 2:
            # Deletion
            if isinstance(next_md, MD_deletion):
                seq_base = aligned_bases[seq_index]
		if seq_base.qual >= args.qualthresh:
		    result.append(Deletion(chr, pos, next_md.ref_bases, '-', None, context))
                else:
		    result.append(Deletion(chr, pos, next_md.ref_bases, '-', ";qlt", context))
		context = next_md.ref_bases[-1]
		md = md[1:]
                cigar = cigar[1:]
                pos += cigar_segment_extent
                # seq_index does not change
            else:
                logging.info("Non del MD in Del Cigar".format(md_orig, cigar_orig))
                exit()
	elif cigar_code == 4:
	    # soft clipping
	    context = 'S'
	    md = md[1:]
	    cigar = cigar[1:]
	    seq_index += cigar_segment_extent
	elif cigar_code == 5:
	    # hard clipping
	    context = 'H'
	    md = md[1:]
	    cigar = cigar[1:]
	else:	    
	    logging.info("unexpected cigar code {}".format(cigar_orig))
            exit()
    return result


# SAM/BAM files store the quality score of a base as a byte (ascii character)
# in "Qual plus 33 format". So we subtract off 33 from the ascii code
# to get the actual score
# See: http://samtools.sourceforge.net/SAMv1.pdf
# ASCII codes 32 an above are the so-called printable characters, but 32
# is a whitespace character, so SAM uses 33 and above.
def ascii_to_phred(ascii):
    return ord(ascii) - 33

def make_base_seq(name, bases, qualities):
    '''Take a list of DNA bases and a corresponding list of quality scores
    and return a list of Base objects where the base and score are
    paired together.'''
    num_bases = len(bases)
    num_qualities = len(qualities)
    if num_bases <= num_qualities:
        return [Base(b, ascii_to_phred(q)) for (b, q) in izip(bases, qualities)]
    else:
        logging.warning("In read {} fewer quality scores {} than bases {}"
            .format(name, num_qualities, num_bases))
        # we have fewer quality scores than bases
        # pad the end with 0 scores (which is ord('!') - 33)
        return [Base(b, ascii_to_phred(q))
            for (b, q) in izip(bases, chain(qualities, repeat('!')))]

# a DNA base paired with its quality score
class Base(object):
    def __init__(self, base, qual):
        self.base = base # a string
        self.qual = qual # an int
    def as_tuple(self):
        return (self.base, self.qual)
    def __eq__(self, other):
        return self.as_tuple() == other.as_tuple()
    def __str__(self):
        return str(self.as_tuple())
    def __repr__(self):
        return str(self)
    def __hash__(self):
        return hash(self.as_tuple)

class SNV(object):
    # bases are represented just as DNA strings
    def __init__(self, chr, pos, ref_base, seq_base, qual, filter):
        self.chr = chr
        self.pos = pos
	self.ref_base = ref_base
        self.seq_base = seq_base
	self.qual = qual
	self.filter = filter
	self.info = []
    def __str__(self):
        return "S: {} {} {} {}".format(self.chr, self.pos, self.ref_base, self.seq_base)
    def __repr__(self):
        return str(self)
    def as_tuple(self):
        return (self.chr, self.pos, self.ref_base, self.seq_base)
    def __hash__(self):
        return hash(self.as_tuple())
    def __eq__(self, other):
        return self.as_tuple() == other.as_tuple()
    def ref(self):
        return self.ref_base
    def alt(self):
        return self.seq_base
    def fil(self):
	if self.filter is None:
	    return "PASS"
	else:
	    return self.filter[1:]
    def position(self):
	return self.pos
    def quality(self):
	return '.'

class Insertion(object):
    # bases are represented just as DNA strings
    def __init__(self, chr, pos, inserted_bases, qual, filter, context):
        self.chr = chr
        self.pos = pos
        self.inserted_bases = inserted_bases
	self.qual = qual
	self.filter = filter
	self.info = []
	self.context = context
	if self.context == None:
	    self.info.append("BS=T")
	    self.context = '-'
	elif self.context == 'S':
	    self.info.append("SC=T")
	    self.context = '-'
	elif self.context == 'H':
	    self.info.append("HC=T")
	    self.context = '-'
    def __str__(self):
        return "I: {} {} {}".format(self.chr, self.pos, self.inserted_bases)
    def __repr__(self):
        return str(self)
    def as_tuple(self):
        return (self.chr, self.pos, self.inserted_bases)
    def __hash__(self):
        return hash(self.as_tuple())
    def __eq__(self, other):
        return self.as_tuple() == other.as_tuple()
    def ref(self):
        return self.context
    def alt(self):
	return self.context + self.inserted_bases
    def fil(self):
        if self.filter is None:
	    return "PASS"
	else:
	    return self.filter[1:]
    def position(self):
	return self.pos - 1
    def quality(self):
	return '.'

class Deletion(object):
    # bases are represented just as DNA strings
    def __init__(self, chr, pos, deleted_bases, qual, filter, context):
        self.chr = chr
        self.pos = pos
        self.deleted_bases = deleted_bases
	self.qual = qual
	self.filter = filter
	self.info = []
	self.context = context
	if self.context == None:
	    self.info.append("BS=T")
	    self.context = '-'
	elif self.context == 'S':
	    self.info.append("SC=T")
	    self.context = '-'
	elif self.context == 'H':
	    self.info.append("HC=T")
	    self.context = '-'
    def __str__(self):
        return "D: {} {} {}".format(self.chr, self.pos, self.deleted_bases)
    def __repr__(self):
        return str(self)
    def as_tuple(self):
        return (self.chr, self.pos, self.deleted_bases)
    def __hash__(self):
        return hash(self.as_tuple())
    def __eq__(self, other):
        return self.as_tuple() == other.as_tuple()
    def ref(self):
	return self.context + self.deleted_bases
    def alt(self):
	return self.context
    def fil(self):
	if self.filter is None:
	    return "PASS"
	else:
	    return self.filter[1:]
    def position(self):
	return self.pos - 1
    def quality(self):
	return '.'

class MD_match(object):
    def __init__(self, size):
        self.size = size
    def __str__(self):
        return str(self.size)
    def __repr__(self):
        return self.__str__()

class MD_mismatch(object):
    def __init__(self, ref_base):
        self.ref_base = ref_base
    def __str__(self):
        return self.ref_base
    def __repr__(self):
        return self.__str__()

class MD_deletion(object):
    def __init__(self, ref_bases):
        self.ref_bases = ref_bases
    def __str__(self):
        return "^" + self.ref_bases
    def __repr__(self):
        return self.__str__()

# [0-9]+(([A-Z]|\^[A-Z]+)[0-9]+)*
def parse_md(md, result):
    if md:
        number_match = re.match('([0-9]+)(.*)', md)
        if number_match is not None:
            number_groups = number_match.groups()
            number = int(number_groups[0])
            md = number_groups[1]
            return parse_md_snv(md, result + [MD_match(number)])
    return result

def parse_md_snv(md, result):
    if md:
        snv_match = re.match('([A-Z])(.*)', md)
        if snv_match is not None:
            snv_groups = snv_match.groups()
            ref_base = snv_groups[0]
            md = snv_groups[1]
            return parse_md(md, result + [MD_mismatch(ref_base)])
        else:
            return parse_md_del(md, result)
    return result

def parse_md_del(md, result):
    if md:
        del_match = re.match('(\^[A-Z]+)(.*)', md)
        if del_match is not None:
            del_groups = del_match.groups()
            ref_bases = del_groups[0][1:]
            md = del_groups[1]
            return parse_md(md, result + [MD_deletion(ref_bases)])
    return result

def proportion_overlap(block_start, block_end, read):
    '''Compute the proportion of the block that is overlapped by the read

          block_start               block_end
               |-------------------------|

        ^---------------------------^
    read.pos                      read_end

               |--------------------|
         overlap_start        overlap_end

    '''
    read_end = read.pos + read.rlen - 1
    if read.rlen <= 0:
        # read is degenerate, zero length
        # treat it as no overlap
        logging.warn("Degenerate read: {}, length: {}".format(read.qname, read.rlen))
        return 0.0
    if read_end < block_start or read.pos > block_end:
        # they don't overlap
        return 0.0
    else:
        overlap_start = max(block_start, read.pos)
        overlap_end = min(block_end, read_end)
        overlap_size = overlap_end - overlap_start + 1
        block_size = block_end - block_start + 1
        return float(overlap_size) / block_size

def write_variant(file, variant, id_info, args):
    id = 0
    if variant.fil() == "PASS" and args.id_info:
	for record in id_info.fetch(variant.chr, variant.position(), variant.position() + max(len(variant.ref()), len(variant.alt())) + 1):
	    if record.POS == variant.position() and record.REF == variant.ref() and (variant.alt() in record.ALT):
		id = 1
    if id == 1:
	file.write('\t'.join([variant.chr, str(variant.position()), \
str(record.ID), variant.ref(), variant.alt(), variant.quality(), variant.fil(), ';'.join(variant.info)]) + '\n')
    else:
	file.write('\t'.join([variant.chr, str(variant.position()), \
'.', variant.ref(), variant.alt(), variant.quality(), variant.fil(), ';'.join(variant.info)]) + '\n')

def nts(s):
    # Turns None into an empty string
    if s is None:
	return ''
    return str(s)

def reverse_complement(sequence):
    complementary_bases = {"A":"T", "T":"A", "G":"C", "C":"G", "N":"N"}
    rc_bases = []
    for base in sequence:
	rc_bases.append(complementary_bases[str(base)])
    rc = "".join([b for b in rc_bases])
    return rc[::-1]


def possible_primer(primer_sequence, block_info, bases, pos, direction, primerthresh):
    # generates possible primers given the primer sequence and knowledge about where the primer should be located
    forward_primer_end = int(block_info[1]) - pos
    reverse_primer_start = int(block_info[2]) - pos + 1
    forward_primer_length = len(primer_sequence[block_info[3]])
    reverse_primer_length = len(primer_sequence[block_info[4]])

    if direction == -1:
	primer_bases = []
	for primer_base in bases[:forward_primer_end]:
	    primer_bases.append(primer_base.base)
	return "".join([b for b in primer_bases])

    if direction == 1:
	primer_bases = []
	for primer_base in bases[reverse_primer_start:]:
	    primer_bases.append(primer_base.base)
	return "".join([b for b in primer_bases])

#    if direction == -1:
 #       for i in range((-1 * locationthresh), (locationthresh + 1)):
  #          primer_bases = []
   #         for primer_base in bases[(forward_primer_end - forward_primer_length + i):(forward_primer_end + i)]:
#	        primer_bases.append(primer_base.base)    
#	    primers.append("".join([b for b in primer_bases]))
	#if block_info[3] == "XRCC2_X2_F1":
	    #print bases
	   #  print primers
	    #exit()
 #       return primers
  #  elif direction == 1:
#	for i in range((-1 * locationthresh), (locationthresh + 1)):
#	    primer_bases = []
#	    for primer_base in bases[(reverse_primer_start + i):(reverse_primer_start + reverse_primer_length + i)]:
#		primer_bases.append(primer_base.base)
#	    primers.append("".join([b for b in primer_bases]))
	#if block_info[4] == "XRCC2_X2_R1":
	    #print bases
	 #   print primers
	    #exit()
#	return primers

def primer_diff(primer1, primer2, gap_penalty):
    # compares two primers (in string representation)
    #print pairwise2.align.globalxx(primer1, primer2, score_only=1)
    score = pairwise2.align.globalxs(primer1, primer2, gap_penalty, 0, score_only=1)
    if isinstance(score, float):
	return len(primer1) - score
    else:
	return len(primer1)
    #return len(primer1) - pairwise2.align.globalxx(primer1, primer2, score_only=1)
	#if alignment != []:
	 #   return len(primer1) - alignment[2]
    #return len(primer1)


def check_primers(primer_sequence, block_info, bases, pos, primerthresh, gap_penalty):
    # checks if the primer sequence in the read is what we expect it to be, and return scores for the forward and reverse
    # primers indicating how far away they are from the expected
    ref_primer_forward = primer_sequence[block_info[3]]
    ref_primer_reverse = primer_sequence[block_info[4]]
    forward_primer_region = possible_primer(primer_sequence, block_info, bases, pos, -1, primerthresh)
    reverse_primer_region = possible_primer(primer_sequence, block_info, bases, pos, 1, primerthresh)
    
    forward_var = 1
    reverse_var = 1

    forward_score = primer_diff(ref_primer_forward, forward_primer_region, gap_penalty)
    reverse_score = primer_diff(ref_primer_reverse, reverse_complement(reverse_primer_region), gap_penalty)
    return [forward_score, reverse_score]

   # if primer_diff(ref_primer_forward, forward_primer_region) <= basethresh:
#	forward_var = 0
 #   if primer_diff(ref_primer_reverse, reverse_complement(reverse_primer_region)) <= basethresh:
#	reverse_var = 0
    #if block_info[3] == "PALB2_X3_F2":
#	print forward_var, reverse_var
 #   if forward_var == 0 and reverse_var == 0:
#	return 0
 #   else:
#	return 1

def printable_base(bases):
    # correct plurality
    if bases == 1:
	return "base"
    else:
	return "bases"

def process_blocks(args, kept_variants_file, bam, sample, block_coords, primer_sequence, data, data2, id_info):
    coverage_info = []
    total_scores = {}
    for block_info in block_coords:
        chr, start, end = block_info[:3]
	start = int(start)
        end = int(end)
	logging.info("processing block chr: {}, start: {}, end: {}".format(chr, start, end))
        # process all the reads in one block
        block_vars = {}
        num_pairs = 0
	num_discards = 0
	# num_primer_vars = 0
	scores = {}
	# use 0 based coordinates to lookup reads from bam file
        read_pairs = lookup_reads(args.overlap, bam, chr, start - 1, end - 1)
	for read_name, reads in read_pairs.items():
            if len(reads) == 1:
                logging.warning("read {} with no pair".format(read_name))
            elif len(reads) == 2:
		num_pairs += 1
                read1, read2 = reads
                #print(read1.query)
                #print([ord(x) - 33 for x in read1.qqual])
                #print(read2.query)
                #print([ord(x) - 33 for x in read2.qqual])
                #exit()
                read1_bases = make_base_seq(read1.qname, read1.query, read1.qqual)
                read2_bases = make_base_seq(read2.qname, read2.query, read2.qqual)
		
		#if args.primercheck:
		    #if check_primer_pair(primer_sequence, block_info, read1_bases, read2_bases, read1.pos + 1, \
		#		read2.pos + 1, args.primerthresh, args.primerlocationthresh) > 0:
		#	num_primer_diff += 1
		 #   read1_check = check_primers(primer_sequence, block_info, read1_bases, read1.pos + 1, args.primerthresh, \
		#		args.primerlocationthresh)
		 #   read2_check = check_primers(primer_sequence, block_info, read2_bases, read2.pos + 1, args.primerthresh, \
		#		args.primerlocationthresh)
		    # print read1_check, read2_check
		 #   if read1_check > 0 or read2_check > 0:
		#	num_primer_vars += 1

		variants1 = read_variants(args, read1.qname, chr, read1.pos + 1, read1_bases, read1.cigar, parse_md(get_MD(read1), []))
                variants2 = read_variants(args, read2.qname, chr, read2.pos + 1, read2_bases, read2.cigar, parse_md(get_MD(read2), []))
                set_variants1 = set(variants1)
                set_variants2 = set(variants2)
                # find the variants each read in the pair share in common
                same_variants = set_variants1.intersection(set_variants2)
		if args.primercheck:
		    read1_check = check_primers(primer_sequence, block_info, read1_bases, read1.pos + 1, args.primerthresh, args.gap_penalty)
		    read2_check = check_primers(primer_sequence, block_info, read2_bases, read2.pos + 1, args.primerthresh, args.gap_penalty)
		    forward_score = max(read1_check[0], read2_check[0])
		    reverse_score = max(read1_check[1], read2_check[1])
		    if forward_score in scores:
			scores[forward_score] += 1
		    else:
			scores[forward_score] = 1
		    if forward_score in total_scores:
			total_scores[forward_score] += 1
		    else:
			total_scores[forward_score] = 1
		    if reverse_score in scores:
			scores[reverse_score] += 1
		    else:
			scores[reverse_score] = 1
		    if reverse_score in total_scores:
			total_scores[reverse_score] += 1
		    else:
			total_scores[reverse_score] = 1
		discard = 0
		if args.primercheck:
		    if forward_score > args.primerthresh or reverse_score > args.primerthresh:
			discard = 1
		if discard == 0:
		    for var in same_variants:
                        # only consider variants within the bounds of the block
                        if var.pos >= start and var.pos <= end:
                            # check here if the score is below a certain threshold, if not don't record it
			    # also decrement num_pairs, since we are ignoring this read pair
			    if var in block_vars:
                                block_vars[var] += 1
                            else:
                                block_vars[var] = 1
		else:
		    logging.warning("read {} discarded due to greater than acceptable variance in primer sequence".format(read_name))
		    num_pairs -= 1
		    num_discards += 1
	    else:
                logging.warning("read {} with more than 2".format(read_name))
	if args.primercheck:
	    logging.warning("number of reads discarded due to unexpected primer sequence: {}".format(num_discards * 2))
        logging.info("number of read pairs in block: {}".format(num_pairs))
        logging.info("number of variants found in block: {}".format(len(block_vars)))

	if args.primercheck:
	    data.write("# " + block_info[3] + '\n')

	if args.primercheck:
	    # print '\n' + block_info[3], int(block_info[1]) - len(primer_sequence[block_info[3]]), primer_sequence[block_info[3]]	
	    # print block_info[4], int(block_info[2]) + 1, reverse_complement(primer_sequence[block_info[4]])
	    total = sum(scores.values())
	    for mismatch in sorted(scores):
		# print mismatch, scores[mismatch]
		# print "Percentage of primers " + str("{:g}".format(mismatch)) + " mismatched bases away from expected sequence: \
# {:.2%}".format(scores[mismatch]/float(total))
		if mismatch < 10:
		    data.write(str(mismatch) + '\t' + "{:.2%}".format(scores[mismatch]/float(total)) + '\n')
	if args.primercheck:
	    data.write("\n\n")

	#if args.primercheck:
	 #   print block_info[3], int(block_info[1]) - len(primer_sequence[block_info[3]]), primer_sequence[block_info[3]]
	  #  print block_info[4], int(block_info[2]) + 1, reverse_complement(primer_sequence[block_info[4]])
	   # print "Percentage of read pairs with primers differing by " + str(args.primerthresh) + \
	#	" " + printable_base(args.primerthresh) + " or less from expected sequence " + str(args.primerlocationthresh) \
#+ " or less away from expected location: {:.2%}".format((float(num_pairs) - float(num_primer_vars))/(num_pairs)) + '\n'
	    #print "Percentage of read pairs with primers differing by more than " + str(args.primerthresh) + \
	#	" " + printable_base(args.primerthresh) + " from each other or more than " + str(args.primerlocationthresh) + " away \
#from expected location: {:.2%}".format(float(num_primer_diff)/(num_pairs)) + '\n'	

	for var in block_vars:
            num_vars = block_vars[var]
            proportion = float(num_vars) / num_pairs
            proportion_str = "{:.2f}".format(proportion)
  	    var.info.append("Sample=" + str(sample))
	    var.info.append("NV=" + str(num_vars))
	    var.info.append("NP=" + str(num_pairs))
	    var.info.append("PCT=" + str('{:.2%}'.format(proportion)))
	    if num_vars < args.absthresh:
		var.filter = ''.join([nts(var.filter), ";at"])
	    if proportion < args.proportionthresh:
		var.filter = ''.join([nts(var.filter), ";pt"])
	    write_variant(kept_variants_file, var, id_info, args)
        coverage_info.append((chr, start, end, num_pairs))
    coverage_filename = sample + '.coverage'
    
    if args.primercheck:
	total2 = sum(total_scores.values())
	for mismatch in sorted(total_scores):
	    if mismatch < 10:
		data2.write(str(mismatch) + '\t' + "{:.2%}".format(total_scores[mismatch]/float(total2)) + '\n')

    if args.coverdir is not None:
        coverage_filename = os.path.join(args.coverdir, coverage_filename)
    with open(coverage_filename, 'w') as coverage_file:
        coverage_file.write('chr\tblock_start\tblock_end\tnum_pairs\n')
        for chr, start, end, num_pairs in sorted(coverage_info, key=itemgetter(3)):
            coverage_file.write('{}\t{}\t{}\t{}\n'.format(chr, start, end, num_pairs))

def write_metadata(args, file):
    file.write("##fileformat=VCFv4.2" + '\n')
    today = datetime.date.today()
    file.write("##fileDate=" + str(today)[:4] + str(today)[5:7] + str(today)[8:] + '\n')
    file.write("##source=ROVER-PCR Variant Caller" + '\n')
    if args.reference:
	file.write("##reference=file:///" + str(args.reference) + '\n')
    # file.write("##contig=" + '\n')
    # file.write("##phasing=" + '\n')
    file.write("##INFO=<ID=Sample,Number=1,Type=String,Description=\"Sample Name\">" + '\n')
    file.write("##INFO=<ID=NV,Number=1,Type=Float,Description=\"Number of read pairs with variant\">" + '\n')
    file.write("##INFO=<ID=NP,Number=1,Type=Float,Description=\"Number of read pairs at POS\">" + '\n')
    file.write("##INFO=<ID=PCT,Number=1,Type=Float,Description=\"Percentage of read pairs at POS with variant\">" + '\n')
    file.write("##INFO=<ID=BS,Number=1,Type=String,Description=\"Context base cannot be determined as indel is located near a region not covered by the MD string\">" + '\n')
    file.write("##INFO=<ID=HC,Number=1,Type=String,Description=\"Context base cannot be determined due to \
hard clipping on the aligned sequence prior to indel event\">" + '\n')
    file.write("##INFO=<ID=SC,Number=1,Type=String,Description=\"Context base cannot be determined due to \
soft clipping on the aligned sequence prior to indel event\">" + '\n')
    file.write("##INFO=<ID=IP,Number=1,Type=String,Description=\"Misaligned or incorrect base sequence in primer region\">" + '\n')
    if args.qualthresh: 
        file.write("##FILTER=<ID=qlt,Description=\"Variant has phred quality score below " + str(args.qualthresh) + "\">" + '\n')
    if args.absthresh:
	file.write("##FILTER=<ID=at,Description=\"Variant does not appear in at least " + str(args.absthresh) + " read pairs\">" + '\n')
    if args.proportionthresh:
	file.write("##FILTER=<ID=pt,Descroption=\"Variant does not appear in at least " + str(args.proportionthresh*100) \
		+ "% of read pairs for the given region\">" + '\n')

# Extra formatting applied to column headings so that everything lines up
# output_header = '\t'.join(["#CHROM", "POS", '', "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"])

# Proper tab separated column headings
output_header = '\t'.join(["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"])

def process_bams(args):
    block_coords = get_block_coords(args.primers)
    primer_sequence = {}
    vcf_reader = 0
    # a dictionary of primers and their sequences
    if args.primercheck:
	primer_info = get_primer_sequence(args.primercheck)
	for primer in primer_info:
	    primer_sequence[primer[0]] = primer[1]
    # with open(args.out, "w") as kept_variants_file, \
    #      open(args.out + '.binned', "w") as binned_variants_file:
    with open(args.out, "w") as kept_variants_file:
	graph_data = open("data.dat", "w")
	graph_total_data = open("data2.dat", "w")
	write_metadata(args, kept_variants_file)
	if args.id_info:
	    vcf_reader = vcf.Reader(filename=args.id_info)
	# write_metadata(args, binned_variants_file)
	kept_variants_file.write(output_header + '\n')
        # binned_variants_file.write(output_header + '\n')
	# ref_dict = Fasta(args.reference)
	for bam_filename in args.bams:
            base = os.path.basename(bam_filename)
            sample = base.split('.')
            if len(sample) > 0:
                sample = sample[0]
            else:
                exit('Cannot deduce sample name from bam filename {}'.format(bam_filename))
            with pysam.Samfile(bam_filename, "rb") as bam:
                logging.info("processing bam file {}".format(bam_filename))
                process_blocks(args, kept_variants_file, bam, sample, block_coords, primer_sequence, graph_data, graph_total_data, vcf_reader)

def main():
    args = parse_args()
    if args.log is None:
        logfile = sys.stdout
    else:
        logfile = args.log
    logging.basicConfig(
        filename=args.log,
        level=logging.DEBUG,
        filemode='w',
        format='%(asctime)s %(message)s',
        datefmt='%m/%d/%Y %H:%M:%S')
    logging.info('program started')
    logging.info('command line: {0}'.format(' '.join(sys.argv)))
    process_bams(args)



if __name__ == '__main__':
    main()
