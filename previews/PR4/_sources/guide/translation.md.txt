# Translating to SciBmad, Bmad, MAD-X and Xsuite

PALSParserPy can translate a PALS-format lattice into four accelerator formats:

- **`palsparserpy/to_scibmad.py`** — emits a [SciBmad](https://github.com/bmad-sim/SciBmad.jl)
  / [Beamlines](https://github.com/bmad-sim/Beamlines.jl) description.
- **`palsparserpy/to_bmad.py`** — emits a classic [Bmad](https://www.classe.cornell.edu/bmad/)
  lattice.
- **`palsparserpy/to_madx.py`** — emits a [MAD-X](https://mad.web.cern.ch/mad/) lattice.
- **`palsparserpy/to_xsuite.py`** — emits a Python script that builds an
  [Xsuite](https://github.com/xsuite/xsuite) lattice in an `xtrack.Environment`.

All four translators take a lattice already parsed by `parse_file`,
walk the element list, and map each PALS element and its parameters
onto the corresponding target-format element.

## Running the translators

Translating is a three-step process: parse, translate, write. `parse_file` reads
a PALS-YAML file into a parsed tree (a `YAMLNode`); `pals_to_bmad` /
`pals_to_madx` / `pals_to_scibmad` / `pals_to_xsuite` translate that tree into an
in-memory model of the *target* lattice (a `BmadLattice` / `MadxLattice` /
`SciBmadLattice` / `XsuiteLattice` of elements, beamlines, and parameters); and
`write_bmad_file` / `write_madx_file` / `write_scibmad_file` / `write_xsuite_file`
take that structure and an output path and serialize the
lattice file:

```python
import os
import palsparserpy as pp

bmad = pp.pals_to_bmad(pp.parse_file(os.path.join("lattice_files", "bta.pals.yaml")))
pp.write_bmad_file(bmad, os.path.join("lattice_files", "bta.pals_out.bmad"))

madx = pp.pals_to_madx(pp.parse_file(os.path.join("lattice_files", "bta.pals.yaml")))
pp.write_madx_file(madx, os.path.join("lattice_files", "bta.pals_out.madx"))

scibmad = pp.pals_to_scibmad(pp.parse_file(os.path.join("lattice_files",
                                                       "convert.pals.yaml")))
pp.write_scibmad_file(scibmad, os.path.join("lattice_files", "convert.pals_out.jl"))

xsuite = pp.pals_to_xsuite(pp.parse_file(os.path.join("lattice_files", "bta.pals.yaml")))
pp.write_xsuite_file(xsuite, os.path.join("lattice_files", "bta.pals_out.py"))
```

The Xsuite translation is a Python script rather than a lattice file, since an
Xsuite lattice is built by Python code. Running it needs `xtrack`, which
PALSParserPy itself does not; it defines the environment `env`, every element and
line, and `line`, the first branch of the lattice, together with `twiss_init` and
`particle_start`, the initial conditions the `BeginningEle` states:

```python
import xtrack as xt

namespace = {}
exec(open("lattice_files/bta.pals_out.py").read(), namespace)
line = namespace["line"]
tw = line.twiss(start=xt.START, end=xt.END, method="4d", **namespace["twiss_init"])
```

The [`examples/`](https://github.com/pals-project/PALSParserPy/tree/main/examples)
directory has runnable scripts, such as `examples/pals_to_bmad.py`,
`examples/pals_to_madx.py` and `examples/pals_to_xsuite.py`.

Anything a translator cannot express is either reported on stdout and skipped, or
raised as a `ValueError` when skipping it would quietly change the physics.

## Element and parameter mapping

PALS element kinds and their parameters do not map one-to-one onto Bmad, MAD-X or
Xsuite.
The translators encode the conversions — renamed parameters, unit changes, and
cases that have no equivalent (and are skipped with a warning). For example,
an `ApertureP` becomes a Bmad `ApertureParams`, with `x_min`/`x_max` mapped to
`x1_limit`/`x2_limit` (or derived from `x_center`/`x_width`).

The complete, element-by-element list of these mappings is given in the
[Parameter mapping reference](#parameter-mapping-reference) below. Consult it
when adding support for a new element or when a parameter comes through
untranslated. MAD-X differs from Bmad widely enough to be worth reading
[What MAD-X does differently](#what-mad-x-does-differently) first, and the same
goes for [What Xsuite does differently](#what-xsuite-does-differently).

## Extending a translator

To add support for a new element or parameter:

1. Find its PALS definition and decide on the target-format equivalent; record
   it in the [Parameter mapping reference](#parameter-mapping-reference) below.
2. Add the mapping to the element builder — `_make_bmad_ele` in
   `palsparserpy/to_bmad.py`, `_make_madx_ele` in `palsparserpy/to_madx.py`,
   `_make_scibmad_ele` in `palsparserpy/to_scibmad.py`, or `_make_xsuite_ele` in
   `palsparserpy/to_xsuite.py` — and to any helper it
   calls (e.g. `_ele_to_bmad_str`, `_make_bmad_line`, `_ele_to_madx_str`, or
   `_ele_to_scibmad_str`, `_make_scibmad_beamline`).
3. Translate a lattice that exercises the element and check the output.

## What MAD-X does differently

Bmad and MAD-X share a great deal, but a PALS lattice meets the differences at
almost every element. The ones that shape the whole translation:

- **Every MAD-X strength is normalized.** MAD-X has no field-valued attribute at
  all, where Bmad has `field_master` and `B1_GRADIENT`. A PALS component stated as
  a field (`Bn1`, `Bsol`, `Bn0_ref`) is therefore divided by the signed
  rigidity `P0/q`, which the file defines once as
  `pals_brho := beam->brho * beam->charge / abs(beam->charge);` and leaves MAD-X to
  evaluate from its own `BEAM` command.
- **MAD-X coefficients carry no `1/n!`.** MAD-X states a multipole as
  `Kn L = (L/Brho) d^n By/dx^n`, and so does PALS, so a PALS `KnN` is MAD-X's `KN`
  outright. (Bmad's `An`/`Bn` do carry the factorial, which is why the Bmad
  translation divides by one and this one does not.)
- **MAD-X has a skew attribute for each of the orders an element owns** — `k1s`,
  `k2s`, `k3s` — so a tilted or skew multipole of the element's own order needs no
  multipole element of its own. But MAD-X magnets are strictly single-order:
  a quadrupole may not carry a sextupole component, and only a `multipole` element
  has the `knl`/`ksl` arrays. Any other order on a magnet is reported and dropped.
- **A bend's geometry and its field are the one `angle`.** MAD-X does not use `k0`
  in its bend map, so it has no equivalent of Bmad's `dg`, the departure of the
  field from the reference bend. PALS decouples the two (the actual field is `Kn0`,
  and `Kn0_from_g_ref` says whether it defaults to the reference field); MAD-X cannot.
  The reference geometry is what is written out; a `Kn0` that disagrees with it is
  reported and dropped, because writing the field out instead would move every element
  downstream of the bend.
- **A misalignment lives outside the element definition.** A PALS `BodyShiftP`
  becomes a `SELECT, FLAG=ERROR` / `EALIGN` pair after the `USE` statement, not an
  attribute of the element. MAD-X rotates about the entrance of an element where
  PALS and Bmad rotate about its centre, so the two agree only to first order in
  the angles.
- **MAD-X has no controller element.** A PALS `Controller` becomes what MAD-X has
  instead: its variables become ordinary MAD-X variables and each of its controls
  becomes a deferred assignment, `q1->k1 := 2*k;`. A `RELATIVE` controller has both
  the value it varies and its own starting point written into the assignment, MAD-X
  forbidding the circular `q1->k1 := q1->k1 + dk` and having no notion of a knob's
  delta. MAD-X variables are global where a PALS controller's are its own, so a name
  two controllers both claim is prefixed with the controller that owns it.
- **MAD-X units are not PALS units.** Energies are GeV against eV, voltages MV
  against V, frequencies MHz against Hz, and phases are counted in turns where PALS
  counts them in radians. A value written as a number is converted during
  translation; one written as an expression is left for MAD-X to evaluate.
- **The longitudinal coordinate is measured against the energy.** MAD-X's `T`, `PT`
  and its dispersion are derivatives with respect to `pt = dE/(p0 c)` where PALS
  uses `pz = dp/p0`, and `pt = beta * pz`. Those quantities are written out divided
  or multiplied by MAD-X's own `beam->beta` rather than converted here.
- **Section order matters.** A MAD-X name has to be defined above the point of use,
  `BEAM` has to precede `USE`, and `EALIGN` can only follow it, there being no
  expanded sequence to apply an error to before then. `write_madx_file` writes the
  sections in that order.

## What Xsuite does differently

Xsuite takes its conventions from MAD-X — normalized strengths, `knl`/`ksl`
multipole arrays without a `1/n!`, sector-bend edge angles — but it is a Python
library rather than a file format, and that shapes the translation as much as the
physics does:

- **The output is a Python script.** `write_xsuite_file` writes a script that
  builds an `xtrack.Environment`: `env.new(...)` for each element,
  `env.new_line(...)` for each BeamLine, and `env.particle_ref` for the reference
  particle. A PALS value written as an expression becomes a quoted string, which
  Xsuite turns into a *deferred* expression that follows the variables it names,
  so changing `env['kq']` after the script has run changes every strength
  written in terms of `kq`.
- **Expressions are re-parenthesized.** Xsuite's expression parser reads `-a^2` as
  `(-a)^2` and `a^b^c` as `(a^b)^c`; PALS reads them as `-(a^2)` and `a^(b^c)`. So
  each PALS expression is parsed with PALS' precedence and written back out with
  `**` and explicit parentheses. Xsuite has most of PALS' functions; `cot` and
  `coth` are rewritten, and the rest (`modulo`, `factorial`, `random`, the
  particle-data functions, ...) are reported. Xsuite predefines no constants, so
  the script defines whichever PALS constants (`pi`, `c_light`, ...) it uses.
- **Every strength is normalized**, as in MAD-X. A field (`Bn1`, `Bsol`,
  `Bn0_ref`) is divided by the signed rigidity, which the script defines once as
  `env['pals_brho'] = p0c / (c * q0)` from the reference particle.
- **A bend's field is held apart from its geometry**, as in PALS and unlike MAD-X.
  The geometry is the `Bend`'s `angle` and arc `length`; its field `k0` follows the
  geometry by default (PALS' `Kn0_from_g_ref`), and is written out when `Kn0`
  differs from the reference curvature or when `Kn0_from_g_ref` is false.
- **Every magnet carries multipole arrays.** Orders a magnet has an attribute for
  become that attribute (`k1`/`k1s` of a `Quadrupole`, `k0`/`k1`/`k2` of a `Bend`,
  ...), per unit length; every other order goes into its integrated `knl`/`ksl`,
  with the PALS tilt rotated into the normal and skew parts. A zero-length
  quadrupole, sextupole or octupole is a thin `Multipole`, the Xsuite magnets of
  those names being thick.
- **Apertures and patches are elements of their own.** A PALS `ApertureP` becomes a
  `LimitRect` or `LimitEllipse` placed before and/or after the element
  (`<name>__aperture_entrance`, `<name>__aperture_exit`), and a `Patch` that both
  shifts and rotates becomes a `Translation` followed by a `Rotation`
  (`<name>__rotation`). Wherever a line names the element, its parts come with it.
- **The RF phase zero is Xsuite's own.** Both measure the phase in radians and
  frequency, voltage and energy in Hz, V and eV, so nothing is rescaled. But an
  Xsuite `Cavity` gives the reference particle `V sin(phase)`: its zero is the
  zero crossing below transition. So `pi/2` is added to an `ACCELERATING` phase,
  `pi` to an `ABOVE_TRANSITION` one, and nothing to a `BELOW_TRANSITION` one. (The
  script uses `phase`; Xsuite's older `lag`, in degrees, is deprecated.)
- **A misalignment lives on the element**, as Xsuite's `shift_*` and `rot_*_rad`
  attributes, anchored at the element's centre as PALS' is. Xsuite's `rot_x_rad`
  turns `s` towards `y`, the opposite of PALS' right-handed `x_rot`.
- **The coordinates are PALS' under other names**: `zeta` is `z`, `delta` is
  `pz` — and Xsuite's dispersion is measured against `delta`, so the PALS
  dispersion carries over unchanged.
- **Line items use Xsuite's line arithmetic**: a repeated subline is
  `n * env['sub']` and a reversed one `-env['sub']`; an `inherit`ed element is an
  Xsuite clone, `env.new('q2', 'q1')`. Xsuite has no element direction reversal
  (`direction: -1`), placement offsets, or per-occurrence parameter changes, which
  are reported.

## Parameter mapping reference

The following is the element-by-element mapping between PALS parameter groups
and their SciBmad/Bmad/MAD-X equivalents.

### Element kinds --> MAD-X keywords

- Bend --> sbend (PALS has the one bend, whose reference geometry is a sector)
- CrabCavity --> crabcavity
- Drift --> drift
- Kicker --> kicker
- Multipole --> multipole
- Octupole --> octupole
- Quadrupole --> quadrupole
- RFCavity --> rfcavity
- Sextupole --> sextupole
- Solenoid --> solenoid
- BeamBeam --> beambeam
- Mask --> collimator
- Instrument --> instrument
- Marker, BeginningEle --> marker
- Patch --> changeref
- Taylor --> matrix (the map itself is not yet translated)
- ACKicker, Wiggler, Converter, EGun, Foil, Match, Fiducial, FloorShift, Fork,
  ReferenceChange, UnionEle, Feedback --> no MAD-X equivalent (an error)
- Placeholder --> dropped from every line, as lattice expansion drops it, in every
  format

### Element kinds --> Xsuite classes

- Bend --> `Bend` (sector bend; `ref_geometry` other than `ARC` is reported)
- Quadrupole, Sextupole, Octupole --> `Quadrupole`, `Sextupole`, `Octupole`; a
  `Multipole` when the length is zero
- Multipole --> `Multipole`
- Kicker --> `Multipole` (`knl[0]` = `Kn0L`, `ksl[0]` = `Ks0L`), thick
  (`isthick=True`) when it has a length
- Solenoid --> `UniformSolenoid`
- RFCavity --> `Cavity`; CrabCavity --> `CrabCavity`
- Drift --> `Drift`
- Marker, BeginningEle --> `Marker`
- Instrument, Mask --> `Marker`, or `Drift` when it has a length (a mask's
  aperture becomes an aperture element of its own)
- Patch --> `Translation` and/or `Rotation`
- ACKicker, BeamBeam (an `xfields` element), Wiggler, Converter, EGun, Foil, Match,
  Fiducial, FloorShift, Fork, ReferenceChange, Girder, Taylor, UnionEle,
  Feedback --> not translated (an error)

### Parameter groups --> Xsuite

- ReferenceP --> `xt.Particles(species, p0c=pc_ref | energy0=E_tot_ref)`, set as
  `env.particle_ref`; a line whose own BeginningEle states a different one gets it
  as `env['line'].particle_ref`. `time_ref` and `location` are reported.
- TwissP --> `twiss_init`: `beta_a/b` --> `betx/y`, `alpha_a/b` --> `alfx/y`,
  `eta_x/y` --> `dx/dy`, `etap_x/y` --> `dpx/dpy`, `phi_a/b` --> `mux/muy` (in
  turns). `cmat*` and `deta_*_ds` are reported.
- ParticleP --> `particle_start`: `x`, `px`, `y`, `py`, `z` --> `zeta`,
  `pz` --> `delta`, `spin_x/y/z`.
- MagneticMultipoleP --> the element's own strength attributes (per unit length)
  and `knl`/`ksl` (integrated), tilts rotated in; fields divided by `pals_brho`.
  A component written as an expression stays a deferred expression (its order may
  not then be tilted).
- BendP --> `angle` and `length` (from whichever two of curvature, length and
  angle PALS gives); `e1`/`e2` --> `edge_entry_angle`/`edge_exit_angle`
  (`e1_rect`/`e2_rect` converted by `ref_geometry`); `edge1_int`/`edge2_int` -->
  `edge_*_fint = 0.5`, `edge_*_hgap = 2 * edge_int`; `tilt_ref` --> `rot_s_rad`;
  `Kn0_from_g_ref: false` --> `k0 = 0`. `h1`, `h2` are reported.
- BodyShiftP --> `shift_x`, `shift_y`, `shift_s`, `rot_x_rad = -x_rot`,
  `rot_y_rad = y_rot`, `rot_s_rad_no_frame = z_rot`, with
  `rot_shift_anchor = length/2`. A Drift or Marker cannot be misaligned (reported);
  a bend turns about its arc's middle rather than its chord's (reported).
- ApertureP --> `LimitRect(min_x, max_x, min_y, max_y)` or
  `LimitEllipse(a, b, shift_x, shift_y)` at `ENTRANCE_END`, `EXIT_END` or
  `BOTH_ENDS`. `CENTER`, `EVERYWHERE`, `VERTICES`, `CUSTOM_SHAPE`, `material`,
  `thickness`, a switched-off aperture, and `aperture_shifts_with_body` on a
  misaligned element are reported.
- RFP --> `frequency` | `harmonic`, `voltage` (or `gradient * L_active`),
  `phase` shifted by the `zero_phase` offset above. `multipass_phase`,
  `num_cells`, `dE_ref` and a `TRAVELING_WAVE` `cavity_type` are reported.
- SolenoidP --> `ks = Ksol` or `Bsol / pals_brho`.
- PatchP --> `Translation(shift_x, shift_y, shift_s)` then
  `Rotation(rot_y_rad, rot_x_rad, rot_s_rad)`, Xsuite's default rotation order
  being PALS' `R_y R_x R_z`. A `z_offset` is tracked as a drift but does not
  advance `s` (reported); `flexible`, `ref_coords` and `user_sets_length` are
  reported.
- MetaP --> comment lines above the element.
- TrackingP --> ignored (program specific by design).
- An extension block (marked with an `extension` key or declared under `PALS:
  extension_labels`) --> reported once, by name.
- ACKickerP, BeamBeamP, CoordinateSetP, ElectricMultipoleP, FloorP, ForkP,
  GirderP, ReferenceChangeP, TaylorP --> not translated (an error). Any other
  group is reported.

### ACKickerP --> None

### ApertureP --> ApertureParams
- x_min --> x1_limit
- x_max --> x2_limit
- x_width and x_center:
    - x1_limit = x_center - x_width / 2
    - x2_limit = x_center + x_width / 2
- Note: Either both min and max are defined, or width and center are defined, not both.
- y_min --> y1_limit
- y_may --> y2_limit
- y_width and y_center:
    - y1_limit = y_center - y_width / 2
    - y2_limit = y_center + y_width / 2

- shape --> aperture_shape
    - RECTANGULAR --> Rectangular
    - ELLIPTICAL --> Elliptical
    - VERTICES --> none
    - CUSTOM_SHAPE --> none

- location --> aperture_at
    - ENTRANCE_END --> Entrance
    - EXIT_END --> Exit
    - BOTH_ENDS --> BothEnds
    - EVERYWHERE --> BothEnds
    - CENTER --> BothEnds
    - NOWHERE --> none

- aperture_shifts_with_body --> aperture_shifts_with_body
- aperture_active --> aperture_active
- vertices --> none
- material --> none
- thickness --> none

- Note (Bmad): Bmad states a limit as a distance from the axis rather than as a
  coordinate — it loses a particle at `x < -x1_limit` — so the low-side limit is
  the negated PALS `x_min`.
- Note (MAD-X): MAD-X states a half extent about the axis and the offset of the
  centre separately, so both PALS forms come to `aperture = {x_half, y_half}` and
  `aper_offset = {x_center, y_center}`; `shape` becomes `apertype`
  (RECTANGULAR --> rectangle, ELLIPTICAL --> ellipse).
- Note (MAD-X): the `shape` decides which components describe the aperture. A
  `RECTANGULAR` or `ELLIPTICAL` one is bounded by its limits and ignores any vertices;
  a `VERTICES` (or `CUSTOM_SHAPE`) one is reported, MAD-X taking a vertex outline only
  from a file of its own, which PALS does not name.
- Note (MAD-X): MAD-X checks an aperture at the entrance of an element only, so
  `location` is not translated; nor are `aperture_shifts_with_body`, `material` and
  `thickness`, and an `aperture_active: false` cannot be expressed.
- Note (MAD-X): MAD-X cannot put an aperture on a drift — use a collimator — and
  its aperture values are positional, so a group that bounds one plane and not the
  other has the unbounded one written out as 1 m.
- Note: shape, location and the rest describe an aperture; they do not put one
  there. A group that sets no limit is skipped entirely by all three translators,
  since writing it out would give the element an aperture the PALS lattice does
  not have.

### BeamBeamP --> Not in SciBmad yet
- Note (MAD-X): sigma_x --> sigx, sigma_y --> sigy, charge --> charge,
  N_particle --> npart. MAD-X models the opposite beam as a four-dimensional lens,
  so sigma_z, alpha_x, beta_x, alpha_y, beta_y and energy have no equivalent.

### BendP --> BendParams
- radius_ref -> caluclated (Bmad: rho)
- Bn0_ref -> calculated (Bmad: B_field)
- e1 --> e1
- e2 --> e2
- e1_rect --> calculated
- e2_rect --> calcualted
- edge1_int --> edge1_int
- edge2_int --> edge2_int
- g_ref --> g_ref
- h1 --> not in scibmad
- h2 --> not in scibmad
- L_chord --> calculated
- L_sagitta --> calculated
- tilt_ref --> tilt_ref

- Note (MAD-X): PALS states a bend's geometry with any two of three sets of mutually
  dependent parameters — a curvature (`g_ref`, `radius_ref`, `Bn0_ref`), a length
  (`length`, `L_chord`, `L_rectangle`) and the angle (`angle_ref`) — one from each of
  two different sets. MAD-X wants one particular pair, the `angle` and the arc length
  `l`, so whichever pair was given is turned into that pair:
  `angle = g_ref * length`, `= 2*asin(g_ref*L_chord/2)`, `= asin(g_ref*L_rectangle)`;
  `l = angle_ref / g_ref`, `= angle_ref*L_chord/(2*sin(angle_ref/2))`,
  `= angle_ref*L_rectangle/sin(angle_ref)`. Only `Bn0_ref` needs `pals_brho`, the rest
  being pure geometry. A bend that states too little for both is reported.
- Note (MAD-X): e1 --> e1, e2 --> e2 (a MAD-X sbend measures its pole faces against
  the same sector geometry PALS does). `e1_rect`/`e2_rect` are converted according to
  `ref_geometry`: `ARC`/`CHORD` --> `e = e_rect + angle/2`; `ENTRANCE_COORDS` -->
  `e1 = e1_rect`, `e2 = e2_rect + angle`; `EXIT_COORDS` --> `e1 = e1_rect + angle`,
  `e2 = e2_rect`.
- Note (Bmad): angle_ref --> angle. e1 --> e1 and e2 --> e2, a Bmad sbend measuring its
  faces against the sector geometry too; `e1_rect`/`e2_rect` are converted the same way as
  for MAD-X, with the angle worked out from `angle_ref`, or from `g_ref`/`radius_ref` with
  `length`, `L_chord` or `L_rectangle`.
- Note (MAD-X): edge1_int --> `fint = 0.5, hgap = 2*edge1_int`, edge2_int -->
  `fintx`/`hgapx`; h1 --> h1, h2 --> h2; tilt_ref --> tilt.
- Note (MAD-X): a MAD-X sbend is always an arc with vertically pure multipoles, so a
  `ref_geometry` other than `ARC`, or a `multipole_geometry` other than
  `FOLLOWS_REF_GEOMETRY`/`VERTICALLY_PURE`, is reported. `L_sagitta` is an output
  parameter and is an error.
- Note (MAD-X): `Kn0_from_g_ref: false` with no order-0 multipole set gives a bend with
  the reference geometry and no field of its own, which MAD-X — tracking through the
  same `angle` it bends the reference orbit with — cannot express, and is reported.

### BodyShiftP --> AlignmentParams
- x_offset --> x_offset
- y_offset --> y_offset
- z_offset --> z_offset
- x_rot --> x_rot
- y_rot --> y_rot
- z_rot --> tilt

- Note (Bmad): x_rot --> `y_pitch = -x_rot` and y_rot --> `x_pitch`, Bmad naming a
  rotation by the plane it tips the element into rather than by the axis it turns
  about.
- Note (MAD-X): the whole group becomes an `EALIGN` command, not element attributes:
  x_offset --> dx, y_offset --> dy, z_offset --> ds, x_rot --> -dphi,
  y_rot --> dtheta, z_rot --> dpsi. MAD-X rotates about the entrance of an element
  where PALS rotates about its centre, so the two agree only to first order.

### ElectricMultipoleP --> Not in SciBmad yet

### FloorP --> Calculated

### CoordinateSetP --> Set in floor shift element (to be added to scibmad)
- Note (Bmad): a `FloorShift` becomes a `floor_shift` and a `Fiducial` a `fiducial`.
  `origin_ele` and `origin_ele_ref_pt` carry over (PALS defaults to `EXIT_END`, Bmad to
  `center`, so the reference point is always written). The CoordinateSetP shift and the
  element's BodyShiftP together place the element, and become one Bmad shift: the patch
  attributes `x_offset`, ..., `x_pitch = y_rot`, `y_pitch = -x_rot`, `tilt = z_rot` for a
  `floor_shift`, and `dx_origin`, ..., `dtheta_origin = y_rot`, `dphi_origin = -x_rot`,
  `dpsi_origin = z_rot` for a `fiducial`. When the CoordinateSetP shift rotates as well as
  the BodyShiftP shifts, the two are composed numerically, which needs numbers.
- Note (Bmad): origin `PREVIOUS_ELEMENT` is Bmad's own default for a `floor_shift` and has
  no meaning for a `fiducial` (an error). `GLOBAL_ORIGIN` is Bmad's default for a `fiducial`;
  a `floor_shift` has no such setting and is placed from Bmad's `beginning` element, which
  is an error when the lattice moves the beginning element off the origin.
- Note (MAD-X): MAD-X has no element that sets the global coordinates of the reference
  curve, so the group is an error. `FloorShift` and `Fiducial`, the two kinds that
  carry it, have no MAD-X equivalent either.

### ForkP --> Needs to be Implemented in scibmad

### Girders (`PALS: girders`, GirderP)
- Note (Bmad): each girder becomes a Bmad `girder`, written after any girder it supports as
  Bmad requires. `GirderP.elements` becomes the slave list, which Bmad takes either as
  element names or as one `start:end` range; any other element set is an error. The
  CoordinateSetP origin becomes `origin_ele` (`GLOBAL_ORIGIN` --> `global_coordinates`) and
  its shift the `d*_origin` attributes; the BodyShiftP becomes the girder's offsets, pitches
  and tilt.
- Note (MAD-X): MAD-X has no girders, so `PALS: girders` is an error.
- Note (SciBmad): SciBmad has no girders; they are not translated.

### MagneticMultipoleP --> BMultipoleParams
- tiltN --> tiltN
- [BK][ns]NL? --> [BK][ns]NL?
- BnN(L) --> BnN(L)
- Note (Bmad): the normal component of the multipole that is an element's own strength becomes
  that strength: `Kn1` --> `K1` for a quadrupole, `Kn2` --> `K2`, `Kn3` --> `K3`, and `Kn0` -->
  `dg` for a bend (`Bn1` --> `B1_GRADIENT`, ..., `Bn0` --> `db_field`). Every other order stays
  a multipole, and becomes the integrated `An`/`Bn`.
- Note (Bmad): a Bmad bend carries a quadrupole and a sextupole component of its own besides its
  bending field, so a bend's `Kn1` --> `K1` and `Kn2` --> `K2` as well (`Bn1` -->
  `B1_GRADIENT`, `Bn2` --> `B2_GRADIENT`). These two hold a normal field only, and are components
  added to a field the bend already has rather than the strength that makes it a bend, so an
  order with a skew part -- a `Ks1`/`Ks2`, or a `tilt1`/`tilt2` that rotates one into being --
  keeps both parts in the `An`/`Bn` form instead. A bend has no attribute above order 2, so its
  higher multipoles stay `An`/`Bn` either way.
- Note (Bmad): PALS states a bend's field outright, where Bmad states its departure from the
  reference bend. So `Kn0` is translated as `dg = Kn0 - g_ref` -- against `1/radius_ref` when
  the reference bend is given as a radius, and against `Bn0_ref` when the field is not
  normalized. A field equal to the reference bend departs from it by nothing, and no `dg` is
  written. The two flavors cannot be mixed: measuring a `Kn0` against a `Bn0_ref` (or the
  reverse) takes the reference momentum, which belongs to the branch and not to the element.
- Note (Bmad): Bmad reads an `An`/`Bn` on an ordinary element as a fraction of that
  element's own strength, where a PALS multipole is the field integral itself, so
  an element left carrying one also gets `scale_multipoles = F`. The kinds that hold
  nothing but multipoles do no such scaling and have no such attribute.

- Note (MAD-X): a PALS coefficient is a MAD-X coefficient outright — neither carries
  the `1/N!` of the field expansion — so the orders an element owns become:
  `KnN` --> `kN` and `KsN` --> `kNs` for a quadrupole (N=1), sextupole (2) and
  octupole (3); `Kn0` --> `angle`, `Kn1` --> `k1`, `Ks1` --> `k1s`, `Kn2` --> `k2`
  for a bend; `Kn0L` --> `-hkick` and `Ks0L` --> `vkick` for a kicker.
- Note (MAD-X): a `tiltN` is rotated into the normal and skew components, MAD-X's
  own `tilt` being one roll for the whole element rather than one per order.
- Note (MAD-X): a length is put in or taken out to match the attribute — `angle`,
  `hkick` and `vkick` are integrated, the rest are not.
- Note (MAD-X): a MAD-X magnet is strictly single-order. Only a `multipole` element
  has multipole arrays, where each order becomes the integrated `knl[N]`/`ksl[N]`;
  any other order on any other magnet is reported and dropped.
- Note (MAD-X): a `BnN`/`BsN` is divided by the signed rigidity `pals_brho`, MAD-X
  having no field-valued strength attribute.

### MetaP --> MetaParams
- alias --> alias (Bmad: alias)
- label --> label (Bmad: type)
- description --> description (Bmad: descrip)
- ID --> none
- location --> none
- history --> none
- Note: any other (non-standard) component --> none
- Note: a component holding a structure rather than a string is not translated.
- Note (Bmad): Bmad has no escape for a quote inside a string, so a value holding one
  quote character is wrapped in the other, and one holding both is not translated.
- Note (MAD-X): a MAD-X element holds no metadata of its own, so every component
  that is a plain string becomes a comment line above the element definition.

### ParticleP --> Create new bunch
- Note (MAD-X): MAD-X starts a particle with the `START` command of the `TRACK`
  module, which has no place in a lattice file, so the coordinates are written out
  as a comment: x, px, y, py as they stand, `z` --> `t = z / beam->beta` and
  `pz` --> `pt = pz * beam->beta`. Spin has no MAD-X equivalent.

### PatchP --> PatchParams
- x_offset --> x_offset
- y_offset --> y_offset
- z_offset --> z_offset
- t_offset --> dt (not in PALS yet)
- x_rot --> x_rot
- y_rot --> y_rot
- z_rot --> z_rot
- flexible --> none
- ref_coords --> none
- user_sets_length --> none
- Note (MAD-X): the offsets become `patch_trans = {x, y, z}` and the rotations
  `patch_ang = {x_rot, y_rot, z_rot}` of a `changeref`. MAD-X applies the three
  angles in an order of its own, so the correspondence is exact only to first order
  in the angles. A changeref has no length, and `flexible`, `ref_coords` and
  `user_sets_length` have no equivalent.

### ReferenceP --> Beamline Properties
- species_ref --> species_ref
- pc_ref --> pc_ref
- E_tot_ref --> E_ref
- time_ref --> none
- location --> none
- Note (MAD-X): the group becomes the `BEAM` command: species_ref --> particle
  (positron, electron, proton, antiproton, posmuon, negmuon; anything else has to
  be given its mass and charge by hand), pc_ref --> `pc` and E_tot_ref --> `energy`,
  both in GeV rather than eV.

### ReferenceChangeP --> Beamline Properties
- extra_dtime_ref --> none
- dE_ref --> dE_ref
- E_tot_ref --> E_ref
- species_ref --> species_ref
- Note (MAD-X): MAD-X takes the reference energy from the `BEAM` command and cannot
  change it in mid-line, so the whole group is an error.

### RFP --> RFParams
- frequency --> rate, rate_meaning = false
- harmon --> rate, if rate_meaning = true
- if neither frequency or harmon exist, set rate_meaning = -1
- voltage --> voltage
- gradient --> none
- phase --> phi0 (both in radians)
- multipass_phase --> none
- cavity_type --> traveling_wave
    - STANDING_WAVE --> false
    - TRAVELING_WAVE --> true
- num_cells --> tracking_method = SaganCavity(num_cells)
- zero_phase --> zero_phase
    - ACCELERATING --> Accelerating
    - BELOW_TRANSITION --> BelowTransition
    - ABOVE_TRANSITION --> AboveTransition

- Note (MAD-X): frequency --> `freq` in MHz, harmon --> `harmon`,
  voltage --> `volt` in MV, gradient --> `volt = gradient * L_active`.
- Note (Bmad): gradient --> `voltage = gradient * L_active` (`L_active` defaulting to the
  element length), Bmad's `gradient` being a dependent attribute.
- Note (Bmad): phase --> `phi0 = phase / twopi` and multipass_phase -->
  `phi0_multipass = multipass_phase / twopi`, Bmad counting RF phases in turns.
- Note (MAD-X): phase --> `lag` in turns, offset by what `zero_phase` measures from.
  MAD-X's zero lag is the zero crossing half a period from the one Bmad and PALS call
  the stable point above transition (`phi0 = lag + 0.5`), so
  ABOVE_TRANSITION --> `lag = phase/2pi - 0.5`, BELOW_TRANSITION --> `lag = phase/2pi`,
  and ACCELERATING --> `lag = phase/2pi - 0.25`.
- Note (MAD-X): a TRAVELING_WAVE cavity is MAD-X's `twcavity`, which only PTC
  tracks, so it is translated as an `rfcavity` with a warning; `multipass_phase`,
  `num_cells`, `L_active` and `dE_ref` have no equivalent.

### SolenoidP --> BMultipoleParams
- Ksol --> Ksol
- Bsol --> Bsol
- Note (MAD-X): Ksol --> `ks`, Bsol --> `ks = Bsol / pals_brho`. A solenoid of zero
  length also needs MAD-X's integrated `ksi`, which PALS does not state.

### TrackingP --> UniversalParams.tracking_method
- Note (MAD-X): tracking parameters are program specific by design and are skipped.

### TwissP --> initial conditions
- Note (Bmad): the group becomes `beginning[...]` settings, which PALS and Bmad
  name alike bar the coupling matrix's underscore (`cmat11` --> `cmat_11`).
- Note (MAD-X): the group becomes a `BETA0` block. beta_a --> betx,
  beta_b --> bety, alpha_a --> alfx, alpha_b --> alfy, phi_a --> `mux = phi_a/2pi`
  (MAD-X counts the phase in turns), eta_x --> `dx = eta_x / beam->beta`, and
  likewise eta_y, etap_x, etap_y.
- Note (MAD-X): PALS states the Twiss parameters in the a/b normal modes and MAD-X
  in the x/y planes, which are the same thing only when the lattice is uncoupled.
  The coupling itself is Bmad's C matrix here and MAD-X's R matrix there, which are
  different parametrizations, so `cmatNN` is not translated; nor is `deta_x_ds`,
  MAD-X having no dispersion derivative.

### Lattices
- Beamlines --> Beamlines
- To be added: Lattices in PALS --> Lattices
- Note (MAD-X): a BeamLine becomes a `line` and a Lattice becomes the `use, period`
  statement. A leading `BeginningEle` is dropped — it carries the reference parameters,
  which become the `BEAM` command — whether the line spells it out or names it; a line
  that does not begin with one keeps every element it has. MAD-X expands one sequence
  at a time, so only the first branch is used and the rest are commented out. MAD-X has
  no geometry attribute — whether a branch closes on itself is decided by how it is
  used — so `periodic` becomes a comment.

### Constants and variables --> Bmad `name = value` definitions
- `constants:` / `variables:` list entry --> `name = value`
- `kind: constant` / `kind: variable` definition --> `name = value`
- Note: Bmad draws no constant/variable distinction, so both translate the same way.
- Note: definitions directly under the `PALS` node are translated as well as the facility's own.
- Note: a definition with no `value` takes the PALS default of zero.
- Note: the definitions are written, in the order the PALS file gives them, ahead of every
  other section of the Bmad file. Bmad, unlike PALS, resolves a name against what the file
  has defined *above* the point of use.
- Not translated to SciBmad.
- Note (MAD-X): the same in every respect, MAD-X also resolving a name against what
  is defined above the point of use. A MAD-X variable is a value and nothing else, so
  `absolute_error` and `relative_error` are reported.
- Note (MAD-X): an expression is carried across as it stands — MAD-X's arithmetic and
  ordinary functions are PALS' as well — but two things in one are reported rather
  than rewritten, expression translation being an open item for all the translators:
  the particle-data functions (`mass_of`, `charge_of`, `anomalous_moment_of`), which
  MAD-X does not have, and the predefined constants MAD-X spells differently
  (`c_light` --> `clight`, `e_charge` --> `qelect`, `r_electron` --> `erad`,
  `r_proton` --> `prad`, `mu_0_vac` --> `amu0`) or does not have at all (`h_planck`,
  `hbar`, `k_boltzmann`, `eps_0_vac`, `fine_structure`, `n_avogadro`,
  `classical_radius_factor`). `pi` is the one they agree on.

### Controllers --> Bmad overlays and groups
- `control_type: ABSOLUTE` --> an `overlay`, which sets its slaves' attributes.
- `control_type: RELATIVE` --> a `group`, which adds to them.
- variables --> the `var = {...}` list and its initial values.
- controls --> `ele[attribute]: expression`, scaled the same way the element
  attribute was: by the length when the attribute and the PALS parameter disagree
  about integration, and by the `1/n!` of the multipole convention. An `overlay`
  driving a bend's `DG` also has the reference bend subtracted, that attribute
  being measured from it rather than from zero; a `group`, which varies rather than
  sets, does not.

### Controllers --> MAD-X variables and deferred assignments
- variables --> `name = value;` (global MAD-X variables)
- controls --> `ele->attribute := expression;`
- MetaP --> comment lines above the definitions
- Note (MAD-X): a PALS controller owns its variables, so `ps1>cur` and `ps2>cur` are
  two independent knobs. MAD-X has one namespace for the whole file, so a variable
  whose bare name another controller or a constant also claims is written as
  `<controller>__<variable>`, and the expressions using it are rewritten to match. A
  name nobody else claims keeps its bare form.
- Note (MAD-X): `control_type: ABSOLUTE` sets the attribute outright, which is what a
  deferred assignment does. `RELATIVE` is a knob: the slave keeps the value the lattice
  gave it and moves by how far the knob has turned *from where it started*, so the
  assignment is `ele->attr := <element's own value> + (expr) - (expr at the variables'
  initial settings)`. MAD-X forbids the circular `ele->k1 := ele->k1 + dk`, so the
  element's own value is read back out of the element definition; and the last term —
  which a Bmad `group` keeps track of by itself — is left out only when it can be shown
  to come to zero, which for a knob resting at zero it does.
- Note (MAD-X): the expression is scaled the same way the element attribute was —
  by the length when the attribute and the PALS parameter disagree about
  integration, by `1/pals_brho` when the parameter is a field, by -1 for an `hkick`.
- Note (MAD-X): a control target may name its element by kind, as `{kind}::{name}`;
  the qualifier is checked against the element found and then dropped. A `>>` or `>>>`
  qualifier naming the BeamLine or Lattice an element is reached through has no MAD-X
  equivalent and is an error.
- Note (MAD-X): a control aimed at a multipole array entry has no target — MAD-X
  cannot name one entry of a `knl` — and is an error, as is one aimed at a tilted
  multipole or selecting its slaves by pattern.

### Controllers --> Xsuite variables and deferred expressions
- variables --> `env['name'] = value` (global Xsuite variables)
- controls --> `env['ele'].attribute = 'expression'`, or
  `env['ele'].knl[n] = 'expression'` for an order the element has no attribute for
  (the array is padded out to reach it)
- MetaP --> comment lines above the definitions
- Note (Xsuite): as for MAD-X, a variable whose bare name another controller or a
  constant also claims is written as `<controller>__<variable>`; a `RELATIVE`
  controller's expression is the element's own value plus the control expression
  less that expression at the variables' initial settings; and the expression is
  scaled by the length, or divided by `pals_brho`, as the attribute was. Unlike
  MAD-X, Xsuite can name one entry of a multipole array. A pattern, a `>>`
  qualifier or a tilted multipole is an error.

### Controllers --> SciBmad Controllers
- Each control becomes an `(ele, :property) => (ele; vars...) -> expression` pair,
  SciBmad keeping the PALS parameter names so that only the group prefix is dropped
  and nothing needs rescaling.
- `RELATIVE` adds its expression to the value the element already carries;
  `ABSOLUTE` replaces it.

### TODO
- translating expression
- names of fundamental constants
- names of functions (tan)
- sinc --> sincu
- MAD-X: `ElectricMultipoleP`, `FloorP`, `ForkP` and `TaylorP`
