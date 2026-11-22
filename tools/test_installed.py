"""Run tests outside the checkout, refusing accidental source-tree imports."""
from pathlib import Path
import shutil
import json
import subprocess
import sys
import tempfile
import palsparserpy
from palsparserpy import _clib

project = Path(sys.argv[1]).resolve()
assert project not in Path(palsparserpy.__file__).resolve().parents, palsparserpy.__file__
assert "_native" in Path(_clib._find_library()).parts, _clib._find_library()
manifest = Path(_clib._find_library()).parent / "native-lock.json"
assert palsparserpy.library_version() == json.loads(manifest.read_text())["version"]
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    shutil.copytree(project / "tests", root / "tests")
    shutil.copytree(project / "lattice_files", root / "lattice_files")
    subprocess.run([sys.executable, "-m", "pytest", str(root / "tests"), "-v"], cwd=root, check=True)
