"""libxr stm32 flash-info（libxr.stm32_flash_generator）：按型号输出 Flash 布局。
libxr stm32 flash-info (libxr.stm32_flash_generator): print the flash layout of a model.
"""

import unittest

import yaml
from fixtures import TestCase, run_libxr


class CommandLine(TestCase):
    """布局写到标准输出，用法和报错写到各自的流。
    The layout goes to stdout; usage and errors go to their own streams.
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

    def test_help_and_wrong_usage(self):
        code, out, err = self.run_flash("--help")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("usage: libxr stm32 flash-info [-h] model\n"), out)
        self.assertIn("libxr stm32 flash-info STM32F103C8T6", out)
        code, out, err = self.run_flash()
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("usage: libxr stm32 flash-info [-h] model\n"), err)

    def test_errors_go_to_stderr(self):
        for model, reason in (
            ("STM32", "Invalid STM32 model format: STM32"),
            ("STM32U575Z0T6", "Unrecognized capacity code for STM32U575Z0T6"),
        ):
            with self.subTest(model=model):
                code, out, err = self.run_flash(model)
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(err, f"Failed to process model {model}: {reason}\n")


if __name__ == "__main__":
    unittest.main()
