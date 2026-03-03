import os
import sys
import tempfile
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from libxr.ConfigTiSyscfgProject import (
    build_yaml_config,
    extract_modules,
    extract_rtos,
    extract_ti_device,
    find_syscfg_file,
    render_template_command,
)


class TestTiSyscfgEntry(unittest.TestCase):
    def test_find_syscfg_file_returns_sorted_first_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "b_file.syscfg"), "w", encoding="utf-8").close()
            open(os.path.join(tmpdir, "a_file.syscfg"), "w", encoding="utf-8").close()

            result = find_syscfg_file(tmpdir)

            self.assertEqual(result, os.path.join(tmpdir, "a_file.syscfg"))

    def test_find_syscfg_file_returns_none_without_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "readme.txt"), "w", encoding="utf-8").close()

            result = find_syscfg_file(tmpdir)

            self.assertIsNone(result)

    def test_extract_metadata_from_syscfg_text(self):
        syscfg_text = """
const board = {
  deviceName: "MSPM0G3507",
};
const gpio = scripting.addModule("/ti/drivers/GPIO");
const uart = scripting.addModule('/ti/drivers/UART2');
const rtos = "FreeRTOS";
"""
        self.assertEqual(extract_ti_device(syscfg_text), "MSPM0G3507")
        self.assertEqual(extract_rtos(syscfg_text), "FreeRTOS")
        self.assertEqual(
            extract_modules(syscfg_text),
            ["/ti/drivers/GPIO", "/ti/drivers/UART2"],
        )

    def test_extract_metadata_fallback_values(self):
        syscfg_text = "const value = 1;"

        self.assertEqual(extract_ti_device(syscfg_text), "UNKNOWN_TI_DEVICE")
        self.assertEqual(extract_rtos(syscfg_text), "Unknown")
        self.assertEqual(extract_modules(syscfg_text), [])

    def test_build_yaml_config_contains_required_sections(self):
        syscfg_text = 'device: "CC2340R5";'
        result = build_yaml_config("/tmp/demo.syscfg", "uart0", syscfg_text)

        self.assertIn("Mcu", result)
        self.assertIn("GPIO", result)
        self.assertIn("Peripherals", result)
        self.assertIn("SysConfig", result)
        self.assertEqual(result["Mcu"]["Family"], "TI")
        self.assertEqual(result["terminal_source"], "uart0")

    def test_render_template_command_raises_for_unknown_placeholder(self):
        with self.assertRaises(ValueError):
            render_template_command(
                "echo {unknown}",
                {"project_dir": "/tmp", "syscfg_file": "/tmp/a.syscfg", "yaml_output": "/tmp/a.yaml"},
            )


if __name__ == "__main__":
    unittest.main()
