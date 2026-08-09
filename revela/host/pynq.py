# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""PYNQ transport: register access through memory-mapped AXI on a Zynq.

STUB -- the intended design, not an implementation.

On a PYNQ-Z2 the pipeline sits on the PS-PL AXI bus and its registers are simply
memory. This transport is the thinnest of the three: pynq.MMIO over the
pipeline's aperture, so reads and writes become loads and stores and bulk reads
are memcpy-fast.

    from pynq import Overlay, MMIO
    overlay = Overlay("revela.bit")
    mmio = MMIO(base_address, address_range)

Two things this transport must get right, both invisible until they bite:

CACHE COHERENCY. The register aperture must be mapped uncached. It usually is by
default, but a cached mapping produces a device that ignores writes until
something else happens to flush the line -- which presents as an intermittent
hardware fault and is not one.

BASE ADDRESS. It comes from the overlay's address map, which the tools generate
from the block design. It does NOT come from revela's register map: revela's
addresses are OFFSETS within the pipeline's aperture, and where that aperture
sits in the Zynq's address space is a platform fact. Adding the two is this
transport's job, and it is the only place the distinction appears.

Requires the pynq package, which only exists on the board. Not a dependency; ask
before adding it.
"""
from __future__ import annotations

from revela.host import Transport


class PynqTransport(Transport):
    """Not implemented yet. See the module docstring for the framing."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "the PYNQ transport is a declared stub: its framing is "
            "documented, its implementation is not written. Use "
            "revela.host.MemoryTransport to exercise the host API without "
            "hardware.")

    def read32(self, address: int) -> int:
        raise NotImplementedError

    def write32(self, address: int, value: int) -> None:
        raise NotImplementedError
