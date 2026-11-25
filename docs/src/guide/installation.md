# Installation

## Published wheels (pip and uv)

Install a release with binary wheels (0.0.2 or later) using:

```console
pip install palsparserpy
# Or in a uv-managed project:
uv add palsparserpy
```

Supported wheels include PALSParserCpp and its native dependencies; no separate
C++ checkout is required. Initial wheel targets are Linux x86-64 (glibc 2.28+),
macOS Intel/ARM (11+), and Windows x86-64. Python 3.10 or newer is required.
See [Binary wheels and release automation](../maintainer/releases.md) for build and release
configuration. Other platforms can compile the complete source distribution.

## Development with an external library

The steps below use a separately built PALSParserCpp checkout. Explicit
`PALS_PARSER_CPP_LIB` or `PALS_PARSER_CPP_DIR` overrides take precedence over
bundled wheel libraries. Without an explicit override, a bundled library takes
precedence over automatically discovered sibling checkouts.

The interpreter must match the library's architecture: an x86-64 Python cannot
load an arm64 `.dylib`.

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
# macOS/Linux: keep the external-library development workflow
PALS_BUNDLE_NATIVE=0 python -m pip install -e .
```

On PowerShell, first set `$env:PALS_BUNDLE_NATIVE = "0"`, then run
`python -m pip install -e .`. This mode installs the Python wrapper without
building a bundled native library. The `-e` (editable) install means edits
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

When neither variable is set and no checkout is found, PALSParserPy falls back
to the library installed in the Python environment itself:
`<sys.prefix>/lib`, or `<sys.prefix>\Library\bin` on Windows. That is where the
`palsparsercpp` conda package puts it. A checkout comes first, so your own build
wins over an installed one.
`palsparserpy._clib.libparser()._name` returns the resolved path, which is worth
checking first if calls behave unexpectedly.

If the library cannot be found, the first call fails with a `FileNotFoundError`
listing every path that was tried, which is usually enough to spot a missing
build or a typo in the variable. Note that `import palsparserpy` itself always
succeeds: the library is looked up lazily so that tooling which only reads the
package — building these docs, for one — does not need a C++ toolchain.

## Matching versions

PALSParserPy mirrors the C library's structs by their memory layout, so it
works only with the PALSParserCpp versions it was written against, which are
listed in `palsparserpy._clib.SUPPORTED_LIBRARY_VERSIONS`. The first call
checks the version the library reports and fails with a `RuntimeError` naming
the library's path, its version and the versions needed, rather than risk
reading mismatched structs. A library older than PALSParserCpp's
`pals_parser_cpp_version()` function is refused the same way.

`pp.library_version()` returns the version of the library in use.
