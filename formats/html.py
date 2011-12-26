#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Parse html pages.
"""

import sys
import logging

from optparse import OptionParser
from BeautifulSoup import BeautifulSoup

from jcvi.apps.base import ActionDispatcher, debug
debug()


def main():

    actions = (
        ('csv', 'convert HTML tables to csv'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def unescape(s, unicode_action="replace"):
    """
    Unescape HTML strings, and convert &amp; etc.
    """
    import HTMLParser
    hp = HTMLParser.HTMLParser()
    s = hp.unescape(s)
    s = s.encode('ascii', unicode_action)
    s = s.replace("\n", "").strip()
    return s


def csv(args):
    """
    %prog csv page.html

    Convert HTML tables to csv.
    """
    import csv

    p = OptionParser(csv.__doc__)
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    htmlfile, = args
    page = open(htmlfile).read()

    soup = BeautifulSoup(page)
    table = soup.find('table')
    rows = table.findAll('tr')
    csvfile = htmlfile.rsplit(".", 1)[0] + ".csv"
    writer = csv.writer(open(csvfile, "w"))

    nrows = 0
    for tr in rows:
        cols = tr.findAll('td')
        if not cols:
            cols = tr.findAll('th')

        row = []
        for td in cols:
            try:
                cell = "".join(td.find(text=True))
                cell = unescape(cell)
            except TypeError:
                cell = ""
            row.append(cell)
        writer.writerow(row)
        nrows += 1

    logging.debug("Table with {0} rows written to `{1}`.".format(nrows, csvfile))


if __name__ == '__main__':
    main()