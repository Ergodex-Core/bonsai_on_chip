#!/usr/bin/env python3
"""Stage a fresh, source-bound native-HBM CL. Does not build or operate a host."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

AWS_REV = "b603a81f65666e0cf7a67ee5cf18b148eb6b08c3"
PINS = {
    "hdk/common/ip": "6d32be972e6da854e61a8d3d6ec0466ab491c1b3",
    "hdk/common/shell_stable/hlx": "2383c2b64572c75163b1b60fbd0abea482c637e6",
}
RTL = (
    "first_slice_top.sv", "weight_store.sv", "dot128.sv",
    "f2_memory_bridge.sv", "hbm_line_bridge.sv", "hbm_cdc_mailbox.sv"
)
WRAPPERS = (
    "cl_bonsai_hbm_rom.sv", "hbm_fixed_clock.sv", "hbm_rom_controller.sv",
    "cl_id_defines.vh"
)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args],
                                   text=True).strip()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Canonical actual-weight fixture.json for vendor simulation"
    )
    parser.add_argument(
        "--image",
        type=Path,
        help="Canonical weights.bin; checked, never copied in full"
    )
    args = parser.parse_args()
    if bool(args.fixture) != bool(args.image):
        parser.error("--fixture and --image must be supplied together")
    hdk, output = args.hdk.resolve(), args.output.resolve()
    source = Path(__file__).resolve().parent
    repo = source.parents[2]
    for path in (hdk, output):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)):
            raise SystemExit(
                "HDK/output paths must exclude whitespace/metacharacters"
            )
    if output.exists():
        raise SystemExit("Use a fresh dedicated output directory")
    for relative, revision in {"": AWS_REV, **PINS}.items():
        path = hdk / relative
        if git(path, "rev-parse", "HEAD") != revision:
            raise SystemExit("Wrong source pin: " + relative)
        origin = git(path, "remote", "get-url", "origin")
        expected = "https://github.com/aws/" + (
            "aws-fpga.git" if not relative else "aws-fpga-resources.git"
        )
        if origin != expected:
            raise SystemExit("Unexpected origin: " + relative)
        subprocess.run([
            "git", "-C",
            str(path), "diff", "--exit-code", "--quiet", "HEAD"
        ],
                       check=True)
        if relative:
            subprocess.run(["git", "-C", str(path), "lfs", "fsck"], check=True)
    subprocess.run([
        "python3",
        str(source / "audit_sources.py"), "--hdk",
        str(hdk), "--resources",
        str(hdk / "hdk/common/ip")
    ],
                   check=True)
    cl = output / "cl_bonsai_hbm_rom"
    for directory in ("design", "build/scripts", "build/constraints",
                      "build/checkpoints", "build/reports",
                      "build/src_post_encryption", "provenance",
                      "verif/scripts", "verif/tests"):
        (cl / directory).mkdir(parents=True, exist_ok=True)
    for name in RTL:
        shutil.copy2(
            repo / "hdl/verilog/first_slice" / name, cl / "design" / name
        )
    for name in WRAPPERS:
        shutil.copy2(source / name, cl / "design" / name)
    common = hdk / "hdk/common/shell_stable/build/scripts"
    tracked = git(
        hdk, "ls-tree", "-r", "--name-only", AWS_REV,
        "hdk/common/shell_stable/build/scripts"
    ).splitlines()
    for relative in tracked:
        path = hdk / relative
        if path.parent == common and path.is_file():
            (cl / "build/scripts" / path.name).symlink_to(path)
    for name in ("encrypt.tcl", "synth_cl_bonsai_hbm_rom.tcl",
                 "audit_dcp.tcl"):
        target = cl / "build/scripts" / name
        if target.is_symlink():
            target.unlink()
        shutil.copy2(source / name, target)
    for name in ("cl_synth_user.xdc", "cl_timing_user.xdc",
                 "small_shell_cl_pnr_user.xdc"):
        shutil.copy2(source / name, cl / "build/constraints" / name)
    shutil.copy2(
        repo / "fpga/aws_f2/first_slice/transport.cpp",
        cl / "provenance/transport.cpp"
    )
    for name in ("Makefile", "Makefile.tests", "top.xsim.f", "waves.tcl"):
        shutil.copy2(source / name, cl / "verif/scripts" / name)
    shutil.copy2(
        source / "test_hbm_rom.sv", cl / "verif/tests/test_hbm_rom.sv"
    )
    compact = None
    if args.fixture:
        spec = importlib.util.spec_from_file_location(
            "stage_fixture", source.parent / "first_slice/stage_fixture.py"
        )
        staging = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(staging)
        compact = staging.stage_fixture(
            args.fixture.resolve(), args.image.resolve(), cl / "verif/tests"
        )
    config_paths = [
        hdk / "hdk/common/ip/cl_ip/cl_ip.srcs/sources_1/ip" / ip /
        (ip + ".xci") for ip in ("cl_hbm", "clk_mmcm_hbm")
    ]
    manifest = {
        "schema":
        1,
        "memory_backend":
        "hbm",
        "backend_id":
        3,
        "hdk":
        str(hdk),
        "hdk_revision":
        AWS_REV,
        "resource_pins":
        PINS,
        "repo_head":
        git(repo, "rev-parse", "HEAD"),
        "repo_dirty":
        bool(git(repo, "status", "--porcelain")),
        "source_sha256": {
            str(p.relative_to(cl)): sha(p)
            for p in sorted(cl.rglob("*"))
            if p.is_file()
        },
        "vendor_config_sha256": {
            str(p.relative_to(hdk)): sha(p)
            for p in config_paths
        },
        "configuration": {
            "map_version": 1,
            "axi_port": 15,
            "pseudochannel": 15,
            "physical_local_bytes": 536870912,
            "logical_max_bytes": 268435456,
            "native_address": "(15 << 29) | local_byte_address",
            "main_clock_hz": 250000000,
            "hbm_clock_hz": 450000000,
            "reference_clock_hz": 100000000,
            "clock_recipe_hbm": "H2",
            "native_axi_beats_per_line": 2,
            "native_axi_beat_bytes": 32,
            "parity_enabled": False,
            "ecc_correction_enabled": False,
            "scrubbing_enabled": False
        },
        "compact_fixture":
        compact,
        "validation":
        "staged only; no vendor simulation, synthesis, route or hardware pass",
    }
    (cl /
     "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(cl)


if __name__ == "__main__":
    main()
