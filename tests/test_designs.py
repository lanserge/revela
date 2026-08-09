# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Pipelines described in JSON: structure in, hardware out.

The description is an INPUT and the register map is an OUTPUT, and the boundary
between them is the thing most worth testing. A description must be unable to
express an address -- otherwise a pipeline builder could emit a map that
disagreed with the hardware it generated, which is precisely what rule 2 exists
to stop.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from revela import designs
from revela.blocks import registry, resolve

DESIGNS = Path(__file__).parent.parent / "pipelines"
EXAMPLES = sorted(DESIGNS.glob("*/*/*/pipeline.json"))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def mono() -> dict:
    return load_json(next(p for p in EXAMPLES if p.parts[-4] == "mono"))


# --------------------------------------------------------------------------- #
# The schema
# --------------------------------------------------------------------------- #

def test_there_are_examples_to_check():
    assert EXAMPLES, "no example pipeline descriptions found"


def test_schema_is_itself_a_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(designs.schema())


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_example_validates(path):
    validator = jsonschema.Draft202012Validator(designs.schema())
    errors = sorted(validator.iter_errors(load_json(path)), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{path.name}: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors)


def test_a_description_cannot_declare_addresses(mono):
    """The load-bearing constraint of the whole format.

    Addresses are ALLOCATED from block-local offsets at composition time. A
    description that could pin one would be a second copy of the address map,
    free to drift from the hardware, and a builder GUI would be the thing that
    drifted it.
    """
    mono["addresses"] = {"blacklevel": 256}
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(mono)


def test_a_node_cannot_carry_a_base(mono):
    mono["nodes"][0]["base"] = 512
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(mono)


def test_region_bases_must_stay_aligned(mono):
    """Alignment is what keeps address decode a bit-slice compare."""
    mono["regions"] = {"config_base": 0x0080}
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(mono)


def test_schema_rejects_an_unknown_key(mono):
    mono["blocks"] = []              # plausible confusion with `nodes`
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(mono)


def test_schema_rejects_a_future_version(mono):
    mono["schema_version"] = 2
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(mono)


def test_schema_requires_at_least_one_node(mono):
    mono["nodes"] = []
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(mono)


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #

def test_blocks_are_resolved_by_name():
    """A description names blocks the way a person does, and imports nothing."""
    assert resolve("blacklevel").name == "blacklevel"
    assert set(registry()) >= {"pipe", "blacklevel", "stats"}


def test_unknown_block_lists_what_exists():
    with pytest.raises(KeyError, match="blacklevel"):
        resolve("no_such_block")


def test_a_declared_stub_cannot_be_composed():
    """Stubs have no ParamSet, so they cannot be named in a description.

    Better to fail here than to compose a pipeline containing a block that has
    no model and therefore cannot be verified.
    """
    with pytest.raises(KeyError, match="declared stubs"):
        resolve("demosaic")


def test_build_creates_the_declared_instances():
    pipeline = designs.load(next(p for p in EXAMPLES if p.parts[-4] == "mono"))
    paths = [s.path for s in pipeline.datapath]
    assert paths == ["blacklevel", "stats", "whitebalance", "gamma"]
    assert pipeline.stages[0].path == "pipe"


def test_dotted_instances_group_into_independent_graphs():
    stereo = next(p for p in EXAMPLES if p.parts[-4] == "stereo")
    pipeline = designs.load(stereo)
    paths = {s.path for s in pipeline.datapath}
    assert paths == {"left.blacklevel", "left.whitebalance", "left.gamma",
                     "left.stats", "right.blacklevel", "right.whitebalance",
                     "right.gamma", "right.stats"}
    # Separate instances at separate bases...
    assert (pipeline.stage("left.blacklevel").instance.base
            != pipeline.stage("right.blacklevel").instance.base)
    # ...and the two graphs genuinely never touch.
    for source, sink in pipeline.edges:
        eyes = {e.split("_")[0].split(".")[0] for e in (str(source), str(sink))}
        assert len(eyes) == 1, f"{source} -> {sink} crosses between eyes"


def test_a_sensor_supplies_only_build_time_parameters(mono):
    """Bit depth and width. Bayer phase stays a register."""
    pipeline = designs.build(mono)
    assert pipeline.spec.bit_depth == 10          # imx219 is a 10-bit part
    assert pipeline.width == 1640                 # binned_2x2 mode

    mapping = pipeline.register_map()
    owners = [b["path"] for b in mapping["blocks"]
              if any(r["name"] == "bayer_phase" for r in b["registers"])]
    assert owners == ["pipe"], "bayer_phase must be a register, not baked in"


def _netlist(name, nodes, connections):
    return {
        "schema_version": 1, "name": name,
        "stream": {"bit_depth": 12},
        "geometry": {"width": 64, "height": 32},
        "nodes": nodes, "connections": connections,
    }


def test_two_instances_of_one_block_in_one_graph():
    """Two of a thing in one datapath, distinguished by instance name."""
    pipeline = designs.build(_netlist(
        "twice",
        [{"instance": "bl_a", "block": "blacklevel"},
         {"instance": "bl_b", "block": "blacklevel"}],
        [{"from": "in", "to": "bl_a.in"},
         {"from": "bl_a.out", "to": "bl_b.in"},
         {"from": "bl_b.out", "to": "out"}]))
    assert len({s.instance.base for s in pipeline.datapath}) == 2


def test_duplicate_instances_are_rejected():
    with pytest.raises(ValueError, match="already in this pipeline"):
        designs.build(_netlist(
            "clash",
            [{"instance": "bl", "block": "blacklevel"},
             {"instance": "bl", "block": "blacklevel"}],
            [{"from": "in", "to": "bl.in"}, {"from": "bl.out", "to": "out"}]))


# --------------------------------------------------------------------------- #
# The netlist must be a well-formed graph
# --------------------------------------------------------------------------- #

def test_an_undriven_input_is_refused():
    """A block input with no driver is a stream that never arrives."""
    with pytest.raises(ValueError, match="not connected"):
        designs.build(_netlist(
            "orphan",
            [{"instance": "bl", "block": "blacklevel"}],
            [{"from": "bl.out", "to": "out"}]))


def test_two_drivers_on_one_input_are_refused():
    """Two sources on one input is a short."""
    with pytest.raises(ValueError, match="exactly one driver"):
        designs.build(_netlist(
            "shorted",
            [{"instance": "a", "block": "blacklevel"},
             {"instance": "b", "block": "blacklevel"}],
            [{"from": "in", "to": "a.in"},
             {"from": "in", "to": "b.in"},
             {"from": "a.out", "to": "b.in"},
             {"from": "b.out", "to": "out"}]))


def test_an_unconnected_pipeline_output_is_refused():
    with pytest.raises(ValueError, match="output 'out' is not connected"):
        designs.build(_netlist(
            "dangling",
            [{"instance": "bl", "block": "blacklevel"}],
            [{"from": "in", "to": "bl.in"}]))


def test_a_cycle_is_refused():
    """A streaming pipeline must be feed-forward."""
    with pytest.raises(ValueError, match="cycle"):
        # a and b feed each other; the pipeline stream bypasses them, so every
        # input still has exactly one driver and only the cycle is wrong.
        designs.build(_netlist(
            "loop",
            [{"instance": "a", "block": "blacklevel"},
             {"instance": "b", "block": "blacklevel"}],
            [{"from": "in", "to": "out"},
             {"from": "a.out", "to": "b.in"},
             {"from": "b.out", "to": "a.in"}]))


def test_a_tap_to_a_sink_is_allowed():
    """Statistics observe the datapath; the stream carries on past them."""
    pipeline = designs.build(_netlist(
        "tapped",
        [{"instance": "bl", "block": "blacklevel"},
         {"instance": "st", "block": "stats"}],
        [{"from": "in", "to": "bl.in"},
         {"from": "bl.out", "to": "out"},
         {"from": "bl.out", "to": "st.in"}]))
    assert "stats" in {s.paramset.block for s in pipeline.datapath}
    verilog = pipeline.generate().verilog
    assert "tap: sink never stalls" in verilog


def test_a_fork_between_two_real_datapaths_is_refused():
    """Both would apply backpressure, and there is no buffering fork element.

    Refusing is better than emitting a fork that deadlocks the first time one
    branch stalls -- which is a bug that only appears under load.
    """
    with pytest.raises(ValueError, match="buffering fork element"):
        # Only b reaches the output; c is a second real consumer of a.out, and
        # that alone is the problem -- both would apply backpressure.
        designs.build(_netlist(
            "forked",
            [{"instance": "a", "block": "blacklevel"},
             {"instance": "b", "block": "blacklevel"},
             {"instance": "c", "block": "blacklevel"}],
            [{"from": "in", "to": "a.in"},
             {"from": "a.out", "to": "b.in"},
             {"from": "a.out", "to": "c.in"},
             {"from": "b.out", "to": "out"}]))


def test_an_unknown_port_lists_what_the_block_has():
    with pytest.raises(KeyError, match="outputs are"):
        designs.build(_netlist(
            "badport",
            [{"instance": "bl", "block": "blacklevel"}],
            [{"from": "in", "to": "bl.in"}, {"from": "bl.chroma", "to": "out"}]))


# --------------------------------------------------------------------------- #
# Round trip
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_describe_round_trips_to_identical_hardware(path):
    """Allocation must be a pure function of structure.

    A description recovered from a built pipeline, rebuilt, must produce the
    same addresses and the same Verilog. If it did not, the address map would
    depend on something outside the description -- and a builder GUI could not be
    trusted to reproduce a design.
    """
    original = designs.load(path)
    recovered = designs.describe(original)
    designs.validate(recovered)
    rebuilt = designs.build(recovered)

    assert rebuilt.register_map() == original.register_map()
    assert rebuilt.generate().verilog == original.generate().verilog


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_described_pipelines_generate(path):
    generated = designs.load(path).generate()
    assert generated.verilog.strip().endswith("endmodule")
    assert generated.top in generated.module_names()


# --------------------------------------------------------------------------- #
# The directory layout must agree with the description
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_directory_topology_matches_the_stream_count(path):
    """`pipelines/<topology>/<sensor>/<variant>/` states things the JSON also states.

    Anything stated twice can disagree. A design filed under `stereo/` that
    describes one stream is mis-filed, and nobody would notice until they went
    looking for a stereo design and found a mono one.
    """
    topology = path.parts[-4]
    inputs = len(load_json(path).get("inputs", [{"name": "in"}]))
    if topology == "mono":
        assert inputs == 1, (
            f"{path} is filed under mono/ but has {inputs} pipeline inputs")
    else:
        assert inputs >= 2, (
            f"{path} is filed under {topology}/ but has only {inputs} input")


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_directory_sensor_matches_the_declared_sensor(path):
    """The sensor is in the path and in the JSON; they must be the same sensor."""
    directory = path.parts[-3]
    declared = load_json(path).get("sensor", {}).get("name")
    assert declared == directory, (
        f"{path} sits under {directory}/ but declares sensor {declared!r}")


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_design_name_follows_its_path(path):
    """Module names must stay unique once many variants are built side by side."""
    topology, sensor, variant = path.parts[-4:-1]
    assert load_json(path)["name"] == f"revela_{topology}_{sensor}_{variant}"


def test_design_names_are_unique():
    names = [load_json(p)["name"] for p in EXAMPLES]
    assert len(names) == len(set(names)), f"duplicate pipeline names: {names}"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_every_design_has_at_least_one_profile(path):
    """A design with no tuning has never been run against anything."""
    profiles_dir = path.parent / "profiles"
    assert sorted(profiles_dir.glob("*.json")), f"{path.parent} has no profiles/"


# --------------------------------------------------------------------------- #
# Subsystems: describe a sub-graph once, instantiate it by name
# --------------------------------------------------------------------------- #

def _eye_subsystem(name="revela_twin"):
    return {
        "schema_version": 1, "name": name,
        "stream": {"bit_depth": 12},
        "geometry": {"width": 64, "height": 32},
        "inputs": [{"name": "l_in"}, {"name": "r_in"}],
        "outputs": [{"name": "l_out"}, {"name": "r_out"}],
        "subsystems": [{
            "name": "eye",
            "inputs": [{"name": "sensor"}], "outputs": [{"name": "video"}],
            "nodes": [{"instance": "blacklevel", "block": "blacklevel"},
                      {"instance": "stats", "block": "stats"}],
            "connections": [{"from": "sensor", "to": "blacklevel.in"},
                            {"from": "blacklevel.out", "to": "video"},
                            {"from": "blacklevel.out", "to": "stats.in"}],
        }],
        "nodes": [{"instance": "left", "subsystem": "eye"},
                  {"instance": "right", "subsystem": "eye"}],
        "connections": [{"from": "l_in", "to": "left.sensor"},
                        {"from": "left.video", "to": "l_out"},
                        {"from": "r_in", "to": "right.sensor"},
                        {"from": "right.video", "to": "r_out"}],
    }


def _eye_spelled_out(name="revela_twin"):
    return {
        "schema_version": 1, "name": name,
        "stream": {"bit_depth": 12},
        "geometry": {"width": 64, "height": 32},
        "inputs": [{"name": "l_in"}, {"name": "r_in"}],
        "outputs": [{"name": "l_out"}, {"name": "r_out"}],
        "nodes": [{"instance": "left.blacklevel", "block": "blacklevel"},
                  {"instance": "left.stats", "block": "stats"},
                  {"instance": "right.blacklevel", "block": "blacklevel"},
                  {"instance": "right.stats", "block": "stats"}],
        "connections": [{"from": "l_in", "to": "left.blacklevel.in"},
                        {"from": "left.blacklevel.out", "to": "l_out"},
                        {"from": "left.blacklevel.out", "to": "left.stats.in"},
                        {"from": "r_in", "to": "right.blacklevel.in"},
                        {"from": "right.blacklevel.out", "to": "r_out"},
                        {"from": "right.blacklevel.out", "to": "right.stats.in"}],
    }


def test_a_subsystem_allocates_ordinary_instances():
    """Inside a subsystem is not a separate namespace for addressing."""
    pipeline = designs.build(_eye_subsystem())
    paths = [s.path for s in pipeline.stages]
    assert paths == ["pipe", "left.blacklevel", "left.stats",
                     "right.blacklevel", "right.stats"]


def test_boundary_ports_flatten_away():
    """`left.sensor` is a name, not a component: it joins two edges into one."""
    pipeline = designs.build(_eye_subsystem())
    edges = {(str(s), str(k)) for s, k in pipeline.edges}
    assert ("l_in", "left.blacklevel.in") in edges
    assert ("right.blacklevel.out", "r_out") in edges
    # The boundary itself does not survive as an endpoint.
    assert not any("left.sensor" in e or "right.video" in e
                   for edge in edges for e in edge)


def test_subsystem_and_spelled_out_give_the_same_register_map():
    """The property that makes subsystems safe to adopt.

    Addresses are allocated per INSTANCE either way, so software cannot tell the
    difference. Only the emitted RTL structure changes.
    """
    assert (designs.build(_eye_subsystem()).register_map()
            == designs.build(_eye_spelled_out()).register_map())


def test_a_subsystem_emits_one_module_not_a_copy_per_instance():
    """The point of the feature, stated as something that can fail."""
    shared = designs.build(_eye_subsystem()).generate().module_names()
    copied = designs.build(_eye_spelled_out()).generate().module_names()

    assert sum("blacklevel" in m for m in shared) == 1, shared
    assert sum("blacklevel" in m for m in copied) == 2, copied


def test_an_unknown_subsystem_lists_what_is_defined():
    description = _eye_subsystem()
    description["nodes"][0]["subsystem"] = "no_such_subsystem"
    with pytest.raises(KeyError, match="eye"):
        designs.build(description)


def test_an_unconnected_subsystem_input_is_refused():
    description = _eye_subsystem()
    description["connections"] = [e for e in description["connections"]
                                  if e["to"] != "left.sensor"]
    with pytest.raises(ValueError, match="input 'sensor' is not connected"):
        designs.build(description)


def test_a_node_cannot_be_both_a_block_and_a_subsystem():
    description = _eye_subsystem()
    description["nodes"][0]["block"] = "blacklevel"
    with pytest.raises(jsonschema.ValidationError):
        designs.validate(description)


def test_the_shipped_stereo_design_uses_a_subsystem():
    """The design in the tree should use the capability, not predate it."""
    stereo = next(p for p in EXAMPLES if p.parts[-4] == "stereo")
    description = load_json(stereo)
    assert description.get("subsystems"), "stereo should describe an eye subsystem"
    assert all("subsystem" in n for n in description["nodes"])
