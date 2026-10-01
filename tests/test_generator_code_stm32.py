"""生成 app_main 源文件（libxr.generator_code_stm32）：静态入口、登记、外设对象、GPIO 名字、
User Code 区域，以及输出不随哈希种子变化。
Generating the app_main source (libxr.generator_code_stm32): the static entry, registrations,
peripheral objects, GPIO names, User Code regions, and output independent of the hash seed.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml
from fixtures import IOC, GeneratorTestCase, logging_marker, user_region

from libxr import cli
from libxr import generator_code_stm32 as generator
from libxr.libxr_config_file import LibXRConfigError
from libxr.stm32_flash_generator import flash_info_to_dict, layout_flash


class EntrySource(GeneratorTestCase):
    """XRobot 和纯 LibXR 两种入口，以及重新生成时保留的用户代码。
    The XRobot and LibXR-only entries, and the user code kept on regeneration.
    """

    def test_xrobot_entry_registers_the_objects_and_calls_xrobot_main(self):
        code = self.generate()
        self.assertIn("XR_REGISTER(power_manager, LibXR::PowerManager);", code)
        self.assertIn('#include "xrobot_main.hpp"', code)
        for legacy in (
            "HardwareContainer",
            "XRobotMain(",
            "ApplicationManager",
            "app_framework.hpp",
        ):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, code)
        self.assertTrue(
            code.endswith("  /* User Code Begin 3 */\n  /* User Code End 3 */\n  XROBOT_MAIN();\n}")
        )
        self.assertEqual(code.count("XROBOT_MAIN"), 1)
        self.assertEqual(user_region(code, 3), "")

    def test_libxr_only_entry_has_no_xrobot_code(self):
        code = self.generate(use_xrobot=False)
        for xrobot in ("XROBOT", "XR_REGISTER", "xrobot_main.hpp", "HardwareContainer"):
            with self.subTest(xrobot=xrobot):
                self.assertNotIn(xrobot, code)
        self.assertIn("Thread::Sleep(UINT32_MAX);", user_region(code, 3))

    def test_leftover_user_xrobot_main_requires_migration(self):
        old = self.generate().replace(
            "  /* User Code End 3 */\n  XROBOT_MAIN();\n",
            "  UserSetup();\n  XROBOT_MAIN();\n  /* User Code End 3 */\n",
        )
        line = old.splitlines().index("  XROBOT_MAIN();") + 1
        with self.assertRaisesMessage(
            ValueError,
            f"line {line}: User Code 3 still calls XROBOT_MAIN(). The generator now emits "
            "XROBOT_MAIN() after the User Code regions of app_main; delete this call from the "
            "User Code region and regenerate. Nothing was written.",
        ):
            self.generate(existing=old)

    def test_comment_mentioning_xrobot_main_is_user_code(self):
        old = self.generate().replace(
            "  /* User Code End 3 */",
            "  // XROBOT_MAIN(); now follows this region\n  /* User Code End 3 */",
        )
        self.assertIn("// XROBOT_MAIN(); now follows this region", self.generate(existing=old))

    def test_the_removed_container_option_is_an_argument_error(self):
        argv = ["gen", "-i", "input.yaml", "-o", "app.cpp", "--hw-cntr"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.build_parser().parse_args(argv)
        self.assertEqual(error.exception.code, 2)

    def test_user_blocks_are_kept_across_regenerations(self):
        old = textwrap.dedent("""\
            /* User Code Begin 1 */
            #include "user_helper.hpp"
            /* User Code End 1 */
            /* User Code Begin 2 */
            PrepareSomething();
            /* User Code End 2 */
            /* User Code Begin 3 */
            LegacyUserCall(peripherals);
            /* User Code End 3 */""")
        first = self.generate(existing=old)
        second = self.generate(existing=first)
        for name in (1, 2, 3):
            with self.subTest(region=name):
                self.assertEqual(user_region(first, name), user_region(old, name))
                self.assertEqual(user_region(second, name), user_region(old, name))

    def test_generated_format_and_lint_bodies_are_refreshed(self):
        generated = self.generate()
        existing = generated.replace(
            "// clang-format off\n", "// clang-format off\nlegacy_format_layout();\n", 1
        ).replace("// NOLINTBEGIN\n", "// NOLINTBEGIN\nlegacy_lint_marker();\n", 1)
        self.assertEqual(self.generate(existing=existing), generated)

    def test_repeat_generation_is_idempotent(self):
        first = self.generate()
        self.assertEqual(self.generate(existing=first), first)


class ProjectConfiguration(GeneratorTestCase):
    """读取 libxr parse 写出的工程 YAML。
    Reading the project YAML that libxr parse writes.
    """

    def test_a_file_without_the_project_sections_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "project.yaml"
            for text, problem in (
                ("", f"{path} must contain a YAML mapping written by `libxr parse`"),
                ("Mcu: {}\nPeripherals: {}\n", "Missing required section: GPIO"),
            ):
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with (
                        self.assertLogs(level="ERROR") as logs,
                        self.assertRaises(SystemExit) as exit,
                    ):
                        generator.load_configuration(str(path))
                    self.assertEqual(exit.exception.code, 1)
                    self.assertEqual(
                        logs.output, [f"ERROR:root:Configuration validation failed: {problem}"]
                    )


class Registrations(GeneratorTestCase):
    """XR_REGISTER 登记：名字、类型，以及 FDCAN 的经典 CAN 引用。
    XR_REGISTER registrations: names, types, and the classic CAN reference of FDCAN.
    """

    def test_named_channels_and_template_types(self):
        generator.registered_devices.clear()
        generator._register_device("adc0", "ADC")
        generator._register_device("terminal", "Terminal<32, 64, 8, 5>")
        text = generator.generate_xrobot_registrations()
        self.assertIn("XR_REGISTER(adc0, LibXR::ADC)", text)
        self.assertIn("XR_REGISTER(terminal, LibXR::Terminal<32, 64, 8, 5>)", text)
        self.assertNotIn("Entry<", text)
        self.assertNotIn('"adc0"', text)

    def test_unnamed_registration_is_diagnosed(self):
        generator.registered_devices.clear()
        generator._register_device("adc.GetChannel(0)", "ADC")
        with self.assertRaisesMessage(
            ValueError, "Static registration needs an existing C++ name: adc.GetChannel(0)"
        ):
            generator.generate_xrobot_registrations()

    def test_every_registered_name_has_one_type(self):
        generator.initialize_registry(True)
        generator._register_device("usart1", "UART")
        with self.assertRaisesMessage(
            ValueError,
            "Generated name 'usart1' (GPIO object) collides with the existing 'usart1' "
            "(UART object); every generated object needs its own name",
        ):
            generator._register_device("usart1", "GPIO")

    def test_fdcan_is_also_registered_as_classic_can(self):
        code = self.generate(self.project(peripherals={"FDCAN": {"FDCAN1": {}, "FDCAN3": {}}}))
        for n in (1, 3):
            with self.subTest(fdcan=n):
                self.assertIn(
                    f"  static STM32CANFD fdcan{n}(&hfdcan{n}, 5);\n"
                    f"  LibXR::CAN& can{n} = fdcan{n};\n",
                    code,
                )
                self.assertIn(
                    f"  XR_REGISTER(fdcan{n}, LibXR::FDCAN);\n  XR_REGISTER(can{n}, LibXR::CAN);\n",
                    code,
                )
        self.assertLess(code.index("LibXR::CAN& can1"), code.index("XR_REGISTER(can1"))

    def test_fdcan_alias_is_xrobot_only(self):
        project = self.project(peripherals={"FDCAN": {"FDCAN1": {}}, "CAN": {"CAN1": {}}})
        code = self.generate(project, use_xrobot=False)
        self.assertIn("static STM32CANFD fdcan1", code)
        self.assertNotIn("LibXR::CAN& can1", code)

    def test_fdcan_alias_colliding_with_classic_can_is_rejected(self):
        alias = "'can1' (LibXR::CAN alias of fdcan1)"
        classic = "'can1' (classic CAN peripheral CAN1)"
        for peripherals, later, earlier in (
            ({"CAN": {"CAN1": {}}, "FDCAN": {"FDCAN1": {}}}, alias, classic),
            ({"FDCAN": {"FDCAN1": {}}, "CAN": {"CAN1": {}}}, classic, alias),
        ):
            with (
                self.subTest(order=list(peripherals)),
                self.assertRaisesMessage(
                    ValueError,
                    f"Generated name {later} collides with the existing {earlier}; every "
                    "generated object needs its own name",
                ),
            ):
                self.generate(self.project(peripherals=peripherals))


class PeripheralObjects(GeneratorTestCase):
    """外设、通道、USB 设备、终端和 DMA 缓冲区的静态对象。
    Static objects of peripherals, channels, USB devices, the terminal and DMA buffers.
    """

    def usb_otg_hs(self, **settings):
        """启用 USB_OTG_HS 的工程数据，USB 设置按给出的值。
        Project data with USB_OTG_HS enabled and the given USB settings.
        """
        generator.libxr_settings.setdefault("USB", {})["usb_otg_hs"] = {"enable": True, **settings}
        return self.project(peripherals={"USB": {"USB_OTG_HS": {"enable": True}}})

    def test_only_single_cdc_otg_hs_is_generated(self):
        project = self.usb_otg_hs(
            tx_buffer_size=128,
            rx_buffer_size=128,
            tx_fifo_size=128,
            rx_fifo_size=256,
            cdc_tx_fifo_size=128,
            cdc_rx_fifo_size=128,
            cdc_queue_size=3,
        )
        code = self.generate(project)
        self.assertIn("static LibXR::USB::CDCUart usb_otg_hs_cdc(", code)
        self.assertIn("static STM32USBDeviceOtgHS usb_hs(", code)
        for absent in (
            "usb_otg_hs_cdc2",
            "usb_otg_hs_ep2_out_buf",
            "usb_otg_hs_ep3_in_buf",
            "usb_otg_hs_ep4_in_buf",
        ):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, code)

    def test_removed_cdc_count_is_not_silently_accepted(self):
        project = self.usb_otg_hs(cdc_count=2)
        with self.assertRaisesMessage(
            ValueError,
            "USB cdc_count is not a generator option; define composite USB in BSP user code",
        ):
            self.generate(project)

    def usb_fs(self, role):
        """启用 usb_fs 的 STM32H503 工程数据；USB 实例的角色和 PCD 句柄是 parse 读出的值。
        STM32H503 project data with usb_fs enabled; the role and PCD handle of the USB instance
        are what parse reads.
        """
        generator.libxr_settings.setdefault("USB", {})["usb_fs"] = {"enable": True}
        return self.project(
            peripherals={"USB": {"USB": {"Role": role, "PCDHandle": "hpcd_USB_DRD_FS"}}},
            mcu="STM32H503RBT6",
            family="STM32H5",
        )

    def test_the_usb_device_uses_the_handle_parse_recorded(self):
        # STM32CubeH5 的 USBX 设备例程都定义 PCD_HandleTypeDef hpcd_USB_DRD_FS；以前生成的是
        # hpcd_USB_FS，链接失败。
        # The USBX device examples of STM32CubeH5 all define PCD_HandleTypeDef hpcd_USB_DRD_FS;
        # hpcd_USB_FS used to be generated and failed to link.
        code = self.generate(self.usb_fs("Device"), use_xrobot=False)
        self.assertIn("extern PCD_HandleTypeDef hpcd_USB_DRD_FS;", code)
        self.assertIn("static STM32USBDeviceDevFs usb_fs(\n      &hpcd_USB_DRD_FS,", code)

    def test_a_usb_in_host_mode_generates_no_device(self):
        with self.assertLogs(level="WARNING") as logs:
            code = self.generate(self.usb_fs("Host"), use_xrobot=False)
        self.assertNotIn("hpcd_", code)
        self.assertNotIn("usb_fs", code)
        self.assertEqual(
            logs.output,
            [
                "WARNING:root:USB instance 'usb_fs' is in host mode in CubeMX, and the LibXR USB "
                "device needs device mode. Skipping generation."
            ],
        )

    def test_resident_peripherals_channels_and_terminal_are_static(self):
        project = self.project(
            gpio={"PA0": {"Label": "LED"}},
            peripherals={
                "ADC": {"ADC1": {"Channels": ["ADC_CHANNEL_0"]}},
                "USART": {"USART1": {}},
                "I2C": {"I2C1": {}},
                "SPI": {"SPI1": {}},
                "CAN": {"CAN1": {}},
                "FDCAN": {"FDCAN2": {}},
                "TIM": {"TIM1": {"Channels": {"CH1": {}}}},
            },
        )
        generator.libxr_settings["terminal_source"] = "usart1"
        generator.libxr_settings["Terminal"]["run_as_thread"] = True
        code = self.generate(project)
        for declaration in (
            "STM32Timebase timebase",
            "STM32PowerManager power_manager",
            "STM32GPIO LED",
            "STM32ADC adc1",
            "auto& adc1_adc_channel_0",
            "STM32UART usart1",
            "STM32I2C i2c1",
            "STM32SPI spi1",
            "STM32CAN can1",
            "STM32CANFD fdcan2",
            "STM32PWM pwm_tim1_ch1",
            "RamFS ramfs",
            "Terminal<32, 32, 5, 5> terminal",
            "LibXR::Thread term_thread",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn("static " + declaration, code)
        self.assertLess(code.index('extern "C" void app_main'), code.index("static STM32Timebase"))

    def test_thread_priorities_are_libxr_levels(self):
        # LibXR 按 RTOS 的优先级数换算等级，原样写数值在 configMAX_PRIORITIES 较大的 FreeRTOS
        # 上偏低，在 ThreadX 上又偏高。
        # LibXR converts the levels by the RTOS priority count; a raw number is too low on a
        # FreeRTOS with a large configMAX_PRIORITIES and too high on ThreadX.
        generator.libxr_settings["SYSTEM"] = "FreeRTOS"
        generator.libxr_settings["terminal_source"] = "usart1"
        generator.libxr_settings["Terminal"]["run_as_thread"] = True
        generator.libxr_settings["Watchdog"] = {
            "run_as_thread": True,
            "thread_priority": "realtime",
        }
        project = self.project(
            peripherals={"USART": {"USART1": {}}, "IWDG": {"IWDG": {"Enabled": True}}}
        )
        code = self.generate(project)
        self.assertIn(
            "PlatformInit(static_cast<uint32_t>(LibXR::Thread::Priority::MEDIUM), 1024);", code
        )
        self.assertIn(
            '"terminal", 1024,\n                     LibXR::Thread::Priority::HIGH);', code
        )
        self.assertIn(
            '"iwdg_wdg", 1024,\n                      LibXR::Thread::Priority::REALTIME);', code
        )
        self.assertNotIn("static_cast<LibXR::Thread::Priority>", code)

    def test_bare_metal_platform_init_takes_no_priority(self):
        generator.libxr_settings["software_timer"]["priority"] = 9
        self.assertIn("  PlatformInit();\n", self.generate())

    def test_a_priority_outside_the_levels_is_rejected(self):
        generator.libxr_settings["SYSTEM"] = "ThreadX"
        for value in (5, -1, 22, True, "urgent", 2.0):
            with self.subTest(value=value):
                generator.libxr_settings["software_timer"]["priority"] = value
                with self.assertRaisesMessage(
                    ValueError,
                    f"software_timer.priority {value!r} is not a priority level; use 0-4 or "
                    "IDLE, LOW, MEDIUM, HIGH, REALTIME",
                ):
                    self.generate()

    def test_dma_cache_alignment_builds_with_older_cmsis(self):
        # 旧的 F7 CMSIS 没有 __SCB_DCACHE_LINE_SIZE，缓存行对齐不能依赖它。
        # Older F7 CMSIS lacks __SCB_DCACHE_LINE_SIZE, so cache-line alignment cannot need it.
        project = self.project(
            peripherals={
                "ADC": {
                    "ADC1": {"Channels": ["ADC_CHANNEL_0"], "DMA_Request": {"Mode": "DMA_CIRCULAR"}}
                }
            }
        )
        code = generator.generate_dma_resources(project)
        self.assertIn("alignas(XR_DCACHE_LINE_SIZE)", code)
        self.assertNotIn("alignas(__SCB_DCACHE_LINE_SIZE)", code)
        self.assertIn("#if defined(__SCB_DCACHE_LINE_SIZE)", code)
        self.assertIn("#define XR_DCACHE_LINE_SIZE 32U", code)

    def test_uart_buffers_follow_the_dma_directions(self):
        code = self.generate(
            self.project(
                peripherals={
                    "USART": {
                        "USART1": {"DMA_TX": "ENABLE", "DMA_RX": "ENABLE"},
                        "USART2": {"DMA_RX": "ENABLE"},
                        "USART3": {},
                    }
                }
            )
        )
        for uart, buffers in (
            ("usart1(&huart1", "usart1_rx_buf, usart1_tx_buf"),
            ("usart2(&huart2", "usart2_rx_buf, {nullptr, 0}"),
            ("usart3(&huart3", "{nullptr, 0}, {nullptr, 0}"),
        ):
            with self.subTest(uart=uart):
                self.assertIn(f"static STM32UART {uart},\n              {buffers}, 5);", code)
        self.assertEqual(
            [line for line in code.splitlines() if line.startswith("alignas(4) static uint8_t")],
            [
                "alignas(4) static uint8_t usart1_tx_buf[128];",
                "alignas(4) static uint8_t usart1_rx_buf[128];",
                "alignas(4) static uint8_t usart2_rx_buf[128];",
            ],
        )

    def test_a_channel_in_several_ranks_gets_one_reference_per_rank(self):
        # DevC 的 ADC3 在第 1 和第 12 个 rank 都转换 IN8；以前两个引用同名，生成时报名字冲突。
        # ADC3 of DevC converts IN8 in ranks 1 and 12; both references used to get the same
        # name, and generation failed with a name collision.
        ranks = [8, 1, 2, 3, 5, 6, 7, 9, 13, 14, 15, 8]
        channels = [f"ADC_CHANNEL_{n}" for n in ranks]
        code = self.generate(
            self.project(
                peripherals={"ADC": {"ADC3": {"DMA": "ENABLE", "RegularConversions": channels}}}
            )
        )
        self.assertIn(
            f"static STM32ADC adc3(&hadc3, adc3_buf, {{{', '.join(channels)}}}, 3.3);", code
        )
        names = [f"adc3_adc_channel_{n}" for n in ranks[:-1]] + ["adc3_adc_channel_8_rank12"]
        self.assertEqual(
            [line for line in code.splitlines() if line.startswith("  static auto& adc3_")],
            [f"  static auto& {name} = adc3.GetChannel({i});" for i, name in enumerate(names)],
        )
        # 默认 buffer_size 为 32 字节，每个 rank 16 个 uint16_t。
        # The default buffer_size is 32 bytes, 16 uint16_t per rank.
        self.assertIn("alignas(4) static uint16_t adc3_buf[192];", code)


class GpioObjectNames(GeneratorTestCase):
    """GPIO 标签成为 app_main 中的 C++ 对象名。
    GPIO labels become C++ object names inside app_main.
    """

    def assertRejected(self, gpio, message, peripherals=None, use_xrobot=True):
        """断言这组 GPIO 标签使生成报错，报错文本为 message。
        Assert that these GPIO labels make generation fail with the error text message.
        """
        with self.assertRaisesMessage(ValueError, message):
            self.generate(self.project(gpio, peripherals), use_xrobot)

    def assertRenameAsked(self, gpio, problem, peripherals=None, use_xrobot=True):
        """断言这组 GPIO 标签使生成报错，要求在 CubeMX 中改名，问题为 problem。
        Assert that these GPIO labels make generation fail asking for a rename in CubeMX, the
        problem being problem.
        """
        message = f"rename these GPIO labels in CubeMX:\n  {problem}"
        self.assertRejected(gpio, message, peripherals, use_xrobot)

    def test_ordinary_labels_and_pins_are_accepted(self):
        for use_xrobot in (True, False):
            with self.subTest(use_xrobot=use_xrobot):
                code = self.generate(self.project({"PA0": {"Label": "LED"}, "PB1": {}}), use_xrobot)
                self.assertIn("static STM32GPIO LED(LED_GPIO_Port, LED_Pin);", code)
                self.assertIn("static STM32GPIO PB1(GPIOB, GPIO_PIN_1);", code)

    def test_keyword_label_is_rejected(self):
        self.assertRenameAsked(
            {"PA0": {"Label": "switch"}}, "GPIO object 'switch' (pin PA0) is a C++ keyword"
        )

    def test_reserved_label_is_rejected(self):
        self.assertRenameAsked(
            {"PA0": {"Label": "_Reset"}},
            "GPIO object '_Reset' (pin PA0) is a reserved C++ identifier",
        )

    def test_cmsis_macro_label_is_rejected(self):
        for label in ("SPI1", "GPIOC", "EXTI0_IRQn", "UNUSED"):
            with self.subTest(label=label):
                self.assertRenameAsked(
                    {"PA0": {"Label": label}},
                    f"GPIO object '{label}' (pin PA0) is a CMSIS/HAL macro or IRQ name",
                )

    def test_label_macro_of_another_label_is_rejected(self):
        self.assertRenameAsked(
            {"PA0": {"Label": "LED"}, "PA1": {"Label": "LED_Pin"}},
            "GPIO object 'LED_Pin' (pin PA1) is the CubeMX macro of GPIO label 'LED'",
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
                    self.assertRenameAsked(
                        {"PA0": {"Label": label}},
                        f"GPIO object '{label}' (pin PA0) collides with a name the generated "
                        "code uses",
                        peripherals,
                        use_xrobot,
                    )

    def test_label_equal_to_a_device_object_is_rejected(self):
        self.assertRejected(
            {"PA0": {"Label": "usart1"}},
            "Generated name 'usart1' (UART object) collides with the existing 'usart1' "
            "(GPIO label usart1 on PA0); every generated object needs its own name",
            {"USART": {"USART1": {}}},
        )

    def test_label_equal_to_another_pin_is_rejected(self):
        self.assertRejected(
            {"PA0": {}, "PB1": {"Label": "PA0"}},
            "Generated name 'PA0' (GPIO label PA0 on PB1) collides with the existing 'PA0' "
            "(GPIO PA0); every generated object needs its own name",
        )


class ExtiInterrupts(GeneratorTestCase):
    """各系列 GPIO 外部中断线对应的 IRQ。
    The IRQ of each GPIO external-interrupt line per family.
    """

    def irq(self, family, pin, mcu_type=""):
        """引脚 PA<pin> 在给定系列上的 EXTI 中断名。
        The EXTI interrupt of pin PA<pin> on the given family.
        """
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

    def test_generated_gpio_uses_the_vector_of_its_part_or_family(self):
        for mcu, family, irq in (
            ("STM32H563ZITx", "STM32H5", "EXTI13_IRQn"),
            (None, "STM32H7", "EXTI15_10_IRQn"),
        ):
            with self.subTest(mcu=mcu, family=family):
                project = self.project(
                    {"PC13": {"Label": "KEY", "GPXTI": True}}, mcu=mcu, family=family
                )
                self.assertIn(
                    f"static STM32GPIO KEY(KEY_GPIO_Port, KEY_Pin, {irq});",
                    self.generate(project, use_xrobot=False),
                )


class UserRegionMarkers(GeneratorTestCase):
    """无法安全保留的 User Code 标记使生成停止，不写任何文件。
    User Code markers that cannot be preserved safely stop generation before anything is
    written.
    """

    def setUp(self):
        super().setUp()
        self.base = self.generate(use_xrobot=False)

    def assertRefused(self, existing, *problems):
        """断言已有代码使生成报错：报错说明没有写入，然后每行列出 problems 中的一个问题。
        Assert that the existing code makes generation fail with an error that says nothing was
        written and then lists problems, one per line.
        """
        message = (
            "existing User Code markers cannot be preserved safely; nothing was written. Fix "
            "the markers and regenerate:"
        )
        with self.assertRaisesMessage(ValueError, "\n  ".join([message, *problems])):
            self.generate(use_xrobot=False, existing=existing)

    @staticmethod
    def line(text, content, occurrence=0):
        """text 中第 occurrence 个（从 0 起）去掉缩进后为 content 的行的行号（从 1 起）。
        The 1-based number of the line of text whose stripped content is content, counting
        from occurrence 0.
        """
        lines = [n for n, line in enumerate(text.splitlines(), 1) if line.strip() == content]
        return lines[occurrence]

    def test_canonical_markers_are_accepted(self):
        existing = self.base.replace(
            "/* User Code Begin 2 */", "/* User Code Begin 2 */\n  Keep();"
        )
        self.assertIn("Keep();", self.generate(use_xrobot=False, existing=existing))

    def test_empty_existing_file_is_new(self):
        self.assertEqual(self.generate(use_xrobot=False, existing="\n"), self.base)

    def test_unpaired_begin_is_refused(self):
        existing = self.base.replace("  /* User Code End 2 */\n", "  Keep();\n", 1)
        begin = self.line(existing, "/* User Code Begin 3 */")
        self.assertRefused(
            existing, f"line {begin}: /* User Code Begin 3 */ opens before User Code End 2"
        )

    def test_unpaired_end_is_refused(self):
        existing = self.base.replace("/* User Code Begin 1 */\n", "", 1)
        end = self.line(existing, "/* User Code End 1 */")
        self.assertRefused(
            existing,
            f"line {end}: /* User Code End 1 */ has no matching Begin marker",
            "User Code Begin 1 / End 1 markers are missing",
        )

    def test_renamed_region_is_refused(self):
        existing = self.base.replace("User Code Begin 2", "User Code Begin 7").replace(
            "User Code End 2", "User Code End 7"
        )
        self.assertRefused(
            existing,
            *(
                f"line {self.line(existing, marker)}: {marker} names a region the generator "
                "does not emit (expected 1, 2, 3)"
                for marker in ("/* User Code Begin 7 */", "/* User Code End 7 */")
            ),
            "User Code Begin 2 / End 2 markers are missing",
        )

    def test_duplicated_region_is_refused(self):
        existing = self.base.replace(
            "/* User Code End 1 */",
            "/* User Code End 1 */\n/* User Code Begin 1 */\nKeep();\n/* User Code End 1 */",
            1,
        )
        second = self.line(existing, "/* User Code Begin 1 */", 1)
        self.assertRefused(existing, f"line {second}: /* User Code Begin 1 */ is duplicated")

    def test_missing_region_is_refused(self):
        existing = self.base.replace("  /* User Code Begin 2 */\n  /* User Code End 2 */\n", "", 1)
        self.assertRefused(existing, "User Code Begin 2 / End 2 markers are missing")

    def test_malformed_marker_is_refused(self):
        existing = self.base.replace("/* User Code Begin 1 */", "// User Code Begin 1", 1)
        self.assertRefused(
            existing,
            f"line {self.line(existing, '// User Code Begin 1')}: malformed User Code marker "
            "// User Code Begin 1",
            f"line {self.line(existing, '/* User Code End 1 */')}: /* User Code End 1 */ has no "
            "matching Begin marker",
            "User Code Begin 1 / End 1 markers are missing",
        )

    def test_region_inside_disabled_block_is_refused(self):
        for replacement, markers in (
            (
                "#if 0\n/* User Code Begin 1 */\nKeep();\n/* User Code End 1 */\n#endif",
                ("/* User Code Begin 1 */", "/* User Code End 1 */"),
            ),
            (
                "#if 0\n/* User Code Begin 1 */\n#endif\nKeep();\n/* User Code End 1 */",
                ("/* User Code Begin 1 */",),
            ),
        ):
            with self.subTest(replacement=replacement):
                existing = self.base.replace(
                    "/* User Code Begin 1 */\n/* User Code End 1 */", replacement, 1
                )
                self.assertRefused(
                    existing,
                    *(
                        f"line {self.line(existing, marker)}: {marker} is inside a "
                        "preprocessor conditional"
                        for marker in markers
                    ),
                )

    def test_conditional_inside_region_body_is_preserved(self):
        body = "#if 0\n  Disabled();\n#endif\n  Kept();"
        existing = self.base.replace(
            "/* User Code Begin 2 */", "/* User Code Begin 2 */\n" + body, 1
        )
        self.assertIn(body, self.generate(use_xrobot=False, existing=existing))

    def test_prose_mentioning_markers_is_not_a_marker(self):
        existing = self.base.replace(
            "/* User Code Begin 2 */",
            "/* User Code Begin 2 */\n  // keep code between the User Code Begin/End lines",
            1,
        )
        self.generate(use_xrobot=False, existing=existing)


LIBXR_CONFIG = textwrap.dedent("""\
    terminal_source: usart1
    USB:
      usb_otg_fs:
        enable: true
      usb_otg_hs:
        enable: true
    """)

GENERATE = textwrap.dedent("""\
    from unittest.mock import patch
    from libxr import cli
    with (patch('libxr.update_notice._latest_release', return_value=None),
          patch('libxr.update_notice._cache_path', return_value=None)):
        cli.main(['parse', '-d', 'project', '-o', 'project/cubemx.yaml'])
        cli.main(['gen', '-i', 'project/cubemx.yaml', '-o', 'project/User/app_main.cpp',
                  '--xrobot', '-d', 'project'])
    """)


def generate_with_hash_seed(root: Path, seed: str) -> dict:
    """在子进程中以给定的 PYTHONHASHSEED 解析 IOC 并生成代码，返回生成的各文件内容。
    Parse IOC and generate code in a subprocess with the given PYTHONHASHSEED; return the
    content of every generated file.
    """
    project = root / seed / "project"
    (project / "User").mkdir(parents=True)
    (project / "demo.ioc").write_text(IOC, encoding="utf-8")
    (project / "User" / "libxr_config.yaml").write_text(LIBXR_CONFIG, encoding="utf-8")
    environment = dict(os.environ, PYTHONHASHSEED=seed)
    subprocess.run(
        [sys.executable, "-c", GENERATE],
        cwd=root / seed,
        env=environment,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in sorted(project.rglob("*"))
        if path.is_file() and path.suffix != ".ioc"
    }


class GenerationRuns(GeneratorTestCase):
    """完整的生成：每次从默认设置开始，只写有变化的文件，推算不出 Flash 布局时不留旧文件。
    Full generations: each starts from the default settings, writes only changed files, and
    leaves no stale flash map when no layout can be derived.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_generator(self, name, project, config=None):
        """在 root/<name> 中由 project 生成（config 为 libxr_config.yaml 的初始内容），返回
        User 目录和日志。
        Generate from project in root/<name>, config being the initial content of
        libxr_config.yaml; return the User directory and the log.
        """
        directory = self.root / name
        user = directory / "User"
        user.mkdir(parents=True, exist_ok=True)
        (directory / "cubemx.yaml").write_text(yaml.safe_dump(project), encoding="utf-8")
        if config is not None:
            (user / "libxr_config.yaml").write_text(config, encoding="utf-8")
        with self.assertLogs(level="INFO") as logs:
            generator.generate(str(directory / "cubemx.yaml"), str(user / "app_main.cpp"))
        return user, logs.output

    def config(self, user):
        """User 目录中 libxr_config.yaml 的内容。
        The content of libxr_config.yaml in the User directory.
        """
        return yaml.safe_load((user / "libxr_config.yaml").read_text(encoding="utf-8"))

    def test_a_second_project_does_not_inherit_settings(self):
        self.run_generator("first", self.project(peripherals={"CAN": {"CAN1": {}}}))
        user, _ = self.run_generator("second", self.project())
        self.assertNotIn("CAN", self.config(user))

    def test_unchanged_files_are_not_rewritten(self):
        user, _ = self.run_generator("demo", self.project())
        times = {path.name: path.stat().st_mtime_ns for path in user.iterdir()}
        _, logs = self.run_generator("demo", self.project())
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in user.iterdir()}, times)
        self.assertEqual(
            logs[-1],
            f"INFO:root:Generated {os.path.normpath(user)}: unchanged app_main.cpp, app_main.h, "
            "flash_map.hpp, libxr_config.yaml",
        )

    def test_the_flash_layout_is_not_copied_to_libxr_config(self):
        # 以前每次都把整张扇区表写进 libxr_config.yaml，却没有代码读它；f103 BSP 的 441 行中
        # 有 389 行是它。
        # The whole sector table used to be written to libxr_config.yaml on every run, with
        # nothing reading it; 389 of the 441 lines of the f103 BSP were that table.
        user, _ = self.run_generator("new", self.project())
        self.assertNotIn("FlashLayout", self.config(user))
        self.assertTrue((user / "flash_map.hpp").is_file())
        old = "terminal_source: ''\nFlashLayout:\n  model: STM32F407IGH6\n  sectors: []\n"
        user, logs = self.run_generator("old", self.project(), old)
        self.assertNotIn("FlashLayout", self.config(user))
        self.assertIn(
            "INFO:root:libxr_config.yaml: removed FlashLayout, which is no longer used", logs
        )

    def test_without_a_flash_layout_the_old_map_is_removed(self):
        self.run_generator("demo", self.project())
        user, logs = self.run_generator("demo", self.project(mcu="STM32X999ZZT6"))
        self.assertFalse((user / "flash_map.hpp").exists())
        self.assertNotIn("flash_map.hpp", (user / "app_main.cpp").read_text(encoding="utf-8"))
        self.assertNotIn("FlashLayout", self.config(user))
        self.assertIn("removed flash_map.hpp", logs[-1])

    def test_can_settings_move_to_lower_case_keys(self):
        user, logs = self.run_generator(
            "demo",
            self.project(peripherals={"CAN": {"CAN1": {}}}),
            config="CAN:\n  CAN1:\n    queue_size: 7\n",
        )
        self.assertIn(
            "static STM32CAN can1(&hcan1, 7);", (user / "app_main.cpp").read_text(encoding="utf-8")
        )
        self.assertEqual(self.config(user)["CAN"], {"can1": {"queue_size": 7}})
        self.assertIn("INFO:root:libxr_config.yaml: renamed CAN.CAN1 to CAN.can1", logs)

    def test_config_values_of_the_wrong_type_are_rejected(self):
        for update, problem in (
            ({"USART": 5}, "'USART': expected a mapping, got int"),
            ({"terminal_source": {"usart1": {}}}, "'terminal_source': expected str, got a mapping"),
        ):
            with (
                self.subTest(update=update),
                self.assertRaisesMessage(
                    LibXRConfigError, f"Config type conflict for key {problem}"
                ),
            ):
                generator._deep_merge({"USART": {}, "terminal_source": ""}, update)
        # 空的段等同于空映射。
        # An empty section counts as an empty mapping.
        self.assertEqual(
            generator._deep_merge({"I2C": {"i2c1": {}}}, {"I2C": None}), {"I2C": {"i2c1": {}}}
        )

    def test_an_invalid_ep0_packet_size_falls_back_to_8(self):
        generator.libxr_settings["USB"]["usb_otg_fs"] = {"enable": True, "ep0_packet_size": 12}
        with self.assertLogs(level="WARNING") as logs:
            code = self.generate(self.project(peripherals={"USB": {"USB_OTG_FS": {}}}))
        self.assertIn("PacketSize0::SIZE_8", code)
        self.assertIn(
            "WARNING:root:USB usb_otg_fs: ep0_packet_size 12 is not 8, 16, 32 or 64; using 8",
            logs.output,
        )

    def test_numeric_settings_are_checked_before_they_reach_the_code(self):
        # 以前这些值原样写进 C++，到编译时才报错。
        # These values used to go into the C++ as they were and fail only at compile time.
        usb = {"USB": {"USB_OTG_FS": {}}}
        for group, settings, peripherals, message in (
            (
                "USART",
                {"usart1": {"tx_queue_size": "five"}},
                {"USART": {"USART1": {}}},
                "USART.usart1.tx_queue_size 'five' is not a positive integer",
            ),
            (
                "I2C",
                {"i2c1": {"buffer_size": -8}},
                {"I2C": {"I2C1": {}}},
                "I2C.i2c1.buffer_size -8 is not a positive integer",
            ),
            (
                "ADC",
                {"adc1": {"vref": "high"}},
                {"ADC": {"ADC1": {"Channels": ["ADC_CHANNEL_0"]}}},
                "ADC.adc1.vref 'high' is not a number",
            ),
            (
                "USB",
                {"usb_otg_fs": {"enable": True, "tx_fifo_size": "big"}},
                usb,
                "USB.usb_otg_fs.tx_fifo_size 'big' is not a positive integer",
            ),
            (
                "USB",
                {"usb_otg_fs": {"enable": True, "vid": 0x10000}},
                usb,
                "USB.usb_otg_fs.vid 65536 is not an integer from 0 to 65535",
            ),
        ):
            with self.subTest(message=message):
                self.setUp()
                generator.libxr_settings[group] = settings
                with self.assertRaisesMessage(ValueError, message):
                    self.generate(self.project(peripherals=peripherals))

    def test_numbers_written_as_strings_are_accepted(self):
        generator.libxr_settings["USART"] = {"usart1": {"tx_queue_size": "7"}}
        generator.libxr_settings["USB"]["usb_otg_fs"] = {
            "enable": True,
            "ep0_packet_size": "16",
            "vid": "0x1D51",
        }
        code = self.generate(
            self.project(peripherals={"USART": {"USART1": {}}, "USB": {"USB_OTG_FS": {}}})
        )
        self.assertIn("{nullptr, 0}, {nullptr, 0}, 7);", code)
        self.assertIn("PacketSize0::SIZE_16", code)
        self.assertIn("0x1D51, 0x6199, 0x100,", code)

    def test_flash_pages_below_one_kilobyte_keep_their_size(self):
        # STM32L0 的页是 128 字节；以前取整成 0 KB。
        # STM32L0 pages are 128 bytes; they used to be truncated to 0 KB.
        code = generator.generate_flash_map_cpp(flash_info_to_dict(layout_flash("STM32L071KBU6")))
        self.assertIn("{0x08000000, 0x00000080},", code)
        self.assertIn("{0x08000080, 0x00000080},", code)

    def test_the_log_names_the_system_and_a_new_config_file(self):
        user, logs = self.run_generator("demo", self.project())
        self.assertIn("INFO:root:System: bare metal", logs)
        path = os.path.join(str(user), "libxr_config.yaml")
        self.assertIn(
            f"INFO:root:{path} does not exist; creating it with the default settings", logs
        )
        _, logs = self.run_generator("demo", self.project(), config="config_version: 2\n")
        self.assertIn(
            f"WARNING:root:{path} has config_version 2, but this libxr supports version 1; "
            "settings of a newer format may have no effect",
            logs,
        )


class HashSeedIndependence(GeneratorTestCase):
    """同一个 CubeMX 工程每次生成的文件逐字节相同。
    The same CubeMX project generates byte-identical files on every run.
    """

    def test_output_does_not_depend_on_hash_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # 几个种子使集合的遍历顺序在各次运行间不同。
            # Several seeds so that set iteration order differs between runs.
            seeds = ["0", "1", "2", "3", "4", "5"]
            outputs = [generate_with_hash_seed(root, seed) for seed in seeds]
        reference = outputs[0]
        self.assertEqual(
            sorted(reference),
            [
                "User/app_main.cpp",
                "User/app_main.h",
                "User/flash_map.hpp",
                "User/libxr_config.yaml",
                "cubemx.yaml",
            ],
        )
        self.assertIn(b"STM32USBDeviceOtgFS usb_fs", reference["User/app_main.cpp"])
        self.assertIn(b"STM32USBDeviceOtgHS usb_hs", reference["User/app_main.cpp"])
        for seed, output in zip(seeds[1:], outputs[1:], strict=True):
            for name in reference:
                with self.subTest(seed=seed, file=name):
                    self.assertEqual(output[name], reference[name])


class GeneratorPin(GeneratorTestCase):
    """libxr_config.yaml 固定的 generator 版本与已安装的不同时警告。
    A warning when the generator pinned in libxr_config.yaml differs from the installed one.
    """

    def warnings(self, pin):
        """在固定 pin、已安装 6.0.0 时检查，返回警告日志。
        Check with the pin pin and 6.0.0 installed; return the warning logs.
        """
        generator.libxr_settings["generator"] = pin
        with (
            mock.patch("libxr.update_notice.installed_version", return_value="6.0.0"),
            self.assertLogs(level="WARNING") as logs,
        ):
            logging_marker()
            generator.check_generator_pin()
        return [line for line in logs.output if "marker" not in line]

    def test_a_different_pin_warns(self):
        self.assertEqual(
            self.warnings("5.2.4"),
            [
                "WARNING:root:libxr_config.yaml pins generator 5.2.4, but libxr 6.0.0 is "
                "installed; the BSP CI generates with 5.2.4"
            ],
        )

    def test_the_same_version_a_commit_or_no_pin_is_quiet(self):
        for pin in ("6.0.0", "0123456789abcdef0123456789abcdef01234567", None):
            with self.subTest(pin=pin):
                self.assertEqual(self.warnings(pin), [])


if __name__ == "__main__":
    unittest.main()
