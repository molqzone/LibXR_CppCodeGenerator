"""测试共用的基类、CubeMX 工程样例和生成器辅助。
Shared test base classes, a CubeMX project sample and generator helpers.

GeneratorTestCase 在每个测试前重新加载 generator_code_stm32，清空它的模块级登记表和配置。
GeneratorTestCase reloads generator_code_stm32 before every test, which clears its
module-level registries and settings.
"""

import contextlib
import importlib
import io
import os
import textwrap
import unittest
from unittest import mock

from xr_syntax.cpp import CppDocument

from libxr import cli
from libxr import generator_code_stm32 as generator

# 测试断言英文输出；中文输出的测试自己设置 XR_LANG。
# Tests assert English output; tests of Chinese output set XR_LANG themselves.
os.environ["XR_LANG"] = "en"

# 一个 STM32F407 工程的 .ioc 片段：CAN、SPI、PWM、两个 UART（其一带 DMA）、两个 USB 设备、
# 一个输出 GPIO 和一个外部中断 GPIO（NVIC 中开启了它的中断）。
# An .ioc excerpt of an STM32F407 project: CAN, SPI, PWM, two UARTs (one with DMA), two USB
# devices, an output GPIO and an external-interrupt GPIO with its interrupt enabled in NVIC.
IOC = textwrap.dedent("""\
    Mcu.Family=STM32F4
    Mcu.Name=STM32F407I(E-G)Hx
    Mcu.UserName=STM32F407IGHx
    Mcu.CPN=STM32F407IGH6
    Mcu.IP0=CAN1
    Mcu.IP1=CAN2
    Mcu.IP2=SPI1
    Mcu.IP3=TIM1
    Mcu.IP4=USART1
    Mcu.IP5=USART6
    Mcu.IP6=USB_OTG_FS
    Mcu.IP7=USB_OTG_HS
    Mcu.IPNb=8
    CAN1.Prescaler=3
    CAN2.Prescaler=3
    Dma.Request0=USART1_RX
    Dma.Request1=USART1_TX
    Dma.Request2=SPI1_RX
    Dma.RequestsNb=3
    Dma.SPI1_RX.2.Instance=DMA2_Stream0
    Dma.USART1_RX.0.Instance=DMA2_Stream5
    Dma.USART1_TX.1.Instance=DMA2_Stream7
    NVIC.EXTI15_10_IRQn=true\\:5\\:0\\:true\\:false\\:true\\:true\\:true\\:true\\:true
    PA9.Signal=USART1_TX
    PA10.Signal=USART1_RX
    PC6.Signal=USART6_TX
    PC7.Signal=USART6_RX
    PA5.Signal=SPI1_SCK
    PE9.Signal=S_TIM1_CH1
    PC13.GPIO_Label=LED
    PC13.Signal=GPIO_Output
    PE12.GPIO_Label=KEY
    PE12.Signal=GPXTI12
    PB0.Signal=GPIO_Output
    SPI1.Mode=SPI_MODE_MASTER
    TIM1.Channel-PWM\\ Generation1\\ CH1=TIM_CHANNEL_1
    TIM1.IPParameters=Channel-PWM Generation1 CH1
    USART1.BaudRate=115200
    USART1.IPParameters=BaudRate
    USART6.BaudRate=115200
    USB_OTG_FS.IPParameters=VirtualMode
    USB_OTG_FS.VirtualMode=Device_Only
    USB_OTG_HS.IPParameters=VirtualMode-Device_Only_FS
    USB_OTG_HS.VirtualMode-Device_Only_FS=Device_Only_FS
    """)


def run_libxr(*argv):
    """以这些参数运行 libxr 命令（不查询 PyPI），返回退出码、标准输出和标准错误。
    Run the libxr command with these arguments, without querying PyPI; return the exit code,
    stdout and stderr.
    """
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch("libxr.update_notice._latest_version", return_value=None),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        try:
            code = cli.main(list(argv))
        except SystemExit as exit:
            code = exit.code
    return code, out.getvalue(), err.getvalue()


def user_region(code, name):
    """生成代码中名为 name 的 User Code 区域的内容，去掉首尾空白；没有该区域时为空字符串。
    The body of the User Code region called name in generated code, stripped; an empty string
    when there is no such region.
    """
    for region in CppDocument.parse(code).user_regions():
        if region.name == str(name):
            return region.body_text.strip()
    return ""


class TestCase(unittest.TestCase):
    """unittest.TestCase 加上逐字比较报错文本的断言。
    unittest.TestCase with an assertion that compares the error text exactly.
    """

    @contextlib.contextmanager
    def assertRaisesMessage(self, exception, message):
        """断言代码块抛出 exception，且报错文本与 message 相同。
        Assert that the block raises exception whose text equals message.
        """
        with self.assertRaises(exception) as context:
            yield context
        self.assertEqual(str(context.exception), message)


class GeneratorTestCase(TestCase):
    """重新加载的 generator_code_stm32 和最小的工程数据。
    A reloaded generator_code_stm32 and minimal project data.
    """

    def setUp(self):
        super().setUp()
        importlib.reload(generator)

    def project(self, gpio=None, peripherals=None, mcu="STM32F407IGH6", family="STM32F4"):
        """工程数据：给定的 MCU、GPIO 和外设。
        Project data with the given MCU, GPIO and peripherals.
        """
        return {
            "Mcu": {"Type": mcu, "Family": family},
            "GPIO": gpio or {},
            "Peripherals": peripherals or {},
        }

    def generate(self, project=None, use_xrobot=True, existing=""):
        """清空登记表后生成 app_main 源文件的文本。
        Reset the registry and generate the text of the app_main source file.
        """
        generator.initialize_registry(use_xrobot)
        return generator.generate_full_code(project or self.project(), use_xrobot, existing)
