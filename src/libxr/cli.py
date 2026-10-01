"""libxr 命令：为不同平台的工程生成基于 LibXR 的 C++ 代码。
The libxr command: generate LibXR-based C++ code for projects of different platforms.

parse 和 gen 按工程所属的平台选择解析器和生成器（PLATFORMS）；只属于一个平台的命令在平台名
之下，例如 libxr stm32 setup。旧的 xr_* 命令见 libxr.legacy。
parse and gen choose the parser and the generator by the platform of the project (PLATFORMS);
commands that belong to one platform sit under its name, such as libxr stm32 setup. The old
xr_* commands are in libxr.legacy.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from xr_syntax.i18n import localize_argparse, tr

from libxr import update_notice
from libxr.output import configure_output


@dataclass(frozen=True)
class Platform:
    """一个平台：名字、如何识别它的工程，以及它的解析器和生成器。
    A platform: its name, how its projects are recognized, and its parser and generator.
    """

    name: str
    # 识别方式的说明，用于无法识别平台时的报错。
    # How a project is recognized, for the error when no platform matches.
    project: Callable[[], str]
    # 目录是否属于这个平台的工程。
    # Whether a directory holds a project of this platform.
    detect: Callable[[str], bool]
    parse: Callable[[argparse.Namespace], None]
    gen: Callable[[argparse.Namespace], None]


def _has_ioc(directory: str) -> bool:
    """directory 中有 .ioc 文件（STM32CubeMX 工程）时为 True；目录无法列出时为 False。
    True when directory holds an .ioc file (an STM32CubeMX project); False when the directory
    cannot be listed.
    """
    try:
        return any(name.endswith(".ioc") for name in os.listdir(directory))
    except OSError:
        return False


def _stm32_parse(args: argparse.Namespace) -> None:
    """用 STM32 解析器运行 libxr parse。
    Run libxr parse with the STM32 parser.
    """
    from libxr.peripheral_analyzer_stm32 import parse_project

    parse_project(args.directory, args.output)


def _stm32_gen(args: argparse.Namespace) -> None:
    """用 STM32 生成器运行 libxr gen。
    Run libxr gen with the STM32 generator.
    """
    from libxr.generator_code_stm32 import generate

    generate(args.input, args.output, args.xrobot, args.libxr_config)


PLATFORMS = (
    Platform(
        "stm32",
        lambda: tr("a directory with an STM32CubeMX .ioc file", "含有 STM32CubeMX .ioc 文件的目录"),
        _has_ioc,
        _stm32_parse,
        _stm32_gen,
    ),
)


def platform_of(directory: str) -> Platform:
    """directory 中的工程所属的平台；无法识别时记录错误（列出支持的平台）并以状态 1 退出。
    The platform of the project in directory; when none matches, log an error that lists the
    supported platforms and exit with status 1.
    """
    for platform in PLATFORMS:
        if platform.detect(directory):
            return platform
    supported = tr("; ", "；").join(f"{p.name}: {p.project()}" for p in PLATFORMS)
    logging.error(
        tr(
            f"{directory}: no supported platform recognized ({supported})",
            f"{directory}：无法识别工程所属的平台（支持 {supported}）",
        )
    )
    sys.exit(1)


def cmd_parse(args: argparse.Namespace) -> None:
    """libxr parse：识别 -d 目录中工程的平台，解析工程并写出配置 YAML。
    libxr parse: recognize the platform of the project in the -d directory, parse the project
    and write the configuration YAML.
    """
    if args.verbose:
        configure_output(logging.DEBUG)
    if not os.path.isdir(args.directory):
        logging.error(
            tr(f"Directory does not exist: {args.directory}", f"目录不存在：{args.directory}")
        )
        sys.exit(1)
    platform_of(args.directory).parse(args)


def cmd_gen(args: argparse.Namespace) -> None:
    """libxr gen：按 -d 工程目录（默认当前目录）所属的平台，由配置 YAML 生成代码。
    libxr gen: generate code from the configuration YAML by the platform of the -d project
    directory, the current directory by default.
    """
    if args.verbose:
        configure_output(logging.DEBUG)
    if not os.path.isdir(args.directory):
        logging.error(
            tr(f"Directory does not exist: {args.directory}", f"目录不存在：{args.directory}")
        )
        sys.exit(1)
    if not os.path.isfile(args.input):
        logging.error(
            tr(
                f"YAML configuration file not found: {args.input}",
                f"找不到 YAML 配置文件：{args.input}",
            )
        )
        sys.exit(1)
    platform_of(args.directory).gen(args)


def cmd_stm32_setup(args: argparse.Namespace) -> None:
    """libxr stm32 setup：把 STM32CubeMX 工程配置为使用 LibXR 的工程。
    libxr stm32 setup: set up an STM32CubeMX project to use LibXR.
    """
    from libxr.config_cubemx_project import setup_project

    setup_project(
        args.directory,
        terminal_source=args.terminal,
        xrobot_enable=args.xrobot,
        commit=args.commit,
        git_source=args.git_source,
        git_mirrors=args.git_mirrors,
    )


def cmd_stm32_cubemx_gen(args: argparse.Namespace) -> None:
    """libxr stm32 cubemx-gen：以脚本模式运行 STM32CubeMX 生成工程；出错时记录错误并以状态 1 退出。
    libxr stm32 cubemx-gen: run STM32CubeMX in script mode to generate the project; an error is
    logged and exits with status 1.
    """
    from libxr.cubemx_generator import generate_cubemx_project

    try:
        generate_cubemx_project(
            project_dir=args.directory,
            ioc_file=args.ioc,
            cubemx_cmd=args.cubemx_cmd,
            java_cmd=args.java_cmd,
            launch_mode=args.launch_mode,
            generate_code_dir=args.generate_code_dir,
            expect_paths=args.expect_path,
            log_dir=args.log_dir,
            script_path=args.script_path,
            keep_script=args.keep_script,
            silent=args.silent,
            firmware=args.firmware,
            download=args.download,
            timeout=args.timeout,
        )
    except Exception as error:
        logging.error(error)
        sys.exit(1)


def cmd_stm32_cmake(args: argparse.Namespace) -> None:
    """libxr stm32 cmake：把 LibXR 接入 CubeMX 生成的 CMake 工程。
    libxr stm32 cmake: integrate LibXR into a CMake project generated by CubeMX.
    """
    from libxr.generator_stm32_cmake import integrate

    integrate(args.directory)


def cmd_stm32_flash_info(args: argparse.Namespace) -> None:
    """libxr stm32 flash-info：以 YAML 打印一个 STM32 型号的 Flash 布局。
    libxr stm32 flash-info: print the flash layout of an STM32 model as YAML.
    """
    from libxr.stm32_flash_generator import print_flash_info

    print_flash_info(args.model)


def cmd_stm32_toolchain(args: argparse.Namespace) -> None:
    """libxr stm32 toolchain：切换默认 preset 的工具链和 clang 的标准库。
    libxr stm32 toolchain: switch the toolchain of the default preset and the clang standard
    library.
    """
    from libxr.stm32_toolchain_switch import switch_toolchain

    switch_toolchain(args.directory, args.compiler, args.std)


def _command(group, name: str, text: str, run: Callable[[argparse.Namespace], None], **options):
    """在 group 中加入子命令 name，帮助和说明都是 text，运行 run。
    Add the subcommand name to group, with text as its help and description, running run.
    """
    parser = group.add_parser(name, help=text, description=text, **options)
    parser.set_defaults(run=run)
    return parser


def _add_parse(commands) -> None:
    """加入 parse 子命令。
    Add the parse subcommand.
    """
    parser = _command(
        commands,
        "parse",
        tr(
            "parse a project into the configuration YAML that gen reads",
            "解析工程，写出 gen 读取的配置 YAML",
        ),
        cmd_parse,
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help=tr(
            "project directory; an STM32CubeMX project holds one .ioc file (default: current "
            "directory)",
            "工程目录，STM32CubeMX 工程含有一个 .ioc 文件（默认：当前目录）",
        ),
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


def _add_gen(commands) -> None:
    """加入 gen 子命令。
    Add the gen subcommand.
    """
    parser = _command(
        commands,
        "gen",
        tr(
            "generate the LibXR code of a project from its configuration YAML",
            "由配置 YAML 生成工程的 LibXR 代码",
        ),
        cmd_gen,
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help=tr("configuration YAML written by parse", "parse 写出的配置 YAML"),
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help=tr(
            "project directory; its platform selects the generator (default: current directory)",
            "工程目录，按它所属的平台选择生成器（默认：当前目录）",
        ),
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
    parser.add_argument(
        "--verbose", action="store_true", help=tr("Enable debug logging", "输出调试日志")
    )


def _add_stm32_setup(commands) -> None:
    """加入 stm32 setup 子命令。
    Add the stm32 setup subcommand.
    """
    parser = _command(
        commands,
        "setup",
        tr(
            "set up an STM32CubeMX project for LibXR: add LibXR, then parse, generate and "
            "integrate CMake",
            "把 STM32CubeMX 工程配置为使用 LibXR：加入 LibXR，再解析、生成代码并接入 CMake",
        ),
        cmd_stm32_setup,
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help=tr(
            "STM32CubeMX project directory (default: current directory)",
            "STM32CubeMX 工程目录（默认：当前目录）",
        ),
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
    xrobot = parser.add_mutually_exclusive_group()
    xrobot.add_argument(
        "--xrobot",
        dest="xrobot",
        action="store_const",
        const=True,
        default=None,
        help=tr(
            "generate XRobot registrations (default: keep the project's current choice)",
            "生成 XRobot 注册代码（默认：沿用工程现在的选择）",
        ),
    )
    xrobot.add_argument(
        "--no-xrobot",
        dest="xrobot",
        action="store_const",
        const=False,
        help=tr("generate LibXR code without XRobot", "生成不含 XRobot 的 LibXR 代码"),
    )
    parser.add_argument(
        "--commit",
        default="",
        help=tr("Specify locked LibXR commit hash", "指定锁定的 LibXR 提交哈希"),
    )
    parser.add_argument(
        "--git-source",
        default="auto",
        help=tr(
            "where a missing LibXR is cloned from: 'auto', 'github', or a base or repository "
            "URL (default: auto); .gitmodules always records "
            + "https://github.com/xrobot-org/libxr.git",
            "缺少 LibXR 时从哪里克隆：'auto'、'github'，或基础地址、仓库地址（默认：auto）；"
            ".gitmodules 始终记录 " + "https://github.com/xrobot-org/libxr.git",
        ),
    )
    parser.add_argument(
        "--git-mirrors",
        default="",
        help=tr(
            "comma-separated mirror base or repository URLs, tried with --git-source auto",
            "以逗号分隔的镜像基础地址或仓库地址，--git-source 为 auto 时参与选择",
        ),
    )


def _add_stm32_cubemx_gen(commands) -> None:
    """加入 stm32 cubemx-gen 子命令。
    Add the stm32 cubemx-gen subcommand.
    """
    parser = _command(
        commands,
        "cubemx-gen",
        tr("Generate STM32CubeMX projects in script mode", "以脚本模式运行 STM32CubeMX 生成工程"),
        cmd_stm32_cubemx_gen,
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help=tr(
            "Directory containing the CubeMX .ioc file (default: current directory)",
            "含有 CubeMX .ioc 文件的目录（默认：当前目录）",
        ),
    )
    parser.add_argument(
        "--ioc",
        default="",
        help=tr(
            "Explicit .ioc file path (default: the only .ioc in --directory)",
            ".ioc 文件路径（默认：--directory 中唯一的 .ioc 文件）",
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
        help=tr(
            "CubeMX launch mode (default: auto: java -jar for a .jar or an installation with "
            "its jre, else direct)",
            "CubeMX 启动方式（默认 auto：.jar 或带 jre 的安装用 java -jar 启动，其余直接启动）",
        ),
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
        "--firmware",
        choices=("keep", "migrate"),
        default=None,
        help=tr(
            "answer when the project was saved by another STM32CubeMX version: keep its "
            "firmware package (Continue) or migrate the project (default: stop)",
            "工程由另一版本的 STM32CubeMX 保存时的回答：keep 沿用它的固件包（Continue），"
            "migrate 迁移工程（默认：停止）",
        ),
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help=tr(
            "let STM32CubeMX download a missing firmware package and accept its license "
            "(default: stop)",
            "允许 STM32CubeMX 下载缺少的固件包并接受其许可协议（默认：停止）",
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


def _add_stm32_cmake(commands) -> None:
    """加入 stm32 cmake 子命令。
    Add the stm32 cmake subcommand.
    """
    parser = _command(
        commands,
        "cmake",
        tr(
            "integrate LibXR into the CMake project generated by CubeMX",
            "把 LibXR 接入 CubeMX 生成的 CMake 工程",
        ),
        cmd_stm32_cmake,
    )
    _add_project_directory(parser)


def _add_project_directory(parser) -> None:
    """加入 -d/--directory：工程目录，默认当前目录。
    Add -d/--directory: the project directory, the current directory by default.
    """
    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help=tr(
            "CubeMX CMake project directory (default: current directory)",
            "CubeMX CMake 工程目录（默认：当前目录）",
        ),
    )


def _add_stm32_flash_info(commands) -> None:
    """加入 stm32 flash-info 子命令。
    Add the stm32 flash-info subcommand.
    """
    parser = _command(
        commands,
        "flash-info",
        tr(
            "print the internal flash layout of an STM32 model as YAML",
            "以 YAML 打印 STM32 型号的内部 Flash 布局",
        ),
        cmd_stm32_flash_info,
        epilog=tr("examples:", "示例：")
        + "\n  libxr stm32 flash-info STM32F103C8T6\n  libxr stm32 flash-info STM32L476RG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("model", help=tr("STM32 model", "STM32 型号"))


def _add_stm32_toolchain(commands) -> None:
    """加入 stm32 toolchain 子命令。
    Add the stm32 toolchain subcommand.
    """
    parser = _command(
        commands,
        "toolchain",
        tr(
            "switch the toolchain of the default preset and the clang standard library",
            "切换默认 preset 的工具链和 clang 的标准库",
        ),
        cmd_stm32_toolchain,
        epilog=tr(
            "A changed toolchain removes build/ and cmake-build*: CMake keeps an existing build "
            "directory on its old compiler.\n\n",
            "工具链改变时删除 build/ 和 cmake-build*：CMake 不会更换已有构建目录的编译器。\n\n",
        )
        + tr("examples:", "示例：")
        + "\n  libxr stm32 toolchain gcc\n  libxr stm32 toolchain clang -g"
        + "\n  libxr stm32 toolchain clang --newlib\n  libxr stm32 toolchain clang --picolibc"
        + "\n  libxr stm32 toolchain clang"
        + tr(
            "    (keeps the current standard library)",
            "    （沿用现在的标准库）",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_project_directory(parser)
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


def build_parser() -> argparse.ArgumentParser:
    """libxr 命令的参数解析器。
    The argument parser of the libxr command.
    """
    localize_argparse()
    parser = argparse.ArgumentParser(
        prog="libxr",
        description=tr(
            "Generate LibXR-based C++ code for projects of different platforms.",
            "为不同平台的工程生成基于 LibXR 的 C++ 代码。",
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"libxr {update_notice.installed_version() or tr('unknown', '未知')}",
        help=tr("show the version and exit", "显示版本号并退出"),
    )
    commands = parser.add_subparsers(metavar="<command>", required=True)
    _add_parse(commands)
    _add_gen(commands)
    stm32 = commands.add_parser(
        "stm32",
        help=tr("commands for STM32CubeMX projects", "STM32CubeMX 工程的命令"),
        description=tr("Commands for STM32CubeMX projects.", "STM32CubeMX 工程的命令。"),
    )
    stm32_commands = stm32.add_subparsers(metavar="<command>", required=True)
    _add_stm32_setup(stm32_commands)
    _add_stm32_cubemx_gen(stm32_commands)
    _add_stm32_cmake(stm32_commands)
    _add_stm32_flash_info(stm32_commands)
    _add_stm32_toolchain(stm32_commands)
    return parser


def run_command(run: Callable[[], None]) -> None:
    """运行一个命令 run；运行期间在后台检查新版本，结束时（失败也一样）提示。
    Run a command, run; a new version is checked in the background meanwhile and reported at
    the end, after a failure too.
    """
    report = update_notice.start()
    try:
        run()
    finally:
        report()


def main(argv: Sequence[str] | None = None) -> int:
    """libxr 命令入口：解析参数并运行子命令；运行期间在后台检查新版本，结束时提示。
    Entry of the libxr command: parse the arguments and run the subcommand; a new version is
    checked in the background meanwhile and reported at the end.
    """
    configure_output()
    args = build_parser().parse_args(argv)
    run_command(lambda: args.run(args))
    return 0
