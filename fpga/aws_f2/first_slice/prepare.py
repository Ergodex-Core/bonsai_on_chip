#!/usr/bin/env python3
"""Stage a fresh custom CL from the pinned HDK and the current first-slice RTL."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from stage_fixture import stage_fixture

AWS_REV = "b603a81f65666e0cf7a67ee5cf18b148eb6b08c3"
PINS = {
    "hdk/common/ip": "6d32be972e6da854e61a8d3d6ec0466ab491c1b3",
    "hdk/common/shell_stable/hlx": "2383c2b64572c75163b1b60fbd0abea482c637e6",
}
RTL = (
    "first_slice_top.sv", "weight_store.sv", "dot128.sv", "f2_memory_bridge.sv"
)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args],
                                   text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hdk", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Fresh parent directory; creates cl_bonsai_first_slice within it",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="Generated first-slice fixture.json"
    )
    parser.add_argument(
        "--image",
        type=Path,
        required=True,
        help="Full canonical weights.bin; validated but not copied"
    )
    args = parser.parse_args()
    hdk, output = args.hdk.resolve(), args.output.resolve()
    source = Path(__file__).resolve().parent
    repo = source.parents[2]
    for path in (hdk, output):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)):
            raise SystemExit(
                "HDK and output paths must exclude whitespace/metacharacters"
            )
    if output.exists():
        raise SystemExit(
            "Output already exists; use a fresh dedicated directory"
        )
    if git(hdk, "rev-parse", "HEAD") != AWS_REV:
        raise SystemExit("AWS HDK revision differs from the ERG-102 pin")
    if git(hdk, "remote", "get-url",
           "origin") != "https://github.com/aws/aws-fpga.git":
        raise SystemExit("Unexpected AWS HDK origin")
    subprocess.run(
        [
            "git", "-C",
            str(hdk), "diff", "--exit-code", "--quiet", "HEAD", "--"
        ],
        check=True,
    )
    for relative, revision in PINS.items():
        if git(hdk / relative, "rev-parse", "HEAD") != revision:
            raise SystemExit(f"Wrong resource pin: {relative}")
        subprocess.run(
            [
                "git",
                "-C",
                str(hdk / relative),
                "diff",
                "--exit-code",
                "--quiet",
                "HEAD",
            ],
            check=True,
        )
        subprocess.run(["git", "-C",
                        str(hdk / relative), "lfs", "fsck"],
                       check=True)
    for name in RTL:
        if not (repo / "hdl/verilog/first_slice" / name).is_file():
            raise SystemExit(f"Required first-slice RTL missing: {name}")
    cl = output / "cl_bonsai_first_slice"
    for relative in (
            "design",
            "build/scripts",
            "build/constraints",
            "build/checkpoints",
            "build/reports",
            "build/src_post_encryption",
            "verif/scripts",
            "verif/tests",
    ):
        (cl / relative).mkdir(parents=True, exist_ok=True)
    for name in RTL:
        shutil.copy2(
            repo / "hdl/verilog/first_slice" / name, cl / "design" / name
        )
    for name in ("cl_bonsai_first_slice.sv", "cl_id_defines.vh"):
        shutil.copy2(source / name, cl / "design" / name)
    # Common scripts remain at the verified pin; no stale example build objects
    # or unrelated worktree files are copied into the custom CL.
    common = hdk / "hdk/common/shell_stable/build/scripts"
    tracked = git(
        hdk,
        "ls-tree",
        "-r",
        "--name-only",
        AWS_REV,
        "hdk/common/shell_stable/build/scripts",
    ).splitlines()
    for relative in tracked:
        path = hdk / relative
        if path.parent == common and path.is_file():
            (cl / "build/scripts" / path.name).symlink_to(path)
    for name in ("encrypt.tcl", "synth_cl_bonsai_first_slice.tcl",
                 "audit_dcp.tcl"):
        target = cl / "build/scripts" / name
        if target.is_symlink():
            target.unlink()
        shutil.copy2(source / name, target)
    for name in ("cl_synth_user.xdc", "cl_timing_user.xdc",
                 "small_shell_cl_pnr_user.xdc"):
        shutil.copy2(source / name, cl / "build/constraints" / name)
    for name in ("Makefile", "Makefile.tests", "top.xsim.f", "waves.tcl"):
        shutil.copy2(source / name, cl / "verif/scripts" / name)
    shutil.copy2(
        source / "test_first_slice.sv", cl / "verif/tests/test_first_slice.sv"
    )
    compact = stage_fixture(
        args.fixture.resolve(), args.image.resolve(), cl / "verif/tests"
    )
    hashes = {}
    for path in sorted(cl.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(cl))
                   ] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema":
        1,
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
        "source_sha256":
        hashes,
        "compact_fixture":
        compact,
        "clock_hz":
        250000000,
        "execution_boundary":
        "custom RTL plus physical DDR via sh_ddr; no BRAM image backend",
    }
    (cl /
     "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(cl)


if __name__ == "__main__":
    main()
