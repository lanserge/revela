# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Profiles: one pipeline, several sensors and several tunings.

The two things worth testing hardest are the boundary and the precedence. A
profile must be unable to change the hardware -- no blocks, no addresses -- and
when the block defaults, the sensor and the profile all speak to one register,
the right one must win, verifiably, with a record of which did.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from revela import designs, profiles
from revela.host import Device, MemoryTransport

DESIGNS = Path(__file__).parent.parent / "pipelines"
PROFILE_DIR = DESIGNS / "mono" / "imx219" / "basic" / "profiles"
EXAMPLES = sorted(DESIGNS.glob("*/*/*/profiles/*.json"))


@pytest.fixture
def indoor() -> dict:
    return profiles.load(PROFILE_DIR / "indoor.json")


@pytest.fixture
def pipeline(indoor):
    return profiles.pipeline_for(indoor)


# --------------------------------------------------------------------------- #
# The schema, and what it forbids
# --------------------------------------------------------------------------- #

def test_there_are_examples_to_check():
    assert EXAMPLES, "no example profiles found"


def test_schema_is_itself_a_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(profiles.schema())


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-5:-2] + p.parts[-1:]))
def test_example_validates(path):
    validator = jsonschema.Draft202012Validator(profiles.schema())
    errors = sorted(validator.iter_errors(json.loads(path.read_text())),
                    key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{path.name}: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors)


def test_a_profile_cannot_add_blocks(indoor):
    """Tuning must not be able to change the hardware."""
    indoor["blocks"] = [{"block": "ccm"}]
    with pytest.raises(jsonschema.ValidationError):
        profiles.validate(indoor)


def test_a_profile_cannot_declare_addresses(indoor):
    indoor["addresses"] = {"blacklevel": 256}
    with pytest.raises(jsonschema.ValidationError):
        profiles.validate(indoor)


def test_a_profile_must_name_a_sensor(indoor):
    """A tuning without a sensor is a tuning for nothing in particular."""
    del indoor["sensor"]
    with pytest.raises(jsonschema.ValidationError):
        profiles.validate(indoor)


# --------------------------------------------------------------------------- #
# Precedence: defaults, then sensor, then profile
# --------------------------------------------------------------------------- #

def test_resolution_covers_every_register(indoor, pipeline):
    """The result describes the whole pipeline, not only what was mentioned."""
    settings = profiles.resolve(indoor, pipeline)
    for stage in pipeline.stages:
        resolved = settings.for_block(stage.path)
        assert set(resolved) == {r.name for r in stage.paramset.registers}


def test_the_sensor_overrides_the_block_default(indoor, pipeline):
    """imx219 pedestals at 64, so black level must not stay at its reset 0."""
    settings = profiles.resolve(indoor, pipeline)
    assert settings.values["blacklevel"]["offset_0_0"] == -64
    assert settings.origin["blacklevel"]["offset_0_0"] == profiles.FROM_SENSOR


def test_the_profile_overrides_the_sensor(indoor, pipeline):
    """A measured per-colour pedestal beats the datasheet's single figure."""
    settings = profiles.resolve(indoor, pipeline)
    assert settings.values["blacklevel"]["offset_0_1"] == -65
    assert settings.origin["blacklevel"]["offset_0_1"] == profiles.FROM_PROFILE


def test_untouched_registers_keep_their_declared_default(indoor, pipeline):
    settings = profiles.resolve(indoor, pipeline)
    assert settings.origin["stats"]["luma_weight_0"] == profiles.FROM_DEFAULT


def test_derive_from_sensor_can_be_switched_off(indoor, pipeline):
    """Bring-up sometimes wants only reset values and explicit overrides."""
    indoor["derive_from_sensor"] = False
    settings = profiles.resolve(indoor, pipeline)
    assert settings.values["blacklevel"]["offset_0_0"] == 0
    assert settings.origin["blacklevel"]["offset_0_0"] == profiles.FROM_DEFAULT
    # The explicit values still apply.
    assert settings.origin["blacklevel"]["offset_0_1"] == profiles.FROM_PROFILE


def test_origin_is_recorded_for_every_value(indoor, pipeline):
    """'Where did this number come from' is the first question of a tuning file."""
    settings = profiles.resolve(indoor, pipeline)
    allowed = {profiles.FROM_DEFAULT, profiles.FROM_SENSOR, profiles.FROM_PROFILE}
    for path, registers in settings.origin.items():
        assert set(registers.values()) <= allowed
        assert set(registers) == set(settings.values[path])


# --------------------------------------------------------------------------- #
# Validation against the block declarations
# --------------------------------------------------------------------------- #

def test_unknown_register_is_refused(indoor, pipeline):
    indoor["values"]["blacklevel"]["offset_9_9"] = 1
    with pytest.raises(KeyError, match="offset_0_0"):
        profiles.resolve(indoor, pipeline)


def test_unknown_block_is_refused(indoor, pipeline):
    """Silently ignoring it is how a profile ends up half-applied after a rename."""
    indoor["values"]["gamma"] = {"lut_0": 1}
    with pytest.raises(KeyError, match="gamma"):
        profiles.resolve(indoor, pipeline)


def test_out_of_range_value_is_refused_with_the_description(indoor, pipeline):
    """A number too big for its register must fail here, not be masked."""
    indoor["values"]["blacklevel"]["offset_0_0"] = 70000
    with pytest.raises(ValueError, match="negated sensor pedestal"):
        profiles.resolve(indoor, pipeline)


def test_a_mode_larger_than_the_build_is_refused(indoor, pipeline):
    """Line buffers are sized at synthesis; a register cannot widen them."""
    indoor["sensor"]["mode"] = "full"
    with pytest.raises(ValueError, match="built for width"):
        profiles.resolve(indoor, pipeline)


# --------------------------------------------------------------------------- #
# One pipeline, several tunings
# --------------------------------------------------------------------------- #

def test_two_profiles_share_one_pipeline_description():
    """The whole point: structure fixed, numbers different."""
    indoor = profiles.load(PROFILE_DIR / "indoor.json")
    outdoor = profiles.load(PROFILE_DIR / "outdoor.json")
    assert indoor["pipeline"] == outdoor["pipeline"]

    one, two = profiles.pipeline_for(indoor), profiles.pipeline_for(outdoor)
    # Same description means identical hardware and identical addresses...
    assert one.generate().verilog == two.generate().verilog
    assert one.register_map() == two.register_map()

    # ...and different values.
    first = profiles.resolve(indoor, one)
    second = profiles.resolve(outdoor, two)
    assert first.values["stats"]["zones_x"] != second.values["stats"]["zones_x"]
    assert first.control["ae"]["target_q8"] != second.control["ae"]["target_q8"]


def test_block_name_key_applies_to_every_instance():
    """A stereo pipeline shares one tuning between both eyes."""
    pipeline = designs.build({
        "schema_version": 1,
        "name": "twin",
        "stream": {"bit_depth": 12},
        "geometry": {"width": 64, "height": 32},
        "inputs": [{"name": "left_in"}, {"name": "right_in"}],
        "outputs": [{"name": "left_out"}, {"name": "right_out"}],
        "nodes": [
            {"instance": "left.blacklevel", "block": "blacklevel"},
            {"instance": "right.blacklevel", "block": "blacklevel"},
        ],
        "connections": [
            {"from": "left_in", "to": "left.blacklevel.in"},
            {"from": "left.blacklevel.out", "to": "left_out"},
            {"from": "right_in", "to": "right.blacklevel.in"},
            {"from": "right.blacklevel.out", "to": "right_out"},
        ],
    })
    profile = {
        "schema_version": 1, "name": "twin_tuning",
        "sensor": {"name": "imx219"},
        # Keying is what is under test; the sensor's own geometry is not, and a
        # 64x32 toy pipeline cannot hold a 3280-wide mode.
        "derive_from_sensor": False,
        "values": {"blacklevel": {"offset_0_0": -70}},
    }
    settings = profiles.resolve(profile, pipeline)
    assert settings.values["left.blacklevel"]["offset_0_0"] == -70
    assert settings.values["right.blacklevel"]["offset_0_0"] == -70


def test_instance_key_overrides_the_block_key():
    """One eye's optics differ; only that eye's value changes."""
    pipeline = designs.build({
        "schema_version": 1,
        "name": "twin",
        "stream": {"bit_depth": 12},
        "geometry": {"width": 64, "height": 32},
        "inputs": [{"name": "left_in"}, {"name": "right_in"}],
        "outputs": [{"name": "left_out"}, {"name": "right_out"}],
        "nodes": [
            {"instance": "left.blacklevel", "block": "blacklevel"},
            {"instance": "right.blacklevel", "block": "blacklevel"},
        ],
        "connections": [
            {"from": "left_in", "to": "left.blacklevel.in"},
            {"from": "left.blacklevel.out", "to": "left_out"},
            {"from": "right_in", "to": "right.blacklevel.in"},
            {"from": "right.blacklevel.out", "to": "right_out"},
        ],
    })
    profile = {
        "schema_version": 1, "name": "twin_tuning",
        "sensor": {"name": "imx219"},
        "derive_from_sensor": False,
        "values": {
            "blacklevel": {"offset_0_0": -70},
            "right.blacklevel": {"offset_0_0": -73},
        },
    }
    settings = profiles.resolve(profile, pipeline)
    assert settings.values["left.blacklevel"]["offset_0_0"] == -70
    assert settings.values["right.blacklevel"]["offset_0_0"] == -73


# --------------------------------------------------------------------------- #
# Applying to a device
# --------------------------------------------------------------------------- #

def test_settings_apply_through_the_host_api(indoor, pipeline):
    register_map = pipeline.register_map()
    device = Device(register_map, MemoryTransport(register_map))
    settings = profiles.resolve(indoor, pipeline)

    written = settings.apply(device)
    assert written == sum(len(v) for v in settings.values.values())

    # Read back through the accessors: the device now holds the profile.
    assert device.blacklevel.offset_0_1 == -65
    assert device.pipe.window_x0 == 8
    assert device.stats.zones_x == 8


def test_a_profile_without_a_pipeline_says_so(indoor):
    del indoor["pipeline"]
    with pytest.raises(KeyError, match="does not name a pipeline"):
        profiles.pipeline_for(indoor)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-5:-2] + p.parts[-1:]))
def test_profile_sensor_matches_its_design_directory(path):
    """A profile filed under a sensor must be tuned for that sensor."""
    directory = path.parts[-4]
    declared = json.loads(path.read_text())["sensor"]["name"]
    assert declared == directory, (
        f"{path} sits under {directory}/ but is tuned for {declared!r}")


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-5:-2] + p.parts[-1:]))
def test_every_profile_resolves_against_its_design(path):
    """Every shipped tuning must actually apply to the pipeline it names."""
    profile = profiles.load(path)
    settings = profiles.resolve(profile, profiles.pipeline_for(profile))
    assert settings.values
