"""libxr 命令（libxr.cli）：平台识别、旧命令、新版本提示和 generator 版本固定的检查。
The libxr command (libxr.cli): platform recognition, the old commands, the new-version notice
and the check of the generator pin.
"""

import contextlib
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import IOC, GeneratorTestCase, TestCase, run_libxr

from libxr import cli
from libxr import generator_code_stm32 as generator

REPOSITORY = Path(__file__).resolve().parents[1]


def installed_scripts() -> dict:
    """pyproject.toml 中 [project.scripts] 的命令名和入口（Python 3.10 没有 tomllib）。
    The command names and entry points of [project.scripts] in pyproject.toml (Python 3.10 has
    no tomllib).
    """
    text = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[project.scripts]\n", 1)[1].split("\n[", 1)[0]
    return dict(re.findall(r'^(\w+) = "([^"]+)"$', section, re.M))


def logging_marker():
    """记一条警告，使 assertLogs 在没有其他警告时也有记录。
    Log one warning so that assertLogs has a record when nothing else warns.
    """
    logging.warning("marker")


class Platforms(TestCase):
    """parse 和 gen 按工程目录识别平台，无法识别时报错并列出支持的平台。
    parse and gen recognize the platform by the project directory and fail with the supported
    platforms when none matches.
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

    def test_gen_reads_the_platform_of_the_project_directory(self):
        project = self.root / "project"
        project.mkdir()
        (project / "demo.ioc").write_text(IOC, encoding="utf-8")
        config = self.root / "elsewhere" / "cubemx.yaml"
        config.parent.mkdir()
        self.assertEqual(run_libxr("parse", "-d", str(project), "-o", str(config))[0], 0)
        output = project / "User" / "app_main.cpp"
        argv = ["gen", "-i", str(config), "-o", str(output)]
        # 默认的工程目录是当前目录，这里不是工程。
        # The default project directory is the current one, which is not a project here.
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        with self.assertLogs(level="ERROR"):
            self.assertEqual(run_libxr(*argv)[0], 1)
        self.assertFalse(output.exists())
        self.assertEqual(run_libxr(*argv, "-d", str(project))[0], 0)
        self.assertTrue(output.is_file())

    def test_a_missing_directory_is_an_error(self):
        for argv in (
            ["parse", "-d", "absent"],
            ["gen", "-i", "x.yaml", "-o", "y.cpp", "-d", "absent"],
        ):
            with self.subTest(command=argv[0]), self.assertLogs(level="ERROR") as logs:
                self.assertEqual(run_libxr(*argv)[0], 1)
            self.assertEqual(logs.output, ["ERROR:root:Directory does not exist: absent"])


class LegacyCommands(TestCase):
    """旧的 xr_* 命令提示新命令后以同样的参数运行它。
    The old xr_* commands name the new command, then run it with the same arguments.
    """

    def test_an_old_command_warns_and_runs_the_new_one(self):
        out = io.StringIO()
        with (
            mock.patch("libxr.update_notice._latest_version", return_value=None),
            contextlib.redirect_stdout(out),
            self.assertLogs(level="WARNING") as logs,
        ):
            code = cli.legacy("xr_stm32_flash", ["STM32F103C8T6"])
        self.assertEqual(code, 0)
        self.assertEqual(
            logs.output,
            [
                "WARNING:root:xr_stm32_flash is now `libxr stm32 flash-info`; "
                "the old name is removed in libxr 7.0.0"
            ],
        )
        self.assertIn("model: STM32F103C8T6", out.getvalue())

    def test_every_old_command_is_installed_and_names_a_subcommand(self):
        scripts = installed_scripts()
        self.assertEqual(scripts.pop("libxr"), "libxr.cli:main")
        self.assertEqual(scripts, {old: f"libxr.cli:{old}" for old in cli.LEGACY_COMMANDS})
        for old, new in cli.LEGACY_COMMANDS.items():
            with self.subTest(old=old), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:
                    cli.build_parser().parse_args([*new, "--help"])
                self.assertEqual(exit.exception.code, 0)

    def test_the_positional_directory_of_xr_stm32_cmake_still_works(self):
        with (
            mock.patch("libxr.update_notice._latest_version", return_value=None),
            mock.patch("libxr.generator_stm32_cmake.integrate") as integrate,
            self.assertLogs(level="WARNING"),
        ):
            self.assertEqual(cli.legacy("xr_stm32_cmake", ["project"]), 0)
        integrate.assert_called_once_with("project")

    def test_an_old_module_still_runs_as_a_script(self):
        result = subprocess.run(
            [sys.executable, "-m", "libxr.stm32_flash_generator", "STM32F103C8T6"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, XR_LANG="en"),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("xr_stm32_flash is now `libxr stm32 flash-info`", result.stderr)
        self.assertIn("model: STM32F103C8T6", result.stdout)


class UpdateNotice(TestCase):
    """命令结束时，PyPI 上有更新的 libxr 就提示升级。
    At the end of a command, a newer libxr on PyPI is reported.
    """

    def run_with_latest(self, latest, *argv):
        """在 PyPI 最新版本为 latest、已安装 6.0.0 时运行 libxr，返回警告日志。
        Run libxr while the latest version on PyPI is latest and 6.0.0 is installed; return the
        warning logs.
        """
        with (
            mock.patch("libxr.update_notice._latest_version", return_value=latest),
            mock.patch("libxr.update_notice.installed_version", return_value="6.0.0"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertLogs(level="WARNING") as logs,
        ):
            logging_marker()
            with contextlib.suppress(SystemExit):
                cli.main(list(argv))
        return [line for line in logs.output if line.startswith("WARNING") and "marker" not in line]

    def test_a_newer_version_is_reported_at_the_end(self):
        self.assertEqual(
            self.run_with_latest("6.1.0", "stm32", "flash-info", "STM32F103C8T6"),
            [
                "WARNING:root:libxr 6.1.0 is available (installed: 6.0.0); "
                "upgrade with `pip install -U libxr`"
            ],
        )

    def test_the_notice_follows_a_failed_command_too(self):
        self.assertEqual(
            self.run_with_latest("6.1.0", "parse", "-d", "absent"),
            [
                "WARNING:root:libxr 6.1.0 is available (installed: 6.0.0); "
                "upgrade with `pip install -U libxr`"
            ],
        )

    def test_no_notice_without_a_newer_version(self):
        for latest in (None, "6.0.0", "5.9.9", "not a version"):
            with self.subTest(latest=latest):
                self.assertEqual(
                    self.run_with_latest(latest, "stm32", "flash-info", "STM32F103C8T6"), []
                )


class GeneratorPin(GeneratorTestCase):
    """libxr_config.yaml 固定的 generator 版本与已安装的不同时警告。
    A warning when the generator pinned in libxr_config.yaml differs from the installed one.
    """

    def warnings(self, pin):
        """在固定 pin、已安装 6.0.0 时检查，返回警告日志。
        Check with the pin pin and 6.0.0 installed; return the warning logs.
        """
        generator.libxr_settings["generator"] = pin
        with (
            mock.patch("libxr.update_notice.installed_version", return_value="6.0.0"),
            self.assertLogs(level="WARNING") as logs,
        ):
            logging_marker()
            generator.check_generator_pin()
        return [line for line in logs.output if "marker" not in line]

    def test_a_different_pin_warns(self):
        self.assertEqual(
            self.warnings("5.2.4"),
            [
                "WARNING:root:libxr_config.yaml pins generator 5.2.4, but libxr 6.0.0 is "
                "installed; the BSP CI generates with 5.2.4"
            ],
        )

    def test_the_same_version_a_commit_or_no_pin_is_quiet(self):
        for pin in ("6.0.0", "0123456789abcdef0123456789abcdef01234567", None):
            with self.subTest(pin=pin):
                self.assertEqual(self.warnings(pin), [])


if __name__ == "__main__":
    unittest.main()
