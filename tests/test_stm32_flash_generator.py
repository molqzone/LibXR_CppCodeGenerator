"""libxr stm32 flash-info（libxr.stm32_flash_generator）：按型号输出 Flash 布局。
libxr stm32 flash-info (libxr.stm32_flash_generator): print the flash layout of a model.
"""

import unittest

import yaml
from fixtures import TestCase, run_libxr

from libxr.stm32_flash_generator import flash_info_to_dict, layout_flash


class SectorLayouts(TestCase):
    """布局规则按等大小、按序列或分 bank 排出扇区。
    Layout rules place the sectors as uniform sizes, as a sequence, or in banks.
    """

    def test_sequences_and_banks(self):
        # 除 H743VG 外，这些扇区表与 STM32CubeProgrammer 器件数据库一致（G474RB 为默认的双
        # bank）；数据库中没有 1 MB 的 H743。
        # Except for H743VG, these sector tables match the STM32CubeProgrammer device database
        # (G474RB in its default dual-bank mode); the database has no 1 MB H743.
        f4_bank = ((16, 4), (64, 1), (128, 7))
        for model, banks in (
            ("STM32F746IGT6", {0x08000000: ((32, 4), (128, 1), (256, 3))}),
            ("STM32F413ZHT6", {0x08000000: ((16, 4), (64, 1), (128, 11))}),
            ("STM32F407IGH6", {0x08000000: f4_bank}),
            ("STM32F427ZIT6", {0x08000000: f4_bank, 0x08100000: f4_bank}),
            ("STM32G474RBT6", {0x08000000: ((2, 32),), 0x08040000: ((2, 32),)}),
            ("STM32H743VGT6", {0x08000000: ((128, 4),), 0x08100000: ((128, 4),)}),
        ):
            expected = []
            for address, runs in banks.items():
                for size_kb, count in runs:
                    for _ in range(count):
                        expected.append((f"0x{address:08X}", float(size_kb)))
                        address += size_kb * 1024
            with self.subTest(model=model):
                sectors = flash_info_to_dict(layout_flash(model))["sectors"]
                self.assertEqual([(s["address"], s["size_kb"]) for s in sectors], expected)


class CommandLine(TestCase):
    """布局写到标准输出，用法写到标准输出或标准错误，报错以错误日志给出。
    The layout goes to stdout, usage to stdout or stderr, and errors are logged.
    """

    def run_flash(self, *argv):
        """以这些参数运行 libxr stm32 flash-info，返回退出码、标准输出和标准错误。
        Run libxr stm32 flash-info with these arguments; return the exit code, stdout and stderr.
        """
        return run_libxr("stm32", "flash-info", *argv)

    def test_the_layout_is_yaml_on_stdout(self):
        code, out, err = self.run_flash("stm32f103c8t6")
        self.assertEqual((code, err), (0, ""))
        layout = yaml.safe_load(out)
        self.assertEqual(
            (layout["model"], layout["flash_base"], layout["flash_size_kb"]),
            ("STM32F103C8T6", "0x08000000", 64),
        )

    def test_a_rule_may_move_the_flash_base(self):
        code, out, _ = self.run_flash("STM32WB09KEV6")
        layout = yaml.safe_load(out)
        self.assertEqual(code, 0)
        self.assertEqual(
            (layout["flash_base"], layout["sectors"][0]["address"]), ("0x10040000",) * 2
        )

    def test_help_and_wrong_usage(self):
        code, out, err = self.run_flash("--help")
        self.assertEqual((code, err), (0, ""))
        usage = "usage: libxr stm32 flash-info [-h] [--verbose] model\n"
        self.assertTrue(out.startswith(usage), out)
        self.assertIn("libxr stm32 flash-info STM32F103C8T6", out)
        code, out, err = self.run_flash()
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith(usage), err)

    def test_errors_are_logged(self):
        for model, reason in (
            ("STM32", "Invalid STM32 model format: STM32"),
            ("STM32U575Z0T6", "Unrecognized capacity code for STM32U575Z0T6"),
            ("STM32WBA62C", "Unrecognized capacity code for STM32WBA62C"),
            ("STM32MP157CAA3", "STM32MP157CAA3 does not provide internal user flash"),
            (
                "STM32Q999RGT6",
                "Unknown STM32 series of STM32Q999RGT6; its flash layout cannot be derived",
            ),
        ):
            # 以前外层再加一句 Failed to process model <型号>，型号出现两次。
            # An outer "Failed to process model <model>" used to repeat the model.
            with self.subTest(model=model), self.assertLogs(level="ERROR") as logs:
                code, out, _ = self.run_flash(model)
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(logs.output, [f"ERROR:root:{reason}"])


if __name__ == "__main__":
    unittest.main()
