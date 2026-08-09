# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The control register file: the emitted decode must be the emitted map.

A register map is only worth anything if the hardware decodes exactly what the
JSON says. The two are generated from one allocation -- revela decides the
addresses, np2hw emits the decode -- and these tests hold that claim as
something that can fail, by reading the addresses back out of the generated
Verilog and comparing them with the map.

The bit-exact half is in ``tests/tb_regfile.py``: coefficients written over
AXI4-Lite, committed at a frame boundary, and the resulting frame compared with
the NumPy model.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from conftest import chain, describe, raw_frame, requires_verilator, run_cocotb
from revela import designs
from revela.blocks import blacklevel
from revela.params import BLOCK_ALIGN

WIDTH, HEIGHT, BIT_DEPTH = 16, 8, 12

# Inside `pipe`'s 256-byte region but past its last register: allocated to a
# block, decoded by nothing. Padding must not answer, or a typo in a host script
# would look like a successful write.
UNMAPPED_ADDRESS = 0x00FC

# A negative offset per CFA colour, all different, so a frame processed with the
# wrong one differs from a frame processed with the right one.
OFFSETS = {"offset_0_0": -100, "offset_0_1": -60,
           "offset_1_0": -48, "offset_1_1": -24}
BAYER_PHASE = 0b11


@pytest.fixture
def pipeline():
    return designs.build(describe("revela_isp", chain("blacklevel"),
                                  bit_depth=BIT_DEPTH, width=WIDTH, height=HEIGHT))


@pytest.fixture
def generated(pipeline):
    return pipeline.generate()


def _flat(path: str) -> str:
    return path.replace(".", "_")


def _decoded_writes(verilog: str) -> dict[str, int]:
    """``{register name: word index}`` from the register file's write decode."""
    return {name: int(word) for word, name
            in re.findall(r"^\s+(\d+): begin shadow_(\w+) <=", verilog, re.M)}


def _decoded_constants(verilog: str) -> dict[int, int]:
    """``{word index: constant}`` for the read-only words in the read decode."""
    return {int(word): int(value, 16) for word, value
            in re.findall(r"^\s+(\d+): begin s_axil_rdata <= 32'h([0-9a-f]{8});",
                          verilog, re.M)}


# --------------------------------------------------------------------------- #
# The decode is the map
# --------------------------------------------------------------------------- #

def test_every_register_decodes_at_the_address_the_map_publishes(pipeline, generated):
    """The claim the whole control plane rests on, stated so it can fail."""
    decoded = _decoded_writes(generated.verilog)
    expected = {}
    for block in pipeline.register_map()["blocks"]:
        for register in block["registers"]:
            expected[f"{_flat(block['path'])}_{register['name']}"] = \
                register["address"] // 4

    assert decoded == expected, (
        "the emitted decode and the emitted register map disagree; software "
        "reading the map would write to the wrong register")


def test_identity_words_are_read_only_constants(pipeline, generated):
    """No storage, no write path -- the value is wired into the decode."""
    constants = _decoded_constants(generated.verilog)
    decoded = _decoded_writes(generated.verilog)

    for block in pipeline.register_map()["blocks"]:
        identity = block["id_version"]
        word = identity["address"] // 4
        assert constants.get(word) == identity["value"], (
            f"{block['path']}: identity word at 0x{identity['address']:04x} is "
            f"not emitted as the constant 0x{identity['value']:08x}")
        assert f"{_flat(block['path'])}_id_version" not in decoded, (
            f"{block['path']}: the identity word has a write path; it is "
            "read-only")


def test_each_block_lands_on_its_own_aligned_region(pipeline):
    """What makes address decode a bit-slice compare rather than a comparator."""
    bases = [block["base"] for block in pipeline.register_map()["blocks"]]
    assert len(bases) == len(set(bases))
    for base in bases:
        assert base % BLOCK_ALIGN == 0, f"0x{base:04x} is not {BLOCK_ALIGN}-aligned"


def test_the_unmapped_probe_address_really_is_unmapped(pipeline):
    """Keeps the simulation test honest if the allocation ever grows into it."""
    used = {register["address"]
            for block in pipeline.register_map()["blocks"]
            for register in block["registers"]}
    used |= {block["id_version"]["address"]
             for block in pipeline.register_map()["blocks"]}
    assert UNMAPPED_ADDRESS not in used


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #

def test_the_control_top_wraps_the_datapath_top(pipeline, generated):
    names = generated.module_names()
    assert generated.top == f"{pipeline.name}_ctrl"
    assert pipeline.name in names, "the datapath top must still be emitted"
    assert generated.meta["control"]["bus"] == "axi4-lite"


def test_the_datapath_can_still_be_generated_without_a_register_file(pipeline):
    """A bit-exact testbench drives coefficients directly; it wants the datapath."""
    plain = pipeline.generate(control=False)
    assert plain.top == pipeline.name
    assert "s_axil_awaddr" not in plain.verilog
    assert "control" not in plain.meta


def test_context_reaches_the_datapath_from_pipe_not_from_a_copy(pipeline, generated):
    """Rule 2: one width register in a pipeline, fanned out.

    The context ports of the datapath top are driven by `pipe`'s registers, so a
    resolution change is one write. A per-block copy would show up here as a
    context port driven by a register belonging to the consuming block.
    """
    bind = pipeline.control_bind({})
    assert bind["ctx_width"] == "param_pipe_width"
    assert bind["ctx_bayer_phase"] == "param_pipe_bayer_phase"
    for port, driver in bind.items():
        if port.startswith("ctx_"):
            assert driver.startswith("param_pipe_"), (
                f"{port} is driven by {driver}, which is not the pipe block; "
                "context has one owner")


def test_a_signed_register_reads_back_sign_extended(generated):
    """A host reading a negative black-level offset must not see 65436."""
    assert re.search(r"s_axil_rdata <= \{\{16\{shadow_blacklevel_offset_0_0\[15\]\}\}",
                     generated.verilog), (
        "the signed offset register is not sign-extended on read-back")


def test_a_declared_but_unbuilt_block_says_so_in_both_artefacts():
    """`stats` has registers and no datapath; neither the map nor the RTL hides it.

    The registers are still decoded, because the map and the decode have to be
    one document. What must not happen is software writing a coefficient, seeing
    it read back, and having no way to know it reached nothing.
    """
    pipeline = designs.build(describe("revela_isp", chain("blacklevel", "stats"),
                                      bit_depth=BIT_DEPTH, width=WIDTH,
                                      height=HEIGHT))
    blocks = {block["path"]: block for block in pipeline.register_map()["blocks"]}
    assert blocks["blacklevel"]["implemented"] is True
    assert blocks["pipe"]["implemented"] is True, "pipe has no datapath to build"
    assert blocks["stats"]["implemented"] is False
    assert blocks["stats"]["not_implemented_reason"]

    verilog = pipeline.generate().verilog
    assert "stats is declared but not built yet" in verilog
    # And it is still decoded, at the address the map publishes.
    decoded = _decoded_writes(verilog)
    for register in blocks["stats"]["registers"]:
        assert decoded[f"stats_{register['name']}"] == register["address"] // 4


def test_a_register_description_reaches_the_register_file(generated):
    """Rule 4, at the control plane: the description is written once."""
    assert "// blacklevel.offset_0_0 (Q16.0). R: Signed offset ADDED" in generated.verilog
    assert "Write the negated sensor pedestal" in generated.verilog


# --------------------------------------------------------------------------- #
# np2hw refuses an allocation it cannot decode
# --------------------------------------------------------------------------- #

def test_two_registers_at_one_address_are_refused():
    from np2hw import Reg, axil_regfile

    with pytest.raises(ValueError, match="both claim offset"):
        axil_regfile([Reg("a", 8, offset=0x10), Reg("b", 8, offset=0x10)],
                     addr_bits=8)


def test_an_unaligned_offset_is_refused():
    from np2hw import Reg, axil_regfile

    with pytest.raises(ValueError, match="not word-aligned"):
        axil_regfile([Reg("a", 8, offset=0x02)], addr_bits=8)


def test_an_offset_outside_the_decoded_space_is_refused():
    from np2hw import Reg, axil_regfile

    with pytest.raises(ValueError, match="outside the"):
        axil_regfile([Reg("a", 8, offset=0x400)], addr_bits=8)


def test_an_unbound_parameter_port_is_refused(pipeline):
    """Every port needs a driver, and the error says what is on offer."""
    from np2hw import control_wrap

    with pytest.raises(KeyError, match="not bound"):
        control_wrap({"module": "m",
                      "interface": {"clock": "clk", "reset": "rst",
                                    "param_prefix": "", "streams": [],
                                    "params": [("param_x", 8, False)]}},
                     pipeline.registers(), bind={}, frame_sync=False)


# --------------------------------------------------------------------------- #
# The simulation
# --------------------------------------------------------------------------- #

@requires_verilator
def test_registers_written_over_axi_reach_the_datapath(tmp_path, rng, generated,
                                                       pipeline):
    """The end-to-end claim: a host writes the map, the image changes to match.

    Everything the testbench needs is in the emitted register map, because that
    is all a host will have.
    """
    frame = raw_frame(rng, WIDTH, HEIGHT, BIT_DEPTH)
    case = {
        "width": WIDTH, "height": HEIGHT, "bit_depth": BIT_DEPTH,
        "map": pipeline.register_map(),
        "unmapped_address": UNMAPPED_ADDRESS,
        "offsets": OFFSETS,
        "bayer_phase": BAYER_PHASE,
        "frame": frame.tolist(),
        "seed": 20260808,
    }
    run_cocotb(tmp_path, generated.verilog, generated.top, "tb_regfile", case)
