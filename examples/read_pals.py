"""Read a PALS file and print the five views of the lattice it creates in
memory."""

import os
import sys

# So the examples run from a checkout without installing it first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palsparserpy as pp                                    # noqa: E402

lattice_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "lattice_files")
file_name = os.path.join(lattice_dir, "ex.pals.yaml")
root_lattice = ""

lat = pp.parse_and_expand_pals(file_name, root_lattice)

print("Printing original lattice information:")
print(pp.to_yaml_string(lat.original))
print("\n" + "-" * 50)

print("Printing combined lattice information:")
print(pp.to_yaml_string(lat.combined))
print("\n" + "-" * 50)

print("Printing expanded lattice information:")
print(pp.to_yaml_string(lat.expanded))
print("\n" + "-" * 50)

print("Printing full expanded lattice information:")
print(pp.to_yaml_string(lat.full_expanded))
print("\n" + "-" * 50)

print("Printing what expansion left over:")
print(pp.to_yaml_string(lat.adjunct))
