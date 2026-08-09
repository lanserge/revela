# IMX477

**No `sensor.json` yet — deliberately, rather than a fabricated one.**

Sony IMX477: 1/2.3-inch, 12.3 Mpixel, 4056x3040, 12-bit, the sensor on the
Raspberry Pi HQ Camera.

The structural facts are easy to state and mostly public. The numbers that make a
description *useful* are not: `line_length_pck`, `frame_length_lines` and
`pixel_rate_hz` per mode set the exposure quantum and the frame rate, and the
whole exposure conversion in `revela/sensors/__init__.py` is computed from them.
Guessing them would produce a file that validates against the schema, passes CI,
and computes wrong exposures on real hardware — which is worse than its absence,
because the absence is obvious and the wrong numbers are not.

They are also exactly the values it would be most tempting to lift from
`drivers/media/i2c/imx477.c`, which is GPL-2.0 and incompatible with this
project's licence. See `CONTRIBUTING.md`.

## To add it

Take the values from the Sony datasheet, fill in `provenance.derived_from`
accordingly, and set `provenance.gpl_driver_transcribed` to `false` truthfully.
`tests/test_sensors.py` will validate the result against `schema.json` and
cross-check the internal consistency the schema cannot express — that the stated
maximum gain agrees with the code range, and that any stated line time agrees
with `line_length_pck / pixel_rate_hz`.

Use `revela/sensors/imx219/sensor.json` as the shape to follow.
