"""GPIO labels become C++ object names inside app_main."""

import importlib
import unittest

from libxr import generator_code_stm32 as generator


class GpioObjectNames(unittest.TestCase):
    def setUp(self):
        importlib.reload(generator)

    def generate(self, gpio, peripherals=None, use_xrobot=True):
        generator.initialize_registry(use_xrobot)
        project = {
            "Mcu": {"Type": "STM32F407IGH6", "Family": "STM32F4"},
            "GPIO": gpio,
            "Peripherals": peripherals or {},
        }
        return generator.generate_full_code(project, use_xrobot, "")

    def assertRejected(self, gpio, message, peripherals=None, use_xrobot=True):
        with self.assertRaisesRegex(ValueError, message):
            self.generate(gpio, peripherals, use_xrobot)

    def test_ordinary_labels_and_pins_are_accepted(self):
        for use_xrobot in (True, False):
            with self.subTest(use_xrobot=use_xrobot):
                code = self.generate({"PA0": {"Label": "LED"}, "PB1": {}}, use_xrobot=use_xrobot)
                self.assertIn("static STM32GPIO LED(LED_GPIO_Port, LED_Pin);", code)
                self.assertIn("static STM32GPIO PB1(GPIOB, GPIO_PIN_1);", code)

    def test_keyword_label_is_rejected(self):
        self.assertRejected(
            {"PA0": {"Label": "switch"}}, "'switch' \\(pin PA0\\) is a C\\+\\+ keyword"
        )

    def test_reserved_label_is_rejected(self):
        self.assertRejected({"PA0": {"Label": "_Reset"}}, "reserved C\\+\\+ identifier")

    def test_cmsis_macro_label_is_rejected(self):
        for label in ("SPI1", "GPIOC", "EXTI0_IRQn", "UNUSED"):
            with self.subTest(label=label):
                self.assertRejected({"PA0": {"Label": label}}, "CMSIS/HAL macro or IRQ name")

    def test_label_macro_of_another_label_is_rejected(self):
        self.assertRejected(
            {"PA0": {"Label": "LED"}, "PA1": {"Label": "LED_Pin"}},
            "is the CubeMX macro of GPIO label 'LED'",
        )

    def test_label_shadowing_a_generated_name_is_rejected(self):
        for use_xrobot in (True, False):
            for label, peripherals in (
                ("hspi1", {"SPI": {"SPI1": {}}}),
                ("spi1_rx_buf", {"SPI": {"SPI1": {"DMA_RX": "ENABLE"}}}),
                ("timebase", {}),
                ("PlatformInit", {}),
            ):
                with self.subTest(label=label, use_xrobot=use_xrobot):
                    self.assertRejected(
                        {"PA0": {"Label": label}},
                        f"'{label}' \\(pin PA0\\) collides with a name the generated code uses",
                        peripherals,
                        use_xrobot,
                    )

    def test_label_equal_to_a_device_object_is_rejected(self):
        self.assertRejected(
            {"PA0": {"Label": "usart1"}},
            "'usart1' \\(UART object\\) collides with the existing 'usart1' \\(GPIO label usart1 on PA0\\)",
            {"USART": {"USART1": {}}},
        )

    def test_label_equal_to_another_pin_is_rejected(self):
        self.assertRejected({"PA0": {}, "PB1": {"Label": "PA0"}}, "'PA0' .*collides")


class ExtiInterrupts(unittest.TestCase):
    def irq(self, family, pin, mcu_type=""):
        return generator._get_exti_irq(pin, f"PA{pin}", True, family, mcu_type)

    def test_per_line_families(self):
        for family in ("STM32H5", "STM32U3", "STM32U5", "STM32L5", "STM32WBA", "STM32N6"):
            with self.subTest(family=family):
                self.assertEqual(
                    [self.irq(family, pin) for pin in (0, 5, 9, 10, 15)],
                    ["EXTI0_IRQn", "EXTI5_IRQn", "EXTI9_IRQn", "EXTI10_IRQn", "EXTI15_IRQn"],
                )
        self.assertEqual(self.irq("STM32H7", 12, "STM32H7S3L8Hx"), "EXTI12_IRQn")

    def test_grouped_families(self):
        self.assertEqual(
            [self.irq("STM32H7", pin, "STM32H723VGTx") for pin in (4, 5, 15)],
            ["EXTI4_IRQn", "EXTI9_5_IRQn", "EXTI15_10_IRQn"],
        )
        for family in ("STM32F0", "STM32G0", "STM32L0", "STM32C0", "STM32U0"):
            with self.subTest(family=family):
                self.assertEqual(
                    [self.irq(family, pin) for pin in (1, 2, 4)],
                    ["EXTI0_1_IRQn", "EXTI2_3_IRQn", "EXTI4_15_IRQn"],
                )

    def test_h5_pin_uses_its_own_vector(self):
        importlib.reload(generator)
        generator.initialize_registry(False)
        project = {
            "Mcu": {"Type": "STM32H563ZITx", "Family": "STM32H5"},
            "GPIO": {"PC13": {"Label": "KEY", "GPXTI": True}},
            "Peripherals": {},
        }
        code = generator.generate_full_code(project, False, "")
        self.assertIn("static STM32GPIO KEY(KEY_GPIO_Port, KEY_Pin, EXTI13_IRQn);", code)

    def test_project_without_part_number_is_mapped_by_family(self):
        importlib.reload(generator)
        generator.initialize_registry(False)
        project = {
            "Mcu": {"Type": None, "Family": "STM32H7"},
            "GPIO": {"PC13": {"Label": "KEY", "GPXTI": True}},
            "Peripherals": {},
        }
        code = generator.generate_full_code(project, False, "")
        self.assertIn("static STM32GPIO KEY(KEY_GPIO_Port, KEY_Pin, EXTI15_10_IRQn);", code)


if __name__ == "__main__":
    unittest.main()
