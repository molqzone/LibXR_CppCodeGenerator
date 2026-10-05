"""新版本提示：命令运行时在后台查询 PyPI，结束时若有比已安装版本更新的 libxr 就提示升级。
New-version notice: PyPI is queried in the background while a command runs, and a libxr newer
than the installed one is reported when the command ends.

查询结果在用户缓存目录中保存一天，一天之内的命令不再查询；从源码以 editable 方式安装时不查询，
也不提示。
The result is kept in the user cache directory for a day, and commands within that day do not
query again; an editable installation from source neither queries nor reports.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

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
# 两次查询之间的最短间隔（秒）；没有结果的查询也算一次，网络不通时不必每个命令都等。
# Shortest interval between two queries, in seconds; a query without a result counts too, so
# that not every command waits while the network is down.
INTERVAL = 24 * 60 * 60


def installed_version() -> str | None:
    """已安装的 libxr 版本；包没有安装时为 None。
    The installed libxr version; None when the package is not installed.
    """
    try:
        return version(PACKAGE)
    except PackageNotFoundError:
        return None


def _editable_install() -> bool:
    """libxr 是否以 editable 方式从源码安装（pip install -e），即 direct_url.json 中 editable 为真。
    Whether libxr is an editable installation from source (pip install -e), that is with
    editable true in its direct_url.json.
    """
    try:
        text = distribution(PACKAGE).read_text("direct_url.json")
        return bool(json.loads(text or "{}").get("dir_info", {}).get("editable"))
    except (PackageNotFoundError, ValueError, AttributeError):
        return False


def _cache_path() -> Path | None:
    """保存查询结果的文件：Windows 为 %LOCALAPPDATA%\\libxr\\update.json，macOS 为
    ~/Library/Caches/libxr/update.json，其他系统为 $XDG_CACHE_HOME（默认 ~/.cache）下的
    libxr/update.json；找不到这些目录时为 None，不缓存。
    The file that keeps the query result: %LOCALAPPDATA%\\libxr\\update.json on Windows,
    ~/Library/Caches/libxr/update.json on macOS and libxr/update.json under $XDG_CACHE_HOME,
    ~/.cache by default, elsewhere; None, without caching, when no such directory is found.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base, PACKAGE, "update.json") if base and base != "~" else None


def _read_cache(path: Path | None) -> dict | None:
    """缓存的内容 {"checked": 查询时间, "latest": 版本信息或 None}；没有或读不了时为 None。
    The cached content {"checked": query time, "latest": release or None}; None when there is
    none or it cannot be read.
    """
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("checked"), (int, float)):
        return data
    return None


def _write_cache(path: Path | None, data: dict) -> None:
    """原子地写入缓存；写不了时不缓存，不报错。
    Write the cache atomically; when it cannot be written, nothing is cached and no error is
    raised.
    """
    if path is None:
        return
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def _latest_release() -> dict | None:
    """PyPI 上 libxr 的最新版本及其 Python 要求 {"version": ..., "requires_python": ...}；请求
    失败或超时时为 None。
    The latest libxr release on PyPI with its Python requirement {"version": ...,
    "requires_python": ...}; None when the request fails or times out.
    """
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=TIMEOUT) as response:
            info = json.load(response)["info"]
        return {"version": str(info["version"]), "requires_python": info.get("requires_python")}
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


def _python_supported(requires_python: str | None) -> bool:
    """当前 Python 是否满足 requires_python；没有要求或要求无法解析时为 True。
    Whether the running Python satisfies requires_python; True without a requirement or when it
    cannot be parsed.
    """
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    if not requires_python:
        return True
    try:
        return SpecifierSet(requires_python).contains(platform.python_version())
    except InvalidSpecifier:
        return True


def upgrade_command() -> str:
    """升级 libxr 的命令：pipx 安装的环境（sys.prefix 中有 pipx 写的 pipx_metadata.json）用
    pipx upgrade，其余用当前 Python 的 pip，升级的就是正在运行 libxr 的环境。
    The command that upgrades libxr: pipx upgrade in an environment installed by pipx, whose
    sys.prefix holds the pipx_metadata.json that pipx writes, else the pip of the running
    Python, which upgrades the environment that runs libxr.
    """
    if os.path.isfile(os.path.join(sys.prefix, "pipx_metadata.json")):
        return f"pipx upgrade {PACKAGE}"
    return f'"{sys.executable}" -m pip install -U {PACKAGE}'


def _report(release: dict | None) -> None:
    """release 比已安装版本新时提示：当前 Python 满足它的要求时给出升级命令，否则写出它要求的
    Python 版本。
    Report a release newer than the installed version: with the upgrade command when the
    running Python meets its requirement, otherwise with the Python version it requires.
    """
    latest = (release or {}).get("version")
    installed = installed_version()
    if not (latest and installed and _is_newer(latest, installed)):
        return
    requires_python = release.get("requires_python")
    if not _python_supported(requires_python):
        current = platform.python_version()
        logging.warning(
            tr(
                f"{PACKAGE} {latest} is available (installed: {installed}), but it needs Python "
                f"{requires_python} and this is Python {current}",
                f"{PACKAGE} {latest} 已发布（已安装 {installed}），但它需要 Python "
                f"{requires_python}，当前是 Python {current}",
            )
        )
        return
    logging.warning(
        tr(
            f"{PACKAGE} {latest} is available (installed: {installed}); "
            f"upgrade with `{upgrade_command()}`",
            f"{PACKAGE} {latest} 已发布（已安装 {installed}）；用 `{upgrade_command()}` 升级",
        )
    )


def start() -> Callable[[], None]:
    """开始检查新版本，返回命令结束时调用的报告函数。
    Start checking for a new version and return the function that reports at the end of the
    command.

    editable 安装不检查。缓存不到一天时直接用缓存的结果；否则先记下这次查询的时间（命令可能在
    查询结束前退出），再在后台线程中查询 PyPI，有结果时写入缓存。报告函数等待查询，但不晚于查询
    开始后 WAIT 秒，没等到结果时用缓存中以前的结果。
    An editable installation is not checked. A cache younger than a day is used as it is;
    otherwise the time of this query is recorded first, since the command may end before the
    query does, and PyPI is queried in a background thread that caches a result. The report
    function waits for the query, but no later than WAIT seconds after it started, and falls
    back to the earlier cached result.
    """
    if _editable_install():
        return lambda: None
    path = _cache_path()
    cache = _read_cache(path)
    now = time.time()
    if cache is not None and 0 <= now - cache["checked"] < INTERVAL:
        return lambda: _report(cache.get("latest"))
    previous = cache.get("latest") if cache else None
    _write_cache(path, {"checked": now, "latest": previous})

    started = time.monotonic()
    result: list[dict | None] = []

    def query() -> None:
        """查询 PyPI，有结果时写入缓存。
        Query PyPI and cache a result.
        """
        release = _latest_release()
        result.append(release)
        if release is not None:
            _write_cache(path, {"checked": now, "latest": release})

    thread = threading.Thread(target=query, daemon=True)
    thread.start()

    def report() -> None:
        """等待查询（不晚于查询开始后 WAIT 秒）后提示；没有结果时用以前缓存的结果。
        Report after waiting for the query, no later than WAIT seconds after it started; without
        a result, the earlier cached one is used.
        """
        thread.join(max(0.0, started + WAIT - time.monotonic()))
        _report((result[0] if result else None) or previous)

    return report
