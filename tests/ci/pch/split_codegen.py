"""Split actual Verilator output into the production helper's tree inputs."""

from pathlib import Path
import shutil
import sys


def main():
    raw, cpp, hdr, policy, flags = map(Path, sys.argv[1:])
    cpp.mkdir(parents=True, exist_ok=True)
    hdr.mkdir(parents=True, exist_ok=True)
    for path in raw.iterdir():
        if path.suffix in (".cpp", ".cc", ".cxx"):
            shutil.copy2(path, cpp / path.name)
        elif path.suffix in (".h", ".hh", ".hpp"):
            shutil.copy2(path, hdr / path.name)
    pch = hdr / "Vtop__pch.h"
    if not pch.is_file():
        raise RuntimeError("Verilator did not generate its PCH header")
    pch.write_text('#include "fixture_policy.h"\n' + pch.read_text())
    shutil.copy2(policy, hdr / policy.name)
    shutil.copy2(flags, cpp / flags.name)


if __name__ == "__main__":
    main()
