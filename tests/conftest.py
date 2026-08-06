"""Shared test setup: make the checkout importable without installing it."""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(scope="session")
def lattice_dir():
    """The directory holding the sample PALS lattices shipped with the repo."""
    return os.path.join(_ROOT, "lattice_files")
