# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""revela's composites load as they are described: the standard ISP, the
board's ISP core, and the sensor profile that tunes it.

The composite format and its loader are np2hw's and are tested there.
"""
from np2hw.design import profile as profiles
from np2hw.design.composite import load
from np2hw.pipeline import compose


def _built(design):
    """A loaded design, checked for generation: model blocks, the stream
    entering, a graph np2hw accepts."""
    compose.shape(design)
    compose.netlist(design)
    return design


def test_bayer_isp_loads_and_flattens_to_its_five_blocks():
    design = load("revela.pipelines.bayer_isp:bayer_isp",
                  build={"bit_depth": 12, "max_width": 1920,
                         "max_height": 1080})
    assert list(design.instances) == ["blacklevel", "whitebalance",
                                      "ha_green", "ha_rb", "ccm"]
    assert design.edges[0] == ("in", "blacklevel.in")
    assert design.edges[-1] == ("ccm.out", "out")
    assert design.parameters == {"bit_depth": 12, "max_width": 1920,
                                 "max_height": 1080}


def test_the_board_isp_core_is_built_at_its_geometry():
    from np2hw import pipeline as pipelines
    pipeline = pipelines.load("revela.boards.pynq_z2.isp:revela_isp_core")
    form = compose.shape(pipeline)
    assert pipeline.name == "revela_isp_core"
    assert (form.spec.bit_depth, form.width, form.height) == (12, 1920, 1080)
    assert [s.path for s in compose.stages(pipeline)[1:]][-2:] == [
        "rgb_gamma", "logo"]


def test_the_board_resets_to_its_curve_and_the_sensor_profile_fits_it():
    regmap = compose.register_map(
        _built(load("revela.boards.pynq_z2.isp:revela_isp_core")))
    knots = [r["default"] for b in regmap["blocks"] if b["path"] == "rgb_gamma"
             for r in b["registers"]]
    # The 2.2 curve, not the declared ramp: bright in the shadows.
    assert len(knots) == 33 and knots[:4] == [0, 53, 72, 87] and \
        knots[-1] == 256
    sensor = profiles.flatten(
        profiles.resolve("revela.sensors.ov5647.profile:ov5647"), regmap)
    assert len(sensor) == 17 and all(k.startswith("isp.") for k in sensor)
