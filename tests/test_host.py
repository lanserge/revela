# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The host API, driven against an in-process register file.

Two claims are under test. First, that the API mirrors the pipeline's hierarchy,
so ``dev.left.blacklevel.offset_0_0`` reaches the register the allocator placed
at that instance's base. Second, and more important, that NOTHING here knows an
address: every one comes from the emitted JSON, so the map and the hardware
cannot drift apart without the ID-and-version check catching it.
"""
from __future__ import annotations

import numpy as np
import pytest

from revela.blocks import blacklevel, stats
from revela.host import Device, MemoryTransport, Register
from revela.host.pynq import PynqTransport
from revela.host.spi import SpiTransport
from revela.host.udp import UdpTransport
from conftest import chain, describe

from revela import designs
from revela.stream import StreamSpec


@pytest.fixture
def register_map() -> dict:
    return designs.build(describe(
        "revela_stereo",
        chain("blacklevel", "stats", prefix="left",
              source="left_in", sink="left_out"),
        chain("blacklevel", "stats", prefix="right",
              source="right_in", sink="right_out"),
        inputs=("left_in", "right_in"), outputs=("left_out", "right_out"))).register_map()


@pytest.fixture
def device(register_map) -> Device:
    return Device(register_map, MemoryTransport(register_map))


# --------------------------------------------------------------------------- #
# The API mirrors the hierarchy
# --------------------------------------------------------------------------- #

def test_attribute_path_matches_the_instance_path(device):
    """dev.left.blacklevel is the instance the allocator called left.blacklevel."""
    assert device.left.blacklevel.path == "left.blacklevel"
    assert device.right.blacklevel.path == "right.blacklevel"
    assert device.pipe.path == "pipe"


def test_writes_reach_the_address_the_map_gave(device, register_map):
    device.left.blacklevel.offset_0_0 = -64

    expected = next(r["address"] for b in register_map["blocks"]
                    if b["path"] == "left.blacklevel"
                    for r in b["registers"] if r["name"] == "offset_0_0")
    assert device._transport.writes[-1][0] == expected


def test_the_two_eyes_are_independent(device):
    """The whole point of per-instance allocation, checked through the host."""
    device.left.blacklevel.offset_0_0 = -64
    device.right.blacklevel.offset_0_0 = -100

    assert device.left.blacklevel.offset_0_0 == -64
    assert device.right.blacklevel.offset_0_0 == -100


def test_signed_registers_round_trip_through_twos_complement(device):
    for value in (-32768, -64, -1, 0, 1, 32767):
        device.left.blacklevel.offset_1_1 = value
        assert device.left.blacklevel.offset_1_1 == value


def test_out_of_range_write_is_refused_with_the_description(device):
    with pytest.raises(ValueError, match="negated sensor pedestal"):
        device.left.blacklevel.offset_0_0 = 40000


def test_unknown_register_lists_what_exists(device):
    with pytest.raises(AttributeError, match="offset_0_0"):
        device.left.blacklevel.offset_9_9 = 1


def test_unknown_block_lists_what_exists(device):
    with pytest.raises(AttributeError, match="left"):
        _ = device.centre


def test_assigning_to_a_block_is_refused(device):
    with pytest.raises(AttributeError, match="assign to a register"):
        device.left = 1


def test_fixed_point_accessors_use_the_declared_q_format(register_map):
    transport = MemoryTransport(register_map)
    device = Device(register_map, transport)
    gain: Register = device.left.stats.register("luma_weight_1")
    assert gain.q_format == "u9.0"

    # A Q8.8 register presents 1.0 as 256; the raw integer is what is written.
    weight = device.left.stats.register("zone_width")
    weight.set(120)
    assert weight.get() == 120


# --------------------------------------------------------------------------- #
# Nothing hardcodes an address
# --------------------------------------------------------------------------- #

def test_addresses_come_only_from_the_map(device, register_map):
    """Every accessor's address must be traceable to the emitted JSON."""
    known = {r["address"] for b in register_map["blocks"] for r in b["registers"]}
    known |= {b["id_version"]["address"] for b in register_map["blocks"]}

    for path, block in device.blocks.items():
        for name in block.read_all():
            assert block.register(name).address in known, (
                f"{path}.{name} resolved to an address not present in the map")


def test_device_rejects_an_unknown_map_format(register_map):
    register_map["map_format_version"] = 99
    with pytest.raises(ValueError, match="map format version"):
        Device(register_map, MemoryTransport())


# --------------------------------------------------------------------------- #
# Bring-up: the ID-and-version word
# --------------------------------------------------------------------------- #

def test_verify_passes_against_a_matching_device(device):
    device.verify()


def test_verify_catches_a_mismatched_bitstream(register_map):
    transport = MemoryTransport(register_map)
    device = Device(register_map, transport)

    # Simulate a bitstream built from a different version of the block.
    address = register_map["blocks"][1]["id_version"]["address"]
    transport.storage[address] ^= 0x0001

    with pytest.raises(RuntimeError, match="does not match this register map"):
        device.verify()


def test_verify_catches_a_device_that_is_not_there(register_map):
    """All-zero reads are what an absent or unconfigured device looks like."""
    device = Device(register_map, MemoryTransport())      # nothing preloaded
    with pytest.raises(RuntimeError, match="expected ID/version"):
        device.verify()


def test_reset_defaults_writes_every_declared_default(device, register_map):
    device.left.blacklevel.offset_0_0 = -64
    device.reset_defaults()

    for block in register_map["blocks"]:
        accessor = device.block(block["path"])
        for register in block["registers"]:
            assert getattr(accessor, register["name"]) == register["default"]


# --------------------------------------------------------------------------- #
# Statistics windows
# --------------------------------------------------------------------------- #

def test_statistics_read_back_in_the_models_shape(device, register_map):
    """The host's array and the model's array must be the same shape.

    They are compared directly when checking hardware against the model, so a
    reshape or a transpose between them would be a silent source of confusion.
    """
    window = device.left.stats.zones
    read = window.read()

    assert read.ndim == 2
    assert read.shape[1] == len(stats.STATS_LAYOUT)
    assert window.layout == stats.STATS_LAYOUT

    model_output = stats.model(
        np.zeros((32, 32), dtype=np.uint16),
        stats.model.params.bind(stats.default_registers(
            stats.ZONES_X, stats.ZONES_Y, (0, 0, 32, 32))),
        bayer_phase=0, window=(0, 0, 32, 32))
    assert read.shape == model_output.shape


def test_the_two_buffers_are_at_different_addresses(device):
    """Double buffering: the host reads frame N while N+1 accumulates."""
    window = device.left.stats.zones
    transport = device._transport

    before = len(transport.storage)
    window.read(0)
    window.read(1)
    assert before == len(transport.storage)      # reads must not create entries

    spec = window._spec
    assert spec["buffer_bytes"] * 2 == spec["size_bytes"]


def test_statistics_field_lookup_is_by_name(device):
    column = device.left.stats.zones.field("count")
    assert column.shape == (stats.ZONES_X * stats.ZONES_Y,)

    with pytest.raises(KeyError, match="sum_r"):
        device.left.stats.zones.field("sum_infrared")


def test_stats_windows_of_the_two_eyes_do_not_alias(device):
    assert device.left.stats.zones.base != device.right.stats.zones.base


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("transport", [SpiTransport, UdpTransport, PynqTransport])
def test_transport_stubs_say_so(transport):
    with pytest.raises(NotImplementedError, match="declared stub"):
        transport()


def test_memory_transport_block_read_is_contiguous():
    transport = MemoryTransport()
    for i in range(8):
        transport.write32(0x100 + 4 * i, i * 11)
    assert transport.read_block(0x100, 8) == [i * 11 for i in range(8)]
