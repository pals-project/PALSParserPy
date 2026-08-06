"""Tests of parameter_value: reading one parameter out of an expanded lattice."""

import pytest

from palsparserpy import parameter_value, parse_and_expand_pals

# A single expandable lattice. Its branch line inline-defines the elements (so they
# are realised in the expanded tree), with a plain-number parameter, an expression
# parameter (to show it comes back evaluated, i.e. from `expanded`, not from the
# raw views), and a non-numeric one. Two quads with different Bn1 exercise the
# conflict case. Facility-level constants and a variable live in the adjunct tree.
PARAM_LATTICE = """
PALS:
  facility:
    - constants:
        - a_two: 5
        - a_expr: 0.3 * 5
    - my_var:
        kind: variable
        value: 37
    - ring:
        kind: Lattice
        branches:
          - main:
              kind: BeamLine
              line:
                - q1:
                    kind: Quadrupole
                    length: 0.5
                    MagneticMultipoleP:
                      Bn1: 2 * 0.6
                - q2:
                    kind: Quadrupole
                    MagneticMultipoleP:
                      Bn1: -1.0
                - f1:
                    kind: Foil
                    ReferenceP:
                      species_ref: "#3He"
    - use: ring
"""


@pytest.fixture(scope="module")
def pv(tmp_path_factory):
    path = tmp_path_factory.mktemp("params") / "params.pals.yaml"
    path.write_text(PARAM_LATTICE)
    lat = parse_and_expand_pals(path, problems="none")
    return lambda s: parameter_value(lat, s)


def test_element_parameters_come_from_the_expanded_lattice(pv):
    assert pv("q1>length") == 0.5
    # 2 * 0.6 comes back evaluated (1.2), proving the value is read from
    # `expanded` and not from the raw `original`/`combined` views.
    assert pv("q1>MagneticMultipoleP.Bn1") == 1.2
    assert pv("q2>MagneticMultipoleP.Bn1") == -1.0


def test_non_numeric_values_stay_strings(pv):
    assert pv("f1>ReferenceP.species_ref") == "#3He"
    assert pv("q1>kind") == "Quadrupole"


def test_unset_parameters_return_the_default(pv):
    assert pv("q1>BendP.g") == 0.0          # element found, parameter absent
    assert pv("q1>not_a_param") == 0.0      # no schema: unknown == unset -> 0


def test_constants_and_variables_fall_through_to_adjunct(pv):
    assert pv("a_two") == 5.0               # compact-form constant
    assert pv("a_expr") == 1.5              # evaluated in adjunct during expansion
    assert pv("my_var") == 37.0             # full-form variable


def test_unidentifiable_lookups_return_none(pv):
    assert pv("nosuch>length") is None      # in neither view
    assert pv("q1") is None                 # a bare element is not a value
    assert pv("q1>MagneticMultipoleP") is None   # a group, not a single value
    assert pv("(unclosed>length") is None   # malformed pattern
    assert pv("nosuch_const") is None


def test_agreeing_matches_collapse_conflicts_are_none(pv):
    assert pv("q.>MagneticMultipoleP.Bn1") is None   # 1.2 vs -1.0 conflict
    assert pv("q.>kind") == "Quadrupole"             # both quads agree
