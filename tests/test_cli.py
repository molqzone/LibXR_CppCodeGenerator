"""libxr 命令（libxr.cli）：平台识别和各子命令共有的选项。
The libxr command (libxr.cli): platform recognition and the options the subcommands share.
"""

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import IOC, TestCase, run_libxr

from libxr import cli, update_notice

REPOSITORY = Path(__file__).resolve().parents[1]


class Platforms(TestCase):
    """parse 按工程目录识别平台并记录在 YAML 中，gen 按记录的平台选择生成器；无法识别时报错并
    列出支持的平台。
    parse recognizes the platform by the project directory and records it in the YAML, and gen
    selects the generator by the recorded platform; when none matches, they fail with the
    supported platforms.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_an_unrecognized_project_is_an_error(self):
        with self.assertLogs(level="ERROR") as logs:
            code, _, _ = run_libxr("parse", "-d", str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(
            logs.output,
            [
                f"ERROR:root:{self.root}: no supported platform recognized "
                "(stm32: a directory with an STM32CubeMX .ioc file)"
            ],
        )

    def parsed_elsewhere(self):
        """解析 root/project 中的工程，把配置 YAML 写到工程之外，当前目录也移到工程之外；返回
        工程目录和 YAML 路径。
        Parse the project in root/project into a configuration YAML outside the project and
        move the current directory outside it too; return the project directory and the YAML.
        """
        project = self.root / "project"
        project.mkdir()
        (project / "demo.ioc").write_text(IOC, encoding="utf-8")
        config = self.root / "elsewhere" / "cubemx.yaml"
        config.parent.mkdir()
        self.assertEqual(run_libxr("parse", "-d", str(project), "-o", str(config))[0], 0)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        return project, config

    def test_gen_takes_the_platform_that_parse_recorded(self):
        project, config = self.parsed_elsewhere()
        self.assertEqual(config.read_text(encoding="utf-8").splitlines()[1], "Platform: stm32")
        output = project / "User" / "app_main.cpp"
        self.assertEqual(run_libxr("gen", "-i", str(config), "-o", str(output))[0], 0)
        self.assertTrue(output.is_file())

    def test_without_a_recorded_platform_gen_reads_the_project_directory(self):
        # 旧版 parse 写出的 YAML 没有 Platform；默认的工程目录是当前目录，这里不是工程。
        # A YAML written by an older parse has no Platform; the default project directory is
        # the current one, which is not a project here.
        project, config = self.parsed_elsewhere()
        text = config.read_text(encoding="utf-8").replace("Platform: stm32\n", "")
        config.write_text(text, encoding="utf-8")
        output = project / "User" / "app_main.cpp"
        argv = ["gen", "-i", str(config), "-o", str(output)]
        with self.assertLogs(level="ERROR"):
            self.assertEqual(run_libxr(*argv)[0], 1)
        self.assertFalse(output.exists())
        self.assertEqual(run_libxr(*argv, "-d", str(project))[0], 0)
        self.assertTrue(output.is_file())

    def test_an_unsupported_recorded_platform_is_an_error(self):
        config = self.root / "cubemx.yaml"
        config.write_text("Platform: zephyr\n", encoding="utf-8")
        with self.assertLogs(level="ERROR") as logs:
            self.assertEqual(run_libxr("gen", "-i", str(config), "-o", "app_main.cpp")[0], 1)
        self.assertEqual(
            logs.output,
            [
                f"ERROR:root:{config}: platform 'zephyr' is not supported "
                "(stm32: a directory with an STM32CubeMX .ioc file)"
            ],
        )

    def test_a_missing_directory_is_an_error(self):
        config = self.root / "cubemx.yaml"
        config.write_text("Mcu: {}\n", encoding="utf-8")
        for argv in (
            ["parse", "-d", "absent"],
            ["gen", "-i", str(config), "-o", "y.cpp", "-d", "absent"],
        ):
            with self.subTest(command=argv[0]), self.assertLogs(level="ERROR") as logs:
                self.assertEqual(run_libxr(*argv)[0], 1)
            self.assertEqual(logs.output, ["ERROR:root:Directory does not exist: absent"])


class Options(TestCase):
    """所有子命令共有的 --verbose，以及 gen 对 XRobot 的选择。
    The --verbose that every subcommand has, and the XRobot choice of gen.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)

    def test_python_m_libxr_is_the_libxr_command(self):
        # 以前只有 CI 的 import smoke 脚本碰过 __main__，而且是把它当模块导入。
        # Only the CI import smoke script used to touch __main__, importing it as a module.
        result = subprocess.run(
            [sys.executable, "-m", "libxr", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, XR_LANG="en"),
            check=False,
        )
        # 从源码目录运行（只设 PYTHONPATH）时没有安装信息，版本显示为 unknown。
        # Run from a source tree, with only PYTHONPATH set, there is no installation record
        # and the version shows as unknown.
        self.assertEqual(
            (result.returncode, result.stdout),
            (0, f"libxr {update_notice.installed_version() or 'unknown'}\n"),
        )

    def test_every_subcommand_takes_verbose(self):
        parser = cli.build_parser()
        for argv in (
            ["parse"],
            ["gen", "-i", "x", "-o", "y"],
            ["stm32", "setup"],
            ["stm32", "cubemx-gen"],
            ["stm32", "cmake"],
            ["stm32", "flash-info", "STM32F103C8T6"],
            ["stm32", "toolchain", "gcc"],
        ):
            with self.subTest(command=argv):
                self.assertTrue(parser.parse_args([*argv, "--verbose"]).verbose)

    def test_a_cubemx_gen_failure_logs_the_traceback_under_verbose(self):
        with (
            mock.patch(
                "libxr.cubemx_generator.generate_cubemx_project",
                side_effect=RuntimeError("broken"),
            ),
            self.assertLogs(level="DEBUG") as logs,
        ):
            self.assertEqual(run_libxr("stm32", "cubemx-gen", "--verbose")[0], 1)
        self.assertEqual(logs.output[0], "ERROR:root:broken")
        self.assertTrue(logs.output[1].startswith("DEBUG:root:Traceback:\nTraceback"))

    def gen_xrobot(self, *options, existing=None):
        """以 options 运行 gen，返回传给生成器的 use_xrobot；existing 为已有输出文件的内容。
        Run gen with options and return the use_xrobot passed to the generator; existing is
        the content of an existing output file.
        """
        config = self.root / "cubemx.yaml"
        config.write_text("Platform: stm32\n", encoding="utf-8")
        output = self.root / "app_main.cpp"
        if existing is not None:
            output.write_text(existing, encoding="utf-8")
        with mock.patch("libxr.generator_code_stm32.generate") as generate:
            argv = ["gen", "-i", str(config), "-o", str(output), *options]
            self.assertEqual(run_libxr(*argv)[0], 0)
        return generate.call_args.args[2]

    def test_gen_keeps_the_xrobot_choice_of_the_output_file(self):
        xrobot = '#include "xrobot_main.hpp"\n'
        self.assertIs(self.gen_xrobot(), False)
        self.assertIs(self.gen_xrobot(existing="int x;\n"), False)
        with self.assertLogs(level="INFO") as logs:
            self.assertIs(self.gen_xrobot(existing=xrobot), True)
        self.assertEqual(
            logs.output,
            [
                f"INFO:root:{self.root / 'app_main.cpp'} uses XRobot; generating with --xrobot "
                "(--no-xrobot turns it off)."
            ],
        )
        self.assertIs(self.gen_xrobot("--no-xrobot", existing=xrobot), False)
        self.assertIs(self.gen_xrobot("--xrobot", existing="int x;\n"), True)


if __name__ == "__main__":
    unittest.main()
