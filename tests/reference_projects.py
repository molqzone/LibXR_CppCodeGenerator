"""两个真实 BSP 工程的基准数据：.ioc 和 libxr_config.yaml 输入，以及解析和生成的预期结果。
Reference data of two real BSP projects: the .ioc and libxr_config.yaml inputs, and the
expected results of parsing and generation.

tests/data/<名字>/ 中，project.ioc 和 libxr_config.yaml 来自 bsp-dev-c（STM32F4、FreeRTOS、CAN、
USB）和 bsp-dev-mc02（STM32H7、FDCAN）；expected/ 是按 BSP CI 的命令得到的文件。生成结果有意
改变时，运行本文件刷新 expected/，再逐行检查差异。
In tests/data/<name>/, project.ioc and libxr_config.yaml come from bsp-dev-c (STM32F4,
FreeRTOS, CAN, USB) and bsp-dev-mc02 (STM32H7, FDCAN); expected/ holds the files the BSP CI
commands produce. When the output changes on purpose, run this file to refresh expected/ and
review the difference line by line.
"""

import contextlib
import io
import shutil
import sys
from pathlib import Path
from unittest import mock

from libxr import generator_code_stm32, peripheral_analyzer_stm32

DATA = Path(__file__).resolve().parent / "data"
PROJECTS = ("devc", "mc02")
GENERATED = ("app_main.cpp", "app_main.h", "flash_map.hpp", "libxr_config.yaml")


def _run(main, argv):
    """以 argv 运行一个命令入口，不检查新版本，屏蔽标准输出。
    Run a command entry with argv, without the version check and with stdout suppressed.
    """
    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch("libxr.package_info.LibXRPackageInfo.check_and_print"),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        main()


def parse(name, directory):
    """在 directory 中对工程 name 运行 xr_parse_ioc，返回写出的 cubemx.yaml。
    Run xr_parse_ioc for project name in directory; return the cubemx.yaml it writes.
    """
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(DATA / name / "project.ioc", directory / "project.ioc")
    output = directory / "cubemx.yaml"
    _run(peripheral_analyzer_stm32.main, ["xr_parse_ioc", "-d", str(directory), "-o", str(output)])
    return output


def generate(name, cubemx_yaml, directory):
    """以 cubemx_yaml 和工程 name 的 libxr_config.yaml 运行 xr_gen_code_stm32 --xrobot，返回
    User/ 目录。
    Run xr_gen_code_stm32 --xrobot with cubemx_yaml and the libxr_config.yaml of project name;
    return the User/ folder.
    """
    user = directory / "User"
    user.mkdir(parents=True, exist_ok=True)
    config = user / "libxr_config.yaml"
    shutil.copy(DATA / name / "libxr_config.yaml", config)
    argv = ["xr_gen_code_stm32", "-i", str(cubemx_yaml), "-o", str(user / "app_main.cpp")]
    _run(generator_code_stm32.main, argv + ["--xrobot", "--libxr-config", str(config)])
    return user


def refresh():
    """重新生成每个工程的 expected/。
    Regenerate expected/ of every project.
    """
    import tempfile

    for name in PROJECTS:
        expected = DATA / name / "expected"
        expected.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            cubemx = parse(name, work / "parse")
            shutil.copy(cubemx, expected / "cubemx.yaml")
            user = generate(name, expected / "cubemx.yaml", work / "generate")
            for file in GENERATED:
                shutil.copy(user / file, expected / file)


if __name__ == "__main__":
    refresh()
