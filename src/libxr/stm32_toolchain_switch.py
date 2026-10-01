#!/usr/bin/env python3
"""libxr stm32 toolchain：切换 STM32 CMake 工程默认 preset 的工具链和 starm-clang 的标准库。
libxr stm32 toolchain: switches the toolchain of the default preset of an STM32 CMake
project and the standard library of starm-clang.

修改工程中的 CMakePresets.json 和 cmake/starm-clang.cmake。CMake 不会在已有的构建目录中更换
编译器（只警告 CMAKE_TOOLCHAIN_FILE 未被使用），所以工具链改变时删除用旧工具链配置过的 build/
和 cmake-build* 目录。
It edits CMakePresets.json and cmake/starm-clang.cmake of the project. CMake does not change the
compiler of an existing build directory (it only warns that CMAKE_TOOLCHAIN_FILE was not used),
so a changed toolchain removes the build/ and cmake-build* directories configured with the old
one.
"""

import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from xr_syntax.i18n import tr

CMAKE_PRESETS = "CMakePresets.json"
# 各编译器在 CubeMX 工程中的工具链文件。
# The toolchain file of each compiler in a CubeMX project.
TOOLCHAIN_FILES = {"gcc": "cmake/gcc-arm-none-eabi.cmake", "clang": "cmake/starm-clang.cmake"}

# 标准库选项到 STARM 配置的映射
# Mapping between standard library options and STARM configs
STD_MAP = {
    "hybrid": "STARM_HYBRID",
    "newlib": "STARM_NEWLIB",
    "picolibc": "STARM_PICOLIBC",
}

STARM_LINE = re.compile(r'(set\s*\(\s*STARM_TOOLCHAIN_CONFIG\s+")([^"]+)(".*\))')


def _fail(message: str) -> None:
    """记录错误并以状态 1 退出。
    Log an error and exit with status 1.
    """
    logging.error(message)
    sys.exit(1)


def _default_preset(presets: dict, path: str) -> dict:
    """presets 中名为 default 的 configure preset；没有时记录错误并以状态 1 退出。
    The configure preset named default in presets; without one, log an error and exit with
    status 1.
    """
    for preset in presets.get("configurePresets", []):
        if preset.get("name") == "default":
            return preset
    _fail(tr(f"No 'default' preset found in {path}", f"{path} 中没有 'default' preset"))


def _starm_line(lines: list[str], path: str) -> int:
    """lines 中第一条 set(STARM_TOOLCHAIN_CONFIG "...") 的行号；没有时记录错误并以状态 1 退出。
    The index of the first set(STARM_TOOLCHAIN_CONFIG "...") in lines; without one, log an error
    and exit with status 1.
    """
    for index, line in enumerate(lines):
        if STARM_LINE.search(line):
            return index
    _fail(
        tr(
            f"Could not find 'set(STARM_TOOLCHAIN_CONFIG ...)' in {path}",
            f"在 {path} 中找不到 'set(STARM_TOOLCHAIN_CONFIG ...)'",
        )
    )


def _read_presets(path: str) -> tuple[str, dict]:
    """读取 CMakePresets.json，返回 (原文, 解析结果)；原文保留换行符。读不了或不是有效的 JSON
    时记录错误（写出行、列）并以状态 1 退出。
    Read CMakePresets.json and return (text, parsed content), the text with its line endings
    kept. When it cannot be read or is not valid JSON, log an error, with the line and column,
    and exit with status 1.
    """
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        return text, json.loads(text)
    except UnicodeDecodeError as error:
        _fail(
            tr(
                f"{path} is not UTF-8 text (byte {error.start + 1}); save it as UTF-8",
                f"{path} 不是 UTF-8 编码（第 {error.start + 1} 个字节）；请以 UTF-8 保存",
            )
        )
    except json.JSONDecodeError as error:
        _fail(
            tr(
                f"{path} line {error.lineno}, column {error.colno}: {error.msg}",
                f"{path} 第 {error.lineno} 行第 {error.colno} 列：{error.msg}",
            )
        )


def _replace_toolchain_file(text: str, current: str | None, expected: str) -> str | None:
    """把原文中 default preset 的 toolchainFile 值 current 换成 expected，其余字符不变；值不存在
    或在原文中不止一处时为 None。
    Replace the toolchainFile value current of the default preset with expected in the text,
    leaving every other character as it is; None when the value is missing or appears more
    than once.
    """
    if current is None:
        return None
    pattern = re.compile(
        r'("toolchainFile"\s*:\s*)' + re.escape(json.dumps(current, ensure_ascii=False))
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    replacement = match.group(1) + json.dumps(expected, ensure_ascii=False)
    return text[: match.start()] + replacement + text[match.end() :]


def remove_build_dirs(directory: str) -> None:
    """删除 directory 下名为 build 或以 cmake-build 开头的子目录，逐个记录。
    Delete the subdirectories of directory named build or starting with cmake-build, logging
    each.
    """
    for entry in sorted(Path(directory).iterdir()):
        if entry.is_dir() and (entry.name == "build" or entry.name.startswith("cmake-build")):
            shutil.rmtree(entry)
            logging.info(
                tr(
                    f"Removed {entry}, configured with the previous toolchain",
                    f"已删除用旧工具链配置过的 {entry}",
                )
            )


def switch_toolchain(directory: str, compiler: str, std: str | None = None) -> None:
    """把 directory 中工程的默认 preset 切换到 compiler（gcc 或 clang），clang 时按 std 切换标准库。
    Switch the default preset of the project in directory to compiler (gcc or clang); for clang,
    switch the standard library to std.

    gcc 不接受 std；clang 不给 std 时沿用 starm-clang.cmake 中现在的标准库。修改任何文件之前先
    检查 CMakePresets.json（必须是有效的 JSON）、default preset、目标工具链文件以及（需要时）
    其中的 STARM_TOOLCHAIN_CONFIG 行，不满足时记录错误并以状态 1 退出。CMakePresets.json 中只
    替换 default preset 的 toolchainFile 值，其余内容不变。工具链改变时删除 build/ 和
    cmake-build* 目录。
    gcc takes no std; clang without std keeps the standard library currently in
    starm-clang.cmake. Before any file changes, CMakePresets.json, which must be valid JSON, its
    default preset, the target toolchain file and, when needed, its STARM_TOOLCHAIN_CONFIG line
    are checked; a failed check logs an error and exits with status 1. Only the toolchainFile
    value of the default preset is replaced in CMakePresets.json, the rest stays as it is. A
    changed toolchain removes the build/ and cmake-build* directories.
    """
    if compiler == "gcc" and std:
        _fail(
            tr(
                "Standard library option (-g/-n/-p) cannot be used with gcc.",
                "gcc 不能使用标准库选项（-g/-n/-p）。",
            )
        )
    presets_path = os.path.normpath(os.path.join(directory, CMAKE_PRESETS))
    toolchain = TOOLCHAIN_FILES[compiler]
    toolchain_path = os.path.normpath(os.path.join(directory, toolchain))
    if not os.path.isfile(presets_path):
        _fail(tr(f"{presets_path} not found.", f"找不到 {presets_path}。"))
    if not os.path.isfile(toolchain_path):
        _fail(tr(f"{toolchain_path} not found.", f"找不到 {toolchain_path}。"))
    text, presets = _read_presets(presets_path)
    preset = _default_preset(presets, presets_path)
    lines = []
    if compiler == "clang":
        with open(toolchain_path, encoding="utf-8", newline="") as f:
            lines = f.readlines()
        starm_index = _starm_line(lines, toolchain_path)

    # 检查都通过之后才写文件。
    # Files are written only after every check has passed.
    expected = "${sourceDir}/" + toolchain
    current = preset.get("toolchainFile")
    if current != expected:
        # 只替换这一个值，保留文件原有的缩进、字符和换行符；定位不到时才整体重写。
        # Only this value is replaced, keeping the indentation, characters and line endings
        # of the file; it is rewritten as a whole only when the value cannot be located.
        new_text = _replace_toolchain_file(text, current, expected)
        if new_text is None:
            preset["toolchainFile"] = expected
            new_text = json.dumps(presets, indent=4, ensure_ascii=False) + "\n"
        with open(presets_path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
        logging.info(
            tr(
                f"Switched the default preset to {toolchain}",
                f"已把 default preset 的工具链切换为 {toolchain}",
            )
        )
        remove_build_dirs(directory)
    else:
        logging.info(
            tr(
                f"Toolchain in default preset already set to {toolchain}",
                f"default preset 的工具链已经是 {toolchain}",
            )
        )

    if compiler == "clang":
        current = STARM_LINE.search(lines[starm_index]).group(2)
        target = STD_MAP[std] if std else current
        if target == current:
            logging.info(
                tr(
                    f'STARM_TOOLCHAIN_CONFIG stays "{current}".',
                    f'STARM_TOOLCHAIN_CONFIG 保持为 "{current}"。',
                )
            )
        else:
            lines[starm_index] = STARM_LINE.sub(rf"\g<1>{target}\g<3>", lines[starm_index])
            with open(toolchain_path, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            logging.info(
                tr(
                    f'Set STARM_TOOLCHAIN_CONFIG to "{target}" in {toolchain_path}',
                    f'已在 {toolchain_path} 中把 STARM_TOOLCHAIN_CONFIG 设为 "{target}"',
                )
            )
    logging.info(tr("Done.", "完成。"))


if __name__ == "__main__":
    from libxr.legacy import run

    raise SystemExit(run("xr_stm32_toolchain_switch"))
