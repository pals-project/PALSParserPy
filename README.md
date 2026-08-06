# PALSParserPy

Python Interface for Particle Accelerator Language Standard (PALS) files.

## Introduction

`PALSParserPy` is a parser for the Particle Accelerator Language Standard
([PALS](https://github.com/campa-consortium/pals)) for the Python language.

In addition, `PALSParserPy` provides translation functions:

- From `PALS` files to [`Bmad`](https://github.com/bmad-sim/bmad-ecosystem) lattice files.
- From `PALS` files to [`SciBmad`](https://github.com/bmad-sim/SciBmad.jl) lattice files.
- From `PALS` files to [`MAD-X`](https://mad.web.cern.ch/mad/) lattice files.

For a translator from `Bmad` to `PALS`, the `Bmad` based `Tao` program can be used.
A translator from `SciBmad` to `PALS` is planned.

## Status

- 2026-08-05: Initial port of
  [PALSParserJ](https://github.com/pals-project/PALSParserJ.jl), the Julia
  interface to the same C library.

## Installation

PALSParserPy is a thin Python wrapper around the C library built by
[PALSParserCpp](https://github.com/pals-project/PALSParserCpp), so both
repositories must be cloned side by side and the C library must be built first:

```console
git clone https://github.com/pals-project/PALSParserCpp.git
git clone https://github.com/pals-project/PALSParserPy.git

cd PALSParserCpp && cmake -S . -B build && cmake --build build && cd ..

cd PALSParserPy && pip install -e .
```

`pip install -e .` installs nothing but the package itself — PALSParserPy has no
Python dependencies. If PALSParserCpp lives somewhere other than beside this
checkout, point at it with `PALS_PARSER_CPP_DIR` or `PALS_PARSER_CPP_LIB`.

**See the [Installation guide](https://pals-project.github.io/PALSParserPy/guide/installation.html)
for full step-by-step instructions.**

## Quick start

```python
import palsparserpy as pp

lat = pp.parse_and_expand_pals("lattice_files/ex.pals.yaml")
print(lat.full_expanded)                       # the expanded lattice, as YAML

pp.parameter_value(lat, "Q1a>length")          # one parameter's value
pp.match_names(lat.full_expanded, "B1.*>BendP.e1")   # the nodes a name selects

pp.write_bmad_file(pp.pals_to_bmad(pp.parse_file("lattice_files/bta.pals.yaml")),
                   "bta.bmad")
```

## Examples

For usage examples, see the runnable scripts in the `examples` directory, e.g.

```console
python examples/read_pals.py
```

They insert the repository root on `sys.path`, so they run from a checkout
whether or not the package has been installed.

### Jupyter notebooks

Some examples are also provided as Jupyter notebooks (e.g.
`examples/manipulate_tree.ipynb`). To run them you need Jupyter:

```console
pip install jupyter
jupyter notebook examples/manipulate_tree.ipynb
```

## Tests

```console
pip install -e ".[test]"
pytest
```

The tests import the package from the checkout, so `pip install -e .` is
optional; the C library, however, must be built.
