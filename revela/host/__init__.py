# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Host-side register access, generated from the emitted register map.

The device object mirrors the pipeline's hierarchy, so the instance path that
:mod:`revela.compose` allocated is the attribute chain software uses::

    dev = Device(register_map, transport)
    dev.pipe.bayer_phase = 0
    dev.left.blacklevel.offset_0_0 = -64
    dev.left.ccm.m00 = 512
    zones = dev.left.stats.zones.read()

**Nothing here hardcodes an address.** Every address comes from the JSON the
pipeline emitted, which came from the same declarations that produced the
Verilog. There is no table in this file to keep in step with the hardware,
because a table like that is only ever correct on the day it is written.

The first thing to do with a new connection is :meth:`Device.verify`, which reads
each block's ID-and-version word and checks it against the map. A mismatch means
the loaded bitstream is not the one this software was built against, and it is
far better to find that out before writing coefficients into whatever happens to
be at those addresses.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np


class Transport(ABC):
    """A way to reach the device's register space.

    Deliberately minimal: everything above is built from 32-bit reads and
    writes, plus a bulk read that a transport may implement more efficiently
    than a loop. A new link needs three methods.
    """

    @abstractmethod
    def read32(self, address: int) -> int:
        """Read one 32-bit register."""

    @abstractmethod
    def write32(self, address: int, value: int) -> None:
        """Write one 32-bit register."""

    def read_block(self, address: int, words: int) -> list[int]:
        """Read a contiguous run of registers.

        The default is a loop; a transport with burst support should override
        it. Statistics windows are read through this, and a 1280-word window
        read one round trip at a time over a slow link is the difference between
        keeping up with the frame rate and not.
        """
        return [self.read32(address + 4 * i) for i in range(words)]

    def close(self) -> None:
        """Release the link. Overridden where there is something to release."""


class MemoryTransport(Transport):
    """An in-process register file, for tests and for bring-up without hardware.

    Registers read back their reset values until written, so software can be
    exercised end to end -- including the ID-and-version check -- before any
    gateware exists.
    """

    def __init__(self, register_map: dict | None = None):
        self.storage: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []
        if register_map is not None:
            self.preload(register_map)

    def preload(self, register_map: dict) -> None:
        """Populate reset values and ID words, as a real device would show them."""
        for block in register_map["blocks"]:
            identity = block["id_version"]
            self.storage[identity["address"]] = identity["value"]
            for register in block["registers"]:
                self.storage[register["address"]] = register["default"]

    def read32(self, address: int) -> int:
        return self.storage.get(int(address), 0)

    def write32(self, address: int, value: int) -> None:
        self.storage[int(address)] = int(value) & 0xFFFFFFFF
        self.writes.append((int(address), int(value)))


# --------------------------------------------------------------------------- #
# Register accessors
# --------------------------------------------------------------------------- #

class Register:
    """One configuration register, described by the map.

    Reads and writes go through the transport at the address the map gives.
    Signed registers are converted to and from two's complement here, once, so
    that callers work in ordinary Python integers and nobody has to remember
    which registers are signed.
    """

    __slots__ = ("_spec", "_transport", "_path")

    def __init__(self, spec: dict, transport: Transport, path: str):
        self._spec = spec
        self._transport = transport
        self._path = path

    @property
    def address(self) -> int:
        return self._spec["address"]

    @property
    def description(self) -> str:
        return self._spec["description"]

    @property
    def q_format(self) -> str:
        return self._spec["q_format"]

    def get(self) -> int:
        raw = self._transport.read32(self.address) & ((1 << self._spec["bits"]) - 1)
        if self._spec["signed"] and raw >> (self._spec["bits"] - 1):
            raw -= 1 << self._spec["bits"]
        return raw

    def set(self, value: int) -> None:
        value = int(value)
        low, high = self.limits
        if not low <= value <= high:
            raise ValueError(
                f"{self._path} = {value} is outside [{low}, {high}] for a "
                f"{self._spec['bits']}-bit "
                f"{'signed' if self._spec['signed'] else 'unsigned'} register "
                f"({self.q_format}). {self.description}")
        self._transport.write32(self.address, value & ((1 << self._spec["bits"]) - 1))

    @property
    def limits(self) -> tuple[int, int]:
        bits = self._spec["bits"]
        if self._spec["signed"]:
            return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        return 0, (1 << bits) - 1

    # -- fixed point ---------------------------------------------------------- #

    def get_real(self) -> float:
        """The register's value as a human number, using its declared Q format.

        Presentation only. The loops and the models work in raw integers.
        """
        return self.get() / (1 << self._spec["frac"])

    def set_real(self, value: float) -> None:
        scale = 1 << self._spec["frac"]
        raw = int(round(float(value) * scale))
        low, high = self.limits
        self.set(max(low, min(high, raw)))

    def __repr__(self) -> str:
        return (f"<Register {self._path} @ 0x{self.address:04x} "
                f"{self.q_format} = {self.get()}>")


class StatsWindowAccessor:
    """A double-buffered statistics RAM window.

    Read in bulk and returned as a 2-D ``(records, fields)`` array, which is the
    memory's own shape and matches what :func:`revela.blocks.stats.model`
    produces -- so comparing hardware against the model is an array comparison
    with no reindexing in between.
    """

    __slots__ = ("_spec", "_transport", "_path")

    def __init__(self, spec: dict, transport: Transport, path: str):
        self._spec = spec
        self._transport = transport
        self._path = path

    @property
    def base(self) -> int:
        return self._spec["base"]

    @property
    def layout(self) -> tuple[str, ...]:
        return tuple(self._spec["layout"])

    def read(self, buffer: int = 0) -> np.ndarray:
        """Read one buffer as ``(records, fields)``.

        Args:
            buffer: which of the two buffers to read. 0 is the completed frame,
                which is what a control loop wants; 1 is the one currently
                accumulating and is only useful for debugging.
        """
        if buffer not in (0, 1):
            raise ValueError(f"{self._path}: buffer must be 0 or 1, got {buffer}")
        address = self.base + buffer * self._spec["buffer_bytes"]
        words = self._transport.read_block(address, self._spec["words"])
        return np.array(words, dtype=np.uint32).reshape(
            self._spec["records"], self._spec["record_words"])

    def field(self, name: str, buffer: int = 0) -> np.ndarray:
        try:
            column = self.layout.index(name)
        except ValueError:
            raise KeyError(
                f"{self._path} has no field {name!r}; it holds {list(self.layout)}"
            ) from None
        return self.read(buffer)[:, column]

    def __repr__(self) -> str:
        return (f"<StatsWindow {self._path} @ 0x{self.base:04x} "
                f"{self._spec['records']} records of {list(self.layout)}>")


class Block:
    """One block instance: its registers, its statistics windows, its identity."""

    def __init__(self, spec: dict, transport: Transport):
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_transport", transport)
        registers = {r["name"]: Register(r, transport, f"{spec['path']}.{r['name']}")
                     for r in spec["registers"]}
        windows = {w["name"]: StatsWindowAccessor(w, transport,
                                                  f"{spec['path']}.{w['name']}")
                   for w in spec["statistics"]}
        object.__setattr__(self, "_registers", registers)
        object.__setattr__(self, "_windows", windows)

    # -- identity -------------------------------------------------------------- #

    @property
    def path(self) -> str:
        return self._spec["path"]

    @property
    def base(self) -> int:
        return self._spec["base"]

    def id_version(self) -> int:
        return self._transport.read32(self._spec["id_version"]["address"])

    def verify(self) -> None:
        """Check the block answers with the ID and version the map expects."""
        expected = self._spec["id_version"]["value"]
        actual = self.id_version()
        if actual != expected:
            raise RuntimeError(
                f"{self.path} @ 0x{self.base:04x}: expected ID/version "
                f"0x{expected:08x} (block {self._spec['block']!r} "
                f"v{self._spec['version']}), read 0x{actual:08x}. The loaded "
                f"bitstream does not match this register map.")

    # -- attribute access ------------------------------------------------------- #

    def __getattr__(self, name: str):
        registers = object.__getattribute__(self, "_registers")
        if name in registers:
            return registers[name].get()
        windows = object.__getattribute__(self, "_windows")
        if name in windows:
            return windows[name]
        spec = object.__getattribute__(self, "_spec")
        raise AttributeError(
            f"{spec['path']} has no register or window {name!r}; it has "
            f"{sorted(registers)} and {sorted(windows)}")

    def __setattr__(self, name: str, value) -> None:
        registers = object.__getattribute__(self, "_registers")
        if name not in registers:
            spec = object.__getattribute__(self, "_spec")
            raise AttributeError(
                f"{spec['path']} has no writable register {name!r}; it has "
                f"{sorted(registers)}")
        registers[name].set(value)

    def register(self, name: str) -> Register:
        """The Register object, for its address, description or Q format."""
        return self._registers[name]

    def write(self, values: dict[str, int]) -> None:
        """Write several registers at once, e.g. a whole coefficient set."""
        for name, value in values.items():
            setattr(self, name, value)

    def read_all(self) -> dict[str, int]:
        return {name: reg.get() for name, reg in self._registers.items()}

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(self._registers) | set(self._windows))

    def __repr__(self) -> str:
        return (f"<Block {self.path} @ 0x{self.base:04x} "
                f"{len(self._registers)} registers>")


class Namespace:
    """An intermediate level of the instance path, e.g. ``dev.left``."""

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_children", {})

    def _add(self, key: str, child) -> None:
        object.__getattribute__(self, "_children")[key] = child

    def __getattr__(self, name: str):
        children = object.__getattribute__(self, "_children")
        if name in children:
            return children[name]
        raise AttributeError(
            f"{object.__getattribute__(self, '_name')} has no {name!r}; it has "
            f"{sorted(children)}")

    def __setattr__(self, name: str, value):
        raise AttributeError(
            f"{object.__getattribute__(self, '_name')}.{name} is a group of "
            "blocks, not a register")

    def __dir__(self):
        return sorted(object.__getattribute__(self, "_children"))

    def __repr__(self) -> str:
        return (f"<Namespace {object.__getattribute__(self, '_name')}: "
                f"{sorted(object.__getattribute__(self, '_children'))}>")


class Device:
    """A pipeline, reachable over a transport, described by its register map."""

    def __init__(self, register_map: dict, transport: Transport):
        # Taken from the generator rather than restated: this library is shipped
        # with revela, so the version it understands IS the version revela emits,
        # and a literal here would be a copy free to fall behind.
        from revela.compose import MAP_FORMAT_VERSION as expected

        if register_map.get("map_format_version") != expected:
            raise ValueError(
                f"register map format version "
                f"{register_map.get('map_format_version')!r} is not the {expected} "
                "this host library understands")

        object.__setattr__(self, "_map", register_map)
        object.__setattr__(self, "_transport", transport)
        object.__setattr__(self, "_blocks", {})
        object.__setattr__(self, "_root", Namespace(register_map["name"]))

        for spec in register_map["blocks"]:
            block = Block(spec, transport)
            self._blocks[spec["path"]] = block
            # Mirror the instance path: "left.blacklevel" becomes dev.left.blacklevel.
            parts = spec["path"].split(".")
            node = self._root
            for part in parts[:-1]:
                children = object.__getattribute__(node, "_children")
                if part not in children:
                    node._add(part, Namespace(part))
                node = children[part]
            node._add(parts[-1], block)

    @classmethod
    def from_file(cls, path: str | Path, transport: Transport) -> "Device":
        return cls(json.loads(Path(path).read_text()), transport)

    # -- access ---------------------------------------------------------------- #

    def __getattr__(self, name: str):
        root = object.__getattribute__(self, "_root")
        try:
            return getattr(root, name)
        except AttributeError:
            raise AttributeError(
                f"{object.__getattribute__(self, '_map')['name']} has no {name!r}; "
                f"it has {dir(root)}") from None

    def __setattr__(self, name: str, value):
        raise AttributeError(
            "assign to a register, not to a block: e.g. dev.left.blacklevel."
            "offset_0_0 = -64")

    def block(self, path: str) -> Block:
        """One block instance by its full path."""
        try:
            return self._blocks[path]
        except KeyError:
            raise KeyError(
                f"no block instance {path!r}; this device has "
                f"{sorted(self._blocks)}") from None

    @property
    def blocks(self) -> dict[str, Block]:
        return dict(self._blocks)

    def __dir__(self):
        return sorted(set(super().__dir__())
                      | set(object.__getattribute__(self, "_root").__dir__()))

    # -- bring-up --------------------------------------------------------------- #

    def verify(self) -> None:
        """Check every block's ID-and-version word before touching anything else.

        This is the reason the word exists. A stale register map and a new
        bitstream will otherwise write coefficients to whatever now lives at
        those addresses, and the resulting behaviour looks like an algorithm bug
        rather than a version mismatch.
        """
        for block in self._blocks.values():
            block.verify()

    def reset_defaults(self) -> None:
        """Write every register's declared reset value."""
        for spec in self._map["blocks"]:
            block = self._blocks[spec["path"]]
            for register in spec["registers"]:
                setattr(block, register["name"], register["default"])

    def __repr__(self) -> str:
        return (f"<Device {self._map['name']!r}: {len(self._blocks)} block "
                f"instances, {self._map['address_bits']}-bit address space>")
