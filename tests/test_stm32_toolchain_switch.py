"""ST Arm Clang 运行库配置（libxr.stm32_toolchain_switch）：-D 选择和 libxr stm32 toolchain。
The ST Arm Clang runtime profile (libxr.stm32_toolchain_switch): -D selection and
libxr stm32 toolchain.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from fixtures import TestCase

from libxr import generator_stm32_cmake as stm32_cmake
from libxr import stm32_toolchain_switch as toolchain_switch

# CubeMX 生成的 cmake/starm-clang.cmake 中的运行库配置段。
# The profile section of a CubeMX-generated cmake/starm-clang.cmake.
CUBEMX_STARM = textwrap.dedent("""\
    set(CMAKE_SYSTEM_NAME               Generic)
    set(CMAKE_SYSTEM_PROCESSOR          arm)

    set(STARM_TOOLCHAIN_CONFIG "STARM_PICOLIBC")

    if(STARM_TOOLCHAIN_CONFIG STREQUAL "STARM_HYBRID")
      set(TOOLCHAIN_MULTILIBS "--hybrid")
    elseif (STARM_TOOLCHAIN_CONFIG STREQUAL "STARM_NEWLIB")
      set(TOOLCHAIN_MULTILIBS "--config=newlib.cfg")
    endif()
    """)

# 早期版本的 xr_stm32_cmake（libxr stm32 cmake）改写后的同一段。
# The same section as earlier xr_stm32_cmake (libxr stm32 cmake) versions rewrote it.
CACHED_STARM = CUBEMX_STARM.replace(
    'set(STARM_TOOLCHAIN_CONFIG "STARM_PICOLIBC")\n\n',
    'set(STARM_TOOLCHAIN_CONFIG "STARM_PICOLIBC" CACHE STRING "ST Arm Clang runtime profile")\n'
    "set_property(CACHE STARM_TOOLCHAIN_CONFIG PROPERTY STRINGS\n"
    "             STARM_HYBRID STARM_NEWLIB STARM_PICOLIBC)\n",
)


class StarmProfile(TestCase):
    """libxr stm32 cmake 规范化运行库配置行，libxr stm32 toolchain 只改写这一行。
    libxr stm32 cmake normalizes the profile line, and libxr stm32 toolchain rewrites only
    that line.
    """

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        (self.project / "cmake").mkdir()
        self.toolchain = self.project / "cmake" / "starm-clang.cmake"
        previous = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)

    def normalize(self, text):
        """写入工具链文件并规范化，返回结果。
        Write the toolchain file, normalize it and return the result.
        """
        self.toolchain.write_text(text, encoding="utf-8")
        stm32_cmake.normalize_starm_clang_toolchain(self.toolchain)
        return self.toolchain.read_text(encoding="utf-8")

    def switch(self, profile):
        """把当前目录工程的运行库配置切换为 profile。
        Switch the runtime profile of the project in the current directory to profile.
        """
        with contextlib.redirect_stderr(io.StringIO()):
            toolchain_switch.patch_clang_stdlib(profile)

    def test_default_line_stays_the_only_switch_target(self):
        text = self.normalize(CUBEMX_STARM)
        self.assertEqual(text.count('set(STARM_TOOLCHAIN_CONFIG "'), 1)
        self.assertIn('set(STARM_TOOLCHAIN_CONFIG "STARM_PICOLIBC")\n# LibXR:', text)
        self.assertIn("Unknown STARM_TOOLCHAIN_CONFIG", text)
        self.assertEqual(self.normalize(text), text)
        self.switch("STARM_NEWLIB")
        self.assertEqual(
            self.toolchain.read_text(encoding="utf-8"),
            text.replace('"STARM_PICOLIBC")\n# LibXR:', '"STARM_NEWLIB")\n# LibXR:'),
        )

    def test_the_current_profile_is_not_rewritten(self):
        self.normalize(CUBEMX_STARM)
        os.utime(self.toolchain, ns=(10**9, 10**9))
        self.switch("STARM_PICOLIBC")
        self.assertEqual(self.toolchain.stat().st_mtime_ns, 10**9)

    def test_cached_default_from_earlier_versions_is_converted(self):
        def lines(text):
            return [line for line in text.splitlines() if line.strip()]

        self.assertEqual(lines(self.normalize(CACHED_STARM)), lines(self.normalize(CUBEMX_STARM)))

    @unittest.skipUnless(shutil.which("cmake"), "cmake is required to configure a build directory")
    def test_command_line_selection_and_switch_reach_the_build_directory(self):
        (self.project / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.20)\nproject(demo NONE)\n"
            'message(STATUS "PROFILE=${STARM_TOOLCHAIN_CONFIG}")\n',
            encoding="utf-8",
        )
        self.normalize(CUBEMX_STARM)

        def configure(build, *arguments):
            if not (self.project / build).exists():
                arguments = (
                    "-S",
                    ".",
                    "-B",
                    build,
                    "-DCMAKE_TOOLCHAIN_FILE=cmake/starm-clang.cmake",
                ) + arguments
            else:
                arguments = (build,) + arguments
            result = subprocess.run(
                ["cmake", *arguments], cwd=self.project, check=True, capture_output=True, text=True
            )
            return [
                line.split("=", 1)[1]
                for line in result.stdout.splitlines()
                if line.startswith("-- PROFILE=")
            ]

        self.assertEqual(configure("default"), ["STARM_PICOLIBC"])
        self.assertEqual(
            configure("selected", "-DSTARM_TOOLCHAIN_CONFIG=STARM_HYBRID"), ["STARM_HYBRID"]
        )
        self.assertEqual(configure("selected"), ["STARM_HYBRID"])
        self.switch("STARM_NEWLIB")
        self.assertEqual(configure("default"), ["STARM_NEWLIB"])
        self.assertEqual(configure("selected"), ["STARM_NEWLIB"])
        self.assertEqual(
            configure("default", "-DSTARM_TOOLCHAIN_CONFIG=STARM_HYBRID"), ["STARM_HYBRID"]
        )
        self.assertEqual(configure("default"), ["STARM_HYBRID"])


if __name__ == "__main__":
    unittest.main()
