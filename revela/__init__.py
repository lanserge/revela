# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela -- an image signal processing pipeline written in NumPy, from which
synthesisable Verilog is generated.

The NumPy models in :mod:`revela.blocks` are written directly at the hardware's
arithmetic: explicit integer widths, explicit rounding, explicit saturation, LUTs
where the hardware has LUTs, shifts where the hardware shifts. There is one model
per block and it is both the specification and the golden reference. Verilog is
generated from it by np2hw; the two are held bit-exactly equal by the tests.

A block is np2hw's (:mod:`np2hw.blocks`): its model plus the declarations
its arithmetic cannot state, registers included (:mod:`np2hw.blocks.params`).
revela is the content: the blocks in :mod:`revela.blocks`, the pipelines
built of them, the sensors' tunings, and the boards that demonstrate them.
"""

# The version has ONE owner: the packaging metadata in
# pyproject.toml. Read back from the INSTALLED distribution
# rather than restated here, so this can never claim a version
# other than the one pip actually resolved.
from importlib.metadata import version as _version

__version__ = _version("revela")

__all__ = ["__version__"]
