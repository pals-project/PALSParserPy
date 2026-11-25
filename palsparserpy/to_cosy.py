"""Translate a selected, fully expanded PALS branch to COSY INFINITY."""

import math
from pathlib import Path
import warnings

from ._cosy_elements import normalize_element
from ._cosy_emit import _LatticeWriter, _fox_template
from ._cosy_reference import ElementContext
from ._cosy_types import CosyConversionWarning, CosyUnsupportedError
from .structs import Lattices, PROBLEM_ERROR

__all__ = ["pals_to_cosy", "write_cosy_file", "CosyConversionWarning", "CosyUnsupportedError"]


def _value(node):
    """Copy expanded node values without interpreting expressions or YAML again."""
    if node.is_map():
        return {k: _value(v) for k, v in node.items()}
    if node.is_sequence():
        return [_value(v) for v in node]
    value = node.value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _named_entry(entry, context):
    if not isinstance(entry, dict) or len(entry) != 1:
        raise ValueError(f"{context} must contain one named expanded mapping")
    name, content = next(iter(entry.items()))
    if not isinstance(content, dict):
        raise ValueError(f"{context} '{name}' is not fully expanded")
    return name, content


def _branch_elements(lattices, branch_name):
    if not isinstance(lattices, Lattices):
        raise TypeError("pals_to_cosy expects the Lattices result of parse_and_expand_pals")
    errors = [p for p in lattices.problems if p.severity == PROBLEM_ERROR]
    if errors:
        raise ValueError("Cannot translate a lattice with expansion errors:\n" + "\n".join(map(str, errors)))
    for problem in lattices.problems:
        warnings.warn(str(problem), CosyConversionWarning, stacklevel=3)
    lattice_name, lattice = _named_entry(_value(lattices.full_expanded), "Expanded lattice")
    branches = [_named_entry(b, "Branch") for b in lattice.get("branches", [])]
    names = [name for name, _ in branches]
    if branch_name is None and len(branches) == 1:
        branch_name = names[0]
    selected = [body for name, body in branches if name == branch_name]
    if len(selected) != 1:
        raise ValueError(f"Choose one exact branch_name; available branches: {', '.join(names) or '(none)'}")
    elements = []
    for entry in selected[0].get("line", []):
        name, body = _named_entry(entry, "Element")
        elements.append({**body, "name": name})
    if not elements:
        raise ValueError(f"Branch '{branch_name}' has no expanded elements")
    return lattice_name, branch_name, elements


def pals_to_cosy(lattices: Lattices, *, branch_name: str | None = None,
                 fringe_field_order: int = 0, computation_order: int = 3,
                 dimensions: int | None = None, quad_aperture: float = 0.027,
                 comments: bool = True, twiss: bool = False,
                 solenoid_fringe: float | None = None) -> str:
    """Return a complete COSY programme from ``lattices.full_expanded``.

    Expansion errors fail before translation. Expansion/model warnings are
    emitted as :class:`CosyConversionWarning`. ``branch_name`` can be omitted
    only for a single branch. The input trees are never modified.

    ``quad_aperture`` is the bore *diameter* in metres. ``dimensions`` defaults
    to 3 (2 with ``twiss``); ``fringe_field_order`` selects COSY FR 0--3.
    Twiss extraction requires a stable periodic map. See the translation guide
    for thin-multipole, RF, solenoid and unsupported-feature limitations.
    """
    if type(fringe_field_order) is not int or fringe_field_order not in range(4):
        raise ValueError("fringe_field_order must be an integer from 0 to 3")
    if type(computation_order) is not int or computation_order < 1:
        raise ValueError("computation_order must be a positive integer")
    dimensions = (2 if twiss else 3) if dimensions is None else dimensions
    if type(dimensions) is not int or dimensions not in (2, 3):
        raise ValueError("dimensions must be 2 or 3")
    for name, value in (("quad_aperture", quad_aperture), ("solenoid_fringe", solenoid_fringe)):
        if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be finite and positive")
    lattice_name, selected, raw_elements = _branch_elements(lattices, branch_name)
    first, last = raw_elements[0], raw_elements[-1]

    def time_ref(raw):
        ref = ElementContext.group(raw, "ReferenceP", raw["name"]) or {}
        return ElementContext.number(ref.get("time_ref"), raw["name"], "ReferenceP.time_ref")

    branch_time = time_ref(last) - time_ref(first)
    initial = ElementContext(first, branch_time)
    elements = []
    previous = None
    for index, raw in enumerate(raw_elements):
        ctx = initial if index == 0 else ElementContext(raw, branch_time)
        if previous is not None:
            previous_raw, previous_ctx = previous
            gain = 0.0
            if previous_raw.get("kind") == "RFCavity":
                rfp = previous_ctx.group(previous_raw, "RFP", previous_raw["name"])
                gain = previous_ctx.optional(rfp, "dE_ref", previous_raw["name"], "RFP", 0.0)
            if ctx.species != previous_ctx.species or not math.isclose(ctx.e_tot_ev, previous_ctx.e_tot_ev + gain, rel_tol=1e-12, abs_tol=1e-6):
                raise CosyUnsupportedError(f"Element '{raw['name']}': reference change outside the supported RF model")
        element = normalize_element(ctx, raw)
        if "multipass_index" in raw:
            element["name"] += f" (pass {raw['multipass_index']})"
        elements.append(element)
        previous = raw, ctx
    radius = quad_aperture / 2
    writer = _LatticeWriter(radius, comments, fringe_field_order,
                            radius if solenoid_fringe is None else solenoid_fringe)
    body = writer.write(elements)
    if twiss and dimensions == 3 and not writer.needs_rf_array:
        raise ValueError("Twiss extraction with dimensions=3 needs RF longitudinal focusing")
    program = _fox_template(source_file=f"{lattice_name}/{selected}", dimensions=dimensions,
                         order=computation_order, ke=initial.kinetic_energy_mev,
                         fr=fringe_field_order, lattice_body=body, particle=initial.species,
                         twiss=twiss, declarations=writer.declarations())
    if any(len(line) > 512 for line in program.splitlines()):
        raise ValueError("Generated COSY line exceeds 512 columns; shorten lattice/element names or disable comments")
    return program


def write_cosy_file(program: str, filename) -> None:
    """Write programme text to a string or pathlib path, using UTF-8."""
    if not isinstance(program, str):
        raise TypeError("program must be the string returned by pals_to_cosy")
    Path(filename).write_text(program, encoding="utf-8", newline="")
