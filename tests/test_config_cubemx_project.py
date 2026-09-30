"""xr_cubemx_cfg 的选项（libxr.config_cubemx_project）：写入 libxr_config.yaml 的终端设备。
Options of xr_cubemx_cfg (libxr.config_cubemx_project): the terminal device written to
libxr_config.yaml.
"""

import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path

from fixtures import TestCase

from libxr import config_cubemx_project as cubemx_cfg
from libxr import generator_code_stm32 as generator


class TerminalOption(TestCase):
    """--terminal 记录的 terminal_source 被生成器使用。
    The terminal_source that --terminal records is the one the generator uses.
    """

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.user = Path(self.temporary.name)
        self.path = self.user / "libxr_config.yaml"

    def effective_terminal(self):
        """生成器读取 libxr_config.yaml 后使用的 terminal_source。
        The terminal_source the generator uses after reading libxr_config.yaml.
        """
        importlib.reload(generator)
        generator.load_libxr_config(str(self.user), "")
        return generator.libxr_settings["terminal_source"]

    def test_terminal_is_recorded_for_a_new_project(self):
        cubemx_cfg.set_terminal_source(str(self.user), "usart1")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "terminal_source: usart1\n")
        self.assertEqual(self.effective_terminal(), "usart1")

    def test_terminal_replaces_the_configured_one_and_keeps_comments(self):
        self.path.write_text(
            "# pinned\ngenerator: 6.0.0\n# console\nterminal_source: usart1  # debug port\n"
            "SYSTEM: None\n",
            encoding="utf-8",
        )
        cubemx_cfg.set_terminal_source(str(self.user), "usb_fs_cdc")
        self.assertRegex(
            self.path.read_text(encoding="utf-8"),
            r"\A# pinned\ngenerator: 6\.0\.0\n# console\nterminal_source: usb_fs_cdc +# debug port\n"
            r"SYSTEM: None\n\Z",
        )
        self.assertEqual(self.effective_terminal(), "usb_fs_cdc")

    def test_unparsable_config_stops_without_writing(self):
        self.path.write_text("terminal_source: [\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cubemx_cfg.set_terminal_source(str(self.user), "usart1")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "terminal_source: [\n")


if __name__ == "__main__":
    unittest.main()
