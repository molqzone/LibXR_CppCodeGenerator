"""新版本提示：命令运行时在后台查询 PyPI，结束时若有比已安装版本更新的 libxr 就提示升级。
New-version notice: PyPI is queried in the background while a command runs, and a libxr newer
than the installed one is reported when the command ends.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version

from xr_syntax.i18n import tr

PACKAGE = "libxr"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
# 查询的超时时间（秒）。
# Timeout of the query, in seconds.
TIMEOUT = 3.0
# 命令结束时最多等到查询开始后这么久（秒）；运行更久的命令不再等待。
# At the end of a command, wait until at most this long after the query started, in seconds;
# a command that ran longer does not wait at all.
WAIT = 1.0


def installed_version() -> str | None:
    """已安装的 libxr 版本；包没有安装时为 None。
    The installed libxr version; None when the package is not installed.
    """
    try:
        return version(PACKAGE)
    except PackageNotFoundError:
        return None


def _latest_version() -> str | None:
    """PyPI 上 libxr 的最新版本；请求失败或超时时为 None。
    The latest libxr version on PyPI; None when the request fails or times out.
    """
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=TIMEOUT) as response:
            return str(json.load(response)["info"]["version"])
    except Exception:
        return None


def _is_newer(latest: str, installed: str) -> bool:
    """latest 是否比 installed 新；任一版本号无法解析时为 False。
    Whether latest is newer than installed; False when either version cannot be parsed.
    """
    from packaging.version import InvalidVersion, Version

    try:
        return Version(latest) > Version(installed)
    except InvalidVersion:
        return False


def upgrade_command() -> str:
    """升级 libxr 的命令：pipx 安装的环境（sys.prefix 中有 pipx 写的 pipx_metadata.json）用
    pipx upgrade，其余用 pip。
    The command that upgrades libxr: pipx upgrade in an environment installed by pipx, whose
    sys.prefix holds the pipx_metadata.json that pipx writes, else pip.
    """
    if os.path.isfile(os.path.join(sys.prefix, "pipx_metadata.json")):
        return f"pipx upgrade {PACKAGE}"
    return f"pip install -U {PACKAGE}"


def start() -> Callable[[], None]:
    """在后台线程中开始查询 PyPI，返回命令结束时调用的报告函数。
    Start the PyPI query in a background thread and return the function that reports at the
    end of the command.

    报告函数等待查询，但不晚于查询开始后 WAIT 秒；查询结果更新时以警告给出新版本和升级命令。
    The report function waits for the query, but no later than WAIT seconds after it started;
    when the result is newer, it logs a warning with the new version and the upgrade command.
    """
    started = time.monotonic()
    result: list[str | None] = []
    thread = threading.Thread(target=lambda: result.append(_latest_version()), daemon=True)
    thread.start()

    def report() -> None:
        """查询已有结果且比已安装版本新时提示升级。
        Suggest an upgrade when the query has a result that is newer than the installed version.
        """
        thread.join(max(0.0, started + WAIT - time.monotonic()))
        latest = result[0] if result else None
        installed = installed_version()
        if latest and installed and _is_newer(latest, installed):
            logging.warning(
                tr(
                    f"{PACKAGE} {latest} is available (installed: {installed}); "
                    f"upgrade with `{upgrade_command()}`",
                    f"{PACKAGE} {latest} 已发布（已安装 {installed}）；"
                    f"用 `{upgrade_command()}` 升级",
                )
            )

    return report
