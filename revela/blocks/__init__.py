# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela's ISP blocks: one file per block, one model per block.

A block is np2hw's (np2hw.blocks): its model -- the decorated function,
written at the hardware's arithmetic, the specification and the golden
reference -- and the declarations its arithmetic cannot state. A block knows
nothing of the pipeline it is placed in: a revela design names revela's
pipeline context (``revela.blocks.pipe:VOCABULARY``) and binds each block's
inputs to it.

Blocks are discovered, not listed (:func:`registry`): a block is available
to a pipeline description as soon as it is declared.
"""
from __future__ import annotations

from np2hw.blocks import block, registry as _registry, resolve as _resolve

#: Declare one of revela's blocks: np2hw's block.
ispblock = block


def registry() -> dict:
    """Every block declared in revela, keyed by name."""
    return _registry(__name__)


def resolve(name: str):
    """revela's block named ``name``, or an error listing what exists."""
    return _resolve(__name__, name)
