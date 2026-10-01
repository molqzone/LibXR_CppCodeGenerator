#!/usr/bin/env python3
"""libxr stm32 toolchain：切换 STM32 CMake 工程默认 preset 的工具链和 starm-clang 的标准库。
libxr stm32 toolchain: switches the toolchain of the default preset of an STM32 CMake
project and the standard library of starm-clang.

在工程根目录运行，修改 CMakePresets.json 和 cmake/starm-clang.cmake。
Run in the project root; it edits CMakePresets.json and cmake/starm-clang.cmake.
"""

import json
import logging
import os
import re
import sys

from xr_syntax.i18n import tr

# 路径和常量
# Paths and constants
CMAKE_PRESETS_PATH = "CMakePresets.json"
CLANG_TOOLCHAIN = "cmake/starm-clang.cmake"
GCC_TOOLCHAIN = "cmake/gcc-arm-none-eabi.cmake"

# 命令行选项到 STARM 配置的映射
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
        logging.error(tr(f"{CMAKE_PRESETS_PATH} not found.", f"找不到 {CMAKE_PRESETS_PATH}。"))
        sys.exit(1)
    with open(CMAKE_PRESETS_PATH, encoding="utf-8") as f:
        presets = json.load(f)
    default_preset = None
    for preset in presets.get("configurePresets", []):
        if preset.get("name") == "default":
            default_preset = preset
            break
    if not default_preset:
        logging.error(
            tr(
                "No 'default' preset found in CMakePresets.json!",
                "CMakePresets.json 中没有 'default' preset！",
            )
        )
        sys.exit(1)
    if compiler == "gcc":
        new_toolchain = GCC_TOOLCHAIN
    elif compiler == "clang":
        new_toolchain = CLANG_TOOLCHAIN
    else:
        logging.error(tr(f"Unsupported compiler: {compiler}", f"不支持的编译器：{compiler}"))
        sys.exit(1)
    expected_value = "${sourceDir}/" + new_toolchain
    if default_preset.get("toolchainFile") != expected_value:
        logging.info(
            tr(
                f"Switching toolchain in default preset to {new_toolchain}",
                f"把 default preset 的工具链切换为 {new_toolchain}",
            )
        )
        default_preset["toolchainFile"] = expected_value
    else:
        logging.info(
            tr(
                f"Toolchain in default preset already set to {new_toolchain}",
                f"default preset 的工具链已经是 {new_toolchain}",
            )
        )
        return
    with open(CMAKE_PRESETS_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(presets, f, indent=4)
        f.write("\n")
    logging.info(tr("CMakePresets.json updated.", "已更新 CMakePresets.json。"))


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
        logging.error(tr(f"{cmake_file} not found.", f"找不到 {cmake_file}。"))
        sys.exit(1)
    with open(cmake_file, encoding="utf-8") as f:
        lines = f.readlines()
    pat = re.compile(r'(set\s*\(\s*STARM_TOOLCHAIN_CONFIG\s+")([^"]+)(".*\))')
    found = False
    for i, line in enumerate(lines):
        m = pat.search(line)
        if m:
            if m.group(2) == starm_config:
                logging.info(
                    tr(
                        f'STARM_TOOLCHAIN_CONFIG already set to "{starm_config}".',
                        f'STARM_TOOLCHAIN_CONFIG 已经是 "{starm_config}"。',
                    )
                )
                return
            else:
                lines[i] = pat.sub(rf"\1{starm_config}\3", line)
                found = True
                logging.info(
                    tr(
                        f'Set STARM_TOOLCHAIN_CONFIG to "{starm_config}" in {cmake_file}',
                        f'已在 {cmake_file} 中把 STARM_TOOLCHAIN_CONFIG 设为 "{starm_config}"',
                    )
                )
                break
    if not found:
        logging.error(
            tr(
                f"Could not find 'set(STARM_TOOLCHAIN_CONFIG ...)' in {cmake_file}",
                f"在 {cmake_file} 中找不到 'set(STARM_TOOLCHAIN_CONFIG ...)'",
            )
        )
        sys.exit(1)
    with open(cmake_file, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    logging.info(tr(f"{cmake_file} updated.", f"已更新 {cmake_file}。"))


def switch_toolchain(compiler: str, std: str | None = None) -> None:
    """把默认 preset 切换到 compiler（gcc 或 clang）；clang 同时按 std 切换标准库。
    Switch the default preset to compiler (gcc or clang); for clang, also switch the standard
    library to std.

    gcc 不接受标准库选项，clang 必须给出 std（hybrid、newlib 或 picolibc）；组合错误时记录错误并
    以状态 1 退出。
    gcc takes no standard library and clang needs std (hybrid, newlib or picolibc); a wrong
    combination logs an error and exits with status 1.
    """
    if compiler == "gcc":
        if std:
            logging.error(
                tr(
                    "Standard library option (-g/-n/-p) cannot be used with gcc!",
                    "gcc 不能使用标准库选项（-g/-n/-p）！",
                )
            )
            sys.exit(1)
        patch_cmakepresets("gcc")
    elif compiler == "clang":
        if not std:
            logging.error(
                tr(
                    "Standard library option required for clang: -g/--gnu/--hybrid, "
                    "-n/--newlib, -p/--picolibc",
                    "clang 需要标准库选项：-g/--gnu/--hybrid、-n/--newlib、-p/--picolibc",
                )
            )
            sys.exit(1)
        patch_cmakepresets("clang")
        patch_clang_stdlib(STD_MAP[std])
    logging.info(tr("Done.", "完成。"))


if __name__ == "__main__":
    from libxr.cli import legacy

    raise SystemExit(legacy("xr_stm32_toolchain_switch"))
