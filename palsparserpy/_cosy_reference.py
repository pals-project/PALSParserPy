"""Read the native parser's upstream reference state and APC particle data."""

from dataclasses import dataclass
from functools import lru_cache
import json
import math

from .parser import evaluate_pals_expression

C_LIGHT = 299792458.0
# COSY's RP mass unit, from its CODATA 1998 constants in cosy.fox.
AMU_MEV_COSY = 1.66053873e-27 * C_LIGHT**2 / 1.602176462e-19 * 1e-6


@dataclass(frozen=True)
class Species:
    name: str
    mass_mev: float
    charge: float
    anomalous_moment: float | None

    def cosy_command(self, kinetic_energy_mev):
        return f"RP {kinetic_energy_mev} {self.mass_mev / AMU_MEV_COSY} {self.charge}"


@lru_cache(maxsize=128)
def species_data(name):
    # Only fixed APC lookup calls, never evaluation of an input field.
    if any(c in name for c in ('"', '\\')):
        raise ValueError(f"Unsupported reference species name: {name!r}")
    quoted = json.dumps(name)
    mass = evaluate_pals_expression(f"mass_of({quoted})")
    charge = evaluate_pals_expression(f"charge_of({quoted})")
    if mass <= 0 or charge == 0:
        raise ValueError(f"COSY needs a massive charged reference species, got {name!r}")
    try:
        anomaly = evaluate_pals_expression(f"anomalous_moment_of({quoted})")
    except ValueError:
        anomaly = None
    return Species(name, mass * 1e-6, charge, anomaly)


class ElementContext:
    """One expanded element's reference; no mutable branch book-keeping."""

    def __init__(self, raw, branch_time):
        name = raw["name"]
        ref = self.group(raw, "ReferenceP", name)
        if not ref or not isinstance(ref.get("species_ref"), str):
            raise ValueError(f"Element '{name}': expanded ReferenceP is incomplete")
        self.species = species_data(ref["species_ref"])
        self.pc_ev = self.number(ref.get("pc_ref"), name, "ReferenceP.pc_ref")
        self.e_tot_ev = self.number(ref.get("E_tot_ref"), name, "ReferenceP.E_tot_ref")
        mass = self.species.mass_mev * 1e6
        if self.pc_ev <= 0 or self.e_tot_ev <= mass:
            raise ValueError(f"Element '{name}': reference momentum and kinetic energy must be positive")
        if not math.isclose(self.e_tot_ev, math.hypot(self.pc_ev, mass), rel_tol=1e-12):
            raise ValueError(f"Element '{name}': inconsistent expanded reference energy and momentum")
        self.branch_time = branch_time

    @property
    def brho(self):
        return self.pc_ev / (C_LIGHT * self.species.charge)

    @property
    def kinetic_energy_mev(self):
        # E - m = (pc)^2 / (E + m), avoiding cancellation for slow ions.
        return self.pc_ev**2 / (self.e_tot_ev + self.species.mass_mev * 1e6) * 1e-6

    @staticmethod
    def number(value, name, field, allow_infinite=False):
        try:
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, str) and value.lower() in (".inf", "+.inf", "-.inf"):
                value = value.replace(".", "")
            result = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Element '{name}': '{field}' must be an expanded number, got {value!r}") from None
        if math.isnan(result) or (math.isinf(result) and not allow_infinite):
            raise ValueError(f"Element '{name}': '{field}' must be finite, got {value!r}")
        return result

    def optional(self, group, key, name, gname, default=None):
        value = group.get(key) if group else None
        return default if value is None else self.number(value, name, f"{gname}.{key}")

    @staticmethod
    def group(raw, gname, name):
        group = raw.get(gname)
        if group is not None and not isinstance(group, dict):
            raise ValueError(f"Element '{name}': '{gname}' must be an expanded mapping")
        if group is not None and "inherit" in group:
            raise ValueError(f"Element '{name}': unresolved inheritance in '{gname}'")
        return group
