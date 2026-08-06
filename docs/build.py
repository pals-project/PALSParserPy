#!/usr/bin/env python3
"""Build the PALSParserPy documentation.

Produces a single site in ``gh-pages/`` (at the repository root): the narrative
docs and the autodoc API reference are one Sphinx build, so unlike the Julia
interface -- whose API reference is a separate Documenter site -- there is
nothing to stitch together afterwards.

Run from anywhere:  python docs/build.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

docs_dir = Path(__file__).parent.resolve()
project_root = docs_dir.parent


def run(cmd, cwd):
    print(f"\n$ {' '.join(str(c) for c in cmd)}  (in {cwd})")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


# 1. Install the Sphinx toolchain.
print("==> Installing Sphinx dependencies…")
run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=docs_dir)

# 2. Build the site. autodoc imports palsparserpy to read its docstrings, but
#    importing it does not load the C library -- that happens lazily on the first
#    call -- so no C++ toolchain is needed here.
print("\n==> Building documentation (Sphinx + Furo)…")
run(["sphinx-build", "-b", "html", "src", "build/html"], cwd=docs_dir)

# 3. Publish into gh-pages/.
print("\n==> Copying into gh-pages/…")
gh_pages = project_root / "gh-pages"
if gh_pages.exists():
    shutil.rmtree(gh_pages)
gh_pages.mkdir()
shutil.copytree(docs_dir / "build" / "html", gh_pages, dirs_exist_ok=True)
(gh_pages / ".nojekyll").touch()

print(f"\nDone! Site in {gh_pages}")
print(f"Open {gh_pages / 'index.html'}.")
