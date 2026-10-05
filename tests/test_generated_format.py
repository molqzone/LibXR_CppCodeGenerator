"""生成的 C++ 文件已按 LibXR 的 clang-format 风格排版：固定版本的 clang-format 不改动 User Code
区域之外的任何内容。
The generated C++ files are laid out in the clang-format style of LibXR already: the pinned
clang-format changes nothing outside the User Code regions.

固定版本和风格文件见 fixtures.py；没有安装这个版本的 clang-format 时跳过。
The pinned version and the style file are in fixtures.py; the tests are skipped without that
version of clang-format.
"""

import random
import re
import tempfile
import unittest
from pathlib import Path

import yaml
from fixtures import (
    DATA,
    IOC,
    GeneratorTestCase,
    clang_format,
    requires_clang_format,
)

from libxr import generator_code_stm32 as generator
from libxr import peripheral_analyzer_stm32

USER_REGION = re.compile(
    r"(/\* User Code Begin (\d+) \*/)(.*?)(/\* User Code End \2 \*/)", re.DOTALL
)
GENERATED_FILES = ("app_main.cpp", "app_main.h", "flash_map.hpp")


def without_user_code(text: str) -> str:
    """text 去掉各 User Code 区域的内容后的文本；区域内的代码由用户排版。
    The text without the body of each User Code region; the user formats the code in a region.
    """
    return USER_REGION.sub(r"\1\n\4", text)


class GeneratedFormat(GeneratorTestCase):
    """工程的生成结果与 clang-format 的结果相同。
    The result of generating a project equals the result of clang-format.
    """

    def generate_files(self, project: dict, config: str, use_xrobot: bool) -> dict[str, str]:
        """由工程数据和 libxr_config.yaml 的内容生成各文件，返回 {文件名: 内容}。
        Generate the files from the project data and the content of libxr_config.yaml and
        return {file name: content}.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = root / "User"
            user.mkdir()
            (root / "cubemx.yaml").write_text(yaml.safe_dump(project), encoding="utf-8")
            (user / "libxr_config.yaml").write_text(config, encoding="utf-8")
            with self.assertLogs(level="INFO"):
                generator.generate(
                    str(root / "cubemx.yaml"), str(user / "app_main.cpp"), use_xrobot
                )
            return {
                name: (user / name).read_text(encoding="utf-8")
                for name in GENERATED_FILES
                if (user / name).exists()
            }

    def assertFormatted(self, files: dict[str, str]) -> None:
        """断言每个文件在 User Code 区域之外与 clang-format 的结果相同。
        Assert that every file equals the clang-format result outside the User Code regions.
        """
        for name, text in files.items():
            with self.subTest(file=name):
                self.assertEqual(
                    without_user_code(clang_format(text, name)), without_user_code(text)
                )

    def fixture(self, name: str) -> tuple[dict, str]:
        """tests/data 中名为 name 的真实工程：libxr parse 写出的 YAML 和 libxr_config.yaml。
        The real project called name in tests/data: the YAML libxr parse wrote and its
        libxr_config.yaml.
        """
        project = yaml.safe_load((DATA / f"{name}.yaml").read_text(encoding="utf-8"))
        return project, (DATA / f"{name}_libxr_config.yaml").read_text(encoding="utf-8")


@requires_clang_format
class RealProjects(GeneratedFormat):
    """DevC（STM32F407，没有数据 cache；USB OTG HS 带两路 CDC，启用数据库）和 MC02（STM32H723，
    有数据 cache，缓冲区放在指定的段）。
    DevC (STM32F407, no data cache; the USB OTG HS with two CDCs and the database enabled) and
    MC02 (STM32H723, with a data cache and the buffers in given sections).
    """

    def assertProjectFormatted(self, name: str) -> None:
        """断言真实工程 name 的各文件排版正确；入口源文件另在没有 XRobot 时检查。
        Assert that the files of the real project name are laid out correctly; the entry source
        is also checked without XRobot.
        """
        project, config = self.fixture(name)
        self.assertFormatted(self.generate_files(project, config, True))
        plain = self.generate_files(project, config, False)
        self.assertFormatted({"app_main.cpp": plain["app_main.cpp"]})

    def test_devc_is_formatted(self):
        self.assertProjectFormatted("devc")

    def test_mc02_is_formatted(self):
        self.assertProjectFormatted("mc02")

    def test_a_project_parsed_from_an_ioc_file_is_formatted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "demo.ioc").write_text(IOC, encoding="utf-8")
            peripheral_analyzer_stm32.parse_project(str(root), str(root / "cubemx.yaml"), False)
            project = yaml.safe_load((root / "cubemx.yaml").read_text(encoding="utf-8"))
        config = "terminal_source: usart1\nUSB:\n  usb_otg_fs:\n    enable: true\n"
        config += "  usb_otg_hs:\n    enable: true\n"
        self.assertFormatted(self.generate_files(project, config, True))

    def test_user_code_is_left_to_the_user(self):
        project, config = self.fixture("devc")
        files = self.generate_files(project, config, True)
        # 用户区中排版不合风格的代码不影响其余部分的检查。
        # Code in a user region that does not follow the style does not affect the check of the
        # rest.
        text = files["app_main.cpp"].replace(
            "  /* User Code End 3 */", "  CustomCall(   1,2 );\n  /* User Code End 3 */"
        )
        self.assertNotEqual(clang_format(text, "app_main.cpp"), text)
        self.assertEqual(
            without_user_code(clang_format(text, "app_main.cpp")), without_user_code(text)
        )
        self.assertIn("CustomCall(   1,2 );", text)


@requires_clang_format
class RandomProjects(GeneratedFormat):
    """随机的工程：标签、名字和缓冲区大小各不相同，覆盖语句的各种断行。
    Random projects with different labels, names and buffer sizes, covering the line breaks of
    the statements.
    """

    FAMILIES = (
        ("STM32F407IGH6", "STM32F4"),
        ("STM32H723VGT6", "STM32H7"),
        ("STM32F746VGT6", "STM32F7"),
        ("STM32H503RBT6", "STM32H5"),
    )

    @staticmethod
    def word(rng: random.Random, low: int, high: int) -> str:
        """长度在 low 到 high 之间的随机标识符。
        A random identifier of a length between low and high.
        """
        length = rng.randint(low, high)
        text = rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
        for _ in range(length - 1):
            text += rng.choice(
                "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789" + ("" if text[-1] == "_" else "_")
            )
        return text.rstrip("_") or "A"

    def project_and_config(self, rng: random.Random) -> tuple[dict, dict]:
        """随机的工程数据和 libxr_config.yaml 的设置。
        Random project data and settings for libxr_config.yaml.
        """
        mcu, family = rng.choice(self.FAMILIES)
        used = set()
        gpio = {}
        for number in range(rng.randint(0, 14)):
            label = self.word(rng, 3, 40)
            while label in used or label.endswith(("_Pin", "_Port")):
                label = self.word(rng, 3, 40)
            used.add(label)
            entry = {"Label": label}
            if rng.random() < 0.3:
                entry["GPXTI"] = True
            gpio[f"P{rng.choice('ABCDE')}{number}"] = entry
        dma = {"DMA_TX": "ENABLE", "DMA_RX": "ENABLE"}
        settings: dict = {"terminal_source": "", "USB": {}}
        peripherals: dict = {}
        if rng.random() < 0.6:
            ranks = [f"ADC_CHANNEL_{rng.randint(0, 19)}" for _ in range(rng.randint(1, 16))]
            peripherals["ADC"] = {"ADC1": {"DMA": "ENABLE", "RegularConversions": ranks}}
        if rng.random() < 0.3:
            peripherals["DAC"] = {"DAC1": {"Channels": {"OUT1": "DAC_OUT1", "OUT2": "DAC_OUT2"}}}
        if rng.random() < 0.6:
            channels = {f"CH{n}": {} for n in range(1, rng.randint(2, 5))}
            peripherals["TIM"] = {"TIM1": {"Channels": channels}, "TIM3": {"Channels": {"CH2": {}}}}
        for group, instances in (("SPI", 3), ("USART", 6), ("I2C", 3)):
            count = rng.randint(0, instances)
            names = [f"{group}{n}" for n in range(1, count + 1)]
            if names:
                peripherals[group] = {
                    name: {key: rng.choice(["ENABLE", "DISABLE"]) for key in dma} for name in names
                }
        peripherals.setdefault("CAN", {}).update({"CAN1": {}} if rng.random() < 0.5 else {})
        if rng.random() < 0.3:
            peripherals["IWDG"] = {"IWDG": {"Enabled": True}}
        for group in ("SPI", "USART", "I2C"):
            for name in peripherals.get(group, {}):
                section = settings.setdefault(group, {})
                section[name.lower()] = {
                    "tx_buffer_size": rng.choice([16, 32, 48, 100, 128, 512]),
                    "rx_buffer_size": rng.choice([16, 32, 48, 100, 128, 512]),
                    "buffer_size": rng.choice([16, 32, 40, 64]),
                    "dma_section": rng.choice(["", ".axi_ram", self.word(rng, 3, 30)]),
                }
        if rng.random() < 0.7:
            # STM32H5 的 USB 是 FSDEV 设备，其余型号用 OTG HS；一到三路 CDC。
            # The USB of the STM32H5 is an FSDEV device, the other parts use OTG HS; one to
            # three CDCs.
            fsdev = family == "STM32H5"
            usb = {
                "enable": True,
                "dma_section": rng.choice(["", ".axi_ram", ".dma_buffers_in_a_long_section"]),
                "cdc": [
                    {
                        "tx_fifo_size": rng.choice([64, 128, 512]),
                        "rx_fifo_size": rng.choice([64, 128, 512]),
                        "queue_size": rng.randint(1, 12),
                    }
                    for _ in range(rng.randint(1, 3))
                ],
                "manufacturer": self.word(rng, 3, 40),
                "product": self.word(rng, 3, 40),
                "serial": self.word(rng, 3, 50),
                "vid": rng.randint(0, 0xFFFF),
                "pid": rng.randint(0, 0xFFFF),
                "bcd": rng.randint(0, 0xFFFF),
            }
            if fsdev:
                settings["USB"]["usb_fs"] = usb
                peripherals["USB"] = {"USB": {"Role": "Device", "PCDHandle": "hpcd_USB_DRD_FS"}}
            else:
                settings["USB"]["usb_otg_hs"] = usb
                peripherals["USB"] = {"USB_OTG_HS": {"Role": "Device"}}
        uarts = [f"usart{n}" for n in range(1, 7) if f"USART{n}" in peripherals.get("USART", {})]
        if uarts and rng.random() < 0.7:
            settings["terminal_source"] = rng.choice(uarts)
            settings["Terminal"] = {"run_as_thread": rng.random() < 0.5}
        if rng.random() < 0.5:
            settings["database"] = {"enable": True, "block_size": rng.choice([1, 4, 32, "auto"])}
        project = {
            "Mcu": {"Type": mcu, "Family": family},
            "GPIO": gpio,
            "Peripherals": peripherals,
            "Timebase": {"Source": rng.choice(["SysTick", "TIM2"]), "IRQ": None},
            "FreeRTOS": {"Enabled": True},
        }
        return project, settings

    def test_random_projects_are_formatted(self):
        rng = random.Random(20261003)
        for number in range(20):
            project, settings = self.project_and_config(rng)
            with self.subTest(project=number):
                files = self.generate_files(project, yaml.safe_dump(settings), number % 2 == 0)
                self.assertFormatted({"app_main.cpp": files["app_main.cpp"]})


if __name__ == "__main__":
    unittest.main()
