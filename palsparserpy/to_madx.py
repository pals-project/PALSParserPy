"""
Translation of a PALS lattice into a MAD-X lattice file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ._common import (FullRepresentation, approx, ctrl_variables, facility_props,
                      fill_multipoles, fmt, name_value_pairs, tilt_rotation,
                      try_float, value_text)
from .node import YAMLNode
from .parser import evaluate_pals_expression

__all__ = ["MadxEleDef", "MadxBeamline", "MadxController", "MadxAlignment",
           "MadxLattice", "pals_to_madx", "write_madx_file"]

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MULTIPOLE_RE = re.compile(r"^MagneticMultipoleP\.([KB])([ns])([0-9]+)(L?)$")
_ATTR_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

#: The name of the MAD-X variable holding the *signed* magnetic rigidity ``P0/q``
#: of the reference particle, which the translation writes out when a lattice
#: states a field rather than a normalized strength.
#:
#: MAD-X has no field-valued strength attribute: every magnet strength it holds is
#: normalized. A PALS field therefore has to be divided by the rigidity here,
#: which MAD-X can compute for itself from the ``BEAM`` command: ``beam->brho`` is
#: ``P0/|q|``, so the sign of the charge has to be put back.
_MADX_RIGIDITY = "pals_brho"

#: The MAD-X expression for the relativistic ``beta`` of the reference particle.
#:
#: MAD-X measures the longitudinal coordinates and the dispersion against
#: ``pt = dE/(p0 c)`` where PALS measures them against ``pz = dp/p0``, and the two
#: differ by exactly this factor (``pt = beta * pz``). MAD-X computes it from the
#: ``BEAM`` command, so the conversion can be left as an expression rather than
#: worked out here.
_MADX_BETA = "beam->beta"

#: The MAD-X keywords that may not be used as a label.
#:
#: MAD-X protects its keywords: a lattice whose element is named after one is a
#: fatal error there rather than here, so the translation reports it instead. The
#: list holds the element-type keywords and the commands a lattice file is likely
#: to collide with, not every MAD-X command.
_MADX_KEYWORDS = {
    "marker", "drift", "sbend", "rbend", "dipedge", "quadrupole", "sextupole",
    "octupole", "multipole", "solenoid", "nllens", "hkicker", "vkicker",
    "kicker", "tkicker", "rfcavity", "twcavity", "rfmultipole", "crabcavity",
    "hacdipole", "vacdipole", "elseparator", "hmonitor", "vmonitor", "monitor",
    "instrument", "placeholder", "collimator", "ecollimator", "rcollimator",
    "beambeam", "wire", "matrix", "yrotation", "xrotation", "srotation",
    "translation", "changeref", "sixmarker",
    "line", "sequence", "beam", "beta0", "use", "select", "twiss", "track",
    "survey", "match", "ealign", "efcomp", "eoption", "value", "show", "option",
    "title", "call", "return"}


@dataclass
class MadxEleDef:
    """A single MAD-X element definition.

    - ``name``: the element name.
    - ``type``: the MAD-X element-type keyword (e.g. ``drift``, ``quadrupole``).
    - ``attrs``: already-translated attribute fragments, each an
      ``"attribute = value"`` string.
    - ``notes``: comment lines to write above the definition. MAD-X elements carry
      no metadata strings of their own, so a PALS ``MetaP`` becomes a comment
      here, as does anything else worth saying about the element in the file it is
      written to.
    """
    name: str
    type: str
    attrs: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class MadxBeamline:
    """A MAD-X ``line`` definition: its ``name`` and the ordered list of member
    element ``members`` (by name)."""
    name: str
    members: List[str] = field(default_factory=list)


@dataclass
class MadxController:
    """A MAD-X rendering of a PALS ``Controller``.

    MAD-X has no controller element. What it has instead is the deferred
    assignment ``:=``, which makes an element attribute depend on a variable
    rather than take its value once, and that is what a controller becomes: its
    variables become ordinary MAD-X variables and each of its controls becomes a
    deferred assignment to the attribute it drives.

    - ``name``: the controller name, written out as a comment heading.
    - ``vars``: the variables' initial values, each a ``"name = value"`` string.
    - ``controls``: the deferred assignments, each an
      ``"ele->attribute := expression"`` string.
    - ``notes``: comment lines to write above the definitions, holding the
      controller's ``MetaP``.
    """
    name: str
    vars: List[str] = field(default_factory=list)
    controls: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class MadxAlignment:
    """The misalignment of one element: what a PALS ``BodyShiftP`` becomes.

    MAD-X keeps a misalignment apart from the element definition, in an
    ``EALIGN`` command applied to whatever the preceding ``SELECT, FLAG=ERROR``
    picked out. ``name`` is the element the errors belong to and ``attrs`` the
    ``EALIGN`` attribute fragments.
    """
    name: str
    attrs: List[str] = field(default_factory=list)


@dataclass
class MadxLattice:
    """An in-memory model of a MAD-X lattice.

    Produced by :func:`pals_to_madx` and serialized to a file by
    :func:`write_madx_file`. The fields mirror the sections of a MAD-X lattice
    file:

    - ``constants``: ``name = value;`` definitions, in definition order.
    - ``beam``: the attributes of the ``BEAM`` command (species and energy).
    - ``beta0``: the attributes of the initial-conditions ``BETA0`` block (Twiss
      and dispersion).
    - ``particle_start``: initial particle coordinates, which MAD-X takes in the
      ``TRACK`` module rather than in a lattice file, and which are written out as
      a comment.
    - ``elements``: element definitions (:class:`MadxEleDef`).
    - ``controllers``: variables and deferred assignments
      (:class:`MadxController`).
    - ``alignments``: ``EALIGN`` misalignments (:class:`MadxAlignment`).
    - ``beamlines``: ``line`` definitions (:class:`MadxBeamline`).
    - ``use``: the branches, each a ``(name, periodic)`` pair, for the ``use``
      statement.
    - ``rigidity``: whether anything written refers to the rigidity variable,
      which then has to be defined ahead of it.
    """
    constants: List[str] = field(default_factory=list)
    beam: List[str] = field(default_factory=list)
    beta0: List[str] = field(default_factory=list)
    particle_start: List[str] = field(default_factory=list)
    elements: List[MadxEleDef] = field(default_factory=list)
    controllers: List[MadxController] = field(default_factory=list)
    alignments: List[MadxAlignment] = field(default_factory=list)
    beamlines: List[MadxBeamline] = field(default_factory=list)
    use: List[Tuple[str, bool]] = field(default_factory=list)
    rigidity: bool = False


# ---------------------------------------------------------------------------
def pals_to_madx(yaml: YAMLNode) -> MadxLattice:
    """Translate a parsed PALS lattice ``yaml`` (as returned by
    :func:`~palsparserpy.parse_file`) into a :class:`MadxLattice`.

    The returned structure is an in-memory model of the *MAD-X* lattice
    (elements, lines, beam), not the input PALS tree. Translation is a three-step
    process: parse the PALS file with ``parse_file``, build the target model with
    ``pals_to_madx``, then emit the MAD-X lattice file with
    :func:`write_madx_file`::

        yaml = parse_file(file_dir)
        write_madx_file(pals_to_madx(yaml), filename)

    Controllers are translated after the elements, in a second pass: a
    ``control_type: RELATIVE`` controller adds to the value the element already
    carries, and the only place that value is written down is the element
    definition this pass has just built.
    """
    pals = yaml["PALS"]
    facility = pals["facility"]
    lat = MadxLattice()

    # Constants and variables may be defined directly under `PALS` as well as in
    # the facility.
    for key in ("constants", "variables"):
        if key in pals:
            lat.constants.extend(_madx_constants(pals[key]))

    n_lattices = 0
    controllers: List[YAMLNode] = []
    for ele in facility:
        props = ele.child(0)
        # The compact `constants:`/`variables:` list is a facility entry in its
        # own right, with no `kind` of its own; every other entry the translation
        # looks at is a named element.
        if props.node_key() in ("constants", "variables"):
            lat.constants.extend(_madx_constants(props))
            continue
        if "kind" not in props:
            continue
        pals_kind = props["kind"].value
        if pals_kind == "BeginningEle":
            beam, beta0, particle = _ele_to_madx_str(ele)
            lat.beam.extend(beam)
            lat.beta0.extend(beta0)
            lat.particle_start.extend(particle)
        elif pals_kind == "BeamLine":
            lat.beamlines.append(_make_madx_line(ele, facility))
        elif pals_kind == "Lattice":
            n_lattices += 1
            if n_lattices > 1:
                raise ValueError(
                    "\nDifferent BeamLine complexes must be translated from "
                    "separate files.\nA MAD-X run expands one sequence at a "
                    "time.\n")
            _add_madx_branches(lat, props["branches"])
        elif pals_kind == "Controller":
            controllers.append(ele)
        elif pals_kind in ("constant", "variable"):
            lat.constants.append(_madx_constant(props, pals_kind))
        else:
            definition, align = _make_madx_ele(ele)
            lat.elements.append(definition)
            if align.attrs:
                lat.alignments.append(align)
            if any(_MADX_RIGIDITY in a for a in definition.attrs):
                lat.rigidity = True

    # A PALS controller owns its variables and MAD-X has no such scope, so what
    # each one is called in the file has to be settled before any of them is
    # written out or referred to.
    varmap, initials = _madx_variable_names(controllers, lat.constants)
    for ele in controllers:
        lat.controllers.append(
            _make_madx_controller(ele, facility, lat, varmap, initials))
    _check_madx_variables(lat)
    if any(_MADX_RIGIDITY in s for c in lat.controllers for s in c.controls):
        lat.rigidity = True

    return lat


# ---------------------------------------------------------------------------
def _madx_definition_name(defn: str) -> str:
    """The name a ``"name = value"`` definition defines."""
    return defn.split("=")[0].strip()


# ---------------------------------------------------------------------------
def _madx_variable_names(controllers: List[YAMLNode], constants: List[str]
                         ) -> Tuple[Dict[Tuple[str, str], str], Dict[str, str]]:
    """Decide what each controller variable is called in the MAD-X file.

    Returns ``(names, initials)`` where ``names`` maps a
    ``(controller, variable)`` pair to its MAD-X name and ``initials`` maps that
    MAD-X name to the variable's initial value.

    A PALS controller owns its variables: ``ps1>cur`` and ``ps2>cur`` are two
    independent knobs, and the standard's own example uses exactly that. A MAD-X
    variable is a name in the one namespace the whole file shares, so a variable
    whose bare name is claimed by another controller, or by a constant, is
    prefixed with the controller that owns it. One that is claimed by nobody else
    keeps its bare name, which is what nearly every lattice will have and is far
    the easier to read.
    """
    claimed: Dict[str, int] = {}
    for ele in controllers:
        for var, _ in ctrl_variables(ele.child(0)):
            claimed[var] = claimed.get(var, 0) + 1
    taken = {_madx_definition_name(c) for c in constants}

    names: Dict[Tuple[str, str], str] = {}
    initials: Dict[str, str] = {}
    for ele in controllers:
        cname = ele.child(0).node_key()
        for var, value in ctrl_variables(ele.child(0)):
            madx = f"{cname}__{var}" if (claimed[var] > 1 or var in taken) else var
            names[(cname, var)] = madx
            initials[madx] = value
    return names, initials


# ---------------------------------------------------------------------------
def _check_madx_variables(lat: MadxLattice) -> None:
    """Report two MAD-X definitions that would claim the one name.

    :func:`_madx_variable_names` prefixes a controller variable that another
    controller's variable or a constant already claims, which settles every
    collision a PALS lattice can have honestly. This is the backstop for the one
    it cannot: a constant named after the prefixed form itself.
    """
    seen: Dict[str, str] = {}
    for constant in lat.constants:
        seen[_madx_definition_name(constant)] = "a constant or variable definition"
    for ctrl in lat.controllers:
        for var in ctrl.vars:
            name = _madx_definition_name(var)
            if name in seen:
                raise ValueError(
                    f"controller {ctrl.name}: variable `{name}` is already "
                    f"defined by {seen[name]}; MAD-X variables are global, so "
                    "the two would drive one another")
            seen[name] = f"controller {ctrl.name}"


# ---------------------------------------------------------------------------
def _madx_substitute(expr: str, replacements: Dict[str, str]) -> str:
    """``expr`` with each name in ``replacements`` replaced by what it maps to.

    Used to put a controller's variables into an expression under whatever MAD-X
    calls them (see :func:`_madx_variable_names`), and to put their initial values
    in place of them. The match is on whole identifiers, and a name reached
    through a dot is left alone, so a variable ``cur`` does not rewrite
    ``current`` nor ``SELF.cur``. Every name is replaced in one pass, so a
    replacement is never itself replaced.
    """
    if not replacements:
        return expr
    alternatives = "|".join(re.escape(name) for name in replacements)
    pattern = re.compile(f"(?<![A-Za-z0-9_.])({alternatives})(?![A-Za-z0-9_])")
    return pattern.sub(lambda m: replacements[m.group(1)], expr)


# ---------------------------------------------------------------------------
def _add_madx_branches(lat: MadxLattice, branches: YAMLNode) -> MadxLattice:
    """Translate a PALS ``Lattice``'s ``branches`` sequence into ``lat``.

    Append each branch to ``lat.use`` as a ``(name, periodic)`` pair. MAD-X has no
    geometry attribute of its own: whether a branch closes on itself is decided by
    how it is used -- a ``TWISS`` given no initial conditions looks for the
    periodic solution -- so the flag is carried through to the comment
    :func:`write_madx_file` writes beside the ``use`` statement.
    """
    if len(branches) == 0:
        return lat
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
            raise ValueError("Expanding lattices is not done during PALS>MAD-X "
                             "translation")
        else:
            raise ValueError(f"This object is neither a scalar, map, nor "
                             f"sequence: {bl!r}")
        lat.use.append((name, periodic))
    return lat


# ---------------------------------------------------------------------------
def write_madx_file(lat: MadxLattice, filename) -> None:
    """Serialize the :class:`MadxLattice` ``lat`` to ``filename`` as a MAD-X
    lattice file.

    Write the constant and variable definitions, the ``BEAM`` command and initial
    conditions, the element definitions, the controller variables and their
    deferred assignments, the ``line`` definitions, the ``use`` statement, and the
    ``EALIGN`` misalignments, each in its own labelled section.

    The order of the sections is the order MAD-X needs them in, which is stricter
    than Bmad's: a name has to be defined above the point of use, ``BEAM`` has to
    come before ``USE``, and the ``SELECT``/``EALIGN`` pairs have to come after
    it, because there is no sequence to apply an error to until one has been
    expanded.
    """
    with open(filename, "w") as out:
        n_section = 0

        # Head each section with the same rule and title, one blank line clear of
        # the one before.
        def section(title):
            nonlocal n_section
            n_section += 1
            if n_section != 1:
                out.write("\n")
            out.write("!=====================================================" +
                      "=================\n" + f"! {title} \n\n")

        if lat.constants:
            section("Constant and variable definitions")
            for constant in lat.constants:
                out.write(constant + ";\n")

        if lat.beam or lat.beta0 or lat.particle_start or lat.rigidity:
            section("Beam and initial conditions")
            if lat.beam:
                out.write("beam, " + ", ".join(lat.beam) + ";\n")

            # A field the lattice states rather than normalizes is divided by
            # this. MAD-X's own `beam->brho` is P0/|q|, and the PALS
            # normalization is by the signed charge.
            if lat.rigidity:
                out.write("\n! Signed magnetic rigidity P0/q, which normalizes a "
                          "stated field.\n"
                          f"{_MADX_RIGIDITY} := beam->brho * beam->charge / "
                          "abs(beam->charge);\n")

            if lat.beta0:
                out.write("\npals_beta0: beta0,\n\t" + ",\n\t".join(lat.beta0) +
                          ";\n! twiss, beta0 = pals_beta0;\n")

            # MAD-X starts a particle in the TRACK module, which has no place in
            # a lattice file.
            if lat.particle_start:
                out.write("\n! Initial particle coordinates. MAD-X sets these "
                          "with the START command:\n"
                          "!   track;\n"
                          "!   start, " + ", ".join(lat.particle_start) + ";\n"
                          "!   run, turns = 1;\n"
                          "!   endtrack;\n")

        section("Element definitions")
        for ele in lat.elements:
            out.write(_format_madx_ele(ele) + "\n")

        # A controller spans several lines, so the definitions are set apart from
        # one another.
        if lat.controllers:
            section("Controller definitions")
            out.write("\n\n".join(_format_madx_controller(c)
                                  for c in lat.controllers) + "\n")

        section("Beamline definitions")
        if lat.beamlines:
            out.write("\n\n".join(_format_madx_line(b) for b in lat.beamlines) + "\n")

        section("Branch structure")
        for i, (name, periodic) in enumerate(lat.use):
            # MAD-X expands one sequence at a time, and each `use` replaces the
            # last, so only the first branch can be the active one.
            prefix = "" if i == 0 else "! "
            geometry = "closed" if periodic else "open"
            out.write(f"{prefix}use, period = {name};\t! {geometry}\n")
        if len(lat.use) > 1:
            out.write("! Only one branch can be expanded at a time; the rest are "
                      "commented out.\n")

        # An EALIGN applies to whatever the preceding SELECT picked out of the
        # expanded sequence, so this section can only come after the `use` above.
        if lat.alignments:
            section("Alignment errors")
            out.write("\n\n".join(_format_madx_alignment(a)
                                  for a in lat.alignments) + "\n")


# ---------------------------------------------------------------------------
def _format_madx_ele(ele: MadxEleDef) -> str:
    """Render a :class:`MadxEleDef` as a ``name: type, attr = val, ...;`` MAD-X
    element definition, with each attribute on its own tab-indented continuation
    line and each note on a comment line above."""
    text = ""
    for note in ele.notes:
        text += f"! {note}\n"
    text += f"{ele.name}: {ele.type}"
    for attr in ele.attrs:
        text += f",\n\t{attr}"
    return text + ";"


# ---------------------------------------------------------------------------
def _format_madx_controller(ctrl: MadxController) -> str:
    """Render a :class:`MadxController` as its variable definitions followed by
    the deferred assignments that depend on them, under a comment naming the
    controller they came from."""
    text = f"! Controller {ctrl.name}\n"
    for note in ctrl.notes:
        text += f"! {note}\n"
    for var in ctrl.vars:
        text += f"{var};\n"
    return text + "\n".join(c + ";" for c in ctrl.controls)


# ---------------------------------------------------------------------------
def _format_madx_alignment(align: MadxAlignment) -> str:
    """Render a :class:`MadxAlignment` as the ``SELECT``/``EALIGN`` pair that
    applies it.

    The element is picked out by an anchored pattern rather than by a range so
    that a name which is a prefix of another one does not take its neighbour's
    errors with it. A MAD-X label may hold a decimal point, which a MAD-X pattern
    reads as "any character", so the name is escaped.
    """
    pattern = re.sub(r"([.*\[\]^$\\])", r"\\\1", align.name)
    return ("select, flag = error, clear;\n"
            f'select, flag = error, pattern = "^{pattern}$";\n'
            "ealign, " + ", ".join(align.attrs) + ";")


# ---------------------------------------------------------------------------
def _format_madx_line(bl: MadxBeamline) -> str:
    """Render a :class:`MadxBeamline` as a MAD-X ``name: line = (...);``
    definition, wrapping the member list with tab-indented continuation lines to
    keep rows under ~80 columns."""
    line_str = ""
    tmp = ""
    l_tmp = len(bl.name) + 4
    n = len(bl.members)

    for i, member in enumerate(bl.members):
        ele_str = member
        if i < n - 1:
            ele_str += ", "
        l_ele_str = len(ele_str)

        if l_tmp + l_ele_str < 80:
            tmp += ele_str
            l_tmp += l_ele_str
        else:
            line_str += tmp + "\n"
            tmp = "\t" + ele_str
            l_tmp = 7 + l_ele_str
    line_str += tmp

    wrapped = line_str if len(line_str) < 80 else ("\n\t" + line_str + "\n\t")
    return f"{bl.name}: line = ({wrapped});"


# ---------------------------------------------------------------------------
def _madx_scale(text: str, factor: float) -> str:
    """``text`` scaled by ``factor``, as a MAD-X value.

    PALS and MAD-X differ in the units of nearly every quantity that is not a
    length or an angle: energies are eV against GeV, voltages V against MV,
    frequencies Hz against MHz. A value written as a number is scaled here and
    comes out a number; one written as an expression -- a constant, say -- is left
    for MAD-X to evaluate and comes out an expression.
    """
    if factor == 1:
        return text
    value = try_float(text)
    return f"({text}) * {fmt(factor)}" if value is None else fmt(value * factor)


# ---------------------------------------------------------------------------
def _madx_shift(text: str, offset: float) -> str:
    """``text`` with ``offset`` added, as a MAD-X value. As with
    :func:`_madx_scale`, a number comes out a number and an expression comes out
    an expression."""
    if offset == 0:
        return text
    value = try_float(text)
    return f"({text}) + {fmt(offset)}" if value is None else fmt(value + offset)


# ---------------------------------------------------------------------------
def _madx_divide(text: str, divisor: str) -> str:
    """``text`` divided by the MAD-X expression ``divisor``, which is not a number
    here and so cannot be worked out during translation."""
    return f"({text}) / {divisor}"


# ---------------------------------------------------------------------------
def _madx_strength(value: float, normalized: bool) -> str:
    """A magnet strength as a MAD-X value.

    Every MAD-X strength attribute is normalized, so a PALS component given as a
    field is divided by the reference rigidity instead of being written out as it
    stands.
    """
    return fmt(value) if normalized else f"{fmt(value)} / {_MADX_RIGIDITY}"


# ---------------------------------------------------------------------------
def _madx_logical(node: YAMLNode) -> str:
    """A PALS boolean as MAD-X's ``true``/``false``."""
    return "true" if node.value.lower() == "true" else "false"


# ---------------------------------------------------------------------------
def _madx_check_name(name: str) -> str:
    """Report a name MAD-X cannot hold, and warn about one it would quietly
    truncate.

    A MAD-X label is at most sixteen characters -- the rest are dropped, which can
    turn two elements into one -- and may not be one of MAD-X's own keywords,
    which is a fatal error there.
    """
    if name.lower() in _MADX_KEYWORDS:
        raise ValueError(f"{name}: is a MAD-X keyword and cannot be used as a "
                         "label")
    if len(name) > 16:
        print(f"{name}: is longer than the 16 characters a MAD-X label keeps; "
              "the rest will be dropped")
    return name


# ---------------------------------------------------------------------------
#: The PALS predefined constants MAD-X either spells differently or does not have
#: at all.
#:
#: ``pi`` is the one the two agree on and is absent from this list. A value of
#: ``None`` means MAD-X has no constant for it. MAD-X's ``emass``, ``pmass`` and
#: ``mumass`` are masses in GeV, where PALS' ``mass_of`` is in eV, so they are not
#: a rename of anything here.
_MADX_CONSTANT_NAMES: Dict[str, Optional[str]] = {
    "c_light": "clight", "e_charge": "qelect",
    "r_electron": "erad", "r_proton": "prad",
    "h_planck": None, "hbar": None,
    "k_boltzmann": None, "eps_0_vac": None,
    "mu_0_vac": "amu0", "classical_radius_factor": None,
    "fine_structure": None, "n_avogadro": None}


# ---------------------------------------------------------------------------
def _madx_check_expression(where: str, text: str) -> str:
    """Report a PALS expression MAD-X has no way to evaluate, and return ``text``
    unchanged.

    An expression is carried across as it stands, MAD-X's arithmetic and its
    ordinary functions being PALS' as well. Two things in one are not: PALS'
    particle-data functions, which look up a species in a table MAD-X does not
    carry, and most of PALS' predefined constants, which MAD-X either spells
    differently or does not have. Both are reported rather than rewritten -- as
    they are for the other translators, expression translation being an open item
    for all of them.
    """
    for fn in ("mass_of", "charge_of", "anomalous_moment_of"):
        if f"{fn}(" in text:
            print(f"{where}: `{fn}` is a PALS function with no MAD-X equivalent; "
                  f"`{text}` will not evaluate")
    for name, madx in _MADX_CONSTANT_NAMES.items():
        if not re.search(f"(?<![A-Za-z0-9_]){name}(?![A-Za-z0-9_])", text):
            continue
        print(f"{where}: the PALS constant `{name}` is " +
              ("not one MAD-X has" if madx is None else f"MAD-X's `{madx}`") +
              f"; `{text}` needs it renamed by hand")
    return text


# ---------------------------------------------------------------------------
def _madx_constants(node: YAMLNode) -> List[str]:
    """Translate a compact-form ``constants:``/``variables:`` list into MAD-X
    ``name = value`` definitions.

    MAD-X draws no distinction between the two: both become a named value the rest
    of the lattice file may use in an expression, so both lists translate the same
    way.
    """
    return [f"{name} = {_madx_check_expression(name, value)}"
            for name, value in name_value_pairs(node)]


# ---------------------------------------------------------------------------
def _madx_constant(props: YAMLNode, pals_kind: str) -> str:
    """Translate a full-form (``kind: constant``, ``kind: variable``) definition
    into a MAD-X ``name = value`` definition.

    A definition whose ``value`` is a structure rather than a single value has no
    MAD-X equivalent and raises an error; one with no ``value`` at all takes PALS'
    default of zero. A MAD-X variable is a value and nothing else, so the error
    bars a PALS definition may carry are reported.
    """
    name = props.node_key()
    for err in ("absolute_error", "relative_error"):
        if err in props:
            print(f"{name}: a MAD-X variable carries no error bar, so {err} is "
                  "not translated")
    if "value" not in props:
        return f"{name} = 0"
    value = props["value"]
    if value.is_map() or value.is_sequence():
        raise ValueError(f"{name}: the `value` of a `{pals_kind}` is not a "
                         "single value")
    return f"{name} = {_madx_check_expression(name, value_text(value))}"


# ---------------------------------------------------------------------------
def _madx_species(species: str) -> str:
    """Map a PALS reference species onto a MAD-X ``PARTICLE``.

    MAD-X knows the mass and charge of a fixed handful of species and nothing
    else; anything outside that set has to be given its mass and charge outright,
    which PALS does not state and which the translation therefore cannot supply.
    """
    known = {"positron": "positron", "electron": "electron", "proton": "proton",
             "anti-proton": "antiproton", "antiproton": "antiproton",
             "muon+": "posmuon", "posmuon": "posmuon",
             "muon-": "negmuon", "negmuon": "negmuon", "muon": "negmuon"}
    name = species.strip("\"' ").lower()
    if name in known:
        return known[name]
    print(f"species_ref `{species}` is not one MAD-X knows; its mass and charge "
          "have to be given to the BEAM command by hand")
    return name


# ---------------------------------------------------------------------------
def _ele_to_madx_str(ele: YAMLNode) -> Tuple[List[str], List[str], List[str]]:
    """Translate a ``BeginningEle`` element into the MAD-X beam and
    initial-condition settings.

    Returns ``(beam, beta0, particle)`` where ``beam`` holds the ``BEAM``
    attributes from the element's ``ReferenceP`` (species and energy), ``beta0``
    holds the ``BETA0`` attributes from its ``TwissP`` (initial Twiss and
    dispersion), and ``particle`` holds the ``START`` attributes from its
    ``ParticleP`` (initial phase-space coordinates).

    Three PALS quantities do not survive the crossing:

      - PALS states the Twiss parameters in the ``a``/``b`` normal modes and
        MAD-X in the ``x``/``y`` planes, which are the same thing only when the
        lattice is uncoupled.
      - The coupling itself is stated as Bmad's ``C`` matrix here and as MAD-X's
        ``R`` matrix there, which are different parametrizations, so ``cmat11``
        and its fellows are not translated.
      - MAD-X has no dispersion derivative, only the momentum dispersion, so
        ``deta_x_ds`` is not translated either.
    """
    props = ele.child(0)
    name = props.node_key()
    beam: List[str] = []
    beta0: List[str] = []
    particle: List[str] = []
    for key in props.keys():
        if key == "TwissP":
            twissP = props["TwissP"]
            for k in twissP.keys():
                val = twissP[k].value
                if k == "beta_a":
                    beta0.append(f"betx = {val}")
                elif k == "beta_b":
                    beta0.append(f"bety = {val}")
                elif k == "alpha_a":
                    beta0.append(f"alfx = {val}")
                elif k == "alpha_b":
                    beta0.append(f"alfy = {val}")
                # MAD-X counts the phase in turns where PALS counts it in radians.
                elif k == "phi_a":
                    beta0.append(f"mux = {_madx_scale(val, 1 / (2 * math.pi))}")
                elif k == "phi_b":
                    beta0.append(f"muy = {_madx_scale(val, 1 / (2 * math.pi))}")
                # MAD-X differentiates against `pt` and PALS against `pz`, and
                # `pt = beta * pz`.
                elif k == "eta_x":
                    beta0.append(f"dx = {_madx_divide(val, _MADX_BETA)}")
                elif k == "eta_y":
                    beta0.append(f"dy = {_madx_divide(val, _MADX_BETA)}")
                elif k == "etap_x":
                    beta0.append(f"dpx = {_madx_divide(val, _MADX_BETA)}")
                elif k == "etap_y":
                    beta0.append(f"dpy = {_madx_divide(val, _MADX_BETA)}")
                elif k.startswith("cmat"):
                    print(f"{name}: TwissP.{k} is Bmad's coupling matrix, which "
                          "is not MAD-X's R matrix, not translated")
                elif k in ("deta_x_ds", "deta_y_ds"):
                    print(f"{name}: TwissP.{k} has no MAD-X equivalent, not "
                          "translated")
        elif key == "ReferenceP":
            referenceP = props["ReferenceP"]
            for k in referenceP.keys():
                val = referenceP[k].value
                if k == "species_ref":
                    beam.append(f"particle = {_madx_species(val)}")
                # PALS states the reference energy in eV, MAD-X in GeV.
                elif k == "pc_ref":
                    beam.append(f"pc = {_madx_scale(val, 1e-9)}")
                elif k == "E_tot_ref":
                    beam.append(f"energy = {_madx_scale(val, 1e-9)}")
                elif k in ("time_ref", "location"):
                    print(f"{name}: ReferenceP.{k} has no MAD-X equivalent, not "
                          "translated")
        elif key == "ParticleP":
            particleP = props["ParticleP"]
            for k in particleP.keys():
                val = particleP[k].value
                if k in ("x", "px", "y", "py"):
                    particle.append(f"{k} = {val}")
                # MAD-X's longitudinal pair is measured against the energy where
                # PALS' is measured against the momentum, and the two differ by
                # the reference velocity.
                elif k == "z":
                    particle.append(f"t = {_madx_divide(val, _MADX_BETA)}")
                elif k == "pz":
                    particle.append(f"pt = {val} * {_MADX_BETA}")
                elif k in ("spin_x", "spin_y", "spin_z"):
                    print(f"{name}: ParticleP.{k} has no MAD-X equivalent, not "
                          "translated")
    # MAD-X takes the species first and works the rest of the beam out from it,
    # so that is the order the BEAM command reads best in whatever order the PALS
    # file gave.
    beam.sort(key=lambda s: 0 if s.startswith("particle") else 1)
    return beam, beta0, particle


# ---------------------------------------------------------------------------
def _make_madx_line(ele: YAMLNode, facility: YAMLNode) -> MadxBeamline:
    """Translate a ``BeamLine`` element into a :class:`MadxBeamline`.

    Collect the member element names into the returned beamline. A leading
    ``BeginningEle`` is dropped -- it carries the reference parameters, which
    become the ``BEAM`` command, and MAD-X has no element for it -- whether it is
    spelled out in the line or named there and defined in the ``facility``. A line
    that does not begin with one, which is a branch forked into with its reference
    parameters propagated, keeps every element it has.
    """
    props = ele.child(0)
    name = _madx_check_name(props.node_key())
    line = props["line"]

    members: List[str] = []
    for i in range(len(line)):
        line_ele = line.child(i)
        if line_ele.is_scalar():
            member = line_ele.value
        elif line_ele.is_map() or line_ele.is_sequence():
            member = line_ele.child(0).node_key()
        else:
            raise ValueError(f"BeamLine {name} element {i + 1} is not scalar or "
                             "sequence or map")

        if i == 0:
            entry_props = line_ele.child(0) \
                if (line_ele.is_map() or line_ele.is_sequence()) \
                else facility_props(facility, member)
            if entry_props is not None and "kind" in entry_props and \
                    entry_props["kind"].value == "BeginningEle":
                continue
        members.append(member)
    return MadxBeamline(name, members)


# ---------------------------------------------------------------------------
def _madx_kind(ele_kind: str) -> str:
    """The MAD-X element-type keyword for the PALS ``ele_kind``.

    Kinds with no MAD-X equivalent raise an error: MAD-X has no branching
    (``Fork``), no support structures (``Girder``), no element that changes the
    reference energy in mid-line (``ReferenceChange``), and no way to build one
    element out of several (``UnionEle``).
    """
    # PALS has the one `Bend`, whose reference geometry is a sector; the pole face
    # rotations that make a bend rectangular are parameters of it (`e1_rect`,
    # `e2_rect`), not a second kind. MAD-X splits the two, so `Bend` maps to
    # MAD-X's sector bend and MAD-X's `RBEND` has no PALS kind to map from.
    #
    # A MAD-X collimator is a drift that its aperture can stop a particle in,
    # which is what a PALS Mask is; MAD-X has nothing for the mask pattern itself.
    #
    # MAD-X has no beginning element: the reference parameters a PALS BeginningEle
    # carries are the BEAM command, and are handled before this point.
    known = {
        # Magnets and RF Cavities
        "Bend": "sbend", "CrabCavity": "crabcavity", "Drift": "drift",
        "Kicker": "kicker", "Multipole": "multipole", "Octupole": "octupole",
        "Quadrupole": "quadrupole", "RFCavity": "rfcavity",
        "Sextupole": "sextupole", "Solenoid": "solenoid",
        # Beam and Plasma Elements
        "BeamBeam": "beambeam",
        # Sources and Collimation
        "Mask": "collimator",
        # Instrumentation and Diagnostics
        "Instrument": "instrument",
        # Map Elements
        "Taylor": "matrix",
        # Bookkeeping Elements
        "BeginningEle": "marker", "Marker": "marker",
        "Placeholder": "placeholder", "Patch": "changeref"}
    if ele_kind in known:
        return known[ele_kind]

    unsupported = {
        "ACKicker": "No MAD-X equivalent of an ACKicker: HACDIPOLE and VACDIPOLE "
                    "each act in one plane",
        "Wiggler": "No Wiggler elements in MAD-X",
        "Converter": "No Converter elements in MAD-X",
        "EGun": "No EGun elements in MAD-X",
        "Foil": "No Foil elements in MAD-X",
        "Match": "No Match elements in MAD-X",
        "Fiducial": "No Fiducial elements in MAD-X",
        "FloorShift": "No FloorShift elements in MAD-X",
        "Fork": "No Fork elements in MAD-X: a MAD-X lattice does not branch",
        "ReferenceChange": "No ReferenceChange elements in MAD-X: the reference "
                           "energy is the BEAM command's",
        "Girder": "No Girder elements in MAD-X",
        "UnionEle": "No UnionEle in MAD-X",
        "Feedback": "No Feedback elements in MAD-X"}
    if ele_kind in unsupported:
        raise ValueError(unsupported[ele_kind])
    raise ValueError(f"Element kind {ele_kind} is not translated to MAD-X")


# ---------------------------------------------------------------------------
#: The multipole orders an element kind holds as its own strength, and the MAD-X
#: attributes that hold them.
#:
#: Each entry maps a PALS element kind to a map from ``(order, skew)`` to the
#: MAD-X attribute name. A quadrupole's order-1 field is MAD-X's ``k1``, not a
#: multipole of a general element; a bend also carries a quadrupole and a
#: sextupole component of its own. Every attribute here holds a strength that is
#: not length integrated, bar the kicker's, which holds a deflection angle -- see
#: :func:`_madx_native_strength`.
#:
#: MAD-X's normal and skew coefficients are the plain field derivatives,
#: ``Kn L = (L/Brho) d^n By/dx^n`` and ``Ks n L = (L/Brho) d^n Bx/dx^n``, and so
#: are PALS': the ``1/N!`` of the PALS field expansion belongs to the expansion and
#: not to the coefficient. So, unlike the Bmad translation, nothing here picks up
#: a factorial.
_MADX_NATIVE_STRENGTH: Dict[str, Dict[Tuple[int, bool], str]] = {
    "Bend": {(0, False): "angle", (1, False): "k1", (1, True): "k1s",
             (2, False): "k2"},
    "Quadrupole": {(1, False): "k1", (1, True): "k1s"},
    "Sextupole": {(2, False): "k2", (2, True): "k2s"},
    "Octupole": {(3, False): "k3", (3, True): "k3s"},
    "Kicker": {(0, False): "hkick", (0, True): "vkick"}}

#: The MAD-X strength attributes that hold a length-integrated value.
#:
#: Every other attribute in :data:`_MADX_NATIVE_STRENGTH` holds a strength per unit
#: length, so a PALS value has to be integrated or de-integrated to match
#: whichever it lands in.
_MADX_INTEGRATED_STRENGTH = {"angle", "hkick", "vkick"}

#: The MAD-X element types that have no length attribute at all.
#:
#: MAD-X rejects an attribute an element type does not have, so a ``length`` --
#: which every PALS element may carry, if only as a zero -- cannot simply be
#: written out. A ``multipole`` is thin too but does have a length of a sort, the
#: fictitious ``lrad``, and is handled apart from these.
_MADX_THIN_KINDS = {"marker", "beambeam", "changeref"}


# ---------------------------------------------------------------------------
def _madx_multipole(full: FullRepresentation, order: int) -> Tuple[float, float]:
    """The normal and skew components of multipole ``order``, with the
    multipole's own tilt rotated into them.

    A tilt of ``T`` on an order-``N`` multipole rotates it by ``(N+1) T`` in the
    normal/skew plane. MAD-X has one tilt for the whole element rather than one
    per order, so the rotation is worked out here and what comes out is a plain
    normal/skew pair. The components keep whatever units the PALS file gave them:
    normalized or not, integrated or not.
    """
    value = (complex(*full.magnitude[order])
             * tilt_rotation(order, full.tilt.get(order, 0.0)))
    return value.real, value.imag


# ---------------------------------------------------------------------------
def _madx_native_strength(full: FullRepresentation, ele_kind: str, name: str,
                          ref_angle=None) -> List[str]:
    """Take the multipoles that are an element's own strength out of ``full`` and
    return their MAD-X attribute fragments.

    The strength of a MAD-X quadrupole is its ``k1``, so that is where a PALS
    ``Kn1`` belongs. Unlike Bmad, MAD-X has an attribute for the skew component of
    each of these -- ``k1s``, ``k2s``, ``k3s`` -- so a tilted multipole of the
    element's own order needs nothing left over, and unlike Bmad it has no
    field-valued attribute, so an unnormalized component is divided by the
    reference rigidity.

    The length is put in or taken out to match the attribute: ``k1`` is a strength
    per unit length and ``angle`` and the kicker's ``hkick`` are integrated. An
    element of zero length whose PALS value is not integrated has no strength to
    state, and neither has one whose integrated value cannot be spread over a
    length of zero; both are reported.

    Two of these attributes are not simply the multipole they come from:

      - A bend's order-0 field is its ``angle``. Bmad states the departure of the
        field from the reference bend and can hold the two apart; MAD-X cannot,
        because it builds the bend's geometry out of the same ``angle`` it tracks
        through (``k0`` is in its database but not in its map). So ``ref_angle``,
        the angle the ``BendP`` geometry has already been written out as (see
        :func:`_madx_bend_geometry`), is what the field is checked against: a
        ``Kn0`` that agrees with it has nothing left to state, and one that
        disagrees is reported and dropped, because the alternative -- writing the
        field out as the angle -- would move every element downstream of the bend.
      - A kicker's deflection is measured the opposite way round from a bend's, in
        both MAD-X and PALS: a positive ``hkick`` bends towards positive ``x`` and
        a positive ``Kn0`` towards negative ``x``, so the horizontal one changes
        sign.
    """
    attrs: List[str] = []
    if ele_kind not in _MADX_NATIVE_STRENGTH:
        return attrs
    native = _MADX_NATIVE_STRENGTH[ele_kind]

    for order in sorted(full.magnitude):
        if (order, False) not in native and (order, True) not in native:
            continue
        normal, skew = _madx_multipole(full, order)
        normalized = full.normalized[order]

        for component, is_skew in ((normal, False), (skew, True)):
            attribute = native.get((order, is_skew))
            if attribute is None:
                if not approx(component, 0):
                    print(f"{name}: a MAD-X {_madx_kind(ele_kind)} has no "
                          f"attribute for the {'skew' if is_skew else 'normal'} "
                          f"order-{order} multipole, not translated")
                continue

            # Put the length in, or take it out, to match what the attribute
            # holds.
            integrated = attribute in _MADX_INTEGRATED_STRENGTH
            value = component
            if integrated and not full.integrated[order]:
                value *= full.L
            elif not integrated and full.integrated[order]:
                if full.L == 0:
                    if not approx(value, 0):
                        print(f"{name}: an integrated order-{order} multipole "
                              "cannot be spread over an element of zero length, "
                              "not translated")
                    continue
                value /= full.L
            if attribute == "hkick":
                value = -value

            # The bend's geometry has already been written out as this same
            # attribute.
            if attribute == "angle" and ref_angle is not None:
                ref_text, ref_value = ref_angle
                # A bend given only a skew order-0 states no field angle to
                # reconcile at all.
                if approx(value, 0) and approx(full.magnitude[order][0], 0):
                    continue
                if normalized and ref_value is not None and approx(value, ref_value):
                    continue
                print(f"{name}: the bend field states an angle of "
                      f"{_madx_strength(value, normalized)} where the reference "
                      f"bend geometry states {ref_text}; MAD-X has the one "
                      "`angle` for both, so the field is not translated")
                continue

            if approx(value, 0):
                continue
            attrs.append(f"{attribute} = {_madx_strength(value, normalized)}")

        # Whichever components landed somewhere are the element's own and stay
        # out of the multipole form; the rest were reported just above.
        full.magnitude.pop(order, None)
        full.integrated.pop(order, None)
        full.normalized.pop(order, None)
        full.tilt.pop(order, None)
    return attrs


# ---------------------------------------------------------------------------
def _madx_multipole_attrs(full: FullRepresentation, name: str) -> List[str]:
    """The ``knl``/``ksl`` attribute fragments of a MAD-X ``multipole``.

    MAD-X states a thin multipole as two arrays of integrated coefficients indexed
    by order from zero up, so an order that is not there still needs its zero
    written in. A component given as a field is divided by the reference rigidity,
    which makes the array entry an expression rather than a number -- which MAD-X
    is happy with, the entries being expressions in general.
    """
    if not full.magnitude:
        return []
    n = max(full.magnitude)
    knl = ["0"] * (n + 1)
    ksl = ["0"] * (n + 1)
    has_normal = False
    has_skew = False

    for order in sorted(full.magnitude):
        normal, skew = _madx_multipole(full, order)
        # Every entry of a MAD-X multipole array is length integrated.
        if not full.integrated[order]:
            normal *= full.L
            skew *= full.L
        normalized = full.normalized[order]
        if not approx(normal, 0):
            knl[order] = _madx_strength(normal, normalized)
            has_normal = True
        if not approx(skew, 0):
            ksl[order] = _madx_strength(skew, normalized)
            has_skew = True

    attrs: List[str] = []
    if has_normal:
        attrs.append("knl = {" + ", ".join(knl) + "}")
    if has_skew:
        attrs.append("ksl = {" + ", ".join(ksl) + "}")
    return attrs


# ---------------------------------------------------------------------------
def _madx_fold(text: str, f, *args: str) -> str:
    """``text``, or the number it comes to when every one of ``args`` is a number.

    A PALS parameter may be written as an expression, which only MAD-X can
    evaluate, or as a plain number, which the translation can work with. Where a
    MAD-X value has to be derived from several PALS ones, ``text`` is that
    derivation written as a MAD-X expression and ``f`` is the same derivation as a
    function, applied here when all of its inputs parse.
    """
    values = [try_float(a) for a in args]
    if any(v is None for v in values):
        return text
    result = f(*values)
    return fmt(result) if math.isfinite(result) else text


# ---------------------------------------------------------------------------
def _madx_times(a: str, b: str) -> str:
    """The product of two MAD-X values, without the clutter of a factor of one.

    Two numbers are multiplied out here; a unit factor -- which is what an element
    of unit length gives, and what several of the bend derivations reduce to --
    comes back as the other operand alone rather than as a product with nothing in
    it.
    """
    va, vb = try_float(a), try_float(b)
    if va is not None and vb is not None:
        return fmt(va * vb)
    if va == 1:
        return b
    if vb == 1:
        return a
    return f"({a}) * ({b})"


# ---------------------------------------------------------------------------
def _madx_bend_geometry(props: YAMLNode, name: str):
    """The reference geometry of a ``Bend`` as ``(angle, angle_value,
    arc_length)``, or ``None`` if the element states none.

    PALS states a bend's geometry with any two of three sets of mutually dependent
    parameters -- a curvature (``g_ref``, ``radius_ref`` or the reference field
    ``Bn0_ref``), a length (``length``, ``L_chord`` or ``L_rectangle``), and the
    angle (``angle_ref``) -- one parameter from each of two different sets, from
    which every other parameter follows. MAD-X states it with exactly two, the
    ``angle`` and the arc length ``l``, so whichever pair the PALS file used has
    to be turned into that pair here.

    Only the field-valued curvature needs the reference rigidity, the others being
    pure geometry. ``angle_value`` is the angle as a number when everything it was
    derived from is one, and ``None`` when it is an expression only MAD-X can
    evaluate. A bend that states too little for the pair to be worked out is
    reported, and comes back with whichever of the two is known.
    """
    if "BendP" not in props:
        return None
    bendP = props["BendP"]

    # The curvature, however the PALS file chose to state it.
    if "g_ref" in bendP:
        g = bendP["g_ref"].value
    elif "radius_ref" in bendP:
        radius = bendP["radius_ref"].value
        g = _madx_fold(f"1 / ({radius})", lambda r: 1 / r, radius)
    elif "Bn0_ref" in bendP:
        g = f"{bendP['Bn0_ref'].value} / {_MADX_RIGIDITY}"
    else:
        g = None

    # A length, and which of the three lengths it is: MAD-X wants the arc.
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

    # The angle and the arc length, from whichever pair of the three sets was
    # given.
    if angle is None and g is not None and length is not None:
        if len_kind == "arc":
            angle = _madx_times(g, length)
        elif len_kind == "chord":
            angle = _madx_fold(f"2 * asin(({g}) * ({length}) / 2)",
                               lambda a, b: 2 * math.asin(a * b / 2), g, length)
        else:
            angle = _madx_fold(f"asin(({g}) * ({length}))",
                               lambda a, b: math.asin(a * b), g, length)

    if arc is None and angle is not None:
        if len_kind == "chord":
            arc = _madx_fold(
                f"({angle}) * ({length}) / (2 * sin(({angle}) / 2))",
                lambda a, l: a * l / (2 * math.sin(a / 2)), angle, length)
        elif len_kind == "rect":
            arc = _madx_fold(f"({angle}) * ({length}) / sin({angle})",
                             lambda a, l: a * l / math.sin(a), angle, length)
        elif g is not None:
            arc = _madx_fold(f"({angle}) / ({g})", lambda a, b: a / b, angle, g)

    if angle is None and arc is None:
        return None
    if angle is None or arc is None:
        print(f"{name}: BendP states too little of the bend geometry for MAD-X, "
              "which needs both the angle and the arc length")
    return angle, (None if angle is None else try_float(angle)), arc


# ---------------------------------------------------------------------------
def _madx_bend_faces(bendP: YAMLNode, name: str, angle) -> List[str]:
    """The ``e1``/``e2`` attribute fragments of a bend's pole faces.

    MAD-X measures the pole-face rotations of an ``sbend`` against the sector
    geometry, which is what PALS' own ``e1`` and ``e2`` are measured against, so
    those two come straight across. PALS also has ``e1_rect`` and ``e2_rect``,
    measured against fiducial lines parallel to each other, and what separates the
    two pairs depends on the bend's ``ref_geometry``::

        ARC, CHORD        e1 = e1_rect + angle/2,   e2 = e2_rect + angle/2
        ENTRANCE_COORDS   e1 = e1_rect,             e2 = e2_rect + angle
        EXIT_COORDS       e1 = e1_rect + angle,     e2 = e2_rect

    A face given both ways is contradictory and raises an error; one given the
    rectangular way on a bend whose angle is unknown cannot be converted, and
    raises one too.
    """
    attrs: List[str] = []
    geometry = bendP["ref_geometry"].value if "ref_geometry" in bendP else "ARC"

    e1_share = 0.0 if geometry == "ENTRANCE_COORDS" else \
        1.0 if geometry == "EXIT_COORDS" else 0.5
    e2_share = 1.0 if geometry == "ENTRANCE_COORDS" else \
        0.0 if geometry == "EXIT_COORDS" else 0.5

    for face, rect, share in (("e1", "e1_rect", e1_share),
                              ("e2", "e2_rect", e2_share)):
        has_face, has_rect = face in bendP, rect in bendP
        if has_face and has_rect:
            raise ValueError(f"{name}: should not have both {face} and {rect}")
        if has_face:
            attrs.append(f"{face} = {bendP[face].value}")
        elif has_rect:
            if angle is None:
                raise ValueError(f"{name}: {rect} is measured against the bend "
                                 "angle, which is not given")
            rect_text = bendP[rect].value
            if share == 0:
                attrs.append(f"{face} = {rect_text}")
                continue
            attrs.append(f"{face} = " + _madx_fold(
                f"{rect_text} + {fmt(share)} * ({angle})",
                lambda r, a, share=share: r + share * a, rect_text, angle))
    return attrs


# ---------------------------------------------------------------------------
def _madx_aperture_attrs(apertureP: YAMLNode, name: str,
                         madx_kind: str) -> List[str]:
    """The ``apertype``/``aperture``/``aper_offset`` attribute fragments of a PALS
    ``ApertureP``.

    MAD-X states an aperture as a half width and a half height about the element's
    axis, with the offset of the aperture's centre given separately; PALS states
    the two edges, or a full width and a centre. Both forms come to the same
    half-extent and centre, which is what is written out.

    The ``shape`` decides which of the components describe the aperture: a
    ``RECTANGULAR`` or ``ELLIPTICAL`` one is bounded by its limits and ignores any
    vertices, and a ``VERTICES`` one is bounded by its vertex list and ignores any
    limits. MAD-X can only take a vertex outline from a file of its own, so a
    ``VERTICES`` aperture is reported rather than written out.

    Shape, location and the rest describe an aperture; they do not put one there.
    Writing them out for a group that sets no limit would hand MAD-X an aperture
    the PALS lattice does not have, so a group that bounds nothing is skipped
    entirely. A group that bounds one plane and not the other still has to state
    both, MAD-X's aperture values being positional; the unbounded plane is left
    wide open and reported.

    What MAD-X has no room for is reported: it puts an aperture at the entrance of
    an element and nowhere else, so ``location`` is lost, and it has no aperture at
    all on a drift.
    """
    attrs: List[str] = []
    shape = apertureP["shape"].value if "shape" in apertureP else "ELLIPTICAL"

    if shape == "VERTICES":
        print(f"{name}: MAD-X takes a vertex outline from a file of its own, "
              "which PALS does not name, so a VERTICES aperture is not translated")
        return attrs
    if shape == "CUSTOM_SHAPE":
        print(f"{name}: a CUSTOM_SHAPE aperture is defined outside PALS and has "
              "no MAD-X equivalent, not translated")
        return attrs

    has_xmin = "x_min" in apertureP
    has_xmax = "x_max" in apertureP
    has_xwidth = "x_width" in apertureP
    has_xcen = "x_center" in apertureP
    has_ymin = "y_min" in apertureP
    has_ymax = "y_max" in apertureP
    has_ywidth = "y_width" in apertureP
    has_ycen = "y_center" in apertureP

    # A RECTANGULAR or ELLIPTICAL aperture is bounded by its limits alone, so a
    # group that sets none of them bounds nothing, whatever else it says.
    if not (has_xmin or has_xmax or has_xwidth or has_xcen or
            has_ymin or has_ymax or has_ywidth or has_ycen):
        return attrs

    if madx_kind == "drift":
        print(f"{name}: MAD-X cannot put an aperture on a drift; use a "
              "collimator, not translated")
        return attrs

    def half_and_centre(plane, has_min, has_max, has_width, has_centre):
        """The half extent and the centre of one plane, which is what MAD-X
        wants, from whichever of the two PALS forms the group used."""
        if (has_min or has_max) and (has_width or has_centre):
            print(f"\n                Ignoring the {plane} aperture of element "
                  f"{name}.\n                Either {plane}_min and max should be "
                  "defined or width and center, not both.\n                ")
            return None
        if has_width:
            width = apertureP[f"{plane}_width"].as_float()
            centre = apertureP[f"{plane}_center"].as_float() if has_centre else 0.0
            return width / 2, centre
        if has_min and has_max:
            lo = apertureP[f"{plane}_min"].as_float()
            hi = apertureP[f"{plane}_max"].as_float()
            return (hi - lo) / 2, (hi + lo) / 2
        if has_min or has_max:
            print(f"{name}: only one side of the {plane} aperture is set, which "
                  "MAD-X cannot state")
            return None
        return None

    x = half_and_centre("x", has_xmin, has_xmax, has_xwidth, has_xcen)
    y = half_and_centre("y", has_ymin, has_ymax, has_ywidth, has_ycen)
    if x is None and y is None:
        print(f"{name}: ApertureP sets no limit MAD-X can state, not translated")
    else:
        # MAD-X's aperture values are positional, so a plane that is not bounded
        # still has to be given a value; one metre is well outside anything an
        # accelerator aperture bounds.
        if x is None or y is None:
            print(f"{name}: MAD-X states both aperture planes together; the "
                  "unbounded one is written out as 1 m")
        x_half, x_centre = (1.0, 0.0) if x is None else x
        y_half, y_centre = (1.0, 0.0) if y is None else y
        attrs.append(f"aperture = {{{fmt(x_half)}, {fmt(y_half)}}}")
        if not (approx(x_centre, 0) and approx(y_centre, 0)):
            attrs.append(f"aper_offset = {{{fmt(x_centre)}, {fmt(y_centre)}}}")

    for akey in apertureP.keys():
        if akey == "shape":
            shape = apertureP["shape"].value
            if shape == "ELLIPTICAL":
                attrs.append("apertype = ellipse")
            elif shape == "RECTANGULAR":
                attrs.append("apertype = rectangle")
            else:
                raise ValueError(f"{name}: aperture shape {shape} is not supported")
        elif akey == "location":
            print(f"{name}: MAD-X checks an aperture at the entrance of an "
                  "element only, so ApertureP.location is not translated")
        elif akey == "aperture_active":
            if apertureP["aperture_active"].value.lower() == "false":
                print(f"{name}: MAD-X cannot switch an aperture off; remove it "
                      "instead")
        # A RECTANGULAR or ELLIPTICAL aperture ignores any vertices, so there is
        # nothing to say about them here.
        elif akey in ("aperture_shifts_with_body", "material", "thickness"):
            print(f"{name}: ApertureP.{akey} has no MAD-X equivalent, not "
                  "translated")
    return attrs


# ---------------------------------------------------------------------------
def _make_madx_ele(ele: YAMLNode) -> Tuple[MadxEleDef, MadxAlignment]:
    """Translate a single PALS element into a :class:`MadxEleDef` and its
    :class:`MadxAlignment`.

    Dispatch on the element ``kind`` and its parameter groups (aperture, bend,
    body shift, multipoles, patch, RF, solenoid, ...) to build the MAD-X element
    type and its attribute fragments. A ``BodyShiftP`` comes back separately
    because MAD-X keeps a misalignment out of the element definition and in an
    ``EALIGN`` command of its own. Unsupported parameter groups emit a message or
    raise an error.
    """
    props = ele.child(0)
    name = _madx_check_name(props.node_key())
    ele_kind = props["kind"].value
    madx_kind = _madx_kind(ele_kind)

    attrs: List[str] = []
    notes: List[str] = []
    align: List[str] = []

    # Strip a trailing comma (and surrounding whitespace) from a fragment before
    # storing it.
    def push_attr(text):
        text = text.rstrip()
        if text.endswith(","):
            text = text[:-1].rstrip()
        if text:
            attrs.append(text)

    # The bend geometry has to be settled before anything else is: MAD-X holds a
    # bend's geometry and its field in the one `angle` attribute, and its arc
    # length may be one PALS states only by way of the geometry.
    geometry = _madx_bend_geometry(props, name) if madx_kind == "sbend" else None
    ref_angle = None if geometry is None else (geometry[0], geometry[1])
    arc_length = None if geometry is None else geometry[2]

    for key in props.keys():
        if key == "length":
            if madx_kind in _MADX_THIN_KINDS:
                if not approx(props["length"].as_float(), 0):
                    print(f"{name}: a MAD-X {madx_kind} has no length, so the "
                          "PALS length is not translated")
            # A MAD-X multipole is thin: what length it has is the fictitious one
            # used to work out the radiation it emits.
            elif madx_kind == "multipole":
                push_attr(f"lrad = {props['length'].value}")
            else:
                push_attr(f"l = {props['length'].value}")
        elif key == "ACKickerP":
            raise ValueError(f"{name}: ACKickerP not yet supported")
        elif key == "ApertureP":
            attrs.extend(_madx_aperture_attrs(props["ApertureP"], name, madx_kind))
        elif key == "BeamBeamP":
            bbP = props["BeamBeamP"]
            for bbkey in bbP.keys():
                val = bbP[bbkey].value
                if bbkey == "sigma_x":
                    push_attr(f"sigx = {val}")
                elif bbkey == "sigma_y":
                    push_attr(f"sigy = {val}")
                elif bbkey == "charge":
                    push_attr(f"charge = {val}")
                elif bbkey == "N_particle":
                    push_attr(f"npart = {val}")
                else:
                    # MAD-X models the opposite beam as a four-dimensional lens:
                    # it has no place for its length, its optics, or its energy.
                    print(f"{name}: BeamBeamP.{bbkey} has no MAD-X equivalent, "
                          "not translated")
        elif key == "BendP":
            bendP = props["BendP"]

            # MAD-X states the geometry as an angle and an arc length, however
            # PALS chose to write the same thing; `_madx_bend_geometry` settled
            # both above.
            if arc_length is not None and "length" not in props:
                push_attr(f"l = {arc_length}")
            if ref_angle is not None and ref_angle[0] is not None:
                push_attr(f"angle = {ref_angle[0]}")
            attrs.extend(_madx_bend_faces(
                bendP, name, None if ref_angle is None else ref_angle[0]))

            for bkey in bendP.keys():
                tmp = ""
                # Settled above: the geometry parameters, and the pole faces.
                if bkey in ("angle_ref", "g_ref", "radius_ref", "Bn0_ref",
                            "L_chord", "L_rectangle", "e1", "e2", "e1_rect",
                            "e2_rect"):
                    continue

                # PALS states the fringe field as an integral with the gap folded
                # in; MAD-X states the dimensionless integral and the gap apart,
                # so half of one is the whole of the other.
                elif bkey == "edge1_int":
                    val = bendP["edge1_int"].as_float()
                    if not approx(val, 0):
                        tmp = f"fint = 0.5, hgap = {fmt(2 * val)},"
                elif bkey == "edge2_int":
                    val = bendP["edge2_int"].as_float()
                    if not approx(val, 0):
                        tmp = f"fintx = 0.5, hgapx = {fmt(2 * val)},"

                elif bkey == "h1":
                    tmp = f"h1 = {bendP['h1'].value},"
                elif bkey == "h2":
                    tmp = f"h2 = {bendP['h2'].value},"
                elif bkey == "tilt_ref":
                    tmp = f"tilt = {bendP['tilt_ref'].value},"
                # Whether the actual field defaults to the reference one, which is
                # handled with the multipoles below.
                elif bkey == "Kn0_from_g_ref":
                    continue
                elif bkey == "L_sagitta":
                    raise ValueError(f"{name}: BendP.L_sagitta is an output "
                                     "parameter and is not translated")
                # A MAD-X sbend is an arc whose multipoles are vertically pure; it
                # has no equivalent of the other geometries, nor of multipoles
                # referred to something other than its own.
                elif bkey == "ref_geometry":
                    if bendP[bkey].value != "ARC":
                        print(f"{name}: BendP.ref_geometry = {bendP[bkey].value} "
                              "has no MAD-X equivalent; a MAD-X sbend is always "
                              "an arc")
                elif bkey == "multipole_geometry":
                    if bendP[bkey].value not in ("FOLLOWS_REF_GEOMETRY",
                                                 "VERTICALLY_PURE"):
                        print(f"{name}: BendP.multipole_geometry = "
                              f"{bendP[bkey].value} has no MAD-X equivalent, not "
                              "translated")
                push_attr(tmp)

            # With `Kn0_from_g_ref` false and no order-0 multipole set, the bend
            # has the geometry of the reference bend and none of its field --
            # which MAD-X, tracking through the same `angle` it builds the
            # geometry from, cannot express.
            if "Kn0_from_g_ref" in bendP and \
                    bendP["Kn0_from_g_ref"].value.lower() == "false" and \
                    not ("MagneticMultipoleP" in props and
                         any(k in ("Kn0", "Bn0", "Kn0L", "Bn0L")
                             for k in props["MagneticMultipoleP"].keys())):
                print(f"{name}: Kn0_from_g_ref is false and no order-0 multipole "
                      "is set, so the bend has no actual field; MAD-X tracks "
                      "through the same angle it bends the reference orbit with "
                      "and cannot hold the two apart")
        elif key == "BodyShiftP":
            bodyshiftP = props["BodyShiftP"]
            for bskey in bodyshiftP.keys():
                val = bodyshiftP[bskey].value
                # MAD-X's DPHI turns the element the other way round from the
                # right-hand rule the other two follow, which is where the sign
                # comes from.
                if bskey == "x_offset":
                    align.append(f"dx = {val}")
                elif bskey == "y_offset":
                    align.append(f"dy = {val}")
                elif bskey == "z_offset":
                    align.append(f"ds = {val}")
                elif bskey == "x_rot":
                    align.append(f"dphi = {_madx_scale(val, -1)}")
                elif bskey == "y_rot":
                    align.append(f"dtheta = {val}")
                elif bskey == "z_rot":
                    align.append(f"dpsi = {val}")
        elif key == "CoordinateSetP":
            raise ValueError(f"{name}: MAD-X has no element that sets the global "
                             "coordinates of the reference curve, so "
                             "CoordinateSetP cannot be translated")
        elif key == "ElectricMultipoleP":
            raise ValueError(f"{name}: ElectricMultipoleP not yet supported")
        elif key == "FloorP":
            raise ValueError(f"{name}: FloorP not yet supported")
        elif key == "ForkP":
            raise ValueError(f"{name}: ForkP not yet supported")
        elif key == "GirderP":
            raise ValueError(f"{name}: GirderP not yet supported")
        elif key == "MagneticMultipoleP":
            full = FullRepresentation()
            full.L = props["length"].as_float() if "length" in props else 1.0
            fill_multipoles(full, props["MagneticMultipoleP"], name)

            # The orders that are the element's own become its strength attributes
            # and leave `full`; what is left has to go in a multipole array, which
            # only a MAD-X multipole has.
            attrs.extend(_madx_native_strength(full, ele_kind, name, ref_angle))
            if madx_kind == "multipole":
                attrs.extend(_madx_multipole_attrs(full, name))
            elif full.magnitude:
                orders = ", ".join(str(o) for o in sorted(full.magnitude))
                print(f"{name}: a MAD-X {madx_kind} cannot carry multipoles of "
                      f"order {orders}; they need a multipole element of their "
                      "own, not translated")
        elif key == "MetaP":
            metaP = props["MetaP"]
            # MAD-X elements hold no metadata of their own, so what PALS says
            # about an element is kept as a comment above it rather than dropped.
            for mkey in metaP.keys():
                val = metaP[mkey]
                if val.is_map() or val.is_sequence():
                    print(f"{name}: MetaP.{mkey} is not a simple string, not "
                          "translated")
                    continue
                notes.append(f"{mkey}: {val.value}")
        elif key == "PatchP":
            patchP = props["PatchP"]
            offsets = ["0", "0", "0"]
            angles = ["0", "0", "0"]
            for pkey in patchP.keys():
                val = patchP[pkey].value
                if pkey == "x_offset":
                    offsets[0] = val
                elif pkey == "y_offset":
                    offsets[1] = val
                elif pkey == "z_offset":
                    offsets[2] = val
                elif pkey == "x_rot":
                    angles[0] = val
                elif pkey == "y_rot":
                    angles[1] = val
                elif pkey == "z_rot":
                    angles[2] = val
                else:
                    # A MAD-X changeref is the transformation and nothing else: it
                    # cannot be told to work out its own offsets, nor which end
                    # its length is measured from.
                    print(f"{name}: PatchP.{pkey} has no MAD-X equivalent, not "
                          "translated")
            if any(o != "0" for o in offsets):
                push_attr("patch_trans = {" + ", ".join(offsets) + "}")
            if any(a != "0" for a in angles):
                push_attr("patch_ang = {" + ", ".join(angles) + "}")
                notes.append("MAD-X applies the three changeref angles in an "
                             "order of its own; the PALS patch rotations match it "
                             "only to first order in the angles.")
        elif key == "RFP":
            rfP = props["RFP"]
            if "frequency" in rfP and "harmon" in rfP:
                raise ValueError(f"{name}: can only define `frequency` or "
                                 "`harmon` but not both")
            # MAD-X's zero phase is the zero crossing half a period away from the
            # one PALS calls the stable point above transition, whichever of the
            # three PALS is measuring from.
            zero_phase = rfP["zero_phase"].value if "zero_phase" in rfP \
                else "ACCELERATING"
            if zero_phase == "ABOVE_TRANSITION":
                lag_offset = -0.5
            elif zero_phase == "BELOW_TRANSITION":
                lag_offset = 0.0
            elif zero_phase == "ACCELERATING":
                lag_offset = -0.25
            else:
                raise ValueError(f"{name}: unknown zero_phase `{zero_phase}`")

            for rfkey in rfP.keys():
                tmp = ""
                # PALS states the frequency in Hz and the voltage in volts, MAD-X
                # in MHz and MV.
                if rfkey == "frequency":
                    tmp = f"freq = {_madx_scale(rfP['frequency'].value, 1e-6)},"
                elif rfkey == "harmon":
                    tmp = f"harmon = {rfP['harmon'].value},"
                elif rfkey == "voltage":
                    tmp = f"volt = {_madx_scale(rfP['voltage'].value, 1e-6)},"
                elif rfkey == "gradient":
                    if "L_active" in rfP:
                        length = rfP["L_active"].value
                    elif "length" in props:
                        length = props["length"].value
                    else:
                        raise ValueError(f"{name}: `gradient` needs a length to "
                                         "become the voltage MAD-X states")
                    tmp = (f"volt = {_madx_scale(rfP['gradient'].value, 1e-6)} * "
                           f"{length},")
                elif rfkey == "phase":
                    tmp = f"lag = {_madx_shift(rfP['phase'].value, lag_offset)},"
                elif rfkey == "cavity_type":
                    if rfP["cavity_type"].value == "TRAVELING_WAVE":
                        print(f"{name}: a traveling wave cavity is MAD-X's "
                              "twcavity, which only PTC tracks; translated as an "
                              "rfcavity")
                elif rfkey in ("multipass_phase", "num_cells", "L_active",
                               "dE_ref"):
                    print(f"{name}: RFP.{rfkey} has no MAD-X equivalent, not "
                          "translated")
                push_attr(tmp)
            # A phase of zero still has to be written out: MAD-X measures it from
            # somewhere else.
            if "phase" not in rfP and lag_offset != 0:
                push_attr(f"lag = {fmt(lag_offset)}")
        elif key == "SolenoidP":
            solP = props["SolenoidP"]
            if "Ksol" in solP:
                push_attr(f"ks = {solP['Ksol'].value}")
            elif "Bsol" in solP:
                push_attr(f"ks = {_madx_divide(solP['Bsol'].value, _MADX_RIGIDITY)}")
            elif solP.keys():
                print(f"{name} - unknown SolenoidP key(s): {solP.keys()}")
            # A thin MAD-X solenoid states its integrated strength instead, `ks`
            # alone doing nothing.
            if "length" in props and props["length"].as_float() == 0:
                print(f"{name}: a solenoid of zero length also needs MAD-X's ksi, "
                      "which PALS does not state")
        elif key == "TaylorP":
            raise ValueError(f"{name}: TaylorP is not yet translated to a MAD-X "
                             "matrix")
        elif key == "TrackingP":
            # Tracking parameters are program specific by design; MAD-X's have no
            # PALS spelling.
            pass
        elif key == "ReferenceChangeP":
            raise ValueError(f"{name}: MAD-X takes the reference energy from the "
                             "BEAM command and cannot change it in mid-line")

    return MadxEleDef(name, madx_kind, attrs, notes), MadxAlignment(name, align)


# ---------------------------------------------------------------------------
def _madx_control_target(cname: str, param: str, facility: YAMLNode,
                         varmap: Dict[Tuple[str, str], str]
                         ) -> Tuple[str, float, bool]:
    """Translate a controller's ``parameter`` target into a MAD-X attribute
    reference.

    Returns ``(target, factor, rigidity)`` where ``target`` is the
    ``"ele->attribute"`` MAD-X reference, the control expression must be
    multiplied by ``factor``, and ``rigidity`` says whether it must also be
    divided by the reference rigidity to hold the same physics. Neither is trivial
    in general because the element translation does not carry PALS parameters
    across unchanged: the attribute a multipole lands in may be length integrated
    where the PALS parameter was not, or the other way round, and a stated field
    has to be normalized because MAD-X has no field-valued attribute.

    A target may name its element by kind as well as by name, as
    ``{kind}::{name}``; the qualifier is checked against the element found and
    then dropped, MAD-X having one namespace for all of them.

    Targets MAD-X cannot express -- a pattern matching several elements, a ``>>``
    or ``>>>`` qualifier naming the BeamLine or Lattice an element is reached
    through, a parameter with no MAD-X attribute, or an order that only a
    multipole array could hold, MAD-X having no way to name one entry of one --
    raise an error.
    """
    if ">>" in param:
        raise ValueError(f"controller {cname}: `{param}` reaches its element "
                         "through a BeamLine or Lattice qualifier, which MAD-X, "
                         "having one namespace for the whole file, cannot express")

    parts = param.split(">")
    if len(parts) != 2:
        raise ValueError(f"controller {cname}: control parameter `{param}` is not "
                         "of the form `element>parameter`")
    slave, path = parts

    # An element may be named by its kind as well as by its name.
    kind_wanted = None
    if "::" in slave:
        qualifier = slave.split("::")
        if len(qualifier) != 2:
            raise ValueError(f"controller {cname}: `{param}` does not name a "
                             "single element kind")
        kind_wanted, slave = qualifier

    if not _NAME_RE.match(slave):
        raise ValueError(f"controller {cname}: `{param}` selects slaves by "
                         "pattern, which MAD-X cannot express")

    props = facility_props(facility, slave)
    if props is None:
        raise ValueError(f"controller {cname}: `{param}` names no element of the "
                         "facility")
    ele_kind = props["kind"].value if "kind" in props else ""
    if kind_wanted is not None and kind_wanted != ele_kind:
        raise ValueError(f"controller {cname}: `{param}` asks for a {kind_wanted} "
                         f"but {slave} is a {ele_kind}")

    # A controller may drive another controller's variable, under whatever MAD-X
    # calls it.
    if ele_kind == "Controller":
        if not _NAME_RE.match(path):
            raise ValueError(f"controller {cname}: `{param}` is not a variable of "
                             f"controller {slave}")
        if (slave, path) not in varmap:
            raise ValueError(f"controller {cname}: `{param}` names no variable of "
                             f"controller {slave}")
        return varmap[(slave, path)], 1.0, False

    if path == "length":
        return f"{slave}->l", 1.0, False

    m = _MULTIPOLE_RE.match(path)
    if m is not None:
        order = int(m.group(3))
        skew = m.group(2) == "s"
        integrated = m.group(4) == "L"
        normalized = m.group(1) == "K"
        ele_length = props["length"].as_float() if "length" in props else 1.0

        # A tilted multipole rotates normal and skew into each other, so the one
        # PALS parameter no longer maps onto the one MAD-X attribute.
        if "MagneticMultipoleP" in props and \
                f"tilt{order}" in props["MagneticMultipoleP"]:
            if not approx(props["MagneticMultipoleP"][f"tilt{order}"].as_float(), 0):
                raise ValueError(f"controller {cname}: `{param}` drives a tilted "
                                 "multipole, which has no single MAD-X attribute")

        native = _MADX_NATIVE_STRENGTH.get(ele_kind, {})
        attribute = native.get((order, skew))
        if attribute is None:
            raise ValueError(f"controller {cname}: a MAD-X {_madx_kind(ele_kind)} "
                             f"has no attribute for `{param}`; MAD-X cannot name "
                             "one entry of a multipole array")

        factor = 1.0
        if attribute in _MADX_INTEGRATED_STRENGTH and not integrated:
            factor = ele_length
        elif attribute not in _MADX_INTEGRATED_STRENGTH and integrated:
            if ele_length == 0:
                raise ValueError(f"controller {cname}: `{param}` is integrated "
                                 "over an element of zero length, which MAD-X's "
                                 f"`{attribute}` cannot state")
            factor = 1 / ele_length
        if attribute == "hkick":
            factor = -factor

        return f"{slave}->{attribute}", factor, not normalized

    raise ValueError(f"controller {cname}: control parameter `{param}` is not yet "
                     "translated to MAD-X")


# ---------------------------------------------------------------------------
def _madx_base_value(lat: MadxLattice, target: str,
                     initials: Dict[str, str]) -> str:
    """The value ``target`` already holds, as written by the element translation.

    A ``control_type: RELATIVE`` controller varies a parameter rather than setting
    it, and a MAD-X deferred assignment can only set one: ``ele->k1 := ele->k1 +
    dk`` is the circular definition MAD-X forbids. So the value being varied has to
    be written into the assignment, and the one place it is written down is the
    definition this reads it back out of.
    """
    parts = target.split("->")
    # A bare name is another controller's variable, whose value is its initial
    # setting.
    if len(parts) == 1:
        return initials.get(target, "0")

    ele_name, attribute = parts
    for ele in lat.elements:
        if ele.name != ele_name:
            continue
        for attr in ele.attrs:
            m = _ATTR_RE.match(attr)
            if m is None:
                continue
            if m.group(1).lower() == attribute.lower():
                return m.group(2)
        return "0"
    return "0"


# ---------------------------------------------------------------------------
def _make_madx_controller(ele: YAMLNode, facility: YAMLNode, lat: MadxLattice,
                          varmap: Dict[Tuple[str, str], str],
                          initials: Dict[str, str]) -> MadxController:
    """Translate a ``Controller`` element into a :class:`MadxController`.

    ``facility`` is needed to reach the slave elements: what a control expression
    must be scaled by depends on the element it drives (see
    :func:`_madx_control_target`). ``varmap`` and ``initials`` carry what each
    controller variable is called in the MAD-X file and what it starts at (see
    :func:`_madx_variable_names`). ``lat`` is needed for a ``RELATIVE``
    controller, whose slaves keep the value their element definitions already gave
    them.

    The two control types part company here. An ``ABSOLUTE`` controller sets its
    slaves outright, and a deferred assignment does the same. A ``RELATIVE`` one is
    a knob: its slaves keep the value the lattice gave them and move by however far
    the knob has been turned *from where it started*, so the assignment is the
    element's own value, plus the expression, less the expression at the variables'
    initial settings. That last term is what a Bmad ``group`` keeps track of by
    itself and MAD-X has nothing for; it is left out only when it can be shown to
    come to zero, which for a knob resting at zero it does.
    """
    props = ele.child(0)
    name = _madx_check_name(props.node_key())

    control_type = props["control_type"].value if "control_type" in props \
        else "ABSOLUTE"
    if control_type not in ("ABSOLUTE", "RELATIVE"):
        raise ValueError(f"{name}: control_type must be ABSOLUTE or RELATIVE, not "
                         f"{control_type}")

    variables: List[str] = []
    renames: Dict[str, str] = {}     # variable -> what MAD-X calls it
    starting: Dict[str, str] = {}    # variable -> where it starts
    for var, value in ctrl_variables(props):
        madx = varmap[(name, var)]
        variables.append(f"{_madx_check_name(madx)} = "
                         f"{_madx_check_expression(name, value)}")
        renames[var] = madx
        starting[var] = f"({value})"

    # A controller may carry a MetaP, which MAD-X has nowhere to put but a
    # comment.
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

    controls: List[str] = []
    if "controls" in props:
        for control in props["controls"]:
            if "parameter" not in control or "expression" not in control:
                raise ValueError(f"{name}: a controls entry needs both a "
                                 "`parameter` and an `expression`")
            target, factor, rigidity = _madx_control_target(
                name, control["parameter"].value, facility, varmap)

            pals_expr = control["expression"].value

            # The same scaling the element attribute was given, whichever form of
            # the expression it is being applied to.
            def scaled(expr, factor=factor, rigidity=rigidity):
                if not approx(factor, 1):
                    expr = f"{fmt(factor)}*({expr})"
                if rigidity:
                    expr = f"({expr}) / {_MADX_RIGIDITY}"
                return expr

            expression = scaled(_madx_check_expression(
                name, _madx_substitute(pals_expr, renames)))

            if control_type == "RELATIVE":
                expression = (f"{_madx_base_value(lat, target, initials)} + "
                              f"({expression})")
                at_start = _madx_substitute(pals_expr, starting)
                # A knob that starts where its expression comes to zero has moved
                # nothing yet, and needs no term saying so. Anything the
                # standalone evaluator cannot reach -- a user-defined constant,
                # say -- is written out and left for MAD-X.
                try:
                    zero_at_start = approx(evaluate_pals_expression(at_start), 0)
                except Exception:
                    zero_at_start = False
                if not zero_at_start:
                    expression += f" - ({scaled(at_start)})"
            controls.append(f"{target} := {expression}")

    return MadxController(name, variables, controls, notes)
