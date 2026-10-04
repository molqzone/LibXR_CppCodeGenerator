"""6.0.0 之前的 xr_* 命令（libxr.legacy）：提示新命令，参数及其含义与原来相同。
The xr_* commands of versions before 6.0.0 (libxr.legacy): they name the new command and
keep their arguments with their meaning.
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

from fixtures import IOC, TestCase, run_libxr

from libxr import cli, legacy

REPOSITORY = Path(__file__).resolve().parents[1]


def installed_scripts() -> dict:
    """pyproject.toml 中 [project.scripts] 的命令名和入口（Python 3.10 没有 tomllib）。
    The command names and entry points of [project.scripts] in pyproject.toml (Python 3.10 has
    no tomllib).
    """
    text = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[project.scripts]\n", 1)[1].split("\n[", 1)[0]
    return dict(re.findall(r'^(\w+) = "([^"]+)"$', section, re.M))


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
            mock.patch("libxr.update_notice._latest_release", return_value=None),
            mock.patch("libxr.update_notice._cache_path", return_value=None),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertLogs(level="WARNING") as logs,
        ):
            try:
                code = legacy.run(old, list(argv))
            except SystemExit as exit:
                code = exit.code
        self.log = logs.output
        return code, out.getvalue(), err.getvalue()

    def test_an_old_command_warns_and_runs_the_new_one(self):
        out = io.StringIO()
        with (
            mock.patch("libxr.update_notice._latest_release", return_value=None),
            mock.patch("libxr.update_notice._cache_path", return_value=None),
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
        # 新命令的 -d 默认为当前目录，旧命令仍然必须给出。
        # The -d of the new commands defaults to the current directory; the old ones still
        # need it.
        for old, argv, missing in (
            ("xr_parse", [], "-d/--directory"),
            ("xr_parse_ioc", [], "-d/--directory"),
            ("xr_cubemx_cfg", [], "-d/--directory"),
            ("xr_cubemx_generate", [], "-d/--directory"),
            ("xr_stm32_cmake", [], "input_dir"),
            ("xr_gen_code", ["-o", "y"], "-i/--input"),
            ("xr_gen_code_stm32", ["-i", "x"], "-o/--output"),
        ):
            with self.subTest(old=old, argv=argv):
                code, _, err = self.run_old(old, *argv)
                self.assertEqual(
                    (code, err.splitlines()[-1]),
                    (2, f"{old}: error: the following arguments are required: {missing}"),
                )

    def test_every_old_option_reaches_the_new_command(self):
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        for old, argv, command, expected in (
            (
                "xr_parse",
                ["-d", "p", "-o", "o.yaml", "--verbose"],
                "cmd_parse",
                {"directory": "p", "output": "o.yaml", "verbose": True},
            ),
            (
                "xr_cubemx_cfg",
                ["-d", "p", "-t", "usart1", "--xrobot", "--commit", "abc"]
                + ["--git-source", "github", "--git-mirrors", "m1"],
                "cmd_stm32_setup",
                {
                    "directory": "p",
                    "terminal": "usart1",
                    "xrobot": True,
                    "commit": "abc",
                    "git_source": "github",
                    "git_mirrors": "m1",
                },
            ),
            (
                "xr_cubemx_generate",
                ["-d", "p", "--ioc", "a.ioc", "--cubemx-cmd", "c", "--java-cmd", "j"]
                + ["--launch-mode", "java", "--generate-code-dir", "g", "--expect-path", "a"]
                + ["--expect-path", "b", "--log-dir", "l", "--script-path", "s"]
                + ["--keep-script", "--silent", "--auto-confirm", "--timeout", "60"],
                "cmd_stm32_cubemx_gen",
                {
                    "directory": "p",
                    "ioc": "a.ioc",
                    "cubemx_cmd": "c",
                    "java_cmd": "j",
                    "launch_mode": "java",
                    "generate_code_dir": "g",
                    "expect_path": ["a", "b"],
                    "log_dir": "l",
                    "script_path": "s",
                    "keep_script": True,
                    "silent": True,
                    "auto_confirm": True,
                    "timeout": 60,
                    "firmware": "migrate",
                    "download": True,
                },
            ),
            (
                "xr_stm32_toolchain_switch",
                ["clang", "--gnu"],
                "cmd_stm32_toolchain",
                {"compiler": "clang", "std": "hybrid", "directory": "."},
            ),
            (
                "xr_stm32_toolchain_switch",
                ["clang", "-p"],
                "cmd_stm32_toolchain",
                {"compiler": "clang", "std": "picolibc", "directory": "."},
            ),
        ):
            with self.subTest(old=old, argv=argv), mock.patch(f"libxr.cli.{command}") as new:
                self.assertEqual(self.run_old(old, *argv)[0], 0)
                self.assertEqual(vars(new.call_args.args[0]), expected)

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

    def test_xr_parse_writes_the_yaml_named_after_the_ioc_file(self):
        # 6.0.0 之前不给 -o 时写 <ioc 名>.yaml，之后的 xr_gen_code_stm32 -i <ioc 名>.yaml 依赖它；
        # 旧命令曾改为写 .config.yaml。libxr parse 仍写 .config.yaml。
        # Before 6.0.0 the output without -o was <ioc name>.yaml, which a following
        # xr_gen_code_stm32 -i <ioc name>.yaml relies on; the old command used to write
        # .config.yaml instead. libxr parse still writes .config.yaml.
        (self.root / "demo.ioc").write_text(IOC, encoding="utf-8")
        for old in ("xr_parse", "xr_parse_ioc"):
            with self.subTest(old=old):
                self.assertEqual(self.run_old(old, "-d", str(self.root))[0], 0)
                self.assertTrue((self.root / "demo.yaml").is_file())
                self.assertFalse((self.root / ".config.yaml").exists())
                (self.root / "demo.yaml").unlink()
        with contextlib.redirect_stdout(io.StringIO()), self.assertLogs(level="INFO"):
            self.assertEqual(run_libxr("parse", "-d", str(self.root))[0], 0)
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()), [".config.yaml", "demo.ioc"]
        )

    def test_hw_cntr_stops_with_its_replacement(self):
        # 6.0.0 的旧命令曾以“无法识别的参数”退出，没有说明原因。
        # The old commands of 6.0.0 used to exit with "unrecognized arguments" and no reason.
        project = self.root / "project"
        project.mkdir()
        (project / "demo.ioc").write_text(IOC, encoding="utf-8")
        (project / "cubemx.yaml").write_text("{}\n", encoding="utf-8")
        argv = ["-i", str(project / "cubemx.yaml"), "-o", "app_main.cpp", "--hw-cntr"]
        for old in ("xr_gen_code", "xr_gen_code_stm32"):
            with (
                self.subTest(old=old),
                mock.patch("libxr.generator_code_stm32.generate") as generate,
            ):
                self.assertEqual(self.run_old(old, *argv)[0], 1)
                generate.assert_not_called()
                self.assertEqual(
                    self.log[1:],
                    [
                        "ERROR:root:--hw-cntr generated a LibXR::HardwareContainer, which LibXR "
                        "no longer has. With --xrobot the generated objects are registered with "
                        "XR_REGISTER and the XRobot Modules use them by name; without it, User "
                        "Code uses the objects directly."
                    ],
                )

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
        usage = (
            "STM32 Flash Information Tool\nUsage:\n  xr_stm32_flash <STM32_MODEL>\n\nExamples:\n"
            "  xr_stm32_flash STM32F103C8T6\n  xr_stm32_flash STM32L476RG\n"
        )
        self.assertEqual(self.run_old("xr_stm32_flash"), (1, "", usage))
        self.assertEqual(self.run_old("xr_stm32_flash", "--help"), (0, usage, ""))
        self.assertEqual(self.run_old("xr_stm32_flash", "STM32F103C8T6", "extra")[0], 1)
        # 无法处理的型号沿用旧的退出码 2；新命令用 1。
        # A model that cannot be processed keeps the old exit code 2; the new command uses 1.
        self.assertEqual(self.run_old("xr_stm32_flash", "STM32X999")[0], 2)
        with self.assertLogs(level="ERROR") as logs:
            self.assertEqual(run_libxr("stm32", "flash-info", "STM32X999")[0], 1)
        self.assertEqual(logs.output, ["ERROR:root:Unrecognized capacity code for STM32X999"])

    def test_xr_stm32_toolchain_switch_works_on_the_current_directory(self):
        with mock.patch("libxr.stm32_toolchain_switch.switch_toolchain") as switch:
            self.assertEqual(self.run_old("xr_stm32_toolchain_switch", "clang", "-n")[0], 0)
        switch.assert_called_once_with(".", "clang", "newlib")
        self.assertEqual(self.run_old("xr_stm32_toolchain_switch", "-d", ".", "gcc")[0], 2)

    def test_every_module_with_a_script_entry_runs_its_old_command(self):
        # 每个带 `python -m` 入口的模块都运行一个旧命令；--help 不需要工程。
        # Every module with a `python -m` entry runs an old command; --help needs no project.
        entries = {}
        for path in sorted((REPOSITORY / "src" / "libxr").glob("*.py")):
            match = re.search(r'raise SystemExit\(run\("(\w+)"\)\)', path.read_text("utf-8"))
            if match:
                entries[path.stem] = match.group(1)
        self.assertEqual(len(entries), 7)
        for module, old in entries.items():
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-m", f"libxr.{module}", "--help"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=dict(os.environ, XR_LANG="en"),
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stderr,
                    f"[WARNING] {old} is now `{legacy.COMMANDS[old].new}`; the old name is "
                    "removed in libxr 7.0.0\n",
                )
                self.assertIn(old, result.stdout)


if __name__ == "__main__":
    unittest.main()
