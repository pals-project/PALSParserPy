"""Build standalone wheels while retaining conda's external-library packaging."""
import os
import sys
from pathlib import Path
from setuptools import setup, Distribution
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist
from setuptools.command.bdist_wheel import bdist_wheel
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
from native import build, prepare

BUNDLE = os.environ.get("PALS_BUNDLE_NATIVE", "0" if os.environ.get("CONDA_BUILD") else "1") == "1"

class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return BUNDLE

class BuildPython(build_py):
    def run(self):
        super().run()
        if BUNDLE:
            build(Path(self.build_lib), Path(self.build_lib).parent / "native-cmake")

class SourceDistribution(sdist):
    def run(self):
        # Include exact native sources and their submodules for offline rebuilds.
        prepare()
        super().run()

class Wheel(bdist_wheel):
    def get_tag(self):
        python, abi, platform = super().get_tag()
        return ("py3", "none", platform) if BUNDLE else (python, abi, platform)

setup(distclass=BinaryDistribution, cmdclass={"build_py": BuildPython, "sdist": SourceDistribution, "bdist_wheel": Wheel})
