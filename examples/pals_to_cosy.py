"""Translate one expanded branch; run from a source checkout."""

import argparse
from pathlib import Path
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import palsparserpy as pp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--branch")
    parser.add_argument("--order", type=int, default=3)
    parser.add_argument("--fr", type=int, choices=range(4), default=0)
    parser.add_argument("--strict", action="store_true", help="Fail on model or expansion warnings")
    args = parser.parse_args()
    with warnings.catch_warnings():
        if args.strict:
            warnings.simplefilter("error", pp.CosyConversionWarning)
        lattice = pp.parse_and_expand_pals(args.input, problems="none")
        program = pp.pals_to_cosy(lattice, branch_name=args.branch,
                                  computation_order=args.order, fringe_field_order=args.fr)
    pp.write_cosy_file(program, args.output)


if __name__ == "__main__":
    main()
