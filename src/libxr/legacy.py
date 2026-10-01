"""6.0.0 之前的 xr_* 命令：参数及其含义保持原样，运行时提示对应的 libxr 子命令；7.0.0 删除。
The xr_* commands of versions before 6.0.0: their arguments keep their old meaning, and they
name their libxr subcommand when they run; they are removed in 7.0.0.

每个旧命令用原来的参数解析器读取参数，再调用新命令的实现。
Each old command reads its arguments with its original parser, then calls the implementation of
the new command.
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from xr_syntax.i18n import localize_argparse, tr

from libxr import cli
from libxr.output import configure_output


def _parser(prog: str, description: str, **options) -> argparse.ArgumentParser:
    """旧命令 prog 的参数解析器。
    The argument parser of the old command prog.
    """
    return argparse.ArgumentParser(prog=prog, description=description, **options)


def _parse(prog: str, argv: list[str]) -> None:
    """xr_parse、xr_parse_ioc：-d 必须给出。
    xr_parse, xr_parse_ioc: -d is required.
    """
    parser = _parser(
        prog, tr("STM32CubeMX IOC Configuration Parser", "STM32CubeMX .ioc 配置解析器")
    )
    parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help=tr("Input directory containing .ioc files", "包含 .ioc 文件的输入目录"),
    )
    parser.add_argument(
        "-o",
        "--output",
        help=tr(
            "output YAML file (default: .config.yaml in DIRECTORY)",
            "输出的 YAML 文件（默认：DIRECTORY 中的 .config.yaml）",
        ),
    )
    parser.add_argument(
        "--verbose", action="store_true", help=tr("Enable debug logging", "输出调试日志")
    )
    args = parser.parse_args(argv)
    if args.verbose:
        configure_output(logging.DEBUG)
    cli.cmd_parse(args)


def _gen_arguments(prog: str, argv: list[str]) -> argparse.Namespace:
    """xr_gen_code、xr_gen_code_stm32 的参数：没有 -d。
    The arguments of xr_gen_code and xr_gen_code_stm32: there is no -d.
    """
    parser = _parser(
        prog,
        tr("Generate STM32 Peripheral Initialization Code", "生成 STM32 外设初始化代码"),
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help=tr("Input YAML configuration file path", "输入的 YAML 配置文件路径"),
    )
    parser.add_argument(
        "-o", "--output", required=True, help=tr("Output C++ file path", "输出的 C++ 文件路径")
    )
    parser.add_argument(
        "--xrobot",
        action="store_true",
        help=tr("Enable XRobot framework integration", "启用 XRobot 框架集成"),
    )
    parser.add_argument(
        "--libxr-config",
        default="",
        help=tr(
            "Optional path or URL to libxr_config.yaml",
            "libxr_config.yaml 的路径或 URL（可选）",
        ),
    )
    return parser.parse_args(argv)


def _gen_code(prog: str, argv: list[str]) -> None:
    """xr_gen_code：按输入 YAML 所在目录识别平台；不是支持的工程时跳过，以状态 0 结束。
    xr_gen_code: the platform comes from the directory of the input YAML; a directory without a
    supported project is skipped with status 0.
    """
    args = _gen_arguments(prog, argv)
    if not os.path.isfile(args.input):
        logging.error(
            tr(
                f"YAML configuration file not found: {os.path.abspath(args.input)}",
                f"找不到 YAML 配置文件：{os.path.abspath(args.input)}",
            )
        )
        sys.exit(1)
    directory = os.path.dirname(os.path.abspath(args.input))
    platform = next((p for p in cli.PLATFORMS if p.detect(directory)), None)
    if platform is None:
        logging.info(
            tr(
                "Skipped: the directory of the input file holds no supported project.",
                "已跳过：输入文件所在目录中没有支持的工程。",
            )
        )
        return
    platform.gen(args)


def _gen_code_stm32(prog: str, argv: list[str]) -> None:
    """xr_gen_code_stm32：总是使用 STM32 生成器。
    xr_gen_code_stm32: always the STM32 generator.
    """
    args = _gen_arguments(prog, argv)
    next(p for p in cli.PLATFORMS if p.name == "stm32").gen(args)


def _cubemx_cfg(prog: str, argv: list[str]) -> None:
    """xr_cubemx_cfg：-d 必须给出；没有 --xrobot 时不使用 XRobot。
    xr_cubemx_cfg: -d is required; without --xrobot, XRobot is not used.
    """
    parser = _parser(prog, tr("Automate STM32CubeMX project setup", "自动配置 STM32CubeMX 工程"))
    parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help=tr("STM32CubeMX project directory", "STM32CubeMX 工程目录"),
    )
    parser.add_argument(
        "-t",
        "--terminal",
        default="",
        help=tr(
            "Terminal device (e.g. usart1, usb_fs_cdc); stored as "
            "terminal_source in User/libxr_config.yaml",
            "终端设备（例如 usart1、usb_fs_cdc），记录为 User/libxr_config.yaml 中的 "
            "terminal_source",
        ),
    )
    parser.add_argument("--xrobot", action="store_true", help=tr("Support XRobot", "支持 XRobot"))
    parser.add_argument(
        "--commit",
        default="",
        help=tr("Specify locked LibXR commit hash", "指定锁定的 LibXR 提交哈希"),
    )
    parser.add_argument(
        "--git-source",
        default="auto",
        help=tr(
            "Git source base URL or full repo URL, or 'auto'/'github' (default: auto)",
            "Git 源的基础地址或完整仓库地址，或 'auto'/'github'（默认：auto）",
        ),
    )
    parser.add_argument(
        "--git-mirrors",
        default="",
        help=tr(
            "Comma-separated mirror base/repo URLs (will be tried when --git-source=auto)",
            "以逗号分隔的镜像基础地址或仓库地址（--git-source=auto 时参与选择）",
        ),
    )
    cli.cmd_stm32_setup(parser.parse_args(argv))


def _cubemx_generate(prog: str, argv: list[str]) -> None:
    """xr_cubemx_generate：-d 必须给出；没有 --ioc 时用按文件名排序的第一个 .ioc；
    --auto-confirm 确认迁移、下载和许可协议（即 --firmware migrate --download）。
    xr_cubemx_generate: -d is required; without --ioc, the first .ioc by file name is used;
    --auto-confirm confirms migration, downloads and licenses (--firmware migrate --download).
    """
    parser = _parser(
        prog,
        tr("Generate STM32CubeMX projects in script mode", "以脚本模式运行 STM32CubeMX 生成工程"),
    )
    parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help=tr("Directory containing the CubeMX .ioc file", "含有 CubeMX .ioc 文件的目录"),
    )
    parser.add_argument(
        "--ioc",
        default="",
        help=tr(
            "Explicit .ioc file path (defaults to the first .ioc in --directory)",
            ".ioc 文件路径（默认：--directory 中的第一个 .ioc 文件）",
        ),
    )
    parser.add_argument(
        "--cubemx-cmd",
        default="",
        help=tr("STM32CubeMX executable path", "STM32CubeMX 可执行文件路径"),
    )
    parser.add_argument(
        "--java-cmd",
        default="",
        help=tr(
            "Java executable path for -jar launch mode",
            "java -jar 启动方式使用的 Java 可执行文件路径",
        ),
    )
    parser.add_argument(
        "--launch-mode",
        choices=("auto", "direct", "java"),
        default="auto",
        help=tr("CubeMX launch mode (default: auto)", "CubeMX 启动方式（默认：auto）"),
    )
    parser.add_argument(
        "--generate-code-dir",
        default="",
        help=tr(
            "Use 'generate code <dir>' instead of 'project generate'",
            "用 'generate code <dir>' 代替 'project generate'",
        ),
    )
    parser.add_argument(
        "--expect-path",
        action="append",
        default=None,
        help=tr(
            "Path that must exist after generation (default: Core/Inc and Drivers)",
            "生成后必须存在的路径（默认：Core/Inc 和 Drivers）",
        ),
    )
    parser.add_argument(
        "--log-dir",
        default="",
        help=tr(
            "Optional directory for command/script/stdout/stderr logs",
            "存放命令、脚本、标准输出和标准错误日志的目录（可选）",
        ),
    )
    parser.add_argument(
        "--script-path",
        default="",
        help=tr(
            "Optional path for the generated CubeMX script file",
            "生成的 CubeMX 脚本文件的路径（可选）",
        ),
    )
    parser.add_argument(
        "--keep-script",
        action="store_true",
        help=tr(
            "Keep the generated CubeMX script in the project directory",
            "在工程目录中保留生成的 CubeMX 脚本",
        ),
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help=tr("Pass -s to STM32CubeMX", "向 STM32CubeMX 传入 -s"),
    )
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help=tr(
            "Confirm migration, download and license dialogs "
            "(libxr stm32 cubemx-gen --firmware migrate --download)",
            "确认迁移、下载和许可协议对话框（即 libxr stm32 cubemx-gen --firmware migrate "
            "--download）",
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help=tr(
            "CubeMX process timeout in seconds (default: 1200)",
            "CubeMX 进程的超时时间，单位为秒（默认：1200）",
        ),
    )
    args = parser.parse_args(argv)
    args.firmware = "migrate" if args.auto_confirm else None
    args.download = args.auto_confirm
    if not args.ioc and os.path.isdir(args.directory):
        ioc_files = sorted(name for name in os.listdir(args.directory) if name.endswith(".ioc"))
        if ioc_files:
            args.ioc = os.path.join(args.directory, ioc_files[0])
    cli.cmd_stm32_cubemx_gen(args)


def _stm32_cmake(prog: str, argv: list[str]) -> None:
    """xr_stm32_cmake：工程目录是必须给出的位置参数。
    xr_stm32_cmake: the project directory is a required positional argument.
    """
    parser = _parser(prog, tr("Generate CMake file for LibXR.", "为 LibXR 生成 CMake 文件。"))
    parser.add_argument(
        "input_dir", help=tr("CubeMX CMake Project Directory", "CubeMX CMake 工程目录")
    )
    cli.cmd_stm32_cmake(argparse.Namespace(directory=parser.parse_args(argv).input_dir))


def _stm32_flash(prog: str, argv: list[str]) -> None:
    """xr_stm32_flash：恰好一个型号参数；个数不对时打印用法并以状态 1 结束，型号无法处理时以
    状态 2 结束。
    xr_stm32_flash: exactly one model argument; a wrong count prints the usage and exits with
    status 1, and a model that cannot be processed exits with status 2.
    """
    from libxr.stm32_flash_generator import print_flash_info

    usage = "\n".join(
        [
            tr("STM32 Flash Information Tool", "STM32 Flash 信息工具"),
            tr("Usage:", "用法："),
            f"  {prog} <STM32_MODEL>",
            tr("\nExamples:", "\n示例："),
            f"  {prog} STM32F103C8T6",
            f"  {prog} STM32L476RG",
        ]
    )
    if argv in (["-h"], ["--help"]):
        print(usage)
        return
    if len(argv) != 1:
        print(usage, file=sys.stderr)
        sys.exit(1)
    print_flash_info(argv[0], error_status=2)


def _stm32_toolchain_switch(prog: str, argv: list[str]) -> None:
    """xr_stm32_toolchain_switch：作用于当前目录，没有 -d。
    xr_stm32_toolchain_switch: works on the current directory; there is no -d.
    """
    examples = (
        f"  {prog} gcc\n  {prog} clang -g\n  {prog} clang --newlib\n  {prog} clang --picolibc"
    )
    parser = _parser(
        prog,
        tr(
            "Switch STM32 toolchain and clang standard library.\nUsage examples:\n" + examples,
            "切换 STM32 工具链和 clang 标准库。\n用法示例：\n" + examples,
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "compiler",
        choices=["gcc", "clang"],
        help=tr("Compiler (gcc or clang)", "编译器（gcc 或 clang）"),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-g",
        "--gnu",
        "--hybrid",
        dest="std",
        action="store_const",
        const="hybrid",
        help=tr("Use GNU(Hybrid) standard library", "使用 GNU（Hybrid）标准库"),
    )
    group.add_argument(
        "-n",
        "--newlib",
        dest="std",
        action="store_const",
        const="newlib",
        help=tr("Use newlib standard library", "使用 newlib 标准库"),
    )
    group.add_argument(
        "-p",
        "--picolibc",
        dest="std",
        action="store_const",
        const="picolibc",
        help=tr("Use picolibc standard library", "使用 picolibc 标准库"),
    )
    args = parser.parse_args(argv)
    args.directory = "."
    cli.cmd_stm32_toolchain(args)


@dataclass(frozen=True)
class Legacy:
    """一个旧命令：对应的新命令，以及以旧参数运行它的函数 run(prog, argv)。
    An old command: its new command, and run(prog, argv), which runs it with the old arguments.
    """

    new: str
    run: Callable[[str, list[str]], None]


COMMANDS = {
    "xr_parse": Legacy("libxr parse", _parse),
    "xr_parse_ioc": Legacy("libxr parse", _parse),
    "xr_gen_code": Legacy("libxr gen", _gen_code),
    "xr_gen_code_stm32": Legacy("libxr gen", _gen_code_stm32),
    "xr_cubemx_cfg": Legacy("libxr stm32 setup", _cubemx_cfg),
    "xr_cubemx_generate": Legacy("libxr stm32 cubemx-gen", _cubemx_generate),
    "xr_stm32_cmake": Legacy("libxr stm32 cmake", _stm32_cmake),
    "xr_stm32_flash": Legacy("libxr stm32 flash-info", _stm32_flash),
    "xr_stm32_toolchain_switch": Legacy("libxr stm32 toolchain", _stm32_toolchain_switch),
}


def run(old: str, argv: Sequence[str] | None = None) -> int:
    """运行旧命令 old：警告它已改名，再以它原来的参数运行；运行期间在后台检查新版本。
    Run the old command old: warn that it was renamed, then run it with its original arguments;
    a new version is checked in the background meanwhile.
    """
    configure_output()
    localize_argparse()
    command = COMMANDS[old]
    logging.warning(
        tr(
            f"{old} is now `{command.new}`; the old name is removed in libxr 7.0.0",
            f"{old} 已改为 `{command.new}`；旧命令将在 libxr 7.0.0 删除",
        )
    )
    args = list(sys.argv[1:] if argv is None else argv)
    cli.run_command(lambda: command.run(old, args))
    return 0


# 旧命令的 console_scripts 入口。
# console_scripts entry points of the old commands.
xr_parse = functools.partial(run, "xr_parse")
xr_parse_ioc = functools.partial(run, "xr_parse_ioc")
xr_gen_code = functools.partial(run, "xr_gen_code")
xr_gen_code_stm32 = functools.partial(run, "xr_gen_code_stm32")
xr_cubemx_cfg = functools.partial(run, "xr_cubemx_cfg")
xr_cubemx_generate = functools.partial(run, "xr_cubemx_generate")
xr_stm32_cmake = functools.partial(run, "xr_stm32_cmake")
xr_stm32_flash = functools.partial(run, "xr_stm32_flash")
xr_stm32_toolchain_switch = functools.partial(run, "xr_stm32_toolchain_switch")
