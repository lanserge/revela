# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The GPIO fields by name, from the layout file the build writes.

Every bit a bench script reads or writes has one statement: the design's
declaration of its GPIO blocks, from which the build derived both the
fabric and gpio_layout.json, which sits beside the bitstream. This reads
that file; no script here writes a bit position down.

    from layout import Layout
    fields = Layout.beside("rx.bit")
    fields.verify(lambda gpio: MMIO(overlay.ip_dict[gpio]["phys_addr"], 0x1000))
    fields.read(mmio, "status_gpio", "hdr_valid")

A field marked `constant` is a word the fabric holds whatever software does
-- the layout's own checksum, built in -- and its `value` is what the build
put there. verify() reads every one back before a script trusts the file:
a layout beside the wrong bitstream is refused by name, not misread.
    fields.write(mmio, "ctrl_gpio", "grab", 1)
"""
import json
from pathlib import Path


class Layout:
    def __init__(self, path):
        self.path = Path(path)
        self.gpios = json.loads(self.path.read_text())

    @classmethod
    def beside(cls, bitstream):
        """The layout written with this bitstream."""
        return cls(Path(bitstream).with_name("gpio_layout.json"))

    def field(self, gpio, name):
        """(register offset, low bit, width) of one field."""
        for channel in self.gpios[gpio]["channels"]:
            if name in channel["fields"]:
                f = channel["fields"][name]
                return channel["offset"], f["lsb"], f["bits"]
        raise KeyError(f"{self.path}: {gpio} has no field {name!r} in this "
                       "build")

    def decode(self, gpio, name, word):
        """One field out of a word already read from its channel."""
        _, lsb, bits = self.field(gpio, name)
        return (word >> lsb) & ((1 << bits) - 1)

    def read(self, mmio, gpio, name):
        offset, _, _ = self.field(gpio, name)
        return self.decode(gpio, name, mmio.read(offset))

    def verify(self, mmio_of):
        """Refuse this layout unless the fabric's constants read what it
        says. ``mmio_of(gpio)`` is the MMIO of a GPIO block, by name."""
        checked = 0
        for gpio, block in self.gpios.items():
            for channel in block["channels"]:
                for name, f in channel["fields"].items():
                    if not f.get("constant"):
                        continue
                    got = self.read(mmio_of(gpio), gpio, name)
                    if got != f["value"]:
                        raise RuntimeError(
                            f"{self.path} is not this bitstream's: {gpio} "
                            f"{name} reads 0x{got:08X}, the file says "
                            f"0x{f['value']:08X} -- copy the gpio_layout.json "
                            "built with the loaded bitstream")
                    checked += 1
        if not checked:
            raise RuntimeError(f"{self.path} states no constant to check "
                               "it against the fabric by")

    def write(self, mmio, gpio, name, value):
        """Set one field, leaving the rest of its word as it is."""
        offset, lsb, bits = self.field(gpio, name)
        mask = ((1 << bits) - 1) << lsb
        mmio.write(offset, (mmio.read(offset) & ~mask) | ((value << lsb) & mask))
