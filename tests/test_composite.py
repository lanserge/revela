# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""The JSON composition loader: references, settings, expressions, flattening.

Each test writes a small block library into a fresh package on the import
path, so references resolve exactly as they do for revela's own libraries --
through importlib, from the top of the import path.
"""
import itertools
import json

import pytest

from revela.designs.composite import (CompositionError, evaluate, find, load,
                                      parse, references, to_pipeline)

WB = "revela.blocks.whitebalance:whitebalance"
BL = "revela.blocks.blacklevel:blacklevel"
_count = itertools.count()


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Write {module: {block: {...}}} as a package; return a function that
    takes the files and gives back the package name."""
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(files, python: dict | None = None) -> str:
        """``files`` is {module: {block: {...}}}, or a function of the
        package name returning it, so a file can reference its own library."""
        name = f"fixture_lib_{next(_count)}"
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        if callable(files):
            files = files(name)
        for module, content in files.items():
            # {block: {...}}, or a whole library file (blocks, profiles)
            whole = set(content) & {"blocks", "profiles"} == set(content) \
                and content
            (package / f"{module}.json").write_text(
                json.dumps(content if whole else {"blocks": content}))
        for module, text in (python or {}).items():
            (package / f"{module}.py").write_text(text)
        return name
    return make


def chain(*names_and_refs, **extra):
    """A composite: in -> each node in order -> out."""
    nodes = {n: {"block": r} for n, r in names_and_refs}
    order = [n for n, _ in names_and_refs]
    connections = [["in", f"{order[0]}.in"]]
    connections += [[f"{a}.out", f"{b}.in"] for a, b in zip(order, order[1:])]
    connections += [[f"{order[-1]}.out", "out"]]
    return dict({"inputs": [{"name": "in"}], "outputs": [{"name": "out"}],
                 "nodes": nodes, "connections": connections}, **extra)


# -- the real library ------------------------------------------------------- #

def test_bayer_isp_loads_and_flattens_to_its_five_blocks():
    design = load("revela.blocks.bayer_isp:bayer_isp",
                  build={"bit_depth": 12, "max_width": 1920,
                         "max_height": 1080})
    assert list(design.instances) == ["blacklevel", "whitebalance",
                                      "ha_green", "ha_rb", "ccm"]
    assert design.edges[0] == ("in", "blacklevel.in")
    assert design.edges[-1] == ("ccm.out", "out")
    assert design.parameters == {"bit_depth": 12, "max_width": 1920,
                                 "max_height": 1080}


def test_an_unset_interface_parameter_is_refused():
    with pytest.raises(CompositionError, match="bit_depth is never set"):
        load("revela.blocks.bayer_isp:bayer_isp")


# -- hierarchy -------------------------------------------------------------- #

def test_nested_composites_flatten_to_edges_between_leaves(library):
    lib = library(lambda lib: {
        "inner": {"pair": chain(("bl", BL), ("wb", WB))},
        "outer": {"top": chain(("a", f"{lib}.inner:pair"),
                               ("b", f"{lib}.inner:pair"))},
    })
    design = load(f"{lib}.outer:top")
    assert list(design.instances) == ["a.bl", "a.wb", "b.bl", "b.wb"]
    assert design.edges == [
        ("in", "a.bl.in"), ("a.bl.out", "a.wb.in"),
        ("a.wb.out", "b.bl.in"), ("b.bl.out", "b.wb.in"),
        ("b.wb.out", "out")]
    assert design.composites == {"": f"{lib}.outer:top",
                                 "a": f"{lib}.inner:pair",
                                 "b": f"{lib}.inner:pair"}


def test_a_composite_that_contains_itself_is_refused(library):
    lib = library(lambda lib: {"m": {"loop": {
        "nodes": {"x": {"block": f"{lib}.m:loop"}}}}})
    with pytest.raises(CompositionError, match="contains itself"):
        load(f"{lib}.m:loop")


def test_a_module_that_is_both_python_and_json_is_refused(library):
    lib = library({"both": {"b": chain(("wb", WB))}},
                  python={"both": "x = 1\n"})
    with pytest.raises(CompositionError, match="both a Python module"):
        find(f"{lib}.both:b")


# -- settings --------------------------------------------------------------- #

def test_the_enclosing_composite_has_the_last_word(library):
    inner = chain(("wb", WB))
    inner["nodes"]["wb"]["values"] = {"gain": [[300, 300], [300, 300]]}
    def files(lib):
        outer = chain(("isp", f"{lib}.inner:lib"))
        outer["nodes"]["isp"]["values"] = {"wb.gain": [[400, 256], [256, 400]]}
        return {"inner": {"lib": inner}, "outer": {"design": outer}}
    lib = library(files)
    design = load(f"{lib}.outer:design")
    assert design.instances["isp.wb"].values == {
        "gain": [[400, 256], [256, 400]]}
    # ...and without the outer setting, the inner one stands.
    assert load(f"{lib}.inner:lib").instances["wb"].values == {
        "gain": [[300, 300], [300, 300]]}


def test_a_setting_of_something_that_does_not_exist_is_refused(library):
    typo = chain(("wb", WB))
    typo["nodes"]["wb"]["values"] = {"gian": [[256] * 2] * 2}
    lib = library({"m": {"d": typo}})
    with pytest.raises(CompositionError,
                       match=r"no register 'gian' \(registers \['gain'\]\)"):
        load(f"{lib}.m:d")


def test_a_register_under_build_is_refused(library):
    wrong = chain(("wb", WB))
    wrong["nodes"]["wb"]["build"] = {"gain": 1}
    lib = library({"m": {"d": wrong}})
    with pytest.raises(CompositionError, match="set it under values"):
        load(f"{lib}.m:d")


def test_a_value_outside_its_register_is_refused(library):
    big = chain(("wb", WB))
    big["nodes"]["wb"]["values"] = {"gain": [[70000, 256], [256, 256]]}
    lib = library({"m": {"d": big}})
    with pytest.raises(CompositionError, match="70000 does not fit gain"):
        load(f"{lib}.m:d")


def test_a_value_of_the_wrong_shape_is_refused(library):
    flat = chain(("wb", WB))
    flat["nodes"]["wb"]["values"] = {"gain": 256}
    lib = library({"m": {"d": flat}})
    with pytest.raises(CompositionError, match=r"shape \(2, 2\), given \(\)"):
        load(f"{lib}.m:d")


def test_a_register_attribute_is_a_build_setting_and_configures_a_variant(
        library):
    d = chain(("wb", WB))
    d["nodes"]["wb"]["build"] = {"gain.frac": 12}
    lib = library({"m": {"d": d}})
    wb = load(f"{lib}.m:d").instances["wb"]
    assert wb.build == {"gain.frac": 12}
    assert next(p for p in wb.leaf.params.params if p.name == "gain").frac == 12


# -- expressions ------------------------------------------------------------ #

def test_build_expressions_reach_down_and_pass_parameters_in(library):
    inner = chain(("wb", WB), parameters={"depth": {}, "frac": {}})
    inner["nodes"]["wb"]["build"] = {"gain.frac": "frac"}
    def files(lib):
        outer = chain(("isp", f"{lib}.inner:lib"),
                      parameters={"depth": {"default": 12}})
        outer["nodes"]["isp"]["build"] = {"depth": "depth",
                                          "frac": "isp.depth - 2"}
        return {"inner": {"lib": inner}, "outer": {"design": outer}}
    lib = library(files)
    design = load(f"{lib}.outer:design")
    assert design.parameters == {"depth": 12}
    assert design.instances["isp.wb"].build == {"gain.frac": 10}
    assert load(f"{lib}.outer:design", build={"depth": 14}) \
        .instances["isp.wb"].build == {"gain.frac": 12}


def test_a_cycle_is_refused(library):
    inner = chain(("wb", WB), parameters={"a": {}, "b": {}})
    def files(lib):
        outer = chain(("isp", f"{lib}.inner:lib"))
        outer["nodes"]["isp"]["build"] = {"a": "isp.b + 1", "b": "isp.a"}
        return {"inner": {"lib": inner}, "outer": {"design": outer}}
    lib = library(files)
    with pytest.raises(CompositionError, match="a cycle"):
        load(f"{lib}.outer:design")


def test_one_register_alone_is_an_alias(library):
    d = chain(("left", WB), ("right", WB))
    d["nodes"]["left"]["values"] = {"gain": [[300, 256], [256, 300]]}
    d["nodes"]["right"]["values"] = {"gain": "left.gain"}
    lib = library({"m": {"d": d}})
    design = load(f"{lib}.m:d")
    assert design.aliases == {"right.gain": "left.gain"}
    assert "gain" not in design.instances["right"].values


def test_arithmetic_between_registers_is_refused(library):
    d = chain(("left", WB), ("right", WB))
    d["nodes"]["right"]["values"] = {"gain": "left.gain * 2"}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="computes with registers"):
        load(f"{lib}.m:d")


def test_a_build_parameter_cannot_follow_a_register(library):
    inner = chain(("wb", WB), parameters={"n": {}})
    def files(lib):
        outer = chain(("isp", f"{lib}.inner:lib"), ("wb", WB))
        outer["nodes"]["isp"]["build"] = {"n": "wb.gain"}
        return {"inner": {"lib": inner}, "outer": {"design": outer}}
    lib = library(files)
    with pytest.raises(CompositionError, match="reads wb.gain, a register"):
        load(f"{lib}.outer:design")


def test_a_register_value_from_build_parameters_is_a_constant(library):
    d = chain(("range", "revela.blocks.tofrange:tofrange"),
              parameters={"mm": {"default": 7500}})
    d["nodes"]["range"]["values"] = {"span": "mm * 2", "offset": "-(1 << 4)"}
    lib = library({"m": {"d": d}})
    assert load(f"{lib}.m:d").instances["range"].values == {
        "span": 15000, "offset": -16}


@pytest.mark.parametrize("text, message", [
    ("1 / 2", "true division"),
    ("1.5", "a constant is an integer"),
    ("__import__('os')", "Call is not allowed"),
    ("a[0]", "Subscript is not allowed"),
    ("1 +", "is not an expression"),
])
def test_the_walker_accepts_only_integer_arithmetic(text, message):
    with pytest.raises(CompositionError, match=message):
        parse(text, "here")


def test_the_walker_computes_as_python_does_on_integers():
    names = {"a": 7, "b.c": -3}
    for text in ("a // 2", "b.c // 2", "a % 3", "b.c % 3", "a << 3",
                 "max(a, b.c) + min(a, 1) * abs(b.c)",
                 "a if b.c < 0 else 0", "(a > 2) + (a == 7)",
                 "~a ^ (a | 8) & 12"):
        tree = parse(text, "here")
        expected = eval(text.replace("b.c", "bc"), {}, {"a": 7, "bc": -3})
        assert evaluate(tree, names.__getitem__) == int(expected), text
    assert references(parse("x.y + max(z, 1)", "here")) == ["x.y", "z"]


# -- connections ------------------------------------------------------------ #

def test_an_unknown_port_is_refused(library):
    d = chain(("wb", WB))
    d["connections"][0] = ["in", "wb.input"]
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match=r"no input 'input'"):
        load(f"{lib}.m:d")


def test_a_sink_driven_twice_is_refused(library):
    d = chain(("a", WB), ("b", WB))
    d["connections"].append(["in", "b.in"])
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="already driven"):
        load(f"{lib}.m:d")


def test_an_open_composite_output_is_refused(library):
    inner = chain(("wb", WB))
    inner["connections"] = inner["connections"][:-1]
    lib = library({"m": {"lib": inner}})
    with pytest.raises(CompositionError, match="output out is driven by "
                                               "nothing inside it"):
        load(f"{lib}.m:lib")


def test_an_undriven_composite_input_is_refused(library):
    def files(lib):
        outer = chain(("isp", f"{lib}.inner:lib"))
        outer["connections"] = outer["connections"][1:]
        return {"inner": {"lib": chain(("wb", WB))},
                "outer": {"design": outer}}
    lib = library(files)
    with pytest.raises(CompositionError, match="used inside it and driven "
                                               "by nothing"):
        load(f"{lib}.outer:design")


# -- the file format -------------------------------------------------------- #

def test_an_address_is_refused_by_the_schema(library):
    d = chain(("wb", WB))
    d["addresses"] = {"wb": 256}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="addresses"):
        load(f"{lib}.m:d")


def test_a_reserved_name_is_refused_by_the_schema(library):
    lib = library({"m": {"d": chain(("max", WB))}})
    with pytest.raises(CompositionError):
        load(f"{lib}.m:d")


def test_a_generator_takes_its_build_parameters_from_its_signature(library):
    d = {"nodes": {"hist": {"block": "np2hw.frame_history:frame_history",
                            "build": {"frames": 3, "bits": 12,
                                      "width": 64, "height": 32}}}}
    lib = library({"m": {"d": d}})
    hist = load(f"{lib}.m:d").instances["hist"]
    assert hist.kind == "generator"
    assert hist.build == {"frames": 3, "bits": 12, "width": 64, "height": 32}
    d["nodes"]["hist"]["build"]["max_width"] = 64
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="not 'max_width'"):
        load(f"{lib}.m:d")


# -- the stream entering, and the bridge to today's build ------------------- #

ISP = "revela.blocks.bayer_isp:bayer_isp"
GEOMETRY = {"bit_depth": 12, "max_width": 64, "max_height": 32}


def test_the_stream_is_stated_where_it_enters():
    design = load(ISP, build=GEOMETRY)
    assert design.streams == {"in": {
        "bits": 12, "max_width": 64, "max_height": 32,
        "context": ["width", "height", "bayer_phase", "bit_depth"]}}


def test_a_wrapper_inherits_the_stream_and_states_nothing(library):
    def files(lib):
        outer = chain(("isp", ISP), ("gamma", "revela.blocks.gamma:rgb_gamma"))
        outer["nodes"]["isp"]["build"] = dict(GEOMETRY)
        return {"m": {"d": outer}}
    lib = library(files)
    design = load(f"{lib}.m:d")
    assert design.streams["in"]["bits"] == 12
    assert list(design.instances)[:2] == ["isp.blacklevel", "isp.whitebalance"]


def test_two_ports_stating_one_stream_differently_are_refused(library):
    def files(lib):
        outer = chain(("isp", ISP))
        outer["inputs"][0]["bits"] = 10
        outer["nodes"]["isp"]["build"] = dict(GEOMETRY)
        return {"m": {"d": outer}}
    lib = library(files)
    with pytest.raises(CompositionError, match="bits = 12, but .* states 10"):
        load(f"{lib}.m:d")


def test_a_stream_fact_on_an_output_is_refused(library):
    d = chain(("wb", WB))
    d["outputs"][0]["bits"] = 12
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="traced, not declared"):
        load(f"{lib}.m:d")


def test_the_bridge_builds_the_pipeline_from_the_stream_entering():
    pipeline = to_pipeline(load(ISP, build=GEOMETRY))
    assert (pipeline.name, pipeline.spec.bit_depth, pipeline.width,
            pipeline.height) == ("bayer_isp", 12, 64, 32)
    assert [s.path for s in pipeline.nodes] == [
        "blacklevel", "whitebalance", "ha_green", "ha_rb", "ccm"]
    assert pipeline.context_from_stream == {"width", "height",
                                            "bayer_phase", "bit_depth"}


def test_the_bridge_refuses_a_stream_that_states_no_geometry(library):
    lib = library({"m": {"d": chain(("wb", WB))}})
    with pytest.raises(CompositionError, match="states no bits, max_width"):
        to_pipeline(load(f"{lib}.m:d"))


def test_the_bridge_refuses_what_is_not_an_isp_block(library):
    d = chain(("wb", WB))
    d["nodes"]["hist"] = {"block": "np2hw.frame_history:frame_history",
                          "build": {"frames": 1, "bits": 12, "width": 8,
                                    "height": 8}}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match=r"hist \(generator\)"):
        to_pipeline(load(f"{lib}.m:d"))


def test_a_designs_values_are_the_register_files_reset_values(library):
    """One place, three readers: the register map, the model's defaults and
    the generated register file's reset all carry what the design set."""
    d = chain(("wb", WB), ("gamma", "revela.blocks.gamma:gamma"))
    d["inputs"][0].update(bits=12, max_width=64, max_height=32)
    d["nodes"]["wb"]["values"] = {"gain": [[300, 256], [256, 301]]}
    d["nodes"]["gamma"]["values"] = {"knots": list(range(0, 264, 8))[::-1]}
    lib = library({"m": {"d": d}})
    pipeline = to_pipeline(load(f"{lib}.m:d"))
    regmap = pipeline.register_map()
    default = {f"{b['path']}.{r['name']}": r["default"]
               for b in regmap["blocks"] for r in b["registers"]}
    assert default["wb.gain_0_0"] == 300 and default["wb.gain_1_1"] == 301
    assert default["gamma.knots_0"] == 256 and default["gamma.knots_32"] == 0
    assert pipeline.stage("wb").paramset.defaults()["gain_1_1"] == 301
    verilog = pipeline.generate(control=True).verilog
    assert "reg_wb_gain_0_0<=300;" in verilog
    assert "reg_gamma_knots_0<=256;" in verilog


def test_undeclared_values_keep_their_declared_reset(library):
    d = chain(("wb", WB))
    d["inputs"][0].update(bits=12, max_width=64, max_height=32)
    lib = library({"m": {"d": d}})
    regmap = to_pipeline(load(f"{lib}.m:d")).register_map()
    gain = {r["name"]: r["default"] for b in regmap["blocks"]
            if b["path"] == "wb" for r in b["registers"]}
    assert set(gain.values()) == {256}          # unity, as declared


def test_the_bridge_refuses_a_register_that_follows_another(library):
    d = chain(("left", WB), ("right", WB))
    d["inputs"][0].update(bits=12, max_width=64, max_height=32)
    d["nodes"]["right"]["values"] = {"gain": "left.gain"}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="sharing one register"):
        to_pipeline(load(f"{lib}.m:d"))


def test_a_design_is_named_by_reference_where_a_path_was():
    from revela import designs
    pipeline = designs.load("revela.boards.pynq_z2.isp:revela_isp_core")
    assert pipeline.name == "revela_isp_core"
    assert (pipeline.spec.bit_depth, pipeline.width, pipeline.height) == \
        (12, 1920, 1080)
    assert [s.path for s in pipeline.nodes][-2:] == ["rgb_gamma", "logo"]


# -- profiles --------------------------------------------------------------- #

from revela.designs import profile as profiles  # noqa: E402

GAIN = [[300, 256], [256, 300]]


def _pair():
    """Two bayer_isp side by side."""
    return {"inputs": [{"name": "left"}, {"name": "right"}],
            "outputs": [{"name": "l_out"}, {"name": "r_out"}],
            "nodes": {"l": {"block": ISP, "build": dict(GEOMETRY)},
                      "r": {"block": ISP, "build": dict(GEOMETRY)}},
            "connections": [["left", "l.in"], ["right", "r.in"],
                            ["l.out", "l_out"], ["r.out", "r_out"]]}


def _lib(library, profiles_):
    """A library: the pair as a design, and the profiles given."""
    return library(lambda lib: {"design": {"pair": _pair()},
                                "tuning": {"profiles": profiles_(lib)}})


def test_a_profile_is_keyed_relative_to_its_block_and_lands_at_every_instance(
        library):
    lib = _lib(library, lambda lib: {"sensor": {
        "for": ISP, "values": {"whitebalance.gain": GAIN}}})
    regmap = to_pipeline(load(f"{lib}.design:pair")).register_map()
    assert {c["path"] for c in regmap["composites"]} == {"", "l", "r"}
    flat = profiles.flatten(profiles.resolve(f"{lib}.tuning:sensor"), regmap)
    assert flat == {f"{side}.whitebalance.gain_{i}_{j}": GAIN[i][j]
                    for side in ("l", "r") for i in range(2)
                    for j in range(2)}


def test_an_included_profile_lands_at_its_node_and_the_outer_value_wins(
        library):
    lib = _lib(library, lambda lib: {
        "sensor": {"for": ISP, "values": {"whitebalance.gain": GAIN,
                                          "ccm.m": [[256, 0, 0], [0, 256, 0],
                                                    [0, 0, 256]]}},
        "rig": {"for": f"{lib}.design:pair",
                "values": {"l": f"{lib}.tuning:sensor",
                           "l.whitebalance.gain": [[400, 256], [256, 400]]}}})
    rig = profiles.resolve(f"{lib}.tuning:rig")
    assert rig.values["l.whitebalance.gain"] == [[400, 256], [256, 400]]
    assert rig.sources["l.ccm.m"] == f"{lib}.tuning:sensor"
    assert "r.ccm.m" not in rig.values


def test_an_include_for_a_different_block_is_refused(library):
    lib = _lib(library, lambda lib: {
        "wb": {"for": WB, "values": {"gain": GAIN}},
        "rig": {"for": f"{lib}.design:pair", "values": {"l": f"{lib}.tuning:wb"}}})
    with pytest.raises(CompositionError, match="is for .*whitebalance.*but l "
                                               "is .*bayer_isp"):
        profiles.resolve(f"{lib}.tuning:rig")


def test_a_register_the_block_does_not_have_is_refused(library):
    lib = _lib(library, lambda lib: {"sensor": {
        "for": ISP, "values": {"ccm.matrix": [[0]]}}})
    with pytest.raises(CompositionError, match=r"no register 'matrix'"):
        profiles.resolve(f"{lib}.tuning:sensor")


def test_a_profile_including_itself_is_refused(library):
    lib = _lib(library, lambda lib: {"loop": {
        "for": f"{lib}.design:pair", "values": {"l": f"{lib}.tuning:loop"}}})
    with pytest.raises(CompositionError):
        profiles.resolve(f"{lib}.tuning:loop")


def test_the_built_map_checks_shape_and_range(library):
    lib = _lib(library, lambda lib: {
        "flat": {"for": ISP, "values": {"whitebalance.gain": [256, 256]}},
        "big": {"for": ISP, "values": {"whitebalance.gain":
                                       [[70000, 256], [256, 256]]}}})
    regmap = to_pipeline(load(f"{lib}.design:pair")).register_map()
    with pytest.raises(CompositionError, match="shape does not match"):
        profiles.flatten(profiles.resolve(f"{lib}.tuning:flat"), regmap)
    with pytest.raises(CompositionError, match="70000 does not fit"):
        profiles.flatten(profiles.resolve(f"{lib}.tuning:big"), regmap)


def test_a_design_without_the_profiles_block_is_refused(library):
    lib = _lib(library, lambda lib: {"sensor": {
        "for": ISP, "values": {"whitebalance.gain": GAIN}}})
    flat_design = to_pipeline(load(f"{lib}.design:pair")).register_map()
    flat_design["composites"] = []
    with pytest.raises(CompositionError, match="has no revela.blocks.bayer_isp"):
        profiles.flatten(profiles.resolve(f"{lib}.tuning:sensor"), flat_design)


def test_the_board_resets_to_its_curve_and_the_sensor_profile_fits_it():
    regmap = to_pipeline(
        load("revela.boards.pynq_z2.isp:revela_isp_core")).register_map()
    knots = [r["default"] for b in regmap["blocks"] if b["path"] == "rgb_gamma"
             for r in b["registers"]]
    # The 2.2 curve, not the declared ramp: bright in the shadows.
    assert len(knots) == 33 and knots[:4] == [0, 53, 72, 87] and \
        knots[-1] == 256
    sensor = profiles.flatten(
        profiles.resolve("revela.sensors.ov5647.profile:ov5647"), regmap)
    assert len(sensor) == 17 and all(k.startswith("isp.") for k in sensor)


# -- libraries outside any installed package -------------------------------- #

def _outside(tmp_path):
    """A library a user keeps anywhere: plain folders, no __init__.py,
    referring to revela's blocks and to each other by reference."""
    root = tmp_path / "their_libs"
    (root / "acme" / "cams").mkdir(parents=True)
    (root / "acme" / "cams" / "imx999.json").write_text(json.dumps({
        "profiles": {"outdoor": {
            "for": ISP, "values": {"whitebalance.gain": GAIN}}}}))
    (root / "acme" / "rig.json").write_text(json.dumps({
        "blocks": {"small": {
            "inputs": [{"name": "in"}], "outputs": [{"name": "out"}],
            "nodes": {"isp": {"block": ISP, "build": dict(GEOMETRY)}},
            "connections": [["in", "isp.in"], ["isp.out", "out"]]}},
        "profiles": {"field": {
            "for": "acme.rig:small",
            "values": {"isp": "acme.cams.imx999:outdoor"}}}}))
    return root


def test_a_library_directory_makes_outside_references_resolve(tmp_path):
    import subprocess
    import sys as _sys
    root = _outside(tmp_path)
    out = tmp_path / "pack"
    ok = subprocess.run(
        [_sys.executable, "-m", "revela", "generate", "acme.rig:small",
         "--library", str(root), "--out", str(out), "--no-verify"],
        capture_output=True, text=True, cwd=tmp_path)
    assert ok.returncode == 0, ok.stderr
    regmap = out / "small_regmap.json"
    flat = subprocess.run(
        [_sys.executable, "-m", "revela.designs.profile", "acme.rig:field",
         "--library", str(root), "--map", str(regmap)],
        capture_output=True, text=True, cwd=tmp_path)
    assert flat.returncode == 0, flat.stderr
    assert json.loads(flat.stdout) == {
        f"isp.whitebalance.gain_{i}_{j}": GAIN[i][j]
        for i in range(2) for j in range(2)}
    # ...and without it, the reference means nothing here: refused, named.
    missing = subprocess.run(
        [_sys.executable, "-m", "revela.designs.profile", "acme.rig:field",
         "--map", str(regmap)], capture_output=True, text=True, cwd=tmp_path)
    assert missing.returncode != 0 and "acme" in missing.stderr


def test_a_library_that_is_not_a_directory_is_refused(tmp_path):
    from revela.designs.composite import add_libraries
    with pytest.raises(CompositionError, match="not a directory"):
        add_libraries([tmp_path / "nowhere"])


def test_a_reset_value_outside_its_register_is_refused_at_the_block():
    from revela.blocks.whitebalance import whitebalance
    with pytest.raises(ValueError, match="reset value 70000 outside"):
        whitebalance.configure(resets={"gain": [[70000, 256], [256, 256]]})


# -- the shorthand ---------------------------------------------------------- #

def test_a_module_alone_names_the_block_called_after_its_file():
    from revela.designs.composite import canonical
    assert find("revela.blocks.bayer_isp").ref == \
        "revela.blocks.bayer_isp:bayer_isp"
    assert find("revela.blocks.ccm").obj is find("revela.blocks.ccm:ccm").obj
    assert canonical("revela.blocks.gamma:rgb_gamma") == \
        "revela.blocks.gamma:rgb_gamma"
    with pytest.raises(CompositionError, match="has no 'hamilton_adams'"):
        find("revela.blocks.demosaic.hamilton_adams")


def test_short_and_full_references_are_one_block_everywhere(library):
    """A profile written for the short form anchors where the map records
    the full one: references are compared in one form."""
    lib = library(lambda lib: {
        "design": {"pair": _pair()},
        "tuning": {"profiles": {"s": {
            "for": "revela.blocks.bayer_isp",
            "values": {"whitebalance.gain": GAIN}}}}})
    regmap = to_pipeline(load(f"{lib}.design:pair")).register_map()
    flat = profiles.flatten(profiles.resolve(f"{lib}.tuning:s"), regmap)
    assert len(flat) == 8


# -- designs for a toolchain: when, pins, vendor settings ------------------- #

IP = "revela.boards.zynq7.ip"


def test_a_node_whose_when_is_false_is_not_built(library):
    d = {"parameters": {"extra": {"default": 0}},
         "nodes": {"a": {"block": f"{IP}:xlconstant"},
                   "b": {"block": f"{IP}:xlconstant", "when": "extra"}},
         "connections": []}
    lib = library({"m": {"d": d}})
    assert list(load(f"{lib}.m:d").instances) == ["a"]
    assert list(load(f"{lib}.m:d", build={"extra": 1}).instances) == ["a", "b"]


def test_a_connection_to_a_node_not_built_must_carry_the_same_when(library):
    d = {"parameters": {"extra": {"default": 0}},
         "nodes": {"a": {"block": f"{IP}:xlconstant"},
                   "b": {"block": f"{IP}:util_vector_logic", "when": "extra"}},
         "connections": [["a.dout", "b.Op1"]]}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="guard the connection"):
        load(f"{lib}.m:d")
    d["connections"] = [{"from": "a.dout", "to": "b.Op1", "when": "extra"}]
    lib = library({"m": {"d": d}})
    assert load(f"{lib}.m:d").edges == []
    assert load(f"{lib}.m:d", build={"extra": 1}).edges == [("a.dout", "b.Op1")]


def test_pins_are_gathered_and_are_the_fpgas_so_their_names_are_global(library):
    jack = {"pins": [{"name": "led", "dir": "O"}],
            "nodes": {"k": {"block": f"{IP}:xlconstant"}},
            "connections": [["k.dout", "led"]]}
    def files(lib):
        top = {"nodes": {"j1": {"block": f"{lib}.jack:jack"},
                         "j2": {"block": f"{lib}.jack:jack"}},
               "connections": []}
        return {"jack": {"jack": jack}, "top": {"top": top}}
    lib = library(files)
    with pytest.raises(CompositionError, match="pin led is also"):
        load(f"{lib}.top:top")
    one = library(lambda lib: {"jack": {"jack": jack}, "top": {"top": {
        "nodes": {"j": {"block": f"{lib}.jack:jack"}}, "connections": []}}})
    design = load(f"{one}.top:top")
    assert design.pins == {"led": {"dir": "O"}}
    assert design.edges == [("j.k.dout", "@led")]


def test_vendor_settings_are_open_and_config_is_verbatim(library):
    d = {"parameters": {"n": {"default": 3}},
         "nodes": {"c": {"block": f"{IP}:xlconcat", "build": {"NUM_PORTS": "n + 1"},
                         "config": {"IN0_WIDTH": "No_buffer"}}},
         "connections": []}
    lib = library({"m": {"d": d}})
    c = load(f"{lib}.m:d").instances["c"]
    assert c.build == {"NUM_PORTS": 4} and c.config == {"IN0_WIDTH": "No_buffer"}


def test_config_on_a_leaf_that_is_not_vendor_ip_is_refused(library):
    d = chain(("wb", WB))
    d["nodes"]["wb"]["config"] = {"x": 1}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="not vendor IP"):
        load(f"{lib}.m:d")


def test_a_default_may_be_derived_and_a_word_is_quoted(library):
    d = {"parameters": {"khz": {"default": 74250},
                        "genlock": {"default": "1 if khz < 100000 else 0"},
                        "clock": {"default": "'/a/b' if genlock else '/c/d'"}},
         "nodes": {"k": {"block": f"{IP}:xlconstant"}}, "connections": []}
    lib = library({"m": {"d": d}})
    assert load(f"{lib}.m:d").parameters == {
        "khz": 74250, "genlock": 1, "clock": "/a/b"}
    assert load(f"{lib}.m:d", build={"khz": 148500}).parameters["clock"] == "/c/d"


def test_a_name_that_is_both_a_parameter_and_a_node_is_refused(library):
    d = {"parameters": {"k": {"default": 1}},
         "nodes": {"k": {"block": f"{IP}:xlconstant"}}, "connections": []}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="both a parameter and a node"):
        load(f"{lib}.m:d")


# -- composites a function writes ------------------------------------------- #

GEN = '''
def pair(config):
    ports = [{"name": n, **({"when": "extra"} if n == config["optional"] else {})}
             for n in config["names"]]
    nodes = {f"k_{n}": dict({"block": "revela.boards.zynq7.ip:xlconstant"},
                            **({"when": "extra"} if n == config["optional"] else {}))
             for n in config["names"]}
    conns = [dict({"from": f"k_{n}.dout", "to": n},
                  **({"when": "extra"} if n == config["optional"] else {}))
             for n in config["names"]]
    return {"parameters": {"extra": {"description": "x"}},
            "outputs": ports, "nodes": nodes, "connections": conns}
pair.composite = True

def broken(config):
    return {"nodes": {}}
broken.composite = True
'''


def test_a_function_can_write_a_composite_from_its_config(library):
    lib = library(lambda lib: {"m": {"d": {
        "parameters": {"extra": {"default": 0}},
        "nodes": {"g": {"block": f"{lib}.gen:pair", "build": {"extra": "extra"},
                        "config": {"names": ["a", "b"], "optional": "b"}},
                  "sink": {"block": f"{IP}:xlconcat"}},
        "connections": [["g.a", "sink.In0"],
                        {"from": "g.b", "to": "sink.In1", "when": "extra"}]}}},
        python={"gen": GEN})
    d = load(f"{lib}.m:d")
    assert sorted(d.instances) == ["g.k_a", "sink"]
    assert d.edges == [("g.k_a.dout", "sink.In0")]
    d = load(f"{lib}.m:d", build={"extra": 1})
    assert sorted(d.instances) == ["g.k_a", "g.k_b", "sink"]


def test_a_written_composite_is_checked_like_one_written_by_hand(library):
    lib = library(lambda lib: {"m": {"d": {
        "nodes": {"g": {"block": f"{lib}.gen:broken"}}, "connections": []}}},
        python={"gen": GEN})
    with pytest.raises(CompositionError, match="wrote a composite that is not one"):
        load(f"{lib}.m:d")
