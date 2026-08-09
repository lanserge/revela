# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""SPI transport: register access over a serial link.

STUB -- the framing below is the intended design, not an implementation.

SPI is the transport of choice for a small FPGA with no CPU: four wires, a
handful of LUTs at the device end, and no software stack. It is slow, which is
the entire design constraint here.

Intended framing, a 40-bit transfer, MSB first:

    bit 39      1 = write, 0 = read
    bits 38:32  reserved, must be zero
    bits 31:0   address on the way out, data on the way back

Reads are two transfers, or one with a turnaround, depending on how many wires
the board actually routed.

The one thing that matters for this transport: ``read_block`` MUST be overridden
with an auto-incrementing burst. A statistics window is 1280 words; at one
address phase per word over a 10 MHz link that is well over a frame time, and
the control loop falls behind the sensor. With auto-increment the address is
sent once and the window streams.

Requires spidev or equivalent, which is why it is not a dependency: adding one
to build a pipeline nobody is connecting to hardware would be wrong. Ask before
adding it.
"""
from __future__ import annotations

from revela.host import Transport


class SpiTransport(Transport):
    """Not implemented yet. See the module docstring for the framing."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "the SPI transport is a declared stub: its framing is "
            "documented, its implementation is not written. Use "
            "revela.host.MemoryTransport to exercise the host API without "
            "hardware.")

    def read32(self, address: int) -> int:
        raise NotImplementedError

    def write32(self, address: int, value: int) -> None:
        raise NotImplementedError
