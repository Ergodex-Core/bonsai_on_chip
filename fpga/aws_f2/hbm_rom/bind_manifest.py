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
    parser.add_argument(
        "--implementation-review",
        type=Path,
        required=True,
        help="Independent review JSON binding DCP/source-manifest/CDC/DRC hashes"
    )
    parser.add_argument(
        "--afi-submission-record",
        type=Path,
        required=True,
        help=
        "Trusted uploader record binding tar checksum, create receipt and AFI IDs"
    )
    args = parser.parse_args()
    cl, work = args.cl.resolve(), args.transport_work.resolve()
    repo = Path(__file__).resolve().parents[3]
    source = json.loads((cl / "source-manifest.json").read_text())
    build = json.loads(args.build_result.read_text())
    if (args.build_result.parent / "exit-code.txt").read_text().strip() != "0":
        raise ValueError("DCP run did not finish successfully")
    if build.get(
            "status"
    ) != "DCP timing checked; independent CDC/DRC review and hardware execution pending":
        raise ValueError("DCP build not ready")
    if source.get("memory_backend") != "hbm" or source.get("backend_id") != 3:
        raise ValueError("Source manifest is not native HBM")
    for relative, expected in source["source_sha256"].items():
        path = (cl / relative).resolve()
        # Build script links resolve into the pinned HDK; their bytes are hashed.
        if sha256(path) != expected:
            raise ValueError("Staged source changed: " + relative)
    dcps = []
    tar_hashes = []
    report_hashes = {}
    for relative, item in build["artifacts"].items():
        path = (cl / relative).resolve()
        path.relative_to(cl)
        if path.stat().st_size != item["bytes"] or sha256(path
                                                          ) != item["sha256"]:
            raise ValueError("DCP/report artifact changed: " + relative)
        if relative.endswith(".post_route.dcp"):
            dcps.append(item["sha256"])
        if relative.endswith(".Developer_CL.tar"):
            tar_hashes.append(item["sha256"])
        if path.name in ("cdc.rpt", "drc.rpt"):
            report_hashes[path.name] = item["sha256"]
    if len(dcps) != 1:
        raise ValueError("Expected one verified routed checkpoint")
    if len(tar_hashes) != 1:
        raise ValueError("Expected one verified AFI input tarball")
    review = json.loads(args.implementation_review.read_text())
    required_review = {
        "decision": "PASS",
        "dcp_sha256": dcps[0],
        "source_manifest_sha256": sha256(cl / "source-manifest.json"),
        "cdc_report_sha256": report_hashes.get("cdc.rpt"),
        "drc_report_sha256": report_hashes.get("drc.rpt"),
    }
    if not review.get("reviewer") or any(
            value is None or review.get(key) != value
            for key, value in required_review.items()):
        raise ValueError(
            "Independent implementation/CDC/DRC review does not bind these artifacts"
        )
    images = json.loads(args.afi_description.read_text())["FpgaImages"]
    if len(images) != 1 or images[0]["State"]["Code"] != "available":
        raise ValueError("Expected one available AFI")
    afi = images[0]
    agfi = afi["FpgaImageGlobalId"]
    if not re.fullmatch(r"agfi-[0-9a-f]{17}", agfi):
        raise ValueError("Invalid AGFI")
    submission = json.loads(args.afi_submission_record.read_text())
    expected_submission = {
        "afi": afi["FpgaImageId"],
        "agfi": agfi,
        "tar_sha256": tar_hashes[0],
        "dcp_sha256": dcps[0]
    }
    if any(submission.get(k) != v for k, v in expected_submission.items()):
        raise ValueError(
            "AFI submission record does not bind this DCP/tarball"
        )
    if not all(submission.get(key)
               for key in ("uploader", "s3_bucket", "s3_key",
                           "create_receipt_sha256", "upload_checksum_sha256")):
        raise ValueError("Incomplete trusted uploader receipt/checksum record")
    if submission["upload_checksum_sha256"] != tar_hashes[0]:
        raise ValueError("Uploaded bytes differ from the prepared tarball")
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
    transport = json.loads((work / "transport-manifest.json").read_text())
    if (transport.get("memory_backend") != "hbm"
            or transport.get("backend_id") != 3
            or transport.get("defines") != ["MEMORY_BACKEND_HBM=1"]):
        raise ValueError("Transport was not compiled with HBM identity")
    for relative, expected in transport["source_and_binary_sha256"].items():
        path = (work / relative).resolve()
        path.relative_to(work)
        if sha256(path) != expected:
            raise ValueError("HBM transport artifact changed: " + relative)
    sources = {
        "fpga/aws_f2/first_slice/transport.cpp":
        sha256(work / "transport.cpp")
    }
    common = (
        "weight_store.sv", "first_slice_top.sv", "dot128.sv",
        "f2_memory_bridge.sv", "hbm_line_bridge.sv", "hbm_cdc_mailbox.sv"
    )
    wrappers = (
        "cl_bonsai_hbm_rom.sv", "hbm_rom_controller.sv", "hbm_fixed_clock.sv",
        "cl_id_defines.vh"
    )
    for filename in common + wrappers:
        relative = (
            "hdl/verilog/first_slice/"
            if filename in common else "fpga/aws_f2/hbm_rom/"
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
        "memory_backend":
        "hbm",
        "backend_id":
        3,
        "transport_defines":
        transport["defines"],
        "configuration":
        source["configuration"],
        "implementation_review_sha256":
        sha256(args.implementation_review),
        "afi_submission_record_sha256":
        sha256(args.afi_submission_record),
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
        "Trusted uploader record checked for artifact hashes/IDs; remote S3/create receipt authenticity is an operator/reviewer responsibility, not established by describe metadata."
    }
    destination = work / "transport.manifest.json"
    with destination.open("x") as output:
        output.write(json.dumps(manifest, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
