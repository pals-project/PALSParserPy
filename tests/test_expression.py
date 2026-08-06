"""Tests of expression evaluation, both standalone and across an expanded
lattice, and of the problem list expansion reports."""

import math

import pytest

from palsparserpy import (PALSParseError, PROBLEM_ERROR, PROBLEM_INPUT,
                          ProblemOrigin, ProblemSeverity,
                          evaluate_pals_expression, parse_and_expand_pals)

# A lattice exercising the expression evaluator: user variables, an immediate
# expression, an expr()-delayed expression, a particle-function constant, and a
# random_gauss() value that must stay unevaluated.
EXPR_LATTICE = """
PALS:
  facility:
    - variables:
        - a_var: 3.75e7 / c_light^2
        - b_var: -0.34
    - cleo:
        kind: Solenoid
        length: 0.1*log(abs(b_var))
        MagneticMultipoleP:
          Kn1: expr(3.74 * a_var)
          Kn2: 0.01 + 0.003*random_gauss()
    - m_e:
        kind: constant
        value: mass_of("electron")
    - main_line:
        kind: BeamLine
        line:
          - cleo
    - lat1:
        kind: Lattice
        branches:
          - main_line
    - use: "lat1"
"""

# A lattice with two controllers: `ps27` has inter-referencing variables and
# controls that use a lattice constant and a deferred random_gauss(); `chrom_a`
# references `ps27`'s variable via the `controller>variable` syntax.
CONTROLLER_LATTICE = """
PALS:
  facility:
    - my_const:
        kind: constant
        value: 2.0
    - ps27:
        kind: Controller
        control_type: ABSOLUTE
        variables:
          cur1: 0.023
          cur2: 0.023 / c_light
        controls:
          - parameter: Qa.*>MagneticMultipoleP.Ks2L
            expression: 0.075*sin(cur1) + 0.3*cur2
          - parameter: Qb>MagneticMultipoleP.Kn1L
            expression: cur1 * my_const
          - parameter: Qc>MagneticMultipoleP.Kn0
            expression: 0.01 + random_gauss()
    - chrom_a:
        kind: Controller
        control_type: RELATIVE
        variables:
          command: 0.4
          derived: 0.4 * 2
        controls:
          - parameter: S1>MagneticMultipoleP.Kn2L
            expression: 5.62 * command + 0.02 * command^2
    - main_line:
        kind: BeamLine
        line:
          - my_const
    - lat1:
        kind: Lattice
        branches:
          - main_line
    - use: "lat1"
"""

# A lattice where one element's parameter references another element's parameter
# via the `element>group.param` syntax inside an expression.
ELEMENT_PARAM_REF_LATTICE = """
PALS:
  facility:
    - thingB:
        kind: Sextupole
        length: 0.3
        MagneticMultipoleP:
          Kn2L: 0.1
    - DH1A:
        kind: Bend
        length: 0.2
        ReferenceP:
          species_ref: proton
          E_tot_ref: 1.0e9
        BendP:
          edge2_int: 0.02 * thingB>MagneticMultipoleP.Kn2L
    - main_line:
        kind: BeamLine
        line:
          - DH1A
    - lat1:
        kind: Lattice
        branches:
          - main_line
    - use: "lat1"
"""

# A lattice that deliberately fails several ways during expansion: an undefined
# constant reference, a dangling element-parameter reference, a dangling line
# reference, and an undefined `inherit` ancestor.
BROKEN_LATTICE = """
PALS:
  facility:
    - constants:
        a_const: 0.3 * undefined_thing
    - thingB:
        kind: Sextupole
        MagneticMultipoleP:
          Kn2L: 0.1
    - DH1A:
        kind: Bend
        BendP:
          edge2_int: 0.02 * thingB>MagneticMultipoleP.NotThere
    - ghost_child:
        kind: Bend
        inherit: ghost_ancestor
    - main_line:
        kind: BeamLine
        line:
          - DH1A
          - ghost_child
          - NoSuchElement
    - lat1:
        kind: Lattice
        branches:
          - main_line
    - use: "lat1"
"""

# A lattice that names a species with a string constant and feeds it to the
# particle-data functions by symbol (mass_of(species)), not a quoted literal.
SPECIES_CONST_LATTICE = """
PALS:
  facility:
    - constants:
        species: "#3He"
        b_const: 0.45 * mass_of(species)
    - DH1A:
        kind: Bend
        ReferenceP:
          species_ref: species
        BendP:
          h1: 1.1 * mass_of(species)
    - main_line:
        kind: BeamLine
        line:
          - DH1A
    - lat1:
        kind: Lattice
        branches:
          - main_line
    - use: "lat1"
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def _inlined(lat, lattice, beamline, name):
    """The element ``name`` as expansion inlined it into ``lat.full_expanded``.

    The expanded tree is rooted at the lattice entry, so the path runs
    lattice > branches > beamline > line > element, with no PALS/facility above.
    """
    return lat.full_expanded[lattice]["branches"][0][beamline]["line"][0][name]


class TestStandaloneEvaluation:
    def test_arithmetic_constants_and_functions(self):
        assert evaluate_pals_expression("2 + 3 * 4") == 14.0
        assert evaluate_pals_expression("2 ^ 3 ^ 2") == 512.0   # right-associative
        assert evaluate_pals_expression("-2 ^ 2") == -4.0       # unary minus looser
        assert evaluate_pals_expression("3.75e7 / c_light^2") == \
            pytest.approx(3.75e7 / 2.99792458e8 ** 2)
        assert evaluate_pals_expression("sqrt(2)") == pytest.approx(math.sqrt(2))
        assert evaluate_pals_expression("modulo(7, 3)") == 1.0
        assert evaluate_pals_expression("pi") == pytest.approx(math.pi)
        # expr(...) wrapper is accepted.
        assert evaluate_pals_expression("expr(2 * pi)") == pytest.approx(2 * math.pi)

    def test_particle_data_functions(self):
        # Species names must always be quoted (single or double). Values mirror
        # AtomicAndPhysicalConstantsCLib (CODATA 2022).
        assert evaluate_pals_expression('mass_of("electron")') == \
            pytest.approx(510998.95069000003)
        assert evaluate_pals_expression("mass_of('proton')") == \
            pytest.approx(938272089.43000007)
        assert evaluate_pals_expression('charge_of("electron")') == -1.0
        assert evaluate_pals_expression('charge_of("anti-proton")') == -1.0
        assert evaluate_pals_expression('charge_of("helion")') == 2.0
        # A mass number must carry a leading `#` (e.g. "#3He", not "3He").
        assert evaluate_pals_expression('mass_of("#3He")') == \
            pytest.approx(2809413524.398952)
        with pytest.raises(ValueError):
            evaluate_pals_expression('mass_of("3He")')

    @pytest.mark.parametrize("expr", [
        "thingB",                     # unknown identifier
        'mass_of("nonsense")',        # unknown species
        "mass_of(electron)",          # unquoted species
        "0.01 + random_gauss()",      # deferred
        "1 +",                        # parse error
    ])
    def test_non_evaluable_inputs_raise(self, expr):
        with pytest.raises(ValueError):
            evaluate_pals_expression(expr)


class TestWholeLatticeEvaluation:
    def test_parse_and_expand_pals_evaluates_the_expanded_tree(self, tmp_path):
        lat = parse_and_expand_pals(_write(tmp_path, "expr.pals.yaml", EXPR_LATTICE))

        a_var = 3.75e7 / 2.99792458e8 ** 2
        # cleo is named by main_line, so its definition is inlined into lat1.
        cleo = _inlined(lat, "lat1", "main_line", "cleo")
        mmp = cleo["MagneticMultipoleP"]

        # Immediate expression using a user variable.
        assert cleo["length"].as_float() == pytest.approx(0.1 * math.log(0.34))
        # expr()-delayed expression is evaluated to a number in the expanded tree.
        assert mmp["Kn1"].as_float() == pytest.approx(3.74 * a_var)
        # random_gauss() is deferred: the text is left untouched.
        assert mmp["Kn2"].value == "0.01 + 0.003*random_gauss()"

        # m_e is not part of the lattice and nothing in it refers to m_e, so it is
        # left over -- evaluated all the same.
        assert lat.adjunct["PALS"]["facility"][2]["m_e"]["value"].as_float() == \
            pytest.approx(510998.95069000003)

        # The combined tree keeps the original expression text.
        assert lat.combined["PALS"]["facility"][1]["cleo"]["length"].value == \
            "0.1*log(abs(b_var))"

    def test_resolves_element_parameter_references(self, tmp_path):
        lat = parse_and_expand_pals(
            _write(tmp_path, "eleparamref.pals.yaml", ELEMENT_PARAM_REF_LATTICE))

        # edge2_int references thingB's Kn2L (0.1) via element>group.param syntax.
        dh1a = _inlined(lat, "lat1", "main_line", "DH1A")
        assert dh1a["BendP"]["edge2_int"].as_float() == pytest.approx(0.02 * 0.1)

    def test_resolves_a_species_name_constant(self, tmp_path):
        lat = parse_and_expand_pals(
            _write(tmp_path, "species.pals.yaml", SPECIES_CONST_LATTICE),
            problems="none")

        m_3he = evaluate_pals_expression('mass_of("#3He")')
        # The constants block is not part of the lattice, so it is left over.
        consts = lat.adjunct["PALS"]["facility"][0]["constants"]
        dh1a = _inlined(lat, "lat1", "main_line", "DH1A")

        # mass_of(species) resolves the `species: "#3He"` constant by name.
        assert consts["b_const"].as_float() == pytest.approx(0.45 * m_3he)
        assert dh1a["BendP"]["h1"].as_float() == pytest.approx(1.1 * m_3he)
        # A bare identifier naming the species constant (species_ref: species) is
        # replaced by its species-name string in the expanded tree.
        assert dh1a["ReferenceP"]["species_ref"].value == "#3He"
        # The species constant itself keeps its string species name.
        assert consts["species"].value == "#3He"

    def test_evaluates_controllers(self, tmp_path):
        lat = parse_and_expand_pals(
            _write(tmp_path, "controller.pals.yaml", CONTROLLER_LATTICE))

        cur1 = 0.023
        cur2 = cur1 / 2.99792458e8
        # Controllers are facility-level, so they are left over rather than part
        # of the lattice; their expressions are evaluated all the same.
        fac = lat.adjunct["PALS"]["facility"]
        ps27 = fac[1]["ps27"]

        # An initial value is a constant expression -- it may use the built-in and
        # user constants, never a variable (rejecting a variable reference is
        # PALSParserCpp's business and is tested there).
        assert ps27["variables"]["cur2"].as_float() == pytest.approx(cur2)
        # Each control expression is computed and stored back in the entry.
        assert ps27["controls"][0]["expression"].as_float() == \
            pytest.approx(0.075 * math.sin(cur1) + 0.3 * cur2)
        # Control expressions may reference lattice constants (my_const = 2).
        assert ps27["controls"][1]["expression"].as_float() == \
            pytest.approx(cur1 * 2.0)
        # random_gauss() stays deferred.
        assert ps27["controls"][2]["expression"].value == "0.01 + random_gauss()"
        # The parameter target spec is a name, left untouched.
        assert ps27["controls"][0]["parameter"].value == \
            "Qa.*>MagneticMultipoleP.Ks2L"

        # A second controller, with a symbol table of its own.
        chrom = fac[2]["chrom_a"]
        assert chrom["variables"]["derived"].as_float() == pytest.approx(0.8)
        assert chrom["controls"][0]["expression"].as_float() == \
            pytest.approx(5.62 * 0.4 + 0.02 * 0.4 ** 2)

        # The combined tree keeps the original controller expression text.
        c_ps27 = lat.combined["PALS"]["facility"][1]["ps27"]
        assert c_ps27["controls"][0]["expression"].value == \
            "0.075*sin(cur1) + 0.3*cur2"


class TestProblemReporting:
    def test_parse_and_expand_pals_reports_expansion_problems(self, tmp_path,
                                                              capsys):
        path = _write(tmp_path, "broken.pals.yaml", BROKEN_LATTICE)
        clean = _write(tmp_path, "clean.pals.yaml", ELEMENT_PARAM_REF_LATTICE)

        # A clean lattice prints nothing by default.
        parse_and_expand_pals(clean)
        assert capsys.readouterr().err == ""

        # "print" (the default) writes the problems to stderr.
        parse_and_expand_pals(path)
        printed = capsys.readouterr().err
        assert "problem(s)" in printed
        assert "NoSuchElement" in printed

        # A filename writes the problems to that file and prints nothing.
        report = tmp_path / "problems.txt"
        parse_and_expand_pals(path, problems=report)
        assert capsys.readouterr().err == ""
        contents = report.read_text()
        assert "reference to undefined element or line 'NoSuchElement'" in contents
        assert "inherit: 'ghost_ancestor' is not defined" in contents
        assert "could not evaluate expression for constants.a_const" in contents
        assert "could not evaluate expression for BendP.edge2_int" in contents

        # "none" prints nothing, but still returns the problems in the struct.
        lat = parse_and_expand_pals(path, problems="none")
        assert capsys.readouterr().err == ""
        assert lat.problems
        assert any("NoSuchElement" in p.message for p in lat.problems)
        assert any("inherit: 'ghost_ancestor' is not defined" in p.message
                   for p in lat.problems)

        # Each problem is classified, not just described. A dangling reference is
        # the author's to fix and leaves the trees untrustworthy around it.
        dangling = [p for p in lat.problems if "NoSuchElement" in p.message]
        assert len(dangling) == 1
        assert dangling[0].severity is PROBLEM_ERROR
        assert dangling[0].origin is PROBLEM_INPUT

        # Nothing in this list is left unclassified by accident: the enums round
        # -trip from C, so an unmapped integer would raise rather than compare.
        assert all(isinstance(p.severity, ProblemSeverity) for p in lat.problems)
        assert all(isinstance(p.origin, ProblemOrigin) for p in lat.problems)
        # `path` is empty rather than undefined when a problem is not tied to one
        # spot, so it is always safe to read.
        assert all(isinstance(p.path, str) for p in lat.problems)

        # A clean lattice carries an empty problems list.
        assert parse_and_expand_pals(clean, problems="none").problems == []

    def test_malformed_file_raises_pinpointing_it(self, tmp_path):
        # A YAML syntax error (a sequence item missing its ':') is fatal: there is
        # no tree to expand. It must raise a catchable error naming the line -- the
        # C library no longer aborts the whole process.
        path = _write(tmp_path, "bad.pals.yaml",
                      "PALS:\n  facility:\n    - cav\n        kind: RFCavity\n")
        with pytest.raises(PALSParseError) as excinfo:
            parse_and_expand_pals(path)
        message = str(excinfo.value)
        assert "line 4" in message
        # The message quotes the source: the preceding line (where the missing ':'
        # really is) and a caret, so the fault is easy to spot.
        assert "3 |     - cav" in message
        assert "^" in message
