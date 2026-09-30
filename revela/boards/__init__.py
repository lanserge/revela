# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Boards the revela ISP is built onto, one package each.

A board is the harness that tests the ISP on silicon: what carries the
stream to it, what shows the picture, and the block design joining them.
The ISP itself is not described here -- a board names the pipeline it
builds and reads that pipeline's published contract, never restating it.

A board folder is a library: its primitives -- one JSON block per jack
or resource, each carrying its own facts and constraints fragment -- and
the designs built from them. A design names its primitives by reference,
`revela.boards.<board>.<file>:<block>`, so a second design on the same
board reuses them and restates nothing.

The checks beside this file hold for any Vivado block design: checkbd
reads the script before Vivado runs, checknets the widths of every wire
drawn between our own blocks, checkhwh the design as built.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path


def resolve(ref: str) -> tuple[dict, Path]:
    """A JSON block by reference, `package.module:block`, and the folder
    it lives in (where the files it names are). Resolved through
    importlib, so a checkout and an installed wheel behave the same."""
    module, sep, name = ref.partition(":")
    package, dot, leaf = module.rpartition(".")
    if not (sep and dot and name):
        raise ValueError(f"{ref!r} is not a block reference "
                         "(package.module:block)")
    folder = Path(importlib.import_module(package).__file__).resolve().parent
    source = folder / f"{leaf}.json"
    blocks = json.loads(source.read_text())["blocks"]
    if name not in blocks:
        raise KeyError(f"{ref}: {source} defines {sorted(blocks)}")
    return blocks[name], folder
