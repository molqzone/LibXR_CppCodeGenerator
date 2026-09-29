"""xr_cubemx_cfg options that feed the code generator."""
import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path

from libxr import config_cubemx_project as cubemx_cfg
from libxr import generator_code_stm32 as generator


class TerminalOption(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.user = Path(self.temporary.name)
        self.path = self.user / 'libxr_config.yaml'

    def effective_terminal(self):
        importlib.reload(generator)
        generator.load_libxr_config(str(self.user), '')
        return generator.libxr_settings['terminal_source']

    def test_terminal_is_recorded_for_a_new_project(self):
        cubemx_cfg.set_terminal_source(str(self.user), 'usart1')
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'terminal_source: usart1\n')
        self.assertEqual(self.effective_terminal(), 'usart1')

    def test_terminal_replaces_the_configured_one_and_keeps_comments(self):
        self.path.write_text('# pinned\ngenerator: 6.0.0\n# console\nterminal_source: usart1  # debug port\n'
                             'SYSTEM: None\n', encoding='utf-8')
        cubemx_cfg.set_terminal_source(str(self.user), 'usb_fs_cdc')
        self.assertRegex(self.path.read_text(encoding='utf-8'),
                         r'\A# pinned\ngenerator: 6\.0\.0\n# console\nterminal_source: usb_fs_cdc +# debug port\n'
                         r'SYSTEM: None\n\Z')
        self.assertEqual(self.effective_terminal(), 'usb_fs_cdc')

    def test_unparsable_config_stops_without_writing(self):
        self.path.write_text('terminal_source: [\n', encoding='utf-8')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cubemx_cfg.set_terminal_source(str(self.user), 'usart1')
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'terminal_source: [\n')


if __name__ == '__main__':
    unittest.main()
