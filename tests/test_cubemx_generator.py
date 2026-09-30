"""用 Python 写的假 STM32CubeMX 运行 cubemx_generator（libxr.cubemx_generator）：命令组装、
成功生成、缺少输出、非零退出码和超时。
Running cubemx_generator (libxr.cubemx_generator) with a fake STM32CubeMX written in Python:
command building, a successful generation, missing output, a non-zero exit code and a timeout.
"""

import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from fixtures import TestCase

from libxr.cubemx_generator import build_cubemx_command, generate_cubemx_project

# 假 CubeMX 按 FAKE_CUBEMX_MODE 行为：ok（默认）为 project generate 脚本创建 Core/Inc 和 Drivers，
# fail 以 23 退出，no-output 什么都不生成，timeout 启动一个持续写心跳文件的子进程后等待。
# The fake CubeMX acts on FAKE_CUBEMX_MODE: ok, the default, creates Core/Inc and Drivers for a
# project generate script, fail exits with 23, no-output creates nothing, and timeout starts a
# child that keeps writing a heartbeat file, then waits.
FAKE_CUBEMX = r"""
import os
import subprocess
import sys
import time


def main() -> int:
    if "-q" not in sys.argv:
        print("missing -q", file=sys.stderr)
        return 2

    script_path = sys.argv[sys.argv.index("-q") + 1]
    with open(script_path, "r", encoding="utf-8") as script_file:
        script_text = script_file.read()

    mode = os.environ.get("FAKE_CUBEMX_MODE", "ok")
    if mode == "fail":
        print("fake CubeMX failure", file=sys.stderr)
        return 23

    if mode == "timeout":
        heartbeat_file = os.environ["FAKE_CUBEMX_HEARTBEAT_FILE"]
        child_code = (
            "import pathlib, sys, time\n"
            "heartbeat = pathlib.Path(sys.argv[1])\n"
            "while True:\n"
            "    heartbeat.write_text(str(time.time()), encoding='utf-8')\n"
            "    time.sleep(0.1)\n"
        )
        subprocess.Popen([sys.executable, "-c", child_code, heartbeat_file])
        for _ in range(50):
            if os.path.exists(heartbeat_file):
                break
            time.sleep(0.02)
        time.sleep(30)
        return 0

    if mode == "no-output":
        return 0

    if "project generate" not in script_text:
        print("unexpected script body", file=sys.stderr)
        return 3
    os.makedirs(os.path.join(os.getcwd(), "Core", "Inc"), exist_ok=True)
    os.makedirs(os.path.join(os.getcwd(), "Drivers"), exist_ok=True)
    print("fake CubeMX generated project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


class CubeMXTestCase(TestCase):
    """临时目录中的假 CubeMX 和一个只有 demo.ioc 的工程。
    A fake CubeMX in a temporary directory, and a project holding only demo.ioc.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.fake = self.tmp / "fake_cubemx.py"
        with self.fake.open("w", encoding="utf-8", newline="\n") as fake:
            fake.write(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_CUBEMX).lstrip())
        if os.name != "nt":
            self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.project = self.tmp / "project"
        self.project.mkdir()
        (self.project / "demo.ioc").write_text(
            "ProjectManager.ProjectName=demo\n", encoding="utf-8"
        )

    def generate(self, mode="ok", **options):
        """以 FAKE_CUBEMX_MODE=mode 用假 CubeMX 生成工程。
        Generate the project with the fake CubeMX under FAKE_CUBEMX_MODE=mode.
        """
        options.setdefault("cubemx_cmd", str(self.fake))
        options.setdefault("launch_mode", "direct")
        options.setdefault("timeout", 5)
        with mock.patch.dict(os.environ, FAKE_CUBEMX_MODE=mode):
            return generate_cubemx_project(project_dir=str(self.project), **options)


class CommandLine(CubeMXTestCase):
    """启动 CubeMX 的命令行。
    The command line that starts CubeMX.
    """

    def test_launch_modes(self):
        script = str(self.tmp / "cubemx script.txt")
        exe = str(self.tmp / "STM32CubeMX.exe")
        jar = str(self.tmp / "STM32CubeMX.jar")
        Path(exe).write_text("", encoding="utf-8")
        Path(jar).write_text("", encoding="utf-8")
        self.assertEqual(build_cubemx_command(exe, script, launch_mode="auto"), [exe, "-q", script])
        java = build_cubemx_command(jar, script, launch_mode="auto", java_cmd=sys.executable)
        index = java.index("-jar")
        self.assertEqual(java[index + 1 : index + 4], [jar, "-q", script])
        self.assertEqual(
            build_cubemx_command(str(self.fake), script, launch_mode="direct", silent=True),
            [sys.executable, str(self.fake), "-q", script, "-s"],
        )
        with self.assertRaisesMessage(
            ValueError,
            "Java launch mode requires an STM32CubeMX .jar path. Use --launch-mode direct for "
            "STM32CubeMX.exe.",
        ):
            build_cubemx_command(exe, script, launch_mode="java", java_cmd=sys.executable)

    def test_a_command_that_cannot_be_built_leaves_no_script(self):
        exe = self.tmp / "STM32CubeMX.exe"
        exe.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.generate(cubemx_cmd=str(exe), launch_mode="java", java_cmd=sys.executable)
        self.assertEqual([p.name for p in self.project.iterdir()], ["demo.ioc"])

    def test_a_missing_cubemx_is_named(self):
        absent = str(self.tmp / "absent" / "STM32CubeMX.exe")
        with self.assertRaisesMessage(
            FileNotFoundError, f"{absent} does not exist and is not a command on PATH"
        ):
            self.generate(cubemx_cmd=absent)


class Generation(CubeMXTestCase):
    """运行 CubeMX 并检查结果。
    Running CubeMX and checking the result.
    """

    def test_a_successful_generation_is_logged_and_removes_its_script(self):
        logs = self.tmp / "logs"
        result = self.generate(log_dir=str(logs))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(Path(result.script_path).exists())
        script = (logs / "cubemx_generate.txt").read_text(encoding="utf-8")
        self.assertIn("config load", script)
        self.assertIn("project generate", script)
        self.assertIn(str(self.fake), (logs / "cubemx_command.txt").read_text(encoding="utf-8"))
        self.assertTrue((self.project / "Core" / "Inc").is_dir())
        self.assertTrue((self.project / "Drivers").is_dir())

    def test_missing_expected_paths_fail_the_run(self):
        missing = ", ".join(
            os.path.abspath(os.path.join(str(self.project), p)) for p in ("Core/Inc", "Drivers")
        )
        with self.assertRaisesMessage(
            RuntimeError, f"STM32CubeMX finished but expected paths are still missing: {missing}"
        ):
            self.generate("no-output")

    def test_a_non_zero_exit_reports_the_code_and_output(self):
        with self.assertRaisesMessage(
            RuntimeError,
            "STM32CubeMX generation failed with exit code 23\nSTDOUT tail:\n\n"
            "STDERR tail:\nfake CubeMX failure",
        ):
            self.generate("fail")

    def test_a_timeout_stops_cubemx_and_its_children(self):
        heartbeat = self.tmp / "child.heartbeat"
        with (
            mock.patch.dict(os.environ, FAKE_CUBEMX_HEARTBEAT_FILE=str(heartbeat)),
            self.assertRaisesMessage(TimeoutError, "STM32CubeMX timed out after 3 seconds"),
        ):
            self.generate("timeout", timeout=3)
        # 心跳文件在 0.5 秒内不再变化时，子进程已停止。
        # The child has stopped once the heartbeat file no longer changes within 0.5 seconds.
        deadline = time.time() + 3
        while time.time() < deadline:
            before = heartbeat.read_text(encoding="utf-8")
            time.sleep(0.5)
            if heartbeat.read_text(encoding="utf-8") == before:
                return
        self.fail("the child of the fake CubeMX kept running after the timeout")


if __name__ == "__main__":
    unittest.main()
