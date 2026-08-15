# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Every source file must carry the copyright and SPDX identifier.

Checked mechanically because a licence header applied inconsistently is worse
than useless: a reviewer cannot tell whether a file without one was an oversight
or was deliberately contributed under different terms, and the ambiguity is
exactly what a licence audit will stop on.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# The exact two lines every .py file must begin with. The year is free so that
# files are not churned annually, and the holder is free so that a CLA-signing
# contributor could in principle appear -- but the SPDX line is fixed.
HEADER = re.compile(
    r"^# Copyright \d{4}(-\d{4})? \S.*\n"
    r"# SPDX-License-Identifier: Apache-2\.0 WITH SHL-2\.1\s*\n"
)

# revela/ is the requirement. tests/ and examples/ are held to it too: they are
# distributed in the sdist and are just as much part of the licensed work.
CHECKED_ROOTS = ("revela", "tests", "examples")


def python_files() -> list[Path]:
    files: list[Path] = []
    for root in CHECKED_ROOTS:
        directory = PROJECT_ROOT / root
        if directory.is_dir():
            files.extend(sorted(p for p in directory.rglob("*.py")
                                if "__pycache__" not in p.parts))
    return files


def test_there_are_files_to_check():
    """Guard against the check silently passing because it found nothing."""
    files = python_files()
    assert len(files) > 5, (
        f"only found {len(files)} Python files to check; the header test is not "
        "actually covering the source tree")


@pytest.mark.parametrize(
    "path", python_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_file_has_spdx_header(path: Path):
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(PROJECT_ROOT)
    assert HEADER.match(text), (
        f"{relative} does not start with the required licence header.\n"
        f"Expected the file to begin with:\n\n"
        f"    # Copyright <year> <author>\n"
        f"    # SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1\n\n"
        f"Found instead:\n\n"
        + "".join(f"    {line}\n" for line in text.splitlines()[:3])
    )


def _is_path_join(node):
    """`Path(...) / "name"` is not arithmetic.

    pathlib overloads the same operator, and a block that loads an asset
    beside itself writes exactly that. The rule this test enforces is about
    the MODELS being written at the hardware's arithmetic, so a division
    whose right-hand side is a string is a path and not a rounding mistake.
    """
    right = getattr(node, "value", None) if isinstance(node, ast.AugAssign) \
        else getattr(node, "right", None)
    return isinstance(right, ast.Constant) and isinstance(right.value, str)


def test_no_blocks_module_uses_floating_point():
    """Rule 1, enforced: no float in the block models.

    The models are the specification and they are written at the hardware's
    arithmetic. A float creeping into a block is not a small style lapse -- it
    means that block's model no longer describes what the gateware does, and the
    bit-exact test would then be pinning the RTL to something the hardware
    cannot compute.
    """
    offenders: list[str] = []

    # Parsed rather than grepped: the block modules emit Verilog, so their string
    # literals are full of '//' comments and '/' characters that a regex cannot
    # tell apart from arithmetic. The AST sees only real code.
    float_attribute = re.compile(r"^(float|double)\w*$")

    for path in sorted((PROJECT_ROOT / "revela" / "blocks").rglob("*.py")):
        relative = path.relative_to(PROJECT_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))

        for node in ast.walk(tree):
            where = getattr(node, "lineno", 0)

            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{relative}:{where}: float literal {node.value!r}")

            elif isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(
                    node.op, ast.Div) and not _is_path_join(node):
                offenders.append(
                    f"{relative}:{where}: true division '/' -- use // and be "
                    "explicit about rounding")

            elif isinstance(node, ast.Attribute) and float_attribute.match(node.attr):
                offenders.append(f"{relative}:{where}: numpy float type '.{node.attr}'")

            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("float", "round"):
                offenders.append(
                    f"{relative}:{where}: call to {node.func.id}() -- rounding in a "
                    "model must be an explicit integer operation")

    assert not offenders, (
        "floating point found in revela/blocks/. The models are the "
        "specification and are written at the hardware's arithmetic: integer "
        "dtypes, // for division, explicit shifts, explicit rounding.\n\n"
        + "\n".join(offenders))
