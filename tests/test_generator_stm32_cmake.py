"""libxr stm32 cmake 写入的 CMake 集成（libxr.generator_stm32_cmake）。
The CMake integration written by libxr stm32 cmake (libxr.generator_stm32_cmake).
"""

import tempfile
import unittest
from pathlib import Path

from fixtures import CUBEMX_STARM, GeneratorTestCase

from libxr import generator_stm32_cmake as stm32_cmake


class Integration(GeneratorTestCase):
    """把 LibXR 接入工程：LibXR.CMake（XRobot 工程另设 XROBOT_MODULES_DIR）、CMakeLists.txt 的
    include 和 starm-clang.cmake 的运行库配置。
    Integrating LibXR into a project: LibXR.CMake (XRobot projects also set
    XROBOT_MODULES_DIR), the include in CMakeLists.txt, and the runtime profile of
    starm-clang.cmake.
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

    def test_the_modules_directory_follows_app_main(self):
        libxr = "set(LIBXR_DRIVER st)\nadd_subdirectory(Middlewares/Third_Party/LibXR)"
        xrobot = (
            "set(LIBXR_DRIVER st)\nset(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)\n"
            "add_subdirectory(Middlewares/Third_Party/LibXR)"
        )
        # 还没有生成 app_main 的工程按纯 LibXR 工程处理。
        # A project without a generated app_main counts as a LibXR-only project.
        for use_xrobot, block in ((True, xrobot), (False, libxr), (None, libxr)):
            with self.subTest(use_xrobot=use_xrobot):
                self.setUp()
                if use_xrobot is not None:
                    self.write_app_main(use_xrobot)
                text = self.run_cmake_generator()
                self.assertIn(block, text)
                self.assertEqual("XROBOT_MODULES_DIR" in text, use_xrobot is True)

    def test_include_inside_user_code_is_not_the_xrobot_choice(self):
        code = self.write_app_main(False).replace(
            "/* User Code Begin 1 */", '/* User Code Begin 1 */\n#include "xrobot_main.hpp"', 1
        )
        (self.root / "User" / "app_main.cpp").write_text(code, encoding="utf-8")
        self.assertFalse(stm32_cmake.project_uses_xrobot(str(self.root)))

    def test_an_existing_file_follows_the_xrobot_choice(self):
        # 以前只给出警告，切换到 XRobot 后要手动加这一行，否则模块不参与构建。
        # Only a warning used to be given; after switching to XRobot the line had to be added
        # by hand, or the Modules were not built.
        head = (
            "set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_CXX_STANDARD_REQUIRED ON)\n\n"
            "set(LIBXR_SYSTEM None)\nset(LIBXR_DRIVER st)\n"
        )
        modules = "set(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)\n"
        library = "add_subdirectory(Middlewares/Third_Party/LibXR)\n"
        for use_xrobot, before, after, messages in (
            (
                False,
                head + modules + library,
                head + library,
                [
                    "INFO:root:LibXR.CMake: removed XROBOT_MODULES_DIR, as User/app_main.cpp "
                    "does not use XRobot"
                ],
            ),
            (False, head + library, head + library, []),
            (
                True,
                head + library,
                head + modules + library,
                [
                    f"INFO:root:LibXR.CMake: added {modules.strip()}, as User/app_main.cpp uses XRobot"
                ],
            ),
            (
                True,
                head,
                head,
                [
                    "WARNING:root:User/app_main.cpp uses XRobot, but LibXR.CMake does not set "
                    f"XROBOT_MODULES_DIR; add {modules.strip()} before "
                    "add_subdirectory(Middlewares/Third_Party/LibXR)"
                ],
            ),
        ):
            with self.subTest(use_xrobot=use_xrobot, before=before):
                self.setUp()
                self.write_app_main(use_xrobot)
                (self.root / "cmake").mkdir()
                (self.root / "cmake" / "LibXR.CMake").write_text(before, encoding="utf-8")
                with self.assertLogs(level="INFO") as logs:
                    self.assertEqual(self.run_cmake_generator(), after)
                self.assertEqual(
                    [line for line in logs.output if "XROBOT_MODULES_DIR" in line], messages
                )

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

    def test_an_include_in_any_spelling_is_not_appended_again(self):
        # 以前只比较整行文字，下面前两种写法会再追加一行 include，CMake 配置失败。
        # Comparing whole lines used to append a second include for the first two, which fails
        # the CMake configure.
        cmakelists = self.root / "CMakeLists.txt"
        for text, appended in (
            ("project(demo)\ninclude(${CMAKE_CURRENT_LIST_DIR}/cmake/LibXR.CMake)", False),
            ("project(demo)\nINCLUDE( cmake/LibXR.CMake )\n", False),
            ("project(demo)\n# include(${CMAKE_CURRENT_LIST_DIR}/cmake/LibXR.CMake)\n", True),
        ):
            with self.subTest(text=text):
                cmakelists.write_text(text, encoding="utf-8")
                self.run_cmake_generator()
                expected = text + (
                    "\n# Add LibXR\n" + stm32_cmake.include_cmake_cmd if appended else ""
                )
                self.assertEqual(cmakelists.read_text(encoding="utf-8"), expected)

    def test_the_st_arm_clang_profile_is_prepared(self):
        (self.root / "cmake").mkdir()
        toolchain = self.root / "cmake" / "starm-clang.cmake"
        toolchain.write_text(CUBEMX_STARM, encoding="utf-8")
        self.run_cmake_generator()
        self.assertIn(
            '  message(FATAL_ERROR "Unknown STARM_TOOLCHAIN_CONFIG: ${STARM_TOOLCHAIN_CONFIG}")\n',
            toolchain.read_text(encoding="utf-8"),
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
