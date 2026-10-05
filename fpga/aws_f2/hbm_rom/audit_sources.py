#!/usr/bin/env python3
"""Check exact vendor configuration and native pin binding without fetching IP/LFS."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare import AWS_REV, PINS, git, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hdk", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument(
        "--verilator",
        help=
        "Also lint widths using vendor-derived declaration stubs (not simulation)"
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    resource_rev = PINS["hdk/common/ip"]
    assert git(args.hdk, "rev-parse", "HEAD") == AWS_REV, "Wrong HDK revision"
    assert git(
        args.resources, "rev-parse", "HEAD"
    ) == resource_rev, "Wrong IP revision"
    results = {}
    declarations = []
    for module, wrapper, instance in (("cl_hbm", "hbm_rom_controller.sv",
                                       "HBM_CORE_I"),
                                      ("clk_mmcm_hbm", "hbm_fixed_clock.sv",
                                       "CLK_MMCM_HBM_I")):
        path = f"cl_ip/cl_ip.gen/sources_1/ip/{module}/{module}.veo"
        template = subprocess.check_output([
            "git", "-C",
            str(args.resources), "show", f"{resource_rev}:{path}"
        ],
                                           text=True)
        expected = set(re.findall(r"\.([A-Za-z_0-9]+)\s*\(", template))
        body = (source / wrapper).read_text().split(instance + " (",
                                                    1)[1].split(");", 1)[0]
        actual = re.findall(r"\.([A-Za-z_0-9]+)\s*\(", body)
        assert len(actual) == len(set(actual)), "Duplicate native port binding"
        assert expected == set(
            actual
        ), f"{module} ports differ: {expected ^ set(actual)}"
        pins = re.findall(
            r"\.([A-Za-z_0-9]+)\s*\([^)]*\).*?//\s*(input|output)\s+(?:wire\s+)?(\[[^]]+\])?",
            template
        )
        assert {
            pin[0]
            for pin in pins
        } == expected, "Incomplete vendor declaration parse"
        declarations.append(
            "module " + module + "(\n" + ",\n".join(
                f"  {direction} logic {width} {name}"
                for name, direction, width in pins
            ) + "\n); endmodule\n"
        )
        results[module] = {
            "connected_ports": len(expected),
            "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
            "wrapper_sha256": sha(source / wrapper)
        }
    xci = args.resources / "cl_ip/cl_ip.srcs/sources_1/ip/cl_hbm/cl_hbm.xci"
    params = json.loads(xci.read_text()
                        )["ip_inst"]["parameters"]["component_parameters"]
    for channel in range(16):
        for suffix in ("USER_PARITY_EN", "DQ_PARITY_EN",
                       "ENABLE_ECC_CORRECTION", "ENABLE_ECC_SCRUBBING"):
            assert params[f"USER_MC{channel}_{suffix}"][0]["value"] == "false"
    assert params["USER_HBM_DENSITY"][0]["value"] == "16GB"
    clock = args.resources / "cl_ip/cl_ip.srcs/sources_1/ip/clk_mmcm_hbm/clk_mmcm_hbm.xci"
    cp = json.loads(clock.read_text()
                    )["ip_inst"]["parameters"]["component_parameters"]
    assert float(cp["PRIM_IN_FREQ"][0]["value"]) == 100
    assert float(cp["CLKOUT1_REQUESTED_OUT_FREQ"][0]["value"]) == 450
    if args.verilator:
        # These declarations validate native port widths/types only. They have
        # no behavior and MUST NEVER be used as simulation/hardware evidence.
        declarations.append(
            """
module xpm_cdc_async_rst #(parameter DEST_SYNC_FF=4, INIT_SYNC_FF=0, RST_ACTIVE_HIGH=0)
(input logic src_arst,dest_clk,output logic dest_arst); endmodule
module xpm_cdc_single #(parameter DEST_SYNC_FF=4,SRC_INPUT_REG=0)
(input logic src_clk,src_in,dest_clk,output logic dest_out); endmodule
module xpm_cdc_array_single #(parameter DEST_SYNC_FF=4,SRC_INPUT_REG=0,WIDTH=1)
(input logic src_clk,input logic [WIDTH-1:0] src_in,input logic dest_clk,
output logic [WIDTH-1:0] dest_out); endmodule
"""
        )
        with tempfile.TemporaryDirectory(prefix="hbm-pin-lint-") as temporary:
            stub = Path(temporary) / "vendor_declarations.sv"
            stub.write_text("\n".join(declarations))
            for top in ("hbm_rom_controller", "hbm_fixed_clock"):
                subprocess.run([
                    args.verilator, "--lint-only", "--top-module", top,
                    str(source / (top + ".sv")),
                    str(stub)
                ],
                               check=True)
        results["declaration_lint"
                ] = "PASS; no functional vendor model executed"
    print(
        json.dumps({
            "status": "PASS",
            "scope": "source/pin/config audit only; not IP simulation",
            "hdk_revision": AWS_REV,
            "resource_revision": resource_rev,
            "hbm_xci_sha256": sha(xci),
            "clock_xci_sha256": sha(clock),
            "modules": results
        },
                   indent=2)
    )


if __name__ == "__main__":
    main()
