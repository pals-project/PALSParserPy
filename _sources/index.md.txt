# PALSParserPy

**PALSParserPy** is a Python parser for the Particle Accelerator Lattice Standard
([PALS](https://github.com/campa-consortium/pals)). It reads PALS-format lattice
files, performs lattice expansion, and translates lattices into
[SciBmad](https://github.com/bmad-sim/SciBmad.jl),
[Bmad](https://www.classe.cornell.edu/bmad/) and
[MAD-X](https://mad.web.cern.ch/mad/) formats.

Under the hood, the package is a thin `ctypes` wrapper around the C library built
by [PALSParserCpp](https://github.com/pals-project/PALSParserCpp) (a
[rapidyaml](https://github.com/biojppm/rapidyaml) backend). A parsed document is
a tree of `YAMLNode` values that you index and mutate with familiar Python idioms
(`node["key"]`, `node[i]`, `key in node`, `.keys()`, `len`, iteration).

```{toctree}
:hidden:
:caption: User Guide

guide/installation
guide/parsing
guide/lattices
guide/expressions
guide/translation
```

```{toctree}
:hidden:
:caption: Reference

api
```

## What it does

1. **Parse** PALS-format YAML into a `YAMLNode` tree, or build one from
   scratch — see [Parsing and writing YAML](guide/parsing.md).
2. **Expand** a lattice — read a lattice file, resolve its includes, and
   expand the line into an ordered list of elements — see
   [Reading and expanding lattices](guide/lattices.md).
3. **Evaluate** the mathematical expressions in the expanded lattice to
   numbers — see [Evaluating expressions](guide/expressions.md).
4. **Translate** a PALS lattice to SciBmad, Bmad or MAD-X format — see
   [Translating to SciBmad, Bmad and MAD-X](guide/translation.md).

The complete docstring reference is in the [API Reference](api.md).

## Quick example

```python
import palsparserpy as pp

# Read a lattice file and expand it.
lat = pp.parse_and_expand_pals("ex.pals.yaml")

print(pp.to_yaml_string(lat.full_expanded))  # the expanded root lattice as YAML
print(pp.to_yaml_string(lat.adjunct))        # everything else in the document

# Build a document from scratch and write it out.
root = pp.create_empty_tree()
server = root.add_map(key="server")
server["host"] = "localhost"
server["port"] = "8080"

pp.write_yaml(root, "config.pals.yaml")
```
