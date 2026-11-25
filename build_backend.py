"""Setuptools backend with native build tools required only for bundled wheels."""
from setuptools import build_meta as _backend
from tools.native import bundle_enabled

build_wheel = _backend.build_wheel
build_sdist = _backend.build_sdist
build_editable = _backend.build_editable
prepare_metadata_for_build_wheel = _backend.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _backend.prepare_metadata_for_build_editable
get_requires_for_build_sdist = _backend.get_requires_for_build_sdist

def get_requires_for_build_wheel(config_settings=None):
    requirements = _backend.get_requires_for_build_wheel(config_settings)
    return requirements + (["cmake>=3.18"] if bundle_enabled() else [])

def get_requires_for_build_editable(config_settings=None):
    requirements = _backend.get_requires_for_build_editable(config_settings)
    return requirements + (["cmake>=3.18"] if bundle_enabled() else [])
