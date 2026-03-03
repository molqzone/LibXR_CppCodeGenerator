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

    def test_find_syscfg_file_does_not_lookup_recursively(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sysconfig")
            os.makedirs(nested, exist_ok=True)
            target = os.path.join(nested, "demo.syscfg")
            open(target, "w", encoding="utf-8").close()

            result = find_syscfg_file(tmpdir)

            self.assertIsNone(result)

    def test_extract_metadata_from_syscfg_text(self):
        syscfg_text = """
const board = {
  deviceName: "MSPM0G3507",
};
const gpio = scripting.addModule("/ti/drivers/GPIO", {}, false);
const uart = scripting.addModule('/ti/drivers/UART2', {}, false);
const rtos = "FreeRTOS";
"""
        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "MSPM0G3507")
        self.assertIsNone(board)
        self.assertEqual(extract_rtos(syscfg_text), "FreeRTOS")
        self.assertEqual(
            extract_modules(syscfg_text),
            ["/ti/drivers/GPIO", "/ti/drivers/UART2"],
        )

    def test_extract_metadata_fallback_values(self):
        syscfg_text = "const value = 1;"

        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "UNKNOWN_TI_DEVICE")
        self.assertIsNone(board)
        self.assertEqual(extract_rtos(syscfg_text), "Unknown")
        self.assertEqual(extract_modules(syscfg_text), [])

    def test_extract_device_from_cli_board(self):
        syscfg_text = """
/**
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507" --product "mspm0_sdk@2.09.00.00"
 */
"""
        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "MSPM0G3507")
        self.assertEqual(board, "LP_MSPM0G3507")

    def test_build_yaml_config_contains_required_sections(self):
        syscfg_text = """
/**
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507"
 */
const GPIO = scripting.addModule("/ti/driverlib/GPIO", {}, false);
const GPIO1 = GPIO.addInstance();
GPIO1.$name = "GPIO_GRP_0";
GPIO1.associatedPins[0].$name = "LED";
GPIO1.associatedPins[0].assignedPort = "PORTB";
GPIO1.associatedPins[0].assignedPin = "22";
GPIO1.associatedPins[0].direction = "OUTPUT";
GPIO1.associatedPins[0].internalResistor = "PULL_UP";
const I2C = scripting.addModule("/ti/driverlib/I2C", {}, false);
const I2C1 = I2C.addInstance();
I2C1.$name = "I2C_0";
"""
        result = build_yaml_config("/tmp/demo.syscfg", "uart0", syscfg_text)

        self.assertIn("Mcu", result)
        self.assertIn("GPIO", result)
        self.assertIn("Peripherals", result)
        self.assertIn("SysConfig", result)
        self.assertEqual(result["Mcu"]["Family"], "TI")
        self.assertEqual(result["Mcu"]["Type"], "MSPM0G3507")
        self.assertEqual(result["SysConfig"]["Board"], "LP_MSPM0G3507")
        self.assertIn("PB22", result["GPIO"])
        self.assertEqual(result["GPIO"]["PB22"]["Signal"], "GPIO_Output")
        self.assertEqual(result["GPIO"]["PB22"]["Label"], "LED")
        self.assertEqual(result["GPIO"]["PB22"]["Pull"], "GPIO_PULLUP")
        self.assertNotIn("GPIO", result["Peripherals"])
        self.assertIn("I2C", result["Peripherals"])
        self.assertIn("I2C_0", result["Peripherals"]["I2C"])
        self.assertEqual(result["terminal_source"], "uart0")

    def test_render_template_command_raises_for_unknown_placeholder(self):
        with self.assertRaises(ValueError):
            render_template_command(
                "echo {unknown}",
                {"project_dir": "/tmp", "syscfg_file": "/tmp/a.syscfg", "yaml_output": "/tmp/a.yaml"},
            )


if __name__ == "__main__":
    unittest.main()
