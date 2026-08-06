"""Tests of node_correspondence: mapping one logical node across the
derivation-chain trees."""

import pytest

from palsparserpy import (evaluate_pals_expression, node_correspondence,
                          parse_and_expand_pals, parse_string)

# A self-contained lattice: `a_const` sits outside the expanded lattice (so it is
# left over rather than expanded), while `repeat: 3` exercises the one-to-many
# correspondence produced by expansion.
CORR_LATTICE = """
PALS:
  facility:
    - constants:
        a_const: 0.3 * r_electron
    - d1:
        kind: Drift
        length: 2.0
    - cell:
        kind: BeamLine
        line:
          - d1
    - main_line:
        kind: BeamLine
        line:
          - cell:
              repeat: 3
    - lat1:
        kind: Lattice
        branches:
          - main_line
    - use: "lat1"
"""


@pytest.fixture(scope="module")
def corr(tmp_path_factory):
    path = tmp_path_factory.mktemp("corr") / "corr.pals.yaml"
    path.write_text(CORR_LATTICE)
    lat = parse_and_expand_pals(path)
    return lat, node_correspondence(lat)


def _a_const(lat):
    return lat.combined["PALS"]["facility"][0]["constants"]["a_const"]


def test_returns_a_dict_keyed_by_node(corr):
    lat, mapping = corr
    assert isinstance(mapping, dict)
    assert mapping
    # The combined, expanded and adjunct roots are keys.
    assert lat.combined in mapping
    assert lat.full_expanded in mapping
    assert lat.adjunct in mapping


def test_a_node_outside_the_lattice_maps_into_adjunct_not_expanded(corr):
    # a_const is not part of the lattice and nothing in it refers to a_const, so
    # expansion leaves it behind: one node in original, combined and adjunct, and
    # none in expanded.
    lat, mapping = corr
    entry = mapping[_a_const(lat)]
    assert len(entry.original) == 1
    assert len(entry.combined) == 1
    assert len(entry.adjunct) == 1
    assert entry.full_expanded == []
    assert entry.original[0].value == "0.3 * r_electron"
    assert entry.combined[0].value == "0.3 * r_electron"
    # The adjunct copy has its expression evaluated to a number, while the
    # original/combined copies keep the original expression text.
    assert entry.adjunct[0].as_float() == \
        evaluate_pals_expression("0.3 * r_electron")
    # The queried node appears in its own tree's list.
    assert entry.combined[0] == _a_const(lat)


def test_lookup_is_consistent_from_any_tree(corr):
    lat, mapping = corr
    entry = mapping[_a_const(lat)]
    # Reaching the class from the original or adjunct node gives the same set.
    assert mapping[entry.original[0]] == entry
    assert mapping[entry.adjunct[0]] == entry


def test_repeat_gives_one_to_many_correspondence(corr):
    # The single `d1` scalar in cell's line is unrolled 3x inside the expanded
    # lattice, so it corresponds to several expanded nodes but one combined.
    lat, mapping = corr
    cell_d1 = lat.combined["PALS"]["facility"][2]["cell"]["line"][0]
    entry = mapping[cell_d1]
    assert len(entry.combined) == 1
    assert len(entry.full_expanded) >= 3
    # Every expanded copy resolves back to this same class.
    assert all(mapping[n] == entry for n in entry.full_expanded)
    # The definition it was expanded from is still standing in adjunct, and
    # belongs to the same class.
    assert len(entry.adjunct) == 1
    assert mapping[entry.adjunct[0]] == entry


def test_a_definition_used_by_the_lattice_reaches_both_trees(corr):
    # main_line is named by lat1's branches, so expansion inlines a copy of its
    # definition into the lattice while the definition itself stays in adjunct.
    # The combined node ties the two sides together. `line` is the node to follow,
    # not `kind`: inlining main_line made it a branch, and a branch has no kind, so
    # no expanded node answers to main_line's.
    lat, mapping = corr
    ml = lat.combined["PALS"]["facility"][3]["main_line"]
    assert mapping[ml["kind"]].full_expanded == []

    entry = mapping[ml["line"]]
    assert len(entry.combined) == 1
    assert len(entry.adjunct) == 1
    assert len(entry.full_expanded) == 1
    # The two copies are the same node of the same definition, but only the
    # expanded one has been expanded: `cell: repeat: 3` is unrolled to 3 entries
    # there, while the definition in adjunct still holds the 1 entry it was
    # written with. Expansion then caps the branch with a zero-length `branch_end`
    # Placeholder holding its final floor placement and reference parameters, so
    # the expanded line is one longer than the unrolling.
    assert len(entry.adjunct[0]) == 1
    assert [n.child(0).node_key() for n in entry.full_expanded[0]] == \
        ["d1", "d1", "d1", "branch_end"]
    # Both copies resolve back to the same class.
    assert mapping[entry.full_expanded[0]] == entry
    assert mapping[entry.adjunct[0]] == entry


def test_unmapped_nodes_are_absent_from_the_dict(corr):
    # A freshly built, unrelated tree shares no nodes with the correspondence.
    _, mapping = corr
    assert parse_string("stray: node") not in mapping
