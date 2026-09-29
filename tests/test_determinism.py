"""The same CubeMX project must generate byte-identical files on every run."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

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

LIBXR_CONFIG = textwrap.dedent("""\
    terminal_source: usart1
    USB:
      usb_otg_fs:
        enable: true
      usb_otg_hs:
        enable: true
    """)

GENERATE = textwrap.dedent("""\
    import sys
    from unittest.mock import patch
    from libxr import generator_code_stm32, peripheral_analyzer_stm32
    with patch('libxr.package_info.LibXRPackageInfo.check_and_print'):
        sys.argv = ['xr_parse_ioc', '-d', 'project', '-o', 'project/cubemx.yaml']
        peripheral_analyzer_stm32.main()
        sys.argv = ['xr_gen_code_stm32', '-i', 'project/cubemx.yaml',
                    '-o', 'project/User/app_main.cpp', '--xrobot']
        generator_code_stm32.main()
    """)


def generate_with_hash_seed(root: Path, seed: str) -> dict:
    project = root / seed / 'project'
    (project / 'User').mkdir(parents=True)
    (project / 'demo.ioc').write_text(IOC, encoding='utf-8')
    (project / 'User' / 'libxr_config.yaml').write_text(LIBXR_CONFIG, encoding='utf-8')
    environment = dict(os.environ, PYTHONHASHSEED=seed)
    subprocess.run([sys.executable, '-c', GENERATE], cwd=root / seed, env=environment,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {path.relative_to(project).as_posix(): path.read_bytes()
            for path in sorted(project.rglob('*')) if path.is_file() and path.suffix != '.ioc'}


class HashSeedIndependence(unittest.TestCase):
    def test_output_does_not_depend_on_hash_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Several seeds so that set iteration order differs between runs.
            seeds = ['0', '1', '2', '3', '4', '5']
            outputs = [generate_with_hash_seed(root, seed) for seed in seeds]
        reference = outputs[0]
        self.assertEqual(sorted(reference), ['User/app_main.cpp', 'User/app_main.h',
                                             'User/flash_map.hpp', 'User/libxr_config.yaml',
                                             'cubemx.yaml'])
        self.assertIn(b'STM32USBDeviceOtgFS usb_fs', reference['User/app_main.cpp'])
        self.assertIn(b'STM32USBDeviceOtgHS usb_hs', reference['User/app_main.cpp'])
        for seed, output in zip(seeds[1:], outputs[1:], strict=True):
            for name in reference:
                with self.subTest(seed=seed, file=name):
                    self.assertEqual(output[name], reference[name])


if __name__ == '__main__':
    unittest.main()
