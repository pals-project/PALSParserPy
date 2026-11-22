"""Pinned native sources, shared by wheel and source-distribution builds."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def run(*args, **kw):
    subprocess.run(list(map(str, args)), check=True, **kw)

def prepare():
    lock = json.loads((ROOT / "native-lock.json").read_text())
    vendor = ROOT / "vendor"
    marker = vendor / "native-lock.json"
    if marker.exists() and json.loads(marker.read_text()) == lock:
        return vendor
    if vendor.exists():
        shutil.rmtree(vendor)
    vendor.mkdir()
    sources = {"PALSParserCpp": {"url": "https://github.com/pals-project/PALSParserCpp.git",
               "tag": "v" + lock["version"], "commit": lock["commit"]}, **lock["dependencies"]}
    for name, source in sources.items():
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / name
            run("git", "clone", "--branch", source["tag"], "--depth", "1", source["url"], checkout)
            actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            if actual != source["commit"]:
                raise RuntimeError(f"Moved source tag for {name}: {actual}")
            run("git", "-C", checkout, "submodule", "update", "--init", "--recursive", "--depth", "1")
            shutil.copytree(checkout, vendor / name, ignore=shutil.ignore_patterns(".git"))
    shutil.copyfile(ROOT / "native-lock.json", marker)
    return vendor

def build(destination, build_dir):
    vendor = prepare()
    run("cmake", "-S", ROOT / "native", "-B", build_dir,
        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        "-DCMAKE_INSTALL_PREFIX=" + str(destination.resolve()))
    run("cmake", "--build", build_dir, "--config", "Release", "--target", "PALSParserCpp", "--parallel", os.environ.get("CMAKE_BUILD_PARALLEL_LEVEL", "2"))
    run("cmake", "--install", build_dir, "--config", "Release", "--component", "PythonWheel")

if __name__ == "__main__":
    prepare()
