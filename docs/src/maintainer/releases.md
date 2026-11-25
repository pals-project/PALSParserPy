# Binary wheels and release automation

The existing Python package and C API bindings are shared by conda and PyPI.
PyPI wheels bundle PALSParserCpp and statically link rapidyaml, PCRE2, and APC.
The complete pinned native sources, including submodules and license files,
are shipped in the source distribution. Wheel repair tools may additionally
bundle compiler runtime libraries. Notices are retained under
`palsparserpy/_native/licenses`.

## Native release updates

The conda recipe's PALSParserCpp version is the reference; `native-lock.json`
records that version and the immutable wheel source pins. `native-update.yml`
is manual-only: supply the conda-selected native version and optionally the
rapidyaml and PCRE2 tags selected for the conda baseline. It opens or updates
`automation/native-release`; it never independently follows the latest release.
APC follows the selected native release because its constants affect tests.
rapidyaml and PCRE2 retain their reviewed pins unless explicitly changed.
The current baseline is PALSParserCpp 0.0.4, rapidyaml 0.15.0, PCRE2 10.49,
and APC 0.12.0, matching the conda recipe/review's tested versions. The conda
recipe itself leaves rapidyaml/PCRE2 resolution to conda-forge.
Changed source repositories or declarations stop automation for review.

The wheel source currently carries the small CMake correction from
[PALSParserCpp #122](https://github.com/pals-project/PALSParserCpp/pull/122).
Remove `native/patches/subdirectory-fixtures.patch` when the conda-selected
native release includes it; do not move the lock to an unreleased branch.

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
3. Use 0.0.2 or later (v0.0.1 already exists). Check that the Python version is unused on PyPI, and update both
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

Creating an sdist or explicitly bundled checkout wheel needs Git/network to prepare pinned native sources. An sdist
already contains them and can rebuild without downloading native sources.
Python build tools still need to be available. `PALS_BUNDLE_NATIVE=0` produces
the existing pure Python wrapper for externally managed libraries. Without an
override, builds bundle only if `vendor/` exists (as in the complete sdist).
A clean checkout or GitHub tag archive therefore builds only Python by default,
independently of `CONDA_BUILD`. Set `PALS_BUNDLE_NATIVE=0` explicitly in conda
recipes. CMake is requested dynamically only for bundled wheels/editable builds;
pure builds need only setuptools.

Dependency/tool updates are proposed monthly by Dependabot. Native dependency
changes shipped without a new PALSParserCpp release need a reviewed pin update
and new Python release; the manual updater does not silently change them.
