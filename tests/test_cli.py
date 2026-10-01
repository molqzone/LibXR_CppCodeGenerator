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

from libxr import cli, legacy
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
    """旧的 xr_* 命令提示新命令，参数及其含义与 6.0.0 之前相同。
    The old xr_* commands name their new command and keep the arguments, with their meaning,
    of versions before 6.0.0.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_old(self, old, *argv):
        """以 argv 运行旧命令 old（不查询 PyPI），返回退出码、标准输出和标准错误。
        Run the old command old with argv, without querying PyPI; return the exit code, stdout
        and stderr.
        """
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("libxr.update_notice._latest_version", return_value=None),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertLogs(level="WARNING"),
        ):
            try:
                code = legacy.run(old, list(argv))
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def test_an_old_command_warns_and_runs_the_new_one(self):
        out = io.StringIO()
        with (
            mock.patch("libxr.update_notice._latest_version", return_value=None),
            contextlib.redirect_stdout(out),
            self.assertLogs(level="WARNING") as logs,
        ):
            code = legacy.run("xr_stm32_flash", ["STM32F103C8T6"])
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
        self.assertEqual(scripts, {old: f"libxr.legacy:{old}" for old in legacy.COMMANDS})
        for old, command in legacy.COMMANDS.items():
            with self.subTest(old=old), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:
                    cli.build_parser().parse_args([*command.new.split()[1:], "--help"])
                self.assertEqual(exit.exception.code, 0)

    def test_the_old_required_arguments_are_still_required(self):
        for old in ("xr_parse", "xr_parse_ioc", "xr_cubemx_cfg", "xr_cubemx_generate"):
            with self.subTest(old=old):
                code, _, err = self.run_old(old)
                self.assertEqual(code, 2)
                self.assertIn("-d/--directory", err)
        code, _, err = self.run_old("xr_stm32_cmake")
        self.assertEqual(code, 2)
        self.assertIn("input_dir", err)
        for new in (["parse"], ["stm32", "setup"], ["stm32", "cubemx-gen"]):
            with self.subTest(new=new):
                self.assertEqual(cli.build_parser().parse_args(new).directory, ".")

    def test_xr_gen_code_takes_the_platform_from_the_input_directory(self):
        project = self.root / "project"
        project.mkdir()
        (project / "demo.ioc").write_text(IOC, encoding="utf-8")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        for folder in (project, elsewhere):
            (folder / "cubemx.yaml").write_text("{}\n", encoding="utf-8")
        with mock.patch("libxr.generator_code_stm32.generate") as generate:
            argv = ["-i", str(project / "cubemx.yaml"), "-o", "app_main.cpp", "--xrobot"]
            self.assertEqual(self.run_old("xr_gen_code", *argv)[0], 0)
            generate.assert_called_once_with(str(project / "cubemx.yaml"), "app_main.cpp", True, "")
            generate.reset_mock()
            argv = ["-i", str(elsewhere / "cubemx.yaml"), "-o", "app_main.cpp"]
            self.assertEqual(self.run_old("xr_gen_code", *argv)[0], 0)
            generate.assert_not_called()
            self.assertEqual(self.run_old("xr_gen_code_stm32", *argv)[0], 0)
            generate.assert_called_once_with(
                str(elsewhere / "cubemx.yaml"), "app_main.cpp", False, ""
            )
        self.assertEqual(self.run_old("xr_gen_code", *argv, "-d", str(project))[0], 2)

    def test_xr_cubemx_cfg_without_xrobot_does_not_use_it(self):
        with mock.patch("libxr.config_cubemx_project.setup_project") as setup:
            self.assertEqual(self.run_old("xr_cubemx_cfg", "-d", "project")[0], 0)
            self.assertIs(setup.call_args.kwargs["xrobot_enable"], False)
            self.assertEqual(self.run_old("xr_cubemx_cfg", "-d", "project", "--xrobot")[0], 0)
            self.assertIs(setup.call_args.kwargs["xrobot_enable"], True)
        self.assertEqual(self.run_old("xr_cubemx_cfg", "-d", "project", "--no-xrobot")[0], 2)

    def test_xr_cubemx_generate_keeps_auto_confirm_and_the_first_ioc(self):
        for name in ("b.ioc", "a.ioc"):
            (self.root / name).write_text("", encoding="utf-8")
        with mock.patch("libxr.cubemx_generator.generate_cubemx_project") as generate:
            self.assertEqual(self.run_old("xr_cubemx_generate", "-d", str(self.root))[0], 0)
            options = generate.call_args.kwargs
            self.assertEqual(options["ioc_file"], os.path.join(str(self.root), "a.ioc"))
            self.assertEqual((options["firmware"], options["download"]), (None, False))
            argv = ["-d", str(self.root), "--auto-confirm"]
            self.assertEqual(self.run_old("xr_cubemx_generate", *argv)[0], 0)
            options = generate.call_args.kwargs
            self.assertEqual((options["firmware"], options["download"]), ("migrate", True))
        self.assertEqual(self.run_old("xr_cubemx_generate", *argv, "--download")[0], 2)

    def test_the_positional_directory_of_xr_stm32_cmake_still_works(self):
        with mock.patch("libxr.generator_stm32_cmake.integrate") as integrate:
            self.assertEqual(self.run_old("xr_stm32_cmake", "project")[0], 0)
        integrate.assert_called_once_with("project")
        self.assertEqual(self.run_old("xr_stm32_cmake", "-d", "project")[0], 2)

    def test_xr_stm32_flash_takes_exactly_one_model(self):
        code, out, err = self.run_old("xr_stm32_flash")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("xr_stm32_flash <STM32_MODEL>", err)
        code, out, _ = self.run_old("xr_stm32_flash", "--help")
        self.assertEqual(code, 0)
        self.assertIn("xr_stm32_flash STM32F103C8T6", out)
        self.assertEqual(self.run_old("xr_stm32_flash", "STM32F103C8T6", "extra")[0], 1)

    def test_xr_stm32_toolchain_switch_works_on_the_current_directory(self):
        with mock.patch("libxr.stm32_toolchain_switch.switch_toolchain") as switch:
            self.assertEqual(self.run_old("xr_stm32_toolchain_switch", "clang", "-n")[0], 0)
        switch.assert_called_once_with(".", "clang", "newlib")
        self.assertEqual(self.run_old("xr_stm32_toolchain_switch", "-d", ".", "gcc")[0], 2)

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
