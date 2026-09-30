# Releasing

Four packages publish to PyPI, and bayerlink-fpga is a package that
publishes once its own publish workflow exists. They are separate
releases, but **the order is not free**: a package cannot be uploaded before the packages it depends on
exist on the index at the version it asks for.

## The order, and where it comes from

Read it off the dependency graph rather than remembering it:

| package | depends on | may publish |
| --- | --- | --- |
| `bayerlink` | numpy | first — nothing internal |
| `np2hw` | numpy; `bayerlink` only as an extra | first — nothing internal required |
| `revela` | `np2hw>=0.6.1` | after np2hw |
| `picam2hdmi` | `bayerlink>=0.5.0` | after bayerlink |
| `bayerlink-fpga` | `np2hw>=0.6.1`, `bayerlink>=0.5.0` | after np2hw and bayerlink |

A workable sequence is therefore **bayerlink → np2hw → revela →
picam2hdmi**, with bayerlink-fpga anywhere after np2hw and bayerlink.
bayerlink and np2hw are independent of each other and may go in either
order. revela's board design (`revela/boards/pynq_z2`) instantiates
bayerlink-fpga's receiver, so a release of revela whose board build is to
install from PyPI needs bayerlink-fpga published first.

## Why a direct reference is refused

**PyPI rejects any distribution whose metadata contains a direct
reference** (`np2hw @ git+https://…`). revela's dependency was one once,
which is why revela could not be published at all until np2hw existed on
the index.

`.github/workflows/publish.yml` enforces this: the build fails if a
direct reference survives into the wheel metadata. That failure is
deliberate and far cheaper than the alternative — **a version number
consumed by a failed upload can never be reused on PyPI, even after
deleting the release.**

It is worth avoiding on its own merits too. A security review will ask
how a dependency is pinned and verified; `git+https` against a moving
branch has no answer, and a PyPI version with a hash does.

## Trusted Publishing

All four use **Trusted Publishing** (OIDC): the job exchanges a
short-lived GitHub identity token for an upload token at publish time.
There is no API token in repository secrets — nothing long-lived to leak,
rotate, or explain.

The one-time setup is done. For a *new* project, on PyPI and again on
TestPyPI: **Add a pending publisher** at
<https://pypi.org/manage/account/publishing/> with the project name, owner
`lanserge`, the repository name, workflow `publish.yml`, and environment
`pypi` (`testpypi` on the test index). Then in the GitHub repository,
**Settings → Environments** → create `pypi` and `testpypi`, with a
required reviewer on `pypi` so an accidental tag cannot publish a release
on its own.

## Releasing one package

```bash
# 1. rehearse on TestPyPI: Actions -> Publish -> Run workflow -> testpypi
python -m venv /tmp/t && /tmp/t/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ <package>
/tmp/t/bin/python -c "import <package>; print(<package>.__version__)"
```

When that works: confirm `version` in `pyproject.toml`, commit, tag
`vX.Y.Z`, push the tag, and publish a GitHub Release from it. The
`Publish` workflow builds, checks the metadata, verifies the tag matches
the declared version, installs the wheel into a clean environment, and
uploads.

Working on two projects at once still works — install the dependency
editable over the top, which is what a co-development checkout wants:

```bash
pip install -e ".[dev]" && pip install -e ../np2hw
```

## Before any release

- [ ] The suite is green, including the bit-exact tests.
- [ ] The dependency licence allow-list job is green.
- [ ] `version` in `pyproject.toml` matches the tag about to be pushed.
- [ ] `CHANGELOG.md` has an entry for this version, and anything breaking
      is named as breaking.
- [ ] `AUTHORS` includes everyone whose work is in the release.
- [ ] Generated output under `pipelines/*/*/*/build/` is regenerated and
      still ignored by git — it must not enter the repository.
- [ ] Every published tag is still an ancestor of `origin/main`. A
      released tag is immutable: make a new version, never move one.

## Version numbers

Pre-1.0, so the register map format and the block API can still change.
What must not change silently:

- `MAP_FORMAT_VERSION` in `revela/compose.py` — bump it when the
  *structure* of the emitted register-map JSON changes, so a host refuses
  a map it does not understand instead of misreading it.
- A block's declared `version` — bump it when that block's register
  layout moves. It is in the ID-and-version word at the block's base,
  which is how software proves a bitstream matches the map it holds.
  Note that it does **not** catch a register whose width changed while
  its layout did not; a depth change needs the whole map compared.
