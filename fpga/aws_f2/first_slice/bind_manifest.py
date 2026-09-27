#!/usr/bin/env python3
"""Bind a built transport to reviewed DCP evidence and an available AFI receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cl", type=Path, required=True)
    parser.add_argument("--build-result", type=Path, required=True)
    parser.add_argument("--transport-work", type=Path, required=True)
    parser.add_argument(
        "--afi-description",
        type=Path,
        required=True,
        help=
        "describe-fpga-images JSON for the one reviewed available custom AFI"
    )
    args = parser.parse_args()
    cl, work = args.cl.resolve(), args.transport_work.resolve()
    repo = Path(__file__).resolve().parents[3]
    source = json.loads((cl / "source-manifest.json").read_text())
    build = json.loads(args.build_result.read_text())
    if (args.build_result.parent / "exit-code.txt").read_text().strip() != "0":
        raise ValueError("DCP run did not finish successfully")
    if build.get("status") != "routed_DCP_ready_for_AFI_submission":
        raise ValueError("DCP build not ready")
    dcps = []
    for relative, item in build["artifacts"].items():
        path = (cl / relative).resolve()
        path.relative_to(cl)
        if path.stat().st_size != item["bytes"] or sha256(path
                                                          ) != item["sha256"]:
            raise ValueError("DCP/report artifact changed: " + relative)
        if relative.endswith(".post_route.dcp"):
            dcps.append(item["sha256"])
    if len(dcps) != 1:
        raise ValueError("Expected one verified routed checkpoint")
    images = json.loads(args.afi_description.read_text())["FpgaImages"]
    if len(images) != 1 or images[0]["State"]["Code"] != "available":
        raise ValueError("Expected one available AFI")
    afi = images[0]
    agfi = afi["FpgaImageGlobalId"]
    if not re.fullmatch(r"agfi-[0-9a-f]{17}", agfi):
        raise ValueError("Invalid AGFI")
    expected_pci = {
        "DeviceId": "0xf010",
        "VendorId": "0x1d0f",
        "SubsystemId": "0x0103",
        "SubsystemVendorId": "0x1d0f"
    }
    if afi.get("PciId") != expected_pci:
        raise ValueError(
            "Available AFI has a different custom CL PCI identity"
        )
    if (work / "build-exitcode.txt").read_text().strip() != "0":
        raise ValueError("Transport build failed")
    for line in (work / "build-sha256.txt").read_text().splitlines():
        expected, relative = line.split(maxsplit=1)
        path = (work / relative).resolve()
        path.relative_to(work)
        if sha256(path) != expected:
            raise ValueError("Transport build artifact changed: " + relative)
    if sha256(work / "transport.cpp"
              ) != sha256(repo / "fpga/aws_f2/first_slice/transport.cpp"):
        raise ValueError("Transport source changed after compilation")
    sources = {
        "fpga/aws_f2/first_slice/transport.cpp":
        sha256(work / "transport.cpp")
    }
    for filename in ("weight_store.sv", "first_slice_top.sv", "dot128.sv",
                     "f2_memory_bridge.sv", "cl_bonsai_first_slice.sv",
                     "cl_id_defines.vh"):
        relative = (
            "fpga/aws_f2/first_slice/"
            if filename.startswith("cl_") else "hdl/verilog/first_slice/"
        ) + filename
        expected = source["source_sha256"]["design/" + filename]
        if sha256(cl / "design" / filename) != expected or sha256(
                repo / relative) != expected:
            raise ValueError("DCP source changed: " + relative)
        sources[relative] = expected
    manifest = {
        "schema":
        1,
        "backend":
        "aws_f2",
        "agfi":
        agfi,
        "afi":
        afi["FpgaImageId"],
        "dcp_sha256":
        dcps[0],
        "binary_sha256":
        sha256(work / "transport"),
        "source_sha256":
        sources,
        "tool_version": (work / "tool-version.txt").read_text().strip(),
        "sdk_revision": (work / "sdk-revision.txt").read_text().strip(),
        "afi_description_sha256":
        sha256(args.afi_description),
        "dcp_build_result_sha256":
        sha256(args.build_result),
        "afi_submission_binding":
        "Operator must retain/review S3 upload checksum and create-fpga-image receipt linking this DCP tar to this AFI; describe metadata alone cannot establish that link."
    }
    destination = work / "transport.manifest.json"
    with destination.open("x") as output:
        output.write(json.dumps(manifest, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
