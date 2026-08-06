"""
The values :func:`palsparserpy.parse_and_expand_pals` hands back: the problems
found while expanding a lattice, and the five views of the lattice itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, NamedTuple

from .node import YAMLNode

__all__ = [
    "ProblemSeverity", "ProblemOrigin", "Problem", "Lattices",
    "NodeCorrespondence",
    "PROBLEM_ERROR", "PROBLEM_WARNING",
    "PROBLEM_INPUT", "PROBLEM_UNSUPPORTED", "PROBLEM_UNSPECIFIED",
]


class ProblemSeverity(IntEnum):
    """Whether a problem leaves the expanded trees trustworthy.

    - ``ERROR`` -- the document is wrong here and expansion could not work
      around it. Do not trust the affected part of the trees.
    - ``WARNING`` -- expansion produced a usable result; something was assumed
      or skipped, but the trees are still sound.

    Mirrors ``enum problem_severity`` in PALSParserCpp.h.
    """
    ERROR = 0
    WARNING = 1


class ProblemOrigin(IntEnum):
    """Who has to act on a problem.

    - ``INPUT`` -- the document is wrong; the lattice author can fix it.
    - ``UNSUPPORTED`` -- valid PALS that PALSParserCpp does not implement yet.
      Editing the lattice will not clear it.
    - ``UNSPECIFIED`` -- the PALS standard does not define the case, so nothing
      was invented. Neither the author nor the library is in the wrong.

    Mirrors ``enum problem_origin`` in PALSParserCpp.h.
    """
    INPUT = 0
    UNSUPPORTED = 1
    UNSPECIFIED = 2


# The C spellings, so that a problem list can be filtered without reaching
# through the enum class.
PROBLEM_ERROR = ProblemSeverity.ERROR
PROBLEM_WARNING = ProblemSeverity.WARNING
PROBLEM_INPUT = ProblemOrigin.INPUT
PROBLEM_UNSUPPORTED = ProblemOrigin.UNSUPPORTED
PROBLEM_UNSPECIFIED = ProblemOrigin.UNSPECIFIED


@dataclass(frozen=True)
class Problem:
    """One problem found while reading or expanding a document.

    - ``message`` -- human-readable description, always present.
    - ``path`` -- the logical spot it was found at, such as
      ``"q1>ApertureP.shape"``. Empty when the problem is not tied to one place.
      This is a location within the document, not a file name or a line number;
      ``message`` already names the file where the file is the point.
    - ``severity`` -- a :class:`ProblemSeverity`: can the trees still be trusted?
    - ``origin`` -- a :class:`ProblemOrigin`: whose problem is it?

    Only a ``PROBLEM_INPUT`` can be cleared by editing the lattice, which is what
    makes the last field worth reading: a tool that fails on any problem at all
    will fail on lattices whose author has nothing left to fix.
    """
    message: str
    path: str
    severity: ProblemSeverity
    origin: ProblemOrigin

    def __str__(self):
        out = "ERROR" if self.severity is ProblemSeverity.ERROR else "WARNING"
        if self.origin is ProblemOrigin.UNSUPPORTED:
            out += " (unsupported)"
        elif self.origin is ProblemOrigin.UNSPECIFIED:
            out += " (unspecified by PALS)"
        if self.path:
            out += f" at {self.path}"
        return f"{out}: {self.message}"


@dataclass(frozen=True)
class Lattices:
    """Five representations of a lattice, each as a root :class:`YAMLNode`, plus
    the list of problems found while expanding it.

    ``expanded`` and ``full_expanded`` are the same expanded lattice holding the
    same values; ``full_expanded`` additionally carries every parameter the
    bookkeeper computed, while ``expanded`` keeps only what the author wrote. See
    :func:`palsparserpy.parse_and_expand_pals` for what each view holds.

    ``problems`` is a list of :class:`Problem` -- one entry per problem
    encountered during expansion (undefined lattice, dangling element/line
    references, undefined ``inherit``/``repeat``/``Fork`` targets, misspelled
    names, and expressions that could not be evaluated). It is empty when
    expansion was clean. Filter it on ``severity`` or ``origin`` to decide what
    is worth acting on::

        lat = parse_and_expand_pals("ex.pals.yaml", problems="none")
        mine = [p for p in lat.problems if p.origin is PROBLEM_INPUT]
    """
    original: YAMLNode
    combined: YAMLNode
    expanded: YAMLNode
    full_expanded: YAMLNode
    adjunct: YAMLNode
    problems: List[Problem] = field(default_factory=list)


class NodeCorrespondence(NamedTuple):
    """The nodes one logical entity maps to in each of the four
    derivation-chain trees, grouped by tree.

    ``expanded`` takes no part -- it is a pruned copy of ``full_expanded``, not a
    step in the chain. A field is empty when a tree has no corresponding node.
    """
    original: List[YAMLNode]
    combined: List[YAMLNode]
    full_expanded: List[YAMLNode]
    adjunct: List[YAMLNode]
