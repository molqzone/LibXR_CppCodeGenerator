"""新版本提示（libxr.update_notice）：命令结束时 PyPI 上有更新的 libxr 就提示升级。
The new-version notice (libxr.update_notice): a newer libxr on PyPI is reported when a
command ends.
"""

import contextlib
import io
import json
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import TestCase, logging_marker

from libxr import cli, update_notice


class UpdateNotice(TestCase):
    """命令结束时，PyPI 上有更新的 libxr 就提示升级。
    At the end of a command, a newer libxr on PyPI is reported.
    """

    FLASH_INFO = ("stm32", "flash-info", "STM32F103C8T6")

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / "libxr" / "update.json"
        self.queries = 0
        self.pip = f'"{sys.executable}" -m pip install -U libxr'

    def run_with_latest(self, latest, *argv, requires_python=">=3.10", editable=False):
        """在 PyPI 最新版本为 latest、已安装 6.0.0 时运行 libxr，返回警告日志；查询次数计入
        self.queries，缓存在 self.cache。
        Run libxr while the latest version on PyPI is latest and 6.0.0 is installed; return the
        warning logs. Queries are counted in self.queries and cached in self.cache.
        """

        def query():
            """PyPI 查询的替身：计数，并返回 latest 的版本信息。
            Stand-in for the PyPI query: count it and return the release of latest.
            """
            self.queries += 1
            return {"version": latest, "requires_python": requires_python} if latest else None

        with (
            mock.patch("libxr.update_notice._latest_release", side_effect=query),
            mock.patch("libxr.update_notice.installed_version", return_value="6.0.0"),
            mock.patch("libxr.update_notice._editable_install", return_value=editable),
            mock.patch("libxr.update_notice._cache_path", return_value=self.cache),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertLogs(level="WARNING") as logs,
        ):
            logging_marker()
            with contextlib.suppress(SystemExit):
                cli.main(list(argv or self.FLASH_INFO))
        return [line for line in logs.output if line.startswith("WARNING") and "marker" not in line]

    def notice(self, command, latest="6.1.0"):
        """升级到 latest 的提示，升级命令为 command。
        The notice of latest with the upgrade command command.
        """
        return [
            f"WARNING:root:libxr {latest} is available (installed: 6.0.0); upgrade with `{command}`"
        ]

    def test_the_notice_follows_a_failed_command_too(self):
        self.assertEqual(
            self.run_with_latest("6.1.0", "parse", "-d", "absent"), self.notice(self.pip)
        )

    def test_an_editable_installation_is_not_checked(self):
        # 照提示升级会把源码安装换成 PyPI 上的包。
        # Following the notice would replace the source installation with the PyPI package.
        self.assertEqual(self.run_with_latest("6.1.0", editable=True), [])
        self.assertEqual(self.queries, 0)

    def test_a_newer_version_is_reported_and_cached_for_a_day(self):
        # 升级命令用当前 Python 的 pip；以前的 pip install -U libxr 可能是另一个环境的 pip。
        # The upgrade uses the pip of the running Python; the earlier pip install -U libxr may
        # be the pip of another environment.
        self.assertEqual(self.run_with_latest("6.1.0"), self.notice(self.pip))
        self.assertEqual(self.run_with_latest("6.2.0"), self.notice(self.pip))
        self.assertEqual(self.queries, 1)
        stale = json.loads(self.cache.read_text(encoding="utf-8"))
        stale["checked"] -= update_notice.INTERVAL
        self.cache.write_text(json.dumps(stale), encoding="utf-8")
        self.assertEqual(self.run_with_latest("6.2.0"), self.notice(self.pip, "6.2.0"))
        self.assertEqual(self.queries, 2)

    def test_a_failed_query_is_not_repeated_within_a_day(self):
        # 网络不通时以前每个命令都多等约 1 秒。
        # Every command used to wait about one more second while the network was down.
        self.assertEqual(self.run_with_latest(None), [])
        self.assertEqual(self.run_with_latest("6.1.0"), [])
        self.assertEqual(self.queries, 1)

    def test_a_release_for_a_newer_python_names_its_requirement(self):
        self.assertEqual(
            self.run_with_latest("6.1.0", requires_python=">=99"),
            [
                "WARNING:root:libxr 6.1.0 is available (installed: 6.0.0), but it needs Python "
                f">=99 and this is Python {platform.python_version()}"
            ],
        )

    def test_a_pipx_installation_is_told_to_upgrade_with_pipx(self):
        with tempfile.TemporaryDirectory() as prefix:
            Path(prefix, "pipx_metadata.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(sys, "prefix", prefix):
                warnings = self.run_with_latest("6.1.0")
        self.assertEqual(warnings, self.notice("pipx upgrade libxr"))

    def test_no_notice_without_a_newer_version(self):
        for latest in (None, "6.0.0", "5.9.9", "not a version"):
            with self.subTest(latest=latest):
                self.cache.unlink(missing_ok=True)
                self.assertEqual(self.run_with_latest(latest), [])


if __name__ == "__main__":
    unittest.main()
