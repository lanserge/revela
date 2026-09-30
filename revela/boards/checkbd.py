# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Structural checks on the block-design script, before a build spends 20 minutes.

`bd.tcl` is hand-written Tcl with build-time flags, and Tcl finds its
mistakes by running into them -- twenty minutes in, having already
synthesised everything up to that line. Every check here was added after
it cost exactly that.

  BRACES BALANCE          an unclosed guard silently swallows the rest
                          of the file into a branch that may not run.
  CREATED BEFORE USED     a cell referenced above its own
                          `create_bd_cell` is a typo Tcl reports as a
                          missing object, with no hint that the object
                          appears later.
  USED INSIDE ITS GUARD   the one this file exists for. A cell created
                          inside `if {$capture}` and wired outside it
                          builds perfectly with the flag on and cannot
                          build at all with it off -- so a configuration
                          the README offers stays broken until someone
                          tries it. Three of these were sitting in this
                          repo on 2026-08-18, and the first two greps
                          missed the third because it names its cell as
                          an address space rather than as a pin.

That last point is why the pattern below lists every syntax bd.tcl uses
to NAME a cell. A check that knows one spelling finds one bug.

    python3 -m revela.boards.checkbd [<bd.tcl> ...]
"""
import pathlib
import re
import sys

# Every way this file names a cell. Add a spelling here, not a new check.
NAMES = re.compile(
    r"get_bd_(?:pins|intf_pins|cells)\s+([A-Za-z_]\w*)/"
    r"|-target_address_space\s+/([A-Za-z_]\w*)/"
    r"|get_bd_addr_segs\s+([A-Za-z_]\w*)/")


def check(path: pathlib.Path) -> int:
    lines = path.read_text().splitlines()
    problems = []

    # Pass one: where each cell is created, and how deeply guarded.
    depth, created, guard_of = 0, {}, {}
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        if "create_bd_cell" in line:
            tokens = line.rstrip().split()
            if tokens:
                name = tokens[-1].strip("[]$")
                created[name] = n
                guard_of[name] = depth
        depth += line.count("{") - line.count("}")

    if depth != 0:
        problems.append(f"braces do not balance: net {depth:+d} at end of file")

    # Pass two: every use, against where it was created.
    depth = 0
    for n, line in enumerate(lines, 1):
        if not line.lstrip().startswith("#"):
            for match in NAMES.finditer(line):
                cell = next(g for g in match.groups() if g)
                if cell not in created:
                    continue          # made by the automation, not here
                if n < created[cell]:
                    problems.append(
                        f"line {n}: {cell!r} is used before it is created "
                        f"(line {created[cell]})")
                elif guard_of[cell] > depth:
                    problems.append(
                        f"line {n}: {cell!r} is used outside the guard it was "
                        f"created in (line {created[cell]}) -- this builds with "
                        f"the flag on and cannot build with it off")
        depth += line.count("{") - line.count("}")

    for problem in problems:
        print(f"  {problem}")
    print(f"  {path}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    targets = ([pathlib.Path(a) for a in sys.argv[1:]]
               or sorted(here.glob("*/bd.tcl")))
    return max(check(t) for t in targets)


if __name__ == "__main__":
    sys.exit(main())
