"""
Translation of a PALS lattice into a Python script that builds an Xsuite lattice.
"""

from __future__ import annotations

import ast
import contextlib
import io
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ._common import (FullRepresentation, approx, ctrl_variables, facility_props,
                      fmt, name_value_pairs, tilt_rotation,
                      try_float, value_text)
from .node import YAMLNode
from .parser import evaluate_pals_expression

__all__ = ["XsuiteEle", "XsuiteBeamline", "XsuiteController", "XsuiteLattice",
           "pals_to_xsuite", "write_xsuite_file"]

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MULTIPOLE_RE = re.compile(r"^MagneticMultipoleP\.([KB])([ns])([0-9]+)(L?)$")
_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

#: The name of the Xsuite variable holding the *signed* magnetic rigidity ``P0/q``
#: of the reference particle, which the script defines when a lattice states a
#: field rather than a normalized strength.
#:
#: Every Xsuite magnet strength is normalized, as in MAD-X, so a PALS field has to
#: be divided by the rigidity. The script works it out from the reference particle
#: it has just built, so the division is left to Xsuite rather than done here.
_XSUITE_RIGIDITY = "pals_brho"

#: The speed of light in m/s, which the rigidity is worked out with.
_C_LIGHT = 299792458.0

#: The PALS predefined constants.
#:
#: Xsuite predefines no constants at all -- an expression naming ``pi`` fails --
#: so whichever of these an expression uses is defined at the top of the script,
#: with the value PALS gives it, in PALS units.
_PALS_CONSTANTS = ("pi", "c_light", "h_planck", "hbar", "k_boltzmann",
                   "r_electron", "r_proton", "e_charge", "mu_0", "epsilon_0",
                   "classical_radius_factor", "fine_structure", "n_avogadro")

#: The PALS functions an Xsuite expression has under the same name.
_XSUITE_FUNCTIONS = {"sqrt", "log", "exp", "sin", "cos", "tan", "sinc", "asin",
                     "acos", "atan", "atan2", "sinh", "cosh", "tanh", "abs",
                     "floor"}

#: The PALS functions Xsuite does not have but which are a simple rewrite of ones
#: it does, each as a template on its single argument.
_XSUITE_REWRITTEN_FUNCTIONS = {"cot": "(1 / tan({}))", "coth": "(1 / tanh({}))"}

#: The species a PALS lattice names that Xsuite spells differently. Anything else
#: is passed through and left for Xsuite's own particle table to recognize.
_XSUITE_SPECIES = {
    "positron": "positron", "electron": "electron", "proton": "proton",
    "anti-proton": "antiproton", "antiproton": "antiproton",
    "muon+": "muon+", "muon-": "muon-", "muon": "muon-",
    "deuteron": "deuteron", "pion+": "pion+", "pion-": "pion-"}


@dataclass
class XsuiteEle:
    """A single Xsuite element, which the script creates with ``env.new``.

    - ``name``: the element name.
    - ``type``: the ``xtrack`` element class (e.g. ``Drift``, ``Quadrupole``), or,
      for an element cloned from another, the name of the element it is cloned
      from.
    - ``attrs``: the element's keyword arguments, each a ``(keyword, source)``
      pair whose ``source`` is the Python text of the value: a number, or a
      quoted string Xsuite turns into a deferred expression.
    - ``knl``, ``ksl``: the multipole arrays, entry by entry in the same Python
      text, kept apart from ``attrs`` so a controller can lengthen them to reach
      an order the element did not set.
    - ``notes``: comment lines to write above the definition, holding the PALS
      ``MetaP`` and anything else worth saying about the element in the script.
    - ``before``, ``after``: elements the translation adds to stand in for what a
      single Xsuite element cannot hold -- an aperture is an element of its own in
      Xsuite, placed at the end of the element it bounds, and a PALS ``Patch``
      that both shifts and rotates is two. Wherever a line names this element,
      these come with it.
    """
    name: str
    type: str
    attrs: List[Tuple[str, str]] = field(default_factory=list)
    knl: List[str] = field(default_factory=list)
    ksl: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    before: List["XsuiteEle"] = field(default_factory=list)
    after: List["XsuiteEle"] = field(default_factory=list)


@dataclass
class XsuiteBeamline:
    """An Xsuite line, which the script creates with ``env.new_line``.

    - ``name``: the line name.
    - ``members``: the ordered line items, each a ``(name, repeat)`` pair naming
      an element or another line and how many times it is repeated. A negative
      ``repeat`` reverses the order of the items, as it does in PALS.
    - ``particle_ref``: the ``xt.Particles`` arguments of the reference particle
      given by the line's own ``BeginningEle``, if it has one.
    """
    name: str
    members: List[Tuple[str, int]] = field(default_factory=list)
    particle_ref: List[str] = field(default_factory=list)


@dataclass
class XsuiteController:
    """An Xsuite rendering of a PALS ``Controller``.

    Xsuite has no controller element. What it has instead is the deferred
    expression: an element attribute assigned a string becomes an expression in
    the environment's variables and follows them from then on. So a controller's
    variables become Xsuite variables and each of its controls becomes an
    expression assigned to the attribute it drives.

    - ``name``: the controller name, written out as a comment heading.
    - ``vars``: the variables' initial values, each a ``(name, source)`` pair.
    - ``controls``: the deferred assignments, each a ``(target, expression)``
      pair where ``target`` is the Python text of the attribute, such as
      ``env['q1'].k1``.
    - ``notes``: comment lines to write above the definitions, holding the
      controller's ``MetaP``.
    """
    name: str
    vars: List[Tuple[str, str]] = field(default_factory=list)
    controls: List[Tuple[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class XsuiteLattice:
    """An in-memory model of an Xsuite lattice.

    Produced by :func:`pals_to_xsuite` and serialized to a Python script by
    :func:`write_xsuite_file`. The fields mirror the sections of the script:

    - ``constants``: variable definitions, each a ``(name, source)`` pair, in
      definition order.
    - ``particle_ref``: the ``xt.Particles`` arguments of the reference particle
      (species and energy).
    - ``twiss_init``: the initial Twiss and dispersion, each a ``(keyword,
      source)`` pair for ``Line.twiss``.
    - ``particle_start``: the initial particle coordinates, each a ``(keyword,
      source)`` pair for ``Line.build_particles``.
    - ``elements``: element definitions (:class:`XsuiteEle`).
    - ``controllers``: variables and deferred expressions
      (:class:`XsuiteController`).
    - ``beamlines``: line definitions (:class:`XsuiteBeamline`).
    - ``branches``: the branches of the lattice, each a ``(name, periodic)``
      pair.
    - ``use``: the branch the script makes its ``line``: the first branch of the
      lattice the last PALS ``use`` names, or of the last lattice defined.
    - ``rigidity``: whether anything written refers to the rigidity variable,
      which then has to be defined ahead of it.
    - ``pals_constants``: the PALS predefined constants the script refers to,
      which it has to define because Xsuite does not, each a ``(name, source)``
      pair.
    """
    constants: List[Tuple[str, str]] = field(default_factory=list)
    particle_ref: List[str] = field(default_factory=list)
    twiss_init: List[Tuple[str, str]] = field(default_factory=list)
    particle_start: List[Tuple[str, str]] = field(default_factory=list)
    elements: List[XsuiteEle] = field(default_factory=list)
    controllers: List[XsuiteController] = field(default_factory=list)
    beamlines: List[XsuiteBeamline] = field(default_factory=list)
    branches: List[Tuple[str, bool]] = field(default_factory=list)
    use: Optional[str] = None
    rigidity: bool = False
    pals_constants: List[Tuple[str, str]] = field(default_factory=list)


class _Extensions:
    """The extension labels a PALS file declares, and the extensions met.

    A PALS file may carry data for one program that no other has a use for, and
    marks it either with an ``extension`` key inside the block or by declaring
    the block's name, or a prefix or suffix of it, under ``PALS:
    extension_labels``. Such a block is not a parameter Xsuite is missing, so
    rather than being reported element by element it is reported once, by name,
    at the end of the translation.
    """

    def __init__(self, pals: YAMLNode):
        self.names: Set[str] = set()
        self.prefixes: Set[str] = set()
        self.suffixes: Set[str] = set()
        self.seen: List[str] = []
        if "extension_labels" in pals:
            labels = pals["extension_labels"]
            for part, target in (("names", self.names),
                                 ("prefixes", self.prefixes),
                                 ("suffixes", self.suffixes)):
                if part in labels and labels[part].is_map():
                    target.update(labels[part].keys())

    def skip(self, key: str, node: YAMLNode) -> bool:
        """Whether ``key`` (holding ``node``) is an extension, noting it if so."""
        is_extension = (key in self.names or
                        any(key.startswith(p) for p in self.prefixes) or
                        any(key.endswith(x) for x in self.suffixes) or
                        (node.is_map() and "extension" in node))
        if is_extension and key not in self.seen:
            self.seen.append(key)
        return is_extension


# ---------------------------------------------------------------------------
def pals_to_xsuite(yaml: YAMLNode) -> XsuiteLattice:
    """Translate a parsed PALS lattice ``yaml`` (as returned by
    :func:`~palsparserpy.parse_file`) into an :class:`XsuiteLattice`.

    The returned structure is an in-memory model of the *Xsuite* lattice
    (elements, lines, reference particle), not the input PALS tree. Translation
    is a three-step process: parse the PALS file with ``parse_file``, build the
    target model with ``pals_to_xsuite``, then emit the Python script that builds
    the lattice with :func:`write_xsuite_file`::

        yaml = parse_file(file_dir)
        write_xsuite_file(pals_to_xsuite(yaml), filename)

    Controllers are translated after the elements, in a second pass: a
    ``control_type: RELATIVE`` controller adds to the value the element already
    carries, and the only place that value is written down is the element
    definition this pass has just built.
    """
    pals = yaml["PALS"]
    facility = pals["facility"]
    lat = XsuiteLattice()
    extensions = _Extensions(pals)

    # Constants and variables may be defined directly under `PALS` as well as in
    # the facility.
    for key in ("constants", "variables"):
        if key in pals:
            lat.constants.extend(_xsuite_constants(pals[key]))

    if "girders" in pals and len(pals["girders"]) > 0:
        raise ValueError("Xsuite has no girders: the `PALS: girders` cannot be "
                         "translated")

    controllers: List[YAMLNode] = []
    placeholders = set()
    lattices: Dict[str, List[str]] = {}     # Lattice -> its branches
    used: Optional[str] = None
    for ele in facility:
        props = ele.child(0)
        # The compact `constants:`/`variables:` list is a facility entry in its
        # own right, with no `kind` of its own; every other entry the translation
        # looks at is a named element.
        if props.node_key() in ("constants", "variables"):
            lat.constants.extend(_xsuite_constants(props))
            continue
        if props.node_key() == "use" and not (props.is_map() or
                                              props.is_sequence()):
            used = props.value
            continue
        if "kind" not in props:
            continue
        pals_kind = props["kind"].value
        if pals_kind == "BeginningEle":
            ref, twiss, particle = _ele_to_xsuite_str(ele, extensions)
            if not lat.particle_ref:
                lat.particle_ref.extend(ref)
            lat.twiss_init.extend(twiss)
            lat.particle_start.extend(particle)
        elif pals_kind == "BeamLine":
            bl, inline = _make_xsuite_line(ele, facility, extensions)
            lat.beamlines.append(bl)
            lat.elements.extend(inline)
        elif pals_kind == "Lattice":
            before = len(lat.branches)
            _add_xsuite_branches(lat, props["branches"])
            lattices[props.node_key()] = [b for b, _ in lat.branches[before:]]
        elif pals_kind == "Controller":
            controllers.append(ele)
        elif pals_kind in ("constant", "variable"):
            lat.constants.append(_xsuite_constant(props, pals_kind))
        elif pals_kind == "Placeholder":
            # A Placeholder holds a place while a PALS lattice is built and is
            # gone once it is.
            placeholders.add(props.node_key())
        else:
            lat.elements.append(_make_xsuite_ele(ele, extensions))

    # A name the facility does not define would fail when the script is run, far
    # from the line that holds it, so it is reported here instead.
    defined = {e.name for e in lat.elements} | {b.name for b in lat.beamlines}
    by_name = {e.name: e for e in lat.elements}
    for ele in list(lat.elements):
        if not ele.type.startswith("'"):
            continue
        parent = ast.literal_eval(ele.type)
        if parent not in defined:
            print(f"{ele.name}: inherits from {parent}, which is not defined in "
                  "the facility, not translated")
            lat.elements.remove(ele)
            defined.discard(ele.name)
            continue
        # A clone has the apertures and patch halves of what it is cloned from,
        # each cloned in turn.
        if parent in by_name:
            for parts, own in ((by_name[parent].before, ele.before),
                               (by_name[parent].after, ele.after)):
                own.extend(XsuiteEle(ele.name + p.name[len(parent):], repr(p.name))
                           for p in parts)
    for bl in lat.beamlines:
        for member, _ in bl.members:
            if member not in defined and member not in placeholders:
                print(f"BeamLine {bl.name}: `{member}` is not defined in the "
                      "facility, not translated")
        bl.members[:] = [m for m in bl.members if m[0] in defined]
    lines = {b.name for b in lat.beamlines}
    for name, _ in lat.branches:
        if name not in lines:
            print(f"Lattice branch `{name}` is not a BeamLine defined in the "
                  "facility, not translated")
    lat.branches[:] = [b for b in lat.branches if b[0] in lines]
    lattices = {k: [b for b in v if b in lines] for k, v in lattices.items()}
    # The lattice PALS expands is the one the last `use` names, or else the last
    # one defined; its first branch is the script's `line`.
    chosen = used if used in lattices else (list(lattices)[-1] if lattices
                                            else None)
    if chosen is not None and lattices[chosen]:
        lat.use = lattices[chosen][0]
    # With no BeginningEle of its own in the facility, the environment takes the
    # reference particle of the first line that states one.
    if not lat.particle_ref:
        lat.particle_ref.extend(next((bl.particle_ref for bl in lat.beamlines
                                      if bl.particle_ref), []))

    # A PALS controller owns its variables and Xsuite has no such scope, so what
    # each one is called in the script has to be settled before any of them is
    # written out or referred to.
    varmap, initials = _xsuite_variable_names(controllers, lat.constants)
    for ele in controllers:
        lat.controllers.append(
            _make_xsuite_controller(ele, facility, lat, varmap, initials))
    _check_xsuite_names(lat)

    sources = _xsuite_sources(lat)
    lat.rigidity = any(_XSUITE_RIGIDITY in s for s in sources)
    for name in _PALS_CONSTANTS:
        pattern = re.compile(f"(?<![A-Za-z0-9_.]){name}(?![A-Za-z0-9_])")
        if any(pattern.search(s) for s in sources if not _NUMBER_RE.match(s)):
            value = "math.pi" if name == "pi" else \
                fmt(evaluate_pals_expression(name))
            lat.pals_constants.append((name, value))
    for key in extensions.seen:
        print(f"`{key}` is a PALS extension, which Xsuite has no use for, not "
              "translated")
    if lat.rigidity and not lat.particle_ref:
        raise ValueError("a field strength has to be divided by the reference "
                         "rigidity, and no BeginningEle states the reference "
                         "particle it is worked out from")
    return lat


# ---------------------------------------------------------------------------
def _xsuite_sources(lat: XsuiteLattice) -> List[str]:
    """The Python text of every value the script will write, for the checks that
    look for a name in any of them."""
    sources = [s for _, s in lat.constants]
    sources.extend(lat.particle_ref)
    sources.extend(s for _, s in lat.twiss_init + lat.particle_start)
    for bl in lat.beamlines:
        sources.extend(bl.particle_ref)

    def add(ele: XsuiteEle):
        sources.extend(s for _, s in ele.attrs)
        sources.extend(ele.knl)
        sources.extend(ele.ksl)
        for part in ele.before + ele.after:
            add(part)

    for ele in lat.elements:
        add(ele)
    for ctrl in lat.controllers:
        sources.extend(s for _, s in ctrl.vars)
        sources.extend(s for _, s in ctrl.controls)
    return sources


# ---------------------------------------------------------------------------
def _xsuite_variable_names(controllers: List[YAMLNode],
                           constants: List[Tuple[str, str]]
                           ) -> Tuple[Dict[Tuple[str, str], str], Dict[str, str]]:
    """Decide what each controller variable is called in the Xsuite environment.

    Returns ``(names, initials)`` where ``names`` maps a
    ``(controller, variable)`` pair to its Xsuite name and ``initials`` maps that
    Xsuite name to the variable's initial value.

    A PALS controller owns its variables: ``ps1>cur`` and ``ps2>cur`` are two
    independent knobs. An Xsuite variable is a name in the one namespace the
    whole environment shares, so a variable whose bare name is claimed by another
    controller, or by a constant, is prefixed with the controller that owns it.
    One that is claimed by nobody else keeps its bare name.
    """
    claimed: Dict[str, int] = {}
    for ele in controllers:
        for var, _ in ctrl_variables(ele.child(0)):
            claimed[var] = claimed.get(var, 0) + 1
    taken = {name for name, _ in constants}

    names: Dict[Tuple[str, str], str] = {}
    initials: Dict[str, str] = {}
    for ele in controllers:
        cname = ele.child(0).node_key()
        for var, value in ctrl_variables(ele.child(0)):
            xsuite = f"{cname}__{var}" if (claimed[var] > 1 or var in taken) else var
            names[(cname, var)] = xsuite
            initials[xsuite] = value
    return names, initials


# ---------------------------------------------------------------------------
def _check_xsuite_names(lat: XsuiteLattice) -> None:
    """Report two definitions that would claim the one name in the environment.

    An Xsuite environment keeps its variables, elements and lines in one
    namespace -- ``env['q1']`` is whichever of them is called ``q1`` -- where
    PALS keeps its constants apart from its elements. So a constant named after
    an element, or two definitions of one variable, cannot both be written out.
    """
    seen: Dict[str, str] = {}

    def claim(name: str, what: str):
        if name in seen and seen[name] != what:
            raise ValueError(f"`{name}` is defined by both {seen[name]} and "
                             f"{what}; an Xsuite environment keeps variables, "
                             "elements and lines in one namespace")
        seen[name] = what

    for name, _ in lat.constants:
        claim(name, "a constant or variable definition")
    for ctrl in lat.controllers:
        for name, _ in ctrl.vars:
            if name in seen:
                raise ValueError(f"controller {ctrl.name}: variable `{name}` is "
                                 f"already defined by {seen[name]}; Xsuite "
                                 "variables are global, so the two would drive one "
                                 "another")
            seen[name] = f"controller {ctrl.name}"
    for ele in lat.elements:
        for part in ele.before + [ele] + ele.after:
            claim(part.name, "an element")
    for bl in lat.beamlines:
        claim(bl.name, "a BeamLine")


# ---------------------------------------------------------------------------
def _xsuite_substitute(expr: str, replacements: Dict[str, str]) -> str:
    """``expr`` with each name in ``replacements`` replaced by what it maps to.

    Used to put a controller's variables into an expression under whatever Xsuite
    calls them (see :func:`_xsuite_variable_names`), and to put their initial
    values in place of them. The match is on whole identifiers, and a name
    reached through a dot is left alone. Every name is replaced in one pass, so a
    replacement is never itself replaced.
    """
    if not replacements:
        return expr
    alternatives = "|".join(re.escape(name) for name in replacements)
    pattern = re.compile(f"(?<![A-Za-z0-9_.])({alternatives})(?![A-Za-z0-9_])")
    return pattern.sub(lambda m: replacements[m.group(1)], expr)


# ---------------------------------------------------------------------------
def _add_xsuite_branches(lat: XsuiteLattice, branches: YAMLNode) -> XsuiteLattice:
    """Translate a PALS ``Lattice``'s ``branches`` sequence into ``lat``.

    Append each branch to ``lat.branches`` as a ``(name, periodic)`` pair. An
    Xsuite line has no geometry attribute: whether it closes on itself is decided
    by how it is used -- ``Line.twiss`` given no initial conditions looks for the
    periodic solution -- so the flag is carried through to the comment
    :func:`write_xsuite_file` writes beside the line.
    """
    for bl in branches:
        if bl.is_scalar():
            name = bl.value
            periodic = False
        elif bl.is_map():
            bl_props = bl.child(0)
            name = bl_props.node_key()
            periodic = ("periodic" in bl_props and
                        bl_props["periodic"].value.lower() == "true")
        elif bl.is_sequence():
            raise ValueError("Expanding lattices is not done during PALS>Xsuite "
                             "translation")
        else:
            raise ValueError(f"This object is neither a scalar, map, nor "
                             f"sequence: {bl!r}")
        if name not in (b for b, _ in lat.branches):
            lat.branches.append((name, periodic))
    return lat


# ---------------------------------------------------------------------------
def write_xsuite_file(lat: XsuiteLattice, filename) -> None:
    """Serialize the :class:`XsuiteLattice` ``lat`` to ``filename`` as a Python
    script that builds the lattice in an ``xtrack.Environment``.

    The script defines the environment ``env``, the reference particle, every
    element and line, and ends with ``line``, the first branch of the lattice,
    ready to track or twiss. It also defines ``twiss_init`` and
    ``particle_start``, the initial conditions the PALS ``BeginningEle`` states,
    as keyword arguments for ``Line.twiss`` and ``Line.build_particles``. Run it,
    or execute it from another script, to get the lattice::

        python lattice.py
        exec(open("lattice.py").read())   # env, line, ... are now defined

    The sections come in the order Python needs them in: a variable has to exist
    before an expression names it, and an element before a line holds it.
    """
    elements = {ele.name: ele for ele in lat.elements}
    with open(filename, "w") as out:
        out.write('"""Xsuite lattice translated from a PALS lattice by '
                  'PALSParserPy."""\n\n')
        out.write("import math\n\nimport xtrack as xt\n\nenv = xt.Environment()\n")

        # Head each section with the same rule and title, one blank line clear of
        # the one before.
        def section(title):
            out.write("\n#=====================================================" +
                      "=================\n" + f"# {title}\n\n")

        if lat.pals_constants:
            section("PALS predefined constants")
            for name, source in lat.pals_constants:
                out.write(f"env[{name!r}] = {source}\n")

        if lat.constants:
            section("Constant and variable definitions")
            for name, source in lat.constants:
                out.write(f"env[{name!r}] = {source}\n")

        if lat.particle_ref or lat.twiss_init or lat.particle_start:
            section("Reference particle and initial conditions")
            if lat.particle_ref:
                out.write("env.particle_ref = xt.Particles(" +
                          ", ".join(lat.particle_ref) + ")\n")
            # A field the lattice states rather than normalizes is divided by
            # this. The PALS normalization is by the signed charge.
            if lat.rigidity:
                out.write("\n# Signed magnetic rigidity P0/q [T*m], which "
                          "normalizes a stated field.\n"
                          f"env[{_XSUITE_RIGIDITY!r}] = float(env.particle_ref."
                          f"p0c[0] / ({_C_LIGHT!r} * env.particle_ref.q0))\n")
            if lat.twiss_init:
                out.write("\n# Initial Twiss parameters, for an open line:\n"
                          "#   line.twiss(start=xt.START, end=xt.END, "
                          "**twiss_init)\n"
                          "twiss_init = dict(" +
                          ", ".join(f"{k}={v}" for k, v in lat.twiss_init) + ")\n")
            if lat.particle_start:
                out.write("\n# Initial particle coordinates:\n"
                          "#   particles = line.build_particles(**particle_start)\n"
                          "particle_start = dict(" +
                          ", ".join(f"{k}={v}" for k, v in lat.particle_start) +
                          ")\n")

        section("Element definitions")
        for ele in lat.elements:
            for part in ele.before + [ele] + ele.after:
                out.write(_format_xsuite_ele(part) + "\n")

        if lat.controllers:
            section("Controller definitions")
            out.write("\n\n".join(_format_xsuite_controller(c)
                                  for c in lat.controllers) + "\n")

        section("Beamline definitions")
        lines = {bl.name for bl in lat.beamlines}
        for bl in _xsuite_line_order(lat.beamlines):
            out.write(_format_xsuite_line(bl, elements, lines) + "\n")
            if bl.particle_ref and bl.particle_ref != lat.particle_ref:
                out.write(f"env[{bl.name!r}].particle_ref = xt.Particles(" +
                          ", ".join(bl.particle_ref) + ")\n")

        section("Branch structure")
        if lat.branches:
            out.write("branches = {\n")
            for name, periodic in lat.branches:
                geometry = "closed" if periodic else "open"
                out.write(f"    {name!r}: env[{name!r}],\t# {geometry}\n")
            out.write("}\n")
            out.write(f"line = branches[{(lat.use or lat.branches[0][0])!r}]\n")
        elif lat.beamlines:
            # A file with no Lattice still has its lines; the last one defined is
            # the one PALS would expand.
            out.write(f"line = env[{lat.beamlines[-1].name!r}]\n")


# ---------------------------------------------------------------------------
def _xsuite_line_order(beamlines: List[XsuiteBeamline]) -> List[XsuiteBeamline]:
    """``beamlines`` in an order that defines every line before a line that holds
    it.

    A PALS file may name a subline before defining it; a Python script cannot.
    Lines keep the order the file gave them wherever that already works. A line
    that holds itself, directly or not, is an error in PALS and is reported here.
    """
    by_name = {bl.name: bl for bl in beamlines}
    ordered: List[XsuiteBeamline] = []
    state: Dict[str, int] = {}     # 1: being placed, 2: placed

    def place(bl: XsuiteBeamline):
        if state.get(bl.name) == 2:
            return
        if state.get(bl.name) == 1:
            raise ValueError(f"BeamLine {bl.name} holds itself")
        state[bl.name] = 1
        for member, _ in bl.members:
            if member in by_name:
                place(by_name[member])
        state[bl.name] = 2
        ordered.append(bl)

    for bl in beamlines:
        place(bl)
    return ordered


# ---------------------------------------------------------------------------
def _format_xsuite_ele(ele: XsuiteEle) -> str:
    """Render an :class:`XsuiteEle` as an ``env.new(...)`` call, with each note on
    a comment line above."""
    text = "".join(f"# {note}\n" for note in ele.notes)
    cls = f"xt.{ele.type}" if not ele.type.startswith("'") else ele.type
    args = [repr(ele.name), cls]
    args.extend(f"{k}={v}" for k, v in ele.attrs)
    if ele.knl:
        args.append("knl=[" + ", ".join(ele.knl) + "]")
    if ele.ksl:
        args.append("ksl=[" + ", ".join(ele.ksl) + "]")
    return text + "env.new(" + ", ".join(args) + ")"


# ---------------------------------------------------------------------------
def _format_xsuite_controller(ctrl: XsuiteController) -> str:
    """Render an :class:`XsuiteController` as its variable definitions followed by
    the deferred expressions that depend on them, under a comment naming the
    controller they came from."""
    text = f"# Controller {ctrl.name}\n"
    for note in ctrl.notes:
        text += f"# {note}\n"
    for name, source in ctrl.vars:
        text += f"env[{name!r}] = {source}\n"
    return text + "\n".join(f"{t} = {e}" for t, e in ctrl.controls)


# ---------------------------------------------------------------------------
def _format_xsuite_line(bl: XsuiteBeamline, elements: Dict[str, XsuiteEle],
                        lines: Set[str]) -> str:
    """Render an :class:`XsuiteBeamline` as an ``env.new_line(...)`` call.

    An element the translation had to split -- an aperture or the second half of
    a patch -- is written out as all of its parts wherever the line names it. A
    repeated subline is written as Xsuite's own line arithmetic, ``n *
    env['sub']``, reversed with a minus sign when PALS reverses it; a repeated
    element is simply named that many times, its reversal being itself.
    """
    components: List[str] = []
    for name, repeat in bl.members:
        if name in lines:
            if repeat == 1:
                components.append(repr(name))
            elif repeat == -1:
                components.append(f"-env[{name!r}]")
            elif repeat > 0:
                components.append(f"{repeat} * env[{name!r}]")
            else:
                components.append(f"{-repeat} * (-env[{name!r}])")
            continue
        ele = elements.get(name)
        parts = [name] if ele is None else \
            [p.name for p in ele.before] + [name] + [p.name for p in ele.after]
        components.extend(repr(p) for p in parts * abs(repeat))

    text = f"env.new_line(name={bl.name!r}, components=["
    row = ""
    for component in components:
        if len(row) + len(component) > 70:
            text += "\n    " + row.rstrip()
            row = ""
        row += component + ", "
    if "\n" in text:
        return text + "\n    " + row.rstrip(", ") + ",\n])"
    return text + row.rstrip(", ") + "])"


# ---------------------------------------------------------------------------
def _xsuite_expression(where: str, text: str) -> str:
    """A PALS expression as an Xsuite one, reporting what Xsuite cannot evaluate.

    An Xsuite expression is a string its parser turns into a deferred expression
    in the environment's variables. Its arithmetic is PALS', with one trap: it
    parses ``-a^2`` as ``(-a)^2`` and ``a^b^c`` as ``(a^b)^c``, where PALS follows
    the usual rule and reads them as ``-(a^2)`` and ``a^(b^c)``. So the expression
    is parsed here, with PALS' precedence, and written back out with every
    operation in parentheses of its own.

    Most of PALS' functions Xsuite has under the same name; ``cot`` and ``coth``
    are rewritten in terms of ones it has, and the rest -- the particle-data
    functions, the random-number ones, and a few others -- are reported, the
    expression being carried across as it stands. PALS' predefined constants are
    left as names: the script defines whichever of them it uses.
    """
    try:
        tree = ast.parse(text.replace("^", "**").strip(), mode="eval").body
    except SyntaxError:
        print(f"{where}: `{text}` is not an expression Xsuite can parse, "
              "carried over as it stands")
        return text

    def emit(node) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return fmt(node.value)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.UnaryOp) and \
                isinstance(node.op, (ast.USub, ast.UAdd)):
            sign = "-" if isinstance(node.op, ast.USub) else "+"
            return f"({sign}{emit(node.operand)})"
        if isinstance(node, ast.BinOp):
            ops = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
                   ast.Pow: "**"}
            op = ops.get(type(node.op))
            if op is not None:
                return f"({emit(node.left)} {op} {emit(node.right)})"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and not node.keywords:
            fn = node.func.id
            args = [emit(a) for a in node.args]
            if fn in _XSUITE_REWRITTEN_FUNCTIONS and len(args) == 1:
                return _XSUITE_REWRITTEN_FUNCTIONS[fn].format(args[0])
            if fn not in _XSUITE_FUNCTIONS:
                print(f"{where}: `{fn}` is a PALS function with no Xsuite "
                      f"equivalent; `{text}` will not evaluate")
            return f"{fn}({', '.join(args)})"
        raise SyntaxError(ast.dump(node))

    try:
        result = emit(tree)
    except SyntaxError:
        print(f"{where}: `{text}` is not an expression Xsuite can parse, "
              "carried over as it stands")
        return text
    # The outermost parentheses say nothing.
    if result.startswith("(") and result.endswith(")") and \
            _balanced(result[1:-1]):
        result = result[1:-1]
    return result


# ---------------------------------------------------------------------------
def _balanced(text: str) -> bool:
    """Whether every parenthesis in ``text`` is closed within it."""
    depth = 0
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


# ---------------------------------------------------------------------------
def _xsuite_value(where: str, text: str) -> str:
    """A PALS value as the Python text of an Xsuite value.

    A number is written as a number, keeping the spelling the PALS file gave it
    when Python reads that spelling too. Anything else is an expression, which is
    translated (see :func:`_xsuite_expression`) and quoted, so that Xsuite makes
    it a deferred expression that follows the variables it names.
    """
    text = text.strip()
    if _NUMBER_RE.match(text):
        return text
    value = try_float(text)
    if value is not None and math.isfinite(value):
        return fmt(value)
    return repr(_xsuite_expression(where, text))


# ---------------------------------------------------------------------------
def _xsuite_number(where: str, text: str) -> str:
    """A PALS value as the Python text of a number, for the places a deferred
    expression cannot go: the reference particle and the initial conditions are
    plain arguments, not attributes of the environment.

    A number is written as :func:`_xsuite_value` writes it; an expression is
    handed to ``env.eval``, which evaluates it once, in the environment's
    variables, when the script runs.
    """
    source = _xsuite_value(where, text)
    return source if source[:1] not in "'\"" else f"env.eval({source})"


# ---------------------------------------------------------------------------
def _xsuite_scale(text: str, factor: float) -> str:
    """``text`` scaled by ``factor``, as a PALS value.

    A value written as a number is scaled here and comes out a number; one
    written as an expression -- a constant, say -- is left for Xsuite to evaluate
    and comes out an expression.
    """
    if factor == 1:
        return text
    value = try_float(text)
    return f"({text}) * {fmt(factor)}" if value is None else fmt(value * factor)


# ---------------------------------------------------------------------------
def _xsuite_shift(text: str, offset: float) -> str:
    """``text`` with ``offset`` added, as a PALS value. As with
    :func:`_xsuite_scale`, a number comes out a number and an expression comes
    out an expression."""
    if offset == 0:
        return text
    value = try_float(text)
    return f"({text}) + {fmt(offset)}" if value is None else fmt(value + offset)


# ---------------------------------------------------------------------------
def _xsuite_strength(value: float, normalized: bool) -> str:
    """A magnet strength as the Python text of an Xsuite value.

    Every Xsuite strength attribute is normalized, so a PALS component given as a
    field is divided by the reference rigidity instead of being written out as it
    stands.
    """
    return fmt(value) if normalized else repr(f"{fmt(value)} / {_XSUITE_RIGIDITY}")


# ---------------------------------------------------------------------------
def _xsuite_constants(node: YAMLNode) -> List[Tuple[str, str]]:
    """Translate a compact-form ``constants:``/``variables:`` list into Xsuite
    variable definitions.

    Xsuite draws no distinction between the two: both become a variable of the
    environment that the rest of the lattice may use in an expression, so both
    lists translate the same way.
    """
    return [(name, _xsuite_value(name, value))
            for name, value in name_value_pairs(node)]


# ---------------------------------------------------------------------------
def _xsuite_constant(props: YAMLNode, pals_kind: str) -> Tuple[str, str]:
    """Translate a full-form (``kind: constant``, ``kind: variable``) definition
    into an Xsuite variable definition.

    A definition whose ``value`` is a structure rather than a single value has no
    Xsuite equivalent and raises an error; one with no ``value`` at all takes
    PALS' default of zero. An Xsuite variable is a value and nothing else, so the
    error bars a PALS definition may carry are reported.
    """
    name = props.node_key()
    for err in ("absolute_error", "relative_error"):
        if err in props:
            print(f"{name}: an Xsuite variable carries no error bar, so {err} is "
                  "not translated")
    if "value" not in props:
        return name, "0"
    value = props["value"]
    if value.is_map() or value.is_sequence():
        raise ValueError(f"{name}: the `value` of a `{pals_kind}` is not a "
                         "single value")
    return name, _xsuite_value(name, value_text(value))


# ---------------------------------------------------------------------------
def _xsuite_species(species: str) -> str:
    """Map a PALS reference species onto a name Xsuite's particle table knows.

    Xsuite looks a species up by name and knows the common particles and the
    ions under names like ``Pb208``. A species outside the handful mapped here is
    passed through as it stands and reported, since Xsuite may spell it
    differently or not know it at all.
    """
    name = species.strip("\"' ")
    if name.lower() in _XSUITE_SPECIES:
        return _XSUITE_SPECIES[name.lower()]
    print(f"species_ref `{species}` is passed to Xsuite as it stands; check that "
          "Xsuite's particle table knows it")
    return name


# ---------------------------------------------------------------------------
def _ele_to_xsuite_str(ele: YAMLNode, extensions: Optional[_Extensions] = None
                       ) -> Tuple[List[str], List[Tuple[str, str]],
                                  List[Tuple[str, str]]]:
    """Translate a ``BeginningEle`` element into the Xsuite reference particle
    and initial conditions.

    Returns ``(ref, twiss, particle)`` where ``ref`` holds the ``xt.Particles``
    arguments from the element's ``ReferenceP`` (species and energy), ``twiss``
    holds the ``Line.twiss`` arguments from its ``TwissP`` (initial Twiss and
    dispersion), and ``particle`` holds the ``Line.build_particles`` arguments
    from its ``ParticleP`` (initial phase-space coordinates).

    Xsuite's coordinates are PALS' under other names: its ``zeta`` is PALS' ``z``
    and its ``delta``, which its dispersion is measured against, is PALS' ``pz``.
    Its energies are in eV, as PALS' are. What does not survive the crossing:

      - PALS states the Twiss parameters in the ``a``/``b`` normal modes and
        Xsuite's initial conditions in the ``x``/``y`` planes, which are the same
        thing only when the lattice is uncoupled.
      - The coupling itself is stated as Bmad's ``C`` matrix, which Xsuite has no
        initial condition for, so ``cmat11`` and its fellows are not translated.
      - Xsuite has no dispersion derivative, so ``deta_x_ds`` is not translated
        either.
    """
    props = ele.child(0)
    name = props.node_key()
    ref: List[str] = []
    twiss: List[Tuple[str, str]] = []
    particle: List[Tuple[str, str]] = []
    for key in props.keys():
        if key == "TwissP":
            twissP = props["TwissP"]
            renames = {"beta_a": "betx", "beta_b": "bety", "alpha_a": "alfx",
                       "alpha_b": "alfy", "eta_x": "dx", "eta_y": "dy",
                       "etap_x": "dpx", "etap_y": "dpy"}
            for k in twissP.keys():
                val = twissP[k].value
                if k in renames:
                    twiss.append((renames[k], _xsuite_number(name, val)))
                # Xsuite counts the phase in turns where PALS counts it in
                # radians.
                elif k in ("phi_a", "phi_b"):
                    mu = "mux" if k == "phi_a" else "muy"
                    twiss.append((mu, _xsuite_number(
                        name, _xsuite_scale(val, 1 / (2 * math.pi)))))
                elif k.startswith("cmat"):
                    print(f"{name}: TwissP.{k} is Bmad's coupling matrix, which "
                          "Xsuite has no initial condition for, not translated")
                else:
                    print(f"{name}: TwissP.{k} has no Xsuite equivalent, not "
                          "translated")
        elif key == "ReferenceP":
            referenceP = props["ReferenceP"]
            for k in referenceP.keys():
                val = referenceP[k].value
                if k == "species_ref":
                    ref.append(repr(_xsuite_species(val)))
                # PALS states the reference energy in eV, and so does Xsuite.
                elif k == "pc_ref":
                    ref.append(f"p0c={_xsuite_number(name, val)}")
                elif k == "E_tot_ref":
                    ref.append(f"energy0={_xsuite_number(name, val)}")
                else:
                    print(f"{name}: ReferenceP.{k} has no Xsuite equivalent, not "
                          "translated")
        elif key == "ParticleP":
            particleP = props["ParticleP"]
            renames = {"x": "x", "px": "px", "y": "y", "py": "py", "z": "zeta",
                       "pz": "delta", "spin_x": "spin_x", "spin_y": "spin_y",
                       "spin_z": "spin_z"}
            for k in particleP.keys():
                if k in renames:
                    particle.append((renames[k],
                                     _xsuite_number(name, particleP[k].value)))
                else:
                    print(f"{name}: ParticleP.{k} has no Xsuite equivalent, not "
                          "translated")
        elif key in ("kind", "length", "MetaP", "TrackingP"):
            pass
        elif extensions is not None and extensions.skip(key, props[key]):
            pass
        else:
            print(f"{name}: BeginningEle.{key} has no Xsuite equivalent, not "
                  "translated")
    # The species is the positional argument of `xt.Particles`, so it goes first
    # whatever order the PALS file gave.
    ref.sort(key=lambda s: 0 if "=" not in s else 1)
    return ref, twiss, particle


# ---------------------------------------------------------------------------
def _make_xsuite_line(ele: YAMLNode, facility: YAMLNode,
                      extensions: Optional[_Extensions] = None
                      ) -> Tuple[XsuiteBeamline, List[XsuiteEle]]:
    """Translate a ``BeamLine`` element into an :class:`XsuiteBeamline`.

    Returns the line and the elements its items define in place, which have to
    be defined in the environment like any other.

    A leading ``BeginningEle`` is dropped from the members -- Xsuite has no
    element for it -- and the reference particle it states becomes the line's
    own, whether it is spelled out in the line or named there and defined in the
    ``facility``. A line that does not begin with one keeps every element it has.

    An item may carry a ``repeat`` count, which Xsuite's line arithmetic
    expresses, and may ``inherit`` from another element, which becomes an Xsuite
    clone of it. What Xsuite has no room for is reported: it cannot reverse an
    element's direction (``direction: -1``), place an item at an offset
    (``placement``), or change an element's parameters for one occurrence of it.
    """
    props = ele.child(0)
    name = props.node_key()
    line = props["line"]
    bl = XsuiteBeamline(name)
    inline: List[XsuiteEle] = []

    for i in range(len(line)):
        line_ele = line.child(i)
        if line_ele.is_scalar():
            member, item = line_ele.value, None
        elif line_ele.is_map() or line_ele.is_sequence():
            item = line_ele.child(0)
            member = item.node_key()
        else:
            raise ValueError(f"BeamLine {name} element {i + 1} is not scalar or "
                             "sequence or map")

        entry_props = item if (item is not None and "kind" in item) \
            else facility_props(facility, member)
        if i == 0 and entry_props is not None and "kind" in entry_props and \
                entry_props["kind"].value == "BeginningEle":
            # One named here and defined in the facility has been translated,
            # and reported on, already.
            with contextlib.redirect_stdout(io.StringIO()) \
                    if item is None or "kind" not in item \
                    else contextlib.nullcontext():
                ref, _, _ = _ele_to_xsuite_str(
                    line_ele if item is not None and "kind" in item
                    else _facility_entry_node(facility, member), extensions)
            bl.particle_ref.extend(ref)
            continue

        repeat = 1
        if item is not None:
            for key in item.keys():
                if key == "repeat":
                    repeat = int(item["repeat"].as_float())
                elif key == "direction":
                    if item["direction"].as_float() < 0:
                        print(f"BeamLine {name}: {member} is reversed in "
                              "direction, which Xsuite cannot express; it is "
                              "translated unreversed")
                elif key == "placement":
                    print(f"BeamLine {name}: {member} has a placement, which "
                          "this translation does not express, not translated")
                elif key == "inherit":
                    if "kind" in item:
                        raise ValueError(f"BeamLine {name}: {member} both "
                                         "defines its kind and inherits one")
                    clone = XsuiteEle(member, repr(item["inherit"].value))
                    extra = [k for k in item.keys()
                             if k not in ("inherit", "repeat", "direction",
                                          "placement")]
                    if extra:
                        print(f"BeamLine {name}: {member} inherits from "
                              f"{item['inherit'].value} and also sets "
                              f"{', '.join(extra)}, which is not translated")
                    inline.append(clone)
            if "kind" in item:
                inline.append(_make_xsuite_ele(line_ele, extensions))
            elif "inherit" not in item:
                extra = [k for k in item.keys()
                         if k not in ("repeat", "direction", "placement")]
                if extra:
                    print(f"BeamLine {name}: this occurrence of {member} sets "
                          f"{', '.join(extra)}, which Xsuite cannot change for "
                          "one occurrence of an element, not translated")
        if repeat != 0:
            bl.members.append((member, repeat))
    return bl, inline


# ---------------------------------------------------------------------------
def _facility_entry_node(facility: YAMLNode, name: str) -> YAMLNode:
    """The ``facility`` entry named ``name`` -- the single-key map an element is
    taken as."""
    for ele in facility:
        if ele.child(0).node_key() == name:
            return ele
    raise ValueError(f"{name}: no such element in the facility")


# ---------------------------------------------------------------------------
def _xsuite_kind(ele_kind: str, length: float, name: str) -> str:
    """The ``xtrack`` element class for the PALS ``ele_kind``.

    Kinds with no Xsuite equivalent raise an error. Several PALS kinds map onto
    the one Xsuite class:

      - An Xsuite ``Marker`` has no length, so an ``Instrument`` or ``Mask`` with
        a length becomes a ``Drift`` (a mask's aperture being an element of its
        own in Xsuite anyway, see :func:`_xsuite_aperture`).
      - A ``Kicker`` is a dipole ``Multipole``, made thick when it has a length.
      - A ``Quadrupole``, ``Sextupole`` or ``Octupole`` of zero length is a thin
        ``Multipole``: the Xsuite magnets of those names are thick.
      - A ``Solenoid`` is Xsuite's ``UniformSolenoid``, the hard-edge one.
      - A ``Patch`` is a ``Translation`` or a ``Rotation`` (or both, see
        :func:`_make_xsuite_ele`); this returns the first.
    """
    thin = approx(length, 0)
    known = {
        # Magnets and RF Cavities
        "Bend": "Bend", "CrabCavity": "CrabCavity", "Drift": "Drift",
        "Kicker": "Multipole", "Multipole": "Multipole",
        "Octupole": "Multipole" if thin else "Octupole",
        "Quadrupole": "Multipole" if thin else "Quadrupole",
        "RFCavity": "Cavity",
        "Sextupole": "Multipole" if thin else "Sextupole",
        "Solenoid": "UniformSolenoid",
        # Sources and Collimation
        "Mask": "Marker" if thin else "Drift",
        # Instrumentation and Diagnostics
        "Instrument": "Marker" if thin else "Drift",
        # Bookkeeping Elements
        "BeginningEle": "Marker", "Marker": "Marker",
        "Patch": "Translation"}
    if ele_kind in known:
        return known[ele_kind]

    unsupported = {
        "ACKicker": "No ACKicker elements in Xsuite",
        "BeamBeam": "No BeamBeam elements in xtrack: beam-beam elements are "
                    "xfields', which this translation does not target",
        "Wiggler": "No Wiggler elements in Xsuite",
        "Converter": "No Converter elements in Xsuite",
        "EGun": "No EGun elements in Xsuite",
        "Foil": "No Foil elements in Xsuite",
        "Match": "No Match elements in Xsuite",
        "Fiducial": "No Fiducial elements in Xsuite",
        "FloorShift": "No FloorShift elements in Xsuite",
        "Fork": "No Fork elements in Xsuite: an Xsuite line does not branch",
        "ReferenceChange": "ReferenceChange is not yet translated to Xsuite",
        "Girder": "No Girder elements in Xsuite",
        "Taylor": "Taylor is not yet translated to an Xsuite map element",
        "UnionEle": "No UnionEle in Xsuite",
        "Feedback": "No Feedback elements in Xsuite"}
    if ele_kind in unsupported:
        raise ValueError(f"{name}: {unsupported[ele_kind]}")
    raise ValueError(f"{name}: element kind {ele_kind} is not translated to "
                     "Xsuite")


# ---------------------------------------------------------------------------
#: The multipole orders an Xsuite element class holds as an attribute of its own,
#: and the attributes that hold them.
#:
#: Each entry maps an ``xtrack`` class to a map from ``(order, skew)`` to the
#: attribute name. Every one of these is a strength per unit length; every other
#: order goes into the ``knl``/``ksl`` arrays, which every Xsuite magnet has and
#: which are length integrated. A bend's order-0 field is its ``k0``, which Xsuite
#: holds apart from the geometry -- see :func:`_xsuite_native_strength`.
#:
#: Xsuite's coefficients are MAD-X's, the plain field derivatives
#: ``Kn L = (L/Brho) d^n By/dx^n``, and so are PALS': the ``1/N!`` of the PALS
#: field expansion belongs to the expansion and not to the coefficient. So, unlike
#: the Bmad translation, nothing here picks up a factorial.
_XSUITE_NATIVE_STRENGTH: Dict[str, Dict[Tuple[int, bool], str]] = {
    "Bend": {(0, False): "k0", (1, False): "k1", (2, False): "k2"},
    "Quadrupole": {(1, False): "k1", (1, True): "k1s"},
    "Sextupole": {(2, False): "k2", (2, True): "k2s"},
    "Octupole": {(3, False): "k3", (3, True): "k3s"}}

#: The Xsuite element classes that carry ``knl``/``ksl`` multipole arrays.
_XSUITE_MULTIPOLE_CLASSES = {"Bend", "Quadrupole", "Sextupole", "Octupole",
                             "Multipole", "UniformSolenoid"}

#: The Xsuite element classes that have a ``length`` attribute.
_XSUITE_THICK_CLASSES = {"Bend", "Quadrupole", "Sextupole", "Octupole",
                         "Multipole", "UniformSolenoid", "Drift", "Cavity",
                         "CrabCavity"}

#: The Xsuite element classes that can be misaligned, which is what a PALS
#: ``BodyShiftP`` becomes.
_XSUITE_ALIGNABLE_CLASSES = {"Bend", "Quadrupole", "Sextupole", "Octupole",
                             "Multipole", "UniformSolenoid", "Cavity",
                             "CrabCavity"}


# ---------------------------------------------------------------------------
def _xsuite_multipole(full: FullRepresentation, order: int) -> Tuple[float, float]:
    """The normal and skew components of multipole ``order``, with the
    multipole's own tilt rotated into them.

    A tilt of ``T`` on an order-``N`` multipole rotates it by ``(N+1) T`` in the
    normal/skew plane. Xsuite has one tilt for the whole element rather than one
    per order, so the rotation is worked out here and what comes out is a plain
    normal/skew pair. The components keep whatever units the PALS file gave them:
    normalized or not, integrated or not.
    """
    value = (complex(*full.magnitude[order])
             * tilt_rotation(order, full.tilt.get(order, 0.0)))
    return value.real, value.imag


# ---------------------------------------------------------------------------
def _xsuite_native_strength(full: FullRepresentation, xsuite_kind: str,
                            name: str, ref_g=None,
                            kn0_stated: bool = False) -> List[Tuple[str, str]]:
    """Take the multipoles that are an element's own strength out of ``full`` and
    return them as attributes.

    The strength of an Xsuite quadrupole is its ``k1``, so that is where a PALS
    ``Kn1`` belongs, and a skew one is its ``k1s``. These attributes are strengths
    per unit length, so an integrated PALS value is divided by the length; an
    element of zero length is a thin ``Multipole`` and has none of them.

    A bend's order-0 field is its ``k0``. Xsuite holds it apart from the bend's
    geometry, as PALS does: by default ``k0`` follows the curvature of the
    reference bend, which is PALS' ``Kn0_from_g_ref``, and set outright it is the
    actual field. So a ``Kn0`` that agrees with ``ref_g``, the curvature the bend
    geometry comes to, is left to follow it, and one that does not is written out.
    A skew order-0 field, which an Xsuite bend has no attribute for, stays in
    ``full`` for the ``ksl`` array.
    """
    attrs: List[Tuple[str, str]] = []
    native = _XSUITE_NATIVE_STRENGTH.get(xsuite_kind)
    if native is None:
        return attrs

    for order in sorted(full.magnitude):
        if (order, False) not in native and (order, True) not in native:
            continue
        normal, skew = _xsuite_multipole(full, order)
        normalized = full.normalized[order]
        leftover = [0.0, 0.0]

        for i, (component, is_skew) in enumerate(((normal, False), (skew, True))):
            attribute = native.get((order, is_skew))
            if attribute is None:
                leftover[i] = component
                continue
            value = component
            if full.integrated[order]:
                if full.L == 0:
                    if not approx(value, 0):
                        print(f"{name}: an integrated order-{order} multipole "
                              "cannot be spread over an element of zero length, "
                              "not translated")
                    continue
                value /= full.L
            if attribute == "k0" and normalized and ref_g is not None and \
                    approx(value, ref_g):
                continue
            # A zero `k0` is a bend with no field, which is worth saying when the
            # PALS file said it; any other zero strength says nothing.
            if approx(value, 0) and not (attribute == "k0" and kn0_stated):
                continue
            attrs.append((attribute, _xsuite_strength(value, normalized)))

        # A component with no attribute of its own goes into the multipole array
        # with the tilt already rotated into it.
        if any(not approx(c, 0) for c in leftover):
            full.magnitude[order] = leftover
            full.tilt.pop(order, None)
        else:
            full.magnitude.pop(order, None)
            full.integrated.pop(order, None)
            full.normalized.pop(order, None)
            full.tilt.pop(order, None)
    return attrs


# ---------------------------------------------------------------------------
def _xsuite_fill_multipoles(full: FullRepresentation, mmP: YAMLNode, name: str
                            ) -> Dict[Tuple[int, bool], Tuple[str, bool, bool]]:
    """Populate ``full`` from a PALS ``MagneticMultipoleP``, as
    :func:`~palsparserpy._common.fill_multipoles` does, and return the components
    written as expressions rather than numbers.

    A component written as a number goes into ``full`` to be rotated by its tilt
    and split between the element's own attributes and its multipole arrays.
    One written as an expression -- ``Kn1: kq`` -- cannot be worked with here, but
    Xsuite can keep it as a deferred expression, so it comes back as
    ``{(order, skew): (text, integrated, normalized)}`` for
    :func:`_xsuite_expression_strengths`. A tilt of such an order cannot be
    rotated into an expression without writing the rotation out, which is not
    done; it raises an error rather than be dropped.
    """
    exprs: Dict[Tuple[int, bool], Tuple[str, bool, bool]] = {}
    tilts: Dict[int, str] = {}
    for key in mmP.keys():
        order = int("".join(ch for ch in key if ch.isdigit()))
        text = mmP[key].value
        if key.startswith("tilt"):
            tilts[order] = text
            continue
        if try_float(text) is not None:
            continue
        exprs[(order, key[1] == "s")] = (text, key.endswith("L"),
                                         key.startswith("K"))
    for order, text in tilts.items():
        value = try_float(text)
        if any(o == order for o, _ in exprs) and value != 0:
            raise ValueError(f"{name}: an order-{order} multipole written as an "
                             "expression cannot be tilted in the translation")
        if value is None:
            raise ValueError(f"{name}: tilt{order} is not a number")
        full.tilt[order] = value
    for key in mmP.keys():
        if key.startswith("tilt") or try_float(mmP[key].value) is None:
            continue
        order = int("".join(ch for ch in key if ch.isdigit()))
        component = 0 if key[1] == "n" else 1
        if order not in full.magnitude:
            full.integrated[order] = key.endswith("L")
            full.normalized[order] = key.startswith("K")
            full.magnitude[order] = [0.0, 0.0]
        full.magnitude[order][component] = mmP[key].as_float()
    return exprs


# ---------------------------------------------------------------------------
def _xsuite_expression_strengths(xele: XsuiteEle,
                                 exprs: Dict[Tuple[int, bool],
                                             Tuple[str, bool, bool]],
                                 length_text: str, length: float) -> None:
    """Add to ``xele`` the multipole components written as expressions (see
    :func:`_xsuite_fill_multipoles`), as deferred Xsuite expressions.

    Each lands where a number would: the element's own attribute (``k1`` of a
    quadrupole, say), which is per unit length, or else an entry of its
    ``knl``/``ksl`` array, which is integrated -- with the length put in or taken
    out in the expression to match, and a field divided by the rigidity.
    """
    native = _XSUITE_NATIVE_STRENGTH.get(xele.type, {})
    for (order, skew), (text, integrated, normalized) in sorted(exprs.items()):
        attribute = native.get((order, skew))
        if attribute is not None:
            if integrated:
                if approx(length, 0):
                    print(f"{xele.name}: an integrated order-{order} multipole "
                          "cannot be spread over an element of zero length, not "
                          "translated")
                    continue
                text = f"({text}) / ({length_text})"
        elif xele.type in _XSUITE_MULTIPOLE_CLASSES:
            if not integrated:
                if approx(length, 0):
                    print(f"{xele.name}: a multipole that is not length "
                          "integrated has no strength on an element of zero "
                          "length, not translated")
                    continue
                text = f"({text}) * ({length_text})"
        else:
            print(f"{xele.name}: an Xsuite {xele.type} cannot carry a multipole "
                  f"of order {order}, not translated")
            continue
        if not normalized:
            text = f"({text}) / {_XSUITE_RIGIDITY}"
        source = repr(_xsuite_expression(xele.name, text))
        if attribute is not None:
            xele.attrs[:] = [a for a in xele.attrs if a[0] != attribute]
            xele.attrs.append((attribute, source))
            continue
        arr = xele.ksl if skew else xele.knl
        arr.extend(["0"] * (order + 1 - len(arr)))
        arr[order] = source


# ---------------------------------------------------------------------------
def _xsuite_multipole_arrays(full: FullRepresentation) -> Tuple[List[str],
                                                                List[str]]:
    """The ``knl``/``ksl`` arrays of whatever multipoles are left in ``full``.

    Xsuite states a multipole as two arrays of integrated coefficients indexed by
    order from zero up, so an order that is not there still needs its zero
    written in. A component given as a field is divided by the reference
    rigidity, which makes the array entry an expression rather than a number.
    """
    if not full.magnitude:
        return [], []
    n = max(full.magnitude)
    knl = ["0"] * (n + 1)
    ksl = ["0"] * (n + 1)
    for order in sorted(full.magnitude):
        normal, skew = _xsuite_multipole(full, order)
        # Every entry of an Xsuite multipole array is length integrated.
        if not full.integrated[order]:
            normal *= full.L
            skew *= full.L
        normalized = full.normalized[order]
        if not approx(normal, 0):
            knl[order] = _xsuite_strength(normal, normalized)
        if not approx(skew, 0):
            ksl[order] = _xsuite_strength(skew, normalized)
    if all(k == "0" for k in knl):
        knl = []
    if all(k == "0" for k in ksl):
        ksl = []
    return knl, ksl


# ---------------------------------------------------------------------------
def _xsuite_fold(text: str, f, *args: str) -> str:
    """``text``, or the number it comes to when every one of ``args`` is a number.

    A PALS parameter may be written as an expression, which only Xsuite can
    evaluate, or as a plain number, which the translation can work with. Where an
    Xsuite value has to be derived from several PALS ones, ``text`` is that
    derivation written as an expression and ``f`` is the same derivation as a
    function, applied here when all of its inputs parse.
    """
    values = [try_float(a) for a in args]
    if any(v is None for v in values):
        return text
    result = f(*values)
    return fmt(result) if math.isfinite(result) else text


# ---------------------------------------------------------------------------
def _xsuite_times(a: str, b: str) -> str:
    """The product of two values, without the clutter of a factor of one."""
    va, vb = try_float(a), try_float(b)
    if va is not None and vb is not None:
        return fmt(va * vb)
    if va == 1:
        return b
    if vb == 1:
        return a
    return f"({a}) * ({b})"


# ---------------------------------------------------------------------------
def _xsuite_bend_geometry(props: YAMLNode, name: str):
    """The reference geometry of a ``Bend`` as ``(angle, arc_length, g)``, each
    the text of a value or ``None``, or ``None`` if the element states none.

    PALS states a bend's geometry with one parameter from each of two of three
    sets -- a curvature (``g_ref``, ``radius_ref`` or the reference field
    ``Bn0_ref``), a length (``length``, ``L_chord`` or ``L_rectangle``), and the
    angle (``angle_ref``). An Xsuite ``Bend`` states it with the ``angle`` and the
    arc ``length``, so whichever pair the PALS file used is turned into that pair
    here. ``g`` is the curvature the two come to, which a bend's ``k0`` is
    compared with.
    """
    if "BendP" not in props:
        return None
    bendP = props["BendP"]

    # The curvature, however the PALS file chose to state it.
    if "g_ref" in bendP:
        g = bendP["g_ref"].value
    elif "radius_ref" in bendP:
        radius = bendP["radius_ref"].value
        g = _xsuite_fold(f"1 / ({radius})", lambda r: 1 / r, radius)
    elif "Bn0_ref" in bendP:
        g = f"({bendP['Bn0_ref'].value}) / {_XSUITE_RIGIDITY}"
    else:
        g = None

    # A length, and which of the three lengths it is: Xsuite wants the arc.
    if "length" in props:
        len_kind, length = "arc", props["length"].value
    elif "L_chord" in bendP:
        len_kind, length = "chord", bendP["L_chord"].value
    elif "L_rectangle" in bendP:
        len_kind, length = "rect", bendP["L_rectangle"].value
    else:
        len_kind, length = "none", None

    angle = bendP["angle_ref"].value if "angle_ref" in bendP else None
    arc = length if len_kind == "arc" else None

    if angle is None and g is not None and length is not None:
        if len_kind == "arc":
            angle = _xsuite_times(g, length)
        elif len_kind == "chord":
            angle = _xsuite_fold(f"2 * asin(({g}) * ({length}) / 2)",
                                 lambda a, b: 2 * math.asin(a * b / 2), g, length)
        else:
            angle = _xsuite_fold(f"asin(({g}) * ({length}))",
                                 lambda a, b: math.asin(a * b), g, length)

    if arc is None and angle is not None:
        if len_kind == "chord":
            arc = _xsuite_fold(
                f"({angle}) * ({length}) / (2 * sin(({angle}) / 2))",
                lambda a, l: a * l / (2 * math.sin(a / 2)), angle, length)
        elif len_kind == "rect":
            arc = _xsuite_fold(f"({angle}) * ({length}) / sin({angle})",
                               lambda a, l: a * l / math.sin(a), angle, length)
        elif g is not None:
            arc = _xsuite_fold(f"({angle}) / ({g})", lambda a, b: a / b, angle, g)

    if g is None and angle is not None and arc is not None:
        g = _xsuite_fold(f"({angle}) / ({arc})",
                         lambda a, l: a / l if l != 0 else math.nan, angle, arc)

    if angle is None and arc is None:
        return None
    if angle is None or arc is None:
        print(f"{name}: BendP states too little of the bend geometry for Xsuite, "
              "which needs both the angle and the arc length")
    return angle, arc, g


# ---------------------------------------------------------------------------
def _xsuite_bend_faces(bendP: YAMLNode, name: str, angle) -> List[Tuple[str, str]]:
    """The ``edge_entry_angle``/``edge_exit_angle`` attributes of a bend's pole
    faces.

    Xsuite measures the edge angles of a ``Bend`` against the sector geometry, as
    MAD-X does, which is what PALS' own ``e1`` and ``e2`` are measured against, so
    those two come straight across. PALS also has ``e1_rect`` and ``e2_rect``,
    measured against fiducial lines parallel to each other, and what separates
    the two pairs depends on the bend's ``ref_geometry``::

        ARC, CHORD        e1 = e1_rect + angle/2,   e2 = e2_rect + angle/2
        ENTRANCE_COORDS   e1 = e1_rect,             e2 = e2_rect + angle
        EXIT_COORDS       e1 = e1_rect + angle,     e2 = e2_rect

    A face given both ways is contradictory and raises an error; one given the
    rectangular way on a bend whose angle is unknown cannot be converted, and
    raises one too.
    """
    attrs: List[Tuple[str, str]] = []
    geometry = bendP["ref_geometry"].value if "ref_geometry" in bendP else "ARC"

    e1_share = 0.0 if geometry == "ENTRANCE_COORDS" else \
        1.0 if geometry == "EXIT_COORDS" else 0.5
    e2_share = 1.0 if geometry == "ENTRANCE_COORDS" else \
        0.0 if geometry == "EXIT_COORDS" else 0.5

    for face, rect, share, attribute in (
            ("e1", "e1_rect", e1_share, "edge_entry_angle"),
            ("e2", "e2_rect", e2_share, "edge_exit_angle")):
        has_face, has_rect = face in bendP, rect in bendP
        if has_face and has_rect:
            raise ValueError(f"{name}: should not have both {face} and {rect}")
        if has_face:
            attrs.append((attribute, _xsuite_value(name, bendP[face].value)))
        elif has_rect:
            if angle is None:
                raise ValueError(f"{name}: {rect} is measured against the bend "
                                 "angle, which is not given")
            rect_text = bendP[rect].value
            text = rect_text if share == 0 else _xsuite_fold(
                f"{rect_text} + {fmt(share)} * ({angle})",
                lambda r, a, share=share: r + share * a, rect_text, angle)
            attrs.append((attribute, _xsuite_value(name, text)))
    return attrs


# ---------------------------------------------------------------------------
def _xsuite_aperture(apertureP: YAMLNode, name: str,
                     has_body_shift: bool) -> Tuple[List[XsuiteEle],
                                                    List[XsuiteEle]]:
    """The aperture elements a PALS ``ApertureP`` becomes, as ``(before,
    after)``.

    An Xsuite aperture is an element of its own, a ``LimitRect`` or
    ``LimitEllipse``, which loses whatever particle lies outside it where it
    stands in the line. So a PALS aperture becomes one placed just before the
    element it bounds, one just after it, or both, as its ``location`` says. An
    aperture centred off the axis is shifted with Xsuite's ``shift_x``/``shift_y``.

    The ``shape`` decides which of the components describe the aperture: a
    ``RECTANGULAR`` aperture is bounded on whichever of its four sides are set,
    and an ``ELLIPTICAL`` one only when all four are, PALS deactivating it
    otherwise. A ``VERTICES`` or ``CUSTOM_SHAPE`` aperture is reported rather than
    written out.

    What Xsuite has no room for is reported: an aperture inside the element
    (``location: CENTER`` or ``EVERYWHERE``), an aperture that moves with a
    misaligned element (an Xsuite aperture element stays where the line puts
    it), and the material and thickness.
    """
    before: List[XsuiteEle] = []
    after: List[XsuiteEle] = []
    shape = apertureP["shape"].value if "shape" in apertureP else "ELLIPTICAL"
    location = apertureP["location"].value if "location" in apertureP \
        else "ENTRANCE_END"

    for akey in apertureP.keys():
        if akey in ("material", "thickness"):
            print(f"{name}: ApertureP.{akey} has no Xsuite equivalent, not "
                  "translated")
        elif akey == "aperture_shifts_with_body" and has_body_shift and \
                apertureP[akey].value.lower() == "true":
            print(f"{name}: an Xsuite aperture element does not move with the "
                  "misaligned element, so aperture_shifts_with_body is not "
                  "translated")

    if "aperture_active" in apertureP and \
            apertureP["aperture_active"].value.lower() == "false":
        print(f"{name}: the aperture is switched off, so no Xsuite aperture "
              "element is made for it")
        return before, after
    if shape == "VERTICES":
        print(f"{name}: a VERTICES aperture is not yet translated to an Xsuite "
              "LimitPolygon, not translated")
        return before, after
    if shape == "CUSTOM_SHAPE":
        print(f"{name}: a CUSTOM_SHAPE aperture is defined outside PALS and has "
              "no Xsuite equivalent, not translated")
        return before, after
    if shape not in ("RECTANGULAR", "ELLIPTICAL"):
        raise ValueError(f"{name}: aperture shape {shape} is not supported")

    def limits(plane):
        """The ``(min, max)`` of one plane, each a number or ``None``, from
        whichever of the two PALS forms the group used."""
        has_min, has_max = f"{plane}_min" in apertureP, f"{plane}_max" in apertureP
        has_width = f"{plane}_width" in apertureP
        has_centre = f"{plane}_center" in apertureP
        if (has_min or has_max) and (has_width or has_centre):
            print(f"{name}: either {plane}_min and {plane}_max should be set or "
                  f"{plane}_width and {plane}_center, not both; the {plane} "
                  "aperture is not translated")
            return None, None
        if has_width:
            width = apertureP[f"{plane}_width"].as_float()
            centre = apertureP[f"{plane}_center"].as_float() if has_centre else 0.0
            exact[plane] = (width / 2, centre)
            return centre - width / 2, centre + width / 2
        if has_centre:
            print(f"{name}: {plane}_center is set without {plane}_width, which "
                  "bounds nothing")
        lo = apertureP[f"{plane}_min"].as_float() if has_min else None
        hi = apertureP[f"{plane}_max"].as_float() if has_max else None
        return lo, hi

    # The half width and centre of a plane given that way, kept as given rather
    # than worked back out of the limits.
    exact: Dict[str, Tuple[float, float]] = {}
    x_lo, x_hi = limits("x")
    y_lo, y_hi = limits("y")
    if all(v is None for v in (x_lo, x_hi, y_lo, y_hi)):
        # Shape, location and the rest describe an aperture; they do not put one
        # there.
        return before, after

    if shape == "RECTANGULAR":
        cls = "LimitRect"
        attrs = [(k, fmt(v)) for k, v in (("min_x", x_lo), ("max_x", x_hi),
                                         ("min_y", y_lo), ("max_y", y_hi))
                 if v is not None]
    else:
        if any(v is None for v in (x_lo, x_hi, y_lo, y_hi)):
            print(f"{name}: an ELLIPTICAL aperture needs all four limits and is "
                  "not active without them, not translated")
            return before, after
        cls = "LimitEllipse"
        x_half, x_centre = exact.get("x", ((x_hi - x_lo) / 2, (x_hi + x_lo) / 2))
        y_half, y_centre = exact.get("y", ((y_hi - y_lo) / 2, (y_hi + y_lo) / 2))
        attrs = [("a", fmt(x_half)), ("b", fmt(y_half))]
        for key, centre in (("shift_x", x_centre), ("shift_y", y_centre)):
            if not approx(centre, 0):
                attrs.append((key, fmt(centre)))

    def limit(suffix):
        return XsuiteEle(f"{name}__aperture_{suffix}", cls, list(attrs))

    if location in ("ENTRANCE_END", "BOTH_ENDS"):
        before.append(limit("entrance"))
    if location in ("EXIT_END", "BOTH_ENDS"):
        after.append(limit("exit"))
    if location in ("CENTER", "EVERYWHERE"):
        print(f"{name}: an Xsuite aperture element stands between elements, so "
              f"an aperture at location {location} is not translated")
    elif location not in ("ENTRANCE_END", "EXIT_END", "BOTH_ENDS", "NOWHERE"):
        raise ValueError(f"{name}: unknown aperture location `{location}`")
    return before, after


# ---------------------------------------------------------------------------
def _make_xsuite_ele(ele: YAMLNode,
                     extensions: Optional[_Extensions] = None) -> XsuiteEle:
    """Translate a single PALS element into an :class:`XsuiteEle`.

    Dispatch on the element ``kind`` and its parameter groups (aperture, bend,
    body shift, multipoles, patch, RF, solenoid, ...) to build the Xsuite element
    class and its attributes. Parameters Xsuite cannot hold are reported on
    stdout; parameter groups with no Xsuite equivalent at all raise an error.
    """
    props = ele.child(0)
    name = props.node_key()
    ele_kind = props["kind"].value
    length_text = props["length"].value if "length" in props else "0"
    length = try_float(length_text)
    if length is None:
        # A length written as an expression is evaluated for the decisions that
        # depend on it, such as whether the element is thin.
        try:
            length = evaluate_pals_expression(length_text)
        except Exception:
            length = 1.0
    xsuite_kind = _xsuite_kind(ele_kind, length, name)
    xele = XsuiteEle(name, xsuite_kind)
    attrs = xele.attrs

    # The bend geometry has to be settled before anything else is: Xsuite holds
    # a bend's field apart from its geometry, and its `k0` is compared with the
    # curvature the geometry comes to.
    geometry = _xsuite_bend_geometry(props, name) if ele_kind == "Bend" else None
    ref_angle = None if geometry is None else geometry[0]
    ref_g = None if geometry is None else try_float(geometry[2] or "")

    for key in props.keys():
        if key == "kind":
            continue
        elif key == "length":
            if xsuite_kind in _XSUITE_THICK_CLASSES:
                if xsuite_kind != "Multipole" or not approx(length, 0):
                    attrs.append(("length", _xsuite_value(name, length_text)))
            elif not approx(length, 0):
                print(f"{name}: an Xsuite {xsuite_kind} has no length, so the "
                      "PALS length is not translated")
        elif key == "ApertureP":
            before, after = _xsuite_aperture(props["ApertureP"], name,
                                             "BodyShiftP" in props)
            xele.before.extend(before)
            xele.after.extend(after)
        elif key == "BendP":
            bendP = props["BendP"]
            if geometry is not None:
                angle, arc, _ = geometry
                if arc is not None and "length" not in props:
                    attrs.append(("length", _xsuite_value(name, arc)))
                if angle is not None:
                    attrs.append(("angle", _xsuite_value(name, angle)))
            attrs.extend(_xsuite_bend_faces(bendP, name, ref_angle))

            for bkey in bendP.keys():
                # Settled above: the geometry parameters, and the pole faces.
                if bkey in ("angle_ref", "g_ref", "radius_ref", "Bn0_ref",
                            "L_chord", "L_rectangle", "e1", "e2", "e1_rect",
                            "e2_rect", "Kn0_from_g_ref"):
                    continue
                # PALS states the fringe field as an integral with the gap folded
                # in; Xsuite states the dimensionless integral and the gap apart,
                # so half of one is the whole of the other.
                elif bkey in ("edge1_int", "edge2_int"):
                    val = bendP[bkey].as_float()
                    end = "entry" if bkey == "edge1_int" else "exit"
                    if not approx(val, 0):
                        attrs.append((f"edge_{end}_fint", "0.5"))
                        attrs.append((f"edge_{end}_hgap", fmt(2 * val)))
                elif bkey in ("h1", "h2"):
                    if not approx(bendP[bkey].as_float(), 0):
                        print(f"{name}: BendP.{bkey}, the pole face curvature, "
                              "has no Xsuite equivalent, not translated")
                elif bkey == "tilt_ref":
                    attrs.append(("rot_s_rad",
                                  _xsuite_value(name, bendP[bkey].value)))
                elif bkey == "L_sagitta":
                    raise ValueError(f"{name}: BendP.L_sagitta is an output "
                                     "parameter and is not translated")
                # An Xsuite Bend is an arc whose multipoles are vertically pure.
                elif bkey == "ref_geometry":
                    if bendP[bkey].value != "ARC":
                        print(f"{name}: BendP.ref_geometry = {bendP[bkey].value} "
                              "has no Xsuite equivalent; an Xsuite Bend is "
                              "always an arc")
                elif bkey == "multipole_geometry":
                    if bendP[bkey].value not in ("FOLLOWS_REF_GEOMETRY",
                                                 "VERTICALLY_PURE"):
                        print(f"{name}: BendP.multipole_geometry = "
                              f"{bendP[bkey].value} has no Xsuite equivalent, "
                              "not translated")
                else:
                    print(f"{name}: BendP.{bkey} has no Xsuite equivalent, not "
                          "translated")

            # With `Kn0_from_g_ref` false and no order-0 multipole set, the bend
            # has the geometry of the reference bend and none of its field, which
            # Xsuite states as a `k0` of zero.
            if "Kn0_from_g_ref" in bendP and \
                    bendP["Kn0_from_g_ref"].value.lower() == "false" and \
                    not ("MagneticMultipoleP" in props and
                         any(k in ("Kn0", "Bn0", "Kn0L", "Bn0L")
                             for k in props["MagneticMultipoleP"].keys())):
                attrs.append(("k0", "0"))
        elif key == "BodyShiftP":
            attrs.extend(_xsuite_body_shift(props["BodyShiftP"], name,
                                            xsuite_kind, length_text,
                                            ele_kind == "Bend"))
        elif key == "MagneticMultipoleP":
            full = FullRepresentation()
            full.L = length
            exprs = _xsuite_fill_multipoles(full, props["MagneticMultipoleP"],
                                            name)

            # The orders that are the element's own become its strength
            # attributes and leave `full`; what is left goes in the multipole
            # arrays, which every Xsuite magnet has.
            kn0_stated = any(k in props["MagneticMultipoleP"]
                             for k in ("Kn0", "Bn0", "Kn0L", "Bn0L"))
            attrs.extend(_xsuite_native_strength(full, xsuite_kind, name, ref_g,
                                                 kn0_stated))
            if full.magnitude and xsuite_kind not in _XSUITE_MULTIPOLE_CLASSES:
                orders = ", ".join(str(o) for o in sorted(full.magnitude))
                print(f"{name}: an Xsuite {xsuite_kind} cannot carry multipoles "
                      f"of order {orders}, not translated")
            else:
                if any(not full.integrated[o] for o in full.magnitude) and \
                        approx(full.L, 0):
                    print(f"{name}: a multipole that is not length integrated "
                          "has no strength on an element of zero length, not "
                          "translated")
                xele.knl, xele.ksl = _xsuite_multipole_arrays(full)
            _xsuite_expression_strengths(xele, exprs, length_text, length)
        elif key == "MetaP":
            metaP = props["MetaP"]
            # Xsuite elements hold no metadata of their own, so what PALS says
            # about an element is kept as a comment above it rather than dropped.
            for mkey in metaP.keys():
                val = metaP[mkey]
                if val.is_map() or val.is_sequence():
                    print(f"{name}: MetaP.{mkey} is not a simple string, not "
                          "translated")
                    continue
                xele.notes.append(f"{mkey}: {val.value}")
        elif key == "PatchP":
            _xsuite_patch(xele, props["PatchP"], name)
        elif key == "RFP":
            attrs.extend(_xsuite_rf(props["RFP"], name, xsuite_kind,
                                    length_text if "length" in props else None))
        elif key == "SolenoidP":
            solP = props["SolenoidP"]
            if "Ksol" in solP:
                attrs.append(("ks", _xsuite_value(name, solP["Ksol"].value)))
            elif "Bsol" in solP:
                bsol = solP["Bsol"].value
                attrs.append(("ks", repr(_xsuite_expression(
                    name, f"({bsol}) / {_XSUITE_RIGIDITY}"))))
            for skey in solP.keys():
                if skey not in ("Ksol", "Bsol"):
                    print(f"{name}: SolenoidP.{skey} has no Xsuite equivalent, "
                          "not translated")
            if approx(length, 0):
                print(f"{name}: an Xsuite UniformSolenoid is thick, and this one "
                      "has no length")
        elif key == "TrackingP":
            # Tracking parameters are program specific by design; Xsuite's have no
            # PALS spelling.
            pass
        elif key in ("repeat", "direction", "placement", "inherit"):
            # Line-item settings, which the line translation deals with.
            pass
        elif key in ("ACKickerP", "BeamBeamP", "CoordinateSetP",
                     "ElectricMultipoleP", "FloorP", "ForkP", "GirderP",
                     "ReferenceChangeP", "TaylorP"):
            raise ValueError(f"{name}: {key} is not translated to Xsuite")
        elif extensions is not None and extensions.skip(key, props[key]):
            pass
        else:
            print(f"{name}: {key} has no Xsuite equivalent, not translated")

    # A thick Multipole -- a kicker with a length -- has to be told that it is
    # thick, or it takes up no length in the line.
    if xsuite_kind == "Multipole" and not approx(length, 0):
        attrs.append(("isthick", "True"))
    if xsuite_kind == "Translation" and "PatchP" not in props:
        xele.type = "Marker"
    return xele


# ---------------------------------------------------------------------------
def _xsuite_body_shift(bodyshiftP: YAMLNode, name: str, xsuite_kind: str,
                       length_text: str, is_bend: bool) -> List[Tuple[str, str]]:
    """The misalignment attributes a PALS ``BodyShiftP`` becomes.

    Every Xsuite magnet and cavity carries its misalignment itself: offsets
    ``shift_x``, ``shift_y``, ``shift_s`` and rotations ``rot_x_rad``,
    ``rot_y_rad``, ``rot_s_rad_no_frame`` (the last turning the element and not
    its reference frame), made about the point ``rot_shift_anchor`` from the
    entrance. PALS turns an element about its centre, so that is where the anchor
    goes.

    Xsuite's ``rot_y_rad`` turns ``s`` towards ``x``, which is PALS' ``y_rot``,
    and its ``rot_s_rad_no_frame`` turns ``x`` towards ``y``, which is PALS'
    ``z_rot``; but its ``rot_x_rad`` turns ``s`` towards ``y``, the opposite of
    the right-handed PALS ``x_rot``, which is where the sign comes from.

    A bend is turned about the centre of its chord in PALS and about the middle of
    its arc in Xsuite, so the two agree only to first order in the bend angle.
    """
    attrs: List[Tuple[str, str]] = []
    if xsuite_kind not in _XSUITE_ALIGNABLE_CLASSES:
        if any(not approx(bodyshiftP[k].as_float(), 0) for k in bodyshiftP.keys()):
            print(f"{name}: an Xsuite {xsuite_kind} cannot be misaligned, so "
                  "BodyShiftP is not translated")
        return attrs
    renames = {"x_offset": ("shift_x", 1), "y_offset": ("shift_y", 1),
               "z_offset": ("shift_s", 1), "x_rot": ("rot_x_rad", -1),
               "y_rot": ("rot_y_rad", 1), "z_rot": ("rot_s_rad_no_frame", 1)}
    rotated = False
    for bskey in bodyshiftP.keys():
        if bskey not in renames:
            print(f"{name}: BodyShiftP.{bskey} has no Xsuite equivalent, not "
                  "translated")
            continue
        attribute, sign = renames[bskey]
        val = bodyshiftP[bskey].value
        attrs.append((attribute, _xsuite_value(name, _xsuite_scale(val, sign))))
        rotated = rotated or bskey.endswith("_rot")
    if rotated and try_float(length_text) != 0:
        attrs.append(("rot_shift_anchor",
                      _xsuite_value(name, _xsuite_scale(length_text, 0.5))))
        if is_bend:
            print(f"{name}: PALS turns a misaligned bend about the centre of its "
                  "chord and Xsuite about the middle of its arc, which agree only "
                  "to first order in the bend angle")
    return attrs


# ---------------------------------------------------------------------------
def _xsuite_patch(xele: XsuiteEle, patchP: YAMLNode, name: str) -> None:
    """Fill ``xele`` in as the Xsuite rendering of a PALS ``PatchP``.

    A PALS patch moves the reference frame by its offsets and then turns it by
    ``R_y(y_rot) R_x(x_rot) R_z(z_rot)``. Xsuite has an element for each half: a
    ``Translation`` shifts the frame by ``shift_x``, ``shift_y`` and ``shift_s``,
    and a ``Rotation`` turns it by ``rot_y_rad``, ``rot_x_rad`` and ``rot_s_rad``
    in that same order, each the right-handed PALS rotation. A patch that does
    both becomes the two, the rotation added after the translation; one that
    does only one becomes that one.

    Xsuite tracks a ``shift_s`` as a drift but does not advance ``s`` through it,
    so a patch with a ``z_offset`` leaves the Xsuite line that much shorter than
    the PALS one, which is reported. A flexible patch, whose offsets come from the
    branch layout rather than the file, is reported too.
    """
    offsets: List[Tuple[str, str]] = []
    angles: List[Tuple[str, str]] = []
    renames = {"x_offset": "shift_x", "y_offset": "shift_y", "z_offset": "shift_s",
               "x_rot": "rot_x_rad", "y_rot": "rot_y_rad", "z_rot": "rot_s_rad"}
    for pkey in patchP.keys():
        val = patchP[pkey].value
        if pkey in renames:
            if try_float(val) == 0:
                continue
            target = offsets if pkey.endswith("offset") else angles
            target.append((renames[pkey], _xsuite_value(name, val)))
        elif pkey == "flexible":
            if val.lower() == "true":
                print(f"{name}: a flexible patch takes its offsets from the branch "
                      "layout, which Xsuite does not work out, not translated")
        else:
            print(f"{name}: PatchP.{pkey} has no Xsuite equivalent, not "
                  "translated")
    if any(k == "shift_s" for k, _ in offsets):
        print(f"{name}: Xsuite tracks a patch's z_offset as a drift but does not "
              "advance s through it")

    if offsets:
        xele.type = "Translation"
        xele.attrs.extend(offsets)
        if angles:
            xele.after.insert(0, XsuiteEle(f"{name}__rotation", "Rotation", angles))
    elif angles:
        xele.type = "Rotation"
        xele.attrs.extend(angles)
    else:
        xele.type = "Marker"


# ---------------------------------------------------------------------------
def _xsuite_rf(rfP: YAMLNode, name: str, xsuite_kind: str,
               length_text: Optional[str]) -> List[Tuple[str, str]]:
    """The attributes a PALS ``RFP`` becomes on an Xsuite ``Cavity`` or
    ``CrabCavity``.

    PALS and Xsuite both state the frequency in Hz and the voltage in volts, so
    those come straight across, as does the harmonic number. A ``gradient``
    becomes the voltage it comes to over the active length.

    The phase is in radians in both, but measured from different places. An
    Xsuite cavity gives the reference particle an energy ``V sin(phase)``: its
    zero phase is the zero crossing below transition, and a particle arriving
    late sees a larger phase, as it does in PALS. A PALS phase is measured from
    the point its ``zero_phase`` names, which comes to adding::

        ACCELERATING       pi/2   (the crest)
        BELOW_TRANSITION   0
        ABOVE_TRANSITION   pi

    A crab cavity's phase is carried over as it stands, its zero point being
    Xsuite's own, which is reported.
    """
    attrs: List[Tuple[str, str]] = []
    if "frequency" in rfP and "harmon" in rfP:
        raise ValueError(f"{name}: can only define `frequency` or `harmon` but "
                         "not both")
    if "voltage" in rfP and "gradient" in rfP:
        raise ValueError(f"{name}: can only define `voltage` or `gradient` but "
                         "not both")
    zero_phase = rfP["zero_phase"].value if "zero_phase" in rfP \
        else "ACCELERATING"
    offsets = {"ACCELERATING": math.pi / 2, "BELOW_TRANSITION": 0.0,
               "ABOVE_TRANSITION": math.pi}
    if zero_phase not in offsets:
        raise ValueError(f"{name}: unknown zero_phase `{zero_phase}`")
    crab = xsuite_kind == "CrabCavity"
    offset = 0.0 if crab else offsets[zero_phase]
    voltage = "crab_voltage" if crab else "voltage"

    for rfkey in rfP.keys():
        val = rfP[rfkey].value
        if rfkey == "frequency":
            attrs.append(("frequency", _xsuite_value(name, val)))
        elif rfkey == "harmon":
            if crab:
                print(f"{name}: an Xsuite CrabCavity takes a frequency, not a "
                      "harmonic number, not translated")
            else:
                attrs.append(("harmonic", _xsuite_value(name, val)))
        elif rfkey == "voltage":
            attrs.append((voltage, _xsuite_value(name, val)))
        elif rfkey == "gradient":
            active = rfP["L_active"].value if "L_active" in rfP else length_text
            if active is None:
                raise ValueError(f"{name}: `gradient` needs a length to become "
                                 "the voltage Xsuite states")
            attrs.append((voltage, _xsuite_value(name, _xsuite_times(val,
                                                                     active))))
        elif rfkey == "phase":
            attrs.append(("phase", _xsuite_value(name, _xsuite_shift(val,
                                                                     offset))))
            if crab:
                print(f"{name}: a crab cavity's phase is carried over as it "
                      "stands, measured from Xsuite's own zero")
        elif rfkey == "zero_phase":
            if crab and zero_phase != "ACCELERATING":
                print(f"{name}: RFP.zero_phase has no Xsuite CrabCavity "
                      "equivalent, not translated")
        elif rfkey == "cavity_type":
            if val == "TRAVELING_WAVE":
                print(f"{name}: Xsuite has no traveling wave cavity; translated "
                      "as a standing wave Cavity")
        elif rfkey == "L_active":
            if "gradient" not in rfP:
                print(f"{name}: RFP.L_active has no Xsuite equivalent, not "
                      "translated")
        else:
            print(f"{name}: RFP.{rfkey} has no Xsuite equivalent, not translated")
    # A phase of zero still has to be written out: Xsuite measures it from
    # somewhere else.
    if "phase" not in rfP and offset != 0:
        attrs.append(("phase", fmt(offset)))
    return attrs


# ---------------------------------------------------------------------------
def _xsuite_control_target(cname: str, param: str, facility: YAMLNode,
                           lat: XsuiteLattice,
                           varmap: Dict[Tuple[str, str], str]
                           ) -> Tuple[str, float, bool]:
    """Translate a controller's ``parameter`` target into the Xsuite attribute it
    drives.

    Returns ``(target, factor, rigidity)`` where ``target`` is the Python text of
    the attribute (``env['q1'].k1``, or ``env['m1'].knl[3]`` for an entry of a
    multipole array), the control expression must be multiplied by ``factor``,
    and ``rigidity`` says whether it must also be divided by the reference
    rigidity to hold the same physics. Neither is trivial in general because the
    element translation does not carry PALS parameters across unchanged: the
    attribute a multipole lands in may be length integrated where the PALS
    parameter was not, or the other way round, and a stated field has to be
    normalized.

    A target may name its element by kind as well as by name, as
    ``{kind}::{name}``; the qualifier is checked against the element found and
    then dropped. Targets Xsuite cannot express -- a pattern matching several
    elements, a ``>>`` qualifier naming the BeamLine or Lattice an element is
    reached through, a parameter with no Xsuite attribute -- raise an error.
    """
    if ">>" in param:
        raise ValueError(f"controller {cname}: `{param}` reaches its element "
                         "through a BeamLine or Lattice qualifier, which Xsuite, "
                         "having one namespace for the whole environment, cannot "
                         "express")

    parts = param.split(">")
    if len(parts) != 2:
        raise ValueError(f"controller {cname}: control parameter `{param}` is not "
                         "of the form `element>parameter`")
    slave, path = parts

    kind_wanted = None
    if "::" in slave:
        qualifier = slave.split("::")
        if len(qualifier) != 2:
            raise ValueError(f"controller {cname}: `{param}` does not name a "
                             "single element kind")
        kind_wanted, slave = qualifier

    if not _NAME_RE.match(slave):
        raise ValueError(f"controller {cname}: `{param}` selects slaves by "
                         "pattern, which Xsuite cannot express")

    props = facility_props(facility, slave)
    if props is None:
        raise ValueError(f"controller {cname}: `{param}` names no element of the "
                         "facility")
    ele_kind = props["kind"].value if "kind" in props else ""
    if kind_wanted is not None and kind_wanted != ele_kind:
        raise ValueError(f"controller {cname}: `{param}` asks for a {kind_wanted} "
                         f"but {slave} is a {ele_kind}")

    # A controller may drive another controller's variable, under whatever Xsuite
    # calls it.
    if ele_kind == "Controller":
        if (slave, path) not in varmap:
            raise ValueError(f"controller {cname}: `{param}` names no variable of "
                             f"controller {slave}")
        return f"env[{varmap[(slave, path)]!r}]", 1.0, False

    if path == "length":
        return f"env[{slave!r}].length", 1.0, False

    m = _MULTIPOLE_RE.match(path)
    if m is None:
        raise ValueError(f"controller {cname}: control parameter `{param}` is not "
                         "yet translated to Xsuite")
    order = int(m.group(3))
    skew = m.group(2) == "s"
    integrated = m.group(4) == "L"
    normalized = m.group(1) == "K"
    ele_length = props["length"].as_float() if "length" in props else 0.0

    # A tilted multipole rotates normal and skew into each other, so the one PALS
    # parameter no longer maps onto the one Xsuite attribute.
    if "MagneticMultipoleP" in props and \
            f"tilt{order}" in props["MagneticMultipoleP"]:
        if not approx(props["MagneticMultipoleP"][f"tilt{order}"].as_float(), 0):
            raise ValueError(f"controller {cname}: `{param}` drives a tilted "
                             "multipole, which has no single Xsuite attribute")

    xsuite_kind = _xsuite_kind(ele_kind, ele_length, slave)
    attribute = _XSUITE_NATIVE_STRENGTH.get(xsuite_kind, {}).get((order, skew))
    if attribute is not None:
        factor = 1.0
        if integrated:
            if ele_length == 0:
                raise ValueError(f"controller {cname}: `{param}` is integrated "
                                 "over an element of zero length, which Xsuite's "
                                 f"`{attribute}` cannot state")
            factor = 1 / ele_length
        return f"env[{slave!r}].{attribute}", factor, not normalized

    if xsuite_kind not in _XSUITE_MULTIPOLE_CLASSES:
        raise ValueError(f"controller {cname}: an Xsuite {xsuite_kind} has no "
                         f"attribute for `{param}`")
    # An entry of a multipole array has to exist before an expression can be
    # assigned to it, so the element's arrays are padded out to reach it.
    array = "ksl" if skew else "knl"
    for ele in lat.elements:
        if ele.name == slave:
            arr = ele.ksl if skew else ele.knl
            arr.extend(["0"] * (order + 1 - len(arr)))
    factor = 1.0 if integrated else ele_length
    return f"env[{slave!r}].{array}[{order}]", factor, not normalized


# ---------------------------------------------------------------------------
def _xsuite_base_value(lat: XsuiteLattice, target: str,
                       initials: Dict[str, str]) -> str:
    """The value ``target`` already holds, as written by the element translation,
    as a PALS value.

    A ``control_type: RELATIVE`` controller varies a parameter rather than
    setting it, and an Xsuite deferred expression can only set one:
    ``env['q1'].k1 = 'k1 + dk'`` would be circular. So the value being varied has
    to be written into the expression, and the one place it is written down is the
    definition this reads it back out of.
    """
    m = re.match(r"^env\['(.*)'\](?:\.(\w+)(?:\[(\d+)\])?)?$", target)
    if m is None:
        return "0"
    name, attribute, index = m.groups()
    # A bare name is another controller's variable, whose value is its initial
    # setting.
    if attribute is None:
        return initials.get(name, "0")

    def unquote(source: str) -> str:
        return ast.literal_eval(source) if source[:1] in "'\"" else source

    for ele in lat.elements:
        if ele.name != name:
            continue
        if index is not None:
            array = ele.knl if attribute == "knl" else ele.ksl
            i = int(index)
            return unquote(array[i]) if i < len(array) else "0"
        for key, source in ele.attrs:
            if key == attribute:
                return unquote(source)
        return "0"
    return "0"


# ---------------------------------------------------------------------------
def _make_xsuite_controller(ele: YAMLNode, facility: YAMLNode, lat: XsuiteLattice,
                            varmap: Dict[Tuple[str, str], str],
                            initials: Dict[str, str]) -> XsuiteController:
    """Translate a ``Controller`` element into an :class:`XsuiteController`.

    ``facility`` is needed to reach the slave elements: what a control expression
    must be scaled by depends on the element it drives (see
    :func:`_xsuite_control_target`). ``varmap`` and ``initials`` carry what each
    controller variable is called in the environment and what it starts at (see
    :func:`_xsuite_variable_names`). ``lat`` is needed for a ``RELATIVE``
    controller, whose slaves keep the value their element definitions already
    gave them.

    An ``ABSOLUTE`` controller sets its slaves outright, and a deferred
    expression does the same. A ``RELATIVE`` one is a knob: its slaves keep the
    value the lattice gave them and move by however far the knob has been turned
    *from where it started*, so the expression is the element's own value, plus
    the control expression, less the control expression at the variables' initial
    settings. That last term is left out only when it can be shown to come to
    zero, which for a knob resting at zero it does.
    """
    props = ele.child(0)
    name = props.node_key()

    control_type = props["control_type"].value if "control_type" in props \
        else "ABSOLUTE"
    if control_type not in ("ABSOLUTE", "RELATIVE"):
        raise ValueError(f"{name}: control_type must be ABSOLUTE or RELATIVE, not "
                         f"{control_type}")

    variables: List[Tuple[str, str]] = []
    renames: Dict[str, str] = {}     # variable -> what Xsuite calls it
    starting: Dict[str, str] = {}    # variable -> where it starts
    for var, value in ctrl_variables(props):
        xsuite = varmap[(name, var)]
        variables.append((xsuite, _xsuite_value(name, value)))
        renames[var] = xsuite
        starting[var] = f"({value})"

    notes: List[str] = []
    if "MetaP" in props:
        metaP = props["MetaP"]
        for mkey in metaP.keys():
            val = metaP[mkey]
            if val.is_map() or val.is_sequence():
                print(f"{name}: MetaP.{mkey} is not a simple string, not "
                      "translated")
                continue
            notes.append(f"{mkey}: {val.value}")

    controls: List[Tuple[str, str]] = []
    if "controls" in props:
        for control in props["controls"]:
            if "parameter" not in control or "expression" not in control:
                raise ValueError(f"{name}: a controls entry needs both a "
                                 "`parameter` and an `expression`")
            target, factor, rigidity = _xsuite_control_target(
                name, control["parameter"].value, facility, lat, varmap)

            pals_expr = control["expression"].value

            # The same scaling the element attribute was given, whichever form of
            # the expression it is being applied to.
            def scaled(expr, factor=factor, rigidity=rigidity):
                if not approx(factor, 1):
                    expr = f"{fmt(factor)}*({expr})"
                if rigidity:
                    expr = f"({expr}) / {_XSUITE_RIGIDITY}"
                return expr

            expression = scaled(_xsuite_substitute(pals_expr, renames))
            if control_type == "RELATIVE":
                expression = (f"{_xsuite_base_value(lat, target, initials)} + "
                              f"({expression})")
                at_start = _xsuite_substitute(pals_expr, starting)
                # A knob that starts where its expression comes to zero has moved
                # nothing yet, and needs no term saying so. Anything the
                # standalone evaluator cannot reach -- a user-defined constant,
                # say -- is written out and left for Xsuite.
                try:
                    zero_at_start = approx(evaluate_pals_expression(at_start), 0)
                except Exception:
                    zero_at_start = False
                if not zero_at_start:
                    expression += f" - ({scaled(at_start)})"
            controls.append((target, repr(_xsuite_expression(name, expression))))

    return XsuiteController(name, variables, controls, notes)
