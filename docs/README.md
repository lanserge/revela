# docs

- **[design-rules.md](design-rules.md)** — the four rules, the register map
  conventions, and the sensor conventions. The canonical statement of how this
  project works; read it before changing anything under `revela/`.
- **[LICENSING.md](LICENSING.md)** — what licence applies to generated Verilog,
  what you owe by shipping it, and what the commercial licence adds. The first
  questions a commercial evaluator asks.
- **[THIRD-PARTY.md](THIRD-PARTY.md)** — every dependency and its licence, and
  why the simulators' terms do not reach your design.
- **[RELEASING.md](RELEASING.md)** — publishing to PyPI, and why np2hw has to
  go first.

## Generated documentation

The register map documentation is **generated**, not written. It comes from the
same JSON the host API reads, which comes from the same block declarations that
produced the Verilog:

```bash
python examples/build_pipeline.py --description pipelines/mono/imx219/basic/pipeline.json \
                                  --out pipelines/mono/imx219/basic/build/
```

writes `build/<name>.v`, `build/<name>.json` and `build/<name>-registers.md`.

That chain matters more than it looks. A register map document written alongside
the hardware is correct on the day it is written and slowly stops being correct,
and the errors it picks up are the ones nobody notices until somebody trusts it.
Generating it means there is no second place for an address to live.
