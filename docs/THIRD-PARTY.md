# Third-party licence audit

Every dependency of revela, with its licence and the role it plays. Written for
the review a partner's open-source or legal team performs before engaging, so it
answers the questions in the order they get asked.

**Summary: every dependency of revela, at every depth, is under a permissive
licence** — MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF-2.0, HPND, or a
compound of those.

`.github/workflows/ci.yml` enforces this on every push with an **allow-list**: a
package whose declared licence is not among the permitted ones fails the build.
An allow-list rather than a deny-list, because a deny-list only catches the
licences somebody thought to name, and the one that causes trouble is the one
nobody anticipated.

Two items need an explanation rather than a table row — the `pathspec` build
dependency and the simulators. Both are below, stated plainly rather than left
for a scanner to find.

Generated against the resolved environment on 2026-08-08.

---

## Required at runtime

Installed by `pip install revela`. These are the only licences that reach anyone
who uses revela.

| Package | Licence | Why it is here |
| --- | --- | --- |
| `np2hw` | MIT | The generator. Traces the NumPy models into Verilog. Same author as revela. |
| `numpy` | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | The models are NumPy. The compound expression is NumPy's own declaration and covers vendored components; all five terms are permissive. |
| `jsonschema` | MIT | Validates the sensor, design and profile schemas. |
| `jsonschema-specifications` | MIT | Transitive of `jsonschema`. |
| `referencing` | MIT | Transitive of `jsonschema`. |
| `rpds-py` | MIT | Transitive of `referencing`. |
| `attrs` | MIT | Transitive of `jsonschema`. |
| `typing-extensions` | PSF-2.0 | Transitive. The Python Software Foundation licence; permissive. |

## Required only to run the tests

Installed by `pip install revela[dev]`. Not present in a deployment.

| Package | Licence |
| --- | --- |
| `pytest` | MIT |
| `cocotb` | BSD-3-Clause |
| `systemrdl-compiler` | MIT — elaborates the emitted `.rdl` and compares it against the JSON map. Deliberately NOT the PeakRDL exporters, which are copyleft tools an integrator runs themselves. |
| `pluggy`, `iniconfig`, `find_libpython` | MIT |
| `packaging` | `Apache-2.0 OR BSD-2-Clause` |
| `Pygments` | BSD-2-Clause |
| `colorama`, `exceptiongroup`, `tomli` | BSD-3-Clause / MIT (Python-version dependent) |

## Required only to build the package

Present in an isolated build environment, never installed alongside revela and
never redistributed.

| Package | Licence | Note |
| --- | --- | --- |
| `hatchling` | MIT | Build backend. |
| `pathspec` | **MPL-2.0** | See below. |
| `trove-classifiers` | Apache-2.0 | |
| `packaging`, `pluggy`, `tomli` | as above | |
| `setuptools` | MIT | Declares nothing readable; see below. |

### `setuptools` and its missing metadata

setuptools 78/79 dropped the legacy `License ::` trove classifiers, and its
wheel metadata predates PEP 639's `License-Expression`, so it declares its
licence in none of the three places the allow-list reads. It is MIT, it is
a build tool that is never imported and never redistributed in a wheel, and
pip installs it whether or not anything asked for it.

The check names it explicitly rather than relaxing the rule: anything else
that declares no licence still fails the build. If setuptools' metadata is
fixed upstream, the exception can be deleted and nothing else changes.

### `pathspec` and MPL-2.0

`pathspec` is a transitive dependency of the `hatchling` build backend. It is
used to match file patterns while assembling a wheel.

MPL-2.0 attaches its obligations at **file level**: they apply to modifications
of the MPL-licensed files themselves, and §3.3 expressly permits combination with
code under other licences. Regardless, none of that engages here:

- revela does not import, link to, or embed `pathspec`;
- `pathspec` is not a dependency of the published wheel — it appears only in the
  isolated environment that *produces* the wheel;
- no `pathspec` file is modified or redistributed.

It is listed because an automated scan of a build environment will report it,
and a reviewer is entitled to an answer rather than a shrug.

## External tools — not dependencies

Verification runs these as **separate programs**, invoked over a process
boundary. They are not linked, not imported, not redistributed, and not needed to
use revela.

| Tool | Licence | Used for |
| --- | --- | --- |
| Verilator | `LGPL-3.0-only OR Artistic-2.0` | Simulating the generated Verilog in the bit-exact tests. |
| Icarus Verilog | `GPL-2.0-or-later` | np2hw's own example suite. Not used by revela. |

**A simulator's licence terms attach to the simulator, not to the designs it
simulates.** Verilator's own licensing documentation states this directly, and
the principle is the ordinary one: compiling a program with GCC does not place
the program under GCC's licence.

Neither tool is required by an evaluator who only wants to generate RTL: they
are needed to *reproduce the verification*, which is a different thing.

## Optional extras of `np2hw`

revela does not install these. They are listed because a reviewer auditing the
whole tree will reach np2hw's `pyproject.toml` and see them.

| Extra | Package | Licence | Note |
| --- | --- | --- | --- |
| `media` | `pillow` | HPND (MIT-like) | |
| `camera` | `opencv-python` | Apache-2.0 | **Worth a look if you enable it.** The published wheels bundle prebuilt third-party libraries whose licences vary by build. If your compliance process is strict, build OpenCV yourself or leave this extra off. revela never requires it. |
| `switchboard` | `switchboard-hw`, `umi` | Apache-2.0 (ZeroASIC) | |

## Sensor descriptions and reference data

Not a software dependency, but the same question:

- `revela/sensors/*/sensor.json` is derived from publicly available manufacturer
  datasheets, and **not transcribed from the Linux kernel media drivers**, whose
  licence is incompatible with this project's. The schema enforces a provenance
  declaration on every sensor, and `imx219`'s `register_sequences` is
  deliberately empty rather than filled in from a driver. The rule, and the
  reason for it, are in [CONTRIBUTING.md](../CONTRIBUTING.md).
- **No image datasets are present in this repository.** The standard demosaic
  reference sets (Kodak, McMaster) are distributed under terms that do not permit
  redistribution and are not included. `.gitignore` refuses the raster formats
  they arrive in.

## How to reproduce this audit

```bash
pip install -e ".[dev]"
python - <<'PY'
from importlib.metadata import distributions
for d in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
    m = d.metadata
    print(f"{m['Name']:<28} {d.version:<12} "
          f"{m.get('License-Expression') or m.get('License') or ''}")
PY
```

Anything that appears there and not here is a finding. Please report it.
