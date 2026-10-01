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

import reference_projects
from fixtures import IOC, GeneratorTestCase, user_region

from libxr import cli
from libxr import generator_code_stm32 as generator


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
        with self.assertRaisesRegex(
            ValueError, r"line \d+: User Code 3 still calls XROBOT_MAIN\(\).*delete"
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


class ReferenceProjects(GeneratorTestCase):
    """两个真实 BSP 工程生成的文件与基准文件逐字节相同。
    The files generated for two real BSP projects equal the reference files byte for byte.
    """

    def test_the_generated_files_are_the_reference(self):
        for name in reference_projects.PROJECTS:
            expected = reference_projects.DATA / name / "expected"
            with self.subTest(project=name), tempfile.TemporaryDirectory() as temporary:
                user = reference_projects.generate(name, expected / "cubemx.yaml", Path(temporary))
                for file in reference_projects.GENERATED:
                    with self.subTest(file=file):
                        self.assertEqual((user / file).read_bytes(), (expected / file).read_bytes())


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
        with self.assertRaisesRegex(ValueError, r"existing C\+\+ name"):
            generator.generate_xrobot_registrations()

    def test_every_registered_name_has_one_type(self):
        generator.initialize_registry(True)
        generator._register_device("usart1", "UART")
        with self.assertRaisesRegex(ValueError, "'usart1'.*collides"):
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
        for peripherals in (
            {"CAN": {"CAN1": {}}, "FDCAN": {"FDCAN1": {}}},
            {"FDCAN": {"FDCAN1": {}}, "CAN": {"CAN1": {}}},
        ):
            with (
                self.subTest(order=list(peripherals)),
                self.assertRaisesRegex(
                    ValueError,
                    r"'can1'.*LibXR::CAN alias of fdcan1|LibXR::CAN alias of fdcan1.*'can1'",
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
        with self.assertRaisesRegex(ValueError, "BSP user code"):
            self.generate(project)

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


class GpioObjectNames(GeneratorTestCase):
    """GPIO 标签成为 app_main 中的 C++ 对象名。
    GPIO labels become C++ object names inside app_main.
    """

    def assertRejected(self, gpio, message, peripherals=None, use_xrobot=True):
        """断言这组 GPIO 标签使生成报错，报错匹配 message。
        Assert that these GPIO labels make generation fail with an error matching message.
        """
        with self.assertRaisesRegex(ValueError, message):
            self.generate(self.project(gpio, peripherals), use_xrobot)

    def test_ordinary_labels_and_pins_are_accepted(self):
        for use_xrobot in (True, False):
            with self.subTest(use_xrobot=use_xrobot):
                code = self.generate(self.project({"PA0": {"Label": "LED"}, "PB1": {}}), use_xrobot)
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

    def assertRefused(self, existing, message):
        """断言已有代码使生成报错，报错匹配 message 并说明没有写入。
        Assert that the existing code makes generation fail with an error matching message
        that says nothing was written.
        """
        with self.assertRaisesRegex(ValueError, message) as error:
            self.generate(use_xrobot=False, existing=existing)
        self.assertIn("nothing was written", str(error.exception))

    def test_canonical_markers_are_accepted(self):
        existing = self.base.replace(
            "/* User Code Begin 2 */", "/* User Code Begin 2 */\n  Keep();"
        )
        self.assertIn("Keep();", self.generate(use_xrobot=False, existing=existing))

    def test_empty_existing_file_is_new(self):
        self.assertEqual(self.generate(use_xrobot=False, existing="\n"), self.base)

    def test_unpaired_begin_is_refused(self):
        existing = self.base.replace("  /* User Code End 2 */\n", "  Keep();\n", 1)
        self.assertRefused(existing, "opens before User Code End 2")

    def test_unpaired_end_is_refused(self):
        existing = self.base.replace("/* User Code Begin 1 */\n", "", 1)
        self.assertRefused(existing, "User Code End 1 \\*/ has no matching Begin")

    def test_renamed_region_is_refused(self):
        existing = self.base.replace("User Code Begin 2", "User Code Begin 7").replace(
            "User Code End 2", "User Code End 7"
        )
        self.assertRefused(existing, "does not emit")

    def test_duplicated_region_is_refused(self):
        existing = self.base.replace(
            "/* User Code End 1 */",
            "/* User Code End 1 */\n/* User Code Begin 1 */\nKeep();\n/* User Code End 1 */",
            1,
        )
        self.assertRefused(existing, "duplicated")

    def test_missing_region_is_refused(self):
        existing = self.base.replace("  /* User Code Begin 2 */\n  /* User Code End 2 */\n", "", 1)
        self.assertRefused(existing, "End 2 markers are missing")

    def test_malformed_marker_is_refused(self):
        existing = self.base.replace("/* User Code Begin 1 */", "// User Code Begin 1", 1)
        self.assertRefused(existing, "malformed User Code marker")

    def test_region_inside_disabled_block_is_refused(self):
        for replacement in (
            "#if 0\n/* User Code Begin 1 */\nKeep();\n/* User Code End 1 */\n#endif",
            "#if 0\n/* User Code Begin 1 */\n#endif\nKeep();\n/* User Code End 1 */",
        ):
            with self.subTest(replacement=replacement):
                existing = self.base.replace(
                    "/* User Code Begin 1 */\n/* User Code End 1 */", replacement, 1
                )
                self.assertRefused(existing, "inside a preprocessor conditional")

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
    with patch('libxr.update_notice._latest_version', return_value=None):
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


if __name__ == "__main__":
    unittest.main()
