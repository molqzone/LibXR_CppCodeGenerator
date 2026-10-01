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

from xr_syntax.i18n import localize_argparse, tr


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
    from libxr.output import configure_output
    from libxr.package_info import LibXRPackageInfo

    configure_output()
    LibXRPackageInfo.check_and_print()

    localize_argparse()
    parser = argparse.ArgumentParser(
        description=tr(
            "Run xr_parse_ioc on a specified directory.", "对指定目录运行 xr_parse_ioc。"
        )
    )
    parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help=tr("Input directory containing .ioc files", "包含 .ioc 文件的输入目录"),
    )
    args, extra_args = parser.parse_known_args()

    target_dir = os.path.abspath(args.directory)

    if not os.path.isdir(target_dir):
        logging.error(
            tr(
                f"Specified directory does not exist: {target_dir}",
                f"指定的目录不存在：{target_dir}",
            )
        )
        sys.exit(1)

    # 在指定目录中查找 .ioc 文件。
    # Search for .ioc files in the specified directory
    ioc_files = [f for f in os.listdir(target_dir) if f.endswith(".ioc")]
    if not ioc_files:
        logging.error(
            tr(
                f"No .ioc files found in directory: {target_dir}",
                f"目录中没有 .ioc 文件：{target_dir}",
            )
        )
        sys.exit(1)

    # 组成运行解析器的命令。
    # Construct the command to run the parser
    cmd = [
        sys.executable,
        "-m",
        "libxr.peripheral_analyzer_stm32",
        "-d",
        target_dir,
        *extra_args,  # 转发其余参数 / Forward other arguments
    ]

    logging.info(
        tr(
            f"Detected {len(ioc_files)} .ioc file(s) in '{target_dir}':",
            f"在 '{target_dir}' 中找到 {len(ioc_files)} 个 .ioc 文件：",
        )
    )
    for f in ioc_files:
        logging.info(f"       - {f}")
    logging.debug(f"CMD: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(
            tr(
                f"xr_parse_ioc exited with code {e.returncode}",
                f"xr_parse_ioc 以返回码 {e.returncode} 退出",
            )
        )
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
