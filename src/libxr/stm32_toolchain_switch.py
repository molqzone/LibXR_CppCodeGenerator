#!/usr/bin/env python3
"""xr_stm32_toolchain_switch：切换 STM32 CMake 工程默认 preset 的工具链和 starm-clang 的标准库。
xr_stm32_toolchain_switch: switches the toolchain of the default preset of an STM32 CMake
project and the standard library of starm-clang.

在工程根目录运行，修改 CMakePresets.json 和 cmake/starm-clang.cmake。
Run in the project root; it edits CMakePresets.json and cmake/starm-clang.cmake.
"""

import argparse
import json
import logging
import os
import re
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# Paths and constants
CMAKE_PRESETS_PATH = "CMakePresets.json"
CLANG_TOOLCHAIN = "cmake/starm-clang.cmake"
GCC_TOOLCHAIN = "cmake/gcc-arm-none-eabi.cmake"

# Mapping between command-line options and STARM configs
STD_MAP = {
    "g": "STARM_HYBRID",
    "gnu": "STARM_HYBRID",
    "hybrid": "STARM_HYBRID",
    "n": "STARM_NEWLIB",
    "newlib": "STARM_NEWLIB",
    "p": "STARM_PICOLIBC",
    "picolibc": "STARM_PICOLIBC",
}


def patch_cmakepresets(compiler):
    """把 CMakePresets.json 中名为 default 的 configure preset 的 toolchainFile 设为 compiler
    对应的工具链文件，并写回文件。
    Set toolchainFile of the configure preset named default in CMakePresets.json to the
    toolchain file of compiler, and write the file back.

    文件不存在、没有 default preset 或 compiler 不是 gcc/clang 时记录错误并以状态 1 退出。
    A missing file, a missing default preset or a compiler other than gcc/clang logs an error
    and exits with status 1.
    """
    if not os.path.exists(CMAKE_PRESETS_PATH):
        logging.error(f"{CMAKE_PRESETS_PATH} not found.")
        sys.exit(1)
    with open(CMAKE_PRESETS_PATH, encoding="utf-8") as f:
        presets = json.load(f)
    default_preset = None
    for preset in presets.get("configurePresets", []):
        if preset.get("name") == "default":
            default_preset = preset
            break
    if not default_preset:
        logging.error("No 'default' preset found in CMakePresets.json!")
        sys.exit(1)
    if compiler == "gcc":
        new_toolchain = GCC_TOOLCHAIN
    elif compiler == "clang":
        new_toolchain = CLANG_TOOLCHAIN
    else:
        logging.error(f"Unsupported compiler: {compiler}")
        sys.exit(1)
    expected_value = "${sourceDir}/" + new_toolchain
    if default_preset.get("toolchainFile") != expected_value:
        logging.info(f"Switching toolchain in default preset to {new_toolchain}")
        default_preset["toolchainFile"] = expected_value
    else:
        logging.info(f"Toolchain in default preset already set to {new_toolchain}")
    with open(CMAKE_PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=4)
        f.write("\n")
    logging.info("CMakePresets.json updated.")


def patch_clang_stdlib(starm_config):
    """把 cmake/starm-clang.cmake 中第一条 set(STARM_TOOLCHAIN_CONFIG "...") 的值改为
    starm_config，并以 LF 换行写回文件。
    Set the value of the first set(STARM_TOOLCHAIN_CONFIG "...") in cmake/starm-clang.cmake
    to starm_config, and write the file back with LF line endings.

    文件不存在或找不到这条 set 语句时记录错误并以状态 1 退出。
    A missing file or a missing set statement logs an error and exits with status 1.
    """
    cmake_file = CLANG_TOOLCHAIN
    if not os.path.exists(cmake_file):
        logging.error(f"{cmake_file} not found.")
        sys.exit(1)
    with open(cmake_file, encoding="utf-8") as f:
        lines = f.readlines()
    pat = re.compile(r'(set\s*\(\s*STARM_TOOLCHAIN_CONFIG\s+")([^"]+)(".*\))')
    found = False
    for i, line in enumerate(lines):
        m = pat.search(line)
        if m:
            if m.group(2) == starm_config:
                logging.info(f'STARM_TOOLCHAIN_CONFIG already set to "{starm_config}".')
                found = True
                break
            else:
                lines[i] = pat.sub(rf"\1{starm_config}\3", line)
                found = True
                logging.info(f'Set STARM_TOOLCHAIN_CONFIG to "{starm_config}" in {cmake_file}')
                break
    if not found:
        logging.error(f"Could not find 'set(STARM_TOOLCHAIN_CONFIG ...)' in {cmake_file}")
        sys.exit(1)
    with open(cmake_file, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    logging.info(f"{cmake_file} updated.")


def main():
    """xr_stm32_toolchain_switch 命令行入口。
    Command-line entry of xr_stm32_toolchain_switch.

    gcc 不接受标准库选项，只切换工具链；clang 必须带 -g、-n、-p 之一，同时切换工具链和
    STARM_TOOLCHAIN_CONFIG。选项组合错误时打印用法并以状态 1 退出。
    gcc takes no standard library option and only switches the toolchain; clang needs one of
    -g, -n and -p and switches both the toolchain and STARM_TOOLCHAIN_CONFIG. A wrong option
    combination prints the usage and exits with status 1.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Switch STM32 toolchain and clang standard library.\n"
            "Usage examples:\n"
            "  python switch_toolchain.py gcc\n"
            "  python switch_toolchain.py clang -g\n"
            "  python switch_toolchain.py clang --newlib\n"
            "  python switch_toolchain.py clang --picolibc"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("compiler", choices=["gcc", "clang"], help="Compiler (gcc or clang)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-g",
        "--gnu",
        "--hybrid",
        dest="std",
        action="store_const",
        const="hybrid",
        help="Use GNU(Hybrid) standard library",
    )
    group.add_argument(
        "-n",
        "--newlib",
        dest="std",
        action="store_const",
        const="newlib",
        help="Use newlib standard library",
    )
    group.add_argument(
        "-p",
        "--picolibc",
        dest="std",
        action="store_const",
        const="picolibc",
        help="Use picolibc standard library",
    )
    args = parser.parse_args()
    compiler = args.compiler
    if compiler == "gcc":
        if args.std:
            logging.error("Standard library option (-g/-n/-p) cannot be used with gcc!")
            parser.print_usage()
            sys.exit(1)
        patch_cmakepresets("gcc")
    elif compiler == "clang":
        if not args.std:
            logging.error(
                "Standard library option required for clang: -g/--gnu/--hybrid, -n/--newlib, -p/--picolibc"
            )
            parser.print_usage()
            sys.exit(1)
        starm_config = STD_MAP[args.std]
        patch_cmakepresets("clang")
        patch_clang_stdlib(starm_config)
    logging.info("Done.")


if __name__ == "__main__":
    main()
