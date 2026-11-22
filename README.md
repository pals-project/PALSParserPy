# PALSParserPy

Python Interface for Particle Accelerator Language Standard (PALS) files.

## Introduction

`PALSParserPy` is a parser for the Particle Accelerator Language Standard
([PALS](https://github.com/pals-project/pals)) for the Python language.

In addition, `PALSParserPy` provides translation functions:

- From `PALS` files to [`Bmad`](https://github.com/bmad-sim/bmad-ecosystem) lattice files.
- From `PALS` files to [`MAD-X`](https://mad.web.cern.ch/mad/) lattice files.
- From `PALS` files to Python scripts that build an
  [`Xsuite`](https://github.com/xsuite/xsuite) (`xtrack`) lattice.

For a translator from `PALS` to [`SciBmad`](https://github.com/bmad-sim/SciBmad.jl),
use `pals_to_scibmad` in [PALSParserJ](https://github.com/pals-project/PALSParserJ.jl).

For a translator from `Bmad` to `PALS`, the `Bmad` based `Tao` program can be used.
A translator from `SciBmad` to `PALS` is planned.

## Status

- 2026-08-05: Initial port of
  [PALSParserJ](https://github.com/pals-project/PALSParserJ.jl), the Julia
  interface to the same C library.

## Installation

Published PyPI wheels include the native parser and its dependencies:

```console
pip install palsparserpy
# Or, in a uv project:
uv add palsparserpy
```

Wheels target Linux x86-64 (glibc 2.28+), macOS Intel/Apple silicon (11+), and
Windows x86-64, with Python 3.10 or later. These installation commands require
a release containing the new wheels; this branch does not publish one itself.
Other platforms can build from the source distribution with CMake and a C++17
compiler (GCC 9+). The source distribution includes the pinned native sources.

Conda packages use the environment's separately packaged native library.
Explicit `PALS_PARSER_CPP_LIB` and `PALS_PARSER_CPP_DIR` overrides remain
available for development. For an external-library-only installation, set
`PALS_BUNDLE_NATIVE=0` when building; conda-build selects this automatically.

See [release automation](docs/src/guide/releases.md) for native version updates,
wheel checks, and initial PyPI configuration. See the
[installation guide](https://pals-project.github.io/PALSParserPy/guide/installation.html)
for checkout development instructions.

## Quick start

```python
import palsparserpy as pp

lat = pp.parse_and_expand_pals("lattice_files/ex.pals.yaml")
print(lat.full_expanded)                       # the expanded lattice, as YAML

pp.parameter_value(lat, "Q1a>length")          # one parameter's value
pp.match_names(lat.full_expanded, "DH.*>BendP.e1")   # the nodes a name selects

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

## License

PALSParserPy is licensed under the GNU General Public License, version 2
only (`GPL-2.0-only`). See [LICENSE](LICENSE).
