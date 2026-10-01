"""命令行输出的语言（libxr.output 和各命令）：中文环境输出中文，其他环境输出英文。
The language of the command-line output (libxr.output and the commands): Chinese in a
Chinese environment, English otherwise.
"""

import contextlib
import io
import logging
import os
import subprocess
import sys
from unittest import mock

from fixtures import TestCase

from libxr import peripheral_analyzer_stm32
from libxr.output import configure_output


class LogOutput(TestCase):
    """configure_output 的格式和级别名。
    The format and level names of configure_output.
    """

    def setUp(self):
        super().setUp()
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        root.handlers.clear()
        self.addCleanup(lambda: (setattr(root, "handlers", saved[0]), root.setLevel(saved[1])))

    def test_level_names_follow_the_language(self):
        stream = io.StringIO()
        with mock.patch.object(sys, "stderr", stream):
            configure_output()
        for language in ("en", "zh"):
            with mock.patch.dict(os.environ, XR_LANG=language):
                logging.getLogger("libxr.test").warning("flash layout skipped")
                logging.getLogger("libxr.test").debug("hidden")
        self.assertEqual(
            stream.getvalue(), "[WARNING] flash layout skipped\n[警告] flash layout skipped\n"
        )


class CommandOutput(TestCase):
    """命令的帮助和报错随语言变化。
    Help texts and errors of the commands follow the language.
    """

    def run_parse_ioc(self, *argv):
        """运行 xr_parse_ioc，返回退出码和标准输出、标准错误。
        Run xr_parse_ioc and return the exit code, stdout and stderr.
        """
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["xr_parse_ioc", *argv]),
            mock.patch("libxr.package_info.LibXRPackageInfo.check_and_print"),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                peripheral_analyzer_stm32.main()
                code = 0
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def test_help_follows_the_language(self):
        for language, usage in (("en", "usage: xr_parse_ioc"), ("zh", "用法：xr_parse_ioc")):
            with self.subTest(language=language), mock.patch.dict(os.environ, XR_LANG=language):
                code, out, _ = self.run_parse_ioc("--help")
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith(usage), out)

    def test_piped_output_is_utf8_whatever_the_platform_encoding(self):
        # PYTHONIOENCODING=ascii 代替中文 Windows 上管道的 GBK：两者都写不出 UTF-8 中文。
        # PYTHONIOENCODING=ascii stands in for the GBK of a pipe on Chinese Windows: neither
        # writes Chinese as UTF-8.
        script = (
            "import sys\n"
            "from unittest import mock\n"
            "from libxr import peripheral_analyzer_stm32\n"
            "sys.argv = ['xr_parse_ioc', '--help']\n"
            "with mock.patch('libxr.package_info.LibXRPackageInfo.check_and_print'):\n"
            "    peripheral_analyzer_stm32.main()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            env=dict(os.environ, XR_LANG="zh", PYTHONIOENCODING="ascii"),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.decode("utf-8").startswith("用法：xr_parse_ioc"))
