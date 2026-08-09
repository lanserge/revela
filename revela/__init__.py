# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela -- an image signal processing pipeline written in NumPy, from which
synthesisable Verilog is generated.

The NumPy models in :mod:`revela.blocks` are written directly at the hardware's
arithmetic: explicit integer widths, explicit rounding, explicit saturation, LUTs
where the hardware has LUTs, shifts where the hardware shifts. There is one model
per block and it is both the specification and the golden reference. Verilog is
generated from it by np2hw; the two are held bit-exactly equal by the tests.

Start at :mod:`revela.compose` for composition and address allocation,
:mod:`revela.params` for how a register is declared, and :mod:`revela.stream`
for the interface between blocks.
"""

__version__ = "0.1.0"

from revela.params import (
    AddressAllocator,
    BlockInstance,
    Context,
    Param,
    ParamSet,
    StatsWindow,
)
from revela.stream import StreamSpec

__all__ = [
    "AddressAllocator",
    "BlockInstance",
    "Context",
    "Param",
    "ParamSet",
    "StatsWindow",
    "StreamSpec",
    "__version__",
]
