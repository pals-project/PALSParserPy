"""The installed wheel must not accidentally load an unrelated checkout."""
import os
import pytest
from palsparserpy import _clib


@pytest.fixture
def no_overrides(monkeypatch):
    monkeypatch.delenv("PALS_PARSER_CPP_LIB", raising=False)
    monkeypatch.delenv("PALS_PARSER_CPP_DIR", raising=False)

def test_bundled_library_precedes_implicit_checkouts(no_overrides):
    paths = _clib._candidates()
    assert os.path.basename(os.path.dirname(paths[0])) == '_native'


def test_explicit_checkout_precedes_bundled_library(no_overrides, monkeypatch, tmp_path):
    monkeypatch.setenv('PALS_PARSER_CPP_DIR', str(tmp_path))
    paths = _clib._candidates()
    assert paths[0] == str(tmp_path / 'build' / ('libPALSParserCpp.' + _clib._dlext()))
    bundled = next(i for i, p in enumerate(paths) if os.path.basename(os.path.dirname(p)) == '_native')
    assert bundled > 0
