"""CMake integration written by xr_stm32_cmake."""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from libxr import generator_code_stm32 as generator
from libxr import generator_stm32_cmake as stm32_cmake

PROJECT = {"Mcu": {"Type": "STM32F407IGH6", "Family": "STM32F4"}, "GPIO": {}, "Peripherals": {}}


class ModulesDirectory(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        (self.project / "User").mkdir()
        (self.project / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")

    def write_app_main(self, use_xrobot):
        importlib.reload(generator)
        generator.initialize_registry(use_xrobot)
        code = generator.generate_full_code(PROJECT, use_xrobot, "")
        (self.project / "User" / "app_main.cpp").write_text(code, encoding="utf-8")
        return code

    def run_cmake_generator(self):
        with (
            patch("sys.argv", ["xr_stm32_cmake", str(self.project)]),
            patch("libxr.package_info.LibXRPackageInfo.check_and_print"),
        ):
            stm32_cmake.main()
        return (self.project / "cmake" / "LibXR.CMake").read_text(encoding="utf-8")

    def test_xrobot_project_sets_modules_directory(self):
        self.write_app_main(True)
        self.assertIn(
            "set(LIBXR_DRIVER st)\nset(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)\n"
            "add_subdirectory(Middlewares/Third_Party/LibXR)",
            self.run_cmake_generator(),
        )

    def test_libxr_project_does_not_set_modules_directory(self):
        self.write_app_main(False)
        text = self.run_cmake_generator()
        self.assertNotIn("XROBOT_MODULES_DIR", text)
        self.assertIn("set(LIBXR_DRIVER st)\nadd_subdirectory(Middlewares/Third_Party/LibXR)", text)

    def test_project_without_generated_code_is_libxr_only(self):
        self.assertNotIn("XROBOT_MODULES_DIR", self.run_cmake_generator())

    def test_include_inside_user_code_is_not_the_xrobot_choice(self):
        code = self.write_app_main(False).replace(
            "/* User Code Begin 1 */", '/* User Code Begin 1 */\n#include "xrobot_main.hpp"', 1
        )
        (self.project / "User" / "app_main.cpp").write_text(code, encoding="utf-8")
        self.assertFalse(stm32_cmake.project_uses_xrobot(str(self.project)))

    def test_existing_file_is_kept_and_mismatch_reported(self):
        self.write_app_main(False)
        (self.project / "cmake").mkdir()
        existing = (
            "set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_CXX_STANDARD_REQUIRED ON)\n\n"
            "set(LIBXR_SYSTEM None)\nset(LIBXR_DRIVER st)\n"
            "set(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)\n"
            "target_compile_features(xr PUBLIC cxx_std_20)\n"
            "set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES\n)\n"
        )
        (self.project / "cmake" / "LibXR.CMake").write_text(existing, encoding="utf-8")
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(self.run_cmake_generator(), existing)
        self.assertIn("remove that line", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
