import os
import sys
import tempfile
import unittest
from importlib import import_module

import yaml


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

detect_platform = import_module("libxr.GeneratorCode").detect_platform
mspm0_generator = import_module("libxr.GeneratorCodeMSPM0")
generate_code = mspm0_generator.generate_code
load_configuration = mspm0_generator.load_configuration
load_settings = mspm0_generator.load_settings
write_outputs = mspm0_generator.write_outputs


def sample_project():
    return {
        "Mcu": {"Family": "TI", "Type": "MSPM0G3507"},
        "GPIO": {
            "PB22": {
                "Signal": "GPIO_Output",
                "Label": "USER_LED_1",
                "Pull": "GPIO_NOPULL",
                "Group": "GPIO_LEDS",
                "Name": "USER_LED_1",
                "Macro": "GPIO_LEDS_USER_LED_1",
            }
        },
        "Peripherals": {
            "UART": {"UART_0": {"BaudRate": 2000000}},
            "I2C": {"I2C_0": {"ClockSpeed": 400000}},
            "SPI": {
                "SPI_0": {
                    "CLKPolarity": "IDLE_LOW",
                    "CLKPhase": "SECOND_EDGE",
                    "dma": {
                        "dma_rx": {"stream": "DMA_CH1"},
                        "dma_tx": {"stream": "DMA_CH0"},
                    },
                }
            },
            "ADC": {
                "ADC12_0": {
                    "Channels": ["DL_ADC12_INPUT_CHAN_2", "DL_ADC12_INPUT_CHAN_3"],
                    "MemoryIndices": [0, 1],
                    "DMA": "ENABLE",
                }
            },
            "PWM": {
                "PWM_0": {
                    "Channels": {
                        "PWM_CHANNEL_0": {
                            "PWM": True,
                            "Name": "MOTOR_A",
                            "DutyCycle": 50,
                        }
                    }
                }
            },
            "MCAN": {"MCAN0": {"Interrupt": True}},
            "DMA": {"DMA": {}},
        },
        "DMA": {"Requests": {}, "Configurations": {}},
        "Timebase": {"Source": "SysTick", "IRQ": None},
    }


class TestMSPM0Generator(unittest.TestCase):
    def test_generate_xrobot_code_uses_sysconfig_driver_macros(self):
        settings = load_settings("")

        code = generate_code(sample_project(), settings, use_xrobot=True)

        self.assertIn('#include "xrobot_main.hpp"', code)
        self.assertIn("GPIO_LEDS_PORT", code)
        self.assertIn("GPIO_LEDS_USER_LED_1_PIN", code)
        self.assertIn("MSPM0_UART_INIT(UART_0", code)
        self.assertIn("MSPM0_I2C_INIT(I2C_0", code)
        self.assertIn("MSPM0_SPI_INIT(SPI_0, DMA_CH1, DMA_CH0", code)
        self.assertIn("MSPM0_ADC_INIT(ADC12_0, 0)", code)
        self.assertIn("DL_ADC12_MEM_IDX_1", code)
        self.assertIn("MSPM0_PWM_INIT(PWM_0, GPIO_PWM_0_C0)", code)
        self.assertIn("SetDutyCycle(0.5f)", code)
        self.assertIn("MSPM0_CAN_INIT(MCAN0, 8)", code)
        self.assertIn("LibXR::HardwareContainer peripherals", code)
        self.assertIn("LibXR::Entry<LibXR::UART>", code)
        self.assertIn("XRobotMain(peripherals);", code)

    def test_generate_code_preserves_user_blocks(self):
        existing = """/* User Code Begin 1 */
#include "custom.hpp"
/* User Code End 1 */
/* User Code Begin 2 */
  CustomInit();
/* User Code End 2 */
/* User Code Begin 3 */
  CustomLoop();
/* User Code End 3 */
"""

        code = generate_code(sample_project(), load_settings(""), existing=existing)

        self.assertIn('#include "custom.hpp"', code)
        self.assertIn("CustomInit();", code)
        self.assertIn("CustomLoop();", code)
        self.assertNotIn("Thread::Sleep(UINT32_MAX)", code)

    def test_spi_without_dma_is_not_emitted(self):
        project = sample_project()
        project["Peripherals"]["SPI"]["SPI_0"].pop("dma")

        code = generate_code(project, load_settings(""))

        self.assertNotIn("MSPM0_SPI_INIT", code)
        self.assertNotIn('#include "mspm0_spi.hpp"', code)

    def test_duplicate_display_names_get_unique_cpp_identifiers(self):
        project = sample_project()
        project["GPIO"] = {
            "PB17": {
                "Signal": "GPIO_Output",
                "Label": "En",
                "Pull": "GPIO_NOPULL",
                "Group": "PTC_MCU",
                "Name": "En",
                "Macro": "PTC_MCU_EN",
            },
            "PB19": {
                "Signal": "GPIO_Output",
                "Label": "EN",
                "Pull": "GPIO_NOPULL",
                "Group": "ISOLATOR",
                "Name": "EN",
                "Macro": "ISOLATOR_EN",
            },
        }

        code = generate_code(project, load_settings(""), use_hw_cntr=True)

        self.assertIn("static LibXR::MSPM0GPIO en(", code)
        self.assertIn("static LibXR::MSPM0GPIO en_2(", code)
        self.assertIn('LibXR::Entry<LibXR::GPIO>{en, {"En"}}', code)
        self.assertIn('LibXR::Entry<LibXR::GPIO>{en_2, {"EN"}}', code)

    def test_write_outputs_creates_header_and_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "app_main.cpp")

            write_outputs(sample_project(), output, use_xrobot=True)

            self.assertTrue(os.path.isfile(output))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "app_main.h")))
            settings_path = os.path.join(tmpdir, "libxr_config.yaml")
            self.assertTrue(os.path.isfile(settings_path))
            with open(settings_path, "r", encoding="utf-8") as source:
                settings = yaml.safe_load(source)
            self.assertEqual(settings["UART"]["uart_0"]["rx_buffer_size"], 256)
            self.assertEqual(settings["PWM"]["motor_a"]["duty_cycle"], 0.5)

    def test_load_and_generic_detection_accept_mspm0(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".config.yaml")
            with open(path, "w", encoding="utf-8") as target:
                yaml.safe_dump(sample_project(), target, sort_keys=False)

            loaded = load_configuration(path)

            self.assertEqual(loaded["Mcu"]["Type"], "MSPM0G3507")
            self.assertEqual(detect_platform(path), "MSPM0")

    def test_load_configuration_rejects_non_mspm0(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".config.yaml")
            project = sample_project()
            project["Mcu"] = {"Family": "STM32", "Type": "STM32F407"}
            with open(path, "w", encoding="utf-8") as target:
                yaml.safe_dump(project, target, sort_keys=False)

            with self.assertRaises(ValueError):
                load_configuration(path)


if __name__ == "__main__":
    unittest.main()
