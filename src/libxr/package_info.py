"""libxr pip 包的版本自检：记录本地版本，PyPI 上有更新版本时提示升级。
Version self-check of the libxr pip package: log the local version and suggest an upgrade
when PyPI has a newer one.
"""

import logging

from xr_syntax.i18n import tr


class LibXRPackageInfo:
    """libxr 包的版本信息与更新检查，各命令行工具启动时调用。
    Version information and update check of the libxr package, called by the command-line
    tools at start-up.
    """

    PKGNAME = "libxr"

    @classmethod
    def get_local_version(cls):
        """已安装的 libxr 版本；包未安装时为 None。
        The installed libxr version; None when the package is not installed.
        """
        try:
            from importlib.metadata import PackageNotFoundError, version
        except ImportError:
            from importlib_metadata import PackageNotFoundError, version
        try:
            return version(cls.PKGNAME)
        except PackageNotFoundError:
            return None

    @classmethod
    def get_remote_version(cls):
        """PyPI 上 libxr 的最新版本。
        The latest libxr version on PyPI.

        没有安装 requests、请求出错、3 秒超时或状态码不是 200 时为 None。
        None when requests is not installed, or the request fails, times out after 3 seconds
        or returns a status other than 200.
        """
        try:
            import requests
        except ImportError:
            return None
        try:
            resp = requests.get(f"https://pypi.org/pypi/{cls.PKGNAME}/json", timeout=3)
            if resp.status_code == 200:
                return resp.json()["info"]["version"]
        except Exception:
            pass
        return None

    @classmethod
    def check_and_print(cls):
        """记录本地版本（未知时为警告）；PyPI 版本更新时以警告给出新版本和升级命令。
        Log the local version, as a warning when it is unknown; when PyPI has a newer version,
        log warnings with that version and the upgrade command.

        没有 packaging 或版本号无法比较时，两个版本不同即提示。
        Without packaging, or when the versions cannot be compared, any difference between
        them triggers the hint.
        """
        local_ver = cls.get_local_version()
        remote_ver = cls.get_remote_version()
        if local_ver:
            logging.info(f"{cls.PKGNAME} {local_ver}")
        else:
            logging.warning(tr(f"{cls.PKGNAME} (version unknown)", f"{cls.PKGNAME}（版本未知）"))
        if local_ver and remote_ver:
            try:
                from packaging.version import parse as vparse

                if vparse(local_ver) < vparse(remote_ver):
                    cls._print_upgrade_notice(local_ver, remote_ver)
            except Exception:
                # fallback: only show if different
                if local_ver != remote_ver:
                    cls._print_upgrade_notice(local_ver, remote_ver)

    @classmethod
    def _print_upgrade_notice(cls, local_ver, remote_ver):
        """以警告给出 PyPI 上的新版本和升级命令。
        Log warnings with the newer version on PyPI and the upgrade command.
        """
        logging.warning(
            tr(
                f"A new version of {cls.PKGNAME} is available: {remote_ver} "
                f"(your version: {local_ver})",
                f"{cls.PKGNAME} 有新版本可用：{remote_ver}（当前版本：{local_ver}）",
            )
        )
        logging.warning(
            tr(
                f"Tip: Upgrade with: pip install -U {cls.PKGNAME}\n",
                f"提示：用 pip install -U {cls.PKGNAME} 升级\n",
            )
        )
