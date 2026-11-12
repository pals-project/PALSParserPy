# Installation

PALSParserPy is a Python wrapper around the C library built by
[PALSParserCpp](https://github.com/pals-project/PALSParserCpp). That library is
compiled from that repository rather than shipped with this package, so
PALSParserPy has to be told where it is. By default it looks for a PALSParserCpp
checkout beside its own, which is the layout below; if you keep PALSParserCpp
somewhere else, see [Pointing at a PALSParserCpp
elsewhere](#pointing-at-a-palsparsercpp-elsewhere) instead.

macOS, Linux, and Windows are all supported — the correct library extension for
the platform (`.dylib`, `.so`, `.dll`) is worked out at load time. Python 3.8 or
newer is required, and the interpreter must be built for the same architecture as
the library (an x86-64 Python cannot load an arm64 `.dylib`).

## 1. Clone the repositories

```console
git clone https://github.com/pals-project/PALSParserCpp.git
git clone https://github.com/pals-project/PALSParserPy.git
```

The default layout looks like this — PALSParserPy locates the compiled library
relative to its own source tree, at `../PALSParserCpp/build/`:

```text
some-directory/
├── PALSParserCpp/
│   └── build/
│       └── libPALSParserCpp.dylib   (or .so / .dll)
└── PALSParserPy/
```

A PALSParserPy checkout that sits *inside* a PALSParserCpp checkout works too:
that repository's own `build/` directory is searched as well.

## 2. Build the C library

From the `PALSParserCpp` directory, configure and build with CMake (this needs
CMake and a C++17 compiler — Apple Clang on macOS, GCC or Clang on Linux, MSVC
on Windows):

```console
cmake -S . -B build
cmake --build build
```

CMake fetches the [rapidyaml](https://github.com/biojppm/rapidyaml) backend
automatically. The result is the shared library `libPALSParserCpp.dylib`
(macOS), `.so` (Linux), or `.dll` (Windows) under `PALSParserCpp/build/`.
Rebuild with `cmake --build build` after changing any PALSParserCpp source. See
the PALSParserCpp `README` for more detail.

## 3. Install the Python package

From the `PALSParserPy` directory:

```console
pip install -e .
```

PALSParserPy has no Python dependencies — the C library is all it binds — so this
installs nothing but the package itself. The `-e` (editable) install means edits
to the checkout take effect without reinstalling.

Installing is optional if you only want to run the bundled scripts: the examples
and the test suite put the repository root on `sys.path` themselves.

## Check the installation

```python
import palsparserpy as pp

root = pp.create_empty_tree()
root["hello"] = "world"
print(pp.to_yaml_string(root))
```

If that prints `hello: world`, the Python package and the underlying C library
are wired up correctly.

## Pointing at a PALSParserCpp elsewhere

The side-by-side layout is only the default. Two environment variables override
it, read the first time PALSParserPy calls into the library — so setting either
one any time before that first call works, including after `import palsparserpy`:

| Variable | Meaning |
|---|---|
| `PALS_PARSER_CPP_DIR` | Path to a PALSParserCpp checkout; its `build/` directory is searched. |
| `PALS_PARSER_CPP_LIB` | Full path to the shared library itself, wherever it lives. |

```python
import os
os.environ["PALS_PARSER_CPP_DIR"] = "/opt/src/PALSParserCpp"

import palsparserpy as pp
```

`PALS_PARSER_CPP_LIB` wins if both are set.
`palsparserpy._clib.libparser()._name` returns the resolved path, which is worth
checking first if calls behave unexpectedly.

If the library cannot be found, the first call fails with a `FileNotFoundError`
listing every path that was tried, which is usually enough to spot a missing
build or a typo in the variable. Note that `import palsparserpy` itself always
succeeds: the library is looked up lazily so that tooling which only reads the
package — building these docs, for one — does not need a C++ toolchain.
