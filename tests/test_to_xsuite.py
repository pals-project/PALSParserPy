"""Tests of the PALS to Xsuite translator.

Most of these look at the text of the Python script the translator writes. The
ones at the end run that script with ``xtrack`` and check the lattice it builds;
they are skipped where ``xtrack`` is not installed.
"""

import math
from textwrap import dedent

import pytest

from palsparserpy import parse_file, pals_to_xsuite, write_xsuite_file

# A small but structurally complete PALS lattice: a BeginningEle (reference,
# Twiss and particle-start settings), a couple of ordinary elements, a BeamLine
# that spells its beginning element out, and a Lattice with one branch.
TRANSLATE_FIXTURE = dedent("""
  PALS:
    facility:
      - beg:
          kind: BeginningEle
          length: 0
          ReferenceP:
            species_ref: electron
            pc_ref: 3E6
          TwissP:
            beta_a: 10
            beta_b: 5
            alpha_a: 0.5
            eta_x: 0.25
            phi_a: 0.5
            cmat11: 0.1
            deta_x_ds: 0.2
          ParticleP:
            x: 1
            px: 4
            z: 0.5
            pz: 1e-3
            spin_x: 1
      - d1:
          kind: Drift
          length: 100
          ApertureP:
            shape: RECTANGULAR
            x_min: -0.03
            x_max: 0.05
            y_min: -0.01
            y_max: 0.02
      - q1:
          kind: Quadrupole
          length: 0.5
          MagneticMultipoleP:
            Kn1: 0.25
      - ring:
          kind: BeamLine
          line:
            - beg:
                kind: BeginningEle
                ReferenceP:
                  species_ref: electron
                  pc_ref: 3E6
            - d1
            - q1
      - lat:
          kind: Lattice
          branches:
            - ring
  """)

# One element of each kind the translator handles, with the parameters whose
# Xsuite spelling, units or sign differ from PALS'.
ELEMENT_FIXTURE = dedent("""
  PALS:
    facility:
      - constants:
          kq: 0.3
          ang: pi/20
      - beg:
          kind: BeginningEle
          ReferenceP:
            species_ref: electron
            pc_ref: 3E9
      - q1:
          kind: Quadrupole
          length: 0.5
          MagneticMultipoleP:
            Kn1: kq
            Kn2L: 0.02
            tilt2: 0.1
          BodyShiftP:
            x_offset: 1e-4
            x_rot: 2e-4
            y_rot: 3e-4
            z_rot: 4e-4
          ApertureP:
            shape: ELLIPTICAL
            location: BOTH_ENDS
            x_width: 0.04
            x_center: 0.001
            y_width: 0.02
      - qthin:
          kind: Quadrupole
          MagneticMultipoleP:
            Kn1L: 0.1
      - qb:
          kind: Quadrupole
          length: 0.5
          MagneticMultipoleP:
            Bn1: 2.0
      - s1:
          kind: Sextupole
          length: 0.2
          MagneticMultipoleP:
            Ks2L: 1.5
      - b1:
          kind: Bend
          length: 2
          BendP:
            angle_ref: ang
            e1_rect: 0.01
            e2: 0.02
            tilt_ref: 0.1
            edge1_int: 0.05
            h1: 0.3
          MagneticMultipoleP:
            Kn1: 0.1
            Ks0: 0.001
      - b2:
          kind: Bend
          length: 1
          BendP:
            g_ref: 0.3
            Kn0_from_g_ref: false
      - b3:
          kind: Bend
          length: 1
          BendP:
            g_ref: 0.3
          MagneticMultipoleP:
            Kn0: 0.31
      - b4:
          kind: Bend
          length: 1
          BendP:
            g_ref: 0.3
          MagneticMultipoleP:
            Kn0: 0.3
      - kick:
          kind: Kicker
          length: 0.1
          MagneticMultipoleP:
            Kn0L: 1e-4
            Ks0L: 2e-4
      - rf1:
          kind: RFCavity
          length: 1.3
          RFP:
            frequency: 5E8
            gradient: 1E6
            phase: 0.1
            zero_phase: ABOVE_TRANSITION
      - rf2:
          kind: RFCavity
          RFP:
            harmon: 100
            voltage: 1E6
            num_cells: 5
      - sol:
          kind: Solenoid
          length: 1
          SolenoidP:
            Bsol: 1.5
      - pt:
          kind: Patch
          PatchP:
            x_offset: 0.01
            y_rot: 0.002
      - pr:
          kind: Patch
          PatchP:
            z_rot: 0.5
      - m1:
          kind: Marker
          MetaP:
            description: a marker
      - d1:
          kind: Drift
          length: 1
      - ring:
          kind: BeamLine
          line:
            - beg
            - q1
            - d1
            - qthin
            - qb
            - s1
            - b1
            - b2
            - b3
            - b4
            - kick
            - rf1
            - rf2
            - sol
            - pt
            - pr
            - m1
      - lat:
          kind: Lattice
          branches:
            - ring:
                periodic: true
  """)

# Line items: a subline repeated and reversed, a clone, an element defined in
# place, a Placeholder, and the settings Xsuite has no room for.
LINE_FIXTURE = dedent("""
  PALS:
    facility:
      - beg:
          kind: BeginningEle
          ReferenceP:
            species_ref: proton
            E_tot_ref: 2E10
      - q1:
          kind: Quadrupole
          length: 0.5
          ApertureP:
            shape: RECTANGULAR
            location: EXIT_END
            x_max: 0.05
      - d1:
          kind: Drift
          length: 1
      - ph:
          kind: Placeholder
      - ring:
          kind: BeamLine
          line:
            - beg
            - cell:
                repeat: 2
            - cell:
                repeat: -1
            - q2:
                inherit: q1
            - q3:
                kind: Quadrupole
                length: 0.2
                direction: -1
            - ph
      - cell:
          kind: BeamLine
          line:
            - q1
            - d1
      - other:
          kind: BeamLine
          line:
            - beg2:
                kind: BeginningEle
                ReferenceP:
                  species_ref: positron
                  pc_ref: 1E9
            - d1
      - lat1:
          kind: Lattice
          branches:
            - other
      - lat2:
          kind: Lattice
          branches:
            - ring:
                periodic: true
      - use: lat2
  """)

# Two controllers, one ABSOLUTE and one RELATIVE resting where its expression
# does not come to zero, which both own a variable called `kq`.
CONTROLLER_FIXTURE = dedent("""
  PALS:
    facility:
      - beg:
          kind: BeginningEle
          ReferenceP:
            species_ref: electron
            pc_ref: 3E9
      - q1:
          kind: Quadrupole
          length: 0.5
          MagneticMultipoleP:
            Kn1: 0.25
      - s1:
          kind: Sextupole
          length: 0.2
          MagneticMultipoleP:
            Ks2L: 1.5
      - m1:
          kind: Multipole
          MagneticMultipoleP:
            Kn1L: 0.7
      - ring:
          kind: BeamLine
          line:
            - beg
            - q1
            - s1
            - m1
      - lat:
          kind: Lattice
          branches:
            - ring
      - knob:
          kind: Controller
          MetaP:
            description: Model 800KL
          variables:
            kq: 0.3
          controls:
            - parameter: q1>MagneticMultipoleP.Kn1
              expression: 2*kq
            - parameter: m1>MagneticMultipoleP.Kn3L
              expression: -kq^2
      - bump:
          kind: Controller
          control_type: RELATIVE
          variables:
            kq: 0.1
          controls:
            - parameter: s1>MagneticMultipoleP.Ks2L
              expression: kq
  """)


def _parsed(tmp_path, text):
    """Write ``text`` to a file in ``tmp_path`` and return the parsed tree."""
    path = tmp_path / "fixture.pals.yaml"
    path.write_text(text)
    return parse_file(path)


def _script(tmp_path, text):
    """Translate the PALS lattice ``text`` and return the Python script written
    for it."""
    path = tmp_path / "fixture.pals_out.py"
    write_xsuite_file(pals_to_xsuite(_parsed(tmp_path, text)), path)
    return path.read_text()


def test_pals_to_xsuite_writes_a_python_script(tmp_path, capsys):
    out = _script(tmp_path, TRANSLATE_FIXTURE)
    reported = capsys.readouterr().out

    # The script builds an environment, and is valid Python.
    assert "import xtrack as xt\n\nenv = xt.Environment()\n" in out
    compile(out, "fixture.pals_out.py", "exec")

    # BeginningEle -> the reference particle. Xsuite states energies in eV, as
    # PALS does, so nothing is converted.
    assert "env.particle_ref = xt.Particles('electron', p0c=3E6)" in out

    # TwissP -> the initial conditions of an open twiss. Xsuite counts the phase
    # advance in turns, and measures the dispersion against delta, which is PALS'
    # pz.
    assert ("twiss_init = dict(betx=10, bety=5, alfx=0.5, dx=0.25, "
            "mux=0.07957747154594767)") in out
    assert "TwissP.cmat11 is Bmad's coupling matrix" in reported
    assert "TwissP.deta_x_ds has no Xsuite equivalent" in reported

    # ParticleP -> particle coordinates. Xsuite's zeta and delta are PALS' z and
    # pz, and it carries the spin too.
    assert ("particle_start = dict(x=1, px=4, zeta=0.5, delta=1e-3, spin_x=1)"
            in out)

    # An aperture is an element of its own in Xsuite, placed at the end of the
    # element it bounds, and a drift can have one.
    assert ("env.new('d1__aperture_entrance', xt.LimitRect, min_x=-0.03, "
            "max_x=0.05, min_y=-0.01, max_y=0.02)") in out
    assert "env.new('d1', xt.Drift, length=100)" in out
    assert "env.new('q1', xt.Quadrupole, length=0.5, k1=0.25)" in out

    # The leading BeginningEle is not an element of the line, and the aperture
    # comes along wherever d1 is named.
    assert ("env.new_line(name='ring', components=['d1__aperture_entrance', "
            "'d1', 'q1'])") in out
    # The line's own reference particle is the environment's, so it is not stated
    # twice.
    assert "env['ring'].particle_ref" not in out

    assert "'ring': env['ring'],\t# open" in out
    assert "line = branches['ring']" in out

    # Nothing states a field, so the rigidity is not defined.
    assert "pals_brho" not in out


def test_an_elements_multipoles_become_its_strengths_and_arrays(tmp_path):
    out = _script(tmp_path, ELEMENT_FIXTURE)

    # A quadrupole's own order is its k1, here an expression Xsuite keeps
    # deferred; any other order goes in the integrated knl/ksl arrays every Xsuite
    # magnet has, with its tilt rotated into a normal and a skew part.
    normal = 0.02 * math.cos(3 * 0.1)
    skew = -0.02 * math.sin(3 * 0.1)
    assert "env.new('q1', xt.Quadrupole, length=0.5, k1='kq', " in out
    assert f"knl=[0, 0, {normal!r}], ksl=[0, 0, {skew!r}])" in out

    # A zero-length quadrupole is a thin Multipole.
    assert "env.new('qthin', xt.Multipole, knl=[0, 0.1])" in out

    # Every Xsuite strength is normalized, so a field is divided by the signed
    # rigidity, which the script works out from the reference particle.
    assert "env.new('qb', xt.Quadrupole, length=0.5, k1='2.0 / pals_brho')" in out
    assert ("env['pals_brho'] = float(env.particle_ref.p0c[0] / (299792458.0 * "
            "env.particle_ref.q0))") in out
    assert "env.new('sol', xt.UniformSolenoid, length=1, ks='1.5 / pals_brho')" \
        in out

    # An integrated strength in a per-length attribute is divided by the length.
    assert "env.new('s1', xt.Sextupole, length=0.2, k2s=7.5)" in out

    # A kicker is a dipole multipole -- PALS' Kn0L deflects the way Xsuite's
    # knl[0] does -- and thick when it has a length.
    assert ("env.new('kick', xt.Multipole, length=0.1, isthick=True, "
            "knl=[0.0001], ksl=[0.0002])") in out


def test_a_bends_geometry_faces_and_field_become_xsuites(tmp_path, capsys):
    out = _script(tmp_path, ELEMENT_FIXTURE)
    reported = capsys.readouterr().out

    # The angle is carried as an expression; e1_rect picks up half of it, and the
    # exit face, given the sector way Xsuite measures against, comes straight
    # across. The fringe integral is split into Xsuite's fint and hgap, the tilt is
    # the rotation of the bend's frame, and a skew dipole goes in ksl.
    assert ("env.new('b1', xt.Bend, length=2, angle='ang', "
            "edge_entry_angle='0.01 + (0.5 * ang)', edge_exit_angle=0.02, "
            "rot_s_rad=0.1, edge_entry_fint=0.5, edge_entry_hgap=0.1, k1=0.1, "
            "ksl=[0.002])") in out
    assert "b1: BendP.h1, the pole face curvature, has no Xsuite equivalent" \
        in reported

    # Xsuite holds the bend's field (k0) apart from its geometry, as PALS does. A
    # bend with the reference geometry and no field has a k0 of zero; one with a
    # field of its own states it; one whose field is the reference one leaves k0
    # to follow the geometry, Xsuite's default.
    assert "env.new('b2', xt.Bend, length=1, angle=0.3, k0=0)" in out
    assert "env.new('b3', xt.Bend, length=1, angle=0.3, k0=0.31)" in out
    assert "env.new('b4', xt.Bend, length=1, angle=0.3)" in out


def test_rf_bodyshift_patch_and_aperture_take_xsuite_conventions(tmp_path,
                                                                capsys):
    out = _script(tmp_path, ELEMENT_FIXTURE)
    reported = capsys.readouterr().out

    # The phase is in radians in both, but Xsuite's zero is the zero crossing
    # below transition: ABOVE_TRANSITION adds pi and ACCELERATING pi/2. A gradient
    # becomes the voltage it comes to over the length.
    assert (f"env.new('rf1', xt.Cavity, length=1.3, frequency=5E8, "
            f"voltage=1300000.0, phase={0.1 + math.pi!r})") in out
    assert (f"env.new('rf2', xt.Cavity, harmonic=100, voltage=1E6, "
            f"phase={math.pi / 2!r})") in out
    assert "rf2: RFP.num_cells has no Xsuite equivalent" in reported

    # Xsuite's rot_x_rad turns s towards y, the opposite of PALS' right-handed
    # x_rot; the rotations are made about the element's centre, as in PALS.
    assert ("shift_x=1e-4, rot_x_rad=-0.0002, rot_y_rad=3e-4, "
            "rot_s_rad_no_frame=4e-4, rot_shift_anchor=0.25") in out

    # An elliptical aperture centred off the axis is shifted.
    assert ("env.new('q1__aperture_entrance', xt.LimitEllipse, a=0.02, b=0.01, "
            "shift_x=0.001)") in out
    assert "env.new('q1__aperture_exit', xt.LimitEllipse" in out

    # A patch that shifts and turns is a Translation followed by a Rotation; one
    # that only turns is a Rotation.
    assert "env.new('pt', xt.Translation, shift_x=0.01)" in out
    assert "env.new('pt__rotation', xt.Rotation, rot_y_rad=0.002)" in out
    assert "env.new('pr', xt.Rotation, rot_s_rad=0.5)" in out
    components = out[out.index("name='ring'"):]
    assert components.index("'pt'") < components.index("'pt__rotation'") < \
        components.index("'pr'")

    # MetaP becomes a comment, and the PALS constant pi, which Xsuite does not
    # define, is defined.
    assert "# description: a marker\nenv.new('m1', xt.Marker)" in out
    assert "env['pi'] = math.pi" in out
    assert "env['ang'] = 'pi / 20'" in out


def test_line_items_become_xsuite_line_arithmetic(tmp_path, capsys):
    out = _script(tmp_path, LINE_FIXTURE)
    reported = capsys.readouterr().out

    # A subline named before it is defined is defined first, as Python needs.
    assert out.index("name='cell'") < out.index("name='ring'")

    # A repeated subline is Xsuite's n * line, and a negative repeat reverses it;
    # a clone takes its parent's aperture with it; an element defined in place is
    # defined; a Placeholder is gone.
    assert ("env.new_line(name='ring', components=[2 * env['cell'], "
            "-env['cell'], 'q2', 'q2__aperture_exit', 'q3'])") in out
    assert "env.new('q2', 'q1')" in out
    assert "env.new('q2__aperture_exit', 'q1__aperture_exit')" in out
    assert "env.new('q3', xt.Quadrupole, length=0.2)" in out
    assert "'ph'" not in out
    assert "q3 is reversed in direction, which Xsuite cannot express" in reported

    # A one-sided rectangular aperture is bounded on the side that is set.
    assert "env.new('q1__aperture_exit', xt.LimitRect, max_x=0.05)" in out

    # A line with a reference particle of its own keeps it; the environment's is
    # the facility's BeginningEle.
    assert "env.particle_ref = xt.Particles('proton', energy0=2E10)" in out
    assert "env['other'].particle_ref = xt.Particles('positron', p0c=1E9)" in out

    # The script's `line` is the first branch of the lattice `use` names.
    assert "line = branches['ring']" in out
    assert "'ring': env['ring'],\t# closed" in out


def test_controllers_become_variables_and_deferred_expressions(tmp_path):
    out = _script(tmp_path, CONTROLLER_FIXTURE)

    # Both controllers own a `kq`, and Xsuite variables are global, so each is
    # prefixed with its controller.
    assert ("# Controller knob\n# description: Model 800KL\n"
            "env['knob__kq'] = 0.3\nenv['q1'].k1 = '2 * knob__kq'\n") in out

    # A control of an order with no attribute of its own reaches into the
    # multipole array, which is padded out to hold it. PALS reads -kq^2 as
    # -(kq^2) where Xsuite would read (-kq)^2, so the parentheses are written out.
    assert "env.new('m1', xt.Multipole, knl=[0, 0.7, 0, 0])" in out
    assert "env['m1'].knl[3] = '-(knob__kq ** 2)'" in out

    # A RELATIVE knob adds to the value the element already has, less its
    # expression where it started, scaled from the integrated Ks2L to per-length
    # k2s.
    assert ("env['s1'].k2s = '(7.5 + (5.0 * bump__kq)) - (5.0 * 0.1)'") in out


def test_expressions_follow_pals_precedence_and_functions(tmp_path, capsys):
    fixture = dedent("""
      PALS:
        facility:
          - constants:
              a: -2^2
              b: 2^3^2
              c: cot(a) + c_light
              d: modulo(a, 3)
          - d1:
              kind: Drift
              length: 1
    """)
    out = _script(tmp_path, fixture)
    assert "env['a'] = '-(2 ** 2)'" in out
    assert "env['b'] = '2 ** (3 ** 2)'" in out
    assert "env['c'] = '(1 / tan(a)) + c_light'" in out
    assert "env['c_light'] = 299792458.0" in out
    assert "`modulo` is a PALS function with no Xsuite equivalent" in \
        capsys.readouterr().out


def test_what_xsuite_cannot_express_is_reported(tmp_path, capsys):
    fork = TRANSLATE_FIXTURE.replace("kind: Drift", "kind: Fork")
    with pytest.raises(ValueError, match="No Fork elements in Xsuite"):
        pals_to_xsuite(_parsed(tmp_path, fork))

    girders = TRANSLATE_FIXTURE.replace(
        "\n  facility:", "\n  girders:\n    - g1:\n        elements: d1\n"
                        "  facility:")
    with pytest.raises(ValueError, match="Xsuite has no girders"):
        pals_to_xsuite(_parsed(tmp_path, girders))

    pattern = CONTROLLER_FIXTURE.replace("parameter: q1>", "parameter: q.*>")
    with pytest.raises(ValueError, match="selects slaves by pattern"):
        pals_to_xsuite(_parsed(tmp_path, pattern))

    # An extension block is reported once, however many elements carry it.
    extension = TRANSLATE_FIXTURE.replace(
        "PALS:\n", "PALS:\n  extension_labels:\n    names:\n"
                   "      BmadP: Bmad data\n").replace(
        "        length: 0.5\n", "        length: 0.5\n"
                                 "        BmadP:\n          key: x\n").replace(
        "        length: 100\n", "        length: 100\n"
                                "        BmadP:\n          key: y\n")
    _script(tmp_path, extension)
    reported = capsys.readouterr().out
    assert reported.count("`BmadP` is a PALS extension") == 1
    assert "BmadP has no Xsuite equivalent" not in reported

    # A name the facility does not define is reported, not left to fail when the
    # script runs.
    missing = TRANSLATE_FIXTURE.replace("          - q1\n",
                                        "          - q1\n          - q9\n")
    out = _script(tmp_path, missing)
    assert "`q9` is not defined in the facility" in capsys.readouterr().out
    assert "'q9'" not in out


# ---------------------------------------------------------------------------
# The script, run.

def _built(tmp_path, text):
    """Translate ``text``, run the script, and return the namespace it leaves."""
    pytest.importorskip("xtrack")
    namespace = {}
    exec(_script(tmp_path, text), namespace)
    return namespace


def test_the_script_builds_the_lattice_in_xtrack(tmp_path):
    ns = _built(tmp_path, ELEMENT_FIXTURE)
    env, line = ns["env"], ns["line"]

    # Every element is there, and the thick kicker takes up its length.
    lengths = 0.5 + 1 + 0.5 + 0.2 + 2 + 1 + 1 + 1 + 0.1 + 1.3 + 1
    assert line.get_length() == pytest.approx(lengths)

    # The rigidity is signed by the charge: an electron's is negative.
    assert env["pals_brho"] == pytest.approx(-3e9 / 299792458.0)
    assert env["qb"].k1 == pytest.approx(2.0 / env["pals_brho"])
    assert env["b1"].angle == pytest.approx(math.pi / 20)
    assert env["b1"].edge_entry_angle == pytest.approx(0.01 + math.pi / 40)

    # A deferred expression follows the variable it names.
    assert env["q1"].k1 == pytest.approx(0.3)
    env["kq"] = 0.5
    assert env["q1"].k1 == pytest.approx(0.5)


def test_the_scripts_controllers_drive_their_slaves(tmp_path):
    ns = _built(tmp_path, CONTROLLER_FIXTURE)
    env = ns["env"]
    assert env["q1"].k1 == pytest.approx(0.6)
    assert env["m1"].knl[3] == pytest.approx(-0.09)
    # The relative knob starts where the element was, and moves it from there.
    assert env["s1"].k2s == pytest.approx(7.5)
    env["bump__kq"] = 0.3
    assert env["s1"].k2s == pytest.approx(7.5 + 5 * 0.2)


def test_the_scripts_twiss_matches_the_initial_conditions(tmp_path):
    ns = _built(tmp_path, TRANSLATE_FIXTURE)
    xt, line = ns["xt"], ns["line"]
    tw = line.twiss(start=xt.START, end=xt.END, method="4d", **ns["twiss_init"])
    assert tw.betx[0] == pytest.approx(10)
    assert tw.alfx[0] == pytest.approx(0.5)
    assert tw.dx[0] == pytest.approx(0.25)
    # Through the 100 m drift the beta function grows as a parabola.
    gamma = (1 + 0.5 ** 2) / 10
    assert tw.rows["d1"].betx[0] == pytest.approx(10)
    assert tw.rows["q1"].betx[0] == pytest.approx(10 - 2 * 0.5 * 100 +
                                                  gamma * 100 ** 2)


@pytest.mark.parametrize("zero_phase, gain, slope", [
    ("ACCELERATING", 1.0, 0.0),
    ("BELOW_TRANSITION", 0.0, 1.0),
    ("ABOVE_TRANSITION", 0.0, -1.0)])
def test_the_rf_phase_zero_is_the_one_pals_names(tmp_path, zero_phase, gain,
                                                 slope):
    fixture = dedent(f"""
      PALS:
        facility:
          - beg:
              kind: BeginningEle
              ReferenceP:
                species_ref: proton
                pc_ref: 1E9
          - rf:
              kind: RFCavity
              RFP:
                frequency: 1E8
                voltage: 1E6
                zero_phase: {zero_phase}
          - ring:
              kind: BeamLine
              line:
                - beg
                - rf
    """)
    ns = _built(tmp_path, fixture)
    xt, cavity = ns["xt"], ns["env"]["rf"]
    try:
        # The reference particle (zeta = 0), and one arriving a little late.
        particles = xt.Particles(mass0=xt.PROTON_MASS_EV, q0=1, p0c=1e9,
                                 zeta=[0.0, -1e-4])
        energy = particles.energy.copy()
        cavity.track(particles)
    except Exception as err:     # no prebuilt kernels and no compiler
        pytest.skip(f"cannot track with xtrack here: {err}")
    gained = particles.energy - energy
    assert gained[0] == pytest.approx(gain * 1e6, abs=1e-3)
    # PALS' zero_phase says which way a late particle's energy goes.
    assert math.copysign(1, gained[1] - gained[0]) == math.copysign(1, slope) \
        or slope == 0
