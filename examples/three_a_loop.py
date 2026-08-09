# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Run the 3A loops against a synthetic sensor, over the host register API.

    python examples/three_a_loop.py

Shows the whole software side working together without any hardware: a pipeline
is composed, its register map emitted, a device opened over the in-process
transport, and the AE and AWB loops driven frame by frame until they converge --
writing their results through the same accessors that would drive a real board.

The "sensor" is a few lines of NumPy that darkens and casts an image according to
the exposure and gain it is given. That is enough to exercise the loops, because
what is being demonstrated is the control path, not the optics.
"""
from __future__ import annotations

import numpy as np

from revela import sensors
from revela.blocks import blacklevel, pipe, stats
from revela.control import Q8, ae, awb
from revela.host import Device, MemoryTransport
from revela import designs
from revela.stream import StreamSpec

FRAME = (64, 64)
ZONES = 8
SCENE_CAST = (1.35, 1.0, 0.75)      # a warm indoor illuminant
NOMINAL_EXPOSURE_NS = 20_000_000    # exposure that would fully expose this scene


def capture(description: dict, exposure_ns: float, gain_q8: int) -> np.ndarray:
    """A synthetic sensor: signal proportional to exposure x gain, plus pedestal.

    Written as a CFA mosaic so the statistics block sees what a real sensor
    delivers, including two green samples per tile.
    """
    bit_depth = description["format"]["bit_depth"]
    pedestal = description["black_level"]["pedestal"]
    full_scale = (1 << bit_depth) - 1

    level = exposure_ns * gain_q8 / (Q8 * NOMINAL_EXPOSURE_NS)
    frame = np.zeros(FRAME, dtype=np.uint16)
    for dy in (0, 1):
        for dx in (0, 1):
            channel = {(0, 0): SCENE_CAST[0], (0, 1): SCENE_CAST[1],
                       (1, 0): SCENE_CAST[1], (1, 1): SCENE_CAST[2]}[(dy, dx)]
            value = int(level * (full_scale - pedestal) * channel) + pedestal
            frame[dy::2, dx::2] = min(full_scale, value)
    return frame


def main() -> int:
    description = sensors.load("imx219")
    build = sensors.build_parameters(description)
    bit_depth = build["bit_depth"]

    # Described the same way a design file describes it -- there is one way to
    # say what a pipeline contains, and this is it, spelled inline.
    pipeline = designs.build({
        "schema_version": 1,
        "name": "revela_isp",
        "stream": {"bit_depth": bit_depth},
        "geometry": {"width": FRAME[1], "height": FRAME[0]},
        "nodes": [{"instance": "blacklevel", "block": "blacklevel"},
                  {"instance": "stats", "block": "stats"}],
        "connections": [
            {"from": "in", "to": "blacklevel.in"},
            {"from": "blacklevel.out", "to": "out"},
            # A TAP: statistics meter the corrected stream and produce no
            # pixels, so the image path runs straight through.
            {"from": "blacklevel.out", "to": "stats.in"},
        ],
    })
    register_map = pipeline.register_map()

    transport = MemoryTransport(register_map)
    device = Device(register_map, transport)

    # Before anything else: prove the bitstream is the one this software knows.
    device.verify()
    print(f"device verified: {len(device.blocks)} blocks, "
          f"{register_map['address_bits']}-bit address space")

    # Pipeline context and black level, both derived from the sensor description.
    device.pipe.write(pipe.from_sensor(description))
    device.blacklevel.write(blacklevel.offsets_from_sensor(description))
    window = (0, 0, FRAME[1], FRAME[0])
    device.stats.write(stats.default_registers(ZONES, ZONES, window))
    print(f"configured: bayer_phase={device.pipe.bayer_phase}, "
          f"black level offset={device.blacklevel.offset_0_0} "
          f"(pedestal {description['black_level']['pedestal']})")
    print()

    exposure_ns, gain_q8 = 2_000_000, Q8
    red_gain, blue_gain = Q8, Q8
    bound = stats.model.params.bind(stats.default_registers(ZONES, ZONES, window))

    print(f"{'frame':>5} {'exposure':>10} {'gain':>7} {'metered':>8} "
          f"{'wb red':>8} {'wb blue':>8}  state")
    for frame_number in range(24):
        raw = capture(description, exposure_ns, gain_q8)

        # The hardware would do this; here the model stands in for it, which is
        # the point of the model being the specification.
        corrected = blacklevel.blacklevel.run(
            raw, blacklevel.offsets_from_sensor(description),
            bayer_phase=device.pipe.bayer_phase, bit_depth=bit_depth)
        measured = stats.model(corrected, bound,
                               bayer_phase=device.pipe.bayer_phase, window=window)

        exposure = ae.solve(description, measured,
                            current_exposure_ns=exposure_ns,
                            current_gain_q8=gain_q8, bit_depth=bit_depth,
                            zones_x=ZONES, zones_y=ZONES)
        balance = awb.solve(measured, bit_depth=bit_depth,
                            current_gains_q8=(red_gain, blue_gain))

        exposure_ns, gain_q8 = exposure["exposure_ns"], exposure["gain_q8"]
        red_gain, blue_gain = balance["gain_red_q8"], balance["gain_blue_q8"]

        # Write the exposure back to the sensor, and the gains to the pipeline.
        # A real system writes coarse_integration and analogue_gain_code over
        # I2C; the white balance gains would go to the whitebalance block, which
        # is still a stub, so they are only reported here.
        state = "converged" if exposure["converged"] else "settling"
        if not balance["confident"]:
            state += ", awb fallback"

        if frame_number % 4 == 3 or frame_number == 0:
            print(f"{frame_number:5d} {exposure_ns / 1e6:9.2f}ms "
                  f"{gain_q8 / Q8:6.2f}x {exposure['measured_q8']:8d} "
                  f"{red_gain / Q8:8.3f} {blue_gain / Q8:8.3f}  {state}")

    print()
    print(f"AE target was {ae.DEFAULT_TARGET_Q8}/256 of full scale; "
          f"metered {exposure['measured_q8']}/256")
    print(f"scene cast was R={SCENE_CAST[0]} B={SCENE_CAST[2]}; "
          f"AWB gains R={red_gain / Q8:.3f} B={blue_gain / Q8:.3f} "
          f"(ideal {1 / SCENE_CAST[0]:.3f} / {1 / SCENE_CAST[2]:.3f})")
    print(f"sensor writes: coarse_integration={exposure['coarse_integration']}, "
          f"analogue_gain_code={exposure['analogue_gain_code']}")
    print(f"register writes issued over the transport: {len(transport.writes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
