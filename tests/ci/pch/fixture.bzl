"""Small real Verilator model using the production generated-C++ PCH helper."""

load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load("//rules:verilator_pch.bzl", "compile_verilator_cpp")

_CODEGEN = """
set -euo pipefail
raw="$(mktemp -d)"
trap 'rm -rf "$raw"' EXIT
VERILATOR_ROOT="$3" "$4" --cc --vpi --timing --output-groups 0 \\
    --prefix Vtop --top-module pch_fixture -Mdir "$raw" "$5"
python3 "$6" "$raw" "$1" "$2" "$7" "$8"
"""

def _pch_fixture_library_impl(ctx):
    cc_toolchain = find_cc_toolchain(ctx)
    features = cc_common.configure_features(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        requested_features = ctx.features,
        unsupported_features = ctx.disabled_features,
    )
    cpp_dir = ctx.actions.declare_directory(ctx.label.name + "_cpp")
    hdr_dir = ctx.actions.declare_directory(ctx.label.name + "_h")
    executable = ctx.executable._verilator_bin
    ctx.actions.run_shell(
        outputs = [cpp_dir, hdr_dir],
        tools = [ctx.attr._verilator_bin[DefaultInfo].files_to_run],
        inputs = [
            ctx.file.src,
            ctx.file._split_codegen,
            ctx.file._policy_header,
            ctx.file._policy_source,
        ],
        command = _CODEGEN,
        arguments = [
            cpp_dir.path,
            hdr_dir.path,
            executable.path + ".runfiles/" + executable.owner.workspace_name,
            executable.path,
            ctx.file.src.path,
            ctx.file._split_codegen.path,
            ctx.file._policy_header.path,
            ctx.file._policy_source.path,
        ],
        mnemonic = "VerilatorPchFixtureCodegen",
        progress_message = "Verilating small PCH regression model",
    )
    deps = ctx.attr.deps + [ctx.attr._verilator_runtime]
    compilation_context, compilation_outputs = compile_verilator_cpp(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        feature_configuration = features,
        cpp_dir = cpp_dir,
        hdr_dir = hdr_dir,
        compilation_contexts = [dep[CcInfo].compilation_context for dep in deps],
        user_compile_flags = [
            "-O2",
            "-std=c++20",
            "-DVERILATOR=1",
            "-DVL_TIME_CONTEXT",
            "-DVM_TIMING=1",
            "-DVM_VPI=1",
            "-DVM_COVERAGE=0",
            "-DVM_SC=0",
            "-DVM_TRACE=0",
            "-DVM_TRACE_FST=0",
            "-DVM_TRACE_VCD=0",
            "-DVM_TRACE_SAIF=0",
            "-faligned-new",
            "-DPCH_FIXTURE_BIAS=37",
        ],
        use_pch = ctx.attr.use_pch,
    )
    linking_context, linking_outputs = cc_common.create_linking_context_from_compilation_outputs(
        actions = ctx.actions,
        feature_configuration = features,
        cc_toolchain = cc_toolchain,
        compilation_outputs = compilation_outputs,
        linking_contexts = [dep[CcInfo].linking_context for dep in deps],
        name = ctx.label.name,
        alwayslink = True,
        disallow_dynamic_library = True,
    )
    libraries = []
    for library in [linking_outputs.library_to_link.static_library, linking_outputs.library_to_link.pic_static_library]:
        if library != None:
            libraries.append(library)
    return [
        DefaultInfo(files = depset(libraries)),
        CcInfo(
            compilation_context = compilation_context,
            linking_context = linking_context,
        ),
    ]

pch_fixture_library = rule(
    implementation = _pch_fixture_library_impl,
    attrs = {
        "src": attr.label(allow_single_file = [".sv"], mandatory = True),
        "deps": attr.label_list(providers = [CcInfo]),
        "use_pch": attr.bool(default = True),
        "_split_codegen": attr.label(default = ":split_codegen.py", allow_single_file = True),
        "_policy_header": attr.label(default = ":fixture_policy.h", allow_single_file = True),
        "_policy_source": attr.label(default = ":fixture_flags.cpp", allow_single_file = True),
        "_verilator_bin": attr.label(default = "@verilator//:verilator_bin", executable = True, cfg = "exec"),
        "_verilator_runtime": attr.label(default = "@verilator//:verilator_runtime"),
        "_cc_toolchain": attr.label(default = "@bazel_tools//tools/cpp:current_cc_toolchain"),
        "_linux": attr.label(default = "@platforms//os:linux"),
    },
    fragments = ["cpp"],
    toolchains = ["@bazel_tools//tools/cpp:toolchain_type"],
)
