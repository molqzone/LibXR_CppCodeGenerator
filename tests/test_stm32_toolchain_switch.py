"""libxr stm32 toolchain（libxr.stm32_toolchain_switch）：切换工具链和 ST Arm Clang 运行库
配置，以及运行库配置的 -D 选择。
libxr stm32 toolchain (libxr.stm32_toolchain_switch): switching the toolchain and the ST
Arm Clang runtime profile, and the -D selection of the profile.
"""

import contextlib
import io
import json
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


# CubeMX 生成的 CMakePresets.json 的 default preset 部分。
# The default preset part of a CubeMX-generated CMakePresets.json.
def presets(toolchain):
    """default preset 使用 cmake/<toolchain> 的 CMakePresets.json 内容。
    The content of a CMakePresets.json whose default preset uses cmake/<toolchain>.
    """
    return {
        "version": 3,
        "configurePresets": [
            {
                "name": "default",
                "hidden": True,
                "generator": "Ninja",
                "binaryDir": "${sourceDir}/build/${presetName}",
                "toolchainFile": "${sourceDir}/cmake/" + toolchain,
            },
            {"name": "debug", "inherits": "default"},
        ],
    }


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
        (self.project / "CMakePresets.json").write_text(
            json.dumps(presets("starm-clang.cmake")), encoding="utf-8"
        )

    def normalize(self, text):
        """写入工具链文件并规范化，返回结果。
        Write the toolchain file, normalize it and return the result.
        """
        self.toolchain.write_text(text, encoding="utf-8")
        stm32_cmake.normalize_starm_clang_toolchain(self.toolchain)
        return self.toolchain.read_text(encoding="utf-8")

    def switch(self, profile):
        """以 libxr stm32 toolchain clang 把工程的运行库配置切换为 profile。
        Switch the runtime profile of the project to profile with libxr stm32 toolchain clang.
        """
        std = {value: key for key, value in toolchain_switch.STD_MAP.items()}[profile]
        with contextlib.redirect_stderr(io.StringIO()):
            toolchain_switch.switch_toolchain(str(self.project), "clang", std)

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


class SwitchToolchain(TestCase):
    """切换默认 preset 的工具链：先检查再修改，工具链改变时删除旧的构建目录。
    Switching the toolchain of the default preset: check first, then change, and remove the old
    build directories when the toolchain changes.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / "cmake").mkdir()
        (self.project / "cmake" / "gcc-arm-none-eabi.cmake").write_text("", encoding="utf-8")
        self.starm = self.project / "cmake" / "starm-clang.cmake"
        self.starm.write_text(CUBEMX_STARM, encoding="utf-8")
        self.presets = self.project / "CMakePresets.json"
        self.presets.write_text(json.dumps(presets("gcc-arm-none-eabi.cmake")), encoding="utf-8")
        for folder in ("build/debug", "cmake-build-release", "keep"):
            (self.project / folder).mkdir(parents=True)

    def switch(self, compiler, std=None):
        """运行 switch_toolchain，返回退出码（成功时为 0）。
        Run switch_toolchain and return the exit code, 0 on success.
        """
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                toolchain_switch.switch_toolchain(str(self.project), compiler, std)
            except SystemExit as exit:
                return exit.code
        return 0

    def toolchain_file(self):
        """CMakePresets.json 中 default preset 的工具链文件。
        The toolchain file of the default preset in CMakePresets.json.
        """
        data = json.loads(self.presets.read_text(encoding="utf-8"))
        return data["configurePresets"][0]["toolchainFile"]

    def folders(self):
        """工程根目录中的目录名。
        The folder names in the project root.
        """
        return sorted(p.name for p in self.project.iterdir() if p.is_dir())

    def test_a_new_compiler_removes_the_build_directories(self):
        self.assertEqual(self.switch("clang", "newlib"), 0)
        self.assertEqual(self.toolchain_file(), "${sourceDir}/cmake/starm-clang.cmake")
        self.assertIn('set(STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB")', self.starm.read_text("utf-8"))
        self.assertEqual(self.folders(), ["cmake", "keep"])

    def test_the_same_compiler_keeps_the_build_directories(self):
        self.assertEqual(self.switch("gcc"), 0)
        self.assertEqual(self.folders(), ["build", "cmake", "cmake-build-release", "keep"])

    def test_clang_without_a_library_keeps_the_current_one(self):
        self.assertEqual(self.switch("clang"), 0)
        self.assertEqual(self.toolchain_file(), "${sourceDir}/cmake/starm-clang.cmake")
        self.assertEqual(self.starm.read_text(encoding="utf-8"), CUBEMX_STARM)

    def test_a_failed_check_changes_nothing(self):
        def missing_gcc():
            self.presets.write_text(json.dumps(presets("starm-clang.cmake")), encoding="utf-8")
            (self.project / "cmake" / "gcc-arm-none-eabi.cmake").unlink()

        cases = (
            ("gcc with a library", lambda: None, ("gcc", "hybrid")),
            ("missing clang toolchain", self.starm.unlink, ("clang", "hybrid")),
            ("missing gcc toolchain", missing_gcc, ("gcc",)),
        )
        for name, prepare, arguments in cases:
            with self.subTest(case=name):
                prepare()
                before = self.presets.read_text(encoding="utf-8")
                self.assertEqual(self.switch(*arguments), 1)
                self.assertEqual(self.presets.read_text(encoding="utf-8"), before)
                self.assertIn("build", self.folders())

    def test_a_profile_line_is_needed_before_anything_changes(self):
        before = self.presets.read_text(encoding="utf-8")
        self.starm.write_text("set(CMAKE_SYSTEM_NAME Generic)\n", encoding="utf-8")
        self.assertEqual(self.switch("clang", "hybrid"), 1)
        self.assertEqual(self.presets.read_text(encoding="utf-8"), before)
        self.assertIn("build", self.folders())

    def test_invalid_presets_are_an_error(self):
        for content, message in (
            (
                b"{ broken",
                "line 1, column 3: Expecting property name enclosed in double quotes",
            ),
            ('{"name": "调试"}'.encode("gbk"), "is not UTF-8 text (byte 11); save it as UTF-8"),
        ):
            with self.subTest(content=content):
                self.presets.write_bytes(content)
                with self.assertLogs(level="ERROR") as logs:
                    self.assertEqual(self.switch("clang"), 1)
                self.assertEqual(logs.output, [f"ERROR:root:{self.presets} {message}"])
                self.assertEqual(self.presets.read_bytes(), content)
                self.assertIn("build", self.folders())

    def test_only_the_toolchain_value_changes(self):
        # 以前整个文件按 4 空格缩进重写：2 空格缩进的文件每行都变，中文写成 调试。
        # The whole file used to be rewritten with 4-space indentation: every line of a
        # 2-space file changed, and Chinese text became 调试.
        data = presets("gcc-arm-none-eabi.cmake")
        data["configurePresets"][1]["displayName"] = "调试"
        text = json.dumps(data, indent=2, ensure_ascii=False).replace("\n", "\r\n") + "\r\n"
        self.presets.write_bytes(text.encode("utf-8"))
        starm = CUBEMX_STARM.replace("\n", "\r\n").encode("utf-8")
        self.starm.write_bytes(starm)
        self.assertEqual(self.switch("clang", "newlib"), 0)
        self.assertEqual(
            self.presets.read_bytes(),
            text.replace("gcc-arm-none-eabi.cmake", "starm-clang.cmake").encode("utf-8"),
        )
        self.assertEqual(self.starm.read_bytes(), starm.replace(b"PICOLIBC", b"NEWLIB", 1))

    def test_a_default_preset_without_a_toolchain_gets_one(self):
        data = presets("gcc-arm-none-eabi.cmake")
        del data["configurePresets"][0]["toolchainFile"]
        data["configurePresets"][1]["displayName"] = "调试"
        self.presets.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.switch("gcc"), 0)
        self.assertEqual(self.toolchain_file(), "${sourceDir}/cmake/gcc-arm-none-eabi.cmake")
        self.assertIn('"displayName": "调试"', self.presets.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
