"""xr_stm32_flash（libxr.stm32_flash_generator）：按型号输出 Flash 布局。
xr_stm32_flash (libxr.stm32_flash_generator): print the flash layout of a model.
"""

import contextlib
import io
import sys
import unittest
from unittest import mock

import yaml
from fixtures import TestCase

from libxr import stm32_flash_generator


class CommandLine(TestCase):
    """布局写到标准输出，用法和报错写到各自的流。
    The layout goes to stdout; usage and errors go to their own streams.
    """

    def run_flash(self, *argv):
        """以这些参数运行 xr_stm32_flash，返回退出码、标准输出和标准错误。
        Run xr_stm32_flash with these arguments; return the exit code, stdout and stderr.
        """
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["xr_stm32_flash", *argv]),
            mock.patch("libxr.package_info.LibXRPackageInfo.check_and_print"),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                stm32_flash_generator.main()
                code = 0
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

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
        self.assertTrue(out.startswith("STM32 Flash Information Tool\nUsage:\n"))
        code, out, err = self.run_flash()
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("STM32 Flash Information Tool\nUsage:\n"))

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
