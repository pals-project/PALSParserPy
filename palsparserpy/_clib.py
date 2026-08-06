"""
Locating and binding the PALSParserCpp shared library.

The C API is tree+nodeId-centric: every operation takes a ``YAMLTreeHandle``
(opaque pointer to a parsed tree) and a ``YAMLNodeId`` (index within that tree).
This module holds the ``ctypes`` mirror of ``PALSParserCpp.h`` -- the structs, the
enum constants, and the function prototypes -- and nothing else; the Python-side
object model lives in :mod:`palsparserpy.parser`.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import (POINTER, Structure, c_bool, c_char_p, c_double, c_int,
                    c_size_t, c_void_p)

__all__ = [
    "YAML_NULL_ID", "libparser", "take_string", "encode",
    "ProblemC", "ProblemListC", "LatticesC", "NodeLinkC", "CorrespondenceMapC",
    "NameMatchesC", "ParamValueC",
    "PARAM_VALUE_MISSING", "PARAM_VALUE_NUMBER", "PARAM_VALUE_STRING",
]

# Sentinel meaning "no node" / "append at the end"; (size_t)-1 in C.
YAML_NULL_ID = ctypes.c_size_t(-1).value

# ─── structs matching the C header ───────────────────────────────────────────

class ProblemC(Structure):
    """Mirrors ``struct problem``. Both strings are owned by the C side and are
    freed with ``free_lattice_problems``; the two enums are C ``int``s."""
    _fields_ = [("message", c_char_p),
                ("path", c_char_p),
                ("severity", c_int),
                ("origin", c_int)]


class ProblemListC(Structure):
    """Mirrors ``struct problem_list``: an owning array of :class:`ProblemC` and
    its length. Freed with ``free_lattice_problems``."""
    _fields_ = [("items", POINTER(ProblemC)),
                ("count", c_size_t)]


class LatticesC(Structure):
    """Mirrors ``struct lattices``: five tree handles plus the problem list, all
    by value. Layout must match field for field and in order."""
    _fields_ = [("original", c_void_p),
                ("combined", c_void_p),
                ("expanded", c_void_p),
                ("full_expanded", c_void_p),
                ("adjunct", c_void_p),
                ("problems", ProblemListC)]


class NodeLinkC(Structure):
    """Mirrors ``struct node_link``: one logical node's id in each tree."""
    _fields_ = [("original", c_size_t),
                ("combined", c_size_t),
                ("full_expanded", c_size_t),
                ("adjunct", c_size_t)]


class CorrespondenceMapC(Structure):
    """Mirrors ``struct correspondence_map``: an owning array of links."""
    _fields_ = [("links", POINTER(NodeLinkC)),
                ("count", c_size_t)]


class NameMatchesC(Structure):
    """Mirrors ``struct name_matches``: a flat array of matched node ids."""
    _fields_ = [("nodes", POINTER(c_size_t)),
                ("count", c_size_t)]


class ParamValueC(Structure):
    """Mirrors ``struct param_value``. ``string`` is held as a raw address rather
    than a ``c_char_p`` so that the pointer survives to be freed with
    ``yaml_free_string``."""
    _fields_ = [("kind", c_int),
                ("number", c_double),
                ("string", c_void_p)]


# ``enum param_value_kind`` from PALSParserCpp.h.
PARAM_VALUE_MISSING = 0
PARAM_VALUE_NUMBER = 1
PARAM_VALUE_STRING = 2

# ─── function prototypes ─────────────────────────────────────────────────────

# name -> (restype, argtypes). Every char*-returning function is declared
# c_void_p rather than c_char_p: ctypes would convert the latter to bytes and
# throw the address away, leaking the string the caller is meant to free.
_PROTOTYPES = {
    "parse_and_expand_PALS":       (LatticesC, [c_char_p, c_char_p]),
    "expand_PALS_string":          (LatticesC, [c_char_p, c_char_p]),
    "free_lattice_problems":       (None, [ProblemListC]),
    "evaluate_pals_expression":    (c_double, [c_char_p, POINTER(c_bool)]),
    "build_correspondence_map":    (CorrespondenceMapC,
                                    [c_void_p, c_void_p, c_void_p, c_void_p]),
    "free_correspondence_map":     (None, [CorrespondenceMapC]),
    "match_names":                 (NameMatchesC, [c_void_p, c_char_p]),
    "free_name_matches":           (None, [NameMatchesC]),
    "get_parameter_value":         (ParamValueC, [c_void_p, c_char_p]),
    "get_lattice_parameter_value": (ParamValueC, [c_void_p, c_void_p, c_char_p]),
    "parse_file":                  (c_void_p, [c_char_p]),
    "parse_string":                (c_void_p, [c_char_p]),
    "yaml_last_parse_error":       (c_char_p, []),
    "create_empty_tree":           (c_void_p, []),
    "delete_tree":                 (None, [c_void_p]),
    "remove_node":                 (None, [c_void_p, c_size_t, c_size_t]),
    "get_root":                    (c_size_t, [c_void_p]),
    "get_parent":                  (c_size_t, [c_void_p, c_size_t]),
    "get_child_by_key":            (c_size_t, [c_void_p, c_size_t, c_char_p]),
    "get_child_by_index":          (c_size_t, [c_void_p, c_size_t, c_size_t]),
    "get_size":                    (c_size_t, [c_void_p, c_size_t]),
    "get_node_key":                (c_void_p, [c_void_p, c_size_t]),
    "is_map":                      (c_bool, [c_void_p, c_size_t]),
    "is_sequence":                 (c_bool, [c_void_p, c_size_t]),
    "is_scalar":                   (c_bool, [c_void_p, c_size_t]),
    "as_string":                   (c_void_p, [c_void_p, c_size_t]),
    "add_scalar":                  (c_size_t, [c_void_p, c_size_t, c_char_p,
                                               c_char_p, c_size_t]),
    "add_map":                     (c_size_t, [c_void_p, c_size_t, c_char_p, c_size_t]),
    "add_sequence":                (c_size_t, [c_void_p, c_size_t, c_char_p, c_size_t]),
    "set_scalar":                  (None, [c_void_p, c_size_t, c_char_p]),
    "set_node_key":                (None, [c_void_p, c_size_t, c_char_p]),
    "deep_copy_node":              (None, [c_void_p, c_size_t, c_void_p, c_size_t]),
    "deep_copy_children":          (None, [c_void_p, c_size_t, c_void_p, c_size_t,
                                           c_size_t]),
    "node_to_string":              (c_void_p, [c_void_p, c_size_t]),
    "tree_to_string":              (c_void_p, [c_void_p]),
    "write_file":                  (c_bool, [c_void_p, c_char_p]),
    "yaml_free_string":            (None, [c_void_p]),
}

# ─── library discovery ───────────────────────────────────────────────────────

def _dlext() -> str:
    """The shared-library extension of this platform."""
    if sys.platform == "darwin":
        return "dylib"
    if sys.platform in ("win32", "cygwin"):
        return "dll"
    return "so"


def _candidates() -> list[str]:
    """Every place the library might be.

    The library is built by PALSParserCpp and is not shipped with this package,
    so it has to be searched for. In order:

      1. ``$PALS_PARSER_CPP_LIB`` -- full path to the shared library itself
      2. ``$PALS_PARSER_CPP_DIR`` -- a PALSParserCpp checkout; its build
         directory is searched
      3. a PALSParserCpp checkout beside this one, and the checkout this one sits
         inside (both layouts the installation guide describes)
    """
    # MSVC drops the "lib" prefix and writes into a per-configuration
    # subdirectory; the single-config generators used elsewhere write straight
    # into build/.
    names = (f"libPALSParserCpp.{_dlext()}", f"PALSParserCpp.{_dlext()}")
    subdirs = ("", "Release", "Debug")

    out = []
    if os.environ.get("PALS_PARSER_CPP_LIB"):
        out.append(os.environ["PALS_PARSER_CPP_LIB"])

    here = os.path.dirname(os.path.abspath(__file__))
    roots = []
    if os.environ.get("PALS_PARSER_CPP_DIR"):
        roots.append(os.environ["PALS_PARSER_CPP_DIR"])
    roots.append(os.path.normpath(os.path.join(here, "..", "..", "PALSParserCpp")))
    roots.append(os.path.normpath(os.path.join(here, "..", "..")))

    for root in roots:
        for sub in subdirs:
            for name in names:
                out.append(os.path.normpath(os.path.join(root, "build", sub, name)))
    return out


def _find_library() -> str:
    """Locate the library, or explain exactly what was looked for and how to fix
    it."""
    candidates = _candidates()
    for path in candidates:
        if os.path.isfile(path):
            return path
    searched = "\n".join("  " + c for c in candidates)
    raise FileNotFoundError(
        f"PALSParserPy could not find the PALSParserCpp shared library "
        f"(libPALSParserCpp.{_dlext()}).\n\n"
        "Build it from a PALSParserCpp checkout:\n"
        "    cmake -S . -B build && cmake --build build\n\n"
        "Then either clone PALSParserCpp next to PALSParserPy, or point\n"
        "PALSParserPy at it:\n"
        "    export PALS_PARSER_CPP_DIR=/path/to/PALSParserCpp\n"
        f"    export PALS_PARSER_CPP_LIB=/path/to/libPALSParserCpp.{_dlext()}\n\n"
        f"Searched:\n{searched}")


_lib = None


def libparser() -> ctypes.CDLL:
    """The loaded PALSParserCpp shared library, with every prototype bound.

    Resolved on first use and cached thereafter. Raises ``FileNotFoundError``
    listing every path tried if the library cannot be found.

    Resolution is deliberately lazy rather than done at import: ``import
    palsparserpy`` must succeed without the C library present, so that
    documentation and other tooling can read the package without a C++
    toolchain. The cost is that a missing library is reported at the first call
    rather than at import.
    """
    global _lib
    if _lib is None:
        lib = ctypes.CDLL(_find_library())
        for name, (restype, argtypes) in _PROTOTYPES.items():
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        _lib = lib
    return _lib


# ─── string helpers ──────────────────────────────────────────────────────────

def encode(text) -> bytes | None:
    """Encode a Python string for the C API. ``None`` passes through as NULL."""
    return None if text is None else str(text).encode("utf-8")


def take_string(ptr) -> str | None:
    """Copy a string the library returned and free it, or ``None`` for NULL.

    Every ``char*`` this API hands back is owned by the caller, so the copy and
    the free belong together.
    """
    if not ptr:
        return None
    text = ctypes.cast(ptr, c_char_p).value.decode("utf-8")
    libparser().yaml_free_string(ptr)
    return text
