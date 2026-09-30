#!/usr/bin/env python3
"""xr_gen_code 入口：输入 YAML 所在目录有 .ioc 文件时转交 STM32 生成器，否则跳过。
Entry point of xr_gen_code: the input goes to the STM32 generator when the directory of the
input YAML holds an .ioc file, and is skipped otherwise.
"""

import argparse
import logging
import os
import subprocess
import sys

from xr_syntax.i18n import localize_argparse, tr


def is_stm32_project(path: str) -> bool:
    """目录中有 .ioc 文件时为真；目录无法列出时记录错误并返回 False。
    True when the directory holds an .ioc file; a directory that cannot be listed logs an
    error and gives False.
    """
    try:
        return any(f.endswith(".ioc") for f in os.listdir(path))
    except Exception as e:
        logging.error(tr(f"Cannot check directory '{path}': {e}", f"无法检查目录 '{path}'：{e}"))
        return False


def main():
    """命令行入口：检查输入文件，在 STM32 工程中以全部原参数运行 libxr.generator_code_stm32。
    Command-line entry: check the input file and, in an STM32 project, run
    libxr.generator_code_stm32 with all original arguments.

    输入文件不存在或生成器失败时以非零状态退出；不是 STM32 工程时以状态 0 退出。
    Exits with a non-zero status when the input file is missing or the generator fails, and
    with status 0 when the project is not an STM32 project.
    """
    from libxr.output import configure_logging
    from libxr.package_info import LibXRPackageInfo

    configure_logging()
    LibXRPackageInfo.check_and_print()

    localize_argparse()
    parser = argparse.ArgumentParser(
        description=tr("Wrapper for STM32 code generation.", "STM32 代码生成的包装入口。")
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help=tr("Input YAML configuration file path", "输入的 YAML 配置文件路径"),
    )

    # We don't parse all args because we want to forward unknown ones later
    known_args, unknown_args = parser.parse_known_args()

    input_path = os.path.abspath(known_args.input)
    input_dir = os.path.dirname(input_path)

    if not os.path.isfile(input_path):
        logging.error(
            tr(
                f"YAML configuration file not found: {input_path}",
                f"找不到 YAML 配置文件：{input_path}",
            )
        )
        sys.exit(1)

    if not is_stm32_project(input_dir):
        logging.info(
            tr(
                "Skipped: This is not an STM32 project (no .ioc file found in input file "
                "directory).",
                "已跳过：不是 STM32 工程（输入文件所在目录中没有 .ioc 文件）。",
            )
        )
        sys.exit(0)

    # Forward all original arguments (not just known) to the generator
    cmd: list[str] = [sys.executable, "-m", "libxr.generator_code_stm32", *sys.argv[1:]]

    logging.info(
        tr(
            "STM32 project detected (found .ioc file in input path).",
            "检测到 STM32 工程（输入路径中有 .ioc 文件）。",
        )
    )
    logging.debug(f"CMD: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(
            tr(
                f"Code generation failed with exit code {e.returncode}",
                f"代码生成失败，退出码 {e.returncode}",
            )
        )
        sys.exit(e.returncode)
    except Exception as e:
        logging.error(tr(f"Unexpected error: {e}", f"意外错误：{e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
