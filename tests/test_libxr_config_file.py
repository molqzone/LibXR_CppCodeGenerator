"""libxr_config.yaml 的读写（libxr.libxr_config_file）：保留用户的键和注释，出错时不改写文件。
Reading and writing libxr_config.yaml (libxr.libxr_config_file): user keys and comments are
kept, and a failure never rewrites the file.
"""

import contextlib
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fixtures import GeneratorTestCase

from libxr import generator_code_stm32 as generator
from libxr import libxr_config_file as config_file

PROJECT = {
    "Mcu": {"Type": "STM32F103C8T6", "Family": "STM32F1"},
    "GPIO": {"PC13": {"Label": "LED"}},
    "Peripherals": {"USART": {"USART1": {"DMA_TX": "ENABLE", "DMA_RX": "ENABLE"}}},
}


class LibXRConfigFile(GeneratorTestCase):
    """生成器每次运行时读取、更新并写回 libxr_config.yaml。
    Every generator run reads, updates and writes back libxr_config.yaml.
    """

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "libxr_config.yaml"

    def regenerate(self, use_xrobot=False):
        """像 main() 那样运行一次生成器（不写 app_main），返回写回的 libxr_config.yaml。
        Run the generator once as main() does, without writing app_main; return the
        libxr_config.yaml it writes back.
        """
        importlib.reload(generator)
        generator.load_libxr_config(str(self.directory), "")
        self.generate(PROJECT, use_xrobot)
        generator.save_libxr_config(str(self.path))
        return self.path.read_text(encoding="utf-8")

    def test_new_file_matches_plain_yaml_layout(self):
        text = self.regenerate()
        cleaned = {
            k: v for k, v in generator.libxr_settings.items() if not (isinstance(v, dict) and not v)
        }
        self.assertEqual(text, yaml.dump(cleaned, allow_unicode=True, sort_keys=False))

    def test_generator_pin_and_comments_survive_regeneration(self):
        first = self.regenerate()
        edited = "# Generator pinned for BSP CI\ngenerator: 6.0.0  # release\n" + first.replace(
            "USART:\n", "# UART buffers\nUSART:\n", 1
        ).replace("tx_queue_size: 5", "tx_queue_size: 5  # keep five")
        self.path.write_text(edited, encoding="utf-8")
        self.assertEqual(self.regenerate(), edited)

    def test_generator_sha_pin_is_kept_verbatim(self):
        self.regenerate()
        sha = "0123456789abcdef0123456789abcdef01234567"
        text = self.path.read_text(encoding="utf-8") + f"generator: {sha}\n"
        self.path.write_text(text, encoding="utf-8")
        self.assertEqual(self.regenerate(), text)
        self.assertEqual(yaml.safe_load(text)["generator"], sha)

    def test_changed_values_keep_neighbouring_comments(self):
        self.regenerate()
        text = self.path.read_text(encoding="utf-8").replace(
            "terminal_source: ", "# terminal device\nterminal_source: ", 1
        )
        text = text.replace("Terminal:\n", "# terminal tuning\nTerminal:\n", 1)
        self.path.write_text(
            text.replace("max_line_size: 32", "max_line_size: 64  # long lines"), encoding="utf-8"
        )
        regenerated = self.regenerate()
        self.assertIn("# terminal device\nterminal_source: ", regenerated)
        self.assertIn("# terminal tuning\nTerminal:\n", regenerated)
        self.assertIn("max_line_size: 64  # long lines", regenerated)

    def test_dropped_device_aliases_are_reported_and_comments_kept(self):
        self.regenerate()
        base = self.path.read_text(encoding="utf-8")
        legacy = base.replace(
            "SYSTEM: None\n",
            (
                "device_aliases:\n"
                "  usart3:\n"
                "    type: UART\n"
                "    aliases: [uart_dr16, remote]  # receiver\n"
                "  PC13: LED_R\n"
                "# pinned generator\n"
                "generator: 6.0.0\n"
                "SYSTEM: None\n"
            ),
            1,
        )
        self.path.write_text(legacy, encoding="utf-8")
        with self.assertLogs(level="WARNING") as logs:
            text = self.regenerate()
        output = "\n".join(logs.output)
        self.assertIn("uart_dr16 -> usart3", output)
        self.assertIn("remote -> usart3", output)
        self.assertIn("LED_R -> PC13", output)
        self.assertNotIn("device_aliases", text)
        self.assertIn("# pinned generator\ngenerator: 6.0.0\n", text)

    def test_unparsable_file_is_an_error_and_is_not_rewritten(self):
        broken = "terminal_source: usart1\nTerminal: [unclosed\n"
        self.path.write_text(broken, encoding="utf-8")
        with self.assertRaisesRegex(config_file.LibXRConfigError, "Cannot parse"):
            generator.load_libxr_config(str(self.directory), "")
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_non_mapping_file_is_an_error(self):
        self.path.write_text("- usart1\n", encoding="utf-8")
        with self.assertRaisesRegex(config_file.LibXRConfigError, "mapping"):
            generator.load_libxr_config(str(self.directory), "")

    def test_type_conflict_is_an_error(self):
        self.path.write_text("terminal_source:\n  device: usart1\n", encoding="utf-8")
        with self.assertRaisesRegex(config_file.LibXRConfigError, "terminal_source"):
            generator.load_libxr_config(str(self.directory), "")

    def test_missing_explicit_config_source_is_an_error(self):
        with self.assertRaisesRegex(config_file.LibXRConfigError, "Cannot locate"):
            generator.load_libxr_config(str(self.directory), str(self.directory / "absent.yaml"))

    def test_bare_output_file_name_writes_into_current_directory(self):
        (self.directory / "input.yaml").write_text(yaml.safe_dump(PROJECT), encoding="utf-8")
        argv = ["xr_gen_code_stm32", "-i", "input.yaml", "-o", "app_main.cpp"]
        previous = Path.cwd()
        os.chdir(self.directory)
        self.addCleanup(os.chdir, previous)
        with patch("sys.argv", argv), patch("libxr.package_info.LibXRPackageInfo.check_and_print"):
            generator.main()
        for name in ("app_main.cpp", "app_main.h", "libxr_config.yaml", "flash_map.hpp"):
            self.assertTrue((self.directory / name).is_file(), name)

    def test_failed_generation_writes_nothing(self):
        (self.directory / "input.yaml").write_text(yaml.safe_dump(PROJECT), encoding="utf-8")
        output = self.directory / "out" / "app_main.cpp"
        output.parent.mkdir()
        broken = "Terminal: [unclosed\n"
        (output.parent / "libxr_config.yaml").write_text(broken, encoding="utf-8")
        argv = ["xr_gen_code_stm32", "-i", str(self.directory / "input.yaml"), "-o", str(output)]
        with (
            patch("sys.argv", argv),
            patch("libxr.package_info.LibXRPackageInfo.check_and_print"),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            generator.main()
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(sorted(p.name for p in output.parent.iterdir()), ["libxr_config.yaml"])
        self.assertEqual((output.parent / "libxr_config.yaml").read_text(encoding="utf-8"), broken)


if __name__ == "__main__":
    unittest.main()
