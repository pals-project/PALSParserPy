"""COSY translation through the real native parser and its derived views."""

import math
import re
import warnings

import pytest

import palsparserpy as pp


from _cosy_fixtures import document


@pytest.fixture
def expand(tmp_path):
    def load(elements, **kwargs):
        path = tmp_path / "input.pals.yaml"
        path.write_text(document(elements, **kwargs))
        return pp.parse_and_expand_pals(path, problems="none")
    return load


def commands(program, command):
    return [line.strip().split(";")[0].split()[1:]
            for line in program.splitlines() if line.strip().startswith(command + " ")]


@pytest.mark.parametrize("species,charge", [("electron", -1), ("proton", 1), ("#12C-1", -1)])
def test_expanded_strength_is_not_counted_four_times(expand, species, charge):
    lat = expand("- q: {kind: Quadrupole, length: 0.5, MagneticMultipoleP: {Kn1: 1.2}}", species=species)
    assert not lat.problems
    before = lat.full_expanded.to_yaml_string()
    assert all(key in before for key in ("Kn1:", "Kn1L:", "Bn1:", "Bn1L:"))
    program = pp.pals_to_cosy(lat)
    length, pole, radius = map(float, commands(program, "MQ")[0])
    assert pole == pytest.approx(1.2 * 1e9 / (299792458 * charge) * radius, rel=1e-12)
    assert length == 0.5
    assert len(commands(program, "MQ")) == 1
    assert lat.full_expanded.to_yaml_string() == before
    rp = list(map(float, commands(program, "RP")[0]))
    mass = pp.evaluate_pals_expression(f'mass_of("{species}")')
    assert rp[0] == pytest.approx((math.hypot(1e9, mass) - mass) * 1e-6)
    assert rp[2] == charge


def test_repeat_inherit_set_and_order(expand):
    lat = expand("- cell: {repeat: 2}", definitions='''
        - q0: {kind: Quadrupole, length: 0.4, MagneticMultipoleP: {Kn1: 0.5}}
        - q: {inherit: q0, length: 0.2}
        - d: {kind: Drift, length: 0.3}
        - cell: {kind: BeamLine, line: [q, d]}
        - set: {parameter: q>MagneticMultipoleP.Kn1, value: 1.25}
    ''')
    assert not lat.problems
    program = pp.pals_to_cosy(lat)
    optics = [s.strip().split()[0] for s in program.splitlines() if re.match(r"\s+(MQ|DL) ", s)]
    assert optics == ["MQ", "DL", "MQ", "DL"]
    assert len(commands(program, "MQ")) == 2
    assert float(commands(program, "MQ")[0][1]) == pytest.approx(1.25 * 1e9 / 299792458 * 0.0135)


def test_branch_selection_and_duplicate_names(tmp_path):
    path = tmp_path / "branches.yaml"
    path.write_text('''PALS:
  facility:
    - a: {kind: BeamLine, line: [{d: {kind: Drift, length: 1, ReferenceP: {species_ref: proton, pc_ref: 1e9}}}]}
    - b: {kind: BeamLine, line: [{d: {kind: Drift, length: 2, ReferenceP: {species_ref: electron, pc_ref: 1e9}}}]}
    - ring: {kind: Lattice, branches: [a, b]}
    - use: ring
''')
    lat = pp.parse_and_expand_pals(path, problems="none")
    assert not lat.problems
    for branch in (None, "missing"):
        with pytest.raises(ValueError, match="available branches: a, b"):
            pp.pals_to_cosy(lat, branch_name=branch)
    assert commands(pp.pals_to_cosy(lat, branch_name="a"), "DL") == [["1.0"]]
    assert commands(pp.pals_to_cosy(lat, branch_name="b"), "DL") == [["2.0"]]


def test_parser_error_is_fatal_and_output_untouched(expand, tmp_path):
    lat = expand("- does_not_exist")
    errors = [p for p in lat.problems if p.severity == pp.PROBLEM_ERROR]
    assert errors
    target = tmp_path / "out.fox"
    target.write_text("keep me")
    with pytest.raises(ValueError, match="expansion errors") as caught:
        pp.write_cosy_file(pp.pals_to_cosy(lat), target)
    assert errors[0].message in str(caught.value)
    assert target.read_text() == "keep me"


def test_random_strength_is_not_evaluated(expand):
    lat = expand("- q: {kind: Quadrupole, length: 1, MagneticMultipoleP: {Kn1: '1 + random()'}}")
    with pytest.raises(ValueError, match="expanded number|expansion errors"):
        pp.pals_to_cosy(lat)


@pytest.mark.parametrize("field", ["pc_ref: .nan", "pc_ref: .inf", "pc_ref: -1", "pc_ref: 0", "pc_ref: 1e9, E_tot_ref: 2e9"])
def test_invalid_reference_rejected(tmp_path, field):
    path = tmp_path / "bad.yaml"
    path.write_text(document("- d: {kind: Drift, length: 1}").replace("pc_ref: 1e9", field))
    with pytest.raises(ValueError):
        pp.pals_to_cosy(pp.parse_and_expand_pals(path, problems="none"))


def test_reference_change_remains_unsupported(expand):
    lat = expand("- r: {kind: ReferenceChange, ReferenceChangeP: {dE_ref: 1e6}}\n- d: {kind: Drift, length: 1}")
    with pytest.raises(pp.CosyUnsupportedError, match="ReferenceChange"):
        pp.pals_to_cosy(lat)


def test_rf_downstream_reference_used_once(expand):
    lat = expand('''
        - rf: {kind: RFCavity, length: 0.2, RFP: {voltage: 1e6, frequency: 5e8, dE_ref: 1e6}}
        - q: {kind: Quadrupole, length: 0.5, MagneticMultipoleP: {Kn1: 1}}
    ''')
    assert not lat.problems
    line = lat.full_expanded["ring"]["branches"][0]["main"]["line"]
    entrance = line[1]["rf"]["ReferenceP"]["E_tot_ref"].as_float()
    ref = line[2]["q"]["ReferenceP"]
    assert ref["E_tot_ref"].as_float() == pytest.approx(entrance + 1e6)
    program = pp.pals_to_cosy(lat)
    pole = float(commands(program, "MQ")[0][1])
    assert pole == pytest.approx(ref["pc_ref"].as_float() / 299792458 * 0.0135)
    assert commands(program, "RF")[0][2:] == ["500000000.0", "90.0", "0.0135"]


def test_rf_harmonic_uses_native_end_to_end_time(expand):
    lat = expand('''
        - d: {kind: Drift, length: 10}
        - rf: {kind: RFCavity, RFP: {voltage: 1000, harmonic_number: 10, zero_phase: BELOW_TRANSITION}}
    ''')
    line = lat.full_expanded["ring"]["branches"][0]["main"]["line"]
    dt = line[-1]["branch_end"]["ReferenceP"]["time_ref"].as_float()
    assert float(commands(pp.pals_to_cosy(lat), "RF")[0][2]) == pytest.approx(10 / dt)


def test_thin_skew_and_tilt(expand):
    lat = expand("- q: {kind: Multipole, MagneticMultipoleP: {Kn1L: 0.2, tilt1: 0.7853981633974483}}")
    program = pp.pals_to_cosy(lat)
    assert len(commands(program, "MMS")) == 1
    assert commands(program, "DL") == [["-5e-07"], ["-5e-07"]]
    value = float(re.search(r"PALSBS\(1\) := ([^ ]+)", program)[1])
    assert value == pytest.approx(-0.2 * 1e9 / 299792458 / 1e-6 * 0.0135)


@pytest.mark.parametrize("geometry", ["length: 1, BendP: {angle_ref: 0.1}", "BendP: {angle_ref: 0.1, L_chord: 1}", "length: 1, BendP: {angle_ref: -0.1, ref_geometry: CHORD}"])
def test_derived_bend_geometry_is_accepted(expand, geometry):
    lat = expand("- b: {kind: Bend, " + geometry + "}")
    assert not lat.problems
    assert len(commands(pp.pals_to_cosy(lat), "DIL")) == 1


def test_model_warnings_and_marker_preserved(expand):
    lat = expand('''
        - note: {kind: Marker}
        - construction_placeholder: {kind: Placeholder}
        - s: {kind: Solenoid, length: 0.3, SolenoidP: {Ksol: 0.1}}
    ''')
    with pytest.warns(pp.CosyConversionWarning, match="tanh fringe"):
        program = pp.pals_to_cosy(lat)
    assert "{ note }" in program
    # Native expansion removes construction placeholders by the PALS contract.
    assert "construction_placeholder" not in lat.full_expanded.to_yaml_string()
    assert "construction_placeholder" not in program
    assert len(commands(program, "CMS")) == 1
    assert commands(program, "DL") == []


@pytest.mark.parametrize("kwargs", [{"fringe_field_order": -1}, {"computation_order": 0}, {"dimensions": 4}, {"quad_aperture": math.inf}, {"solenoid_fringe": -1}])
def test_invalid_options(expand, kwargs):
    with pytest.raises(ValueError):
        pp.pals_to_cosy(expand("- d: {kind: Drift, length: 1}"), **kwargs)


def test_writer_accepts_path_and_exact_text(expand, tmp_path):
    program = pp.pals_to_cosy(expand("- d: {kind: Drift, length: 1}"))
    path = tmp_path / "output.fox"
    pp.write_cosy_file(program, path)
    assert path.read_bytes() == program.encode("utf-8")


def test_positive_ion_parser_error_is_not_suppressed(expand):
    # Native 0.0.4 mistakes '+' in a literal species name for arithmetic.
    # Keep the fail-on-any-parser-error rule, even when reference book-keeping
    # has otherwise succeeded. Remove this version-specific expectation when
    # the upstream parser fixes it; never clear problems in the translator.
    lat = expand("- d: {kind: Drift, length: 1}", species="#12C+6")
    if any(p.severity == pp.PROBLEM_ERROR for p in lat.problems):
        with pytest.raises(ValueError, match="expansion errors"):
            pp.pals_to_cosy(lat)
    else:
        assert float(commands(pp.pals_to_cosy(lat), "RP")[0][2]) == 6


def test_native_warning_is_reported(expand):
    lat = expand("- foil: {kind: Foil, length: 0.1}")
    upstream = [p for p in lat.problems if p.severity == pp.PROBLEM_WARNING]
    assert upstream
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pp.pals_to_cosy(lat)
    assert any(upstream[0].message in str(w.message) for w in caught)


def test_load_include_and_set_are_native(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts/elements.yaml").write_text("- q: {kind: Quadrupole, length: 0.2, MagneticMultipoleP: {Kn1: 0.1}}\n")
    (tmp_path / "layout.yaml").write_text(document("- q", definitions='- include: "parts/elements.yaml"'))
    root = tmp_path / "joiner.yaml"
    root.write_text('''PALS:
  load: [layout.yaml]
  facility:
    - set: {parameter: q>MagneticMultipoleP.Kn1, value: 0.75}
''')
    lat = pp.parse_and_expand_pals(root, problems="none")
    assert not lat.problems
    pole = float(commands(pp.pals_to_cosy(lat), "MQ")[0][1])
    assert pole == pytest.approx(0.75 * 1e9 / 299792458 * 0.0135)


def test_multipass_order_is_retained(expand):
    lat = expand("- cell\n- cell", definitions='''
        - q: {kind: Quadrupole, length: 0.2, MagneticMultipoleP: {Kn1: 0.5}}
        - cell: {kind: BeamLine, multipass: true, line: [q]}
    ''')
    assert not lat.problems
    program = pp.pals_to_cosy(lat)
    assert len(commands(program, "MQ")) == 2
    assert "q (pass 1)" in program and "q (pass 2)" in program


def test_legacy_wiggler_extension_is_outside_native_contract(expand):
    lat = expand("- w: {kind: Wiggler, length: 1, WigglerP: {peak_field: 1, period: 0.1}}")
    with pytest.raises(ValueError, match="unknown parameter group 'WigglerP'"):
        pp.pals_to_cosy(lat)


def test_switched_off_rf_reports_reference_mismatch(expand):
    lat = expand('''
        - c: {kind: RFCavity, is_on: false, length: 1, RFP: {voltage: 1e6, dE_ref: 1e6, frequency: 5e8}}
        - d: {kind: Drift, length: 1}
    ''')
    with pytest.warns(pp.CosyConversionWarning, match="gain of 0.0 eV"):
        program = pp.pals_to_cosy(lat)
    assert not commands(program, "RF")
    assert commands(program, "DL") == [["1.0"], ["1.0"]]


def test_zero_curvature_bend_is_a_drift(expand):
    lat = expand("- b: {kind: Bend, length: 1, BendP: {angle_ref: 0}}")
    program = pp.pals_to_cosy(lat)
    assert not commands(program, "DIL")
    assert commands(program, "DL") == [["1.0"]]


@pytest.mark.parametrize("field", ["voltage: .inf, frequency: 5e8", "voltage: 1e6, frequency: .nan", "voltage: 1e6, frequency: -1"])
def test_nonfinite_or_negative_rf_is_rejected(expand, field):
    lat = expand("- c: {kind: RFCavity, length: 1, RFP: {" + field + "}}")
    with pytest.raises(ValueError):
        pp.pals_to_cosy(lat)


def test_error_in_unselected_branch_is_fatal(tmp_path):
    source = document("- d: {kind: Drift, length: 1}", definitions="- bad: {kind: BeamLine, line: [missing_element]}")
    path = tmp_path / "branches.yaml"
    path.write_text(source.replace("branches: [main]", "branches: [main, bad]"))
    lat = pp.parse_and_expand_pals(path, problems="none")
    assert any(p.severity == pp.PROBLEM_ERROR for p in lat.problems)
    with pytest.raises(ValueError, match="expansion errors"):
        pp.pals_to_cosy(lat, branch_name="main")


@pytest.mark.parametrize("phase,zero,species,expected,gain", [
    (math.pi / 3, "ACCELERATING", "proton", 150, 500),
    (math.pi / 3, "ACCELERATING", "electron", -30, 500),
    (math.pi / 6, "BELOW_TRANSITION", "proton", 30, 500),
    (math.pi / 6, "ABOVE_TRANSITION", "electron", 30, -500),
    (13 * math.pi / 3, "ACCELERATING", "proton", 150, 500),
])
def test_nonzero_rf_phase_and_charge(expand, phase, zero, species, expected, gain):
    lat = expand(f"- c: {{kind: RFCavity, RFP: {{voltage: 1000, frequency: 5e8, phase: {phase}, zero_phase: {zero}, dE_ref: {gain}}}}}", species=species)
    assert not lat.problems
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        program = pp.pals_to_cosy(lat)
    assert float(commands(program, "RF")[0][3]) == pytest.approx(expected)


def test_active_rf_gain_mismatch_is_reported(expand):
    lat = expand("- c: {kind: RFCavity, RFP: {voltage: 1e6, frequency: 5e8, dE_ref: 0}}")
    with pytest.warns(pp.CosyConversionWarning, match="downstream maps are relative"):
        pp.pals_to_cosy(lat)


def test_time_only_reference_change_enters_harmonic_frequency(expand):
    lat = expand('''
        - d: {kind: Drift, length: 10}
        - r: {kind: ReferenceChange, ReferenceChangeP: {dtime_ref: 1e-9}}
        - c: {kind: RFCavity, RFP: {voltage: 1000, harmonic_number: 10, zero_phase: BELOW_TRANSITION}}
    ''')
    assert not lat.problems
    line = lat.full_expanded["ring"]["branches"][0]["main"]["line"]
    dt = line[-1]["branch_end"]["ReferenceP"]["time_ref"].as_float()
    mass = pp.evaluate_pals_expression('mass_of("proton")')
    assert dt == pytest.approx(10 * math.hypot(1e9, mass) / (299792458 * 1e9) + 1e-9)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert float(commands(pp.pals_to_cosy(lat), "RF")[0][2]) == pytest.approx(10 / dt)


@pytest.mark.parametrize("settings", [
    "BendP: {g_ref: 0.3, Kn0_from_g_ref: false}, MagneticMultipoleP: {Kn1: 0.1}",
    "BendP: {g_ref: 0.3}, MagneticMultipoleP: {Bn0: 0}",
])
def test_zero_actual_bend_field_warns(expand, settings):
    lat = expand("- b: {kind: Bend, length: 1, " + settings + "}")
    assert not lat.problems
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pp.pals_to_cosy(lat)
    assert any("actual dipole field 0.0 T differs from the reference field" in str(w.message) for w in caught)


def test_infinite_bend_radius_is_a_drift(expand):
    lat = expand("- b: {kind: Bend, length: 1, BendP: {radius_ref: .inf}}")
    assert not lat.problems
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert commands(pp.pals_to_cosy(lat), "DL") == [["1.0"]]


@pytest.mark.parametrize("geometry,edges", [("ENTRANCE_COORDS", (0, 1)), ("EXIT_COORDS", (1, 0))])
def test_rectangle_length_and_face_conventions(expand, geometry, edges):
    lat = expand(f"- b: {{kind: Bend, BendP: {{g_ref: 0.3, L_rectangle: 1, ref_geometry: {geometry}}}}}")
    assert not lat.problems
    args = list(map(float, commands(pp.pals_to_cosy(lat), "DIL")[0]))
    angle = math.asin(0.3)
    assert args[0] == pytest.approx(angle / 0.3)
    assert args[3] == pytest.approx(edges[0] * math.degrees(angle))
    assert args[5] == pytest.approx(edges[1] * math.degrees(angle))


def test_native_aperture_sets_bend_half_gap(expand):
    lat = expand("- b: {kind: Bend, length: 1, BendP: {g_ref: 0.3}, ApertureP: {y_min: -0.02, y_max: 0.02}}")
    assert not lat.problems
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert float(commands(pp.pals_to_cosy(lat, fringe_field_order=3), "DIL")[0][2]) == 0.02


@pytest.mark.parametrize("fr", [0, 1, 2, 3])
def test_solenoid_warning_is_independent_of_fr(expand, fr):
    lat = expand("- s: {kind: Solenoid, length: 0.5, SolenoidP: {Bsol: 3}}")
    with pytest.warns(pp.CosyConversionWarning, match="tanh fringe"):
        pp.pals_to_cosy(lat, fringe_field_order=fr)


@pytest.mark.parametrize("element,match", [
    ("- p: {kind: Patch, PatchP: {x_offset: 0.01}}", "Patch"),
    ("- q: {kind: Quadrupole, length: 1, MagneticMultipoleP: {Kn1: 1, Kn1_taper: 0.1}}", "taper"),
    ("- b: {kind: Bend, is_on: false, length: 1, BendP: {g_ref: 0.3}}", "is_on"),
])
def test_unrepresented_reference_or_field_models_fail(expand, element, match):
    lat = expand(element)
    assert not lat.problems
    with pytest.raises(pp.CosyUnsupportedError, match=match):
        pp.pals_to_cosy(lat)


def test_3d_twiss_without_rf_fails(expand):
    with pytest.raises(ValueError, match="needs RF longitudinal focusing"):
        pp.pals_to_cosy(expand("- d: {kind: Drift, length: 1}"), twiss=True, dimensions=3)


@pytest.mark.parametrize("flag", ["false", "False", "FALSE"])
def test_boolean_case_variants_switch_magnets_off(expand, flag):
    lat = expand("- q: {kind: Quadrupole, length: 1, is_on: " + flag + ", MagneticMultipoleP: {Kn1: 1}}")
    assert not lat.problems
    script = pp.pals_to_cosy(lat)
    assert not commands(script, "MQ")
    assert commands(script, "DL") == [["1.0"]]
    lat = expand("- b: {kind: Bend, length: 1, BendP: {g_ref: 0.3, Kn0_from_g_ref: " + flag + "}}")
    with pytest.warns(pp.CosyConversionWarning, match="reference field"):
        pp.pals_to_cosy(lat)


@pytest.mark.parametrize("flag", ["no", "yes", "0", "1"])
@pytest.mark.parametrize("element", [
    "- q: {kind: Quadrupole, length: 1, is_on: FLAG, MagneticMultipoleP: {Kn1: 1}}",
    "- b: {kind: Bend, length: 1, BendP: {g_ref: 0.3, Kn0_from_g_ref: FLAG}}",
])
def test_non_boolean_field_controls_fail_closed(expand, flag, element):
    lat = expand(element.replace("FLAG", flag))
    assert not lat.problems
    with pytest.raises(ValueError, match="must be true or false"):
        pp.pals_to_cosy(lat)


def test_zero_field_solenoid_is_warning_free_drift(expand):
    lat = expand("- s: {kind: Solenoid, length: 0.5, SolenoidP: {Bsol: 0}}")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        script = pp.pals_to_cosy(lat)
    assert not commands(script, "CMS")
    assert commands(script, "DL") == [["0.5"]]


def test_open_aperture_uses_missing_gap_warning(expand):
    lat = expand("- b: {kind: Bend, length: 1, BendP: {g_ref: 0.3}, ApertureP: {y_min: -.inf, y_max: .inf}}")
    assert not lat.problems
    with pytest.warns(pp.CosyConversionWarning, match="no pole gap"):
        script = pp.pals_to_cosy(lat, fringe_field_order=3)
    assert float(commands(script, "DIL")[0][2]) == 0.0135


@pytest.mark.parametrize("parameter", ["dE_ref", "dpc_ref"])
def test_zero_reference_change_is_noop(expand, parameter):
    lat = expand("- r: {kind: ReferenceChange, ReferenceChangeP: {" + parameter + ": 0}}\n- d: {kind: Drift, length: 1}")
    assert not lat.problems
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert commands(pp.pals_to_cosy(lat), "DL") == [["1.0"]]


def test_curvature_alone_reports_missing_geometry(expand):
    lat = expand("- b: {kind: Bend, BendP: {g_ref: 0.3}}")
    assert not lat.problems
    with pytest.raises(ValueError, match="curvature alone does not fix the bend"):
        pp.pals_to_cosy(lat)


def test_long_comment_cannot_produce_invalid_cosy_input(expand):
    lat = expand("- " + "m" * 520 + ": {kind: Marker}")
    assert not lat.problems
    with pytest.raises(ValueError, match="exceeds 512 columns"):
        pp.pals_to_cosy(lat)
    assert max(map(len, pp.pals_to_cosy(lat, comments=False).splitlines())) <= 512
