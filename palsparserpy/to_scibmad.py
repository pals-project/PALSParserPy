"""
Translation of a PALS lattice into a SciBmad lattice file.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from ._common import ctrl_variables, facility_entry, facility_props, fmt
from .node import YAMLNode

__all__ = ["SciBmadEle", "SciBmadBeamline", "SciBmadLatticeList",
           "SciBmadController", "SciBmadLattice", "pals_to_scibmad",
           "write_scibmad_file"]

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class SciBmadEle:
    """A single SciBmad ``LineElement``: its ``name`` and the already-translated
    keyword-argument fragments (``attrs``, each a ``"keyword = value"``
    string)."""
    name: str
    attrs: List[str] = field(default_factory=list)


@dataclass
class SciBmadBeamline:
    """A SciBmad ``Beamline``: its ``name``, the ordered member element
    ``members`` (by name), and the reference-parameter fragments ``ref`` taken
    from the line's first entry."""
    name: str
    members: List[str] = field(default_factory=list)
    ref: List[str] = field(default_factory=list)


@dataclass
class SciBmadLatticeList:
    """A SciBmad lattice list: its ``name`` and the ordered branch/beamline
    ``branches`` (by name)."""
    name: str
    branches: List[str] = field(default_factory=list)


@dataclass
class SciBmadController:
    """A SciBmad ``Controller``: what a PALS ``Controller`` becomes.

    - ``name``: the controller name.
    - ``slaves``: the controlled properties, each a
      ``"(ele, :prop) => (ele; vars...) -> expr"`` pair-and-function string.
    - ``vars``: the variables' initial values, each a ``"name = value"`` string.
    """
    name: str
    slaves: List[str] = field(default_factory=list)
    vars: List[str] = field(default_factory=list)


@dataclass
class SciBmadLattice:
    """An in-memory model of a SciBmad lattice.

    Produced by :func:`pals_to_scibmad` and serialized to a file by
    :func:`write_scibmad_file`:

    - ``particle``: ``BeginningEle`` particle-coordinate lines (including the
      ``v = [...]`` vector).
    - ``elements``: ``LineElement`` definitions (:class:`SciBmadEle`).
    - ``controllers``: ``Controller`` definitions (:class:`SciBmadController`).
    - ``beamlines``: ``Beamline`` definitions (:class:`SciBmadBeamline`).
    - ``lattices``: lattice lists (:class:`SciBmadLatticeList`).
    """
    particle: List[str] = field(default_factory=list)
    elements: List[SciBmadEle] = field(default_factory=list)
    controllers: List[SciBmadController] = field(default_factory=list)
    beamlines: List[SciBmadBeamline] = field(default_factory=list)
    lattices: List[SciBmadLatticeList] = field(default_factory=list)


# ---------------------------------------------------------------------------
def pals_to_scibmad(yaml: YAMLNode) -> SciBmadLattice:
    """Translate a parsed PALS lattice ``yaml`` (as returned by
    :func:`~palsparserpy.parse_file`) into a :class:`SciBmadLattice`.

    The returned structure is an in-memory model of the *SciBmad* lattice
    (elements, beamlines, lattice lists), not the input PALS tree. Translation is
    a three-step process: parse the PALS file with ``parse_file``, build the
    target model with ``pals_to_scibmad``, then emit the SciBmad lattice file with
    :func:`write_scibmad_file`::

        yaml = parse_file(file_dir)
        write_scibmad_file(pals_to_scibmad(yaml), filename)
    """
    facility = yaml["PALS"]["facility"]
    lat = SciBmadLattice()
    for ele in facility:
        props = ele.child(0)
        if "kind" not in props:
            continue
        kind = props["kind"].value
        if kind == "BeginningEle":
            _, particle = _ele_to_scibmad_str(ele)
            lat.particle.extend(particle)
        elif kind == "Lattice":
            name = props.node_key()
            branches = []
            for bl in props["branches"]:
                # A branch is either the bare name of a beamline or that name
                # carrying the branch's own settings, which SciBmad takes from the
                # beamline rather than the lattice list.
                branches.append(bl.child(0).node_key() if bl.is_map() else bl.value)
            lat.lattices.append(SciBmadLatticeList(name, branches))
        elif kind == "BeamLine":
            lat.beamlines.append(_make_scibmad_beamline(ele, facility))
        elif kind == "Controller":
            lat.controllers.append(_make_scibmad_controller(ele, facility))
        elif kind in ("constant", "variable"):
            raise ValueError(f"{props.node_key()}: `{kind}` definitions are not "
                             "yet translated to SciBmad")
        else:
            lat.elements.append(_make_scibmad_ele(ele))
    return lat


# ---------------------------------------------------------------------------
def write_scibmad_file(lat: SciBmadLattice, filename) -> None:
    """Serialize the :class:`SciBmadLattice` ``lat`` to ``filename`` as a SciBmad
    lattice file.

    Write the particle-start block, the ``@elements`` block of ``LineElement``s,
    the ``Controller`` definitions, the ``Beamline`` definitions, and the lattice
    lists.
    """
    with open(filename, "w") as out:
        if lat.particle:
            out.write("\n".join(lat.particle) + "\n\n")
        out.write("@elements begin\n")
        for ele in lat.elements:
            out.write(_format_scibmad_ele(ele) + "\n")
        out.write("end\n\n")
        for ctrl in lat.controllers:
            out.write(_format_scibmad_controller(ctrl) + "\n")
        if lat.controllers:
            out.write("\n")
        for bl in lat.beamlines:
            out.write(_format_scibmad_beamline(bl) + "\n")
        for latt in lat.lattices:
            out.write(_format_scibmad_lattice(latt) + "\n")


# ---------------------------------------------------------------------------
def _format_scibmad_ele(ele: SciBmadEle) -> str:
    """Render a :class:`SciBmadEle` as a ``name = LineElement(...)`` definition."""
    return f"{ele.name} = LineElement({', '.join(ele.attrs)})"


# ---------------------------------------------------------------------------
def _format_scibmad_controller(ctrl: SciBmadController) -> str:
    """Render a :class:`SciBmadController` as a
    ``name = Controller(slaves...; vars = (; ...))`` definition."""
    slaves = ",\n  ".join(ctrl.slaves)
    variables = ", ".join(ctrl.vars)
    return f"{ctrl.name} = Controller(\n  {slaves};\n  vars = (; {variables})\n)"


# ---------------------------------------------------------------------------
def _format_scibmad_beamline(bl: SciBmadBeamline) -> str:
    """Render a :class:`SciBmadBeamline` as a ``name = Beamline([members],
    ref...)`` definition."""
    members = "".join(m + "," for m in bl.members)
    ref = "".join(r + "," for r in bl.ref)
    return f"{bl.name} = Beamline([{members}], {ref})"


# ---------------------------------------------------------------------------
def _format_scibmad_lattice(latt: SciBmadLatticeList) -> str:
    """Render a :class:`SciBmadLatticeList` as a ``name = [branches]`` list."""
    inner = "".join(b + "," for b in latt.branches)
    return f"{latt.name} = [{inner}]"


# ---------------------------------------------------------------------------
def _ele_to_scibmad_str(ele: YAMLNode) -> Tuple[List[str], List[str]]:
    """Translate a ``BeginningEle`` element into SciBmad reference and particle
    fragments.

    Returns ``(ref, particle)`` where ``ref`` holds the reference-parameter
    fragments from the element's ``ReferenceP`` (species and energy) and
    ``particle`` holds the coordinate lines from its ``ParticleP`` (followed by
    the ``v = [...]`` phase-space vector).
    """
    props = ele.child(0)
    ref: List[str] = []
    particle: List[str] = []
    for key in props.keys():
        if key == "TwissP":
            print("TwissP not yet supported")
        elif key == "ReferenceP":
            referenceP = props["ReferenceP"]
            for k in referenceP.keys():
                if k == "species_ref":
                    ref.append(f"species_ref = {referenceP[k].value}")
                elif k == "pc_ref":
                    ref.append(f"pc_ref = {referenceP[k].value}")
                elif k == "E_tot_ref":
                    ref.append(f"E_ref = {referenceP[k].value}")
                elif k in ("time_ref", "location"):
                    print(f"{k} not supported yet")
        elif key == "ParticleP":
            particleP = props["ParticleP"]
            for k in particleP.keys():
                val = particleP[k].value
                if k in ("x", "y", "z", "px", "py", "pz"):
                    particle.append(f"{k} = {val}")
                elif k in ("spin_x", "spin_y", "spin_z"):
                    # SciBmad carries spin as a quaternion, not as its components.
                    print(f"{k} not yet supported")
            particle.append("v = [ x px y py z pz ]")
    return ref, particle


# ---------------------------------------------------------------------------
def _make_scibmad_beamline(ele: YAMLNode, facility: YAMLNode) -> SciBmadBeamline:
    """Translate a ``BeamLine`` element into a :class:`SciBmadBeamline`.

    Collect the member element names (dropping the leading reference entry,
    ``line[0]``) and the reference parameters read from that first entry. A line
    may also name its beginning element instead of spelling it out, in which case
    the reference parameters are on that element's ``facility`` definition.
    """
    props = ele.child(0)
    name = props.node_key()
    line = props["line"]
    beginning = line.child(0)
    if not beginning.is_map():
        beginning = facility_entry(facility, beginning.value)
        if beginning is None:
            raise ValueError(f"BeamLine {name}: its first element is not defined "
                             "in the facility")
    ref, _ = _ele_to_scibmad_str(beginning)
    members: List[str] = []
    for i in range(1, len(line)):
        line_ele = line.child(i)
        if line_ele.is_scalar():
            members.append(line_ele.value)
        elif line_ele.is_map():
            members.append(line_ele.child(0).node_key())
    return SciBmadBeamline(name, members, ref)


# ---------------------------------------------------------------------------
def _scibmad_control_target(cname: str, param: str,
                            facility: YAMLNode) -> Tuple[str, str]:
    """Translate a controller's ``parameter`` target into a SciBmad
    ``(element, :property)`` pair.

    Returns ``(element, property)``. SciBmad keeps the PALS parameter names, so a
    group-qualified target such as ``q>MagneticMultipoleP.Kn1`` needs only its
    group prefix dropped.

    A target may name its element by kind as well as by name, as
    ``{kind}::{name}``; the qualifier is checked against the element found and
    then dropped, SciBmad naming each element once.

    Targets SciBmad cannot express -- a pattern matching several elements, or a
    ``>>`` or ``>>>`` qualifier naming the BeamLine or Lattice an element is
    reached through -- raise an error.
    """
    if ">>" in param:
        raise ValueError(f"controller {cname}: `{param}` reaches its element "
                         "through a BeamLine or Lattice qualifier, which has no "
                         "SciBmad equivalent")

    parts = param.split(">")
    if len(parts) != 2:
        raise ValueError(f"controller {cname}: control parameter `{param}` is not "
                         "of the form `element>parameter`")
    slave, path = parts

    # An element may be named by its kind as well as by its name.
    if "::" in slave:
        qualifier = slave.split("::")
        if len(qualifier) != 2:
            raise ValueError(f"controller {cname}: `{param}` does not name a "
                             "single element kind")
        kind_wanted, slave = qualifier
        props = facility_props(facility, slave)
        if props is None:
            raise ValueError(f"controller {cname}: `{param}` names no element of "
                             "the facility")
        ele_kind = props["kind"].value if "kind" in props else ""
        if kind_wanted != ele_kind:
            raise ValueError(f"controller {cname}: `{param}` asks for a "
                             f"{kind_wanted} but {slave} is a {ele_kind}")

    if not _NAME_RE.match(slave):
        raise ValueError(f"controller {cname}: `{param}` selects slaves by "
                         "pattern, which a SciBmad Controller cannot express")

    # `length` is the one PALS element parameter that is not in a group, and the
    # one whose SciBmad name differs.
    if path == "length":
        return slave, "L"

    prop = path.split(".")[-1]
    if not _NAME_RE.match(prop):
        raise ValueError(f"controller {cname}: `{param}` does not name a single "
                         "parameter")
    return slave, prop


# ---------------------------------------------------------------------------
def _make_scibmad_controller(ele: YAMLNode,
                             facility: YAMLNode) -> SciBmadController:
    """Translate a ``Controller`` element into a :class:`SciBmadController`.

    Each control becomes a function of the controller's variables, which SciBmad
    passes as keyword arguments. ``control_type: RELATIVE`` adds its expression to
    the value the element already carries -- that is what makes it relative --
    while ``ABSOLUTE`` replaces it.
    """
    props = ele.child(0)
    name = props.node_key()

    control_type = props["control_type"].value if "control_type" in props \
        else "ABSOLUTE"
    if control_type not in ("ABSOLUTE", "RELATIVE"):
        raise ValueError(f"{name}: control_type must be ABSOLUTE or RELATIVE, not "
                         f"{control_type}")

    var_names: List[str] = []
    variables: List[str] = []
    for var, value in ctrl_variables(props):
        var_names.append(var)
        variables.append(f"{var} = {value}")
    # SciBmad calls every control function with all of the controller's variables.
    signature = "(ele; " + ", ".join(var_names) + ")"

    slaves: List[str] = []
    if "controls" in props:
        for control in props["controls"]:
            if "parameter" not in control or "expression" not in control:
                raise ValueError(f"{name}: a controls entry needs both a "
                                 "`parameter` and an `expression`")
            slave, prop = _scibmad_control_target(
                name, control["parameter"].value, facility)
            expression = control["expression"].value
            if control_type == "RELATIVE":
                expression = f"ele.{prop} + ({expression})"
            slaves.append(f"({slave}, :{prop}) => {signature} -> {expression}")

    return SciBmadController(name, slaves, variables)


# ---------------------------------------------------------------------------
def _make_scibmad_ele(ele: YAMLNode) -> SciBmadEle:
    """Translate a single PALS element into a :class:`SciBmadEle`.

    Dispatch on the element's parameter groups (aperture, bend, body shift,
    multipoles, patch, RF, solenoid, tracking, reference change, ...) to build the
    keyword-argument fragments of a ``LineElement``. Unsupported parameters emit a
    message.
    """
    props = ele.child(0)
    attrs: List[str] = []

    for key in props.keys():
        if key == "kind":
            attrs.append(f"kind = {props['kind'].value}")
        elif key == "length":
            attrs.append(f"L = {props['length'].value}")
        elif key == "ACKickerP":
            print("ACKickerP not yet supported")
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

            # Shape and location describe an aperture; they do not put one there.
            # A group that sets no limit bounds nothing, so writing them out would
            # give the element an aperture the PALS lattice does not have. A
            # `vertices` aperture is bounded by its vertex list.
            if not (has_xmin or has_xmax or has_xwidth or has_xcen or
                    has_ymin or has_ymax or has_ywidth or has_ycen or
                    "vertices" in apertureP):
                continue

            if (has_xmin or has_xmax) and (has_xwidth or has_xcen):
                print("Either min and max should be defined or width and center, "
                      "not both.")
            elif (has_xmin and not has_xmax) or (has_xmax and not has_xmin):
                print("Both min and max need to be defined.")
            elif has_xmin and has_xmax:
                attrs.append(f"x1_limit = {apertureP['x_min'].value}")
                attrs.append(f"x2_limit = {apertureP['x_max'].value}")
            elif (has_xwidth and not has_xcen) or (has_xcen and not has_xwidth):
                print("Both width and center need to be defined.")
            elif has_xwidth and has_xcen:
                width = apertureP["x_width"].as_float()
                center = apertureP["x_center"].as_float()
                attrs.append(f"x1_limit = {fmt(center - width / 2)}")
                attrs.append(f"x2_limit = {fmt(center + width / 2)}")

            if (has_ymin or has_ymax) and (has_ywidth or has_ycen):
                print("Either min and max should be defined or width and center, "
                      "not both.")
            elif (has_ymin and not has_ymax) or (has_ymax and not has_ymin):
                print("Both min and max need to be defined.")
            elif has_ymin and has_ymax:
                attrs.append(f"y1_limit = {apertureP['y_min'].value}")
                attrs.append(f"y2_limit = {apertureP['y_max'].value}")
            elif (has_ywidth and not has_ycen) or (has_ycen and not has_ywidth):
                print("Both width and center need to be defined.")
            elif has_ywidth and has_ycen:
                width = apertureP["y_width"].as_float()
                center = apertureP["y_center"].as_float()
                attrs.append(f"y1_limit = {fmt(center - width / 2)}")
                attrs.append(f"y2_limit = {fmt(center + width / 2)}")

            for akey in apertureP.keys():
                if akey == "shape":
                    shape = apertureP["shape"].value
                    if shape == "ELLIPTICAL":
                        attrs.append("aperture_shape = ApertureShape.Elliptical")
                    elif shape == "RECTANGULAR":
                        attrs.append("aperture_shape = ApertureShape.Rectangular")
                    else:
                        print(f"shape {shape} is not supported")
                elif akey == "location":
                    location = apertureP["location"].value
                    if location == "ENTRANCE_END":
                        attrs.append("aperture_at = ApertureAt.Entrance")
                    elif location == "EXIT_END":
                        attrs.append("aperture_at = ApertureAt.Exit")
                    elif location == "BOTH_ENDS":
                        attrs.append("aperture_at = ApertureAt.BothEnds")
                    elif location in ("EVERYWHERE", "CENTER"):
                        attrs.append("aperture_at = ApertureAt.BothEnds")
                        print(f"location {location} not supported, set to BothEnds")
                    elif location == "NOWHERE":
                        print(f"location {location} not supported")
                elif akey == "aperture_shifts_with_body":
                    shifts = apertureP["aperture_shifts_with_body"].value.lower()
                    attrs.append("aperture_shifts_with_body = "
                                 f"{str(shifts == 'true').lower()}")
                elif akey == "aperture_active":
                    active = apertureP["aperture_active"].value.lower()
                    attrs.append(f"aperture_active = {str(active == 'true').lower()}")
                elif akey == "vertices":
                    print("vertices not yet supported")
                elif akey == "material":
                    print("material not yet supported")
                elif akey == "thickness":
                    print("thickness not yet supported")
        elif key == "BeamBeamP":
            bbP = props["BeamBeamP"]
            for bbkey in bbP.keys():
                if bbkey in ("sigma_x", "sigma_y", "sigma_z", "alpha_x", "beta_x",
                             "alpha_y", "beta_y", "charge", "energy", "N_particle"):
                    attrs.append(f"{bbkey} = {bbP[bbkey].value}")
        elif key == "BendP":
            bendP = props["BendP"]
            for bkey in bendP.keys():
                if bkey == "radius_ref":
                    print("radius_ref not yet supported")
                elif bkey == "Bn0_ref":
                    print("Bn0_ref not yet supported")
                elif bkey == "e1":
                    attrs.append(f"e1 = {bendP['e1'].value}")
                elif bkey == "e2":
                    attrs.append(f"e2 = {bendP['e2'].value}")
                elif bkey == "e1_rect":
                    print("e1_rect not yet supported")
                elif bkey == "e2_rect":
                    print("e2_rect not yet supported")
                elif bkey == "edge1_int":
                    attrs.append(f"edge1_int = {bendP['edge1_int'].value}")
                elif bkey == "edge2_int":
                    attrs.append(f"edge2_int = {bendP['edge2_int'].value}")
                elif bkey == "g_ref":
                    attrs.append(f"g_ref = {bendP['g_ref'].value}")
                elif bkey == "h1":
                    print("h1 not yet supported")
                elif bkey == "h2":
                    print("h2 not yet supported")
                elif bkey == "L_chord":
                    print("L_chord not yet supported")
                elif bkey == "L_sagitta":
                    print("L_sagitta not yet supported")
                elif bkey == "tilt_ref":
                    attrs.append(f"tilt_ref = {bendP['tilt_ref'].value}")
        elif key == "BodyShiftP":
            bodyshiftP = props["BodyShiftP"]
            for bskey in bodyshiftP.keys():
                if bskey in ("x_offset", "y_offset", "z_offset", "x_rot", "y_rot"):
                    attrs.append(f"{bskey} = {bodyshiftP[bskey].value}")
                elif bskey == "z_rot":
                    attrs.append(f"tilt = {bodyshiftP['z_rot'].value}")
        elif key == "ElectricMultipoleP":
            print("ElectricMultipoleP not yet supported")
        elif key == "FloorP":
            print("FloorP not yet supported")
        elif key == "FloorShiftP":
            print("FloorShiftP not yet supported")
        elif key == "ForkP":
            print("ForkP not yet supported")
        elif key == "GirderP":
            print("GirderP not yet supported")
        elif key == "MagneticMultipoleP":
            mmP = props["MagneticMultipoleP"]
            for mmkey in mmP.keys():
                attrs.append(f"{mmkey} = {mmP[mmkey].value}")
        elif key == "MetaP":
            metaP = props["MetaP"]
            for mkey in metaP.keys():
                if mkey in ("alias", "label", "description"):
                    attrs.append(f"{mkey} = {metaP[mkey].value}")
            print("MetaP not yet supported")
        elif key == "PatchP":
            patchP = props["PatchP"]
            for pkey in patchP.keys():
                if pkey == "x_offset":
                    attrs.append(f"dx = {patchP['x_offset'].value}")
                elif pkey == "y_offset":
                    attrs.append(f"dy = {patchP['y_offset'].value}")
                elif pkey == "z_offset":
                    attrs.append(f"dz = {patchP['z_offset'].value}")
                elif pkey == "t_offset":
                    attrs.append(f"dt = {patchP['t_offset'].value}")
                elif pkey == "x_rot":
                    attrs.append(f"dx_rot = {patchP['x_rot'].value}")
                elif pkey == "y_rot":
                    attrs.append(f"dy_rot = {patchP['y_rot'].value}")
                elif pkey == "z_rot":
                    attrs.append(f"dz_rot = {patchP['z_rot'].value}")
                elif pkey == "flexible":
                    print("flexible not yet supported")
                elif pkey == "ref_coords":
                    print("ref_coords not yet supported")
                elif pkey == "user_sets_length":
                    print("user_sets_length not yet supported")
        elif key == "RFP":
            rfP = props["RFP"]
            if props["kind"].value == "CrabCavity":
                attrs.append("is_crabcavity = true")
            num_cells = 0
            l_active = 0.0
            for rfkey in rfP.keys():
                if rfkey == "frequency":
                    attrs.append(f"rate = {rfP['frequency'].value}")
                    attrs.append("rate_meaning = false")
                elif rfkey == "harmon":
                    attrs.append(f"rate = {rfP['harmon'].value}")
                    attrs.append("rate_meaning = true")
                elif rfkey == "voltage":
                    attrs.append(f"voltage = {rfP['voltage'].value}")
                elif rfkey == "gradient":
                    print("gradient not yet supported")
                elif rfkey == "phase":
                    attrs.append(f"phi0 = {fmt(2 * math.pi * rfP['phase'].as_float())}")
                elif rfkey == "multipass_phase":
                    print("multipass_phase not yet supported")
                elif rfkey == "cavity_type":
                    traveling = rfP["cavity_type"].value == "TRAVELING_WAVE"
                    attrs.append(f"traveling_wave = {str(traveling).lower()}")
                elif rfkey == "num_cells":
                    num_cells = rfP["num_cells"].as_int()
                elif rfkey == "L_active":
                    l_active = rfP["L_active"].as_float()
                elif rfkey == "zero_phase":
                    zp = rfP["zero_phase"].value
                    if zp == "ACCELERATING":
                        attrs.append("zero_phase = Accelerating")
                    elif zp == "BELOW_TRANSITION":
                        attrs.append("zero_phase = BelowTransition")
                    elif zp == "ABOVE_TRANSITION":
                        attrs.append("zero_phase = AboveTransition")
            if "frequency" not in rfP and "harmon" not in rfP:
                attrs.append("rate_meaning = -1")
            attrs.append(f"tracking_method = SaganCavity(num_cells = {num_cells}, "
                         f"L_active = {fmt(l_active)})")
        elif key == "SolenoidP":
            solP = props["SolenoidP"]
            for skey in solP.keys():
                attrs.append(f"{skey} = {solP[skey].value}")
        elif key == "TrackingP":
            trackingP = props["TrackingP"]
            for tkey in trackingP.keys():
                if tkey == "SciBmad":
                    sbm = trackingP["SciBmad"]
                    for sbkey in sbm.keys():
                        if sbkey == "tracking_method":
                            if sbm["tracking_method"].value == "scibmad_standard":
                                attrs.append("tracking_method = SciBmadStandard()")
        elif key == "ReferenceChangeP":
            refchangeP = props["ReferenceChangeP"]
            for rkey in refchangeP.keys():
                if rkey == "extra_dtime_ref":
                    print("extra_dtime_ref not yet supported")
                elif rkey == "dE_ref":
                    attrs.append(f"dE_ref = {refchangeP['dE_ref'].value}")
                elif rkey == "E_tot_ref":
                    attrs.append(f"E_ref = {refchangeP['E_tot_ref'].value}")
                elif rkey == "species_ref":
                    attrs.append(f"species_ref = {refchangeP['species_ref'].value}")

    return SciBmadEle(props.node_key(), attrs)
