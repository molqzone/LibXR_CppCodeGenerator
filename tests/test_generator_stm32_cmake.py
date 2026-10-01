"""libxr stm32 cmake 写入的 CMake 集成（libxr.generator_stm32_cmake）。
The CMake integration written by libxr stm32 cmake (libxr.generator_stm32_cmake).
"""

import tempfile
import unittest
from pathlib import Path

from fixtures import GeneratorTestCase

from libxr import generator_stm32_cmake as stm32_cmake


class ModulesDirectory(GeneratorTestCase):
    """app_main 使用 XRobot 时 LibXR.CMake 设置 XROBOT_MODULES_DIR。
    LibXR.CMake sets XROBOT_MODULES_DIR when app_main uses XRobot.
    """

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "User").mkdir()
        (self.root / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")

    def write_app_main(self, use_xrobot):
        """生成并写入 User/app_main.cpp，返回其文本。
        Generate and write User/app_main.cpp; return its text.
        """
        code = self.generate(use_xrobot=use_xrobot)
        (self.root / "User" / "app_main.cpp").write_text(code, encoding="utf-8")
        return code

    def run_cmake_generator(self):
        """把 LibXR 接入工程，返回 cmake/LibXR.CMake 的文本。
        Integrate LibXR into the project and return the text of cmake/LibXR.CMake.
        """
        stm32_cmake.integrate(str(self.root))
        return (self.root / "cmake" / "LibXR.CMake").read_text(encoding="utf-8")

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
        (self.root / "User" / "app_main.cpp").write_text(code, encoding="utf-8")
        self.assertFalse(stm32_cmake.project_uses_xrobot(str(self.root)))

    def test_existing_file_is_kept_and_mismatch_reported(self):
        self.write_app_main(False)
        (self.root / "cmake").mkdir()
        existing = (
            "set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_CXX_STANDARD_REQUIRED ON)\n\n"
            "set(LIBXR_SYSTEM None)\nset(LIBXR_DRIVER st)\n"
            "set(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)\n"
            "target_compile_features(xr PUBLIC cxx_std_20)\n"
            "set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES\n)\n"
        )
        (self.root / "cmake" / "LibXR.CMake").write_text(existing, encoding="utf-8")
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(self.run_cmake_generator(), existing)
        self.assertIn("remove that line", "\n".join(logs.output))

    def test_build_directories_are_kept(self):
        (self.root / "build" / "debug").mkdir(parents=True)
        self.run_cmake_generator()
        self.assertTrue((self.root / "build" / "debug").is_dir())

    def test_new_user_sources_are_globbed_at_build_time(self):
        self.assertIn(
            'GLOB LIBXR_USER_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/User/*.cpp"',
            self.run_cmake_generator(),
        )
        path = self.root / "cmake" / "LibXR.CMake"
        older = path.read_text(encoding="utf-8").replace(" CONFIGURE_DEPENDS", "")
        path.write_text(older, encoding="utf-8")
        self.assertEqual(
            self.run_cmake_generator(),
            older.replace("GLOB LIBXR_USER_SOURCES", "GLOB LIBXR_USER_SOURCES CONFIGURE_DEPENDS"),
        )

    def test_a_directory_without_cmakelists_is_left_untouched(self):
        (self.root / "CMakeLists.txt").unlink()
        (self.root / "build").mkdir()
        with self.assertLogs(level="ERROR") as logs, self.assertRaises(SystemExit) as exit:
            self.run_cmake_generator()
        self.assertEqual(exit.exception.code, 1)
        self.assertEqual(logs.output, [f"ERROR:root:{self.root / 'CMakeLists.txt'} not found."])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["User", "build"])


if __name__ == "__main__":
    unittest.main()
