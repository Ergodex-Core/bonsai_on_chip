package(default_visibility = ["//visibility:public"])

py_library(
    name = "prepare_fixture",
    srcs = ["prepare_fixture.py"],
    imports = ["."],
    deps = ["//utils/weightstore:pack_image"],
)

py_test(
    name = "test_prepare_fixture",
    srcs = ["test_prepare_fixture.py"],
    deps = [":prepare_fixture"],
)

py_library(
    name = "run",
    srcs = ["run.py"],
    imports = ["."],
    deps = [":prepare_fixture"],
)

py_test(
    name = "test_run",
    srcs = ["test_run.py"],
    deps = [":run"],
)
