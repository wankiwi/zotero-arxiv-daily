"""Explicit first-time ledger setup; no credentials or paid requests are used."""
import argparse
from zot2dailypaper.budget import bootstrap_ledger

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize-new-ledger',action='store_true',required=True,
                        help='Initialize only if the ledger has never existed on paper-state')
    parser.parse_args()
    bootstrap_ledger()
    print('Initialized budget ledger; existing delivery history preserved. No model request made.')
