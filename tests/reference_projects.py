"""四个真实 BSP 工程的基准数据：.ioc 和 libxr_config.yaml 输入，以及解析和生成的预期结果。
Reference data of four real BSP projects: the .ioc and libxr_config.yaml inputs, and the
expected results of parsing and generation.

tests/data/<名字>/ 中，project.ioc 和 libxr_config.yaml 来自 bsp-dev-c（STM32F4、CAN、I2C、
OTG USB）、bsp-dev-mc02（STM32H7、FDCAN、DAC）、BSP-OpenCR-1.0（STM32F7、IWDG）和
bsp_stm32f103（STM32F1、FSDEV USB），四者都用 FreeRTOS；expected/ 是按 BSP CI 的命令得到的
文件。生成结果有意改变时，运行本文件刷新 expected/，再逐行检查差异。
In tests/data/<name>/, project.ioc and libxr_config.yaml come from bsp-dev-c (STM32F4, CAN,
I2C, OTG USB), bsp-dev-mc02 (STM32H7, FDCAN, DAC), BSP-OpenCR-1.0 (STM32F7, IWDG) and
bsp_stm32f103 (STM32F1, FSDEV USB), all with FreeRTOS; expected/ holds the files the BSP CI
commands produce. When the output changes on purpose, run this file to refresh expected/ and
review the difference line by line.
"""

import shutil
from pathlib import Path

from fixtures import run_libxr

DATA = Path(__file__).resolve().parent / "data"
PROJECTS = ("devc", "mc02", "opencr", "f103")
GENERATED = ("app_main.cpp", "app_main.h", "flash_map.hpp", "libxr_config.yaml")


def _run(*argv):
    """以这些参数运行 libxr 命令；失败时抛出带标准错误的 AssertionError。
    Run the libxr command with these arguments; a failure raises an AssertionError with stderr.
    """
    code, _, err = run_libxr(*argv)
    assert code == 0, err


def parse(name, directory):
    """在 directory 中对工程 name 运行 libxr parse，返回写出的 cubemx.yaml。
    Run libxr parse for project name in directory; return the cubemx.yaml it writes.
    """
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(DATA / name / "project.ioc", directory / "project.ioc")
    output = directory / "cubemx.yaml"
    _run("parse", "-d", str(directory), "-o", str(output))
    return output


def generate(name, cubemx_yaml, directory):
    """以 cubemx_yaml 和工程 name 的 libxr_config.yaml 运行 libxr gen --xrobot，返回
    User/ 目录。
    Run libxr gen --xrobot with cubemx_yaml and the libxr_config.yaml of project name;
    return the User/ folder.
    """
    user = directory / "User"
    user.mkdir(parents=True, exist_ok=True)
    config = user / "libxr_config.yaml"
    shutil.copy(DATA / name / "libxr_config.yaml", config)
    output = str(user / "app_main.cpp")
    options = ["--xrobot", "--libxr-config", str(config), "-d", str(DATA / name)]
    _run("gen", "-i", str(cubemx_yaml), "-o", output, *options)
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
