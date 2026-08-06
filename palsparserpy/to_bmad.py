"""
Translation of a PALS lattice into a Bmad lattice file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ._common import (ABRepresentation, FullRepresentation, approx,
                      ctrl_variables, facility_props, fill_multipoles, fmt,
                      name_value_pairs, tilt_rotation, value_text)
from .node import YAMLNode

__all__ = ["BmadEleDef", "BmadBeamline", "BmadController", "BmadLattice",
           "pals_to_bmad", "write_bmad_file"]

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MULTIPOLE_RE = re.compile(r"^MagneticMultipoleP\.([KB])([ns])([0-9]+)(L?)$")


@dataclass
class BmadEleDef:
    """A single Bmad element definition.

    - ``name``: the element name.
    - ``type``: the Bmad element-type name (e.g. ``Drift``, ``Quadrupole``).
    - ``attrs``: already-translated attribute fragments, each an
      ``"attribute = value"`` string.
    """
    name: str
    type: str
    attrs: List[str] = field(default_factory=list)


@dataclass
class BmadBeamline:
    """A Bmad ``line`` definition: its ``name`` and the ordered list of member
    element ``members`` (by name)."""
    name: str
    members: List[str] = field(default_factory=list)


@dataclass
class BmadController:
    """A Bmad ``overlay`` or ``group`` element: what a PALS ``Controller``
    becomes.

    - ``name``: the controller name.
    - ``type``: ``"overlay"`` for ``control_type: ABSOLUTE``, ``"group"`` for
      ``RELATIVE``. Bmad's overlay sets the slave parameter and its group adds to
      it, which is the same split PALS makes.
    - ``slaves``: the controlled parameters, each an
      ``"ele[attribute]: expression"`` string.
    - ``vars``: the variable names, in definition order.
    - ``inits``: the variables' initial values, each a ``"name = value"`` string.
    """
    name: str
    type: str
    slaves: List[str] = field(default_factory=list)
    vars: List[str] = field(default_factory=list)
    inits: List[str] = field(default_factory=list)


@dataclass
class BmadLattice:
    """An in-memory model of a Bmad lattice.

    Produced by :func:`pals_to_bmad` and serialized to a file by
    :func:`write_bmad_file`. The fields mirror the sections of a Bmad lattice
    file:

    - ``constants``: ``name = value`` definitions, in definition order.
    - ``parameters``: global ``parameter[...] = ...`` settings (species, energy,
      geometry).
    - ``beginning``: ``beginning[...] = ...`` initial Twiss, coupling and
      dispersion settings.
    - ``particle_start``: ``particle_start[...] = ...`` initial-coordinate
      settings.
    - ``elements``: element definitions (:class:`BmadEleDef`).
    - ``controllers``: ``overlay``/``group`` definitions
      (:class:`BmadController`).
    - ``beamlines``: ``line`` definitions (:class:`BmadBeamline`).
    - ``use``: branch names for the final ``use, ...`` statement.
    """
    constants: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    beginning: List[str] = field(default_factory=list)
    particle_start: List[str] = field(default_factory=list)
    elements: List[BmadEleDef] = field(default_factory=list)
    controllers: List[BmadController] = field(default_factory=list)
    beamlines: List[BmadBeamline] = field(default_factory=list)
    use: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
def pals_to_bmad(yaml: YAMLNode) -> BmadLattice:
    """Translate a parsed PALS lattice ``yaml`` (as returned by
    :func:`~palsparserpy.parse_file`) into a :class:`BmadLattice`.

    The returned structure is an in-memory model of the *Bmad* lattice (elements,
    beamlines, parameters), not the input PALS tree. Translation is a three-step
    process: parse the PALS file with ``parse_file``, build the target model with
    ``pals_to_bmad``, then emit the Bmad lattice file with
    :func:`write_bmad_file`::

        yaml = parse_file(file_dir)
        write_bmad_file(pals_to_bmad(yaml), filename)
    """
    pals = yaml["PALS"]
    facility = pals["facility"]
    lat = BmadLattice()

    # Constants and variables may be defined directly under `PALS` as well as in
    # the facility.
    for key in ("constants", "variables"):
        if key in pals:
            lat.constants.extend(_bmad_constants(pals[key]))

    n_lattices = 0
    for ele in facility:
        props = ele.child(0)
        # The compact `constants:`/`variables:` list is a facility entry in its
        # own right, with no `kind` of its own; every other entry the translation
        # looks at is a named element.
        if props.node_key() in ("constants", "variables"):
            lat.constants.extend(_bmad_constants(props))
            continue
        if "kind" not in props:
            continue
        pals_kind = props["kind"].value
        if pals_kind == "BeginningEle":
            params, beginning, particle = _ele_to_bmad_str(ele)
            lat.parameters.extend(params)
            lat.beginning.extend(beginning)
            lat.particle_start.extend(particle)
        elif pals_kind == "BeamLine":
            lat.beamlines.append(_make_bmad_line(ele))
        elif pals_kind == "Lattice":
            n_lattices += 1
            if n_lattices > 1:
                raise ValueError(
                    "\nDifferent BeamLine complexes must be translated from "
                    "separate files.\nBmad only supports one branching lattice "
                    "per file.\nConsider using different Tao universes.\n")
            _add_bmad_branches(lat, props["branches"])
        elif pals_kind == "Controller":
            lat.controllers.append(_make_bmad_controller(ele, facility))
        elif pals_kind in ("constant", "variable"):
            lat.constants.append(_bmad_constant(props, pals_kind))
        else:
            lat.elements.append(_make_bmad_ele(ele))
    return lat


# ---------------------------------------------------------------------------
def _add_bmad_branches(lat: BmadLattice, branches: YAMLNode) -> BmadLattice:
    """Translate a PALS ``Lattice``'s ``branches`` sequence into ``lat``.

    Append each branch name to ``lat.use`` and its geometry to ``lat.parameters``
    (``parameter[geometry]`` for a single branch, ``<name>[geometry]`` when
    several branches are present).
    """
    if len(branches) == 0:
        return lat
    single = len(branches) == 1
    for bl in branches:
        if bl.is_scalar():
            name = bl.value
            periodic = "open"
        elif bl.is_map():
            bl_props = bl.child(0)
            name = bl_props.node_key()
            # `periodic` is a YAML node, not a string, so it has to be rendered
            # before it is compared.
            periodic = "closed" if ("periodic" in bl_props and
                                    bl_props["periodic"].value.lower() == "true") \
                else "open"
        elif bl.is_sequence():
            raise ValueError("Expanding lattices is not done during PALS>Bmad "
                             "translation")
        else:
            raise ValueError(f"This object is neither a scalar, map, nor "
                             f"sequence: {bl!r}")
        lat.use.append(name)
        if single:
            lat.parameters.append(f"parameter[geometry] = {periodic}")
        else:
            lat.parameters.append(f"{name}[geometry] = {periodic}")
    return lat


# ---------------------------------------------------------------------------
def write_bmad_file(lat: BmadLattice, filename) -> None:
    """Serialize the :class:`BmadLattice` ``lat`` to ``filename`` as a Bmad
    lattice file.

    Write the constant and variable definitions, the global, beginning-Twiss and
    particle-start parameters, the element definitions, the ``overlay``/``group``
    definitions, the beamline (``line``) definitions, and the branch (``use``)
    statement, each in its own labelled section. The constants come first
    because Bmad, unlike PALS, resolves a name against what the file has defined
    *above* the point of use.
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
                out.write(constant + "\n")

        section("Lattice parameters")
        for parameter in lat.parameters:
            out.write(parameter + "\n")
        if lat.beginning:
            out.write("\n")
            for parameter in lat.beginning:
                out.write(parameter + "\n")
        if lat.particle_start:
            out.write("\n")
            for parameter in lat.particle_start:
                out.write(parameter + "\n")

        section("Element definitions")
        for ele in lat.elements:
            out.write(_format_bmad_ele(ele) + "\n")

        # A controller spans several lines, so the definitions are set apart from
        # one another.
        if lat.controllers:
            section("Controller definitions")
            out.write("\n\n".join(_format_bmad_controller(c)
                                  for c in lat.controllers) + "\n")

        section("Beamline definitions")
        if lat.beamlines:
            out.write("\n\n".join(_format_bmad_line(b) for b in lat.beamlines) + "\n")

        section("Branch structure")
        if lat.use:
            out.write("use, " + ", ".join(lat.use))


# ---------------------------------------------------------------------------
def _format_bmad_ele(ele: BmadEleDef) -> str:
    """Render a :class:`BmadEleDef` as a ``name: type, attr = val, ...`` Bmad
    element definition, with each attribute on its own tab-indented continuation
    line."""
    text = f"{ele.name}: {ele.type}"
    for attr in ele.attrs:
        text += f",\n\t{attr}"
    return text


# ---------------------------------------------------------------------------
def _format_bmad_controller(ctrl: BmadController) -> str:
    """Render a :class:`BmadController` as a
    ``name: overlay = {...}, var = {...}, v = init`` definition.

    A control expression is a line's worth of text on its own, so each slave, the
    variable list and each variable's initial value get a tab-indented
    continuation line of their own. Every broken line ends in the comma that
    continues it.
    """
    text = f"{ctrl.name}: {ctrl.type} = {{" + ",\n\t\t".join(ctrl.slaves) + "}"
    if ctrl.vars:
        text += ",\n\tvar = {" + ", ".join(ctrl.vars) + "}"
    for init in ctrl.inits:
        text += ",\n\t" + init
    return text


# ---------------------------------------------------------------------------
def _format_bmad_line(bl: BmadBeamline) -> str:
    """Render a :class:`BmadBeamline` as a Bmad ``name: line = (...)``
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
    return f"{bl.name}: line = ({wrapped})"


# ---------------------------------------------------------------------------
def _ele_to_bmad_str(ele: YAMLNode) -> Tuple[List[str], List[str], List[str]]:
    """Translate a ``BeginningEle`` element into Bmad global-parameter settings.

    Returns ``(params, beginning, particle_start)`` where ``params`` holds
    ``parameter[...]`` strings from the element's ``ReferenceP`` (species and
    energy), ``beginning`` holds ``beginning[...]`` strings from its ``TwissP``
    (initial Twiss, coupling and dispersion), and ``particle_start`` holds
    ``particle_start[...]`` strings from its ``ParticleP`` (initial phase-space
    coordinates and spin).
    """
    props = ele.child(0)
    params: List[str] = []
    beginning: List[str] = []
    particle: List[str] = []
    for key in props.keys():
        if key == "TwissP":
            twissP = props["TwissP"]
            for k in twissP.keys():
                # PALS and Bmad give these the same names, bar the coupling
                # matrix's underscore.
                attribute = "cmat_" + k[4:] if k.startswith("cmat") else k
                beginning.append(f"beginning[{attribute}] = {twissP[k].value}")
        elif key == "ReferenceP":
            referenceP = props["ReferenceP"]
            for k in referenceP.keys():
                if k == "species_ref":
                    params.append(f"parameter[particle] = {referenceP[k].value}")
                elif k == "pc_ref":
                    params.append(f"parameter[p0c] = {referenceP[k].value}")
                elif k == "E_tot_ref":
                    params.append(f"parameter[E_tot] = {referenceP[k].value}")
                elif k in ("time_ref", "location"):
                    print(f"{k} not supported yet")
        elif key == "ParticleP":
            particleP = props["ParticleP"]
            for k in particleP.keys():
                val = particleP[k].value
                if k in ("x", "y", "z", "px", "py", "pz",
                         "spin_x", "spin_y", "spin_z"):
                    particle.append(f"particle_start[{k}] = {val}")
    return params, beginning, particle


# ---------------------------------------------------------------------------
def _make_bmad_line(ele: YAMLNode) -> BmadBeamline:
    """Translate a ``BeamLine`` element into a :class:`BmadBeamline`.

    Collect the member element names (dropping the leading reference entry,
    ``line[0]``, by design) into the returned beamline.
    """
    props = ele.child(0)
    name = props.node_key()
    line = props["line"]
    members: List[str] = []
    for i in range(1, len(line)):
        line_ele = line.child(i)
        if line_ele.is_scalar():
            members.append(line_ele.value)
        elif line_ele.is_map() or line_ele.is_sequence():
            members.append(line_ele.child(0).node_key())
        else:
            raise ValueError(f"BeamLine {name} element {i + 1} is not scalar or "
                             "sequence or map")
    return BmadBeamline(name, members)


# ---------------------------------------------------------------------------
def _bmad_constants(node: YAMLNode) -> List[str]:
    """Translate a compact-form ``constants:``/``variables:`` list into Bmad
    ``name = value`` definitions.

    Bmad draws no distinction between the two: both become a named value the rest
    of the lattice file may use in an expression, so both lists translate the
    same way.
    """
    return [f"{name} = {value}" for name, value in name_value_pairs(node)]


# ---------------------------------------------------------------------------
def _bmad_constant(props: YAMLNode, pals_kind: str) -> str:
    """Translate a full-form (``kind: constant``, ``kind: variable``) definition
    into a Bmad ``name = value`` definition.

    A definition whose ``value`` is a structure rather than a single value has no
    Bmad equivalent and raises an error; one with no ``value`` at all takes PALS'
    default of zero.
    """
    name = props.node_key()
    if "value" not in props:
        return f"{name} = 0"
    value = props["value"]
    if value.is_map() or value.is_sequence():
        raise ValueError(f"{name}: the `value` of a `{pals_kind}` is not a "
                         "single value")
    return f"{name} = {value_text(value)}"


# ---------------------------------------------------------------------------
def _bmad_control_target(cname: str, param: str,
                         facility: YAMLNode) -> Tuple[str, float, float]:
    """Translate a controller's ``parameter`` target into a Bmad slave reference.

    Returns ``(target, factor, offset)`` where ``target`` is the
    ``"ele[attribute]"`` Bmad reference and the control expression must be
    multiplied by ``factor`` and have ``offset`` subtracted from it to hold the
    same physics. Neither is trivial in general because the element translation
    does not carry PALS parameters across unchanged: a multipole that is not the
    element's own becomes Bmad's normalized *integrated* strength ``An``/``Bn``,
    so a controller driving a non-integrated one has to pick up the slave's
    length (and the ``1/n!`` of the multipole convention) here. A multipole that
    *is* the element's own strength becomes ``K1``, ``K2``, ``K3`` or a bend's
    ``DG`` (see :func:`_native_strength`), which is not length integrated, so
    there an integrated PALS parameter is the one that needs the length; and
    ``DG``, alone among them, is measured from the reference bend rather than
    from zero, which is the ``offset`` (see :func:`_bend_reference`). A bend's
    added ``K1`` and ``K2`` are used only when that order has no skew part, so a
    controller driving one of those has to look at the skew component to know
    which attribute it will find.

    A target may name its element by kind as well as by name, as
    ``{kind}::{name}``; the qualifier is checked against the element found and
    then dropped, the Bmad file naming each element once.

    Targets Bmad cannot express -- a pattern matching several elements, a ``>>``
    or ``>>>`` qualifier naming the BeamLine or Lattice an element is reached
    through, or a parameter with no Bmad attribute -- raise an error.
    """
    # Bmad has a `branch>>ele` qualifier of its own, but a PALS BeamLine is not a
    # Bmad branch -- it may be spliced into a longer line -- so the two do not
    # correspond.
    if ">>" in param:
        raise ValueError(f"controller {cname}: `{param}` reaches its element "
                         "through a BeamLine or Lattice qualifier, which has no "
                         "Bmad equivalent")

    parts = param.split(">")
    if len(parts) != 2:
        raise ValueError(f"controller {cname}: control parameter `{param}` is "
                         "not of the form `element>parameter`")
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
                         "pattern, which a Bmad overlay cannot express")

    props = facility_props(facility, slave)
    if props is None:
        raise ValueError(f"controller {cname}: `{param}` names no element of the "
                         "facility")
    if kind_wanted is not None:
        ele_kind = props["kind"].value if "kind" in props else ""
        if kind_wanted != ele_kind:
            raise ValueError(f"controller {cname}: `{param}` asks for a "
                             f"{kind_wanted} but {slave} is a {ele_kind}")

    # A controller may drive another controller's variable, and so may a Bmad
    # overlay.
    if "kind" in props and props["kind"].value == "Controller":
        if not _NAME_RE.match(path):
            raise ValueError(f"controller {cname}: `{param}` is not a variable "
                             f"of controller {slave}")
        return f"{slave}[{path}]", 1.0, 0.0

    if path == "length":
        return f"{slave}[L]", 1.0, 0.0

    m = _MULTIPOLE_RE.match(path)
    if m is not None:
        order = int(m.group(3))
        skew = m.group(2) == "s"
        integrated = m.group(4) == "L"
        ele_length = props["length"].as_float() if "length" in props else 1.0
        # A tilted multipole rotates normal and skew into each other, so the one
        # PALS parameter no longer maps onto the one Bmad attribute.
        if "MagneticMultipoleP" in props and \
                f"tilt{order}" in props["MagneticMultipoleP"]:
            if not approx(props["MagneticMultipoleP"][f"tilt{order}"].as_float(), 0):
                raise ValueError(f"controller {cname}: `{param}` drives a tilted "
                                 "multipole, which has no single Bmad attribute")

        # The normal component of the element's own multipole is its strength
        # attribute, which the element translation writes without the length or
        # the factorial.
        ele_kind = props["kind"].value if "kind" in props else ""
        native = _NATIVE_STRENGTH.get(ele_kind, {}).get(order)
        if native is not None and not skew and not (integrated and ele_length == 0) \
                and not (ele_kind == "Bend" and order > 0 and _has_skew(props, order)):
            offset = _bend_reference(props, slave, m.group(1) == "K") \
                if ele_kind == "Bend" and order == 0 else 0.0
            attribute = native[0] if m.group(1) == "K" else native[1]
            return (f"{slave}[{attribute}]",
                    1 / ele_length if integrated else 1.0, offset)

        fact = math.factorial(order)
        return (f"{slave}[{'A' if skew else 'B'}{order}]",
                (1.0 if integrated else ele_length) / fact, 0.0)

    raise ValueError(f"controller {cname}: control parameter `{param}` is not "
                     "yet translated to Bmad")


# ---------------------------------------------------------------------------
def _make_bmad_controller(ele: YAMLNode, facility: YAMLNode) -> BmadController:
    """Translate a ``Controller`` element into a :class:`BmadController`.

    ``facility`` is needed to reach the slave elements: what a control expression
    must be scaled by depends on the element it drives (see
    :func:`_bmad_control_target`).
    """
    props = ele.child(0)
    name = props.node_key()

    control_type = props["control_type"].value if "control_type" in props \
        else "ABSOLUTE"
    if control_type == "ABSOLUTE":
        bmad_type = "overlay"
    elif control_type == "RELATIVE":
        bmad_type = "group"
    else:
        raise ValueError(f"{name}: control_type must be ABSOLUTE or RELATIVE, "
                         f"not {control_type}")

    variables: List[str] = []
    inits: List[str] = []
    for var, value in ctrl_variables(props):
        variables.append(var)
        inits.append(f"{var} = {value}")

    slaves: List[str] = []
    if "controls" in props:
        for control in props["controls"]:
            if "parameter" not in control or "expression" not in control:
                raise ValueError(f"{name}: a controls entry needs both a "
                                 "`parameter` and an `expression`")
            target, factor, offset = _bmad_control_target(
                name, control["parameter"].value, facility)
            expression = control["expression"].value
            if not approx(factor, 1):
                expression = f"{fmt(factor)}*({expression})"
            # An `overlay` sets the attribute, so an attribute Bmad measures from
            # something other than zero needs that something taken off. A `group`
            # varies the attribute instead, and what it is measured from is the
            # same before and after, so there the offset cancels.
            if bmad_type == "overlay" and not approx(offset, 0):
                expression = f"{expression} - ({fmt(offset)})"
            slaves.append(f"{target}: {expression}")

    return BmadController(name, bmad_type, slaves, variables, inits)


# ---------------------------------------------------------------------------
def _bmad_kind(ele_kind: str) -> str:
    """The Bmad element-type name for the PALS ``ele_kind``.

    Kinds with no Bmad equivalent (e.g. ``UnionEle``, ``Feedback``) raise an
    error.
    """
    # Magnets and RF Cavities
    #
    # PALS has the one `Bend`, whose reference geometry is a sector; the pole
    # face rotations that make a bend rectangular are parameters of it
    # (`e1_rect`, `e2_rect`), not a second kind. Bmad splits the two, so `Bend`
    # maps to Bmad's sector bend and Bmad's `RBend` has no PALS kind to map from.
    renamed = {"ACKicker": "AC_Kicker", "Bend": "SBend",
               "CrabCavity": "Crab_Cavity", "Multipole": "AB_Multipole",
               # Bookkeeping Elements
               "BeginningEle": "Beginning_Ele", "FloorShift": "Floor_Shift",
               "Placeholder": "Marker", "ReferenceChange": "Patch"}
    unchanged = {
        # Magnets and RF Cavities
        "Drift", "Kicker", "Octupole", "Quadrupole", "RFCavity", "Sextupole",
        "Solenoid", "Wiggler",
        # Beam and Plasma Elements
        "BeamBeam",
        # Sources and Collimation
        "Converter", "Foil", "Mask",
        # Instrumentation and Diagnostics
        "Instrument",
        # Map Elements
        "Match", "Taylor",
        # Bookkeeping Elements
        "Fiducial", "Fork", "Marker", "Patch",
        # Structural and Grouping Elements
        "Girder"}

    if ele_kind in renamed:
        return renamed[ele_kind]
    if ele_kind in unchanged:
        return ele_kind
    if ele_kind == "EGun":
        return "E_Gun"
    # Structural and Grouping Elements
    if ele_kind == "UnionEle":
        raise ValueError("No UnionEle in Bmad")
    # External Circuits
    if ele_kind == "Feedback":
        raise ValueError("No Feedback elements in Bmad")
    raise ValueError(f"Element kind {ele_kind} is not translated to Bmad")


# ---------------------------------------------------------------------------
def _kind_map(ele_kind: str):
    """The multipole representation type used for a given element kind.

    Elements that carry field multipoles map to
    :class:`~palsparserpy._common.ABRepresentation`; kinds that have no multipole
    attributes, or are unrecognized, raise an error.
    """
    if ele_kind in ("Bend", "Quadrupole", "Sextupole", "Octupole", "Multipole",
                    "Solenoid", "Kicker", "Wiggler", "RFCavity", "CrabCavity"):
        return ABRepresentation
    if ele_kind in ("EGun", "Mask", "Converter", "Instrument"):
        raise ValueError(f"Bmad {ele_kind} has no multipole attributes")
    raise ValueError(f"Element type {ele_kind} is unrecognized")


# ---------------------------------------------------------------------------
#: The multipole orders an element kind holds as its own strength, and the Bmad
#: attributes that hold them.
#:
#: Each entry maps a PALS element kind to a map from multipole order to
#: ``(normalized_attribute, field_attribute)``: a quadrupole's order-1 field is
#: Bmad's ``K1`` (or ``B1_GRADIENT``), not a ``B1`` multipole. A bend carries a
#: quadrupole and a sextupole component of its own as well as its bending field,
#: so it has three. A bend's order-0 field is ``DG`` (or ``DB_FIELD``), which
#: Bmad measures from the reference bend rather than from zero, so that one is
#: written with an offset (see :func:`_bend_reference`). Kinds whose strength does
#: not line up one-to-one with a PALS multipole -- a kicker's ``HKICK``, a
#: solenoid's ``KS`` -- are deliberately absent, and keep the multipole form.
#:
#: A bend has no attribute above order 2, so its higher multipoles keep the
#: ``An``/``Bn`` form.
_NATIVE_STRENGTH: Dict[str, Dict[int, Tuple[str, str]]] = {
    "Bend": {0: ("DG", "DB_FIELD"), 1: ("K1", "B1_GRADIENT"),
             2: ("K2", "B2_GRADIENT")},
    "Quadrupole": {1: ("K1", "B1_GRADIENT")},
    "Sextupole": {2: ("K2", "B2_GRADIENT")},
    "Octupole": {3: ("K3", "B3_GRADIENT")},
}


# ---------------------------------------------------------------------------
def _native_strength(full: FullRepresentation, ele_kind: str,
                     offset: float = 0.0) -> List[str]:
    """Take the multipoles that are an element's own strength out of ``full`` and
    return their Bmad attribute fragments.

    The strength of a Bmad quadrupole is its ``K1``, so that is where a PALS
    ``Kn1`` belongs: leaving it in a ``B1`` multipole would give an element whose
    nominal strength is zero and whose field comes entirely from a multipole slot.
    A bend has a ``K1`` and a ``K2`` of its own on top of its bending field, so a
    bend's ``Kn1`` and ``Kn2`` land there in the same way. A native attribute is
    not length integrated, so an integrated PALS value is divided by the element
    length; a tilted one is rotated first, and whatever lands in the skew part is
    left behind in ``full`` as an ordinary multipole. That rotation is why the
    tilt does not simply become the Bmad element ``tilt``, which is already spoken
    for by ``BodyShiftP.z_rot``.

    ``offset`` is subtracted from the order-0 value written, for the one native
    attribute Bmad does not measure from zero: a bend's ``DG`` is the departure of
    the field from the reference bend (see :func:`_bend_reference`).

    An order is left in ``full`` untouched, to be written in the multipole form,
    when it has no native attribute for this kind; when an integrated multipole
    sits on a zero-length element, which no non-integrated attribute can express;
    and, for a bend's added ``K1`` and ``K2``, when the field has a skew part. As
    elsewhere in this conversion, an element with no ``length`` is taken to be one
    metre long.
    """
    if ele_kind not in _NATIVE_STRENGTH:
        return []
    native = _NATIVE_STRENGTH[ele_kind]

    attrs: List[str] = []
    for order in sorted(full.magnitude):
        if order not in native:
            continue

        length = full.L if full.integrated[order] else 1.0
        if length == 0:
            continue
        strength = (complex(*full.magnitude[order])
                    * tilt_rotation(order, full.tilt.get(order, 0.0)) / length)

        # A bend's `K1` and `K2` are components added to a field the element
        # already has, not the strength that makes it the element it is, and Bmad
        # has no skew attribute to go with them. So an order with a skew part is
        # left whole in the `An`/`Bn` form, which holds both parts in the one
        # convention, rather than split between a native attribute and a
        # multipole slot.
        if ele_kind == "Bend" and order > 0 and not approx(strength.imag, 0):
            continue

        # What is left is a skew multipole of the same order, in the same units
        # the native attribute was just read in: no longer integrated, and with
        # the tilt already applied.
        full.magnitude[order] = [0.0, strength.imag]
        full.integrated[order] = False
        full.tilt.pop(order, None)

        # Order 0 is compared against the offset rather than against zero: a bend
        # whose field is the reference bend has no departure from it to write,
        # and a PALS file states the two to the same handful of digits, which is
        # not enough to subtract exactly. For every other order -- and for an
        # offset of zero -- this is the same exact test as before.
        off = offset if order == 0 else 0.0
        if approx(strength.real, off):
            continue
        attribute = native[order][0] if full.normalized[order] else native[order][1]
        attrs.append(f"{attribute} = {fmt(strength.real - off)}")
    return attrs


# ---------------------------------------------------------------------------
def _bend_reference(props: YAMLNode, name: str, normalized: bool) -> float:
    """The reference bend strength a ``Bend``'s order-0 normal multipole is
    measured against.

    PALS states the field of a bend outright, as ``MagneticMultipoleP.Kn0`` (or
    ``Bn0``). Bmad states it as ``DG`` (or ``DB_FIELD``), the departure of the
    field from the reference bend the element geometry is built on, so the
    reference has to come off the PALS value: ``dg = Kn0 - g_ref``. The reference
    is ``BendP.g_ref`` -- or the curvature ``1/radius_ref`` of that same bend --
    for a ``normalized`` multipole, and ``BendP.Bn0_ref`` for an unnormalized one.
    A bend with no reference of its own does not bend, and the offset is zero.

    The two flavors cannot be mixed: going from one to the other takes the
    reference momentum, which belongs to the branch and not to the element, so a
    normalized field measured against an unnormalized reference (or the reverse)
    raises an error.
    """
    if "BendP" not in props:
        return 0.0
    bendP = props["BendP"]
    has_g = "g_ref" in bendP or "radius_ref" in bendP
    has_B = "Bn0_ref" in bendP

    if normalized:
        if has_B and not has_g:
            raise ValueError(f"{name}: the bend field (Kn0) and its reference "
                             "bend (Bn0_ref) are not both normalized")
        if "g_ref" in bendP:
            return bendP["g_ref"].as_float()
        if "radius_ref" in bendP:
            return 1 / bendP["radius_ref"].as_float()
    else:
        if has_g and not has_B:
            raise ValueError(f"{name}: the bend field (Bn0) and its reference "
                             "bend (g_ref) are not both normalized")
        if has_B:
            return bendP["Bn0_ref"].as_float()
    return 0.0


# ---------------------------------------------------------------------------
def _has_skew(props: YAMLNode, order: int) -> bool:
    """Whether the element has a nonzero skew multipole of the given ``order``.

    Which Bmad attribute an order lands in can depend on it: a bend's ``K1`` and
    ``K2`` are used only for a field with no skew part (see
    :func:`_native_strength`), so a controller driving one has to ask. Any of the
    four spellings of the component -- normalized or not, integrated or not --
    counts.
    """
    if "MagneticMultipoleP" not in props:
        return False
    mmP = props["MagneticMultipoleP"]
    for key in (f"Ks{order}", f"Ks{order}L", f"Bs{order}", f"Bs{order}L"):
        if key in mmP and not approx(mmP[key].as_float(), 0):
            return True
    return False


# ---------------------------------------------------------------------------
def _mp_key(rep: ABRepresentation) -> List[str]:
    """The Bmad attribute fragments for A/B field-integral multipoles.

    Emits an ``An = ...`` / ``Bn = ...`` fragment for each nonzero coefficient in
    ``rep``.
    """
    out: List[str] = []
    for order in sorted(rep.A):
        if not approx(rep.A[order], 0):
            out.append(f"A{order} = {fmt(rep.A[order])}")
        if not approx(rep.B[order], 0):
            out.append(f"B{order} = {fmt(rep.B[order])}")
    return out


# ---------------------------------------------------------------------------
def _bmad_quote(text: str) -> Optional[str]:
    """``text`` as a quoted Bmad string constant, or ``None`` if it cannot be
    quoted.

    Bmad accepts either quote character but has no escape for one inside a string,
    so a ``text`` holding a double quote is wrapped in single quotes. One holding
    both is unrepresentable.
    """
    if '"' not in text:
        return f'"{text}"'
    if "'" in text:
        return None
    return f"'{text}'"


# ---------------------------------------------------------------------------
def _make_bmad_ele(ele: YAMLNode) -> BmadEleDef:
    """Translate a single PALS element into a :class:`BmadEleDef`.

    Dispatch on the element ``kind`` and its parameter groups (aperture, bend,
    body shift, multipoles, patch, RF, solenoid, reference change, ...) to build
    the Bmad element type and its attribute fragments. Unsupported parameter
    groups emit a message or raise an error.
    """
    props = ele.child(0)
    name = props.node_key()
    ele_kind = props["kind"].value
    ele_kind_bmad = _bmad_kind(ele_kind)

    attrs: List[str] = []

    # Strip a trailing comma (and surrounding whitespace) from a fragment before
    # storing it.
    def push_attr(text):
        text = text.rstrip()
        if text.endswith(","):
            text = text[:-1].rstrip()
        if text:
            attrs.append(text)

    for key in props.keys():
        if key == "length":
            push_attr(f"L = {props['length'].value}")
        elif key == "ACKickerP":
            raise ValueError("ACKickerP not yet supported")
        elif key == "ApertureP":
            apertureP = props["ApertureP"]

            has_xmin = "x_min" in apertureP
            has_xmax = "x_max" in apertureP
            has_xwidth = "x_width" in apertureP
            has_xcen = "x_center" in apertureP
            has_ymin = "y_min" in apertureP
            has_ymax = "y_max" in apertureP
            has_ywidth = "y_width" in apertureP
            has_ycen = "y_center" in apertureP

            # Shape, location and the rest describe an aperture; they do not put
            # one there. Writing them out for a group that sets no limit would
            # hand Bmad an aperture the PALS lattice does not have. A `vertices`
            # aperture is bounded too, by its vertex list.
            has_xaperture = has_xmin or has_xmax or has_xwidth or has_xcen
            has_yaperture = has_ymin or has_ymax or has_ywidth or has_ycen
            if not (has_xaperture or has_yaperture or "vertices" in apertureP):
                continue

            tmp = ""
            if (has_xmin or has_xmax) and (has_xwidth or has_xcen):
                print(f"\n                Ignoring ApertureP of element {name}."
                      "\n                Either x_min and max should be defined "
                      "or width and center, not both.\n                ")
            # Bmad states a limit as a distance from the axis, not as a
            # coordinate: it loses a particle at `x < -x1_limit`, so the low-side
            # limit is the negated PALS `x_min`.
            elif has_xwidth:
                width = apertureP["x_width"].as_float()
                center = apertureP["x_center"].as_float() if has_xcen else 0.0
                tmp += f"x1_limit = {fmt(width / 2 - center)}, "
                tmp += f"x2_limit = {fmt(width / 2 + center)},"
            elif has_xmin and has_xmax:
                tmp += f"x1_limit = {fmt(-apertureP['x_min'].as_float())}, "
                tmp += f"x2_limit = {apertureP['x_max'].value},"
            push_attr(tmp)

            tmp = ""
            if (has_ymin or has_ymax) and (has_ywidth or has_ycen):
                print(f"\n                Ignoring ApertureP of element {name}."
                      "\n                Either y_min and max should be defined "
                      "or width and center, not both.\n                ")
            elif has_ywidth:
                width = apertureP["y_width"].as_float()
                center = apertureP["y_center"].as_float() if has_ycen else 0.0
                tmp += f"y1_limit = {fmt(width / 2 - center)}, "
                tmp += f"y2_limit = {fmt(width / 2 + center)},"
            elif has_ymin and has_ymax:
                tmp += f"y1_limit = {fmt(-apertureP['y_min'].as_float())}, "
                tmp += f"y2_limit = {apertureP['y_max'].value},"
            push_attr(tmp)

            for akey in apertureP.keys():
                tmp = ""
                if akey == "shape":
                    shape = apertureP["shape"].value
                    if shape == "ELLIPTICAL":
                        tmp += "aperture_type = elliptical,"
                    elif shape == "RECTANGULAR":
                        tmp += "aperture_type = rectangular,"
                    else:
                        raise ValueError(f"shape {shape} is not supported")
                elif akey == "location":
                    location = apertureP["location"].value
                    if location == "ENTRANCE_END":
                        tmp += "aperture_at = entrance_end,"
                    elif location == "EXIT_END":
                        tmp += "aperture_at = exit_end,"
                    elif location in ("BOTH_ENDS", "CENTER"):
                        if location == "CENTER":
                            print("location=CENTER not supported, set to "
                                  "aperture_at=both_ends")
                        tmp += "aperture_at = both_ends,"
                    elif location == "EVERYWHERE":
                        tmp += "aperture_at = continuous,"
                    elif location == "NOWHERE":
                        tmp += "aperture_at = no_aperture,"
                elif akey == "aperture_shifts_with_body":
                    shifts = apertureP["aperture_shifts_with_body"].value.lower()
                    tmp += f"offset_moves_aperture = {'T' if shifts == 'true' else 'F'},"
                elif akey == "aperture_active":
                    active = apertureP["aperture_active"].value.lower()
                    tmp += f"is_on = {'T' if active == 'true' else 'F'},"
                elif akey == "vertices":
                    print("vertices not yet supported")
                elif akey == "material":
                    print("material not yet supported")
                elif akey == "thickness":
                    print("thickness not yet supported")
                push_attr(tmp)
        elif key == "BeamBeamP":
            raise ValueError(f"{name}: BeamBeamP not translated yet")
        elif key == "BendP":
            bendP = props["BendP"]
            has_e1 = "e1" in bendP
            has_e1_rect = "e1_rect" in bendP
            has_e2 = "e2" in bendP
            has_e2_rect = "e2_rect" in bendP
            if (has_e1 or has_e2) and (has_e1_rect or has_e2_rect):
                raise ValueError(f"{name}: should not have both e1 and e1_rect, "
                                 "nor both e2 and e2_rect")

            for bkey in bendP.keys():
                tmp = ""
                if bkey == "radius_ref":
                    tmp += f"rho = {bendP['radius_ref'].value},"
                elif bkey == "Bn0_ref":
                    tmp += f"B_field = {bendP['Bn0_ref'].value},"

                elif bkey in ("e1", "e1_rect"):
                    tmp += f"e1 = {bendP['e1'].value},"
                elif bkey in ("e2", "e2_rect"):
                    tmp += f"e2 = {bendP['e2'].value},"

                elif bkey == "edge1_int":
                    val = bendP["edge1_int"].as_float()
                    if not approx(val, 0):
                        tmp += "fint = 0.5, "
                        tmp += f"hgap = {fmt(2 * val)},"
                elif bkey == "edge2_int":
                    val = bendP["edge2_int"].as_float()
                    if not approx(val, 0):
                        tmp += "fintx = 0.5, "
                        tmp += f"hgapx = {fmt(2 * val)},"
                elif bkey == "g_ref":
                    tmp += f"g = {bendP['g_ref'].value},"
                elif bkey == "h1":
                    tmp += f"h1 = {bendP['h1'].value},"
                elif bkey == "h2":
                    tmp += f"h2 = {bendP['h2'].value},"
                elif bkey == "L_chord":
                    raise ValueError(f"{name}: L_chord is a derived quantity for "
                                     "SBend elements")
                elif bkey == "L_sagitta":
                    raise ValueError(f"{name}: L_sagitta is a derived quantity "
                                     "for SBend/RBend elements")
                elif bkey == "tilt_ref":
                    tmp += f"ref_tilt = {bendP['tilt_ref'].value},"
                push_attr(tmp)
        elif key == "BodyShiftP":
            bodyshiftP = props["BodyShiftP"]
            for bskey in bodyshiftP.keys():
                tmp = ""
                if bskey == "x_offset":
                    tmp = f"x_offset = {bodyshiftP['x_offset'].value},"
                elif bskey == "y_offset":
                    tmp = f"y_offset = {bodyshiftP['y_offset'].value},"
                elif bskey == "z_offset":
                    tmp = f"z_offset = {bodyshiftP['z_offset'].value},"
                elif bskey == "x_rot":
                    tmp = f"y_pitch = {fmt(-bodyshiftP['x_rot'].as_float())},"
                elif bskey == "y_rot":
                    tmp = f"x_pitch = {bodyshiftP['y_rot'].value},"
                elif bskey == "z_rot":
                    tmp = f"tilt = {bodyshiftP['z_rot'].value},"
                push_attr(tmp)
        elif key == "ElectricMultipoleP":
            raise ValueError("ElectricMultipoleP not yet supported")
        elif key == "FloorP":
            raise ValueError("FloorP not yet supported")
        elif key == "FloorShiftP":
            raise ValueError("FloorShiftP not yet supported")
        elif key == "ForkP":
            raise ValueError("ForkP not yet supported")
        elif key == "GirderP":
            raise ValueError("GirderP not yet supported")
        elif key == "MagneticMultipoleP":
            mmP = props["MagneticMultipoleP"]

            full = FullRepresentation()
            full.L = props["length"].as_float() if "length" in props else 1.0

            fill_multipoles(full, mmP, name)

            if all(full.normalized.values()):
                pass                            # push_attr("field_master = F") is the default
            elif not any(full.normalized.values()) and ele_kind != "RFCavity":
                push_attr("field_master = T")
            else:
                raise ValueError(f"{name}: Multipoles of one element must be all "
                                 "normalized or all unnormalized.")

            # The multipoles that are the element's own strength become Bmad
            # strength attributes; the rest stay multipoles. A bend's order-0
            # attribute is `DG`, which Bmad measures from the reference bend
            # rather than from zero.
            offset = _bend_reference(props, name, full.normalized[0]) \
                if ele_kind == "Bend" and 0 in full.normalized else 0.0
            attrs.extend(_native_strength(full, ele_kind, offset))

            # Pick the element-specific representation, then down-convert.
            rep = _kind_map(ele_kind)(full)
            mp_attrs = _mp_key(rep)
            attrs.extend(mp_attrs)

            # Bmad reads An/Bn on an ordinary element as fractions of that
            # element's own strength, scaling them by K1*L for a quadrupole, K2*L
            # for a sextupole, and so on. PALS multipoles are the field integrals
            # themselves, so the scaling has to be turned off. The kinds that hold
            # nothing but multipoles do not scale, and have no such attribute to
            # set.
            if any(re.match(r"[AB][0-9]", a) for a in mp_attrs) and \
                    ele_kind_bmad not in ("AB_Multipole", "Multipole", "SAD_Mult"):
                push_attr("scale_multipoles = F")

        elif key == "MetaP":
            metaP = props["MetaP"]
            for mkey in metaP.keys():
                # Bmad keeps three metadata strings. The rest of MetaP (ID,
                # location, history and any custom nodes) has nowhere to go in
                # Bmad, so it is dropped.
                bkey = {"alias": "alias", "label": "type",
                        "description": "descrip"}.get(mkey, "")
                if bkey == "":
                    print(f"{name}: MetaP.{mkey} has no Bmad equivalent, not "
                          "translated")
                    continue

                # `description` (and any component, in principle) may be a
                # structure rather than a string, which a Bmad attribute cannot
                # hold.
                val = metaP[mkey]
                if val.is_map() or val.is_sequence():
                    print(f"{name}: MetaP.{mkey} is not a simple string, not "
                          "translated")
                    continue

                text = _bmad_quote(val.value)
                if text is None:
                    print(f"{name}: MetaP.{mkey} holds both quote characters, "
                          "not translated")
                    continue
                push_attr(f"{bkey} = {text}")
        elif key == "PatchP":
            patchP = props["PatchP"]
            for pkey in patchP.keys():
                tmp = ""
                if pkey == "x_offset":
                    tmp = f"x_offset = {patchP['x_offset'].value},"
                elif pkey == "y_offset":
                    tmp = f"y_offset = {patchP['y_offset'].value},"
                elif pkey == "z_offset":
                    tmp = f"z_offset = {patchP['z_offset'].value},"
                elif pkey == "t_offset":
                    tmp = f"t_offset = {patchP['t_offset'].value},"
                elif pkey == "x_rot":
                    tmp = f"y_pitch = {fmt(-patchP['x_rot'].as_float())},"
                elif pkey == "y_rot":
                    tmp = f"x_pitch = {patchP['y_rot'].value},"
                elif pkey == "z_rot":
                    tmp = f"tilt = {patchP['z_rot'].value},"
                elif pkey == "flexible":
                    flex = patchP["flexible"].value.lower()
                    tmp = f"flexible = {'T' if flex == 'true' else 'F'},"
                elif pkey == "ref_coords":
                    ref = patchP["ref_coords"].value
                    if ref == "ENTRANCE_END":
                        tmp = "ref_coords = entrance_end,"
                    elif ref == "EXIT_END":
                        tmp = "ref_coords = exit_end,"
                elif pkey == "user_sets_length":
                    usl = patchP["user_sets_length"].value.lower()
                    tmp = f"user_sets_length = {'T' if usl == 'true' else 'F'},"
                push_attr(tmp)
        elif key == "RFP":
            rfP = props["RFP"]
            if props["kind"].value == "CrabCavity":
                raise ValueError(f"{name}: CrabCavity not yet translated")
            for rfkey in rfP.keys():
                tmp = ""
                if rfkey == "frequency":
                    tmp += f"rf_frequency = {rfP['frequency'].value}, "
                    tmp += "harmon_master = false,"

                elif rfkey == "harmon":
                    tmp += f"harmon = {rfP['harmon'].value}, "
                    tmp += "harmon_master = true,"

                elif rfkey == "voltage":
                    tmp += f"voltage = {rfP['voltage'].value},"

                elif rfkey == "gradient":
                    if "L" in props and props["L"].as_float() != 0:
                        length = props["L"].as_float()
                        grad = rfP["gradient"].as_float()
                        tmp += f"voltage = {fmt(grad * length)},"
                        print(f"{name}: gradient not yet supported, replacing "
                              "with voltage = gradient * length")
                    else:
                        raise ValueError(
                            f"{name}: `gradient` not yet supported & `length` is "
                            "undefined => voltage is undefined")

                elif rfkey == "phase":
                    tmp += f"phi0 = {rfP['phase'].value},"

                elif rfkey == "multipass_phase":
                    tmp += f"phi0_multipass = {rfP['multipass_phase'].value},"

                elif rfkey == "cavity_type":
                    tmp += f"cavity_type = {rfP['cavity_type'].value},"

                elif rfkey == "num_cells":
                    tmp += f"n_cell = {rfP['num_cells'].value},"

                elif rfkey == "zero_phase":
                    zp = rfP["zero_phase"].value
                    if zp == "ACCELERATING":
                        raise ValueError(f"{name}: `Accelerating` phase is not "
                                         "supported with phi0_autoscale in Bmad")
                    elif zp == "BELOW_TRANSITION":
                        tmp += "rf_phase_below_transition_ref = T,"
                    elif zp == "ABOVE_TRANSITION":
                        tmp += "rf_phase_below_transition_ref = F,"
                    else:
                        print(f"{name}: unknown zero_phase type")

                elif rfkey == "L_active":
                    raise ValueError(f"{name}: `L_active` is a dependent "
                                     "parameter in Bmad")

                elif rfkey == "dE_ref":
                    raise ValueError(f"{name}: needs translation to LCavity for "
                                     "`dE_ref`")
                push_attr(tmp)
            if "frequency" in rfP and "harmon" in rfP:
                raise ValueError(f"{name}: can only define `frequency` or "
                                 "`harmon` but not both")
        elif key == "SolenoidP":
            solP = props["SolenoidP"]
            if solP.keys():
                if "Ksol" in solP:
                    push_attr(f"ks = {solP['Ksol'].value}")
                elif "Bsol" in solP:
                    push_attr("field_master = T")
                    push_attr(f"bs_field = {solP['Bsol'].value}")
                else:
                    print(f"{name} - unknown key(s): {solP.keys()}")
        elif key == "TrackingP":
            trackingP = props["TrackingP"]
            for tkey in trackingP.keys():
                if tkey == "Bmad":
                    pass
        elif key == "ReferenceChangeP":
            if ele_kind_bmad != "Patch":
                raise ValueError(
                    f"{name}: Bmad reference changes only allowed in Patch "
                    "elements (PALS: Patch / RefereneChange)")
            refchangeP = props["ReferenceChangeP"]
            for rkey in refchangeP.keys():
                if rkey == "dtime_ref":
                    push_attr(f"t_offset = {refchangeP['dtime_ref'].value}")

                elif rkey == "dE_ref":
                    push_attr(f"E_tot_offset = {refchangeP['dE_ref'].value}")

                elif rkey == "dpc_ref":
                    raise ValueError(f"{name}: dpc_ref (p0c_offset) not "
                                     "supported by Bmad, only E_tot_offset")

                elif rkey == "time_ref":
                    raise ValueError(f"{name}: setting time_ref is not supported "
                                     "by Bmad")

                elif rkey == "E_tot_ref":
                    push_attr(f"E_tot_set = {refchangeP['E_tot_ref'].value}")

                elif rkey == "pc_ref":
                    push_attr(f"p0c_set = {refchangeP['pc_ref'].value}")

                elif rkey == "species_ref":
                    raise ValueError(f"{name}: changing species in-beamline is "
                                     "not supported by Bmad")

    return BmadEleDef(name, ele_kind_bmad, attrs)
