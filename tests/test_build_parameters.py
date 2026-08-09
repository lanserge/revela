# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Build-time register overrides: one declaration, many fixed-point variants.

The principle under test: a block's Q format and widths live on its Param
declarations and NOWHERE else -- the model reads its shift from the configured
declaration, the register map derives q_format from it, the host quantises
through it. A design overriding `frac` therefore changes the model, the RTL,
the map and the docs together, or not at all. These tests hold each limb of
that claim, plus the two failure modes designed against from the start: two
variants merging into one type, and the round trip losing the overrides.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import chain, describe, raw_frame, requires_verilator, run_cocotb
from revela import designs
from revela.blocks import whitebalance
from revela.stream import StreamSpec

WIDTH, HEIGHT, BIT_DEPTH = 16, 8, 12


def _design_with_override(frac=12, name="revela_isp"):
    description = describe(name, chain("blacklevel", "whitebalance"),
                           bit_depth=BIT_DEPTH, width=WIDTH, height=HEIGHT)
    for node in description["nodes"]:
        if node["block"] == "whitebalance":
            node["registers"] = {"gain": {"frac": frac}}
    return description


# --------------------------------------------------------------------------- #
# The variant mechanism
# --------------------------------------------------------------------------- #

def test_identical_overrides_share_one_variant_object():
    """Object identity IS type identity downstream; the cache makes it hold."""
    a = whitebalance.whitebalance.configure({"gain": {"frac": 12}})
    b = whitebalance.whitebalance.configure({"gain": {"frac": 12}})
    c = whitebalance.whitebalance.configure({"gain": {"frac": 10}})
    assert a is b and a is not c
    assert whitebalance.whitebalance.configure(None) is whitebalance.whitebalance


def test_unity_default_moves_with_the_overridden_frac():
    """Reset must stay a pass-through in EVERY variant, or an unconfigured
    Q4.12 pipeline would silently apply x16."""
    variant = whitebalance.whitebalance.configure({"gain": {"frac": 12}})
    gain = next(p for p in variant.params if p.name == "gain")
    assert (gain.q_format, gain.default) == ("u4.12", 4096)

    frame = np.full((4, 4), 777, dtype=np.uint16)
    out = variant(frame, variant.params.bind({}),
                  variant.context_view({"bayer_phase": 0}), BIT_DEPTH)
    np.testing.assert_array_equal(out, frame)


def test_the_model_reads_the_declaration_not_a_module_constant():
    """x1.5 encodes differently per variant; the result must not."""
    frame = np.full((2, 2), 100, dtype=np.uint16)
    for frac in (8, 10, 12):
        variant = whitebalance.whitebalance.configure({"gain": {"frac": frac}})
        one_and_a_half = (1 << frac) + (1 << (frac - 1))
        bound = variant.params.bind(variant.params.declaration("gain")
                                    .values(np.full((2, 2), one_and_a_half)))
        out = variant(frame, bound, variant.context_view({"bayer_phase": 0}),
                      BIT_DEPTH)
        assert out[0, 0] == 150, f"frac={frac}"


def test_refused_attributes_name_what_is_allowed():
    with pytest.raises(ValueError, match="allows \\['bits', 'frac'\\]"):
        whitebalance.whitebalance.configure({"gain": {"signed": 1}})
    with pytest.raises(KeyError, match="declares no register"):
        whitebalance.whitebalance.configure({"volume": {"bits": 8}})


# --------------------------------------------------------------------------- #
# Through the design JSON
# --------------------------------------------------------------------------- #

def test_a_design_override_reaches_the_map_the_rtl_and_the_docs():
    pipeline = designs.build(_design_with_override(frac=12))
    blocks = {b["path"]: b for b in pipeline.register_map()["blocks"]}
    wb = blocks["whitebalance"]
    assert wb["register_overrides"] == {"gain": {"frac": 12}}
    formats = {r["name"]: r["q_format"] for r in wb["registers"]}
    assert formats["gain_0_0"] == "u4.12"
    assert {r["name"]: r["default"] for r in wb["registers"]}["gain_0_0"] == 4096

    verilog = pipeline.generate(control=False).verilog
    assert "stage0 >> 12" in verilog, "the trace must use the overridden shift"


def test_two_variants_of_one_block_are_two_types_not_one():
    """The map must never say two different Q formats are the same hardware."""
    description = describe("revela_isp",
                           chain("whitebalance", prefix="a",
                                 source="in", sink="mid_unused"),
                           inputs=("in",), outputs=("mid_unused",),
                           bit_depth=BIT_DEPTH, width=WIDTH, height=HEIGHT)
    # Hand-build instead: two parallel whitebalance instances, distinct fracs.
    from revela.compose import Pipeline
    from revela.blocks import resolve

    pipeline = Pipeline("revela_two", StreamSpec(bit_depth=BIT_DEPTH),
                        WIDTH, HEIGHT, inputs=("a_in", "b_in"),
                        outputs=("a_out", "b_out"))
    pipeline.add("wb_a", resolve("whitebalance"),
                 registers={"gain": {"frac": 12}})
    pipeline.add("wb_b", resolve("whitebalance"))
    for src, dst in (("a_in", "wb_a.in"), ("wb_a.out", "a_out"),
                     ("b_in", "wb_b.in"), ("wb_b.out", "b_out")):
        pipeline.connect(src, dst)
    pipeline.validate()

    addrmap = pipeline.address_map()
    names = {inst.path: inst.block.name for inst in addrmap.instances}
    assert names["wb_a"] == "whitebalance__gain_frac12"
    assert names["wb_b"] == "whitebalance"
    assert names["wb_a"] != names["wb_b"], "variants merged into one type"

    formats = {}
    for block in pipeline.register_map()["blocks"]:
        for register in block["registers"]:
            formats[(block["path"], register["name"])] = register["q_format"]
    assert formats[("wb_a", "gain_0_0")] == "u4.12"
    assert formats[("wb_b", "gain_0_0")] == "u8.8"


def test_describe_round_trips_the_overrides_to_identical_hardware():
    """The guard that makes the feature safe: recover, rebuild, compare."""
    original = designs.build(_design_with_override(frac=10))
    recovered = designs.describe(original)
    node = next(n for n in recovered["nodes"] if n["block"] == "whitebalance")
    assert node["registers"] == {"gain": {"frac": 10}}

    designs.validate(recovered)
    rebuilt = designs.build(recovered)
    assert rebuilt.register_map() == original.register_map()
    assert rebuilt.generate().verilog == original.generate().verilog


def test_an_undeclared_override_fails_at_build_naming_the_rules():
    description = _design_with_override()
    for node in description["nodes"]:
        if node["block"] == "whitebalance":
            node["registers"] = {"gain": {"default": 999}}
    with pytest.raises(ValueError, match="allows \\['bits', 'frac'\\]"):
        designs.build(description)


# --------------------------------------------------------------------------- #
# Rule 3 does not stop at the base declaration
# --------------------------------------------------------------------------- #

@pytest.mark.verilog
@requires_verilator
def test_a_variant_is_bit_exact_too(tmp_path, rng):
    """The Q4.12 variant against its own model, in silicon-simulation.

    Same harness as the base block's test; the case carries the overrides and
    the testbench configures the SAME variant, so model and RTL derive from
    one configured declaration -- which is the entire feature.
    """
    variant = whitebalance.whitebalance.configure({"gain": {"frac": 12}})
    generated = variant.generate(StreamSpec(bit_depth=BIT_DEPTH), WIDTH, HEIGHT,
                                 module_name="revela_whitebalance")
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    gains = variant.params.declaration("gain").values(
        rng.integers(1024, 16385, (2, 2)))                     # x0.25 .. x4
    run_cocotb(
        tmp_path=tmp_path,
        verilog=generated.verilog,
        toplevel=generated.top,
        test_module="tb_block",
        case={
            "block": "whitebalance",
            "width": WIDTH, "height": HEIGHT, "bit_depth": BIT_DEPTH,
            "registers": {"gain": {"frac": 12}},
            "trials": [{"seed": int(rng.integers(0, 2**31)),
                        "context": {"bayer_phase": 1}, "values": gains,
                        "frame": frame.ravel().tolist()}],
        },
    )
