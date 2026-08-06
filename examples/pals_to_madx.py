"""Produces a file "PALSParserPy/lattice_files/bta.pals_out.madx"."""

import os
import sys

# So the examples run from a checkout without installing it first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palsparserpy as pp                                    # noqa: E402

pals_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ex_file = os.path.join(pals_dir, "lattice_files", "bta.pals.yaml")
out_file = os.path.join(pals_dir, "lattice_files", "bta.pals_out.madx")

pp.write_madx_file(pp.pals_to_madx(pp.parse_file(ex_file)), out_file)
