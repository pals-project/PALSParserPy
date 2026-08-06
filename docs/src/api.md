# API Reference

Everything below is exported from the top-level `palsparserpy` package, so
`pp.parse_and_expand_pals(...)` reaches it after `import palsparserpy as pp`.

## The tree objects

```{eval-rst}
.. autoclass:: palsparserpy.YAMLNode
   :members:
   :special-members: __getitem__, __setitem__, __delitem__, __contains__, __len__, __iter__

.. autoclass:: palsparserpy.YAMLTree

.. autoexception:: palsparserpy.PALSParseError
```

## Parsing and building

```{eval-rst}
.. autofunction:: palsparserpy.parse_file
.. autofunction:: palsparserpy.parse_string
.. autofunction:: palsparserpy.create_empty_tree
```

## Function forms of the node operations

Each of these is the free-function spelling of the like-named
{class}`~palsparserpy.YAMLNode` method above.

```{eval-rst}
.. autofunction:: palsparserpy.is_map
.. autofunction:: palsparserpy.is_sequence
.. autofunction:: palsparserpy.is_scalar
.. autofunction:: palsparserpy.get_parent
.. autofunction:: palsparserpy.node_key
.. autofunction:: palsparserpy.add_scalar
.. autofunction:: palsparserpy.add_map
.. autofunction:: palsparserpy.add_sequence
.. autofunction:: palsparserpy.set_scalar
.. autofunction:: palsparserpy.set_key
.. autofunction:: palsparserpy.remove
.. autofunction:: palsparserpy.deep_copy_node
.. autofunction:: palsparserpy.deep_copy_children
.. autofunction:: palsparserpy.to_yaml_string
.. autofunction:: palsparserpy.write_yaml
```

## Lattices

```{eval-rst}
.. autofunction:: palsparserpy.parse_and_expand_pals
.. autofunction:: palsparserpy.evaluate_pals_expression
.. autofunction:: palsparserpy.node_correspondence
.. autofunction:: palsparserpy.match_names
.. autofunction:: palsparserpy.parameter_value
```

## What expansion hands back

```{eval-rst}
.. autoclass:: palsparserpy.Lattices
   :members:

.. autoclass:: palsparserpy.Problem
   :members:

.. autoclass:: palsparserpy.ProblemSeverity
   :members:

.. autoclass:: palsparserpy.ProblemOrigin
   :members:

.. autoclass:: palsparserpy.NodeCorrespondence
   :members:
```

## Translation

```{eval-rst}
.. autofunction:: palsparserpy.pals_to_bmad
.. autofunction:: palsparserpy.write_bmad_file
.. autoclass:: palsparserpy.BmadLattice
.. autoclass:: palsparserpy.BmadEleDef
.. autoclass:: palsparserpy.BmadBeamline
.. autoclass:: palsparserpy.BmadController

.. autofunction:: palsparserpy.pals_to_madx
.. autofunction:: palsparserpy.write_madx_file
.. autoclass:: palsparserpy.MadxLattice
.. autoclass:: palsparserpy.MadxEleDef
.. autoclass:: palsparserpy.MadxBeamline
.. autoclass:: palsparserpy.MadxController
.. autoclass:: palsparserpy.MadxAlignment

.. autofunction:: palsparserpy.pals_to_scibmad
.. autofunction:: palsparserpy.write_scibmad_file
.. autoclass:: palsparserpy.SciBmadLattice
.. autoclass:: palsparserpy.SciBmadEle
.. autoclass:: palsparserpy.SciBmadBeamline
.. autoclass:: palsparserpy.SciBmadLatticeList
.. autoclass:: palsparserpy.SciBmadController
```
