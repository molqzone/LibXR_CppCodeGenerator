import os
import sys
import tempfile
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from libxr.ConfigTiSyscfgProject import (
    build_yaml_config,
    extract_peripherals,
    extract_modules,
    extract_rtos,
    extract_ti_device,
    find_syscfg_file,
    render_template_command,
)


class TestTiSyscfgEntry(unittest.TestCase):
    def test_find_syscfg_file_returns_sorted_first_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "b_file.syscfg"), "w", encoding="utf-8").close()
            open(os.path.join(tmpdir, "a_file.syscfg"), "w", encoding="utf-8").close()

            result = find_syscfg_file(tmpdir)

            self.assertEqual(result, os.path.join(tmpdir, "a_file.syscfg"))

    def test_find_syscfg_file_returns_none_without_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "readme.txt"), "w", encoding="utf-8").close()

            result = find_syscfg_file(tmpdir)

            self.assertIsNone(result)

    def test_find_syscfg_file_does_not_lookup_recursively(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sysconfig")
            os.makedirs(nested, exist_ok=True)
            target = os.path.join(nested, "demo.syscfg")
            open(target, "w", encoding="utf-8").close()

            result = find_syscfg_file(tmpdir)

            self.assertIsNone(result)

    def test_extract_metadata_from_syscfg_text(self):
        syscfg_text = """
/**
 * @v2CliArgs --device "MSPM0G3507" --package "LQFP-64(PM)"
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507" --rtos freertos
 */
const gpio = scripting.addModule("/ti/drivers/GPIO", {}, false);
const uart = scripting.addModule('/ti/drivers/UART2', {}, false);
const rtos = "FreeRTOS";
"""
        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "MSPM0G3507")
        self.assertEqual(board, "LP_MSPM0G3507")
        self.assertEqual(extract_rtos(syscfg_text), "FreeRTOS")
        self.assertEqual(
            extract_modules(syscfg_text),
            ["/ti/drivers/GPIO", "/ti/drivers/UART2"],
        )

    def test_extract_metadata_fallback_values(self):
        syscfg_text = "const value = 1;"

        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "UNKNOWN_TI_DEVICE")
        self.assertIsNone(board)
        self.assertEqual(extract_rtos(syscfg_text), "Unknown")
        self.assertEqual(extract_modules(syscfg_text), [])

    def test_extract_device_from_cli_board(self):
        syscfg_text = """
/**
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507" --product "mspm0_sdk@2.09.00.00"
 */
"""
        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "MSPM0G3507")
        self.assertEqual(board, "LP_MSPM0G3507")

    def test_extract_device_from_cli_v2_device_preferred(self):
        syscfg_text = """
/**
 * @cliArgs --device "MSPM0L222X" --package "LQFP-80(PN)" --part "Default"
 * @v2CliArgs --device "MSPM0L2228" --package "LQFP-80(PN)"
 * @cliArgs --board /ti/boards/LP_MSPM0L2228 --rtos freertos
 */
"""
        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "MSPM0L2228")
        self.assertEqual(board, "LP_MSPM0L2228")

    def test_extract_device_from_unquoted_cli_board(self):
        syscfg_text = """
/**
 * @cliArgs --board /ti/boards/LP_MSPM0G3507 --rtos nortos
 */
"""
        device, board = extract_ti_device(syscfg_text)
        self.assertEqual(device, "MSPM0G3507")
        self.assertEqual(board, "LP_MSPM0G3507")

    def test_build_yaml_config_contains_required_sections(self):
        syscfg_text = """
/**
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507"
 */
const GPIO = scripting.addModule("/ti/driverlib/GPIO", {}, false);
const GPIO1 = GPIO.addInstance();
GPIO1.$name = "GPIO_GRP_0";
GPIO1.associatedPins[0].$name = "LED";
GPIO1.associatedPins[0].assignedPort = "PORTB";
GPIO1.associatedPins[0].assignedPin = "22";
GPIO1.associatedPins[0].direction = "OUTPUT";
GPIO1.associatedPins[0].internalResistor = "PULL_UP";
const I2C = scripting.addModule("/ti/driverlib/I2C", {}, false);
const I2C1 = I2C.addInstance();
I2C1.$name = "I2C_0";
"""
        result = build_yaml_config("/tmp/demo.syscfg", "uart0", syscfg_text)

        self.assertIn("Mcu", result)
        self.assertIn("GPIO", result)
        self.assertIn("Peripherals", result)
        self.assertIn("Timebase", result)
        self.assertNotIn("SysConfig", result)
        self.assertEqual(result["Mcu"]["Family"], "TI")
        self.assertEqual(result["Mcu"]["Type"], "MSPM0G3507")
        self.assertIn("PB22", result["GPIO"])
        self.assertEqual(result["GPIO"]["PB22"]["Signal"], "GPIO_Output")
        self.assertEqual(result["GPIO"]["PB22"]["Label"], "LED")
        self.assertEqual(result["GPIO"]["PB22"]["Pull"], "GPIO_PULLUP")
        self.assertNotIn("GPIO", result["Peripherals"])
        self.assertIn("I2C", result["Peripherals"])
        self.assertIn("I2C_0", result["Peripherals"]["I2C"])
        self.assertEqual(result["terminal_source"], "uart0")

    def test_build_yaml_config_gpio_without_signal_fields_uses_ti_fallback(self):
        syscfg_text = """
/**
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507"
 */
const GPIO = scripting.addModule("/ti/driverlib/GPIO", {}, false);
const GPIO1 = GPIO.addInstance();
GPIO1.$name = "GPIO_GRP_0";
GPIO1.associatedPins[0].$name = "PIN_0";
GPIO1.associatedPins[0].assignedPort = "PORTB";
GPIO1.associatedPins[0].assignedPin = "22";
"""
        result = build_yaml_config("/tmp/demo.syscfg", "uart0", syscfg_text)

        self.assertIn("PB22", result["GPIO"])
        self.assertEqual(result["GPIO"]["PB22"]["Signal"], "GPIO_Output")
        self.assertEqual(result["GPIO"]["PB22"]["Pull"], "GPIO_NOPULL")

    def test_build_yaml_config_gpio_sets_interrupt_en_marker(self):
        syscfg_text = """
/**
 * @cliArgs --board "/ti/boards/LP_MSPM0G3507"
 */
const GPIO = scripting.addModule("/ti/driverlib/GPIO", {}, false);
const GPIO1 = GPIO.addInstance();
GPIO1.$name = "GPIO_GRP_0";
GPIO1.associatedPins[0].$name = "BUTTON";
GPIO1.associatedPins[0].assignedPort = "PORTA";
GPIO1.associatedPins[0].assignedPin = "8";
GPIO1.associatedPins[0].direction = "INPUT";
GPIO1.associatedPins[0].interruptEn = true;
"""
        result = build_yaml_config("/tmp/demo.syscfg", "uart0", syscfg_text)

        self.assertIn("PA8", result["GPIO"])
        self.assertEqual(result["GPIO"]["PA8"]["Signal"], "GPIO_Input")
        self.assertTrue(result["GPIO"]["PA8"]["interruptEn"])

    def test_extract_peripherals_detects_modules_without_instances(self):
        syscfg_text = """
const DMA = scripting.addModule("/ti/driverlib/DMA");
const MCAN = scripting.addModule("/ti/driverlib/MCAN", {}, false);
const Board = scripting.addModule("/ti/driverlib/Board", {}, false);
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("DMA", peripherals)
        self.assertIn("DMA", peripherals["DMA"])
        self.assertEqual(peripherals["DMA"]["DMA"], {})
        self.assertIn("MCAN", peripherals)
        self.assertIn("MCAN", peripherals["MCAN"])
        self.assertEqual(peripherals["MCAN"]["MCAN"], {})
        self.assertNotIn("Board", peripherals)

    def test_extract_peripherals_uses_instance_name_when_available(self):
        syscfg_text = """
const SPI = scripting.addModule("/ti/driverlib/SPI", {}, false);
const SPI1 = SPI.addInstance();
SPI1.$name = "SPI_0";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("SPI", peripherals)
        self.assertIn("SPI_0", peripherals["SPI"])
        self.assertNotIn("SPI", peripherals["SPI"])

    def test_extract_peripherals_spi_parses_core_parameters(self):
        syscfg_text = """
const SPI = scripting.addModule("/ti/driverlib/SPI", {}, false);
const SPI1 = SPI.addInstance();
SPI1.$name = "SPI_0";
SPI1.targetBitRate = 500000;
SPI1.mode = "PERIPHERAL";
SPI1.dataSize = 8;
SPI1.bitOrder = "MSB_FIRST";
SPI1.frameFormat = "MOTOROLA_POL0_PHA1";
SPI1.phase = "FIRST_EDGE";
SPI1.polarity = "IDLE_LOW";
SPI1.enableDMAEvent1 = true;
SPI1.enableDMAEvent2 = false;
SPI1.enabledDMAEvent1Triggers = "DL_SPI_DMA_INTERRUPT_RX";
SPI1.enabledDMAEvent2Triggers = "DL_SPI_DMA_INTERRUPT_TX";
SPI1.enabledInterrupts = ["RX","TX_EMPTY"];
SPI1.chipSelect = ["1"];
SPI1.peripheral.$assign = "SPI1";
SPI1.peripheral.sclkPin.$assign = "PA17";
SPI1.peripheral.mosiPin.$assign = "PB8";
SPI1.peripheral.misoPin.$assign = "PB7";
SPI1.peripheral.cs1Pin.$assign = "PA27";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("SPI", peripherals)
        self.assertIn("SPI_0", peripherals["SPI"])
        spi_cfg = peripherals["SPI"]["SPI_0"]
        self.assertEqual(spi_cfg["BaudRate"], 500000)
        self.assertEqual(spi_cfg["Mode"], "PERIPHERAL")
        self.assertEqual(spi_cfg["DataSize"], 8)
        self.assertEqual(spi_cfg["FirstBit"], "MSB_FIRST")
        self.assertEqual(spi_cfg["FrameFormat"], "MOTOROLA_POL0_PHA1")
        self.assertEqual(spi_cfg["CLKPhase"], "FIRST_EDGE")
        self.assertEqual(spi_cfg["CLKPolarity"], "IDLE_LOW")
        self.assertTrue(spi_cfg["DMAEvent1"])
        self.assertFalse(spi_cfg["DMAEvent2"])
        self.assertEqual(spi_cfg["DMAEvent1Trigger"], "DL_SPI_DMA_INTERRUPT_RX")
        self.assertEqual(spi_cfg["DMAEvent2Trigger"], "DL_SPI_DMA_INTERRUPT_TX")
        self.assertEqual(spi_cfg["Interrupts"], ["RX", "TX_EMPTY"])
        self.assertEqual(spi_cfg["ChipSelect"], "1")
        self.assertEqual(spi_cfg["Instance"], "SPI1")
        self.assertEqual(
            spi_cfg["Pins"],
            {"SCLK": "PA17", "MOSI": "PB8", "MISO": "PB7", "CS1": "PA27"},
        )

    def test_extract_peripherals_pwm_parses_core_parameters(self):
        syscfg_text = """
const PWM = scripting.addModule("/ti/driverlib/PWM", {}, false);
const PWM1 = PWM.addInstance();
PWM1.$name = "PWM_1";
PWM1.pwmMode = "CENTER_ALIGN";
PWM1.timerCount = 4000;
PWM1.timerStartTimer = true;
PWM1.clockDivider = 8;
PWM1.clockPrescale = 4;
PWM1.enableShadowLoad = true;
PWM1.ccIndex = [0];
PWM1.ccIndexCmpl = [0];
PWM1.peripheral.$assign = "TIMA0";
PWM1.peripheral.ccp0Pin.$assign = "PB8";
PWM1.peripheral.ccp0Pin_cmpl.$assign = "PB9";
PWM1.PWM_CHANNEL_0.$name = "MOTOR_A";
PWM1.PWM_CHANNEL_0.dutyCycle = 50;
PWM1.PWM_CHANNEL_0.ccValue = 2000;
PWM1.PWM_CHANNEL_0.invert = true;
PWM1.PWM_CHANNEL_0.shadowUpdateMode = "ZERO_EVT";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("PWM", peripherals)
        self.assertIn("PWM_1", peripherals["PWM"])
        pwm_cfg = peripherals["PWM"]["PWM_1"]
        self.assertEqual(pwm_cfg["Mode"], "CENTER_ALIGN")
        self.assertEqual(pwm_cfg["Period"], 4000)
        self.assertEqual(pwm_cfg["Prescaler"], 8)
        self.assertEqual(pwm_cfg["ClockPrescaler"], 4)
        self.assertNotIn("Started", pwm_cfg)
        self.assertNotIn("ShadowLoad", pwm_cfg)
        self.assertNotIn("Timer", pwm_cfg)
        self.assertNotIn("CCIndex", pwm_cfg)
        self.assertNotIn("CCIndexCmpl", pwm_cfg)
        self.assertIn("Channels", pwm_cfg)
        self.assertIn("PWM_CHANNEL_0", pwm_cfg["Channels"])
        ch0_cfg = pwm_cfg["Channels"]["PWM_CHANNEL_0"]
        self.assertTrue(ch0_cfg["PWM"])
        self.assertEqual(ch0_cfg["DutyCycle"], 50)
        self.assertNotIn("CCValue", ch0_cfg)
        self.assertNotIn("Invert", ch0_cfg)
        self.assertNotIn("Label", ch0_cfg)
        self.assertNotIn("Pin", ch0_cfg)
        self.assertNotIn("PinCmpl", ch0_cfg)
        self.assertNotIn("Complementary", ch0_cfg)

    def test_extract_peripherals_pwm_keeps_enabled_fallback_without_params(self):
        syscfg_text = """
const PWM = scripting.addModule("/ti/driverlib/PWM", {}, false);
const PWM1 = PWM.addInstance();
PWM1.$name = "PWM_0";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("PWM", peripherals)
        self.assertIn("PWM_0", peripherals["PWM"])
        self.assertEqual(peripherals["PWM"]["PWM_0"], {})

    def test_extract_peripherals_uart_parses_core_parameters(self):
        syscfg_text = """
const UART = scripting.addModule("/ti/driverlib/UART", {}, false);
const UART1 = UART.addInstance();
UART1.$name = "UART_0";
UART1.targetBaudRate = 115200;
UART1.wordLength = "8_BITS";
UART1.parity = "NONE";
UART1.stopBits = "ONE";
UART1.uartMode = "DALI";
UART1.direction = "TX";
UART1.flowControl = "RTS_CTS";
UART1.enableFIFO = true;
UART1.rxFifoThreshold = "DL_UART_RX_FIFO_LEVEL_ONE_ENTRY";
UART1.txFifoThreshold = "DL_UART_TX_FIFO_LEVEL_EMPTY";
UART1.enableDMARX = false;
UART1.enableDMATX = true;
UART1.enabledDMARXTriggers = "DL_UART_DMA_INTERRUPT_RX";
UART1.enabledDMATXTriggers = "DL_UART_DMA_INTERRUPT_TX";
UART1.enabledInterrupts = ["RX","TX"];
UART1.peripheral.$assign = "UART0";
UART1.peripheral.rxPin.$assign = "PA11";
UART1.peripheral.txPin.$assign = "PA10";
UART1.peripheral.rtsPin.$assign = "PA8";
UART1.peripheral.ctsPin.$assign = "PA9";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("UART", peripherals)
        self.assertIn("UART_0", peripherals["UART"])
        uart_cfg = peripherals["UART"]["UART_0"]
        self.assertEqual(uart_cfg["BaudRate"], 115200)
        self.assertEqual(uart_cfg["WordLength"], "8_BITS")
        self.assertEqual(uart_cfg["Parity"], "NONE")
        self.assertEqual(uart_cfg["StopBits"], "ONE")
        self.assertEqual(uart_cfg["Mode"], "DALI")
        self.assertEqual(uart_cfg["Direction"], "TX")
        self.assertEqual(uart_cfg["FlowControl"], "RTS_CTS")
        self.assertTrue(uart_cfg["FIFO"])
        self.assertEqual(uart_cfg["RXFifoThreshold"], "DL_UART_RX_FIFO_LEVEL_ONE_ENTRY")
        self.assertEqual(uart_cfg["TXFifoThreshold"], "DL_UART_TX_FIFO_LEVEL_EMPTY")
        self.assertFalse(uart_cfg["DMA_RX"])
        self.assertTrue(uart_cfg["DMA_TX"])
        self.assertEqual(uart_cfg["DMARXTrigger"], "DL_UART_DMA_INTERRUPT_RX")
        self.assertEqual(uart_cfg["DMATXTrigger"], "DL_UART_DMA_INTERRUPT_TX")
        self.assertEqual(uart_cfg["Interrupts"], ["RX", "TX"])
        self.assertEqual(uart_cfg["Instance"], "UART0")
        self.assertEqual(
            uart_cfg["Pins"],
            {"RX": "PA11", "TX": "PA10", "RTS": "PA8", "CTS": "PA9"},
        )

    def test_extract_peripherals_uart_uses_fallbacks_without_parameters(self):
        syscfg_text = """
const UART = scripting.addModule("/ti/driverlib/UART", {}, false);
const UART1 = UART.addInstance();
UART1.$name = "UART_0";
UART1.enableDMARX = true;
UART1.enableDMATX = true;
UART1.peripheral.rxPin.$suggestSolution = "PA11";
UART1.peripheral.txPin.$suggestSolution = "PA10";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("UART", peripherals)
        self.assertIn("UART_0", peripherals["UART"])
        self.assertEqual(
            peripherals["UART"]["UART_0"],
            {
                "WordLength": "8_BITS",
                "RXFifoThreshold": "DL_UART_RX_FIFO_LEVEL_1_2_FULL",
                "TXFifoThreshold": "DL_UART_TX_FIFO_LEVEL_1_2_EMPTY",
                "DMA_RX": True,
                "DMA_TX": True,
                "DMARXTrigger": "DL_UART_DMA_INTERRUPT_RX",
                "DMATXTrigger": "DL_UART_DMA_INTERRUPT_TX",
                "Pins": {"RX": "PA11", "TX": "PA10"},
            },
        )

    def test_extract_peripherals_spi_uses_fallbacks_without_parameters(self):
        syscfg_text = """
const SPI = scripting.addModule("/ti/driverlib/SPI", {}, false);
const SPI1 = SPI.addInstance();
SPI1.$name = "SPI_0";
SPI1.enableDMAEvent1 = true;
SPI1.enableDMAEvent2 = true;
SPI1.peripheral.sclkPin.$suggestSolution = "PA17";
SPI1.peripheral.mosiPin.$suggestSolution = "PB8";
SPI1.peripheral.misoPin.$suggestSolution = "PB7";
SPI1.peripheral.cs0Pin.$suggestSolution = "PA13";
"""
        peripherals = extract_peripherals(syscfg_text)

        self.assertIn("SPI", peripherals)
        self.assertIn("SPI_0", peripherals["SPI"])
        self.assertEqual(
            peripherals["SPI"]["SPI_0"],
            {
                "RXFifoThreshold": "DL_SPI_RX_FIFO_LEVEL_1_2_FULL",
                "TXFifoThreshold": "DL_SPI_TX_FIFO_LEVEL_1_2_EMPTY",
                "DMAEvent1": True,
                "DMAEvent2": True,
                "DMAEvent1Trigger": "DL_SPI_DMA_INTERRUPT_RX",
                "DMAEvent2Trigger": "DL_SPI_DMA_INTERRUPT_TX",
                "Pins": {"SCLK": "PA17", "MOSI": "PB8", "MISO": "PB7", "CS0": "PA13"},
            },
        )

    def test_render_template_command_raises_for_unknown_placeholder(self):
        with self.assertRaises(ValueError):
            render_template_command(
                "echo {unknown}",
                {"project_dir": "/tmp", "syscfg_file": "/tmp/a.syscfg", "yaml_output": "/tmp/a.yaml"},
            )


if __name__ == "__main__":
    unittest.main()
