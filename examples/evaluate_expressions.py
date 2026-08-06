"""Evaluating the mathematical expressions in a PALS lattice.

parse_and_expand_pals evaluates every expression to a number, drawing on built-in
physical constants, math and particle-data functions, and any constants/variables
the lattice defines. This happens across the expanded views (`expanded` and
`full_expanded`, which hold the same values) and `adjunct`; the `original` and
`combined` views keep the expression text as written.

evaluate_pals_expression evaluates a single expression string on its own.
"""

import os
import sys

# So the examples run from a checkout without installing it first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palsparserpy as pp                                    # noqa: E402

ex_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "lattice_files", "ex.pals.yaml")

lat = pp.parse_and_expand_pals(ex_file)

# -- combined keeps the source text; the evaluated number is downstream of it --
# Constants and variables are not part of the lattice, so they are `adjunct`.
consts_c = lat.combined["PALS"]["facility"][0]["constants"]
consts_e = lat.adjunct["PALS"]["facility"][0]["constants"]
vars_e = lat.adjunct["PALS"]["facility"][1]["variables"]

print("a_const  as written :", consts_c["a_const"].value)   # 0.3 * r_electron
print("a_const  evaluated  :", consts_e["a_const"].value)   # a number
# a_var references the constant a_const defined above it.
print("a_var    evaluated  :", vars_e["a_var"].value, " (= a_const^2)\n")

# -- an element parameter written as an expression is evaluated too ------------
q1a_length = pp.match_names(lat.full_expanded, "Q1a>length")  # 1.03 * pi / c_light
if q1a_length:
    print("Q1a length evaluated:", q1a_length[0].value,
          " (= 1.03 * pi / c_light)\n")

# -- evaluating a single expression on its own ---------------------------------
# Built-in constants and math functions:
print("3.75e7 / c_light^2   =", pp.evaluate_pals_expression("3.75e7 / c_light^2"))
# Particle-data functions take a *quoted* species name:
print('mass_of("electron") =', pp.evaluate_pals_expression('mass_of("electron")'))
# A leading expr(...) wrapper is accepted:
print("expr(2 * pi)         =", pp.evaluate_pals_expression("expr(2 * pi)"))

# Non-evaluable strings raise ValueError -- e.g. an unquoted species name, or a
# deferred random_gauss(). Guard with try/except if the input is untrusted:
try:
    pp.evaluate_pals_expression("mass_of(electron)")   # unquoted -> error
except ValueError as err:
    print("\nunquoted species name is rejected:", err)
