"""Toolchain-matched Clang PCH compilation for generated Verilator C++."""

load("@bazel_tools//tools/build_defs/cc:action_names.bzl", "ACTION_NAMES")

_PCH_SCRIPT = """
set -eu
compiler="$1"
shift
# Clang stores paths below this physical root relatively in a relocatable PCH.
# Each consumer uses its own execroot (.), including after a disk-cache restore.
exec "$compiler" -x c++-header "$@" --relocatable-pch -isysroot "$(pwd -P)" \\
    -Xclang -fno-pch-timestamp
"""

def _compile(ctx, cc_toolchain, feature_configuration, cpp_dir, hdr_dir, compilation_contexts, flags, name, **kwargs):
    # Bazel chooses language-specific options before expanding source trees;
    # their extensionless parent otherwise loses --cxxopt options. The sentinel
    # is an ordinary .cc source and already receives those options from Bazel.
    flags = ctx.fragments.cpp.cxxopts + flags if cpp_dir.is_directory else flags
    return cc_common.compile(
        name = name,
        actions = ctx.actions,
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        srcs = [cpp_dir],
        public_hdrs = [hdr_dir],
        quote_includes = [hdr_dir.path],
        user_compile_flags = flags,
        compilation_contexts = compilation_contexts,
        **kwargs
    )

def compile_verilator_cpp(ctx, cc_toolchain, feature_configuration, cpp_dir, hdr_dir, compilation_contexts, user_compile_flags, pch_header = "Vtop__pch.h", use_pch = True):
    """Compile with the original object variants and an explicit PCH when supported.

    Args:
      ctx: Rule context with the cpp fragment.
      cc_toolchain: Selected C++ toolchain.
      feature_configuration: Configured C++ features.
      cpp_dir: Generated C++ tree artifact.
      hdr_dir: Generated header tree artifact, containing pch_header.
      compilation_contexts: Transitive C++ dependency contexts.
      user_compile_flags: Rule copts, shared by the PCH and consumers.
      pch_header: Name of the generated umbrella header.
      use_pch: False for the independent no-PCH regression baseline.

    Returns:
      The compilation context and compilation outputs, as cc_common.compile does.
    """

    # A custom SDK/sysroot cannot also serve as the relocatable execroot. Keep
    # those toolchains, and non-Clang compilers, on their existing compile path.
    all_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.cxxopts + user_compile_flags
    custom_sysroot = cc_toolchain.sysroot or any([
        flag.startswith("--sysroot") or flag.startswith("-isysroot")
        for flag in all_flags
    ])
    linux = ctx.target_platform_has_constraint(ctx.attr._linux[platform_common.ConstraintValueInfo])
    if not use_pch or not linux or cc_toolchain.compiler != "clang" or custom_sysroot or "verilator_pch" in ctx.disabled_features:
        return _compile(ctx, cc_toolchain, feature_configuration, cpp_dir, hdr_dir, compilation_contexts, user_compile_flags, ctx.label.name)

    # Bazel's public API does not expose its default PIC selection. Ask compile
    # for its exact outputs/context under an unused name. These analysis-only
    # objects are never returned or consumed, so their actions never execute.
    # Forcing both variants would double large UVM builds in fastbuild mode.
    sentinel = ctx.actions.declare_file(ctx.label.name + "_pch_selection.cc")
    ctx.actions.write(sentinel, "// Analysis-only probe of Bazel's object variants.\n")
    context, selection = _compile(ctx, cc_toolchain, feature_configuration, sentinel, hdr_dir, compilation_contexts, user_compile_flags, ctx.label.name + "_pch_selection")
    variants = []
    if selection.objects:
        variants.append(False)
    if selection.pic_objects:
        variants.append(True)

    outputs = []
    for pic in variants:
        pch = ctx.actions.declare_file(ctx.label.name + (".pic.pch" if pic else ".nopic.pch"))
        variables = cc_common.create_compile_variables(
            cc_toolchain = cc_toolchain,
            feature_configuration = feature_configuration,
            source_file = hdr_dir.path + "/" + pch_header,
            output_file = pch.path,
            user_compile_flags = all_flags,
            include_directories = context.includes,
            quote_include_directories = context.quote_includes,
            system_include_directories = context.system_includes,
            framework_include_directories = context.framework_includes,
            preprocessor_defines = depset(transitive = [context.defines, context.local_defines]),
            use_pic = pic,
        )
        ctx.actions.run_shell(
            outputs = [pch],
            inputs = depset([hdr_dir], transitive = [context.headers, cc_toolchain.all_files]),
            command = _PCH_SCRIPT,
            arguments = [cc_common.get_tool_for_action(
                feature_configuration = feature_configuration,
                action_name = ACTION_NAMES.cpp_compile,
            )] + cc_common.get_memory_inefficient_command_line(
                feature_configuration = feature_configuration,
                action_name = ACTION_NAMES.cpp_compile,
                variables = variables,
            ),
            env = cc_common.get_environment_variables(
                feature_configuration = feature_configuration,
                action_name = ACTION_NAMES.cpp_compile,
                variables = variables,
            ),
            execution_requirements = {
                requirement: ""
                for requirement in cc_common.get_execution_requirements(
                    feature_configuration = feature_configuration,
                    action_name = ACTION_NAMES.cpp_compile,
                )
            },
            mnemonic = "VerilatorPch",
            progress_message = "Precompiling Verilator headers (%s) for %s" % ("PIC" if pic else "non-PIC", ctx.label),
        )
        _, compiled = _compile(
            ctx,
            cc_toolchain,
            feature_configuration,
            cpp_dir,
            hdr_dir,
            compilation_contexts,
            user_compile_flags + ["-isysroot", ".", "-include-pch", pch.path],
            ctx.label.name,
            disallow_pic_outputs = not pic,
            disallow_nopic_outputs = pic,
            additional_inputs = [pch],
        )
        outputs.append(compiled)

    return context, cc_common.merge_compilation_outputs(compilation_outputs = outputs)
