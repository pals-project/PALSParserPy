"""
The lattice-level API: expanding a PALS document into its five views, evaluating
an expression, mapping nodes across the derivation chain, resolving a name-match
string, and reading a parameter value.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Dict, List, Union

from ._clib import (PARAM_VALUE_NUMBER, PARAM_VALUE_STRING, ParamValueC,
                    ProblemListC, encode, libparser, take_string, YAML_NULL_ID)
from .node import PALSParseError, YAMLNode, _root_node
from .structs import (Lattices, NodeCorrespondence, Problem, ProblemOrigin,
                      ProblemSeverity)

__all__ = ["parse_and_expand_pals", "evaluate_pals_expression",
           "node_correspondence", "match_names", "parameter_value"]


# ─── parse_and_expand_pals ───────────────────────────────────────────────────

def _take_problem_list(problems: ProblemListC) -> List[Problem]:
    """Copy the C-owned problems into a list and free the underlying C array.

    Always frees, even when the list is empty. Both strings are copied out before
    the free, so nothing points into C memory afterwards.
    """
    out = []
    for i in range(problems.count):
        entry = problems.items[i]
        out.append(Problem(entry.message.decode("utf-8"),
                           entry.path.decode("utf-8"),
                           ProblemSeverity(entry.severity),
                           ProblemOrigin(entry.origin)))
    libparser().free_lattice_problems(problems)
    return out


def _report_problems(problems: List[Problem], mode) -> None:
    """Apply the ``problems`` output policy: ``"print"`` (the default) writes to
    stderr, ``"none"`` does nothing, and anything else names a file to write."""
    if mode == "none":
        return
    if mode == "print":
        if problems:
            print(f"parse_and_expand_pals: {len(problems)} problem(s) encountered "
                  "during lattice expansion:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
        return
    with open(mode, "w") as out:
        if not problems:
            out.write("No problems encountered during lattice expansion.\n")
        else:
            out.write(f"{len(problems)} problem(s) encountered during lattice "
                      "expansion:\n")
            for problem in problems:
                out.write(f"  - {problem}\n")


def parse_and_expand_pals(filename, root_lattice: str = "", *,
                          problems: Union[str, os.PathLike] = "print") -> Lattices:
    """Parse a PALS lattice file and return its five views.

    Returns a :class:`~palsparserpy.structs.Lattices` holding the ``original``,
    ``combined``, ``expanded``, ``full_expanded`` and ``adjunct`` views together
    with the list of expansion ``problems``.

    Args:
      filename: Path to the top-level YAML lattice file.
      root_lattice: Name of the lattice to expand. If empty (the default), the
        lattice to expand is chosen with the following priority:

          1. the lattice named by the last ``use`` statement, or
          2. the last lattice defined in the file if no ``use`` statement is
             present.

      problems: What to do with the list of problems found while expanding
        (undefined lattice, dangling element/line references, undefined
        ``inherit``/``repeat``/``Fork`` targets, and expressions that could not be
        evaluated). One of:

          - ``"print"`` (the default) -- print the problems to ``stderr``
            (nothing is printed when there are none);
          - ``"none"`` -- do nothing (no printing, no file);
          - any other path -- write the problems to that file, printing nothing.
            Those two names are reserved, so a report cannot be written to a file
            called ``print`` or ``none``.

    The same problems handed to ``problems`` are also returned in the
    ``problems`` field regardless of the reporting mode, so ``"none"`` still lets
    the caller inspect them programmatically. Each entry carries a ``message``,
    the ``path`` it was found at, a ``severity`` and an ``origin``; only a
    ``PROBLEM_INPUT`` can be cleared by editing the lattice.

    The five tree views are:

    - ``original``: the tree as read in, mapping each file (including any
      file it includes or loads) to its unparsed contents.
    - ``combined``: the tree with all ``include`` directives resolved and spliced
      inline, and every ``load`` merged in subnode by subnode.
    - ``full_expanded``: the selected lattice fully expanded, and nothing else --
      scalars substituted with their full definitions, every ``repeat`` unrolled,
      every ``inherit`` merged in, forks resolved, ``set``
      commands executed and ABSOLUTE controllers applied. It is rooted at a map
      holding the single ``name -> Lattice`` entry, without the ``PALS``/
      ``facility`` scaffolding the lattice was defined under, so the lattice is
      reached as ``lat.full_expanded["lat1"]`` rather than through
      ``["PALS"]["facility"]``. Its ``branches`` entries are branches, not the
      ``BeamLine``s they were built from, and so carry no ``kind``; a
      ``BeamLine`` referenced inside a ``line`` is a sub-line whose contents are
      spliced directly into the enclosing line, so no nested ``BeamLine``
      survives in the expanded tree. Elements of a ``multipass`` line carry a
      ``multipass_index`` giving their pass number -- how many times a particle
      will have travelled through that physical element by that point -- so every
      element of one traversal shares an index (the nearest enclosing
      ``multipass`` line wins when they nest). Every dependent parameter is
      computed and present: each element carries its ``element_index`` (its
      position, counting from one, in the branch line that holds it), its
      ``ReferenceP``, ``FloorP`` and ``s_position``, the derived members of every
      parameter family it uses, and the non-zero defaults of the groups it
      carries; each branch is capped with a ``branch_end`` ``Placeholder``
      holding its final reference and floor, numbered with the rest.
    - ``expanded``: the same lattice with all of that removed -- what the author
      wrote decides which parameters stay. It is ``full_expanded`` with nodes
      pruned rather than an earlier snapshot, so a parameter present in both
      views holds the same value in both. Use it to see the inputs rather than
      their consequences, or to write a lattice back out without the computed
      values.
    - ``adjunct``: everything the expanded views do not carry, keeping its
      ``PALS``/``facility`` scaffolding: element and beamline definitions,
      ``use`` statements, constants and variables, ``Controller``s, ``set``
      commands, and any ``Lattice`` that was not the one expanded. A definition
      that expansion substituted into the lattice is *copied*, so it appears in
      both trees.

    Every mathematical expression is evaluated to a number across the expanded
    views and ``adjunct`` (see :func:`evaluate_pals_expression`;
    ``random()``/``random_gauss()`` are left as text). ``Controller`` elements are
    evaluated against their own scoped variable tables, with each control
    ``expression`` computed and stored back in its control entry; controllers are
    facility-level, so they are found in ``adjunct``.

    Each view is backed by its own tree; all five are freed independently when
    their nodes are garbage collected.
    """
    filename = os.fspath(filename)
    if not os.path.isfile(filename):
        raise FileNotFoundError(f"File not found: {filename}")

    handles = libparser().parse_and_expand_PALS(encode(filename),
                                                encode(root_lattice))

    # Take ownership of the problem list before anything can raise.
    problem_list = _take_problem_list(handles.problems)

    # NULL handles mean a fatal parse failure (a malformed top-level file):
    # there is no tree to expand. The C library reports why -- with the offending
    # line/column -- as the single problem, so surface that rather than a bare
    # failure.
    if not all((handles.original, handles.combined, handles.expanded,
                handles.full_expanded, handles.adjunct)):
        detail = "" if not problem_list else \
            "\n  " + "\n  ".join(p.message for p in problem_list)
        raise PALSParseError(f"Failed to parse lattice file: {filename}{detail}")

    _report_problems(problem_list, problems)

    return Lattices(_root_node(handles.original),
                    _root_node(handles.combined),
                    _root_node(handles.expanded),
                    _root_node(handles.full_expanded),
                    _root_node(handles.adjunct),
                    problem_list)


# ─── expression evaluation ───────────────────────────────────────────────────

def evaluate_pals_expression(expr: str) -> float:
    """Evaluate a single PALS mathematical expression to a ``float``.

    Supports the full PALS expression grammar: arithmetic (``+ - * / ^``), unary
    signs, parentheses, the built-in constants (``pi``, ``c_light``,
    ``r_electron``, ...), the math functions (``sqrt``, ``log``, ``sin``,
    ``floor``, ``modulo``, ...), and the particle-data functions ``mass_of``,
    ``charge_of`` and ``anomalous_moment_of`` (backed by
    AtomicAndPhysicalConstantsCLib), whose species-name argument must be quoted,
    e.g. ``mass_of("#3He")`` (a mass number carries a leading ``#``). A leading
    ``expr(...)`` wrapper is accepted and unwrapped.

    This evaluates a standalone string, so user-defined constants and variables
    are **not** in scope -- use :func:`parse_and_expand_pals` for whole-lattice
    evaluation, whose expanded trees already have every expression resolved to a
    number. Raises ``ValueError`` if ``expr`` is not evaluable: a parse error, an
    unknown identifier or species, a ``random()``/``random_gauss()`` expression
    (which is intentionally deferred), or a non-finite result.

    Example:
        >>> evaluate_pals_expression("3.75e7 / c_light^2")     # 4.172...e-10
        >>> evaluate_pals_expression('mass_of("electron")')    # 510998.95069...
        >>> evaluate_pals_expression("expr(2 * pi)")           # 6.283...
    """
    ok = ctypes.c_bool(False)
    value = libparser().evaluate_pals_expression(encode(expr), ctypes.byref(ok))
    if not ok.value:
        raise ValueError(f'Not an evaluable PALS expression: "{expr}"')
    return value


# ─── node correspondence ─────────────────────────────────────────────────────

def node_correspondence(lat: Lattices) -> Dict[YAMLNode, NodeCorrespondence]:
    """Map every node of a lattice to the nodes it corresponds to across the
    ``original``, ``combined``, ``full_expanded`` and ``adjunct`` trees.

    The correspondence is exact: it is computed from provenance recorded while
    the trees were derived from one another (``original`` -> ``combined`` ->
    ``full_expanded`` and ``adjunct``), not by re-matching after the fact.
    Because expansion can duplicate a node (scalar substitution, ``repeat``,
    ``inherit``, forks), the correspondence is one-to-many -- a single
    ``combined``/``original`` node can map to several ``full_expanded`` copies --
    so each field of the returned value is a list of nodes.

    Expansion splits the document, so a node of ``combined`` may land in
    ``full_expanded``, in ``adjunct``, or in both: a definition that was
    substituted into the lattice is copied there while its definition stays
    behind. Those copies share one equivalence class, tied together through the
    ``combined`` node they came from.

    The ``expanded`` view takes no part in the correspondence: it is a pruned
    copy of ``full_expanded`` rather than a step in the derivation chain, so a
    node in it is found by the path it sits at, not by a recorded link.

    Returns:
      A ``dict`` keyed by node. For any node that participates in the
      correspondence, ``corr[node]`` is a
      :class:`~palsparserpy.structs.NodeCorrespondence` --
      ``(original, combined, full_expanded, adjunct)`` -- listing every
      corresponding node grouped by tree. The queried node appears in its own
      tree's list, so the four lists together are the full equivalence class of
      ``node``. A list is empty when a tree has no corresponding node (e.g. the
      synthesised ``destination_pointer`` scalar exists only in
      ``full_expanded``, and a constant that the lattice never references exists
      only in ``adjunct``).

    Example:
        >>> lat = parse_and_expand_pals("lattice.pals.yaml")
        >>> corr = node_correspondence(lat)
        >>> a_const = lat.combined["PALS"]["facility"][0]["constants"]["a_const"]
        >>> corr[a_const].original        # the same constant in the original tree
        >>> corr[a_const].adjunct         # constants are not part of the lattice
        >>> corr[a_const].full_expanded   # empty unless the lattice referenced it
    """
    lib = libparser()
    cmap = lib.build_correspondence_map(lat.original.tree.handle,
                                        lat.combined.tree.handle,
                                        lat.full_expanded.tree.handle,
                                        lat.adjunct.tree.handle)
    try:
        links = [(cmap.links[i].original, cmap.links[i].combined,
                  cmap.links[i].full_expanded, cmap.links[i].adjunct)
                 for i in range(cmap.count)]
    finally:
        lib.free_correspondence_map(cmap)

    # Each participating node is a (tree tag, id) key. A link ties together the
    # original/combined nodes of one logical entity with its copy in one of the
    # two derived trees; union those keys and then read off the connected
    # components. Copies that share a combined node -- the same definition in
    # `full_expanded` and in `adjunct` -- are joined transitively through it.
    parent: Dict[tuple, tuple] = {}

    def add(key):
        parent.setdefault(key, key)
        return key

    def find(key):
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:      # path compression
            parent[key], key = root, parent[key]
        return root

    def union(a, b):
        parent[find(a)] = find(b)

    for original, combined, full_expanded, adjunct in links:
        # A link names a node in exactly one of the two derived trees.
        derived = add(("full_expanded", full_expanded)) \
            if full_expanded != YAML_NULL_ID else add(("adjunct", adjunct))
        if combined != YAML_NULL_ID:
            key_combined = add(("combined", combined))
            union(derived, key_combined)
            if original != YAML_NULL_ID:
                union(key_combined, add(("original", original)))

    # Gather the members of each connected component.
    groups: Dict[tuple, list] = {}
    for key in parent:
        groups.setdefault(find(key), []).append(key)

    trees = {"original": lat.original.tree, "combined": lat.combined.tree,
             "full_expanded": lat.full_expanded.tree, "adjunct": lat.adjunct.tree}

    def node_of(key):
        return YAMLNode(trees[key[0]], key[1])

    result: Dict[YAMLNode, NodeCorrespondence] = {}
    for members in groups.values():
        entry = NodeCorrespondence(
            original=[node_of(k) for k in members if k[0] == "original"],
            combined=[node_of(k) for k in members if k[0] == "combined"],
            full_expanded=[node_of(k) for k in members if k[0] == "full_expanded"],
            adjunct=[node_of(k) for k in members if k[0] == "adjunct"])
        for key in members:
            result[node_of(key)] = entry
    return result


# ─── name matching ───────────────────────────────────────────────────────────

def match_names(node: YAMLNode, match_string: str) -> List[YAMLNode]:
    """Every named construct in ``node``'s tree that is matched by
    ``match_string``, following PALS *Name Matching*.

    ``node`` may be any node of the tree to search (typically a lattice-view root
    such as ``lat.full_expanded``); the whole tree is searched and the returned
    nodes belong to that same tree.

    ``match_string`` has the form::

        [{lattice}>>>][{branch}>>][{kind}::]{name}[>{group}.{sub}. ... .{parameter}]

    ``{lattice}``, ``{branch}`` and ``{name}`` are `PCRE2 <https://www.pcre.org>`_
    patterns matched against the whole name (anchored at both ends); ``{kind}``
    is matched exactly; the parameter path after the single ``>`` is matched
    exactly, key by key. An omitted or empty pattern matches any name at that
    level. ``{branch}`` matches an element if any enclosing BeamLine/Branch name
    matches, so elements in sub-lines are included.

    The node returned for each match is whatever the string resolves to: the
    element node (no parameter path), the parameter-group or parameter node (with
    a path), or -- for a bare name (no lattice/branch/kind qualifier and no
    parameter path) -- additionally each matching constant and variable defined
    directly under the ``PALS`` or ``facility`` node (both the full
    ``kind: constant``/``kind: variable`` and the compact
    ``constants:``/``variables:`` forms). Lattice parameters therefore include
    constant and variable names.

    Which tree to search follows from that: elements are in
    ``lat.full_expanded``, while constants and variables are defined at facility
    level and so are found in ``lat.adjunct``. Searching ``lat.full_expanded``
    for a constant matches nothing, since the ``PALS``/``facility`` node it would
    be defined under is not part of that tree.

    Not yet implemented from *Element Name Matching*: ``#N`` instance selection,
    ``{e1}:{e2}`` ranges, ``,`` unions, and ``&`` intersections.

    Results are de-duplicated and returned in document order. A malformed pattern
    yields an empty list.

    Example:
        >>> lat = parse_and_expand_pals("lattice.pals.yaml")
        >>> match_names(lat.full_expanded, "B1.*>BendP.e1")   # e1 of every B1... bend
        >>> match_names(lat.full_expanded, "Quadrupole::.*")  # every quadrupole
        >>> match_names(lat.full_expanded, "inj>>>arc>>Q.*>length")
        >>> match_names(lat.adjunct, "a_.*")                  # constants/variables
    """
    lib = libparser()
    matches = lib.match_names(node.tree.handle, encode(match_string))
    try:
        ids = [matches.nodes[i] for i in range(matches.count)]
    finally:
        lib.free_name_matches(matches)
    return [YAMLNode(node.tree, node_id) for node_id in ids]


# ─── parameter values ────────────────────────────────────────────────────────

def _param_value_result(value: ParamValueC) -> Union[float, str, None]:
    """Turn the raw ``param_value`` returned by the C API into a Python value: a
    ``float`` for a number, a ``str`` for a string (copied out, then the owning C
    string is freed), or ``None``."""
    if value.kind == PARAM_VALUE_NUMBER:
        return value.number
    if value.kind != PARAM_VALUE_STRING:
        return None
    return take_string(value.string)


def parameter_value(lat: Lattices, match_string: str) -> Union[float, str, None]:
    """The value of the lattice parameter named by ``match_string``, looked up in
    the expanded lattice ``lat``.

    ``match_string`` uses the same PALS *Name Matching* syntax as
    :func:`match_names`. It names either an element parameter (with a
    ``>{group}.{sub}. ... .{parameter}`` path) or, as a *bare* name (no
    lattice/branch/kind qualifier and no path), a constant or variable -- the
    same constructs :func:`match_names` resolves.

    Only two of ``lat``'s five views are searched: ``lat.full_expanded``, which
    holds the element parameters, and then, if the name is not found there,
    ``lat.adjunct``, which holds the facility-level constants, variables, and any
    definitions not spliced into the lattice. The raw ``lat.original`` and
    ``lat.combined`` views are **not** searched -- they carry unevaluated,
    pre-expansion text. ``lat.expanded`` is not searched either: a dependent
    parameter is a legitimate thing to ask for, and only ``full_expanded``
    carries one.

    Because both searched views are post-expansion, values come back already
    evaluated: a numeric value as a ``float``, and a non-numeric one (e.g. a
    species name like ``"#3He"``, or an expression expansion left unevaluated
    such as one using ``random()``) verbatim as a ``str``.

    The value is resolved as follows:

      - **Element parameter, set:** its value -- a ``float``, or a ``str`` when
        non-numeric.
      - **Element parameter, not set:** the parameter's default is returned
        (``0.0`` for every parameter, for now -- real per-parameter defaults come
        later).
      - **Constant or variable (bare name):** its value, the same way.
      - **Nothing identified:** ``None``, when the name matches nothing in either
        view, names a bare element (an element has no single scalar value), stops
        on a whole parameter group, or several matches carry conflicting values.

    Example:
        >>> lat = parse_and_expand_pals("lattice.pals.yaml")
        >>> parameter_value(lat, "quad1>MagneticMultipoleP.Bn1")  # 1.0
        >>> parameter_value(lat, "quad1>BendP.g")                 # 0.0 (unset)
        >>> parameter_value(lat, "a_const")                       # from adjunct
        >>> parameter_value(lat, "quad1>nope.nope")               # None
    """
    value = libparser().get_lattice_parameter_value(
        lat.full_expanded.tree.handle, lat.adjunct.tree.handle,
        encode(match_string))
    return _param_value_result(value)
