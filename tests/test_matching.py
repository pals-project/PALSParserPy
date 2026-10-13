"""Tests of match_names: the binding to PALS name matching.

The semantics of name matching are tested in PALSParserCpp; this checks that
strings reach the C matcher and the nodes come back attached to the tree that
was searched.
"""

from palsparserpy import (is_map, match_names, node_key, parse_and_expand_pals,
                          parse_string)

MATCH_LATTICE = """
PALS:
  facility:
    - constants:
        - a_const: 0.3 * r_electron
        - a_two: 5
    - start:
        kind: BeginningEle
        ReferenceP:
          species_ref: electron
          E_tot_ref: 1e9
    - B1a:
        kind: Bend
        length: 1.2
        BendP:
          e1: 0.1
          g_ref: 0.02
    - Q1:
        kind: Quadrupole
        length: 0.5
    - main:
        kind: BeamLine
        line:
          - start
          - B1a
          - Q1
          - B1a
    - lat1:
        kind: Lattice
        branches:
          - main
"""


def _expanded(tmp_path):
    path = tmp_path / "match.pals.yaml"
    path.write_text(MATCH_LATTICE)
    return parse_and_expand_pals(str(path), problems="none")


def test_constants_by_bare_name():
    root = parse_string(MATCH_LATTICE)
    m = match_names(root, "a_const")
    assert len(m) == 1
    assert node_key(m[0]) == "a_const"
    assert m[0].value == "0.3 * r_electron"
    assert {node_key(n) for n in match_names(root, "a_.*")} == {"a_const", "a_two"}


def test_definitions_before_expansion():
    root = parse_string(MATCH_LATTICE)
    m = match_names(root, "B1a>length")
    assert [n.value for n in m] == ["1.2"]
    m = match_names(root, "main>>Q1")
    assert [n.value for n in m] == ["Q1"]


def test_elements_of_the_expanded_lattice(tmp_path):
    lat = _expanded(tmp_path)
    m = match_names(lat.full_expanded, "B1a")
    assert len(m) == 2
    assert all(node_key(n) == "B1a" and is_map(n) for n in m)
    assert [n.value for n in match_names(lat.full_expanded, "B1a#2>element_index")] \
        == ["4"]
    assert len(match_names(lat.full_expanded, "B1a#1:Q1")) == 2
    assert match_names(lat.full_expanded, "(unclosed") == []


def test_returned_nodes_belong_to_the_searched_tree(tmp_path):
    lat = _expanded(tmp_path)
    m = match_names(lat.full_expanded, "Q1>length")
    assert m[0].tree is lat.full_expanded.tree
