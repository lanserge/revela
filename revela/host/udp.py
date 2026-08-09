# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""UDP transport: register access over Ethernet.

STUB -- the framing below is the intended design, not an implementation.

The natural transport when the device is on a network: no drivers, no kernel
module, works from any machine, and fast enough that reading statistics every
frame is not a consideration.

Intended framing, one request datagram and one reply:

    byte 0      opcode: 0 = read, 1 = write
    byte 1      word count, so a whole statistics window is one exchange
    bytes 2:3   sequence number, echoed in the reply
    bytes 4:7   address
    bytes 8:    payload, little-endian 32-bit words

UDP rather than TCP is deliberate: the exchange is request/reply with a naturally
idempotent retry, so TCP's ordering and retransmission add latency and head-of-
line blocking in exchange for guarantees this protocol does not need. The
sequence number exists so that a late reply from a timed-out request is
discarded rather than mistaken for the answer to the next one -- which is the
failure this transport will actually hit, and it silently returns the wrong
register values if the number is not checked.

Needs only the standard library. It is a stub because there is no gateway on the
device side yet, not because of a dependency.
"""
from __future__ import annotations

from revela.host import Transport


class UdpTransport(Transport):
    """Not implemented yet. See the module docstring for the framing."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "the UDP transport is a declared stub: its framing is "
            "documented, its implementation is not written. Use "
            "revela.host.MemoryTransport to exercise the host API without "
            "hardware.")

    def read32(self, address: int) -> int:
        raise NotImplementedError

    def write32(self, address: int, value: int) -> None:
        raise NotImplementedError
