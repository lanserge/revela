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
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# The identifier is READ, not written here. It lives in the packaging
# metadata -- the same place this project keeps its version -- so the
# licence has one owner and this test cannot drift from what is actually
# published. The year is free so files are not churned annually, and the
# holder is free so a CLA-signing contributor could appear.
def declared_licence() -> str:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    licence = data.get("project", {}).get("license")
    if isinstance(licence, dict):                    # the older table form
        licence = licence.get("text")
    assert licence, "pyproject.toml declares no licence to check against"
    return licence


def header_pattern() -> re.Pattern:
    return re.compile(
        r"^# Copyright \d{4}(-\d{4})? \S.*\n"
        r"# SPDX-License-Identifier: " + re.escape(declared_licence()) + r"\s*\n")

# revela/ is the requirement. tests/ and examples/ are held to it too: they are
# distributed in the sdist and are just as much part of the licensed work.
CHECKED_ROOTS = ("revela", "tests", "examples")
# Source that is not Python. A licence check scoped to one language, in
# source DIRECTORIES only, misses whatever else is source -- the FuseSoC
# core file beside them went uncovered for exactly that reason.
OTHER_SUFFIXES = (".c", ".h", ".v", ".sv", ".sh", ".tcl", ".xdc", ".core")
ROOT_FILES = ("revela.core",)
# JSON carries no comment syntax, so a pipeline description cannot hold a
# header and is covered by the repository's LICENSE files instead. Named
# here so the gap is a decision rather than an oversight.
NO_COMMENT_SYNTAX = (".json",)


def python_files() -> list[Path]:
    files: list[Path] = []
    for root in CHECKED_ROOTS:
        directory = PROJECT_ROOT / root
        if directory.is_dir():
            files.extend(sorted(p for p in directory.rglob("*.py")
                                if "__pycache__" not in p.parts))
    return files


def other_files() -> list[Path]:
    files: list[Path] = []
    for root in CHECKED_ROOTS:
        directory = PROJECT_ROOT / root
        if directory.is_dir():
            files.extend(sorted(p for p in directory.rglob("*")
                                if p.suffix in OTHER_SUFFIXES
                                and "__pycache__" not in p.parts))
    files.extend(PROJECT_ROOT / n for n in ROOT_FILES
                 if (PROJECT_ROOT / n).is_file())
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
    assert header_pattern().match(text), (
        f"{relative} does not start with the required licence header.\n"
        f"Expected the file to begin with:\n\n"
        f"    # Copyright <year> <author>\n"
        f"    # SPDX-License-Identifier: {declared_licence()}\n\n"
        f"Found instead:\n\n"
        + "".join(f"    {line}\n" for line in text.splitlines()[:3])
    )


@pytest.mark.parametrize(
    "path", other_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_non_python_file_states_the_licence(path: Path):
    """The same requirement, one line looser about WHERE the header sits.

    A FuseSoC core file must open with ``CAPI=2:`` and an HTML page with its
    doctype, so the identifier cannot always be the first thing in the file
    -- only among the first things."""
    head = "".join(path.read_text(encoding="utf-8",
                                  errors="replace").splitlines(True)[:6])
    relative = path.relative_to(PROJECT_ROOT)
    found = re.search(r"SPDX-License-Identifier:\s*(.+?)\s*"
                      r"(?:-->|\*/)?\s*$", head, re.M)
    assert found, (
        f"{relative} has no SPDX-License-Identifier in its first lines; "
        f"every source file states the licence it is under.\n"
        f"Expected: SPDX-License-Identifier: {declared_licence()}")
    assert found.group(1) == declared_licence(), (
        f"{relative} claims {found.group(1)!r} but this project publishes as "
        f"{declared_licence()!r} (pyproject.toml). A file under different "
        "terms is a decision, not a typo -- make it deliberately or fix the "
        "header.")


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
