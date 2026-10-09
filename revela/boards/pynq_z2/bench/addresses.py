# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Where each register slave sits, from the address map the build writes.

The design's register bus places every slave; the build writes where, by
the slave's cell name, to address_map.json beside the bitstream. The
hardware handoff cannot say -- Vivado does not see through the bus -- so
this file is the one statement, and no script here writes an address down.

    from addresses import Addresses
    regs = Addresses.beside("rx.bit")
    status = regs.mmio("status_gpio")
"""
import json
from pathlib import Path


class Addresses:
    def __init__(self, path):
        self.path = Path(path)
        self.slaves = json.loads(self.path.read_text())

    @classmethod
    def beside(cls, bitstream):
        """The address map written with this bitstream."""
        return cls(Path(bitstream).with_name("address_map.json"))

    def __contains__(self, name):
        return name in self.slaves

    def base(self, name):
        if name not in self.slaves:
            raise KeyError(f"{self.path.name} places no {name!r}; it places "
                           f"{sorted(self.slaves)}")
        return self.slaves[name]["base"]

    def mmio(self, name):
        """The slave's registers, its whole window."""
        from pynq import MMIO
        return MMIO(self.base(name), self.slaves[name]["size"])
