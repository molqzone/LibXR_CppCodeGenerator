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

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def is_stm32_project(path: str) -> bool:
    """目录中有 .ioc 文件时为真；目录无法列出时记录错误并返回 False。
    True when the directory holds an .ioc file; a directory that cannot be listed logs an
    error and gives False.
    """
    try:
        return any(f.endswith(".ioc") for f in os.listdir(path))
    except Exception as e:
        logging.error(f"Cannot check directory '{path}': {e}")
        return False


def main():
    """命令行入口：检查输入文件，在 STM32 工程中以全部原参数运行 libxr.generator_code_stm32。
    Command-line entry: check the input file and, in an STM32 project, run
    libxr.generator_code_stm32 with all original arguments.

    输入文件不存在或生成器失败时以非零状态退出；不是 STM32 工程时以状态 0 退出。
    Exits with a non-zero status when the input file is missing or the generator fails, and
    with status 0 when the project is not an STM32 project.
    """
    from libxr.package_info import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(description="Wrapper for STM32 code generation.")
    parser.add_argument("-i", "--input", required=True, help="Input YAML configuration file path")

    # We don't parse all args because we want to forward unknown ones later
    known_args, unknown_args = parser.parse_known_args()

    input_path = os.path.abspath(known_args.input)
    input_dir = os.path.dirname(input_path)

    if not os.path.isfile(input_path):
        logging.error(f"YAML configuration file not found: {input_path}")
        sys.exit(1)

    if not is_stm32_project(input_dir):
        logging.info(
            "Skipped: This is not an STM32 project (no .ioc file found in input file directory)."
        )
        sys.exit(0)

    # Forward all original arguments (not just known) to the generator
    cmd: list[str] = [sys.executable, "-m", "libxr.generator_code_stm32", *sys.argv[1:]]

    logging.info("STM32 project detected (found .ioc file in input path).")
    logging.debug(f"CMD: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Code generation failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
