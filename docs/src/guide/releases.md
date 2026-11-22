# Binary wheels and release automation

The existing Python package and C API bindings are shared by conda and PyPI.
PyPI wheels bundle PALSParserCpp and statically link rapidyaml, PCRE2, and APC.
The complete pinned native sources, including submodules and license files,
are shipped in the source distribution. Wheel repair tools may additionally
bundle compiler runtime libraries. Notices are retained under
`palsparserpy/_native/licenses`.

## Native release updates

`native-lock.json` is the authoritative wheel source pin. Every Monday, or on
manual dispatch, `native-update.yml` checks the latest stable PALSParserCpp
release and opens or updates `automation/native-release`. It resolves tags to
commits and follows the dependency tags in the upstream CMake file. Changed
source repositories or declarations stop automation for review.

If the public C header is byte-for-byte unchanged, the update also extends the
wrapper's supported version range. Otherwise a maintainer must inspect the
structs and prototypes and update the bindings/range explicitly. A passing
suite does not replace that ABI review. Update PRs never auto-merge or publish.
GitHub must allow Actions to create pull requests. The bot explicitly dispatches
Wheels because GITHUB_TOKEN-created PRs do not trigger PR workflows themselves.

## Wheel validation

`wheels.yml` first creates one complete source distribution, then builds wheels
from it on Linux x86-64, macOS Intel/ARM, and Windows x86-64. The wheels use the
`py3-none` ABI tag because ctypes does not use the Python C API. cibuildwheel
repairs platform dependencies and tests the installed package on Python 3.10.
Tests run outside the checkout and assert the bundled library is selected.
A separate job installs the Linux wheel through uv on Python 3.14 and repeats
the suite. Pull requests, main pushes, and manual dispatch run without publishing.

## First-time publication setup

1. Configure a PyPI Trusted Publisher for project `palsparserpy`, GitHub owner
   `pals-project`, repository `PALSParserPy`, workflow `wheels.yml`, environment
   `pypi`. Use a pending publisher if the PyPI project does not exist yet.
2. Create the GitHub `pypi` environment and initially require a maintainer's
   approval. No PyPI API-token secret is required.
3. Check that the Python version is unused on PyPI, and update both
   `pyproject.toml` and `palsparserpy/__init__.py`. Native and Python versions
   need not match. Publish a GitHub release tagged `v<python-version>`.

The release run checks its tag against package metadata, rebuilds from the
source distribution, and waits for every wheel and uv test before the protected
publish job uploads wheels and source to PyPI using OIDC. Prereleases are built
and tested but not uploaded. No workflow in this change creates a release.

## Local and conda builds

```console
python -m pip install build
python -m build
```

A checkout build needs Git/network to prepare pinned native sources. An sdist
already contains them and can rebuild without downloading native sources.
Python build tools still need to be available. `PALS_BUNDLE_NATIVE=0` produces
the existing pure Python wrapper for externally managed libraries; conda-build
selects that default when `CONDA_BUILD` is set. This mode retains setuptools
and does not need to build or load bundled native code.

Dependency/tool updates are proposed monthly by Dependabot. Native dependency
changes shipped without a new PALSParserCpp release need a reviewed pin update
and new Python release; the scheduled updater does not silently change them.
