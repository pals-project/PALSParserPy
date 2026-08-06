"""Tests of match_names: PALS name matching."""

import pytest

from palsparserpy import is_map, match_names, node_key, parse_string

# A self-contained two-lattice lattice: constants/variables at the top, elements
# with ungrouped (`length`) and grouped (`BendP.e1`) parameters, a sub-line
# (sub/S1), and a repeated element name (B1a in both lattices) to exercise `>>>`.
MATCH_LATTICE = """
PALS:
  facility:
    - constants:
        - a_const: 0.3 * r_electron
        - a_two: 5
    - my_var:
        kind: variable
        value: 37
    - lat1:
        kind: Lattice
        branches:
          - main:
              kind: BeamLine
              line:
                - B1a:
                    kind: Bend
                    length: 1.2
                    BendP:
                      e1: 0.1
                      g_ref: 0.02
                - B1b:
                    kind: Bend
                    length: 1.5
                    BendP:
                      e1: 0.3
                - Q1:
                    kind: Quadrupole
                    length: 0.5
                - sub:
                    kind: BeamLine
                    line:
                      - S1:
                          kind: Sextupole
                          length: 0.2
    - lat2:
        kind: Lattice
        branches:
          - other:
              kind: BeamLine
              line:
                - B1a:
                    kind: Bend
                    length: 9.9
"""


@pytest.fixture(scope="module")
def root():
    return parse_string(MATCH_LATTICE)


def test_constants_and_variables_bare_name(root):
    m = match_names(root, "a_const")
    assert len(m) == 1
    assert node_key(m[0]) == "a_const"
    assert m[0].value == "0.3 * r_electron"

    m = match_names(root, "a_.*")
    assert {node_key(n) for n in m} == {"a_const", "a_two"}

    m = match_names(root, "my_var")                 # full form -> named map node
    assert len(m) == 1
    assert node_key(m[0]) == "my_var"
    assert is_map(m[0])

    assert match_names(root, "a") == []             # anchored whole-name match


def test_bare_name_matches_the_elements_themselves(root):
    m = match_names(root, "B1a")                    # in both lattices
    assert len(m) == 2
    assert all(node_key(n) == "B1a" and is_map(n) for n in m)


def test_element_parameters(root):
    m = match_names(root, "B1.*>BendP.e1")          # lat1's B1a, B1b
    assert {n.value for n in m} == {"0.1", "0.3"}

    m = match_names(root, "B1a>length")             # both lattices
    assert {n.value for n in m} == {"1.2", "9.9"}

    assert len(match_names(root, ">length")) == 5   # every element

    m = match_names(root, ">BendP.g_ref")
    assert len(m) == 1
    assert node_key(m[0]) == "g_ref"

    m = match_names(root, "B1a>BendP")              # drop parameter -> group node
    assert len(m) == 1
    assert node_key(m[0]) == "BendP"
    assert is_map(m[0])


def test_kind_restriction(root):
    m = match_names(root, "Quadrupole::.*>length")
    assert len(m) == 1
    assert m[0].value == "0.5"

    assert len(match_names(root, "Bend::B1a>length")) == 2
    assert match_names(root, "Sextupole::B1a>length") == []


def test_branch_filter_includes_sub_lines(root):
    assert len(match_names(root, "main>>B1.*>length")) == 2

    m = match_names(root, "main>>S1>length")        # S1 is in sub-line of main
    assert len(m) == 1
    assert m[0].value == "0.2"

    assert match_names(root, "nobranch>>B1.*>length") == []


def test_lattice_qualifier(root):
    m = match_names(root, "lat1>>>B1a>length")
    assert len(m) == 1
    assert m[0].value == "1.2"

    m = match_names(root, "lat2>>>B1a>length")
    assert len(m) == 1
    assert m[0].value == "9.9"


def test_non_matches_and_bad_patterns(root):
    assert match_names(root, "nosuch>foo") == []
    assert match_names(root, "B1a>BendP.nope") == []
    assert match_names(root, "(unclosed") == []


def test_returned_nodes_belong_to_the_searched_tree(root):
    m = match_names(root, "lat1>>>B1a>length")
    assert m[0].tree is root.tree
