# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela's blocks are found by name, and a declared stub cannot be composed.

The netlist rules -- drivers, cycles, taps, forks, ports -- are np2hw's and
are tested there.
"""
import pytest

from revela.blocks import registry, resolve


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

def test_blocks_are_found_by_name():
    assert resolve("blacklevel").name == "blacklevel"
    assert set(registry()) >= {"pipe", "blacklevel", "stats"}


def test_unknown_block_lists_what_exists():
    with pytest.raises(KeyError, match="blacklevel"):
        resolve("no_such_block")


def test_a_declared_stub_cannot_be_composed():
    """Stubs have no ParamSet, so they cannot be named in a design.

    Better to fail here than to compose a pipeline containing a block that has
    no model and therefore cannot be verified.
    """
    with pytest.raises(KeyError, match="declared stubs"):
        resolve("demosaic")
