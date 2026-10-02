# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""A design is a netlist, and a netlist must be a buildable graph.

Each design here is a composite, written to a library and loaded by
reference -- the one way a pipeline is described. What is checked is the
graph: every block input driven exactly once, no cycle, a tap allowed and a
fork between two real datapaths refused, a port that exists.
"""
import pytest

from conftest import block_ref, build, chain, describe
from revela.blocks import registry, resolve

STREAM = {"bits": 12, "max_width": 64, "max_height": 32}


def _netlist(name, nodes, connections, context=None):
    """A design of ``nodes`` ({instance: block name}) wired by
    ``connections``, taking a 12-bit stream of up to 64x32 at ``in``."""
    port = {"name": "in", **STREAM}
    if context is not None:
        port["context"] = context
    return {"blocks": {name: {
        "inputs": [port], "outputs": [{"name": "out"}],
        "nodes": {inst: {"block": block_ref(b)} for inst, b in nodes.items()},
        "connections": [list(c) for c in connections]}}}


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

def test_blocks_are_found_by_name():
    assert resolve("blacklevel").name == "blacklevel"
    assert set(registry()) >= {"pipe", "blacklevel", "stats"}


def test_unknown_block_lists_what_exists():
    with pytest.raises(KeyError, match="blacklevel"):
        resolve("no_such_block")


def test_a_declared_stub_cannot_be_composed():
    """Stubs have no ParamSet, so they cannot be named in a design.

    Better to fail here than to compose a pipeline containing a block that has
    no model and therefore cannot be verified.
    """
    with pytest.raises(KeyError, match="declared stubs"):
        resolve("demosaic")


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #

def test_build_creates_the_declared_instances():
    """Declaration order for addresses, the context block first; a tap sits
    in the datapath where it watches."""
    pipeline = build(describe("ordered",
                              chain("blacklevel", "stats", "whitebalance")))
    assert [s.path for s in pipeline.datapath] == \
        ["blacklevel", "stats", "whitebalance"]
    assert pipeline.stages[0].path == "pipe"


def test_prefixed_chains_are_independent_graphs():
    pipeline = build(describe(
        "eyes",
        chain("blacklevel", "stats", prefix="left", source="left_in",
              sink="left_out"),
        chain("blacklevel", "stats", prefix="right", source="right_in",
              sink="right_out"),
        inputs=("left_in", "right_in"), outputs=("left_out", "right_out")))
    assert {s.path for s in pipeline.datapath} == {
        "left.blacklevel", "left.stats", "right.blacklevel", "right.stats"}
    # Separate instances at separate bases...
    assert (pipeline.stage("left.blacklevel").instance.base
            != pipeline.stage("right.blacklevel").instance.base)
    # ...and the two graphs genuinely never touch.
    for source, sink in pipeline.edges:
        eyes = {e.split("_")[0].split(".")[0] for e in (str(source), str(sink))}
        assert len(eyes) == 1, f"{source} -> {sink} crosses between eyes"


def test_two_instances_of_one_block_in_one_graph():
    """Two of a thing in one datapath, distinguished by instance name."""
    pipeline = build(_netlist(
        "twice", {"bl_a": "blacklevel", "bl_b": "blacklevel"},
        [("in", "bl_a.in"), ("bl_a.out", "bl_b.in"), ("bl_b.out", "out")]))
    assert len({s.instance.base for s in pipeline.datapath}) == 2


# --------------------------------------------------------------------------- #
# Stream context
# --------------------------------------------------------------------------- #

def test_stream_context_becomes_ports_not_registers():
    """A header-carried fact gets a port and loses its register.

    When the stream itself carries its geometry and CFA phase (a bayerlink
    header does), those context facts must stop existing as registers
    entirely -- a register copy of a header fact is a second answer software
    could set to disagree with the picture arriving. What stays is what is
    genuinely software's choice: the crop window.
    """
    carried = ["bayer_phase", "bit_depth", "height", "width"]
    pipeline = build(_netlist("ctx_carried", {"bl": "blacklevel"},
                              [("in", "bl.in"), ("bl.out", "out")],
                              context=carried))
    pipe = next(b for b in pipeline.register_map()["blocks"]
                if b["path"] == "pipe")
    names = {r["name"] for r in pipe["registers"]}
    assert not names & set(carried), \
        f"{sorted(names & set(carried))} kept registers"
    assert {"window_x0", "window_y0", "window_x1", "window_y1"} <= names
    assert "ctx_width" in pipeline.generate(control=True).verilog


def test_an_unknown_context_name_is_refused():
    """The vocabulary is the per-frame header's, closed by the schema."""
    with pytest.raises(ValueError, match="'depth' is not one of"):
        build(_netlist("ctx_bad", {"bl": "blacklevel"},
                       [("in", "bl.in"), ("bl.out", "out")],
                       context=["depth"]))


# --------------------------------------------------------------------------- #
# The netlist must be a well-formed graph
# --------------------------------------------------------------------------- #

def test_an_undriven_input_is_refused():
    """A block input with no driver is a stream that never arrives."""
    with pytest.raises(ValueError, match="not connected"):
        build(_netlist("orphan", {"bl": "blacklevel"}, [("bl.out", "out")]))


def test_two_drivers_on_one_input_are_refused():
    """Two sources on one input is a short."""
    with pytest.raises(ValueError, match="already driven"):
        build(_netlist(
            "shorted", {"a": "blacklevel", "b": "blacklevel"},
            [("in", "a.in"), ("in", "b.in"), ("a.out", "b.in"),
             ("b.out", "out")]))


def test_an_unconnected_pipeline_output_is_refused():
    with pytest.raises(ValueError, match="output out is driven by nothing"):
        build(_netlist("dangling", {"bl": "blacklevel"}, [("in", "bl.in")]))


def test_a_cycle_is_refused():
    """A streaming pipeline must be feed-forward."""
    with pytest.raises(ValueError, match="cycle"):
        # a and b feed each other; the pipeline stream bypasses them, so every
        # input still has exactly one driver and only the cycle is wrong.
        build(_netlist(
            "loop", {"a": "blacklevel", "b": "blacklevel"},
            [("in", "out"), ("a.out", "b.in"), ("b.out", "a.in")]))


def test_a_tap_to_a_sink_is_allowed():
    """Statistics observe the datapath; the stream carries on past them."""
    pipeline = build(_netlist(
        "tapped", {"bl": "blacklevel", "st": "stats"},
        [("in", "bl.in"), ("bl.out", "out"), ("bl.out", "st.in")]))
    assert "stats" in {s.paramset.block for s in pipeline.datapath}
    assert "tap: sink never stalls" in pipeline.generate().verilog


def test_a_fork_between_two_real_datapaths_is_refused():
    """Both would apply backpressure, and there is no buffering fork element.

    Refusing is better than emitting a fork that deadlocks the first time one
    branch stalls -- which is a bug that only appears under load.
    """
    with pytest.raises(ValueError, match="buffering fork element"):
        # Only b reaches the output; c is a second real consumer of a.out, and
        # that alone is the problem -- both would apply backpressure.
        build(_netlist(
            "forked", {"a": "blacklevel", "b": "blacklevel", "c": "blacklevel"},
            [("in", "a.in"), ("a.out", "b.in"), ("a.out", "c.in"),
             ("b.out", "out")]))


def test_an_unknown_port_lists_what_the_block_has():
    with pytest.raises(ValueError, match=r"has no output 'chroma' \(it has \['out'\]\)"):
        build(_netlist("badport", {"bl": "blacklevel"},
                       [("in", "bl.in"), ("bl.chroma", "out")]))


# --------------------------------------------------------------------------- #
# One composite, instantiated twice
# --------------------------------------------------------------------------- #

def _two_eyes(shared: bool) -> dict:
    """Two eyes of blacklevel -> whitebalance: one composite instantiated
    twice, or the same run spelled out as two."""
    description = describe(
        "twin",
        chain("blacklevel", "whitebalance", prefix="left",
              source="left_in", sink="left_out"),
        chain("blacklevel", "whitebalance", prefix="right",
              source="right_in", sink="right_out"),
        inputs=("left_in", "right_in"), outputs=("left_out", "right_out"))
    if shared:
        blocks = description["blocks"]
        blocks["eye"] = blocks.pop("left")
        del blocks["right"]
        for eye in ("left", "right"):
            blocks["twin"]["nodes"][eye]["block"] = "{lib}:eye"
    return description


def test_a_shared_composite_keeps_the_spelled_out_register_map():
    """Addresses are allocated per INSTANCE, so sharing the description
    changes nothing a host can see."""
    shared, spelled = build(_two_eyes(True)), build(_two_eyes(False))
    a, b = shared.register_map(), spelled.register_map()
    a.pop("composites"), b.pop("composites")
    assert a == b


def test_a_shared_composite_emits_one_module_per_block():
    shared = build(_two_eyes(True)).generate().module_names()
    spelled = build(_two_eyes(False)).generate().module_names()
    assert "revela_eye_blacklevel" in shared
    assert "revela_left_blacklevel" in spelled and \
        "revela_right_blacklevel" in spelled
    assert len(shared) < len(spelled)


# --------------------------------------------------------------------------- #
# Allocation is a pure function of structure
# --------------------------------------------------------------------------- #

def test_allocation_is_a_pure_function_of_the_design():
    """Built twice, a design is the same hardware at the same addresses; and
    the order its connections are written in moves nothing -- only the
    declaration order of its nodes assigns addresses. A builder GUI writing
    the same graph in another order must get the same map."""
    import copy
    import random

    description = describe("pure", chain("blacklevel", "stats",
                                         "whitebalance"))
    def addresses(pipeline):
        # the map less the record of which library each build was read
        # from -- each build here is its own
        regmap = pipeline.register_map()
        regmap.pop("composites")
        return regmap

    first, again = build(description), build(copy.deepcopy(description))
    assert addresses(first) == addresses(again)
    assert first.generate().verilog == again.generate().verilog

    shuffled = copy.deepcopy(description)
    random.Random(7).shuffle(shuffled["blocks"]["pure"]["connections"])
    assert shuffled != description
    assert addresses(build(shuffled)) == addresses(first)
