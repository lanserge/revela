# Releasing

revela and np2hw publish to PyPI independently, but **np2hw must go first**, and
that is not a preference.

## Why the order is fixed

revela's dependency was originally a direct git reference, and **PyPI refuses
any distribution whose metadata contains a direct reference** -- so revela could
not be published at all until np2hw existed on PyPI and the dependency became an
ordinary version spec. np2hw 0.1.0 is published and the dependency is now
`np2hw>=0.1.0`; the rule stands for any future dependency someone is tempted to
point at a git URL.

`.github/workflows/publish.yml` enforces this: the build fails if a direct
reference survives into the wheel metadata. That failure is deliberate and is
much cheaper than the alternative — **a version number consumed by a failed
upload can never be reused on PyPI, even after deleting the release.**

A direct reference is also worth removing on its own merits. A partner's
security review will ask how the dependency is pinned and verified; `git+https`
against a moving branch has no answer, and a PyPI version with a hash does.

## One-time setup

Both projects use **Trusted Publishing** (OIDC), so there is no API token stored
in repository secrets — nothing long-lived to leak, rotate, or explain.

For each project, on PyPI and again on TestPyPI:

1. https://pypi.org/manage/account/publishing/ → **Add a pending publisher**

   | Field | np2hw | revela |
   | --- | --- | --- |
   | PyPI project name | `np2hw` | `revela` |
   | Owner | `lanserge` | `lanserge` |
   | Repository | `np2hw` | `revela` |
   | Workflow | `publish.yml` | `publish.yml` |
   | Environment | `pypi` | `pypi` |

2. Repeat at https://test.pypi.org/manage/account/publishing/ with environment
   `testpypi`.

3. In each GitHub repository: **Settings → Environments** → create `pypi` and
   `testpypi`. Add a required reviewer on `pypi`, so an accidental tag cannot
   publish a release on its own.

Both names were free on PyPI and TestPyPI when this was written. Register them
by publishing, not by squatting.

## Step 1 — np2hw

```bash
cd ../np2hw
# rehearse: Actions -> Publish -> Run workflow -> target: testpypi
python -m venv /tmp/t && /tmp/t/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ np2hw
/tmp/t/bin/python -c "import np2hw; print(np2hw.__all__)"
```

When that works: bump `version` in `pyproject.toml`, commit, tag `vX.Y.Z`, push
the tag, and publish a GitHub Release from it. The `Publish` workflow builds,
checks the metadata, verifies the tag matches the declared version, installs the
wheel into a clean environment, and uploads.

## Step 2 — switch revela's dependency

In `pyproject.toml`:

```diff
 dependencies = [
     "numpy>=1.24",
     "jsonschema>=4.0",
-    # np2hw is the generator; revela is its flagship proof case. Depended on,
-    # never vendored -- a fork here would let the two drift, and the whole claim
-    # is that one NumPy model drives both.
-    "np2hw @ git+https://github.com/lanserge/np2hw.git",
+    # np2hw is the generator; revela is its flagship proof case. Depended on,
+    # never vendored -- a fork here would let the two drift, and the whole claim
+    # is that one NumPy model drives both.
+    "np2hw>=0.1.0",
 ]

-[tool.hatch.metadata]
-# np2hw is not published to PyPI; it is depended on by git URL so that a clone
-# of revela reproduces with one command. Vendoring it instead would let the two
-# drift, which is the one thing the project cannot allow.
-allow-direct-references = true
```

Also update the "Install revela and its dependencies" comment in
`.github/workflows/ci.yml`, which currently explains that np2hw comes from git.

Working on both projects at once still works — install np2hw editable over the
top, which is what a co-development checkout wants anyway:

```bash
pip install -e ".[dev]" && pip install -e ../np2hw
```

## Step 3 — revela

**Gated on the repository going public**, which is gated on the working FPGA
example: publishing the sdist to PyPI would make the source public through the
side door, so a private repository and a PyPI release are mutually exclusive.
When the gate opens:

```bash
# rehearse: Actions -> Publish -> Run workflow -> target: testpypi
```

Then tag and release as above.

## Before any release

- [ ] `pytest` green, including the bit-exact tests (CI runs them with Verilator).
- [ ] The dependency licence allow-list job is green.
- [ ] `version` in `pyproject.toml` matches the tag you are about to push.
- [ ] `AUTHORS` includes everyone whose work is in the release.
- [ ] Generated `pipelines/*/*/*/build/` output is regenerated, and still ignored
      by git — it must not enter the repository.

## Version numbers

Pre-1.0, so the register map format and the block API can still change. What
must not change silently:

- `MAP_FORMAT_VERSION` in `revela/compose.py` — bump it when the *structure* of
  the emitted register-map JSON changes, so a host refuses a map it does not
  understand instead of misreading it.
- A block's declared `version` — bump it when that block's register layout
  moves. It is in the ID-and-version word at the block's base, which is how
  software proves a bitstream matches the map it is holding.
