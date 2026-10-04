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
from unittest import mock

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
        # 这些测试检查文件的布局和用户内容；新文件中固定版本的 generator 见
        # test_generator_code_stm32.GeneratorPin。
        # These tests check the layout of the file and the user content; the generator pin in a
        # new file is covered by test_generator_code_stm32.GeneratorPin.
        patcher = mock.patch("libxr.update_notice.installed_version", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        self.assertEqual(
            logs.output,
            [
                "WARNING:root:Removed the legacy device_aliases table from libxr_config.yaml; "
                "generated objects are registered only under their own names. Update "
                "configurations that used these aliases (alias -> device):",
                "WARNING:root:  uart_dr16 -> usart3",
                "WARNING:root:  remote -> usart3",
                "WARNING:root:  LED_R -> PC13",
            ],
        )
        self.assertNotIn("device_aliases", text)
        self.assertIn("# pinned generator\ngenerator: 6.0.0\n", text)

    def test_aliases_equal_to_their_device_are_not_listed(self):
        # libxr 5.x 带 --xrobot 时每项默认只有设备名本身；以前 DevC 升级时这样的条目列出 41 行，
        # 真正要改的别名淹没在其中。
        # With --xrobot libxr 5.x wrote only the device name itself for each entry by default;
        # upgrading DevC used to list 41 such lines, burying the aliases that need a change.
        self.regenerate()
        base = self.path.read_text(encoding="utf-8")
        table = (
            "device_aliases:\n"
            "  usart1:\n    type: UART\n    aliases:\n    - usart1\n"
            "  PC13:\n    type: GPIO\n    aliases: [PC13, LED_R]\n"
        )
        self.path.write_text(base.replace("SYSTEM: None\n", table + "SYSTEM: None\n"), "utf-8")
        with self.assertLogs(level="WARNING") as logs:
            self.regenerate()
        self.assertEqual(logs.output[1:], ["WARNING:root:  LED_R -> PC13"])
        identical = table.replace("[PC13, LED_R]", "[PC13]")
        self.path.write_text(base.replace("SYSTEM: None\n", identical + "SYSTEM: None\n"), "utf-8")
        with self.assertLogs(level="INFO") as logs:
            text = self.regenerate()
        self.assertEqual(
            [entry for entry in logs.output if "device_aliases" in entry],
            [
                "INFO:root:libxr_config.yaml: removed device_aliases, which is no longer used; "
                "each alias in it equals the name of its object, so nothing needs to change"
            ],
        )
        self.assertNotIn("WARNING", " ".join(logs.output))
        self.assertNotIn("device_aliases", text)

    def test_written_values_read_back_the_same(self):
        document, settings = config_file.parse("count: 010\nenable: yes\n", "test")
        config_file.update(document, dict(settings, mode="on", time="12:30", flag="no"))
        text = config_file.dump(document)
        self.assertTrue(text.startswith("count: 010\n"), text)
        self.assertEqual(
            yaml.safe_load(text),
            {"count": 8, "enable": True, "mode": "on", "time": "12:30", "flag": "no"},
        )

    def test_single_cdc_settings_are_written_back_as_a_cdc_list(self):
        project = {
            "Mcu": {"Type": "STM32F407IGH6", "Family": "STM32F4"},
            "GPIO": {},
            "Peripherals": {"USB": {"USB_OTG_HS": {"Role": "Device"}}},
        }
        old = (
            "USB:\n"
            "  usb_otg_hs:\n"
            "    enable: true\n"
            "    dma_section: .dma  # DMA RAM\n"
            "    cdc_tx_fifo_size: 96\n"
            "    cdc_rx_fifo_size: 80\n"
            "    cdc_queue_size: 4\n"
            "    vid: 5840\n"
        )
        self.path.write_text(old, encoding="utf-8")
        importlib.reload(generator)
        generator.load_libxr_config(str(self.directory), "")
        self.generate(project, False)
        generator.save_libxr_config(str(self.path))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(
            "    dma_section: .dma  # DMA RAM\n"
            "    cdc:\n"
            "    - {tx_fifo_size: 96, rx_fifo_size: 80, queue_size: 4}\n"
            "    vid: 5840\n",
            text,
        )
        self.assertNotIn("cdc_", text)
        # 再生成一次，文件不变。
        # Generating again leaves the file as it is.
        importlib.reload(generator)
        generator.load_libxr_config(str(self.directory), "")
        self.generate(project, False)
        generator.save_libxr_config(str(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_a_cdc_list_written_in_block_style_keeps_its_style(self):
        project = {
            "Mcu": {"Type": "STM32F407IGH6", "Family": "STM32F4"},
            "GPIO": {},
            "Peripherals": {"USB": {"USB_OTG_HS": {"Role": "Device"}}},
        }
        self.path.write_text(
            "USB:\n"
            "  usb_otg_hs:\n"
            "    enable: true\n"
            "    cdc:\n"
            "    - tx_fifo_size: 64  # first\n"
            "      rx_fifo_size: 64\n"
            "      queue_size: 3\n"
            "    - {tx_fifo_size: 128}\n",
            encoding="utf-8",
        )
        importlib.reload(generator)
        generator.load_libxr_config(str(self.directory), "")
        self.generate(project, False)
        generator.save_libxr_config(str(self.path))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(
            "    cdc:\n"
            "    - tx_fifo_size: 64  # first\n"
            "      rx_fifo_size: 64\n"
            "      queue_size: 3\n"
            "    - {tx_fifo_size: 128, rx_fifo_size: 128, queue_size: 3}\n",
            text,
        )

    def test_a_new_key_goes_after_the_key_before_it(self):
        document, _ = config_file.parse("a: 1\nc: 3  # keep\n", "test")
        config_file.update(document, {"a": 1, "b": 2, "c": 3, "d": 4})
        self.assertEqual(config_file.dump(document), "a: 1\nb: 2\nc: 3  # keep\nd: 4\n")

    def test_a_comment_only_file_keeps_its_comments(self):
        comments = "# generator: 6.0.0\n\n#  terminal_source: usart1\n"
        self.path.write_text(comments, encoding="utf-8")
        kept = self.regenerate()
        self.path.unlink()
        self.assertEqual(kept, comments + self.regenerate())

    def test_unparsable_file_is_an_error_and_is_not_rewritten(self):
        broken = "terminal_source: usart1\nTerminal: [unclosed\n"
        self.path.write_text(broken, encoding="utf-8")
        with self.assertRaises(config_file.LibXRConfigError) as error:
            generator.load_libxr_config(str(self.directory), "")
        self.assertEqual(
            str(error.exception),
            f"{self.path} line 3, column 1: expected ',' or ']', but got '<stream end>'",
        )
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_read_errors_name_the_file_line_and_problem(self):
        for text, message in (
            ("USART: {}\nUSART: {}\n", "line 2: key 'USART' is duplicated (already on line 1)"),
            (
                "SPI:\n  spi1:\n    tx_buffer_size: 32\n  spi1:\n    tx_buffer_size: 64\n",
                "line 4: key 'spi1' is duplicated (already on line 2)",
            ),
        ):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(config_file.LibXRConfigError) as error:
                    generator.load_libxr_config(str(self.directory), "")
                self.assertEqual(str(error.exception), f"{self.path} {message}")
        self.path.write_bytes("# 中文注释\nterminal_source: usart1\n".encode("gbk"))
        with self.assertRaises(config_file.LibXRConfigError) as error:
            generator.load_libxr_config(str(self.directory), "")
        self.assertEqual(
            str(error.exception), f"{self.path} is not UTF-8 text (byte 3); save it as UTF-8"
        )

    def test_a_byte_order_mark_is_read_and_dropped(self):
        self.path.write_bytes(b"\xef\xbb\xbfterminal_source: usart1\n")
        generator.load_libxr_config(str(self.directory), "")
        self.assertEqual(generator.libxr_settings["terminal_source"], "usart1")
        self.assertTrue(generator.libxr_config_text().startswith("terminal_source: usart1\n"))

    def test_empty_mappings_in_the_file_are_kept(self):
        # 以前所有值为空映射的顶层键都会被删掉，用户写的也不例外。
        # Every top-level key holding an empty mapping used to be dropped, the user's too.
        self.path.write_text("board_notes: {}\nI2C:\nterminal_source: ''\n", encoding="utf-8")
        text = self.regenerate()
        self.assertIn("board_notes: {}\n", text)
        self.assertIn("I2C:\n", text)
        self.assertNotIn("I2C: {}", text)
        self.assertNotIn("CAN:", text)

    def test_non_mapping_file_is_an_error(self):
        self.path.write_text("- usart1\n", encoding="utf-8")
        with self.assertRaisesMessage(
            config_file.LibXRConfigError,
            f"{self.path} must contain a YAML mapping at the top level",
        ):
            generator.load_libxr_config(str(self.directory), "")

    def test_a_section_that_is_not_a_mapping_is_an_error(self):
        # 以前报错不写是哪个文件。
        # The error used to leave out which file it was about.
        self.path.write_text("USART: 5\n", encoding="utf-8")
        with self.assertRaisesMessage(
            config_file.LibXRConfigError, f"{self.path}: USART 5 is not a mapping"
        ):
            generator.load_libxr_config(str(self.directory), "")

    def test_missing_explicit_config_source_is_an_error(self):
        absent = self.directory / "absent.yaml"
        with self.assertRaisesMessage(
            config_file.LibXRConfigError, f"Cannot locate config source: {absent}"
        ):
            generator.load_libxr_config(str(self.directory), str(absent))

    def test_bare_output_file_name_writes_into_current_directory(self):
        (self.directory / "input.yaml").write_text(yaml.safe_dump(PROJECT), encoding="utf-8")
        previous = Path.cwd()
        os.chdir(self.directory)
        self.addCleanup(os.chdir, previous)
        generator.generate("input.yaml", "app_main.cpp")
        for name in ("app_main.cpp", "app_main.h", "libxr_config.yaml", "flash_map.hpp"):
            self.assertTrue((self.directory / name).is_file(), name)

    def test_failed_generation_writes_nothing(self):
        (self.directory / "input.yaml").write_text(yaml.safe_dump(PROJECT), encoding="utf-8")
        output = self.directory / "out" / "app_main.cpp"
        output.parent.mkdir()
        broken = "Terminal: [unclosed\n"
        (output.parent / "libxr_config.yaml").write_text(broken, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            generator.generate(str(self.directory / "input.yaml"), str(output))
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(sorted(p.name for p in output.parent.iterdir()), ["libxr_config.yaml"])
        self.assertEqual((output.parent / "libxr_config.yaml").read_text(encoding="utf-8"), broken)


if __name__ == "__main__":
    unittest.main()
