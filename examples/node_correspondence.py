"""Mapping corresponding nodes across the derivation-chain trees of a PALS
lattice.

parse_and_expand_pals returns five views of a lattice; four of them -- `original`,
`combined`, `full_expanded` and `adjunct` -- form the derivation chain that
node_correspondence connects: given any node, it hands back the nodes it
corresponds to in the others. (`expanded` takes no part: it is a pruned copy of
`full_expanded`, so its nodes are found by path.) The correspondence is computed
from provenance recorded as the trees are derived from one another, so it is exact
even where expansion duplicates a node (a `repeat`, an `inherit`, a scalar
substitution, a fork).
"""

import os
import sys

# So the examples run from a checkout without installing it first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palsparserpy as pp                                    # noqa: E402

ex_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "lattice_files", "ex.pals.yaml")

lat = pp.parse_and_expand_pals(ex_file)
corr = pp.node_correspondence(lat)

print(f"Built a correspondence over {len(corr)} nodes.\n")

# -- A node outside the lattice is left over, not expanded ---------------------
# 'a_const' is defined at the top level of the facility and the lattice never
# refers to it, so expansion leaves it behind: it appears once in `original`, once
# in `combined` and once in `adjunct`, and not at all in `full_expanded`.
a_const = lat.combined["PALS"]["facility"][0]["constants"]["a_const"]
entry = corr[a_const]

print("Correspondence of the 'a_const' node:")
print("  in original:", [pp.to_yaml_string(n) for n in entry.original])
print("  in combined:", [pp.to_yaml_string(n) for n in entry.combined])
print("  in adjunct:", [pp.to_yaml_string(n) for n in entry.adjunct])
print("  in full_expanded:", [pp.to_yaml_string(n) for n in entry.full_expanded],
      " (empty)")
print()

# The map can be queried from *any* of those four trees and returns the same
# equivalence class -- here we start from the node in the original tree.
assert corr[entry.original[0]] == entry
print("Looking the class up from the original node gives the same result.\n")

# -- A node duplicated by expansion maps one-to-many ---------------------------
# Find a combined node that expansion turned into several expanded copies (for
# ex.pals.yaml this is the 'repeat'ed sub-line unrolled inside inj_line).
one_to_many = None
for node, e in corr.items():
    if len(e.combined) == 1 and node == e.combined[0] and len(e.full_expanded) > 1:
        one_to_many = e
        break

if one_to_many is not None:
    print("A combined node that expansion duplicated:")
    print("  combined source:", pp.to_yaml_string(one_to_many.combined[0]))
    print(f"  -> {len(one_to_many.full_expanded)} corresponding full_expanded "
          "nodes.")
