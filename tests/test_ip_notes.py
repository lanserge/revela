# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Every algorithm block carries a dated patent check. Enforced, not hoped.

revela ships silicon IP with a commercial tier alongside the open licence,
so "we believe this is fine" is not a provenance trail. The rule: any block
implementing a method with an academic or patent lineage states, in its
module docstring, when its patent situation was last checked and against
what -- `patent-checked YYYY-MM-DD` plus the substance. A new algorithm
file without one fails CI before it can be merged, which is the point:
the check happens when the method arrives, not when a licensee asks.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

PHRASE = re.compile(r"[Pp]atent-checked (\d{4}-\d{2}-\d{2})")

# Directories whose blocks implement METHODS -- things with a literature
# and patent lineage -- as opposed to plain arithmetic like an offset or
# a matrix multiply. Grows as families are added (denoise, wdr, ...).
ALGORITHM_FAMILIES = [Path("revela/blocks/demosaic")]


def _algorithm_files():
    for family in ALGORITHM_FAMILIES:
        for file in sorted(family.glob("*.py")):
            if file.name != "__init__.py":
                yield file


def test_every_algorithm_block_carries_a_dated_patent_check():
    files = list(_algorithm_files())
    assert len(files) >= 5, "the demosaic family has gone missing"
    for file in files:
        found = PHRASE.search(file.read_text())
        assert found, (
            f"{file} has no dated patent check; add 'patent-checked "
            "YYYY-MM-DD' with the substance of what was checked -- see "
            "CONTRIBUTING.md")
        datetime.date.fromisoformat(found.group(1))   # a real date, not noise
