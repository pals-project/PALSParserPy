# Adapted from pals2cosy, copyright (c) 2026 Eremey Valetov.
# MIT licence: ../palsparserpy/COSY_COPYING.txt.
"""End-to-end checks: PALS lattice → COSYScript → COSY INFINITY transfer map.

Each test compares map coefficients computed by COSY with the value implied
by the PALS definition of the element. Skipped without COSY INFINITY.
"""

import math
import warnings

import pytest

import palsparserpy as pp
from palsparserpy._cosy_reference import C_LIGHT
from _cosy_fixtures import document
from _cosy_harness import requires_cosy, requires_sysca, run_cosy, compile_cosy


@pytest.fixture(scope="module")
def cosy_dir(tmp_path_factory):
    return compile_cosy(str(tmp_path_factory.mktemp("cosy")))


@pytest.fixture
def pals_file(tmp_path):
    counter = iter(range(1000))
    def write(text):
        path = tmp_path / f"lattice{next(counter)}.pals.yaml"
        path.write_text(text)
        return path
    return write


pytestmark = requires_cosy

PC = 1.0e9           # reference momentum [eV/c]
BRHO = PC / C_LIGHT  # proton rigidity [T m]


def _script(pals_file, elements, species="proton", order=3, pc=PC, **convert_kwargs):
    lat = pp.parse_and_expand_pals(pals_file(document(elements, species=species, pc=pc)), problems="none")
    script = pp.pals_to_cosy(lat, computation_order=order, **convert_kwargs)
    line = lat.full_expanded["ring"]["branches"][0]["main"]["line"]
    mass = pp.evaluate_pals_expression(f'mass_of("{species}")')
    charge = pp.evaluate_pals_expression(f'charge_of("{species}")')
    bp = {"brho_tm": pc / (C_LIGHT * charge),
          "kinetic_energy_mev": pc**2 / (math.hypot(pc, mass) + mass) / 1e6}
    elements = [{"length": e.child(0)["length"].as_float() if "length" in e.child(0) else 0.0} for e in line]
    return bp, elements, script


def _run(script, cosy_dir, length):
    result = run_cosy(script, cosy_dir, timeout=120)
    assert not result.errors, result.errors
    assert result.scalars["SPOS"] == pytest.approx(length)
    return result


def _map(pals_file, cosy_dir, elements, species="proton", order=3, **convert_kwargs):
    bp, elems, script = _script(pals_file, elements, species, order, **convert_kwargs)
    return bp, _run(script, cosy_dir, sum(e["length"] for e in elems))


@pytest.mark.parametrize("species", ["proton", "electron", "muon", "#12C-1"])
def test_rigidity_matches_cosy(pals_file, cosy_dir, species):
    # COSY's reference rigidity is the one normalized strengths were converted
    # with, to rounding: RP gets the CODATA mass, not a COSY preset mass.
    bp, m = _map(pals_file, cosy_dir, "- d: {kind: Drift, length: 1.0}", species=species)
    assert m.scalars["CHIM"] == pytest.approx(bp["brho_tm"], rel=1e-12)
    assert abs(m.scalars["CHIM"]) == pytest.approx(BRHO, rel=1e-12)


def test_thick_quadrupole_from_gradient(pals_file, cosy_dir):
    _, m = _map(pals_file, cosy_dir,
                "- q: {kind: Quadrupole, length: 0.5, MagneticMultipoleP: {Bn1: 2.0}}")
    k = math.sqrt(2.0 / BRHO)
    assert m.coeff(0, 1) == pytest.approx(math.cos(k * 0.5), rel=1e-6)
    assert m.coeff(1, 1) == pytest.approx(-k * math.sin(k * 0.5), rel=1e-6)
    assert m.coeff(2, 0, 0, 1) == pytest.approx(math.cosh(k * 0.5), rel=1e-6)


def test_normalized_quadrupole_focuses_electrons(pals_file, cosy_dir):
    _, m = _map(pals_file, cosy_dir,
                "- q: {kind: Quadrupole, length: 0.5, MagneticMultipoleP: {Kn1: 0.8}}",
                species="electron")
    k = math.sqrt(0.8)
    assert m.coeff(1, 1) == pytest.approx(-k * math.sin(k * 0.5), rel=1e-6)


@pytest.mark.parametrize("order, kind", [(2, "Sextupole"), (3, "Octupole"),
                                         (4, "Multipole"), (5, "Multipole"),
                                         (6, "Multipole")])
def test_multipole_kick_matches_pals_definition(pals_file, cosy_dir, order, kind):
    # x'' = -(K_N / N!) x^N on the midplane; the order-N coefficient of a
    # pure multipole is exactly -(K_N / N!) L.
    k_n, length = 3.0, 0.2
    _, m = _map(pals_file, cosy_dir,
                f"- m: {{kind: {kind}, length: {length}, MagneticMultipoleP: {{Kn{order}: {k_n}}}}}",
                order=order)
    assert m.coeff(1, order) == pytest.approx(-k_n / math.factorial(order) * length, rel=1e-9)


def test_skew_quadrupole_sign(pals_file, cosy_dir):
    # Bs1: By = -Bs1 y, Bx = Bs1 x, so a' = +Ks1 y and b' = +Ks1 x (thin lens).
    _, m = _map(pals_file, cosy_dir,
                "- q: {kind: Quadrupole, length: 0.01, MagneticMultipoleP: {Ks1: 2.0}}")
    assert m.coeff(1, 0, 0, 1) == pytest.approx(2.0 * 0.01, rel=1e-3)
    assert m.coeff(3, 1) == pytest.approx(2.0 * 0.01, rel=1e-3)


def test_tilted_quadrupole_equals_skew(pals_file, cosy_dir):
    _, tilted = _map(pals_file, cosy_dir,
                     "- q: {kind: Quadrupole, length: 0.3, MagneticMultipoleP: "
                     "{Kn1: 1.5, tilt1: 0.7853981633974483}}")
    _, skew = _map(pals_file, cosy_dir,
                   "- q: {kind: Quadrupole, length: 0.3, MagneticMultipoleP: {Ks1: -1.5}}")
    for row, exps in ((1, (0, 0, 1)), (3, (1,)), (0, (1,)), (2, (0, 1, 1))):
        assert tilted.coeff(row, *exps) == pytest.approx(skew.coeff(row, *exps),
                                                         rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("kind, order, command", [("Quadrupole", 1, "MQ"),
                                                  ("Sextupole", 2, "MH")])
def test_tilt_fold_equals_rotated_frame(pals_file, cosy_dir, kind, order, command):
    # The (N+1) tilt fold into normal and skew components must equal COSY's
    # own frame rotation RA(tilt) M RA(-tilt) of the untilted multipole.
    tilt, element = 0.4, f"- m: {{kind: {kind}, length: 0.3, MagneticMultipoleP: {{Kn{order}: 2.0"
    _, _, folded = _script(pals_file, element + f", tilt{order}: {tilt}}}}}")
    _, _, plain = _script(pals_file, element + "}}")
    line = next(l for l in plain.splitlines() if l.strip().startswith(command))
    deg = math.degrees(tilt)
    rotated = plain.replace(line, f"    RA {deg} ;\n{line}\n    RA {-deg} ;")
    a, b = _run(folded, cosy_dir, 0.3), _run(rotated, cosy_dir, 0.3)
    monomials = [(1,), (0, 1), (0, 0, 1), (0, 0, 0, 1), (2,), (1, 0, 1), (0, 0, 2)]
    for row in range(4):
        for exps in monomials:
            assert a.coeff(row, *exps) == pytest.approx(b.coeff(row, *exps), rel=1e-9, abs=1e-12)
    assert abs(a.coeff(1, order - 1, 0, 1)) > 1e-3  # (a|x^(N-1) y): a skew component


def test_thin_quadrupole(pals_file, cosy_dir):
    _, m = _map(pals_file, cosy_dir,
                "- q: {kind: Quadrupole, MagneticMultipoleP: {Kn1L: 0.1}}\n"
                "- d: {kind: Drift, length: 1.0}")
    assert m.coeff(1, 1) == pytest.approx(-0.1, rel=1e-6)
    assert m.coeff(0, 1) == pytest.approx(1 - 0.1, rel=1e-6)


@pytest.mark.parametrize("angle", [0.3, -0.3])
def test_rectangular_bend_has_no_horizontal_focusing(pals_file, cosy_dir, angle):
    _, m = _map(pals_file, cosy_dir,
                f"- b: {{kind: Bend, length: 1.0, BendP: {{angle_ref: {angle}, "
                f"e1_rect: 0.0, e2_rect: 0.0}}}}")
    rho = 1.0 / abs(angle)
    assert m.coeff(0, 1) == pytest.approx(1.0, abs=1e-12)
    assert m.coeff(0, 0, 1) == pytest.approx(rho * math.sin(abs(angle)), rel=1e-9)
    # vertical: entrance-edge kick -tan(e)/rho, then the 1 m body
    assert m.coeff(2, 0, 0, 1) == pytest.approx(1 - math.tan(abs(angle) / 2) / rho,
                                                rel=1e-9)


@pytest.mark.parametrize("angle", [0.3, -0.3])
def test_bend_direction(pals_file, cosy_dir, angle):
    # A positive angle bends toward -x, so a higher-momentum particle, bent
    # less, leaves at +x: sign((x|delta)) = sign(angle_ref).
    _, m = _map(pals_file, cosy_dir, f"- b: {{kind: Bend, length: 1.0, BendP: {{g_ref: {angle}}}}}")
    dispersion = (1 - math.cos(angle)) / angle
    assert m.coeff(0, 0, 0, 0, 0, 0, 1) == pytest.approx(
        dispersion * _dp_over_p_per_delta(), rel=1e-6)


@pytest.mark.parametrize("angle", [0.3, -0.3])
def test_sector_bend(pals_file, cosy_dir, angle):
    _, m = _map(pals_file, cosy_dir,
                f"- b: {{kind: Bend, length: 1.0, BendP: {{g_ref: {angle}}}}}")
    assert m.coeff(0, 1) == pytest.approx(math.cos(angle), rel=1e-9)
    assert m.coeff(2, 0, 0, 1) == pytest.approx(1.0, abs=1e-12)


def test_bend_from_chord(pals_file, cosy_dir):
    _, m = _map(pals_file, cosy_dir,
                "- b: {kind: Bend, BendP: {g_ref: 0.25, L_chord: 1.9}}")
    angle = 2 * math.asin(0.25 * 1.9 / 2)
    assert m.scalars["SPOS"] == pytest.approx(angle / 0.25)
    assert m.coeff(0, 1) == pytest.approx(math.cos(angle), rel=1e-9)


@pytest.mark.parametrize("tilt", [math.pi / 2, -math.pi / 2])
def test_vertical_bend_from_tilt(pals_file, cosy_dir, tilt):
    # tilt_ref = +pi/2 rotates the bend direction -x into -y (downward), and
    # -pi/2 into +y; with the bend direction fixed by test_bend_direction,
    # this pins the sense of the RA rotation.
    _, m = _map(pals_file, cosy_dir,
                f"- b: {{kind: Bend, length: 1.0, BendP: {{g_ref: 0.3, tilt_ref: {tilt}}}}}")
    dispersion = (1 - math.cos(0.3)) / 0.3
    assert abs(m.coeff(0, 0, 0, 0, 0, 0, 1)) < 1e-12
    assert m.coeff(2, 0, 0, 1) == pytest.approx(math.cos(0.3), rel=1e-9)
    assert m.coeff(0, 1) == pytest.approx(1.0, abs=1e-12)
    assert m.coeff(2, 0, 0, 0, 0, 0, 1) == pytest.approx(
        math.copysign(dispersion, tilt) * _dp_over_p_per_delta(), rel=1e-6)


def test_spin_anomaly_reaches_cosy(pals_file, cosy_dir):
    # With twiss=True the programme declares a local G0 for the Twiss gamma, so the
    # anomalous moment must reach COSY's global G0 through RPS.
    _, _, script = _script(pals_file, """\
- qf: {kind: Quadrupole, length: 0.2, MagneticMultipoleP: {Kn1: 1.0}}
- d1: {kind: Drift, length: 1.0}
- qd: {kind: Quadrupole, length: 0.2, MagneticMultipoleP: {Kn1: -1.0}}
- d2: {kind: Drift, length: 1.0}""", twiss=True)
    script = script.replace("\nRUN ;\n", "\nRUN ;\nWRITE 6 '### G0' ; WRITE 6 G0 ;\n")
    result = run_cosy(script, cosy_dir)
    assert not result.errors, result.errors
    lines = [line.strip() for line in result.stdout.splitlines()]
    g0 = float(lines[lines.index("### G0") + 1].replace("D", "E"))
    assert g0 == pytest.approx(pp.evaluate_pals_expression('anomalous_moment_of("proton")'), rel=1e-12)


def _hard_edge_vertical(g, length, e, edge_int):
    """(y|y) and (b|y) of a sector bend between hard edges with the MAD/Bmad
    fringe-field correction psi = 2 g edge_int (1 + sin^2 e) / cos e."""
    psi = 2 * g * edge_int * (1 + math.sin(e) ** 2) / math.cos(e)
    kick = -g * math.tan(e - psi)
    return 1 + length * kick, 2 * kick + length * kick ** 2


@pytest.mark.parametrize("fr, tolerance", [pytest.param(1, 0.03, marks=requires_sysca),
                                           (3, 0.01)])
def test_bend_fringe_integral(pals_file, cosy_dir, fr, tolerance):
    # With the half-gap derived from edge_int, COSY's soft-edge bend gives the
    # first-order vertical fringe-field correction of the hard-edge model.
    g, length, e, edge_int = 0.5, 1.0, 0.1, 0.01
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _, m = _map(pals_file, cosy_dir,
                    f"- b: {{kind: Bend, length: {length}, BendP: {{g_ref: {g}, e1: {e}, "
                    f"e2: {e}, edge1_int: {edge_int}, edge2_int: {edge_int}}}}}",
                    order=1, fringe_field_order=fr)
    expected = _hard_edge_vertical(g, length, e, edge_int)
    without = _hard_edge_vertical(g, length, e, 0.0)
    for row, value, hard in zip((2, 3), expected, without):
        assert m.coeff(row, 0, 0, 1) == pytest.approx(value, abs=tolerance * abs(value - hard))


def _dp_over_p_per_delta():
    """COSY's delta is the relative kinetic-energy deviation: dp/p = delta E K / (pc)^2."""
    m = pp.evaluate_pals_expression('mass_of("proton")') * 1e-6
    pc = PC / 1e6
    e_tot = math.hypot(pc, m)
    return (e_tot - m) * e_tot / pc ** 2


@pytest.mark.parametrize("species", ["proton", "electron"])
def test_rf_on_crest_accelerates(pals_file, cosy_dir, species):
    bp, m = _map(pals_file, cosy_dir,
                 "- c: {kind: RFCavity, length: 0.6, RFP: {voltage: 2.0e6, "
                 "frequency: 1.3e9, dE_ref: 2.0e6}}", species=species)
    assert m.scalars["E0"] == pytest.approx(bp["kinetic_energy_mev"] + 2.0, rel=1e-12)


def test_rf_zero_crossing_below_transition(pals_file, cosy_dir):
    # Below transition the stable phase gives late particles (l < 0) more energy.
    bp, m = _map(pals_file, cosy_dir,
                 "- c: {kind: RFCavity, length: 0.6, RFP: {voltage: 2.0e6, "
                 "frequency: 1.3e9, zero_phase: BELOW_TRANSITION}}")
    assert m.scalars["E0"] == pytest.approx(bp["kinetic_energy_mev"], rel=1e-12)
    assert m.coeff(5, 0, 0, 0, 0, 1) < 0


def _hard_edge_solenoid(field, length):
    k = field / (2 * BRHO)
    c, s = math.cos(k * length), math.sin(k * length)
    return {"x|x": c * c, "x|y": s * c, "a|x": -k * s * c}


def test_solenoid_short_fringe_matches_hard_edge(pals_file, cosy_dir):
    # With a fringe length D much shorter than L, COSY's tanh-edged CMS
    # approaches the hard-edge PALS solenoid; the remaining focusing
    # deficit is about D/L.
    fringe, length = 5e-4, 1.0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _, m = _map(pals_file, cosy_dir,
                    f"- s: {{kind: Solenoid, length: {length}, SolenoidP: {{Bsol: 2.0}}}}",
                    solenoid_fringe=fringe)
    hard = _hard_edge_solenoid(2.0, length)
    assert m.coeff(0, 1) == pytest.approx(hard["x|x"], rel=1e-4)
    assert m.coeff(0, 0, 0, 1) == pytest.approx(hard["x|y"], rel=1e-4)
    assert m.coeff(1, 1) == pytest.approx(hard["a|x"], rel=2 * fringe / length)


def test_solenoid_default_fringe_warns_and_matches_estimate(pals_file, cosy_dir):
    # The default fringe length is the bore radius; the warning quotes the
    # D/L focusing deficit that COSY's map shows.
    length, fringe = 0.5, 0.0135
    with pytest.warns(UserWarning, match=r"focusing is about 2\.70% weaker"):
        _, m = _map(pals_file, cosy_dir,
                    f"- s: {{kind: Solenoid, length: {length}, SolenoidP: {{Bsol: 3.0}}}}")
    hard = _hard_edge_solenoid(3.0, length)
    assert m.coeff(1, 1) == pytest.approx(hard["a|x"] * (1 - fringe / length), rel=1e-3)
    assert m.coeff(1, 1) != pytest.approx(hard["a|x"], rel=1e-2)


def test_slow_ion_rigidity_avoids_energy_cancellation(pals_file, cosy_dir):
    bp, m = _map(pals_file, cosy_dir, "- d: {kind: Drift, length: 1}", species="#12C-1", pc=1e7)
    assert m.scalars["CHIM"] == pytest.approx(bp["brho_tm"], rel=1e-12)


@pytest.mark.parametrize("species", ["proton", "electron"])
def test_rf_nonzero_phase_half_crest_gain(pals_file, cosy_dir, species):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        bp, m = _map(pals_file, cosy_dir,
                     f"- c: {{kind: RFCavity, RFP: {{voltage: 2e6, frequency: 1.3e9, phase: {math.pi/3}, dE_ref: 1e6}}}}",
                     species=species)
    assert m.scalars["E0"] == pytest.approx(bp["kinetic_energy_mev"] + 1.0, rel=1e-12)


@pytest.mark.parametrize("zero,sign", [("BELOW_TRANSITION", -1), ("ABOVE_TRANSITION", 1)])
def test_electron_rf_zero_crossing_slope(pals_file, cosy_dir, zero, sign):
    bp, m = _map(pals_file, cosy_dir,
                 f"- c: {{kind: RFCavity, RFP: {{voltage: 2e6, frequency: 1.3e9, zero_phase: {zero}}}}}",
                 species="electron")
    assert m.scalars["E0"] == pytest.approx(bp["kinetic_energy_mev"], rel=1e-12)
    assert sign * m.coeff(5, 0, 0, 0, 0, 1) > 0


def test_ten_normal_and_skew_orders_fit_cosy_input(pals_file, cosy_dir):
    # All coefficients nonzero used to produce a line over COSY's 512-column
    # limit. This checks real parsing/execution, not just emitted line lengths.
    fields = ", ".join(f"{kind}{order}: 0.12345678912345678"
                       for order in range(1, 11) for kind in ("Kn", "Ks"))
    _map(pals_file, cosy_dir,
         "- m: {kind: Multipole, length: 0.2, MagneticMultipoleP: {" + fields + "}}", order=1)
