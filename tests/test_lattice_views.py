"""Tests that each of the five views parse_and_expand_pals returns is the one it
claims to be.

Each handle of ``struct lattices`` is read by position, so a field added to the C
struct and missed on the Python side would silently hand back the neighbouring
tree.
"""

import pytest

from palsparserpy import parse_and_expand_pals

# One element carrying enough reference data for the bookkeeper to run, so the
# derived parameters it computes are there to tell the two expanded views apart.
# `Kn1: 2 * 0.6` doubles as the marker for the pre-expansion views, which keep the
# expression text.
VIEWS_LATTICE = """
PALS:
  facility:
    - ring:
        kind: Lattice
        branches:
          - main:
              kind: BeamLine
              line:
                - q1:
                    kind: Quadrupole
                    length: 0.5
                    ReferenceP:
                      species_ref: electron
                      pc_ref: 1e9
                    MagneticMultipoleP:
                      Kn1: 2 * 0.6
    - use: ring
"""


@pytest.fixture(scope="module")
def views(tmp_path_factory):
    path = tmp_path_factory.mktemp("views") / "views.pals.yaml"
    path.write_text(VIEWS_LATTICE)
    return str(path), parse_and_expand_pals(path, problems="none")


def _q1(view):
    return view["ring"]["branches"][0]["main"]["line"][0]["q1"]


def test_the_five_views_are_five_distinct_trees(views):
    _, lat = views
    trees = [v.tree for v in (lat.original, lat.combined, lat.expanded,
                              lat.full_expanded, lat.adjunct)]
    assert len({id(t) for t in trees}) == 5
    assert lat.problems == []


def test_original_and_combined_keep_the_pre_expansion_text(views):
    path, lat = views
    # original is keyed by the path of each file read, combined by the PALS root.
    assert lat.original.keys() == [path]
    assert "PALS" in lat.combined
    for root in (lat.original[path], lat.combined):
        q1_raw = (root["PALS"]["facility"][0]["ring"]["branches"][0]["main"]
                  ["line"][0]["q1"])
        assert q1_raw["MagneticMultipoleP"]["Kn1"].value == "2 * 0.6"


def test_adjunct_keeps_the_facility_scaffolding(views):
    _, lat = views
    assert "PALS" in lat.adjunct
    assert "ring" not in lat.adjunct


def test_both_expanded_views_are_rooted_at_the_lattice(views):
    _, lat = views
    for view in (lat.expanded, lat.full_expanded):
        assert "ring" in view
        assert "PALS" not in view


def test_what_the_author_wrote_is_the_same_in_both(views):
    _, lat = views
    for view in (lat.expanded, lat.full_expanded):
        assert _q1(view)["length"].as_float() == 0.5
        # Evaluated in both, so a value shared by the two views agrees.
        assert _q1(view)["MagneticMultipoleP"]["Kn1"].as_float() == pytest.approx(1.2)


def test_only_full_expanded_carries_the_computed_parameters(views):
    _, lat = views
    full, exp = _q1(lat.full_expanded), _q1(lat.expanded)
    # Derived member of a parameter family the element uses.
    assert "Kn1L" in full["MagneticMultipoleP"]
    assert "Kn1L" not in exp["MagneticMultipoleP"]
    # Placement, and a group the author never wrote.
    assert "s_position" in full
    assert "s_position" not in exp
    assert "FloorP" in full
    assert "FloorP" not in exp
    # The branch_end Placeholder capping the branch is pruned as well.
    assert len(lat.full_expanded["ring"]["branches"][0]["main"]["line"]) == 2
    assert len(lat.expanded["ring"]["branches"][0]["main"]["line"]) == 1
