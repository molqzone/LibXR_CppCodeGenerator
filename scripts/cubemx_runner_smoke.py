#!/usr/bin/env python3
"""cubemx_generator 的冒烟测试：用 Python 写的假 STM32CubeMX 检查命令组装、成功生成、缺少输出、
非零退出码和超时。
Smoke tests for cubemx_generator: a fake STM32CubeMX written in Python checks command building,
a successful generation, missing output, a non-zero exit code and a timeout.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
import tempfile
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from libxr.cubemx_generator import (  # noqa: E402
    build_cubemx_command,
    generate_cubemx_project,
)

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
        child_pid_file = os.environ.get("FAKE_CUBEMX_CHILD_PID_FILE", "")
        heartbeat_file = os.environ.get("FAKE_CUBEMX_HEARTBEAT_FILE", "")
        child_code = (
            "import pathlib, sys, time\n"
            "heartbeat = pathlib.Path(sys.argv[1])\n"
            "while True:\n"
            "    heartbeat.write_text(str(time.time()), encoding='utf-8')\n"
            "    time.sleep(0.1)\n"
        )
        child = subprocess.Popen([sys.executable, "-c", child_code, heartbeat_file])
        if child_pid_file:
            with open(child_pid_file, "w", encoding="utf-8") as pid_file:
                pid_file.write(str(child.pid))
        for _ in range(50):
            if heartbeat_file and os.path.exists(heartbeat_file):
                break
            time.sleep(0.02)
        time.sleep(30)
        return 0

    if mode == "no-output":
        return 0


    if "project generate" in script_text:
        os.makedirs(os.path.join(os.getcwd(), "Core", "Inc"), exist_ok=True)
        os.makedirs(os.path.join(os.getcwd(), "Drivers"), exist_ok=True)
    else:
        print("unexpected script body", file=sys.stderr)
        return 3

    print("fake CubeMX generated project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


@contextlib.contextmanager
def patched_env(**values: str) -> Iterator[None]:
    """在 with 块内临时设置环境变量，值为空字符串时删除该变量；退出时恢复原值。
    Set environment variables for the with block, removing those whose value is an empty string,
    and restore the old values on exit.
    """
    old_values = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_fake_cubemx(tmpdir: Path) -> Path:
    """在 tmpdir 中写入假的 CubeMX 脚本 fake_cubemx.py（shebang 为当前解释器），非 Windows 上
    加执行权限，返回其路径。
    Write the fake CubeMX script fake_cubemx.py into tmpdir with the current interpreter as its
    shebang, make it executable outside Windows, and return its path.

    假 CubeMX 按环境变量 FAKE_CUBEMX_MODE 行为：ok（默认）为 project generate 脚本创建 Core/Inc
    和 Drivers，fail 以 23 退出，no-output 什么都不生成，timeout 启动一个持续写心跳文件的子进程后
    等待。
    The fake CubeMX acts on FAKE_CUBEMX_MODE: ok, the default, creates Core/Inc and Drivers for a
    project generate script, fail exits with 23, no-output creates nothing, and timeout starts a
    child that keeps writing a heartbeat file, then waits.
    """
    fake = tmpdir / "fake_cubemx.py"
    with fake.open("w", encoding="utf-8", newline="\n") as fake_file:
        fake_file.write(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_CUBEMX).lstrip())
    if os.name != "nt":
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def write_ioc(project_dir: Path) -> None:
    """在工程目录中写入最小的 demo.ioc。
    Write a minimal demo.ioc into the project directory.
    """
    (project_dir / "demo.ioc").write_text("ProjectManager.ProjectName=demo\n", encoding="utf-8")


def assert_contains(haystack: str, needle: str) -> None:
    """haystack 不含 needle 时抛出 AssertionError。
    Raise AssertionError when needle is not in haystack.
    """
    if needle not in haystack:
        raise AssertionError(f"expected {needle!r} in {haystack!r}")


def run_command_builder_smoke(tmpdir: Path, fake_cubemx: Path) -> None:
    """检查 build_cubemx_command：auto 模式下可执行文件直接启动、.jar 经 java -jar 启动，java 模式
    拒绝非 .jar 路径，direct 模式下 .py 用当前解释器启动并附加 -s。
    Check build_cubemx_command: in auto mode an executable starts directly and a .jar through
    java -jar, java mode rejects a path that is not a .jar, and in direct mode a .py starts with
    the current interpreter and gets -s.
    """
    script_path = str(tmpdir / "cubemx script.txt")
    exe_path = str(tmpdir / "STM32CubeMX.exe")
    jar_path = str(tmpdir / "STM32CubeMX.jar")
    Path(exe_path).write_text("", encoding="utf-8")
    Path(jar_path).write_text("", encoding="utf-8")

    direct = build_cubemx_command(exe_path, script_path, launch_mode="auto")
    if direct != [exe_path, "-q", script_path]:
        raise AssertionError(f"auto mode should directly launch executables: {direct!r}")

    java = build_cubemx_command(jar_path, script_path, launch_mode="auto", java_cmd=sys.executable)
    jar_index = java.index("-jar")
    if java[jar_index + 1 : jar_index + 4] != [jar_path, "-q", script_path]:
        raise AssertionError(f"auto mode should launch jars through java -jar: {java!r}")

    try:
        build_cubemx_command(exe_path, script_path, launch_mode="java", java_cmd=sys.executable)
    except ValueError as error:
        assert_contains(str(error), ".jar")
    else:
        raise AssertionError("java launch mode accepted a non-jar CubeMX path")

    py_cmd = build_cubemx_command(str(fake_cubemx), script_path, launch_mode="direct", silent=True)
    if py_cmd != [sys.executable, str(fake_cubemx), "-q", script_path, "-s"]:
        raise AssertionError(f"direct launch command changed unexpectedly: {py_cmd!r}")


def run_success_smoke(tmpdir: Path, fake_cubemx: Path) -> None:
    """检查成功的生成：退出码为 0，临时脚本已删除，日志中有脚本和命令，Core/Inc 和 Drivers 已创建。
    Check a successful generation: exit code 0, the temporary script removed, the script and the
    command in the logs, and Core/Inc and Drivers created.
    """
    project_dir = tmpdir / "project"
    project_dir.mkdir()
    write_ioc(project_dir)
    log_dir = tmpdir / "logs"

    result = generate_cubemx_project(
        project_dir=str(project_dir),
        cubemx_cmd=str(fake_cubemx),
        launch_mode="direct",
        log_dir=str(log_dir),
        timeout=5,
    )

    if result.returncode != 0:
        raise AssertionError(f"unexpected return code: {result.returncode}")
    if Path(result.script_path).exists():
        raise AssertionError("temporary CubeMX script was not removed")

    script_copy = (log_dir / "cubemx_generate.txt").read_text(encoding="utf-8")
    assert_contains(script_copy, "config load")
    assert_contains(script_copy, "project generate")

    command_log = (log_dir / "cubemx_command.txt").read_text(encoding="utf-8")
    assert_contains(command_log, str(fake_cubemx))
    if not (project_dir / "Core" / "Inc").is_dir() or not (project_dir / "Drivers").is_dir():
        raise AssertionError("fake CubeMX did not create expected output paths")


def run_expect_path_smoke(tmpdir: Path, fake_cubemx: Path) -> None:
    """检查 CubeMX 正常退出但没有生成期望路径时，运行器抛出 RuntimeError。
    Check that the runner raises RuntimeError when CubeMX exits normally without creating the
    expected paths.
    """
    project_dir = tmpdir / "missing-output"
    project_dir.mkdir()
    write_ioc(project_dir)

    with patched_env(FAKE_CUBEMX_MODE="no-output"):
        try:
            generate_cubemx_project(
                project_dir=str(project_dir),
                cubemx_cmd=str(fake_cubemx),
                launch_mode="direct",
                timeout=5,
            )
        except RuntimeError as error:
            assert_contains(str(error), "expected paths")
        else:
            raise AssertionError("missing expected paths did not fail the runner")


def run_returncode_smoke(tmpdir: Path, fake_cubemx: Path) -> None:
    """检查 CubeMX 以非零退出码结束时，运行器抛出的 RuntimeError 中带有退出码和标准错误内容。
    Check that a non-zero CubeMX exit makes the runner raise a RuntimeError carrying the exit code
    and the stderr text.
    """
    project_dir = tmpdir / "returncode"
    project_dir.mkdir()
    write_ioc(project_dir)

    with patched_env(FAKE_CUBEMX_MODE="fail"):
        try:
            generate_cubemx_project(
                project_dir=str(project_dir),
                cubemx_cmd=str(fake_cubemx),
                launch_mode="direct",
                timeout=5,
            )
        except RuntimeError as error:
            assert_contains(str(error), "exit code 23")
            assert_contains(str(error), "fake CubeMX failure")
        else:
            raise AssertionError("non-zero CubeMX exit did not fail the runner")


def child_stopped_writing(heartbeat_file: Path) -> bool:
    """心跳文件存在且 0.5 秒内内容没有变化时为 True，即子进程已停止写入。
    True when the heartbeat file exists and its content does not change within 0.5 seconds,
    meaning the child process stopped writing.
    """
    if not heartbeat_file.exists():
        return False
    previous = heartbeat_file.read_text(encoding="utf-8")
    time.sleep(0.5)
    current = heartbeat_file.read_text(encoding="utf-8")
    return current == previous


def run_timeout_smoke(tmpdir: Path, fake_cubemx: Path) -> None:
    """检查超时：运行器抛出 TimeoutError，并且假 CubeMX 启动的子进程在 3 秒内停止写心跳文件。
    Check a timeout: the runner raises TimeoutError, and the child process started by the fake
    CubeMX stops writing its heartbeat file within 3 seconds.
    """
    project_dir = tmpdir / "timeout"
    project_dir.mkdir()
    write_ioc(project_dir)
    child_pid_file = tmpdir / "child.pid"
    heartbeat_file = tmpdir / "child.heartbeat"

    with patched_env(
        FAKE_CUBEMX_MODE="timeout",
        FAKE_CUBEMX_CHILD_PID_FILE=str(child_pid_file),
        FAKE_CUBEMX_HEARTBEAT_FILE=str(heartbeat_file),
    ):
        try:
            generate_cubemx_project(
                project_dir=str(project_dir),
                cubemx_cmd=str(fake_cubemx),
                launch_mode="direct",
                timeout=3,
            )
        except TimeoutError as error:
            assert_contains(str(error), "timed out")
        else:
            raise AssertionError("timed-out CubeMX process did not fail the runner")

    deadline = time.time() + 3
    while time.time() < deadline:
        if child_stopped_writing(heartbeat_file):
            return
        time.sleep(0.1)
    raise AssertionError("fake CubeMX child process kept running after timeout termination")


def main() -> int:
    """在临时目录中依次运行各项冒烟检查；全部通过时打印结果并返回 0，失败时抛出 AssertionError。
    Run every smoke check in a temporary directory; print a message and return 0 when all pass,
    and raise AssertionError on a failure.
    """
    with tempfile.TemporaryDirectory(prefix="cubemx_runner_smoke_") as tmp:
        tmpdir = Path(tmp)
        fake_cubemx = write_fake_cubemx(tmpdir)
        run_command_builder_smoke(tmpdir, fake_cubemx)
        run_success_smoke(tmpdir, fake_cubemx)
        run_expect_path_smoke(tmpdir, fake_cubemx)
        run_returncode_smoke(tmpdir, fake_cubemx)
        run_timeout_smoke(tmpdir, fake_cubemx)

    print("CubeMX runner smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
