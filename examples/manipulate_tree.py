"""Read a PALS file and then manipulate the resulting tree structure in
memory."""

import os
import sys

# So the examples run from a checkout without installing it first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palsparserpy as pp                                    # noqa: E402

lattice_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "lattice_files")
ex_file = os.path.join(lattice_dir, "ex.pals.yaml")
expand_file = os.path.join(lattice_dir, "expand.pals.yaml")

print("============ Printing Developer Information ============")

# reading a lattice from a yaml file
print("""Use the function 'tree = parse_file(filename)' to read a YAML file.
This reads in any YAML file. To read in a PALS file with lattice expansion,
use the function parse_and_expand_pals.""")

tree = pp.parse_file(ex_file)

# printing to terminal
print("To print a tree to console, use the 'pp.to_yaml_string(tree)' function.")
print(pp.to_yaml_string(tree), "\n")

# type checking
print("The root node of 'ex.pals.yaml' is the 'PALS' map, so is_map(tree) =",
      pp.is_map(tree))

# The lattice contents live under the 'facility' node of the 'PALS' root.
facility = tree["PALS"]["facility"]
print("The 'facility' node is a sequence, so is_sequence(facility) =",
      pp.is_sequence(facility))

# accessing a sequence
print("Elements in a sequence may be accessed by their index.")
first_ele = facility[0]
print("The first element of 'facility' is: \n", pp.to_yaml_string(first_ele))

# accessing a map
print("Elements in a map may be accessed by their key.")
a_const = first_ele["constants"]["a_const"]
print("The 'a_const' constant has the value:\n    ", pp.to_yaml_string(a_const))

# add a new sequence element to the facility containing new_map: {apples: 5}
print("Adding a new element '-apples: 5' to facility.")
new_map_entry = facility.add_map()
map_node = new_map_entry.add_map(key="new_map")
map_node.add_scalar("5", key="apples")

# add a new sequence element to the facility containing magnets
print("Adding a new element")
print("    - magnet_list:")
print("        - magnet1")
print("        - magnet2")
print("to facility.\n")
magnets_entry = facility.add_map()
sequence = magnets_entry.add_sequence(key="magnet_list")
sequence.add_scalar("magnet1")
sequence.add_scalar("magnet2", index=0)

# writing trees to files
print("Use 'write_yaml(tree, filename)' to write the edited tree to a file.")
pp.write_yaml(tree, expand_file)
print("Wrote tree to 'expand.pals.yaml'\n\n")

print("========== Printing Final Modified Tree ==========")
print(pp.to_yaml_string(tree))
