#!/usr/bin/env python3
"""对 src/libxr 中的每个顶层模块导入 libxr.<模块>，检查安装的 libxr 包能否全部导入。
Import libxr.<module> for every top-level module in src/libxr, checking that the installed
libxr package imports completely.
"""

import importlib
import sys
from pathlib import Path


def main() -> int:
    """逐个导入模块并列出结果；有模块导入失败时把错误写到标准错误并返回 1，否则返回 0。
    Import each module and list the results; return 1 with the errors on stderr when an import
    fails, else 0.
    """
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src" / "libxr"

    failures = []
    imported = []

    for path in sorted(src_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue

        module_name = f"libxr.{path.stem}"
        try:
            importlib.import_module(module_name)
            imported.append(module_name)
        except Exception as error:
            failures.append((module_name, error))

    print(f"Imported {len(imported)} modules.")
    for module_name in imported:
        print(f"OK  {module_name}")

    if failures:
        print(f"Failed to import {len(failures)} modules.", file=sys.stderr)
        for module_name, error in failures:
            print(f"ERR {module_name}: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
