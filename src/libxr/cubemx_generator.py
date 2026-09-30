#!/usr/bin/env python

"""以脚本模式运行 STM32CubeMX 生成工程代码，可选自动确认迁移、许可和下载对话框。
Run STM32CubeMX in script mode to generate project code, optionally auto-confirming migration,
license and download dialogs.

出现 ST 账号登录对话框时停止生成并报错，不自动登录。
An ST account login dialog stops the generation with an error; login is not automated.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from xr_syntax.i18n import localize_argparse, tr

from libxr.output import configure_logging

LOGGER = logging.getLogger(__name__)

POSITIVE_BUTTON_LABELS = (
    "i agree",
    "accept",
    "yes",
    "ok",
    "continue",
    "download",
    "install",
    "migrate",
    "convert",
    "finish",
    "close",
    "同意",
    "接受",
    "是",
    "确定",
    "继续",
    "下载",
    "安装",
    "迁移",
    "转换",
    "完成",
    "关闭",
)

AGREEMENT_LABELS = (
    "i agree",
    "i accept",
    "accept",
    "agree",
    "同意",
    "接受",
)

DIALOG_KEYWORDS = (
    "migrat",
    "compat",
    "convert",
    "license",
    "agreement",
    "accept",
    "download",
    "install",
    "package",
    "software",
    "firmware",
    "repository",
    "协议",
    "许可",
    "同意",
    "接受",
    "下载",
    "安装",
    "迁移",
    "兼容",
    "转换",
)

STARTUP_DIALOG_KEYWORDS = (
    "user preferences",
    "project manager settings",
    "load project",
    "software packs loading failed",
)

DIALOG_CLASS_KEYWORDS = (
    "sunawtdialog",
    "dialog",
)

PROGRESS_KEYWORDS = (
    "progress",
    "downloading",
    "extracting",
    "unzipping",
    "download file",
    "download paused",
    "download resumed",
    "user cancelled unzip",
    "pause",
    "resume",
    "cancel",
    "解压",
    "下载中",
    "下载暂停",
    "下载恢复",
    "取消",
)

PROGRESS_BUTTON_LABELS = (
    "pause",
    "resume",
    "cancel",
    "暂停",
    "恢复",
    "取消",
)

ACCOUNT_LOGIN_KEYWORDS = (
    "login",
    "log in",
    "sign in",
    "sign-in",
    "st account",
    "my st",
    "myst",
    "username",
    "password",
    "e-mail",
    "email",
    "authentication",
    "登录",
    "登入",
    "账号",
    "帐号",
    "账户",
    "密码",
    "邮箱",
)

DEFAULT_EXPECT_PATHS = ("Core/Inc", "Drivers")
GENERIC_DIALOG_CONFIRM_LIMIT = 2


class DialogBlockedError(RuntimeError):
    """CubeMX 显示无法安全确认的对话框（ST 账号登录对话框）时抛出。
    Raised when CubeMX shows a dialog that cannot be accepted safely, the ST account login
    dialog.
    """


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    """text 含有 keywords 中任一子串时为 True。
    True when text contains any of the keywords as a substring.
    """
    return any(keyword in text for keyword in keywords)


def _is_account_login_text(flat_text: str) -> bool:
    """文本含有账号登录关键词（login、password、登录等）时为 True，不区分大小写。
    True when the text contains an account login keyword such as login, password or 登录,
    ignoring case.
    """
    return _contains_any(flat_text.lower(), ACCOUNT_LOGIN_KEYWORDS)


def _is_progress_text(flat_text: str) -> bool:
    """文本含有下载、解压等进度关键词时为 True，不区分大小写。
    True when the text contains a progress keyword such as downloading or extracting, ignoring
    case.
    """
    return _contains_any(flat_text.lower(), PROGRESS_KEYWORDS)


def _is_explicit_dialog_text(flat_text: str) -> bool:
    """文本含有迁移、许可、下载等对话框关键词或启动对话框关键词时为 True，不区分大小写。
    True when the text contains a dialog keyword such as migration, license or download, or a
    startup dialog keyword, ignoring case.
    """
    lowered = flat_text.lower()
    return _contains_any(lowered, DIALOG_KEYWORDS) or _is_startup_dialog_text(lowered)


def _is_startup_dialog_text(flat_text: str) -> bool:
    """文本含有 CubeMX 启动阶段对话框的关键词（如 user preferences、load project）时为 True，
    不区分大小写。
    True when the text contains a keyword of a CubeMX startup dialog, such as user preferences
    or load project, ignoring case.
    """
    return _contains_any(flat_text.lower(), STARTUP_DIALOG_KEYWORDS)


def _is_dialog_class(class_name: str) -> bool:
    """窗口类名含有 dialog（包括 Java AWT 的 SunAwtDialog）时为 True，不区分大小写。
    True when the window class name contains dialog, including the Java AWT SunAwtDialog,
    ignoring case.
    """
    lowered_class = class_name.lower()
    return any(keyword in lowered_class for keyword in DIALOG_CLASS_KEYWORDS)


def _java_user_state_options() -> list[str]:
    """以 java -jar 启动 CubeMX 时附加的 JVM 参数：user.home 设为当前用户主目录，Java 首选项
    根目录设为其中的 .java。
    JVM options added when CubeMX is started through java -jar: user.home set to the current
    user's home directory and the Java preferences root to .java inside it.
    """
    java_home = os.path.abspath(os.path.expanduser("~"))
    prefs_root = os.path.join(java_home, ".java")
    return [
        f"-Duser.home={java_home}",
        f"-Djava.util.prefs.userRoot={prefs_root}",
    ]


def _can_use_generic_dialog_fallback(
    confirm_counts: dict[int, int],
    window_id: int,
    flat_text: str,
    class_name: str,
) -> bool:
    """窗口能否使用通用的键盘确认：文本或类名须像对话框，且该窗口的尝试次数未达到
    GENERIC_DIALOG_CONFIRM_LIMIT；允许时计入一次尝试。
    Whether a window may get the generic keyboard confirmation: its text or class name must look
    like a dialog and it must have fewer than GENERIC_DIALOG_CONFIRM_LIMIT attempts; an allowed
    attempt is counted.
    """
    if _is_explicit_dialog_text(flat_text) or _is_dialog_class(class_name):
        return _consume_generic_dialog_fallback(confirm_counts, window_id)
    return False


def _consume_generic_dialog_fallback(confirm_counts: dict[int, int], window_id: int) -> bool:
    """为窗口计入一次通用确认尝试；达到 GENERIC_DIALOG_CONFIRM_LIMIT 后返回 False，
    放弃日志只记录一次。
    Count one generic confirmation attempt for a window; once GENERIC_DIALOG_CONFIRM_LIMIT is
    reached, return False and log the give-up message only once.
    """
    count = confirm_counts.get(window_id, 0)
    if count >= GENERIC_DIALOG_CONFIRM_LIMIT:
        if count == GENERIC_DIALOG_CONFIRM_LIMIT:
            LOGGER.info(
                tr(
                    f"Leaving generic CubeMX dialog untouched after {count} keyboard attempts",
                    f"已尝试 {count} 次键盘确认，不再处理这个 CubeMX 通用对话框",
                )
            )
            confirm_counts[window_id] = count + 1
        return False
    confirm_counts[window_id] = count + 1
    return True


def _friendly_path_name(path: str) -> str:
    """路径的末级名称，用于提示信息（'.' 显示为当前目录名）；没有末级名称（如根目录）时为绝对路径。
    The last component of a path for messages, so '.' shows the current folder name; the
    absolute path when there is none, as for a root directory.
    """
    abs_path = os.path.abspath(path)
    base = os.path.basename(abs_path.rstrip(os.sep))
    return base or abs_path


def _resolve_existing_path(path_or_cmd: str) -> str:
    """把路径或命令名解析为已存在路径的绝对路径：先展开 ~ 和环境变量，再查文件系统和 PATH。
    Resolve a path or command name to the absolute path of an existing path: ~ and environment
    variables are expanded, then the file system and PATH are searched.

    Raises:
        FileNotFoundError: 路径不存在，PATH 中也找不到。
            The path does not exist and is not found on PATH.
    """
    expanded = os.path.expandvars(os.path.expanduser(path_or_cmd))
    if os.path.exists(expanded):
        return os.path.abspath(expanded)
    found = shutil.which(expanded)
    if found:
        return os.path.abspath(found)
    raise FileNotFoundError(path_or_cmd)


def _iter_cubemx_candidates() -> Iterable[str]:
    """按顺序给出 STM32CubeMX 的候选位置：先是环境变量 STM32CUBEMX_CMD、CUBEMX_CMD、STM32CUBEMX
    中的非空值，再是当前平台的默认安装位置。
    Yield candidate STM32CubeMX locations in order: the non-empty values of the environment
    variables STM32CUBEMX_CMD, CUBEMX_CMD and STM32CUBEMX, then the platform's default install
    locations.
    """
    env_candidates = (
        os.environ.get("STM32CUBEMX_CMD", ""),
        os.environ.get("CUBEMX_CMD", ""),
        os.environ.get("STM32CUBEMX", ""),
    )
    for value in env_candidates:
        if value:
            yield value

    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("PROGRAMFILES", "")
        candidates = [
            "STM32CubeMX.exe",
            os.path.join(local_appdata, "Programs", "STM32CubeMX", "STM32CubeMX.exe"),
            os.path.join(
                program_files, "STMicroelectronics", "STM32Cube", "STM32CubeMX", "STM32CubeMX.exe"
            ),
        ]
    else:
        home = os.path.expanduser("~")
        candidates = [
            "STM32CubeMX",
            os.path.join(home, "STM32CubeMX", "STM32CubeMX"),
            "/opt/st/stm32cubemx/STM32CubeMX",
            "/usr/local/bin/STM32CubeMX",
        ]

    yield from candidates


def resolve_cubemx_command(explicit_cmd: str = "") -> str:
    """STM32CubeMX 的绝对路径：给出 explicit_cmd 时只解析它，否则取第一个存在的候选位置。
    The absolute path of STM32CubeMX: explicit_cmd alone when given, else the first candidate
    location that exists.

    Raises:
        FileNotFoundError: 找不到 STM32CubeMX。
            STM32CubeMX was not found.
    """
    if explicit_cmd:
        return _resolve_existing_path(explicit_cmd)

    for candidate in _iter_cubemx_candidates():
        try:
            return _resolve_existing_path(candidate)
        except FileNotFoundError:
            continue

    raise FileNotFoundError(
        tr(
            "Unable to locate STM32CubeMX. Pass --cubemx-cmd or set STM32CUBEMX_CMD.",
            "找不到 STM32CubeMX。请传入 --cubemx-cmd 或设置 STM32CUBEMX_CMD。",
        )
    )


def _iter_java_candidates(cubemx_cmd: str) -> Iterable[str]:
    """按顺序给出 Java 的候选位置：环境变量 STM32CUBEMX_JAVA、JAVA_CMD 中的非空值，JAVA_HOME 下的
    java，CubeMX 同目录 jre 中的 java，最后是命令名 java。
    Yield candidate Java locations in order: the non-empty values of the environment variables
    STM32CUBEMX_JAVA and JAVA_CMD, java under JAVA_HOME, java in the jre next to CubeMX, and
    finally the command name java.
    """
    env_candidates = (
        os.environ.get("STM32CUBEMX_JAVA", ""),
        os.environ.get("JAVA_CMD", ""),
    )
    for value in env_candidates:
        if value:
            yield value

    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        yield os.path.join(java_home, "bin", "java.exe" if os.name == "nt" else "java")

    cubemx_dir = os.path.dirname(os.path.abspath(cubemx_cmd))
    bundled_java = os.path.join(cubemx_dir, "jre", "bin", "java.exe" if os.name == "nt" else "java")
    yield bundled_java
    yield "java"


def resolve_java_command(cubemx_cmd: str, java_cmd: str = "") -> str:
    """运行 CubeMX .jar 所用 Java 的绝对路径：给出 java_cmd 时只解析它，否则取第一个存在的候选位置。
    The absolute path of the Java that runs a CubeMX .jar: java_cmd alone when given, else the
    first candidate location that exists.

    Raises:
        FileNotFoundError: 找不到 Java。
            No Java runtime was found.
    """
    if java_cmd:
        return _resolve_existing_path(java_cmd)

    for candidate in _iter_java_candidates(cubemx_cmd):
        try:
            return _resolve_existing_path(candidate)
        except FileNotFoundError:
            continue

    raise FileNotFoundError(
        tr(
            "Unable to locate Java runtime for STM32CubeMX. "
            "Pass --java-cmd or use --launch-mode direct.",
            "找不到运行 STM32CubeMX 所需的 Java。请传入 --java-cmd 或使用 --launch-mode direct。",
        )
    )


def _is_java_archive(cubemx_cmd: str) -> bool:
    """路径的扩展名为 .jar 时为 True，不区分大小写。
    True when the path has the .jar extension, ignoring case.
    """
    return os.path.splitext(cubemx_cmd)[1].lower() == ".jar"


def _format_script_path(path: str) -> str:
    """把路径写成 CubeMX 脚本中的形式：绝对路径，Windows 上用正斜杠，含空白时加双引号。
    Format a path for a CubeMX script: absolute, with forward slashes on Windows, and in double
    quotes when it contains whitespace.
    """
    normalized = os.path.abspath(path)
    if os.name == "nt":
        normalized = normalized.replace("\\", "/")
    if any(ch.isspace() for ch in normalized):
        return f'"{normalized}"'
    return normalized


def build_cubemx_script(ioc_path: str, generate_code_dir: str = "") -> str:
    """CubeMX 脚本文本：加载 .ioc，执行 project generate（给出 generate_code_dir 时改为
    generate code <目录>），最后 exit。
    The CubeMX script text: load the .ioc, run project generate (generate code <dir> when
    generate_code_dir is given), then exit.
    """
    script_lines = [f"config load {_format_script_path(ioc_path)}"]
    if generate_code_dir:
        script_lines.append(f"generate code {_format_script_path(generate_code_dir)}")
    else:
        script_lines.append("project generate")
    script_lines.append("exit")
    return "\n".join(script_lines) + "\n"


def _shell_join(args: Sequence[str]) -> str:
    """把参数拼成一行按 shell 规则引用的命令，用于日志；引用出错时直接以空格连接。
    Join arguments into one shell-quoted command line for logs; plain space-joined when quoting
    fails.
    """
    try:
        import shlex

        return " ".join(shlex.quote(arg) for arg in args)
    except Exception:
        return " ".join(args)


def build_cubemx_command(
    cubemx_cmd: str,
    script_path: str,
    launch_mode: str = "auto",
    java_cmd: str = "",
    silent: bool = False,
) -> list[str]:
    """组成让 STM32CubeMX 执行 script_path 脚本的命令行。
    Build the command line that makes STM32CubeMX run the script at script_path.

    launch_mode 为 java，或为 auto 且 cubemx_cmd 是 .jar 时，用 java -jar 启动；否则直接启动，
    其中 .py 文件用当前 Python 解释器运行。silent 为 True 时追加 -s。
    With launch_mode java, or auto and a .jar cubemx_cmd, CubeMX is started through java -jar;
    otherwise it is started directly, a .py file with the current Python interpreter. silent
    appends -s.

    Raises:
        ValueError: launch_mode 不是 auto、direct、java 之一，或 java 模式下 cubemx_cmd 不是 .jar。
            launch_mode is not auto, direct or java, or java mode is given a cubemx_cmd that is
            not a .jar.
        FileNotFoundError: 需要 java -jar 启动但找不到 Java。
            java -jar is needed but no Java runtime was found.
    """
    launch_mode = launch_mode.lower()
    if launch_mode not in {"auto", "direct", "java"}:
        raise ValueError(
            tr(f"Unsupported launch mode: {launch_mode}", f"不支持的启动方式：{launch_mode}")
        )

    use_java = launch_mode == "java" or (launch_mode == "auto" and _is_java_archive(cubemx_cmd))

    if use_java and not _is_java_archive(cubemx_cmd):
        raise ValueError(
            tr(
                "Java launch mode requires an STM32CubeMX .jar path. "
                "Use --launch-mode direct for STM32CubeMX.exe.",
                "java 启动方式需要 STM32CubeMX 的 .jar 路径；"
                "STM32CubeMX.exe 请使用 --launch-mode direct。",
            )
        )

    command: list[str]
    if use_java:
        resolved_java = resolve_java_command(cubemx_cmd, java_cmd)
        command = [
            resolved_java,
            *_java_user_state_options(),
            "-jar",
            cubemx_cmd,
            "-q",
            script_path,
        ]
    elif cubemx_cmd.lower().endswith(".py"):
        command = [sys.executable, cubemx_cmd, "-q", script_path]
    else:
        command = [cubemx_cmd, "-q", script_path]

    if silent:
        command.append("-s")
    return command


def find_ioc_file(directory: str) -> str | None:
    """目录中按文件名排序的第一个 .ioc 文件的路径；没有时为 None。
    The path of the first .ioc file in the directory by file name order; None when there is none.
    """
    for file_name in sorted(os.listdir(directory)):
        if file_name.endswith(".ioc"):
            return os.path.join(directory, file_name)
    return None


@dataclass
class CubeMXRunResult:
    """一次 STM32CubeMX 运行的命令、脚本路径、标准输出、标准错误、退出码和日志目录。
    The command, script path, stdout, stderr, exit code and log directory of one STM32CubeMX run.

    没有写日志时 log_dir 为空字符串。临时脚本在返回前已删除，此时 script_path 只记录其原位置。
    log_dir is an empty string when no logs were written. A temporary script is deleted before
    the result is returned; script_path then only records where it was.
    """

    command: list[str]
    script_path: str
    stdout: str
    stderr: str
    returncode: int
    log_dir: str = ""


class _BaseDialogController:
    """对话框自动确认控制器的基类；后台线程反复调用 pump_once。
    Base class of the dialog auto-confirmation controllers; a background thread calls pump_once
    repeatedly.
    """

    def pump_once(self) -> None:
        """检查一次 CubeMX 进程的窗口并处理需要确认的对话框；由子类实现。
        Inspect the CubeMX process windows once and handle the dialogs to confirm; implemented by
        subclasses.
        """
        raise NotImplementedError


class _NullDialogController(_BaseDialogController):
    """不做任何处理的控制器，用于无法自动确认对话框的环境。
    A controller that does nothing, used where dialogs cannot be auto-confirmed.
    """

    def pump_once(self) -> None:
        """不做任何处理。
        Do nothing.
        """
        return


class _WindowsDialogController(_BaseDialogController):
    """Windows 上通过 user32 找到 CubeMX 进程树的窗口，勾选同意项并点击肯定按钮或用键盘确认。
    On Windows, find the windows of the CubeMX process tree through user32, tick agreement boxes
    and click a positive button or confirm with the keyboard.
    """

    BM_CLICK = 0x00F5
    BM_GETCHECK = 0x00F0
    BST_CHECKED = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    SW_RESTORE = 9
    VK_TAB = 0x09
    VK_RETURN = 0x0D
    VK_SPACE = 0x20
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101

    def __init__(self, process_id: int):
        """绑定 user32 和 kernel32，记录要监视的 CubeMX 进程号。
        Bind user32 and kernel32 and record the id of the CubeMX process to watch.
        """
        from ctypes import wintypes

        self.process_id = process_id
        self.wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.CreateToolhelp32Snapshot.restype = self.wintypes.HANDLE
        self.kernel32.Process32FirstW.restype = self.wintypes.BOOL
        self.kernel32.Process32NextW.restype = self.wintypes.BOOL
        self._last_action: dict[int, float] = {}
        self._generic_confirm_count: dict[int, int] = {}

    def pump_once(self) -> None:
        """检查一次 CubeMX 进程树的可见顶层窗口并确认相关对话框；刚确认过的窗口 2 秒内跳过。
        Inspect the visible top-level windows of the CubeMX process tree once and confirm the
        relevant dialogs; a window confirmed less than 2 seconds ago is skipped.

        Raises:
            DialogBlockedError: 出现 ST 账号登录对话框。
                An ST account login dialog is shown.
        """
        hwnds = self._enum_windows()
        for hwnd in hwnds:
            title = self._window_text(hwnd)
            class_name = self._class_name(hwnd)
            child_items = self._child_items(hwnd)
            flat_text = self._flatten_window_text(title, class_name, child_items)
            if _is_account_login_text(flat_text):
                raise DialogBlockedError(_st_login_blocked_message())
            if not self._looks_relevant(flat_text, class_name):
                continue
            if self._acted_recently(hwnd):
                continue
            if self._accept_window(hwnd, class_name, child_items):
                self._last_action[hwnd] = time.time()

    def _enum_windows(self) -> list[int]:
        """CubeMX 进程树中所有可见顶层窗口的句柄。
        The handles of all visible top-level windows of the CubeMX process tree.
        """
        hwnds: list[int] = []
        process_ids = self._related_process_ids()
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, self.wintypes.HWND, self.wintypes.LPARAM)

        def callback(hwnd: int, _lparam: int) -> bool:
            """EnumWindows 回调：收集属于这些进程的可见窗口，并继续枚举。
            EnumWindows callback: collect the visible windows owned by these processes and
            continue the enumeration.
            """
            if not self.user32.IsWindowVisible(hwnd):
                return True
            pid = self.wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in process_ids:
                hwnds.append(hwnd)
            return True

        self.user32.EnumWindows(enum_proc(callback), 0)
        return hwnds

    def _related_process_ids(self) -> set:
        """CubeMX 进程及其全部子孙进程的进程号；进程快照失败时只含 CubeMX 进程本身。
        The ids of the CubeMX process and all its descendants; only the CubeMX process itself when
        the process snapshot fails.
        """
        ids = {self.process_id}

        class ProcessEntry(ctypes.Structure):
            """Toolhelp32 的 PROCESSENTRY32W 结构。
            The Toolhelp32 PROCESSENTRY32W structure.
            """

            _fields_ = [
                ("dwSize", self.wintypes.DWORD),
                ("cntUsage", self.wintypes.DWORD),
                ("th32ProcessID", self.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", self.wintypes.DWORD),
                ("cntThreads", self.wintypes.DWORD),
                ("th32ParentProcessID", self.wintypes.DWORD),
                ("pcPriClassBase", self.wintypes.LONG),
                ("dwFlags", self.wintypes.DWORD),
                ("szExeFile", self.wintypes.WCHAR * 260),
            ]

        snapshot = self.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot in (-1, self.wintypes.HANDLE(-1).value):
            return ids

        parent_by_pid: dict[int, int] = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        except Exception:
            pass
        finally:
            self.kernel32.CloseHandle(snapshot)

        queue = [self.process_id]
        while queue:
            parent = queue.pop(0)
            for pid, ppid in parent_by_pid.items():
                if ppid == parent and pid not in ids:
                    ids.add(pid)
                    queue.append(pid)
        return ids

    def _child_items(self, hwnd: int) -> list[tuple[int, str, str]]:
        """窗口全部子窗口（含嵌套的子窗口）的 (句柄, 类名, 文本) 列表。
        The (handle, class name, text) of every child window of a window, nested ones included.
        """
        items: list[tuple[int, str, str]] = []
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, self.wintypes.HWND, self.wintypes.LPARAM)

        def callback(child_hwnd: int, _lparam: int) -> bool:
            """EnumChildWindows 回调：记录子窗口，并继续枚举。
            EnumChildWindows callback: record the child window and continue the enumeration.
            """
            items.append((child_hwnd, self._class_name(child_hwnd), self._window_text(child_hwnd)))
            return True

        self.user32.EnumChildWindows(hwnd, enum_proc(callback), 0)
        return items

    def _window_text(self, hwnd: int) -> str:
        """窗口标题或控件文字，去掉首尾空白；没有文字时为空字符串。
        The window title or control text, stripped; an empty string when there is none.
        """
        length = self.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()

    def _class_name(self, hwnd: int) -> str:
        """窗口类名（最多 255 个字符）。
        The window class name, up to 255 characters.
        """
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def _flatten_window_text(
        self, title: str, class_name: str, child_items: Sequence[tuple[int, str, str]]
    ) -> str:
        """把标题、类名以及各子窗口的类名和文字按行连成一段小写文本，用于关键词匹配。
        Join the title, the class name and each child window's class and text line by line
        into one lowercase text for keyword matching.
        """
        parts = [title, class_name]
        for _, child_class, text in child_items:
            parts.extend((child_class, text))
        return "\n".join(parts).lower()

    def _looks_relevant(self, flat_text: str, class_name: str) -> bool:
        """窗口类名像对话框，或文本含有对话框关键词时为 True。
        True when the window class looks like a dialog or the text contains a dialog keyword.
        """
        if _is_dialog_class(class_name):
            return True
        return _is_explicit_dialog_text(flat_text)

    def _can_use_keyboard_fallback(self, hwnd: int, flat_text: str, class_name: str) -> bool:
        """该窗口能否使用通用的键盘确认；允许时计入一次尝试。
        Whether the window may get the generic keyboard confirmation; an allowed attempt is
        counted.
        """
        return _can_use_generic_dialog_fallback(
            self._generic_confirm_count, hwnd, flat_text, class_name
        )

    def _is_progress_window(
        self, flat_text: str, child_items: Sequence[tuple[int, str, str]]
    ) -> bool:
        """文本含有进度关键词，或某个子窗口的文字含有暂停、恢复、取消等按钮文字时为 True。
        True when the text contains a progress keyword or a child window's text contains a
        pause, resume or cancel label.
        """
        if any(keyword in flat_text for keyword in PROGRESS_KEYWORDS):
            return True
        for _, _, text in child_items:
            lowered = text.lower()
            if any(label == lowered or label in lowered for label in PROGRESS_BUTTON_LABELS):
                return True
        return False

    def _acted_recently(self, hwnd: int) -> bool:
        """距上次确认该窗口不到 2 秒时为 True。
        True when the window was confirmed less than 2 seconds ago.
        """
        last = self._last_action.get(hwnd, 0.0)
        return (time.time() - last) < 2.0

    def _accept_window(
        self, hwnd: int, class_name: str, child_items: Sequence[tuple[int, str, str]]
    ) -> bool:
        """确认一个对话框：勾选未勾选的同意复选框，点击第一个文字含肯定词的子窗口；没有这样的
        子窗口时，在次数限制内用键盘确认。进度窗口不处理，以免打断下载或解压。
        Confirm a dialog: tick unticked agreement checkboxes and click the first child window
        whose text has a positive label; without one, fall back to the keyboard within the
        attempt limit. Progress windows are left alone so downloads and extraction go on.

        Returns:
            执行了点击或键盘确认时为 True。
            True when a click or keyboard confirmation was performed.

        Raises:
            DialogBlockedError: 窗口是 ST 账号登录对话框。
                The window is an ST account login dialog.
        """
        flat_text = self._flatten_window_text(self._window_text(hwnd), class_name, child_items)
        if _is_account_login_text(flat_text):
            raise DialogBlockedError(_st_login_blocked_message())
        if self._is_progress_window(flat_text, child_items):
            LOGGER.info(
                tr(
                    "Skipping CubeMX progress window to avoid interrupting downloads/extraction",
                    "跳过 CubeMX 进度窗口，以免打断下载或解压",
                )
            )
            return False

        for child_hwnd, child_class, text in child_items:
            lowered = text.lower()
            if not any(label in lowered for label in AGREEMENT_LABELS):
                continue
            if child_class != "Button":
                continue
            checked = self.user32.SendMessageW(child_hwnd, self.BM_GETCHECK, 0, 0)
            if checked != self.BST_CHECKED:
                self.user32.SendMessageW(child_hwnd, self.BM_CLICK, 0, 0)
                LOGGER.info(
                    tr(
                        f"Auto-confirmed agreement checkbox: {text}",
                        f"已自动勾选同意复选框：{text}",
                    )
                )

        for child_hwnd, _, text in child_items:
            lowered = text.lower()
            if any(label in lowered for label in POSITIVE_BUTTON_LABELS):
                self.user32.SendMessageW(child_hwnd, self.BM_CLICK, 0, 0)
                LOGGER.info(
                    tr(
                        f"Auto-confirmed CubeMX dialog button: {text}",
                        f"已自动点击 CubeMX 对话框按钮：{text}",
                    )
                )
                return True

        if self._can_use_keyboard_fallback(hwnd, flat_text, class_name):
            self._confirm_awt_dialog(hwnd)
            LOGGER.info(
                tr(
                    "Auto-confirmed CubeMX Java dialog with keyboard fallback",
                    "已用键盘备用方式自动确认 CubeMX Java 对话框",
                )
            )
            return True

        LOGGER.info(
            tr(
                "Relevant CubeMX window detected but no safe positive button was found; "
                "leaving it untouched",
                "检测到相关的 CubeMX 窗口，但没有找到可安全点击的确认按钮；不做处理",
            )
        )
        return False

    def _tap_key(self, virtual_key: int) -> None:
        """用 keybd_event 按下并松开一个虚拟键。
        Press and release one virtual key with keybd_event.
        """
        self.user32.keybd_event(virtual_key, 0, 0, 0)
        time.sleep(0.03)
        self.user32.keybd_event(virtual_key, 0, self.KEYEVENTF_KEYUP, 0)
        time.sleep(0.08)

    def _confirm_awt_dialog(self, hwnd: int) -> None:
        """恢复窗口并置于前台，再依次按空格、Tab、回车。
        Restore the window, bring it to the foreground, then press Space, Tab and Enter.
        """
        self.user32.ShowWindow(hwnd, self.SW_RESTORE)
        self.user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        # Swing dialogs often expose no native Button children. Space handles an
        # initial license checkbox focus; Tab/Enter then activates the default
        # positive action on migration/download/license prompts.
        for key in (self.VK_SPACE, self.VK_TAB, self.VK_RETURN):
            self._tap_key(key)


class _LinuxX11DialogController(_BaseDialogController):
    """Linux 上通过 X11（python-xlib）找到 CubeMX 进程树的窗口，用模拟按键和点击确认对话框。
    On Linux, find the windows of the CubeMX process tree through X11 (python-xlib) and confirm
    dialogs with synthetic key presses and clicks.
    """

    def __init__(self, process_id: int):
        """连接 X 显示，取得所需的窗口属性 atom，记录要监视的 CubeMX 进程号。
        Connect to the X display, intern the window property atoms needed and record the id of
        the CubeMX process to watch.

        Raises:
            ImportError: 没有安装 python-xlib。
                python-xlib is not installed.
        """
        from Xlib import XK, X, display  # type: ignore
        from Xlib.ext import xtest  # type: ignore

        self.X = X
        self.XK = XK
        self.display_module = display
        self.xtest = xtest
        self.process_id = process_id
        self.display = display.Display()
        self.root = self.display.screen().root
        self.pid_atom = self.display.intern_atom("_NET_WM_PID")
        self.name_atom = self.display.intern_atom("_NET_WM_NAME")
        self.utf8_atom = self.display.intern_atom("UTF8_STRING")
        self.class_atom = self.display.intern_atom("WM_CLASS")
        self._last_action: dict[int, float] = {}
        self._generic_confirm_count: dict[int, int] = {}

    def pump_once(self) -> None:
        """检查一次 X11 窗口树中属于 CubeMX 进程树的窗口，用按键序列确认相关对话框。
        Inspect the X11 windows of the CubeMX process tree once and confirm the relevant dialogs
        with a key sequence.

        只按窗口标题和类名判断；进度窗口不处理，刚确认过的窗口 3 秒内跳过，每个窗口最多尝试
        GENERIC_DIALOG_CONFIRM_LIMIT 次。
        Only the window title and class name are used; progress windows are left alone, a window
        confirmed less than 3 seconds ago is skipped, and each window gets at most
        GENERIC_DIALOG_CONFIRM_LIMIT attempts.

        Raises:
            DialogBlockedError: 出现 ST 账号登录对话框。
                An ST account login dialog is shown.
        """
        process_ids = self._related_process_ids()
        for window in self._iter_windows(self.root):
            if self._window_pid(window) not in process_ids:
                continue
            title = self._window_title(window)
            class_name = self._window_class(window)
            flat_text = "\n".join((title, class_name)).lower()
            if _is_account_login_text(flat_text):
                raise DialogBlockedError(_st_login_blocked_message())
            if not self._looks_relevant(flat_text, class_name):
                continue
            if self._is_progress_window(flat_text):
                LOGGER.info(
                    tr(
                        "Skipping CubeMX progress window to avoid interrupting "
                        "downloads/extraction",
                        "跳过 CubeMX 进度窗口，以免打断下载或解压",
                    )
                )
                continue
            if self._acted_recently(window.id):
                continue
            if not self._can_use_keyboard_fallback(window.id, flat_text, class_name):
                continue
            self._activate_window(window)
            self._confirm_window(window)
            self._last_action[window.id] = time.time()

    def _iter_windows(self, window):
        """深度优先给出窗口本身及其全部子孙窗口；查询不到子窗口的窗口不再向下展开。
        Yield the window and all its descendants depth-first; a window whose children cannot be
        queried is not expanded.
        """
        yield window
        try:
            children = window.query_tree().children
        except Exception:
            return
        for child in children:
            yield from self._iter_windows(child)

    def _related_process_ids(self) -> set:
        """CubeMX 进程及其全部子孙进程的进程号，父子关系从 /proc/<pid>/stat 读取。
        The ids of the CubeMX process and all its descendants, with the parent ids read from
        /proc/<pid>/stat.
        """
        ids = {self.process_id}
        queue = [self.process_id]
        while queue:
            parent = queue.pop(0)
            try:
                for entry in os.listdir("/proc"):
                    if not entry.isdigit():
                        continue
                    stat_path = os.path.join("/proc", entry, "stat")
                    try:
                        with open(stat_path, encoding="utf-8", errors="ignore") as stat_file:
                            fields = stat_file.read().split()
                    except OSError:
                        continue
                    if len(fields) < 4:
                        continue
                    try:
                        pid = int(fields[0])
                        ppid = int(fields[3])
                    except ValueError:
                        continue
                    if ppid == parent and pid not in ids:
                        ids.add(pid)
                        queue.append(pid)
            except OSError:
                break
        return ids

    def _window_pid(self, window) -> int:
        """窗口 _NET_WM_PID 属性中的进程号；没有该属性或读取失败时为 -1。
        The process id in the window's _NET_WM_PID property; -1 when it is missing or unreadable.
        """
        try:
            prop = window.get_full_property(self.pid_atom, self.X.AnyPropertyType)
            if prop and prop.value:
                return int(prop.value[0])
        except Exception:
            return -1
        return -1

    def _window_title(self, window) -> str:
        """窗口标题：先读 UTF-8 的 _NET_WM_NAME，再读 WM_NAME；都读不到时为空字符串。
        The window title: the UTF-8 _NET_WM_NAME first, then WM_NAME; an empty string when
        neither can be read.
        """
        try:
            prop = window.get_full_property(self.name_atom, self.utf8_atom)
            if prop and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return str(value)
        except Exception:
            pass
        try:
            name = window.get_wm_name()
            return name or ""
        except Exception:
            return ""

    def _window_class(self, window) -> str:
        """窗口的 WM_CLASS（实例名和类名，以换行分隔）；读不到时为空字符串。
        The window's WM_CLASS, instance and class name separated by a newline; an empty string
        when it cannot be read.
        """
        try:
            value = window.get_wm_class()
            if value:
                return "\n".join(str(item) for item in value if item)
        except Exception:
            pass
        try:
            prop = window.get_full_property(self.class_atom, self.X.AnyPropertyType)
            if prop and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.replace(b"\x00", b"\n").decode("utf-8", errors="ignore")
                return str(value)
        except Exception:
            pass
        return ""

    def _looks_relevant(self, flat_text: str, class_name: str) -> bool:
        """窗口类名像对话框，或文本含有对话框关键词时为 True。
        True when the window class looks like a dialog or the text contains a dialog keyword.
        """
        if _is_dialog_class(class_name):
            return True
        return _is_explicit_dialog_text(flat_text)

    def _can_use_keyboard_fallback(self, window_id: int, flat_text: str, class_name: str) -> bool:
        """该窗口能否使用通用的键盘确认；允许时计入一次尝试。
        Whether the window may get the generic keyboard confirmation; an allowed attempt is
        counted.
        """
        return _can_use_generic_dialog_fallback(
            self._generic_confirm_count, window_id, flat_text, class_name
        )

    def _is_progress_window(self, flat_text: str) -> bool:
        """文本含有下载、解压等进度关键词时为 True。
        True when the text contains a progress keyword such as downloading or extracting.
        """
        return _is_progress_text(flat_text)

    def _acted_recently(self, window_id: int) -> bool:
        """距上次确认该窗口不到 3 秒时为 True。
        True when the window was confirmed less than 3 seconds ago.
        """
        last = self._last_action.get(window_id, 0.0)
        return (time.time() - last) < 3.0

    def _focus_first_viewable_window(self, window) -> bool:
        """把输入焦点交给窗口树中第一个可见（viewable）的窗口；成功时为 True。
        Give input focus to the first viewable window in the window's tree; True on success.
        """
        for candidate in self._iter_windows(window):
            try:
                attributes = candidate.get_attributes()
                if attributes.map_state != self.X.IsViewable:
                    continue
                candidate.set_input_focus(self.X.RevertToParent, self.X.CurrentTime)
                self.display.sync()
                return True
            except Exception:
                continue
        return False

    def _activate_window(self, window) -> bool:
        """把窗口提到最上层并设置输入焦点；无法设置焦点时为 False。
        Raise the window to the top and give it input focus; False when focus cannot be set.
        """
        try:
            window.configure(stack_mode=self.X.Above)
            self.display.sync()
        except Exception:
            pass

        if self._focus_first_viewable_window(window):
            return True

        try:
            window.set_input_focus(self.X.RevertToParent, self.X.CurrentTime)
            self.display.sync()
            return True
        except Exception:
            return False

    def _window_abs_geometry(self, window) -> tuple[int, int, int, int] | None:
        """窗口相对根窗口的位置和尺寸 (x, y, 宽, 高)；读取失败时为 None。
        The window position and size relative to the root window as (x, y, width, height); None
        when they cannot be read.
        """
        try:
            geometry = window.get_geometry()
            parent = window.query_tree().parent
            if parent is None:
                return int(geometry.x), int(geometry.y), int(geometry.width), int(geometry.height)
            translated = parent.translate_coords(self.root, geometry.x, geometry.y)
            return int(translated.x), int(translated.y), int(geometry.width), int(geometry.height)
        except Exception:
            return None

    def _click_default_dialog_button(self, window) -> bool:
        """在窗口底边以上 24 像素的水平中点模拟一次左键点击，即底部按钮行的常见默认按钮位置。
        Simulate a left click at the horizontal center, 24 pixels above the bottom edge of the
        window, where the default button of the bottom button row usually is.

        Returns:
            点击成功时为 True；读不到位置、窗口小于 80×60 像素或点击失败时为 False。
            True when the click was sent; False when the position cannot be read, the window
            is smaller than 80×60 pixels or the click fails.
        """
        geometry = self._window_abs_geometry(window)
        if geometry is None:
            return False
        x, y, width, height = geometry
        if width < 80 or height < 60:
            return False

        # Swing/AWT dialogs often do not expose native button text through X11.
        # The positive action is conventionally centered in the bottom button row.
        click_x = x + width // 2
        click_y = y + max(1, height - 24)
        try:
            self.xtest.fake_input(self.display, self.X.MotionNotify, x=click_x, y=click_y)
            self.display.sync()
            time.sleep(0.03)
            self.xtest.fake_input(self.display, self.X.ButtonPress, 1)
            self.xtest.fake_input(self.display, self.X.ButtonRelease, 1)
            self.display.sync()
            LOGGER.info(
                tr(
                    f"Auto-clicked CubeMX dialog default button at {click_x},{click_y}",
                    f"已自动点击 CubeMX 对话框默认按钮位置 {click_x},{click_y}",
                )
            )
            return True
        except Exception as error:
            LOGGER.debug(f"CubeMX X11 default-button click failed: {error}")
            return False

    def _tap(
        self, key_name: str, alt: bool = False, shift: bool = False, control: bool = False
    ) -> None:
        """用 XTest 按下并松开一个按键，可同时按住 Alt、Shift、Ctrl；按键名没有对应键码时
        不做任何事。
        Press and release one key through XTest, optionally holding Alt, Shift or Control;
        nothing happens when the key name has no keycode.
        """
        keycode = self.display.keysym_to_keycode(self.XK.string_to_keysym(key_name))
        if not keycode:
            return
        altcode = self.display.keysym_to_keycode(self.XK.string_to_keysym("Alt_L"))
        shiftcode = self.display.keysym_to_keycode(self.XK.string_to_keysym("Shift_L"))
        controlcode = self.display.keysym_to_keycode(self.XK.string_to_keysym("Control_L"))
        if alt and altcode:
            self.xtest.fake_input(self.display, self.X.KeyPress, altcode)
        if shift and shiftcode:
            self.xtest.fake_input(self.display, self.X.KeyPress, shiftcode)
        if control and controlcode:
            self.xtest.fake_input(self.display, self.X.KeyPress, controlcode)
        self.xtest.fake_input(self.display, self.X.KeyPress, keycode)
        self.xtest.fake_input(self.display, self.X.KeyRelease, keycode)
        if control and controlcode:
            self.xtest.fake_input(self.display, self.X.KeyRelease, controlcode)
        if shift and shiftcode:
            self.xtest.fake_input(self.display, self.X.KeyRelease, shiftcode)
        if alt and altcode:
            self.xtest.fake_input(self.display, self.X.KeyRelease, altcode)
        self.display.sync()
        time.sleep(0.05)

    def _confirm_window(self, window) -> None:
        """依次发送回车、空格、Tab、回车和 Alt+O、Alt+Y、Alt+I、Alt+A，再点击底部默认按钮位置。
        Send Return, Space, Tab, Return and Alt+O, Alt+Y, Alt+I, Alt+A in order, then click where
        the default bottom button usually is.
        """
        for key_name, alt in (
            ("Return", False),
            ("space", False),
            ("Tab", False),
            ("Return", False),
            ("o", True),
            ("y", True),
            ("i", True),
            ("a", True),
        ):
            self._tap(key_name, alt=alt)
        clicked = self._click_default_dialog_button(window)
        suffix = tr(" and default-button click", "，并点击了默认按钮位置") if clicked else ""
        LOGGER.info(
            tr(
                f"Auto-confirmed CubeMX dialog with X11 key sequence{suffix}",
                f"已用 X11 按键序列自动确认 CubeMX 对话框{suffix}",
            )
        )


def _st_login_blocked_message() -> str:
    """CubeMX 要求 ST 账号登录时的错误信息：先在本机的 CubeMX 中手动登录并安装所需固件包，
    再重新生成。
    The error message for an ST account login request: sign in and install the required firmware
    packages in CubeMX on this machine by hand, then generate again.
    """
    return tr(
        "STM32CubeMX requested ST account login. This tool does not automate CubeMX login or "
        "state setup; open CubeMX on this machine, sign in, install required firmware packages, "
        "then run generation again.",
        "STM32CubeMX 要求登录 ST 账号。本工具不会自动登录 CubeMX，也不会自动配置它的状态；"
        "请在本机打开 CubeMX，登录并安装所需的固件包，然后重新生成。",
    )


def create_dialog_controller(
    process_id: int,
) -> _BaseDialogController:
    """为 CubeMX 进程创建当前平台的对话框控制器。
    Create the dialog controller of the current platform for a CubeMX process.

    Windows 上使用 user32；其他平台需要 DISPLAY 和 python-xlib，缺少其一或 X11 控制器启动失败时
    记录警告并返回不做任何处理的控制器。
    Windows uses user32; elsewhere DISPLAY and python-xlib are required, and without either, or
    when the X11 controller fails to start, a warning is logged and a controller that does nothing
    is returned.
    """
    if os.name == "nt":
        return _WindowsDialogController(process_id)
    if not os.environ.get("DISPLAY"):
        LOGGER.warning(
            tr(
                "CubeMX auto-confirm is enabled but DISPLAY is not set; "
                "dialog automation is disabled.",
                "已启用 CubeMX 自动确认，但没有设置 DISPLAY；对话框自动处理已关闭。",
            )
        )
        return _NullDialogController()
    try:
        return _LinuxX11DialogController(process_id)
    except ImportError:
        LOGGER.warning(
            tr(
                "CubeMX auto-confirm on Linux requires python-xlib. "
                "Install it or disable --auto-confirm.",
                "在 Linux 上自动确认 CubeMX 对话框需要 python-xlib。"
                "请安装它，或不使用 --auto-confirm。",
            )
        )
        return _NullDialogController()
    except Exception as error:
        LOGGER.warning(
            tr(
                f"CubeMX auto-confirm could not start on Linux: {error}",
                f"无法在 Linux 上启动 CubeMX 自动确认：{error}",
            )
        )
        return _NullDialogController()


class _DialogWatchThread(threading.Thread):
    """按固定间隔调用对话框控制器的后台守护线程；遇到无法确认的对话框时记录错误并停止。
    A background daemon thread that calls the dialog controller at a fixed interval; a dialog
    that cannot be confirmed is recorded as the error and stops it.
    """

    def __init__(
        self,
        process_id: int,
        stop_event: threading.Event,
        poll_interval: float = 0.5,
    ):
        """为 process_id 创建对话框控制器；stop_event 置位时线程结束，poll_interval 为
        轮询间隔（秒）。
        Create the dialog controller for process_id; the thread ends when stop_event is set, and
        poll_interval is the polling interval in seconds.
        """
        super().__init__(daemon=True)
        self.controller = create_dialog_controller(process_id)
        self.stop_event = stop_event
        self.poll_interval = poll_interval
        self.error: BaseException | None = None

    def run(self) -> None:
        """反复调用 pump_once 直到 stop_event 置位；DialogBlockedError 存入 error 并置位
        stop_event，其他异常只记录警告。
        Call pump_once until stop_event is set; a DialogBlockedError is stored in error and sets
        stop_event, other exceptions are only logged as warnings.
        """
        while not self.stop_event.is_set():
            try:
                self.controller.pump_once()
            except DialogBlockedError as error:
                self.error = error
                self.stop_event.set()
                break
            except Exception as error:
                LOGGER.warning(
                    tr(
                        f"CubeMX dialog watcher error: {error}",
                        f"CubeMX 对话框监视线程出错：{error}",
                    )
                )
            self.stop_event.wait(self.poll_interval)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """结束进程及其子进程：Windows 上执行 taskkill /T /F；其他平台向进程组发送 SIGTERM，3 秒后仍未
    退出则发送 SIGKILL。
    Terminate a process and its children: taskkill /T /F on Windows; elsewhere SIGTERM to the
    process group, then SIGKILL when it has not exited after 3 seconds.

    上述操作无法执行时只结束该进程本身；进程已退出时不做任何事。
    When that cannot be done, only the process itself is killed; an exited process is left alone.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except Exception:
            pass
    elif hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
            except Exception:
                pass
        except Exception:
            pass
    process.kill()


def _tail_text(text: str, lines: int = 40) -> str:
    """文本的最后 lines 行（默认 40 行），用于错误信息。
    The last lines of a text, 40 by default, for error messages.
    """
    text_lines = text.splitlines()
    return "\n".join(text_lines[-lines:])


def _write_text_file(path: str, content: str) -> None:
    """以 UTF-8 和 LF 换行写入文本文件，覆盖已有内容。
    Write a text file as UTF-8 with LF line endings, replacing any existing content.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)


def _prepare_script_path(project_dir: str, script_path: str, keep_script: bool) -> tuple[str, bool]:
    """决定 CubeMX 脚本的写入位置。
    Choose where the CubeMX script is written.

    Returns:
        (脚本路径, 运行后是否删除)：给出 script_path 时用它；keep_script 时为工程目录中的
        cubemx_generate.txt；否则为在工程目录中新建的临时文件，运行后删除。
        (script path, whether to delete it after the run): script_path when given;
        cubemx_generate.txt in the project directory with keep_script; otherwise a new
        temporary file in the project directory, deleted after the run.
    """
    if script_path:
        return os.path.abspath(script_path), False
    if keep_script:
        return os.path.join(project_dir, "cubemx_generate.txt"), False

    fd, name = tempfile.mkstemp(prefix="cubemx_generate_", suffix=".txt", dir=project_dir)
    os.close(fd)
    return name, True


def _normalize_expect_paths(project_dir: str, expect_paths: Sequence[str]) -> list[str]:
    """把期望路径转为绝对路径，相对路径以工程目录为基准。
    Make the expected paths absolute, resolving relative ones against the project directory.
    """
    resolved = []
    for path in expect_paths:
        if os.path.isabs(path):
            resolved.append(os.path.abspath(path))
        else:
            resolved.append(os.path.abspath(os.path.join(project_dir, path)))
    return resolved


def generate_cubemx_project(
    project_dir: str,
    ioc_file: str = "",
    cubemx_cmd: str = "",
    java_cmd: str = "",
    launch_mode: str = "auto",
    generate_code_dir: str = "",
    expect_paths: Sequence[str] | None = None,
    log_dir: str = "",
    script_path: str = "",
    keep_script: bool = False,
    silent: bool = False,
    auto_confirm: bool = False,
    timeout: int = 1200,
) -> CubeMXRunResult:
    """以脚本模式运行 STM32CubeMX 生成工程，再检查期望的输出路径是否存在。
    Generate a project by running STM32CubeMX in script mode, then check that the expected output
    paths exist.

    没有 ioc_file 时使用工程目录中按文件名排序的第一个 .ioc。给出 log_dir 时在其中写入脚本、命令行、
    标准输出和标准错误。auto_confirm 为 True 时由后台线程自动确认对话框。
    Without ioc_file, the first .ioc in the project directory by file name is used. With log_dir,
    the script, the command line, stdout and stderr are written there. With auto_confirm, a
    background thread confirms dialogs.

    Args:
        launch_mode: auto、direct 或 java，含义见 build_cubemx_command。
            auto, direct or java, as described in build_cubemx_command.
        generate_code_dir: 给出时脚本执行 generate code <目录>，而不是 project generate。
            When given, the script runs generate code <dir> instead of project generate.
        expect_paths: 生成后必须存在的路径，相对路径以工程目录为基准；None 时为 Core/Inc
            和 Drivers。
            Paths that must exist after generation, relative ones resolved against the project
            directory; Core/Inc and Drivers when None.
        script_path, keep_script: 脚本位置，见 _prepare_script_path；默认用运行后删除的临时文件。
            Where the script goes, see _prepare_script_path; a temporary file deleted after the
            run by default.
        silent: 为 True 时向 STM32CubeMX 传入 -s。
            Pass -s to STM32CubeMX when True.
        timeout: CubeMX 运行的时限（秒），超时后结束整个进程树。
            The CubeMX time limit in seconds; the whole process tree is terminated when it expires.

    Raises:
        FileNotFoundError: 找不到工程目录、.ioc 文件、STM32CubeMX 或 Java。
            The project directory, the .ioc file, STM32CubeMX or Java was not found.
        ValueError: launch_mode 无效，或 java 模式下 CubeMX 路径不是 .jar。
            launch_mode is invalid, or java mode is given a CubeMX path that is not a .jar.
        TimeoutError: CubeMX 在 timeout 秒内没有结束。
            CubeMX did not finish within timeout seconds.
        RuntimeError: 出现 ST 账号登录对话框、CubeMX 以非零退出码结束，或期望路径不存在。
            An ST account login dialog appeared, CubeMX exited with a non-zero code, or an
            expected path is missing.
    """
    project_dir = os.path.abspath(project_dir)
    if not os.path.isdir(project_dir):
        raise FileNotFoundError(
            tr(f"Project directory not found: {project_dir}", f"找不到工程目录：{project_dir}")
        )

    ioc_path = os.path.abspath(ioc_file) if ioc_file else find_ioc_file(project_dir)
    if not ioc_path:
        project_name = _friendly_path_name(project_dir)
        raise FileNotFoundError(
            tr(
                f"No .ioc file found in {project_name}",
                f"{project_name} 中没有 .ioc 文件",
            )
        )

    resolved_cubemx_cmd = resolve_cubemx_command(cubemx_cmd)
    actual_script_path, should_cleanup_script = _prepare_script_path(
        project_dir, script_path, keep_script
    )
    script_text = build_cubemx_script(ioc_path, generate_code_dir)
    _write_text_file(actual_script_path, script_text)

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        _write_text_file(os.path.join(log_dir, "cubemx_generate.txt"), script_text)

    command = build_cubemx_command(
        resolved_cubemx_cmd,
        actual_script_path,
        launch_mode=launch_mode,
        java_cmd=java_cmd,
        silent=silent,
    )
    command_line = _shell_join(command)
    LOGGER.info(tr(f"Running CubeMX command: {command_line}", f"运行 CubeMX 命令：{command_line}"))

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    with contextlib.ExitStack() as logs:
        stdout_handle = None
        stderr_handle = None
        if log_dir:
            stdout_handle = logs.enter_context(
                open(
                    os.path.join(log_dir, "cubemx_stdout.log"), "w", encoding="utf-8", newline="\n"
                )
            )
            stderr_handle = logs.enter_context(
                open(
                    os.path.join(log_dir, "cubemx_stderr.log"), "w", encoding="utf-8", newline="\n"
                )
            )
            _write_text_file(
                os.path.join(log_dir, "cubemx_command.txt"), _shell_join(command) + "\n"
            )

        def consume_stream(stream, sink: list[str], handle) -> None:
            """逐行读取子进程的输出流存入 sink，给出 handle 时同时写入日志文件；读完后关闭流。
            Read a child process stream line by line into sink, also writing each line to handle
            when given; the stream is closed at the end.
            """
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
                    if handle is not None:
                        handle.write(line)
                        handle.flush()
            finally:
                stream.close()

        try:
            # CubeMX path is resolved before this point and arguments are passed as
            # a list with shell disabled, so project paths cannot be shell-expanded.
            popen_kwargs = {}
            if os.name != "nt":
                popen_kwargs["start_new_session"] = True

            process = subprocess.Popen(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
                command,
                cwd=project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                bufsize=1,
                shell=False,
                **popen_kwargs,
            )
        except Exception:
            if should_cleanup_script:
                with contextlib.suppress(OSError):
                    os.remove(actual_script_path)
            raise

        stop_event = threading.Event()
        watch_thread = None
        if auto_confirm:
            watch_thread = _DialogWatchThread(process.pid, stop_event)
            watch_thread.start()

        stdout_thread = threading.Thread(
            target=consume_stream, args=(process.stdout, stdout_lines, stdout_handle), daemon=True
        )
        stderr_thread = threading.Thread(
            target=consume_stream, args=(process.stderr, stderr_lines, stderr_handle), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()

        timeout_error: TimeoutError | None = None
        dialog_error: BaseException | None = None
        deadline = time.time() + timeout
        try:
            while True:
                if watch_thread is not None and watch_thread.error is not None:
                    dialog_error = watch_thread.error
                    _terminate_process_tree(process)
                    returncode = process.wait(timeout=5)
                    break
                returncode = process.poll()
                if returncode is not None:
                    break
                if time.time() >= deadline:
                    _terminate_process_tree(process)
                    returncode = process.wait(timeout=5)
                    timeout_error = TimeoutError(
                        tr(
                            f"STM32CubeMX timed out after {timeout} seconds",
                            f"STM32CubeMX 运行超过 {timeout} 秒，已超时",
                        )
                    )
                    break
                time.sleep(0.2)
        finally:
            stop_event.set()
            if watch_thread is not None:
                watch_thread.join(timeout=2.0)

        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

        stdout_text = "".join(stdout_lines)
        stderr_text = "".join(stderr_lines)

    result = CubeMXRunResult(
        command=command,
        script_path=actual_script_path,
        stdout=stdout_text,
        stderr=stderr_text,
        returncode=returncode,
        log_dir=os.path.abspath(log_dir) if log_dir else "",
    )

    if should_cleanup_script:
        with contextlib.suppress(OSError):
            os.remove(actual_script_path)

    if timeout_error is not None:
        raise timeout_error

    if dialog_error is not None:
        raise RuntimeError(str(dialog_error)) from dialog_error

    if returncode != 0:
        stdout_tail = _tail_text(stdout_text)
        stderr_tail = _tail_text(stderr_text)
        raise RuntimeError(
            tr(
                "STM32CubeMX generation failed with exit code "
                f"{returncode}\nSTDOUT tail:\n{stdout_tail}\nSTDERR tail:\n{stderr_tail}",
                f"STM32CubeMX 生成失败，退出码 {returncode}\n"
                f"标准输出末尾：\n{stdout_tail}\n标准错误末尾：\n{stderr_tail}",
            )
        )

    effective_expect_paths = DEFAULT_EXPECT_PATHS if expect_paths is None else expect_paths
    missing_paths = [
        path
        for path in _normalize_expect_paths(project_dir, effective_expect_paths)
        if not os.path.exists(path)
    ]
    if missing_paths:
        raise RuntimeError(
            tr(
                "STM32CubeMX finished but expected paths are still missing: ",
                "STM32CubeMX 已结束，但仍缺少期望的路径：",
            )
            + ", ".join(missing_paths)
        )

    LOGGER.info(tr("STM32CubeMX generation finished successfully.", "STM32CubeMX 生成完成。"))
    return result


def main() -> None:
    """xr_cubemx_generate 命令入口：解析命令行参数并调用 generate_cubemx_project；出错时记录错误并
    以退出码 1 结束。
    Entry point of xr_cubemx_generate: parse the command line and call generate_cubemx_project;
    on an error, log it and exit with code 1.
    """
    configure_logging()
    localize_argparse()

    parser = argparse.ArgumentParser(
        description=tr(
            "Generate STM32CubeMX projects in script mode", "以脚本模式运行 STM32CubeMX 生成工程"
        )
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
        help=tr(
            "CubeMX launch mode (default: auto; .jar uses java -jar, executables launch directly)",
            "CubeMX 启动方式（默认：auto；.jar 用 java -jar 启动，可执行文件直接启动）",
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
        "--auto-confirm",
        action="store_true",
        help=tr(
            "Attempt to auto-confirm migration/license/download dialogs",
            "尝试自动确认迁移、许可和下载对话框",
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

    args = parser.parse_args()

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
            auto_confirm=args.auto_confirm,
            timeout=args.timeout,
        )
    except Exception as error:
        LOGGER.error(error)
        sys.exit(1)


if __name__ == "__main__":
    main()
