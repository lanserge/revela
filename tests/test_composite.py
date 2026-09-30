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
    ("1.5", "every parameter is an integer"),
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


def test_the_bridge_refuses_reset_values_it_cannot_carry(library):
    d = chain(("wb", WB))
    d["inputs"][0].update(bits=12, max_width=64, max_height=32)
    d["nodes"]["wb"]["values"] = {"gain": [[300, 256], [256, 300]]}
    lib = library({"m": {"d": d}})
    with pytest.raises(CompositionError, match="does not take reset values"):
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


def test_the_shipped_profiles_resolve_against_the_board():
    regmap = to_pipeline(
        load("revela.boards.pynq_z2.isp:revela_isp_core")).register_map()
    display = profiles.flatten(
        profiles.resolve("revela.boards.pynq_z2.isp:display"), regmap)
    sensor = profiles.flatten(
        profiles.resolve("revela.sensors.ov5647.profile:ov5647"), regmap)
    assert len(display) == 33 and all(k.startswith("rgb_gamma.knots_")
                                      for k in display)
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
