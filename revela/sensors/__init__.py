# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Sensor descriptions: load, validate, and convert to register values.

One JSON file per sensor MODEL, under ``revela/sensors/<name>/sensor.json``,
validated against ``schema.json``. What lives here is what is true of every part
of that model. What does NOT live here is anything true of one physical camera:
its CCM, its lens shading mesh, its defect map. Those are calibration outputs,
they differ per unit and per lens, and they are loaded at runtime from wherever
the calibration process wrote them. The schema rejects a ``calibration`` key
outright so that the distinction fails in CI rather than in the field.

Where each part of a description is consumed
--------------------------------------------

Software at runtime
    Exposure and gain conversion, mode selection, I2C sequences. The 3A loop
    reads the JSON and computes register writes. This is most of the file.

Build-time parameters
    Bit depth (datapath width) and image width (line buffer sizing). ONLY these.
    Everything else that varies between sensors is a runtime register, because
    generating a different pipeline per sensor turns the verification matrix
    into sensors x modes, and a matrix like that does not stay green.

Generated logic, one case
    On a headless target with no CPU, ``register_sequences`` can be compiled into
    an I2C init sequencer: a ROM of register writes plus the state machine that
    walks it. That is the only path from this file into gateware, and it is a
    demonstration that np2hw reaches past the datapath.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SENSOR_ROOT = Path(__file__).parent
SCHEMA_PATH = SENSOR_ROOT / "schema.json"

# Gain and exposure are computed in Q8.8 integers: 256 is unity. The 3A loops
# run in integers too, so nothing here promotes to float except where a
# datasheet quantity is genuinely real-valued (line time in nanoseconds).
Q8 = 256


@lru_cache(maxsize=1)
def schema() -> dict:
    """The sensor description schema, loaded once."""
    return json.loads(SCHEMA_PATH.read_text())


def available() -> list[str]:
    """Names of every sensor description shipped with revela."""
    return sorted(p.parent.name for p in SENSOR_ROOT.glob("*/sensor.json"))


def path_for(name: str) -> Path:
    path = SENSOR_ROOT / name / "sensor.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no sensor description for {name!r}; revela ships {available()}")
    return path


def load(name: str, validate_description: bool = True) -> dict:
    """Load one sensor description, validating it by default."""
    description = json.loads(path_for(name).read_text())
    if validate_description:
        validate(description)
    return description


def validate(description: dict) -> None:
    """Raise if ``description`` does not satisfy the schema.

    Raises:
        jsonschema.ValidationError: with the failing path, so a contributor sees
            which field is wrong rather than that the file is wrong.
    """
    import jsonschema

    jsonschema.validate(instance=description, schema=schema())


# --------------------------------------------------------------------------- #
# Mode selection
# --------------------------------------------------------------------------- #

def mode(description: dict, name: str | None = None) -> dict:
    """One readout mode by name, or the first (the part's default)."""
    modes = description["modes"]
    if name is None:
        return modes[0]
    for entry in modes:
        if entry["name"] == name:
            return entry
    raise KeyError(
        f"{description['name']} has no mode {name!r}; it defines "
        f"{[m['name'] for m in modes]}")


def _timing(description: dict, mode_entry: dict, key: str) -> int:
    """A timing figure, taking the mode's override when it has one."""
    return int(mode_entry.get(key, description["timing"][key]))


def build_parameters(description: dict, mode_name: str | None = None) -> dict:
    """The ONLY things revela takes from a sensor at build time.

    Bit depth sets the datapath width; width sizes the line buffers. Everything
    else -- Bayer phase included -- is a runtime register.
    """
    entry = mode(description, mode_name)
    return {
        "bit_depth": int(description["format"]["bit_depth"]),
        "width": int(entry["width"]),
    }


# --------------------------------------------------------------------------- #
# Frame timing
# --------------------------------------------------------------------------- #

def line_time_ns(description: dict, mode_name: str | None = None) -> float:
    """Time to read one line, in nanoseconds.

    This is the exposure quantum: coarse integration is counted in line times,
    so nothing shorter than this is expressible on the part.
    """
    entry = mode(description, mode_name)
    stated = description["timing"].get("line_time_ns")
    if stated is not None and mode_name is None:
        return float(stated)
    line_length = _timing(description, entry, "line_length_pck")
    pixel_rate = _timing(description, entry, "pixel_rate_hz")
    return line_length * 1e9 / pixel_rate


def frame_time_ns(description: dict, mode_name: str | None = None) -> float:
    entry = mode(description, mode_name)
    return line_time_ns(description, mode_name) * _timing(
        description, entry, "frame_length_lines")


# --------------------------------------------------------------------------- #
# Exposure: time -> integration registers
# --------------------------------------------------------------------------- #

def exposure_lines(description: dict, exposure_ns: float,
                   mode_name: str | None = None) -> int:
    """Coarse integration register value for a requested exposure time.

    Clamped to the part's limits, including the margin that must remain between
    coarse integration and ``frame_length_lines``. The clamp lives here so no
    caller has to remember it; exceeding it corrupts the frame.
    """
    entry = mode(description, mode_name)
    coarse = description["exposure"]["coarse"]
    per_line = line_time_ns(description, mode_name)

    lines = int((float(exposure_ns) + per_line / 2) // per_line)   # round to nearest
    low = int(coarse["min"])
    margin = int(coarse.get("max_margin", 0))
    high = min(
        (1 << int(coarse["bits"])) - 1,
        _timing(description, entry, "frame_length_lines") - margin,
    )
    return max(low, min(high, lines))


def exposure_ns_of(description: dict, lines: int,
                   mode_name: str | None = None) -> float:
    """Actual exposure time a coarse integration value produces.

    The AE loop needs this: the value it asked for and the value the part can
    express differ by up to a line time, and integrating that error into the loop
    is how exposure oscillates.
    """
    return int(lines) * line_time_ns(description, mode_name)


def max_exposure_ns(description: dict, mode_name: str | None = None) -> float:
    entry = mode(description, mode_name)
    coarse = description["exposure"]["coarse"]
    high = min(
        (1 << int(coarse["bits"])) - 1,
        _timing(description, entry, "frame_length_lines")
        - int(coarse.get("max_margin", 0)),
    )
    return exposure_ns_of(description, high, mode_name)


# --------------------------------------------------------------------------- #
# Gain: gain -> register code
# --------------------------------------------------------------------------- #

def gain_code(description: dict, gain_q8: int, channel: str = "analogue") -> int:
    """Register code for a requested gain, in Q8.8 (256 = 1.0).

    Sony parts map analogue gain as ``gain = 256 / (256 - code)``, which is why
    this is a description field and not a constant: the mapping is not linear,
    it is not the same across vendors, and a linear approximation drifts badly at
    high gain, which is exactly where AE spends its time in low light.

    Rearranged for the code, in integers::

        gain_q8 = 65536 / (256 - code)   =>   code = 256 - 65536 / gain_q8
    """
    spec = _gain_spec(description, channel)
    model = spec["model"]
    low, high = int(spec["code_min"]), int(spec["code_max"])
    gain_q8 = max(Q8, int(gain_q8))

    if model == "sony_inverse":
        code = Q8 - (Q8 * Q8 + gain_q8 // 2) // gain_q8
    elif model == "linear_q8":
        code = gain_q8
    elif model == "db_per_step":
        code = _db_code(gain_q8, int(spec["step_millidb"]))
    else:                                       # unreachable: schema constrains it
        raise ValueError(f"unknown gain model {model!r}")
    return max(low, min(high, int(code)))


def gain_of_code(description: dict, code: int, channel: str = "analogue") -> int:
    """The gain a code actually produces, in Q8.8.

    As with exposure, the loop needs what it GOT, not what it asked for.
    """
    spec = _gain_spec(description, channel)
    model = spec["model"]
    code = max(int(spec["code_min"]), min(int(spec["code_max"]), int(code)))

    if model == "sony_inverse":
        denominator = Q8 - code
        if denominator <= 0:                    # the mapping's singularity
            return int(spec.get("max_gain_q8", Q8))
        return (Q8 * Q8) // denominator
    if model == "linear_q8":
        return code
    if model == "db_per_step":
        return _db_gain(code, int(spec["step_millidb"]))
    raise ValueError(f"unknown gain model {model!r}")


def max_gain_q8(description: dict, channel: str = "analogue") -> int:
    spec = _gain_spec(description, channel)
    stated = spec.get("max_gain_q8")
    if stated is not None:
        return int(stated)
    return gain_of_code(description, int(spec["code_max"]), channel)


def _gain_spec(description: dict, channel: str) -> dict:
    try:
        return description["gain"][channel]
    except KeyError:
        raise KeyError(
            f"{description['name']} describes no {channel!r} gain; it has "
            f"{list(description['gain'])}") from None


def _db_code(gain_q8: int, step_millidb: int) -> int:
    import math

    decibels = 20.0 * math.log10(max(1, gain_q8) / Q8)
    return int(round(decibels * 1000 / step_millidb))


def _db_gain(code: int, step_millidb: int) -> int:
    return int(round(Q8 * 10 ** (code * step_millidb / 20000.0)))
