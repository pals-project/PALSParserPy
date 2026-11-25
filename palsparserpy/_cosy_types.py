# Adapted from pals2cosy, copyright (c) 2026 Eremey Valetov.
# MIT licence: see COSY_COPYING.txt.
"""Internal element representation used by the COSY emitter.

``bn`` / ``bs`` are normal and skew PALS field coefficients ``B_N`` in
  T/m^N, with any multipole tilt already folded in. The field is
  ``By + i Bx = sum_N (bn[N] + i bs[N]) (x + i y)^N / N!``. For a ``thin``
  element (zero length) they hold the length-integrated values in T/m^(N-1).
Angles are in degrees except ``tilt`` in radians; lengths are in metres and
edge curvatures in 1/m.

Author: Eremey Valetov
"""

MAX_MULTIPOLE_ORDER = 10  # COSY MMS holds at most 10 normal + 10 skew orders


def blank_element(etype, name="", length=0.0):
    return {
        "type": etype,
        "name": name,
        "length": length,
        "angle": None,
        "tilt": 0.0,
        "pole_gap": None,
        "edge_int": None,
        "bn": {},
        "bs": {},
        "thin": False,
        "bz": None,
        "rf_voltage_kv": None,
        "rf_frequency_hz": None,
        "rf_phase_deg": None,
    }


class CosyConversionWarning(UserWarning):
    """An input feature is omitted or approximated in the COSY model."""


class CosyUnsupportedError(NotImplementedError, ValueError):
    """The COSY emitter cannot represent an input feature."""
