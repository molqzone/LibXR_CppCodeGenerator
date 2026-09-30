#!/usr/bin/env python3
"""xr_parse：检查目录中的 .ioc 文件并转发给 xr_parse_ioc；目前只支持 STM32 工程。
xr_parse: checks a directory for .ioc files and forwards to xr_parse_ioc; only STM32 projects
are supported so far.
"""

import argparse
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def main():
    """xr_parse 命令行入口：检查包版本，确认 -d 目录存在且含 .ioc 文件，再以子进程运行
    libxr.peripheral_analyzer_stm32，其余参数原样转发。
    Command-line entry of xr_parse: check the package version, make sure the -d directory
    exists and holds .ioc files, then run libxr.peripheral_analyzer_stm32 in a subprocess with
    the remaining arguments passed through.

    目录不存在或没有 .ioc 文件时以状态 1 退出；子进程失败时以其返回码退出。
    A missing directory or one without .ioc files exits with status 1; a failed subprocess
    exits with its return code.
    """
    from libxr.package_info import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(description="Run xr_parse_ioc on a specified directory.")
    parser.add_argument(
        "-d", "--directory", required=True, help="Input directory containing .ioc files"
    )
    args, extra_args = parser.parse_known_args()

    target_dir = os.path.abspath(args.directory)

    if not os.path.isdir(target_dir):
        logging.error(f"Specified directory does not exist: {target_dir}")
        sys.exit(1)

    # Search for .ioc files in the specified directory
    ioc_files = [f for f in os.listdir(target_dir) if f.endswith(".ioc")]
    if not ioc_files:
        logging.error(f"No .ioc files found in directory: {target_dir}")
        sys.exit(1)

    # Construct the command to run the parser
    cmd = [
        sys.executable,
        "-m",
        "libxr.peripheral_analyzer_stm32",
        "-d",
        target_dir,
        *extra_args,  # Forward other arguments
    ]

    logging.info(f"Detected {len(ioc_files)} .ioc file(s) in '{target_dir}':")
    for f in ioc_files:
        logging.info(f"       - {f}")
    logging.debug(f"CMD: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"xr_parse_ioc exited with code {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
