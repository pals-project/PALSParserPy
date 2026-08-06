"""
PALSParserPy -- a Python wrapper around the PALSParserCpp C library (rapidyaml
backend) for the Particle Accelerator Language Standard (PALS).

The C API is tree+nodeId-centric: every operation takes a ``YAMLTreeHandle``
(opaque pointer to a parsed tree) and a ``YAMLNodeId`` (index within that tree).

On the Python side:

  - :class:`YAMLTree` owns the C tree handle and frees it when it is collected.
  - :class:`YAMLNode` is a lightweight value holding a reference to its parent
    tree (keeping it alive) and the integer node id.

Reading a lattice::

    import palsparserpy as pp

    lat = pp.parse_and_expand_pals("lattice.pals.yaml")
    print(lat.full_expanded)

Translating one::

    pp.write_bmad_file(pp.pals_to_bmad(pp.parse_file("lattice.pals.yaml")),
                       "lattice.bmad")
"""

from ._clib import YAML_NULL_ID
from .node import (PALSParseError, YAMLNode, YAMLTree, add_map, add_scalar,
                   add_sequence, create_empty_tree, deep_copy_children,
                   deep_copy_node, get_parent, is_map, is_scalar, is_sequence,
                   node_key, parse_file, parse_string, remove, set_key,
                   set_scalar, to_yaml_string, write_yaml)
from .parser import (evaluate_pals_expression, match_names, node_correspondence,
                     parameter_value, parse_and_expand_pals)
from .structs import (PROBLEM_ERROR, PROBLEM_INPUT, PROBLEM_UNSPECIFIED,
                      PROBLEM_UNSUPPORTED, PROBLEM_WARNING, Lattices,
                      NodeCorrespondence, Problem, ProblemOrigin,
                      ProblemSeverity)
from .to_bmad import (BmadBeamline, BmadController, BmadEleDef, BmadLattice,
                      pals_to_bmad, write_bmad_file)
from .to_madx import (MadxAlignment, MadxBeamline, MadxController, MadxEleDef,
                      MadxLattice, pals_to_madx, write_madx_file)
from .to_scibmad import (SciBmadBeamline, SciBmadController, SciBmadEle,
                         SciBmadLattice, SciBmadLatticeList, pals_to_scibmad,
                         write_scibmad_file)

__version__ = "0.1.0"

__all__ = [
    # tree objects and YAML manipulation
    "YAMLTree", "YAMLNode", "PALSParseError", "YAML_NULL_ID",
    "parse_file", "parse_string", "create_empty_tree",
    "is_map", "is_sequence", "is_scalar", "get_parent", "node_key",
    "add_scalar", "add_map", "add_sequence", "set_scalar", "set_key", "remove",
    "deep_copy_node", "deep_copy_children", "to_yaml_string", "write_yaml",
    # lattice-level API
    "parse_and_expand_pals", "evaluate_pals_expression", "node_correspondence",
    "match_names", "parameter_value",
    # what it hands back
    "Lattices", "Problem", "NodeCorrespondence",
    "ProblemSeverity", "ProblemOrigin",
    "PROBLEM_ERROR", "PROBLEM_WARNING",
    "PROBLEM_INPUT", "PROBLEM_UNSUPPORTED", "PROBLEM_UNSPECIFIED",
    # translation
    "pals_to_bmad", "write_bmad_file",
    "BmadLattice", "BmadEleDef", "BmadBeamline", "BmadController",
    "pals_to_madx", "write_madx_file",
    "MadxLattice", "MadxEleDef", "MadxBeamline", "MadxController",
    "MadxAlignment",
    "pals_to_scibmad", "write_scibmad_file",
    "SciBmadLattice", "SciBmadEle", "SciBmadBeamline", "SciBmadLatticeList",
    "SciBmadController",
]
