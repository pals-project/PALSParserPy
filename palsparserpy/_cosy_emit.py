# Adapted from pals2cosy, copyright (c) 2026 Eremey Valetov.
# MIT licence: see COSY_COPYING.txt.
"""COSY INFINITY programme emission; no PALS parsing or book-keeping."""

import math
import re
import warnings

from ._cosy_types import MAX_MULTIPOLE_ORDER, CosyConversionWarning

# Element types that produce no COSYScript optics commands (emitted as DL if length > 0)
_PASSIVE_TYPES = frozenset({
    "KICKER", "INSTRUMENT", "MARKER",
})

_MULTIPOLE_TYPES = frozenset({"QPF", "SXT", "OCT", "MULT"})

# Zero-length (thin) multipoles are modelled as a slice of this length
# between drifts of minus half its length, which keeps the total length and
# reproduces the thin kick to O(K L * THIN_SLICE).
THIN_SLICE = 1e-6

# COSY's CMS solenoid has tanh fringe fields of length D, which reduce its
# focusing relative to the hard-edge PALS solenoid by about D/L. Below this
# ratio the difference is not reported.
SOLENOID_FRINGE_TOLERANCE = 1e-3

# Integral of F (1 - F) over S for COSY's default dipole Enge function F
# (procedure FD), where S is the distance from the effective field boundary
# in units of the full gap 2d. A bend of half-gap d therefore has the PALS
# fringe-field integral edge_int = d * DIPOLE_ENGE_INTEGRAL.
DIPOLE_ENGE_INTEGRAL = 0.3694585

_MM_NORMAL = "PALSBN"
_MM_SKEW = "PALSBS"
_RF_VOLTAGE = "PALSRFV"


def pole_tip_field(bn, order, radius):
    """COSY pole-tip field [T] of a PALS multipole coefficient ``B_N`` [T/m^N].

    PALS defines ``By + i Bx = (Bn + i Bs) (x + i y)^N / N!``, so the field
    magnitude at radius r is ``Bn r^N / N!``, which is what COSY's
    MQ/MH/MO/M5/MMS take at aperture r.
    """
    return bn * radius ** order / math.factorial(order)


class _LatticeWriter:
    """Generates the body of the LATTICE procedure."""

    def __init__(self, r, comments, fringe_field_order, solenoid_fringe):
        self.r = r
        self.comments = comments
        self.fr = fringe_field_order
        self.solenoid_fringe = solenoid_fringe
        self.needs_multipole_arrays = False
        self.needs_rf_array = False

    def declarations(self):
        lines = []
        if self.needs_multipole_arrays:
            lines.append(f"VARIABLE {_MM_NORMAL} 1 {MAX_MULTIPOLE_ORDER} ; "
                         f"VARIABLE {_MM_SKEW} 1 {MAX_MULTIPOLE_ORDER} ;")
        if self.needs_rf_array:
            lines.append(f"VARIABLE {_RF_VOLTAGE} 1 1 1 ;")
        return lines

    def _comment(self, elem):
        if self.comments and elem.get("name"):
            safe = re.sub(r'[^\x20-\x7e]|[{}]', '', elem["name"])
            return f" {{ {safe} }}"
        return ""

    def _drift(self, elem):
        length = elem["length"]
        return [f"    DL {length} ;{self._comment(elem)}"] if length > 0 else []

    def write(self, elements):
        lines = []
        for elem in elements:
            lines.extend(self._element(elem))
        return "\n".join(lines)

    def _element(self, elem):
        etype = elem["type"]
        if etype == "MARKER" and elem["length"] == 0:
            comment = self._comment(elem)
            return ["   " + comment] if comment else []
        if etype == "DRIFT" or etype in _PASSIVE_TYPES:
            return self._drift(elem)
        if etype in _MULTIPOLE_TYPES:
            return self._multipole(elem)
        if etype == "DIPOLE_CONSOLIDATED":
            if elem["length"] <= 0:
                return []
            if elem["angle"] == 0:
                return self._drift(elem)
            return _dipole_fox(elem, self._half_gap(elem), self._comment(elem))
        if etype == "SOL":
            return self._solenoid(elem)
        if etype == "RFC":
            return self._rf(elem)
        warnings.warn(
            f"Unknown element type '{etype}' (name='{elem.get('name', '')}'); "
            f"emitting as a drift",
            CosyConversionWarning,
        )
        return self._drift(elem)

    def _half_gap(self, elem):
        """Half-gap of a bend, which sets the extent of COSY's fringe field.

        With fringe fields on, PALS fringe-field integrals take precedence:
        the half-gap is chosen so that COSY's default Enge field has them.
        """
        name = elem.get("name", "")
        edge_int = elem.get("edge_int") or (0.0, 0.0)
        if any(edge_int) and self.fr == 0:
            for face, value in enumerate(edge_int, 1):
                if value:
                    warnings.warn(
                        f"Bend '{name}': BendP.edge{face}_int is not represented at "
                        f"fringe_field_order=0, where COSY's bends have hard edges; fringe_field_order=1, 2 or 3 "
                        f"includes it", CosyConversionWarning)
        elif any(edge_int):
            if edge_int[0] != edge_int[1]:
                warnings.warn(
                    f"Bend '{name}': COSY's DIL has one fringe-field extent for both "
                    f"faces; it is set from the mean of edge1_int = {edge_int[0]} m and "
                    f"edge2_int = {edge_int[1]} m", CosyConversionWarning)
            return (edge_int[0] + edge_int[1]) / 2 / DIPOLE_ENGE_INTEGRAL

        half_gap = (elem.get("pole_gap") or 0.0) / 2
        if half_gap == 0 and self.fr > 0:
            warnings.warn(
                f"Bend '{name}' gives no pole gap (ApertureP.y_width) and no "
                f"edge1_int/edge2_int; its fringe field uses the bore radius {self.r} m "
                f"as the half-gap", CosyConversionWarning)
            half_gap = self.r
        return half_gap

    def _solenoid(self, elem):
        length = elem["length"]
        bz = elem.get("bz")
        if length <= 0:
            return []
        if not bz:
            return self._drift(elem)
        fringe = self.solenoid_fringe
        if fringe / length > SOLENOID_FRINGE_TOLERANCE:
            warnings.warn(
                f"Solenoid '{elem.get('name', '')}': COSY's CMS has tanh fringe fields "
                f"of length {fringe} m, so its focusing is about {fringe / length:.2%} "
                f"weaker than the hard-edge solenoid; a shorter solenoid_fringe "
                f"reduces the difference at the cost of integration time",
                CosyConversionWarning,
            )
        return [f"    CMS {bz} {fringe} {length} ;{self._comment(elem)}"]

    def _multipole(self, elem):
        thin = bool(elem.get("thin"))
        length = THIN_SLICE if thin else elem["length"]
        if length <= 0:
            return []
        scale = 1 / THIN_SLICE if thin else 1.0
        normal, skew = {}, {}
        for order, b in (elem.get("bn") or {}).items():
            normal[order] = pole_tip_field(b * scale, order, self.r)
        for order, b in (elem.get("bs") or {}).items():
            skew[order] = pole_tip_field(b * scale, order, self.r)

        etype = elem["type"]
        comment = self._comment(elem)
        nonzero = {k for k, v in normal.items() if v} | {k for k, v in skew.items() if v}
        if not nonzero:
            return [] if thin else self._drift(elem)

        r = self.r
        if skew or max(nonzero, default=0) > 5:
            nb = max(nonzero)
            self.needs_multipole_arrays = True
            # COSY input lines are limited to 512 columns.
            assign = [
                f"    {_MM_NORMAL}({i}) := {normal.get(i, 0.0)} ; "
                f"{_MM_SKEW}({i}) := {skew.get(i, 0.0)} ;"
                for i in range(1, nb + 1)]
            cmd = [*assign,
                   f"    MMS {length} {_MM_NORMAL} {_MM_SKEW} {nb} {r} ;{comment}"]
        elif etype == "QPF" and nonzero == {1}:
            cmd = [f"    MQ {length} {normal.get(1, 0.0)} {r} ;{comment}"]
        elif etype == "SXT" and nonzero == {2}:
            cmd = [f"    MH {length} {normal[2]} {r} ;{comment}"]
        elif etype == "OCT" and nonzero == {3}:
            cmd = [f"    MO {length} {normal[3]} {r} ;{comment}"]
        else:
            b = [normal.get(i, 0) for i in range(1, 6)]
            cmd = [f"    M5 {length} {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {r} ;{comment}"]

        if not thin:
            return cmd
        half = -THIN_SLICE / 2
        lines = []
        if self.fr != 0:
            lines.append("    FR 0 ;")
        lines += [f"    DL {half} ;", *cmd, f"    DL {half} ;"]
        if self.fr != 0:
            lines.append(f"    FR {self.fr} ;")
        return lines

    def _rf(self, elem):
        v_kv = elem.get("rf_voltage_kv")
        f_hz = elem.get("rf_frequency_hz")
        if v_kv is None or f_hz is None or v_kv == 0:
            return self._drift(elem)
        phi = elem.get("rf_phase_deg") or 0.0
        half = elem["length"] / 2
        self.needs_rf_array = True
        # RF V N W PHI D: V = voltage coefficient array (kV), N = polynomial
        # order (0 = uniform), W = frequency (Hz), PHI = phase (deg),
        # D = aperture. COSY's RF is a thin kick, so it sits mid-cavity.
        lines = [f"    DL {half} ;"] if half > 0 else []
        lines += [f"    {_RF_VOLTAGE}(1,1) := {v_kv} ;",
                  f"    RF {_RF_VOLTAGE} 0 {f_hz} {phi} {self.r} ;{self._comment(elem)}"]
        if half > 0:
            lines.append(f"    DL {half} ;")
        return lines


def _dipole_fox(elem, d_half, comment=""):
    """Generate COSYScript lines for a consolidated dipole of half-gap ``d_half``."""
    lines = []
    angle = elem["angle"]
    e1 = elem["entrance_angle"]
    e2 = elem["exit_angle"]
    h1 = elem.get("entrance_curvature", 0.0) or 0
    h2 = elem.get("exit_curvature", 0.0) or 0
    tilt = elem.get("tilt", 0.0) or 0.0
    # A tilted bend is a bend in a frame rotated by tilt_ref (x toward y)
    # about the entrance axis; RA rotates COSY's coordinates by the same angle.
    if tilt:
        lines.append(f"    RA {math.degrees(tilt)} ;")

    # CB wrap for negative bending angle
    use_cb = angle < 0
    if use_cb:
        lines.append("    CB ;")

    lines.append(f"    DIL {elem['length']} {abs(angle)} {d_half} {e1} {h1} {e2} {h2} ;{comment}")

    if use_cb:
        lines.append("    CB ;")

    if tilt:
        lines.append(f"    RA {-math.degrees(tilt)} ;")

    return lines


# ---------------------------------------------------------------------------
# COSYScript template
# ---------------------------------------------------------------------------

def _particle_commands(particle, ke):
    """COSYScript commands that set the reference particle."""
    commands = [particle.cosy_command(ke)]
    if particle.anomalous_moment:
        # RP leaves COSY's spin G0 alone; RPS with spin tracking off sets it, as
        # the presets RPP, RPE and RPMU do.
        commands.append(f"RPS 0 {particle.anomalous_moment}")
    return commands


def _fox_template(source_file, dimensions, order, ke, fr, lattice_body,
                  particle, twiss=False, declarations=()):
    dim = dimensions
    reference = "".join(f"    {cmd} ;\n" for cmd in _particle_commands(particle, ke))
    safe_source = re.sub(r'[^\x20-\x7e]|[{}]', '', source_file)
    decl = "".join(f"        {line}\n" for line in declarations)
    # No explicit FD needed — OV calls FD internally, loading default
    # Enge coefficients (PEP/SLAC) for all multipoles.

    twiss_vars = f"""\
    VARIABLE A0 100 {dim} ; VARIABLE B0 100 {dim} ; VARIABLE G0 100 {dim} ;
    VARIABLE R0 100 {dim} ; VARIABLE MU0 100 {dim} ; VARIABLE F0 100 {2*dim} ;
""" if twiss else ""

    twiss_block = """
    CO 1 ; PM 99 ;

    GT MAP F0 MU0 A0 B0 G0 R0 ;

    OPENF 51 'result.txt' 'UNKNOWN' ;
        WRITE 51 '{' ;
        WRITE 51 '"twiss": {' ;
           WRITE 51 '  "beta_x": "'&S(CONS(B0(1)))&'",' ;
           WRITE 51 '  "beta_y": "'&S(CONS(B0(2)))&'",' ;
           WRITE 51 '  "alpha_x": "'&S(CONS(A0(1)))&'",' ;
           WRITE 51 '  "alpha_y": "'&S(CONS(A0(2)))&'",' ;
           WRITE 51 '  "gamma_x": "'&S(CONS(G0(1)))&'",' ;
           WRITE 51 '  "gamma_y": "'&S(CONS(G0(2)))&'",' ;
           WRITE 51 '  "mu_x": "'&S(CONS(MU0(1)))&'",' ;
           WRITE 51 '  "mu_y": "'&S(CONS(MU0(2)))&'"' ;
        WRITE 51 '}' ;
        WRITE 51 '}' ;
    CLOSEF 51 ;""" if twiss else ""

    return f"""\
{{ Generated by PALSParserPy COSY translator from {safe_source} }}
INCLUDE 'COSY' ;
PROCEDURE RUN ;
{twiss_vars}\
    PROCEDURE LATTICE ;
{decl}\
        UM ; CR ;
{lattice_body}
    ENDPROCEDURE ;

    OV {order} {dim} 0 ;
{reference}\
    FR {fr} ;
    LATTICE ;
{twiss_block}
ENDPROCEDURE ;
RUN ;
END ;
"""
