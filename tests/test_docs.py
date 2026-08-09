# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The documentation may not drift from the registry.

Prose restates code, and prose has no test suite of its own -- twice now a
block has shipped complete while the README still named it a stub. These
tests turn that drift into a red build: the registry is the one statement of
which blocks exist and which are built, so the human-facing surfaces are
checked against it rather than against anyone's memory.
"""
from __future__ import annotations

from pathlib import Path

from revela.blocks import registry

ROOT = Path(__file__).resolve().parent.parent


def _status_section() -> str:
    text = (ROOT / "README.md").read_text()
    _, _, after = text.partition("## Status")
    assert after, "README has no '## Status' section"
    return after


def test_readme_status_names_every_complete_block():
    """A block that is built must be claimed, a stub must not be.

    The status paragraph is the first thing an evaluator reads; a complete
    block missing from it undersells the project, and a stub listed as
    complete is a false claim -- both are drift from the registry.
    """
    status = _status_section()
    for name, block in registry().items():
        if block.traceable:
            assert f"`{name}`" in status, (
                f"{name!r} generates RTL but the README status does not "
                "name it")


def test_design_rules_structure_lists_every_complete_block():
    """The docs/design-rules.md tree is updated by hand; this notices when a
    hand forgets."""
    rules = (ROOT / "docs" / "design-rules.md").read_text()
    for name, block in registry().items():
        if block.traceable:
            assert f"{name}.py" in rules, (
                f"{name!r} is complete but absent from the design-rules "
                "structure listing")
