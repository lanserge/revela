# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Sensor descriptions must satisfy the schema, and stay internally consistent.

Bad contributions should fail in CI, not in hardware. The schema catches
structural mistakes; the consistency tests below catch the ones a schema cannot
express -- a stated maximum gain that disagrees with the code range, a stated
line time that disagrees with the pixel rate, a licence-provenance field left
unanswered.
"""
from __future__ import annotations

import json

import jsonschema
import pytest

from revela import sensors
from revela.blocks import blacklevel, pipe

SENSOR_NAMES = sensors.available()


def test_there_are_sensors_to_check():
    """A schema test that validates nothing passes trivially forever."""
    assert SENSOR_NAMES, "no sensor descriptions found under revela/sensors/"


def test_schema_is_itself_a_valid_json_schema():
    schema = sensors.schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == 1


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_sensor_validates_against_schema(name):
    description = json.loads(sensors.path_for(name).read_text())
    # Validate explicitly rather than through load(), so the failure names the
    # offending field instead of just the file.
    validator = jsonschema.Draft202012Validator(sensors.schema())
    errors = sorted(validator.iter_errors(description), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{name}: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors)


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_directory_name_matches_declared_name(name):
    assert sensors.load(name)["name"] == name


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_provenance_states_no_gpl_driver_was_transcribed(name):
    """The licence question must be answered explicitly, not left blank.

    Register tables in the Linux kernel media drivers are GPL-2.0 and are
    incompatible with this project's licence. Requiring an explicit `false`
    means a contributor has to consider the question rather than skip past it.
    """
    provenance = sensors.load(name)["provenance"]
    assert "gpl_driver_transcribed" in provenance, (
        f"{name}: provenance must state gpl_driver_transcribed explicitly")
    assert provenance["gpl_driver_transcribed"] is False
    assert provenance["derived_from"], f"{name}: provenance.derived_from is empty"


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_no_per_unit_calibration_is_committed(name):
    """A CCM, a shading mesh or a defect map here would be one unit's data.

    They vary per unit, per lens and per illuminant. Committing them would ship
    one camera's measurements as though they described every part of the model.
    """
    description = json.loads(sensors.path_for(name).read_text())
    forbidden = {"calibration", "ccm", "colour_matrix", "color_matrix",
                 "lens_shading", "shading_mesh", "defect_map", "bad_pixels"}
    present = forbidden & set(description)
    assert not present, (
        f"{name}: {sorted(present)} is per-unit calibration data. It is a "
        "calibration output, loaded at runtime, and must not live in the repo.")


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_stated_max_gain_matches_the_code_range(name):
    """A stated maximum must agree with the mapping and the code limit.

    The schema can require the field but cannot check it against the model; a
    figure copied from a marketing table rather than derived from `code_max` is
    exactly the kind of thing that silently mis-clamps the AE loop at the top of
    its range.
    """
    description = sensors.load(name)
    for channel in ("analogue", "digital"):
        if channel not in description["gain"]:
            continue
        spec = description["gain"][channel]
        if "max_gain_q8" not in spec:
            continue
        derived = sensors.gain_of_code(description, spec["code_max"], channel)
        assert spec["max_gain_q8"] == derived, (
            f"{name}: {channel} gain states max_gain_q8={spec['max_gain_q8']} "
            f"but code_max={spec['code_max']} under model {spec['model']!r} "
            f"gives {derived}")


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_stated_line_time_matches_the_derivation(name):
    """If line_time_ns is stated, it must agree with line_length / pixel_rate."""
    description = sensors.load(name)
    stated = description["timing"].get("line_time_ns")
    if stated is None:
        pytest.skip(f"{name} does not state line_time_ns")
    timing = description["timing"]
    derived = timing["line_length_pck"] * 1e9 / timing["pixel_rate_hz"]
    assert abs(stated - derived) < 1.0, (
        f"{name}: states line_time_ns={stated} but line_length_pck / "
        f"pixel_rate_hz gives {derived:.2f}")


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_mode_names_are_unique(name):
    modes = [m["name"] for m in sensors.load(name)["modes"]]
    assert len(modes) == len(set(modes)), f"{name}: duplicate mode names in {modes}"


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_coarse_integration_fits_the_shortest_mode(name):
    """The exposure clamp must leave at least one usable line in every mode."""
    description = sensors.load(name)
    for mode in description["modes"]:
        longest = sensors.max_exposure_ns(description, mode["name"])
        assert longest > 0, (
            f"{name}: mode {mode['name']!r} leaves no usable exposure range")


# --------------------------------------------------------------------------- #
# What the rest of the library does with a description
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_only_bit_depth_and_width_are_build_time(name):
    """Rule: exactly two things come from a sensor at build time."""
    parameters = sensors.build_parameters(sensors.load(name))
    assert set(parameters) == {"bit_depth", "width"}


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_context_registers_derive_from_the_description(name):
    """Bayer phase reaches the hardware as a register, never as a build option."""
    description = sensors.load(name)
    context = pipe.from_sensor(description)
    assert 0 <= context["bayer_phase"] <= 3
    assert pipe.BAYER_ORDER_NAMES[context["bayer_phase"]] == description["cfa"]["order"]
    assert context["bit_depth"] == description["format"]["bit_depth"]
    # Every value must fit the register the pipe block declares for it.
    for signal, value in context.items():
        declared = pipe.pipe.params.param(signal)
        low, high = declared.limits
        assert low <= value <= high, (
            f"{name}: context {signal}={value} does not fit the "
            f"{declared.bits}-bit register the pipe block declares")


@pytest.mark.parametrize("name", SENSOR_NAMES)
def test_black_level_registers_derive_from_the_pedestal(name):
    """The datasheet's positive pedestal becomes the register's negative offset."""
    description = sensors.load(name)
    values = blacklevel.offsets_from_sensor(description)
    pedestal = description["black_level"]["pedestal"]
    assert set(values) == {"offset_0_0", "offset_0_1", "offset_1_0", "offset_1_1"}
    assert all(v <= 0 for v in values.values()), (
        f"{name}: black level offsets must be negative to remove a pedestal")
    if "per_colour" not in description["black_level"]:
        assert all(v == -pedestal for v in values.values())
    # And they must fit the declared register.
    for register, value in values.items():
        low, high = blacklevel.blacklevel.params.param(register).limits
        assert low <= value <= high


# --------------------------------------------------------------------------- #
# The schema must actually reject bad input
# --------------------------------------------------------------------------- #

def _valid_description() -> dict:
    return json.loads(sensors.path_for(SENSOR_NAMES[0]).read_text())


def test_schema_rejects_committed_calibration():
    description = _valid_description()
    description["calibration"] = {"ccm": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
    with pytest.raises(jsonschema.ValidationError):
        sensors.validate(description)


def test_schema_rejects_a_claimed_gpl_transcription():
    description = _valid_description()
    description["provenance"]["gpl_driver_transcribed"] = True
    with pytest.raises(jsonschema.ValidationError):
        sensors.validate(description)


def test_schema_rejects_an_unknown_cfa_order():
    description = _valid_description()
    description["cfa"]["order"] = "RGBG"
    with pytest.raises(jsonschema.ValidationError):
        sensors.validate(description)


def test_schema_rejects_an_odd_mode_dimension():
    """An odd dimension has no consistent Bayer phase."""
    description = _valid_description()
    description["modes"][0]["width"] = 1281
    with pytest.raises(jsonschema.ValidationError):
        sensors.validate(description)


def test_schema_rejects_an_unknown_top_level_key():
    """additionalProperties is false so a typo fails rather than being ignored."""
    description = _valid_description()
    description["blacklevel"] = 64          # plausible typo for black_level
    with pytest.raises(jsonschema.ValidationError):
        sensors.validate(description)


def test_schema_rejects_a_future_schema_version():
    description = _valid_description()
    description["schema_version"] = 2
    with pytest.raises(jsonschema.ValidationError):
        sensors.validate(description)


def test_unknown_sensor_reports_what_is_available():
    with pytest.raises(FileNotFoundError, match="imx219"):
        sensors.load("no_such_sensor")
