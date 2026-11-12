"""Tests of node_correspondence: mapping one logical node across the
derivation-chain trees."""

import pytest

from palsparserpy import (controller_expressions, evaluate_pals_expression,
                          get_parent, node_correspondence, node_key,
                          original_node, parse_and_expand_pals, parse_string)

# A self-contained lattice: `a_const` sits outside the expanded lattice (so it is
# left over rather than expanded), while `repeat: 3` exercises the one-to-many
# correspondence produced by expansion.
CORR_LATTICE = """
PALS:
  facility:
    - constants:
        - a_const: 0.3 * r_electron
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
    return lat.combined["PALS"]["facility"][0]["constants"][0]["a_const"]


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


# ─── original_node ──────────────────────────────────────────────────────────

SOURCE_LATTICE = """
PALS:
  facility:
    - q1:
        kind: Quadrupole
        MagneticMultipoleP:
          Kn1: 0.5
    - q2:
        kind: Quadrupole
        MagneticMultipoleP:
          Kn1: expr(2 * q1>MagneticMultipoleP.Kn1)
    - q3:
        kind: Quadrupole
        MagneticMultipoleP:
          Kn1: 0.1
    - set:
        parameter: q3>MagneticMultipoleP.Kn1
        value: expr(3 * q1>MagneticMultipoleP.Kn1)
    - ln:
        kind: BeamLine
        line:
          - begin:
              kind: BeginningEle
              ReferenceP: {species_ref: electron, E_tot_ref: 1.0e9}
          - q1
          - q2
          - q3
    - lat1:
        kind: Lattice
        branches:
          - ln
    - use: lat1
"""


@pytest.fixture
def source_lat(tmp_path):
    path = tmp_path / "source.pals.yaml"
    path.write_text(SOURCE_LATTICE)
    return parse_and_expand_pals(str(path))


@pytest.mark.parametrize("view", ["expanded", "full_expanded"])
def test_original_node_leads_to_the_text_a_value_came_from(source_lat, view):
    line = getattr(source_lat, view)["lat1"]["branches"][0]["ln"]["line"]

    def source(i, name):
        node = line[i][name]["MagneticMultipoleP"]["Kn1"]
        return original_node(source_lat, node)

    assert source(1, "q1").value == "0.5"
    assert source(2, "q2").value == "expr(2 * q1>MagneticMultipoleP.Kn1)"
    # Written by a set: the set's value, not the 0.1 it replaced.
    assert source(3, "q3").value == "expr(3 * q1>MagneticMultipoleP.Kn1)"
    assert source(1, "q1").tree is source_lat.original.tree


def test_original_node_is_none_for_a_computed_value(source_lat):
    line = source_lat.full_expanded["lat1"]["branches"][0]["ln"]["line"]
    assert original_node(source_lat, line[1]["q1"]["FloorP"]["z"]) is None


# ─── controller_expressions ─────────────────────────────────────────────────

CONTROLLED_LATTICE = """
PALS:
  facility:
    - q1:
        kind: Quadrupole
    - q2:
        kind: Quadrupole
        MagneticMultipoleP:
          Kn1: 0.3
    - q3:
        kind: Quadrupole
        MagneticMultipoleP:
          Kn1: 0.2
    - ps1:
        kind: Controller
        variables:
          cur: 0.25
        controls:
          - parameter: q1>MagneticMultipoleP.Kn1
            expression: 2 * cur
    - ps2:
        kind: Controller
        variables:
          cur: 0.1
        controls:
          - parameter: q1>MagneticMultipoleP.Kn1
            expression: cur
    - knob:
        kind: Controller
        control_type: RELATIVE
        variables:
          k: 0.0
        controls:
          - parameter: q2>MagneticMultipoleP.Kn1
            expression: 3 * k
    - ln:
        kind: BeamLine
        line:
          - begin:
              kind: BeginningEle
              ReferenceP: {species_ref: electron, E_tot_ref: 1.0e9}
          - q1
          - q2
          - q3
    - lat1:
        kind: Lattice
        branches:
          - ln
    - use: lat1
"""


@pytest.fixture
def controlled_lat(tmp_path):
    path = tmp_path / "controlled.pals.yaml"
    path.write_text(CONTROLLED_LATTICE)
    return parse_and_expand_pals(str(path))


@pytest.mark.parametrize("view", ["expanded", "full_expanded"])
def test_controller_expressions_lists_every_driving_controller(controlled_lat,
                                                               view):
    line = getattr(controlled_lat, view)["lat1"]["branches"][0]["ln"]["line"]

    def kn1(i, name):
        return line[i][name]["MagneticMultipoleP"]["Kn1"]

    def described(i, name):
        return [f"{node_key(get_parent(get_parent(get_parent(e))))}: {e.value}"
                for e in controller_expressions(controlled_lat, kn1(i, name))]

    # Two ABSOLUTE controllers sum into q1, in evaluation order.
    assert described(1, "q1") == ["ps1: 2 * cur", "ps2: cur"]
    assert described(2, "q2") == ["knob: 3 * k"]
    assert controller_expressions(controlled_lat, kn1(3, "q3")) == []
    assert all(e.tree is controlled_lat.original.tree
               for e in controller_expressions(controlled_lat, kn1(1, "q1")))
    # A controller-driven value has no single source text.
    assert original_node(controlled_lat, kn1(1, "q1")) is None
