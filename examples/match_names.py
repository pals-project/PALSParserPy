"""Finding named constructs by name.

match_names implements PALS name matching:

    [{lattice}>>>][{branch}>>][{kind}::]{name}[>{group}.{sub}. ... .{parameter}]

{lattice}, {branch}, {name} are PCRE2 patterns (anchored whole-name matches);
{kind} and the dotted parameter path are matched exactly. It returns the nodes the
string resolves to -- elements, parameter groups, parameters, constants, or
variables -- which live in the tree you searched. Elements are searched for in the
`full_expanded` view; constants and variables are not part of the lattice, so they
are found in `adjunct`.
"""

import os
import sys

# So the examples run from a checkout without installing it first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palsparserpy as pp                                    # noqa: E402

ex_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "lattice_files", "ex.pals.yaml")
lat = pp.parse_and_expand_pals(ex_file)


def label(node):
    """A node as "key = value", omitting the value for container nodes."""
    if pp.is_map(node) or pp.is_sequence(node):
        return pp.node_key(node)
    return f"{pp.node_key(node)} = {node.value}"


def show_matches(query, tree=None):
    tree = lat.full_expanded if tree is None else tree
    matches = pp.match_names(tree, query)
    print(f'  "{query}"  →  {len(matches)} match(es)')
    for node in matches:
        print("     ", label(node))


# -- Element parameters --------------------------------------------------------
print("Element parameters:")
show_matches("Q1a>length")               # a named element's length
show_matches("Quadrupole::.*>length")    # restrict to a kind with `::`
show_matches("lat1>>>Q1a>length")        # restrict to a lattice with `>>>`

# -- Whole elements ------------------------------------------------------------
# Drop the parameter path to match the element node itself.
print("\nElements:")
show_matches("Q1a")

# -- Constants and variables ---------------------------------------------------
# A bare name also matches constants/variables by name. These are defined at
# facility level rather than inside the lattice, so search the adjunct view.
print("\nConstants and variables:")
show_matches("a_const", tree=lat.adjunct)
show_matches(".*_var", tree=lat.adjunct)

# -- Editing matched parameters in place ---------------------------------------
# The returned nodes belong to lat.full_expanded, so they can be modified directly.
print("\nEditing in place:")
for node in pp.match_names(lat.full_expanded, "Q1a>direction"):
    node.set_scalar("1")
show_matches("Q1a>direction")
