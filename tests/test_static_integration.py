"""Static XRobot output integration; no model invocation or vendor regeneration."""
import copy
import importlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import contextlib
import io

from libxr import GeneratorCodeSTM32 as generator


class StaticEntry(unittest.TestCase):
    def setUp(self):
        importlib.reload(generator)
        self.project = {'Mcu': {'Type': 'STM32F407IGH6', 'Family': 'STM32F4'}, 'GPIO': {}, 'Peripherals': {}}
        generator.initialize_registry(True)

    def test_new_xrobot_default_is_static(self):
        code = generator.generate_full_code(self.project, True, '')
        self.assertIn('XR_REGISTER(power_manager, LibXR::PowerManager);', code)
        self.assertIn('XROBOT_MAIN();', code)
        self.assertNotIn('HardwareContainer', code)
        self.assertNotIn('XRobotMain(', code)
        self.assertNotIn('ApplicationManager', code)
        self.assertIn('#include "xrobot_main.hpp"', code)

    def test_generator_owns_xrobot_main_after_user_code(self):
        code = generator.generate_full_code(self.project, True, '')
        self.assertTrue(code.endswith(
            '  /* User Code Begin 3 */\n  /* User Code End 3 */\n  XROBOT_MAIN();\n}'))
        self.assertEqual(code.count('XROBOT_MAIN'), 1)
        self.assertEqual(generator.preserve_user_blocks(code, 3), '')

    def test_leftover_user_xrobot_main_requires_migration(self):
        old = generator.generate_full_code(self.project, True, '').replace(
            '  /* User Code End 3 */\n  XROBOT_MAIN();\n',
            '  UserSetup();\n  XROBOT_MAIN();\n  /* User Code End 3 */\n')
        with self.assertRaisesRegex(ValueError, r'line \d+: User Code 3 still calls XROBOT_MAIN\(\).*delete'):
            generator.generate_full_code(self.project, True, old)

    def test_comment_mentioning_xrobot_main_is_user_code(self):
        old = generator.generate_full_code(self.project, True, '').replace(
            '  /* User Code End 3 */', '  // XROBOT_MAIN(); now follows this region\n  /* User Code End 3 */')
        code = generator.generate_full_code(self.project, True, old)
        self.assertIn('// XROBOT_MAIN(); now follows this region', code)

    def test_libxr_only_keeps_user_xrobot_code_untouched(self):
        generator.initialize_registry(False)
        base = generator.generate_full_code(self.project, False, '')
        self.assertNotIn('XROBOT', base)
        self.assertIn('Thread::Sleep(UINT32_MAX);', generator.preserve_user_blocks(base, 3))

    def test_xrobot_does_not_reenable_container(self):
        code = generator.generate_full_code(self.project, True, '')
        self.assertIn('XROBOT_MAIN();', code)
        self.assertNotIn('HardwareContainer', code)
        self.assertNotIn('#include "app_framework.hpp"', code)

    def test_libxr_only_has_no_tooling_dependency(self):
        generator.initialize_registry(False)
        code = generator.generate_full_code(self.project, False, '')
        self.assertNotIn('XR_REGISTER', code)
        self.assertNotIn('xrobot_main.hpp', code)
        self.assertNotIn('HardwareContainer', code)

    def test_removed_container_option_is_rejected(self):
        self.assertFalse(hasattr(generator, 'generate_xrobot_hardware_container'))
        with patch('sys.argv', ['generator', '-i', 'input.yaml', '-o', 'app.cpp', '--hw-cntr']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                generator.parse_arguments()
        self.assertEqual(error.exception.code, 2)

    def test_named_channels_and_template_types(self):
        generator.registered_devices.clear()
        generator._register_device('adc0', 'ADC')
        generator._register_device('terminal', 'Terminal<32, 64, 8, 5>')
        text = generator.generate_xrobot_registrations()
        self.assertIn('XR_REGISTER(adc0, LibXR::ADC)', text)
        self.assertIn('XR_REGISTER(terminal, LibXR::Terminal<32, 64, 8, 5>)', text)
        self.assertNotIn('Entry<', text)
        self.assertNotIn('"adc0"', text)

    def test_legacy_device_aliases_are_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'libxr_config.yaml'
            path.write_text('device_aliases:\n  usart3:\n    type: UART\n    aliases: [uart_dr16]\n', encoding='utf-8')
            generator.load_libxr_config(temporary, '')
            generator.save_libxr_config(str(path))
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('device_aliases', text)
            self.assertNotIn('uart_dr16', text)

    def test_unnamed_registration_is_diagnosed(self):
        generator.registered_devices.clear()
        generator._register_device('adc.GetChannel(0)', 'ADC')
        with self.assertRaisesRegex(ValueError, r'existing C\+\+ name'):
            generator.generate_xrobot_registrations()

    def test_user_blocks_are_not_silently_rewritten(self):
        old = '''/* User Code Begin 1 */
#include "user_helper.hpp"
/* User Code End 1 */
/* User Code Begin 2 */
PrepareSomething();
/* User Code End 2 */
/* User Code Begin 3 */
LegacyUserCall(peripherals);
/* User Code End 3 */'''
        first = generator.generate_full_code(self.project, True, old)
        second = generator.generate_full_code(self.project, True, first)
        for i in (1, 2, 3):
            self.assertEqual(generator.preserve_user_blocks(first, i), generator.preserve_user_blocks(old, i))
            self.assertEqual(generator.preserve_user_blocks(first, i), generator.preserve_user_blocks(second, i))
        self.assertIn('LegacyUserCall(peripherals);', first)

    def test_only_single_cdc_otg_hs_is_generated(self):
        project = copy.deepcopy(self.project)
        project['Peripherals']['USB'] = {'USB_OTG_HS': {'enable': True}}
        generator.libxr_settings.setdefault('USB', {})['usb_otg_hs'] = {
            'enable': True,
            'tx_buffer_size': 128,
            'rx_buffer_size': 128,
            'tx_fifo_size': 128,
            'rx_fifo_size': 256,
            'cdc_tx_fifo_size': 128,
            'cdc_rx_fifo_size': 128,
            'cdc_queue_size': 3,
        }
        code = generator.generate_full_code(project, True, '')
        self.assertIn('CDCUart usb_otg_hs_cdc(', code)
        self.assertNotIn('usb_otg_hs_cdc2', code)
        self.assertNotIn('usb_otg_hs_ep2_out_buf', code)
        self.assertNotIn('usb_otg_hs_ep3_in_buf', code)
        self.assertNotIn('usb_otg_hs_ep4_in_buf', code)
        self.assertIn('static LibXR::USB::CDCUart usb_otg_hs_cdc(', code)
        self.assertIn('static STM32USBDeviceOtgHS usb_hs(', code)

    def test_removed_cdc_count_is_not_silently_accepted(self):
        project = copy.deepcopy(self.project)
        project['Peripherals']['USB'] = {'USB_OTG_HS': {'enable': True}}
        generator.libxr_settings['USB']['usb_otg_hs'] = {'enable': True, 'cdc_count': 2}
        with self.assertRaisesRegex(ValueError, 'BSP user code'):
            generator.generate_full_code(project, True, '')

    def test_resident_peripherals_channels_and_terminal_are_static(self):
        project = copy.deepcopy(self.project)
        project['GPIO'] = {'PA0': {'Label': 'LED'}}
        project['Peripherals'] = {
            'ADC': {'ADC1': {'Channels': ['ADC_CHANNEL_0']}},
            'USART': {'USART1': {}}, 'I2C': {'I2C1': {}}, 'SPI': {'SPI1': {}},
            'CAN': {'CAN1': {}}, 'FDCAN': {'FDCAN2': {}},
            'TIM': {'TIM1': {'Channels': {'CH1': {}}}},
        }
        generator.libxr_settings['terminal_source'] = 'usart1'
        generator.libxr_settings['Terminal']['run_as_thread'] = True
        code = generator.generate_full_code(project, True, '')
        for declaration in ('STM32Timebase timebase', 'STM32PowerManager power_manager',
            'STM32GPIO LED', 'STM32ADC adc1', 'auto& adc1_adc_channel_0',
            'STM32UART usart1', 'STM32I2C i2c1', 'STM32SPI spi1', 'STM32CAN can1',
            'STM32CANFD fdcan2', 'STM32PWM pwm_tim1_ch1', 'RamFS ramfs',
            'Terminal<32, 32, 5, 5> terminal', 'LibXR::Thread term_thread'):
            with self.subTest(declaration=declaration):
                self.assertIn('static '+declaration, code)
        self.assertLess(code.index('extern "C" void app_main'), code.index('static STM32Timebase'))

    def test_fdcan_is_also_registered_as_classic_can(self):
        project = copy.deepcopy(self.project)
        project['Peripherals'] = {'FDCAN': {'FDCAN1': {}, 'FDCAN3': {}}}
        code = generator.generate_full_code(project, True, '')
        self.assertIn('  static STM32CANFD fdcan1(&hfdcan1, 5);\n  LibXR::CAN& can1 = fdcan1;\n', code)
        self.assertIn('  static STM32CANFD fdcan3(&hfdcan3, 5);\n  LibXR::CAN& can3 = fdcan3;\n', code)
        self.assertIn('  XR_REGISTER(fdcan1, LibXR::FDCAN);\n  XR_REGISTER(can1, LibXR::CAN);\n', code)
        self.assertIn('  XR_REGISTER(fdcan3, LibXR::FDCAN);\n  XR_REGISTER(can3, LibXR::CAN);\n', code)
        self.assertLess(code.index('LibXR::CAN& can1'), code.index('XR_REGISTER(can1'))

    def test_fdcan_alias_is_xrobot_only(self):
        generator.initialize_registry(False)
        project = copy.deepcopy(self.project)
        project['Peripherals'] = {'FDCAN': {'FDCAN1': {}}, 'CAN': {'CAN1': {}}}
        code = generator.generate_full_code(project, False, '')
        self.assertIn('static STM32CANFD fdcan1', code)
        self.assertNotIn('LibXR::CAN& can1', code)

    def test_fdcan_alias_colliding_with_classic_can_is_rejected(self):
        for peripherals in ({'CAN': {'CAN1': {}}, 'FDCAN': {'FDCAN1': {}}},
                            {'FDCAN': {'FDCAN1': {}}, 'CAN': {'CAN1': {}}}):
            with self.subTest(order=list(peripherals)):
                generator.initialize_registry(True)
                project = copy.deepcopy(self.project)
                project['Peripherals'] = peripherals
                with self.assertRaisesRegex(ValueError, r"'can1'.*LibXR::CAN alias of fdcan1|LibXR::CAN alias of fdcan1.*'can1'"):
                    generator.generate_full_code(project, True, '')

    def test_every_registered_name_has_one_type(self):
        generator._register_device('usart1', 'UART')
        with self.assertRaisesRegex(ValueError, "'usart1'.*collides"):
            generator._register_device('usart1', 'GPIO')

    def test_dma_cache_alignment_builds_with_older_cmsis(self):
        """Cache-line alignment must not require __SCB_DCACHE_LINE_SIZE (absent in old F7 CMSIS)."""
        project = copy.deepcopy(self.project)
        project['Peripherals'] = {'ADC': {'ADC1': {'Channels': ['ADC_CHANNEL_0'],
                                                   'DMA_Request': {'Mode': 'DMA_CIRCULAR'}}}}
        code = generator.generate_dma_resources(project)
        self.assertIn('alignas(XR_DCACHE_LINE_SIZE)', code)
        self.assertNotIn('alignas(__SCB_DCACHE_LINE_SIZE)', code)
        self.assertIn('#if defined(__SCB_DCACHE_LINE_SIZE)', code)
        self.assertIn('#define XR_DCACHE_LINE_SIZE 32U', code)

    def test_generated_format_and_lint_bodies_are_refreshed(self):
        generated = generator.generate_full_code(self.project, True, '')
        existing = generated.replace(
            '// clang-format off\n',
            '// clang-format off\nlegacy_format_layout();\n',
            1,
        ).replace(
            '// NOLINTBEGIN\n',
            '// NOLINTBEGIN\nlegacy_lint_marker();\n',
            1,
        )
        regenerated = generator.generate_full_code(self.project, True, existing)
        self.assertNotIn('legacy_format_layout();', regenerated)
        self.assertNotIn('legacy_lint_marker();', regenerated)
        self.assertEqual(generated, regenerated)

    def test_repeat_generation_is_idempotent(self):
        first = generator.generate_full_code(self.project, True, '')
        second = generator.generate_full_code(self.project, True, first)
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
