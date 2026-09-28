#!/usr/bin/env python
"""STM32 Peripheral Code Generator - Core Module (Optimized)"""

import logging
import os
import re
import sys
import urllib.request
import argparse
import yaml
from xr_syntax.cpp import CppDocument, identifier_occurrences

from libxr import LibXRConfigFile as libxr_config_file
from libxr.LibXRConfigFile import LibXRConfigError

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# --------------------------
# Global Configuration
# --------------------------
# Generated device objects and their LibXR interface, registered with XR_REGISTER.
registered_devices = {"power_manager": "PowerManager"}
# What produced each registered name, for collision diagnostics.
registered_origins = {}
libxr_settings = {
    "terminal_source": "",
    "software_timer": {"priority": 2, "stack_depth": 1024},
    "SPI": {},
    "I2C": {},
    "USART": {},
    "ADC": {},
    "TIM": {},
    "CAN": {},
    "FDCAN": {},
    "USB": {},
    "Terminal": {
        "read_buff_size": 32,
        "max_line_size": 32,
        "max_arg_number": 5,
        "max_history_number": 5
    },
    "SYSTEM": "None"
}
# Round-trip document of the loaded libxr_config.yaml (comments, user keys).
libxr_config_document = None


# --------------------------
# Configuration Initialization
# --------------------------
def initialize_registry(use_xrobot: bool) -> None:
    """Reset the generated-device registry; only XRobot output registers devices."""
    registered_devices.clear()
    registered_origins.clear()
    if use_xrobot:
        _register_device("power_manager", "PowerManager", "power manager")


# --------------------------
# CLI Arguments
# --------------------------
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Generate STM32 Peripheral Initialization Code",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-i", "--input", required=True,
                        help="Input YAML configuration file path")
    parser.add_argument("-o", "--output", required=True,
                        help="Output C++ file path")
    parser.add_argument("--xrobot", action="store_true",
                        help="Enable XRobot framework integration")
    parser.add_argument("--libxr-config", default="",
                        help="Optional path or URL to libxr_config.yaml")
    return parser.parse_args()


# --------------------------
# Device Registration
# --------------------------
def _register_device(name: str, dev_type: str, origin: str = ""):
    """Record one generated object; one name has exactly one registered type."""
    origin = origin or f"{dev_type} object"
    if name in registered_devices:
        raise ValueError(
            f"Generated name '{name}' ({origin}) collides with the existing "
            f"'{name}' ({registered_origins.get(name, registered_devices[name])}); "
            "every generated object needs its own name")
    registered_devices[name] = dev_type
    registered_origins[name] = origin


def _generate_fdcan_can_alias(instance: str) -> str:
    """Expose an FDCAN object under the classic CAN interface as well.

    fdcanN stays registered as LibXR::FDCAN; the reference canN names the
    same object as LibXR::CAN, so each registered name keeps one type.
    """
    fdcan_name = instance.lower()
    match = re.fullmatch(r"fdcan(\d+)", fdcan_name)
    if match is None:
        raise ValueError(f"Cannot derive the CAN alias of FDCAN instance '{instance}'")
    can_name = f"can{match.group(1)}"
    _register_device(can_name, "CAN", f"LibXR::CAN alias of {fdcan_name}")
    return f"  LibXR::CAN& {can_name} = {fdcan_name};\n"


# --------------------------
# Peripheral Instance Generation
# --------------------------
def generate_peripheral_instances(project_data: dict, use_xrobot: bool = False) -> str:
    """Generate initialization code for all peripherals with topological sorting."""
    code_sections = {
        "adc": [],
        "pwm": [],
        "main": []
    }

    for p_type, instances in project_data.get("Peripherals", {}).items():
        for instance_name, config in instances.items():
            section, code = PeripheralFactory.create(p_type, instance_name, config)
            if use_xrobot and p_type.upper() == "FDCAN" and code:
                code += _generate_fdcan_can_alias(instance_name)
            if section in code_sections:
                code_sections[section].append(code)

    # Assemble code in correct order: ADC config -> PWM -> Main peripherals
    return "\n".join([
        "\n".join(code_sections["adc"]),
        "\n".join(code_sections["pwm"]),
        "\n".join(code_sections["main"])
    ])


# --------------------------
# Configuration Loading
# --------------------------
def load_configuration(file_path: str, use_xrobot: bool) -> dict:
    """Load and validate project YAML configuration with enhanced error reporting."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

            # Basic schema validation
            required_sections = ["Mcu", "GPIO", "Peripherals"]
            for section in required_sections:
                if section not in config:
                    raise ValueError(f"Missing required section: {section}")

            # Detect RTOS
            if 'FreeRTOS' in config:
                libxr_settings['SYSTEM'] = 'FreeRTOS'
                logging.info("Detected FreeRTOS configuration")
            elif 'ThreadX' in config:
                libxr_settings['SYSTEM'] = 'ThreadX'
            else:
                libxr_settings['SYSTEM'] = 'None'

            # Software timer config
            if 'software_timer' in config:
                libxr_settings['software_timer'].update(config['software_timer'])

            # Terminal source
            if 'terminal_source' in config:
                libxr_settings['terminal_source'] = config['terminal_source']

            if "Peripherals" in config:
                empty_keys = [k for k, v in config["Peripherals"].items() if not v or v == {}]
                for k in empty_keys:
                    logging.info(f"Skipping empty peripheral config: {k}")
                    del config["Peripherals"][k]

            return config
    except FileNotFoundError:
        logging.error(f"Configuration file not found: {file_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logging.error(f"YAML syntax error: {str(e)}")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"Configuration validation failed: {str(e)}")
        sys.exit(1)


# --------------------------
# Library Configuration
# --------------------------
def load_libxr_config(output_dir: str, config_source: str) -> None:
    """Load libxr_config.yaml (or --libxr-config) into the effective settings.

    A configuration that exists but cannot be read or parsed stops
    generation; defaults never silently replace it.
    """
    global libxr_settings, libxr_config_document
    config_path = os.path.join(output_dir, "libxr_config.yaml")

    if config_source:
        if config_source.startswith("http://") or config_source.startswith("https://"):
            logging.info(f"Downloading libxr_config.yaml from {config_source}")
            try:
                with urllib.request.urlopen(config_source) as response:
                    text = response.read().decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise LibXRConfigError(f"Cannot download {config_source}: {error}") from error
            document, saved_config = libxr_config_file.parse(text, config_source)
        elif os.path.exists(config_source):
            logging.info(f"Using external libxr_config.yaml from {config_source}")
            document, saved_config = libxr_config_file.read(config_source)
        else:
            raise LibXRConfigError(f"Cannot locate config source: {config_source}")
    elif os.path.exists(config_path):
        document, saved_config = libxr_config_file.read(config_path)
        if saved_config.get("config_version", 1) > 1:
            logging.warning("Config file format is newer than expected")
    else:
        logging.info("Creating new library configuration file")
        libxr_config_document = libxr_config_file.new_document()
        return

    saved_config.pop("SYSTEM", None)
    libxr_settings = _deep_merge(libxr_settings, saved_config)
    libxr_config_document = document


def _report_dropped_device_aliases(aliases) -> None:
    """Name every alias of the removed device_aliases table for migration."""
    pairs = []
    if isinstance(aliases, dict):
        for device, entry in aliases.items():
            names = entry.get("aliases", []) if isinstance(entry, dict) else entry
            if isinstance(names, str):
                names = [names]
            for name in names or []:
                pairs.append(f"{name} -> {device}")
    logging.warning(
        "Removed the legacy device_aliases table from libxr_config.yaml; generated "
        "objects are registered only under their own names. Update configurations "
        "that used these aliases (alias -> device):")
    for pair in pairs or [repr(aliases)]:
        logging.warning(f"  {pair}")


def save_libxr_config(config_path: str) -> None:
    """Write the effective settings, dropping empty sections and legacy keys.

    Keys the generator does not interpret (such as the ``generator`` pin) and
    comments are kept.
    """
    # device_aliases was the legacy runtime alias table; it is no longer used.
    if "device_aliases" in libxr_settings:
        _report_dropped_device_aliases(libxr_settings["device_aliases"])
    cleaned_config = {
        k: v for k, v in libxr_settings.items()
        if not (isinstance(v, dict) and len(v) == 0) and k != "device_aliases"
    }
    document = libxr_config_document
    if document is None:
        document = libxr_config_file.new_document()
    libxr_config_file.update(document, cleaned_config)
    libxr_config_file.write(config_path, document)


def _deep_merge(base: dict, update: dict) -> dict:
    """Recursively merge nested dictionaries with type checking."""
    for key, value in update.items():
        if isinstance(value, dict):
            node = base.setdefault(key, {})
            if isinstance(node, dict):
                _deep_merge(node, value)
            else:
                raise LibXRConfigError(
                    f"Config type conflict for key '{key}': expected {type(node).__name__}, got a mapping")
        else:
            base[key] = value
    return base


# --------------------------
# GPIO Configuration
# --------------------------
def _sanitize_cpp_identifier(name: str) -> str:
    return re.sub(r'\W|^(?=\d)', '_', name)


CPP_KEYWORDS = frozenset("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char
char8_t char16_t char32_t class compl concept const consteval constexpr
constinit const_cast continue co_await co_return co_yield decltype default
delete do double dynamic_cast else enum explicit export extern false float for
friend goto if inline int long mutable namespace new noexcept not not_eq
nullptr operator or or_eq private protected public register reinterpret_cast
requires return short signed sizeof static static_assert static_cast struct
switch template this thread_local throw true try typedef typeid typename union
unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())

# Object-like CMSIS/HAL macros a GPIO object name would be expanded into.
_CMSIS_INSTANCE_MACRO = re.compile(
    r"GPIO[A-Z]|(?:ADC|DAC|TIM|LPTIM|HRTIM|SPI|I2S|I2C|I3C|USART|UART|LPUART|"
    r"CAN|FDCAN|DMA|BDMA|GPDMA|HPDMA|LPDMA|MDMA|DMAMUX|DMA2D|SAI|SDMMC|SDIO|"
    r"QUADSPI|OCTOSPI|OCTOSPIM|XSPI|FMC|FSMC|COMP|OPAMP|DFSDM|MDF|ADF|IWDG|"
    r"WWDG|RTC|TAMP|CRC|RNG|HASH|CRYP|AES|SAES|PKA|ETH|LTDC|DCMI|DCMIPP|PSSI|"
    r"USB_OTG_FS|USB_OTG_HS|USB|UCPD|TSC|LCD|CEC|SPDIFRX|SWPMI|MDIOS|RCC|PWR|"
    r"FLASH|EXTI|SYSCFG|DBGMCU|SCB|NVIC|SysTick|MPU|FPU|ITM|DWT|CoreDebug|TPI|"
    r"ICACHE|DCACHE|GTZC|VREFBUF|CORDIC|FMAC|JPEG|RAMCFG|OTFDEC|IPCC|HSEM)\d*")
_HAL_MACROS = frozenset({"NULL", "UNUSED", "UID_BASE"})


def _gpio_object_name(port: str, gpio_data: dict) -> str:
    return _sanitize_cpp_identifier(gpio_data.get("Label", "") or port)


def check_gpio_names(project_data: dict, generated_code: str, use_xrobot: bool) -> None:
    """Reject GPIO object names that the generated app_main cannot declare.

    A GPIO object is named after its CubeMX label. A label that is a C++
    keyword or reserved identifier, a CMSIS/HAL macro, a macro CubeMX derives
    from another label, or any other name the generated code uses would fail
    to compile or silently shadow that name inside app_main.
    """
    gpio = project_data.get("GPIO", {})
    label_macros = {}
    for data in gpio.values():
        label = data.get("Label", "")
        if label:
            for suffix in ("_Pin", "_GPIO_Port", "_EXTI_IRQn"):
                label_macros[f"{label}{suffix}"] = label
    counts = {}
    for occurrence in identifier_occurrences(generated_code):
        counts[occurrence.text] = counts.get(occurrence.text, 0) + 1
    # Declaration, plus the XR_REGISTER line with --xrobot.
    expected_uses = 2 if use_xrobot else 1
    problems = []
    for port, data in gpio.items():
        name = _gpio_object_name(port, data)
        where = f"GPIO object '{name}' (pin {port.split('-')[0]})"
        if name in CPP_KEYWORDS:
            problems.append(f"{where} is a C++ keyword")
        elif "__" in name or re.match(r"_[A-Z]", name):
            problems.append(f"{where} is a reserved C++ identifier")
        elif name in label_macros:
            problems.append(f"{where} is the CubeMX macro of GPIO label '{label_macros[name]}'")
        elif (name in _HAL_MACROS or name.endswith("_IRQn")
              or _CMSIS_INSTANCE_MACRO.fullmatch(name)):
            problems.append(f"{where} is a CMSIS/HAL macro or IRQ name")
        elif counts.get(name, 0) > expected_uses:
            problems.append(f"{where} collides with a name the generated code uses")
    if problems:
        raise ValueError(
            "rename these GPIO labels in CubeMX:\n  " + "\n  ".join(problems))


def generate_gpio_alias(port: str, gpio_data: dict, project_data: dict) -> str:
    base_port = port.split("-")[0]
    port_define = f"GPIO{base_port[1]}"
    pin_num = int(base_port[2:])
    pin_define = f"GPIO_PIN_{pin_num}"
    label = gpio_data.get("Label", "")

    if label:
        port_define = f"{label}_GPIO_Port"
        pin_define = f"{label}_Pin"

    irq_define = _get_exti_irq(pin_num, base_port, gpio_data.get("GPXTI", False),
                               project_data.get("Mcu", {}).get("Family", "STM32F4"),
                               project_data.get("Mcu", {}).get("Type") or "")
    irq_str = f", {irq_define}" if irq_define else ""

    var_name = _gpio_object_name(port, gpio_data)

    _register_device(var_name, "GPIO",
                     f"GPIO label {label} on {base_port}" if label else f"GPIO {base_port}")

    return f"{var_name}({port_define}, {pin_define}{irq_str})"


# EXTI interrupt vectors per CubeMX family (Mcu.Family), from the device
# vector tables. These families share EXTI0_1/EXTI2_3/EXTI4_15:
_EXTI_SHARED_LINE_FAMILIES = frozenset({"STM32F0", "STM32G0", "STM32L0", "STM32C0", "STM32U0"})
# These have one vector per line, EXTI0_IRQn..EXTI15_IRQn (as do the STM32H7R/S
# parts of the STM32H7 family); the others share EXTI9_5 and EXTI15_10.
_EXTI_PER_LINE_FAMILIES = frozenset({"STM32H5", "STM32U3", "STM32U5", "STM32L5", "STM32WBA", "STM32N6"})


def _get_exti_irq(pin_num: int, port: str, is_exti: bool, mcu_family: str, mcu_type: str = "") -> str:
    if not is_exti:
        return ""

    if mcu_family.startswith("STM32WB0"):
        if port.startswith("PA"):
            return "GPIOA_IRQn"
        elif port.startswith("PB"):
            return "GPIOB_IRQn"

    if mcu_family in _EXTI_SHARED_LINE_FAMILIES:
        if pin_num <= 1: return "EXTI0_1_IRQn"
        if pin_num <= 3: return "EXTI2_3_IRQn"
        return "EXTI4_15_IRQn"
    elif mcu_family in _EXTI_PER_LINE_FAMILIES or mcu_type.startswith(("STM32H7R", "STM32H7S")):
        return f"EXTI{pin_num}_IRQn"
    else:
        if 5 <= pin_num <= 9: return "EXTI9_5_IRQn"
        if 10 <= pin_num <= 15: return "EXTI15_10_IRQn"
        return f"EXTI{pin_num}_IRQn"


# --------------------------
# DMA Configuration
# --------------------------
DMA_DEFAULT_SIZES = {
    "SPI": {"tx": 32, "rx": 32},
    "USART": {"tx": 128, "rx": 128},
    "I2C": {"buffer": 32},
    "ADC": {"buffer": 32}
}


def generate_dma_resources(project_data: dict) -> str:
    """
    Generate DMA buffer definitions for all relevant peripherals,
    using per-buffer 'dma_section' config.
    - Reads libxr_settings['SPI'/'USART'/...][instance]['dma_section']
    - If section is empty, no attribute is added; if not, __attribute__((section("..."))) is added
    Returns generated C code as string.
    Cache-equipped targets use padded storage with the original array extent.
    Both ends are isolated without changing DMA lengths or endpoint capacities.
    """
    dma_code = []
    # Default section settings
    DEFAULT_SECTIONS = {
        "DMA": "",
        "BDMA": "",
    }

    def get_buf_section(user_section: str, dma_type: str) -> str:
        """
        Return buffer section name:
        - If user config is set, use it.
        - Otherwise, use default by dma_type.
        """
        if user_section:  # User configuration takes priority
            return user_section
        return DEFAULT_SECTIONS.get(dma_type, "")

    def buffer_declaration(data_type: str, name: str, count, section: str) -> str:
        # Align the storage type, not only its object: sizeof then includes tail
        # padding. Keep the array extent so RawData and split buffers are unchanged.
        return "\n".join([
            "#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)",
            "static struct alignas(XR_DCACHE_LINE_SIZE)",
            "{",
            f"  {data_type} data[{count}];",
            f"}} {name}_storage{section};",
            f"static constexpr auto& {name} = {name}_storage.data;",
            "#else",
            f"alignas(4) static {data_type} {name}[{count}]{section};",
            "#endif",
        ])

    # Iterate all peripherals
    for p_type_raw, instances in project_data.get("Peripherals", {}).items():
        # Normalize peripheral type (e.g. "spi1" -> "SPI")
        match = re.match(r'([A-Za-z0-9]+?)(\d*)$', p_type_raw)
        p_type_base = match.group(1).upper() if match else p_type_raw.upper()

        # Ensure settings dict exists for this peripheral
        if p_type_base not in libxr_settings:
            libxr_settings[p_type_base] = {}

        # SPI/USART/UART/LPUART
        if p_type_base in ["SPI", "USART", "UART", "LPUART"]:
            for instance, config in instances.items():
                # Check DMA enable flags
                tx_dma = config.get("DMA_TX", "DISABLE") == "ENABLE"
                rx_dma = config.get("DMA_RX", "DISABLE") == "ENABLE"
                # Use configured DMA type if available, fallback to "DMA"
                dma_type = config.get("DMA_TX_TYPE", config.get("DMA_RX_TYPE", "DMA"))
                # Instance name for variable
                instance_lower = instance.lower()
                instance_config = libxr_settings[p_type_base].setdefault(instance_lower, {})
                tx_size = instance_config.setdefault(
                    "tx_buffer_size",
                    DMA_DEFAULT_SIZES.get(p_type_base, {}).get("tx", 32)
                )
                rx_size = instance_config.setdefault(
                    "rx_buffer_size",
                    DMA_DEFAULT_SIZES.get(p_type_base, {}).get("rx", 32)
                )
                # Get dma_section config or assign default
                dma_section = instance_config.get("dma_section", None)
                if not dma_section:
                    dma_section = get_buf_section("", dma_type)
                    instance_config["dma_section"] = dma_section
                sec_str = f' __attribute__((section("{dma_section}")))' if dma_section else ""

                buf_code = []
                if tx_dma:
                    buf_code.append(buffer_declaration("uint8_t", f"{instance_lower}_tx_buf", tx_size, sec_str))
                if rx_dma:
                    buf_code.append(buffer_declaration("uint8_t", f"{instance_lower}_rx_buf", rx_size, sec_str))
                if buf_code:
                    dma_code.append("\n".join(buf_code))

        # I2C/ADC
        elif p_type_base in ["I2C", "ADC"]:
            for instance, config in instances.items():
                dma_type = config.get("DMA_RX_TYPE", "DMA")  # type tag for section picking
                instance_lower = instance.lower()
                instance_config = libxr_settings[p_type_base].setdefault(instance_lower, {})
                buf_size = instance_config.setdefault(
                    "buffer_size",
                    DMA_DEFAULT_SIZES[p_type_base]["buffer"]
                )
                dma_section = instance_config.get("dma_section", None)
                if not dma_section:
                    dma_section = get_buf_section("", dma_type)
                    instance_config["dma_section"] = dma_section
                sec_str = f' __attribute__((section("{dma_section}")))' if dma_section else ""

                # ADC buffer is uint16_t, I2C is uint8_t
                if p_type_base == "ADC":
                    # 通道选择规则：DMA 开启→RegularConversions，否则→Channels
                    active_channels = (
                        config.get("RegularConversions", [])
                        if config.get("DMA") == "ENABLE"
                        else config.get("Channels", [])
                    )
                    ch_cnt = max(1, len(active_channels))               # 至少保留 1 份缓冲
                    elems_per_channel = max(1, int(buf_size // 2))      # 每通道的 uint16_t 元素数
                    total_elems = ch_cnt * elems_per_channel            # 总元素数 = 通道数 × 每通道元素数
                    dma_code.append(buffer_declaration("uint16_t", f"{instance_lower}_buf", total_elems, sec_str))
                else:
                    dma_code.append(buffer_declaration("uint8_t", f"{instance_lower}_buf", buf_size, sec_str))

        elif p_type_base == "USB":
            # Generate buffer variables for each USB EP (controlled by dma_section)
            for instance, cfg in instances.items():
                # Normalize instance name
                inst_u = (instance or "USB_FS").upper()
                inst_u = (inst_u
                          .replace("USBOTG", "USB_OTG_")
                          .replace("OTGFS", "OTG_FS")
                          .replace("OTGHS", "OTG_HS"))
                if inst_u == "USB":
                    inst_u = "USB_FS"
                is_otg = inst_u.startswith("USB_OTG_")
                inst_lower = inst_u.lower()

                # Read or set default USB config from libxr_settings (consistent with _generate_usb)
                usb_cfg = libxr_settings.setdefault("USB", {}).setdefault(inst_lower, {})

                def _as_int(v, d):
                    try:
                        return int(str(v), 0)
                    except Exception:
                        return d

                enable = usb_cfg.setdefault("enable", cfg.get("enable", False))
                if not enable:
                    logging.info(f"Skipping disabled USB instance: {instance}")
                    continue

                if 'cdc_count' in usb_cfg or 'cdc_count' in cfg:
                    raise ValueError("USB cdc_count is not a generator option; define composite USB in BSP user code")

                # EP0 packet size, fallback to defaults if needed
                ep0 = _as_int(usb_cfg.get("ep0_packet_size", cfg.get("ep0_packet_size", cfg.get("packet_size", 8))), 8)
                if ep0 not in (8, 16, 32, 64):
                    ep0 = 8
                usb_cfg.setdefault("ep0_packet_size", ep0)

                tx_sz = usb_cfg.setdefault("tx_buffer_size", _as_int(cfg.get("tx_buffer_size", 128), 128))
                rx_sz = usb_cfg.setdefault("rx_buffer_size", _as_int(cfg.get("rx_buffer_size", 128), 128))
                usb_cfg.setdefault("rx_fifo_size", _as_int(cfg.get("rx_fifo_size", 256 if is_otg else 128), 256 if is_otg else 128))
                usb_cfg.setdefault("tx_fifo_size", _as_int(cfg.get("tx_fifo_size", 128), 128))

                # Section name (same as UART)
                dma_section = usb_cfg.get("dma_section", cfg.get("dma_section", ""))
                if "dma_section" not in usb_cfg:
                    usb_cfg["dma_section"] = dma_section
                sec_str = f' __attribute__((section("{dma_section}")))' if dma_section else ""

                # One line per variable to avoid attribute only on the last one
                dma_code.append(buffer_declaration("uint8_t", f"{inst_lower}_ep0_in_buf", ep0, sec_str))
                dma_code.append(buffer_declaration("uint8_t", f"{inst_lower}_ep0_out_buf", ep0, sec_str))
                dma_code.append(buffer_declaration("uint8_t", f"{inst_lower}_ep1_in_buf", tx_sz, sec_str))
                dma_code.append(buffer_declaration("uint8_t", f"{inst_lower}_ep1_out_buf", rx_sz, sec_str))
                dma_code.append(buffer_declaration("uint8_t", f"{inst_lower}_ep2_in_buf", 16, sec_str))

    # Final output with section header if any code generated
    if dma_code:
        # Older CMSIS core_cm7.h (e.g. STM32F7 Cube packs) lacks the line-size
        # macro; the Cortex-M7 data cache line is fixed at 32 bytes.
        output = "\n".join([
            "/* DMA Resources */",
            "#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)",
            "#if defined(__SCB_DCACHE_LINE_SIZE)",
            "#define XR_DCACHE_LINE_SIZE __SCB_DCACHE_LINE_SIZE",
            "#else",
            "#define XR_DCACHE_LINE_SIZE 32U",
            "#endif",
            "#endif",
        ]) + "\n"
        output += "\n".join(dma_code)
    else:
        output = "/* No DMA Resources generated. */"
    return output

# --------------------------
# Peripheral Generation
# --------------------------
class PeripheralFactory:
    @staticmethod
    def create(p_type: str, instance: str, config: dict) -> str:
        handler_map = {
            "ADC": PeripheralFactory._generate_adc,
            "DAC": PeripheralFactory._generate_dac,
            "TIM": PeripheralFactory._generate_tim,
            "FDCAN": PeripheralFactory._generate_canfd,
            "CAN": PeripheralFactory._generate_can,
            "SPI": PeripheralFactory._generate_spi,
            "USART": PeripheralFactory._generate_uart,
            "UART": PeripheralFactory._generate_uart,
            "LPUART": PeripheralFactory._generate_uart,
            "I2C": PeripheralFactory._generate_i2c,
            "IWDG": PeripheralFactory._generate_iwdg,
            "USB": PeripheralFactory._generate_usb,
        }
        generator = handler_map.get(p_type.upper())
        return generator(instance, config) if generator else ("", "")

    @staticmethod
    def _generate_adc(instance: str, config: dict) -> tuple:
        """Generate ADC initialization with configurable queue size."""
        conversions = config.get("RegularConversions", []) if config.get("DMA") == "ENABLE" else config.get("Channels",
                                                                                                            [])
        adc_config = libxr_settings['ADC'].setdefault(instance.lower(), {})
        vref = adc_config.setdefault('vref', 3.3)

        channels_code = f"  static STM32ADC {instance.lower()}(&h{instance.lower()}, {instance.lower()}_buf, {{{', '.join(conversions)}}}, {vref});\n"

        index = 0

        for channel in conversions:
            channels_code += f"  static auto& {instance.lower()}_{channel.lower()} = {instance.lower()}.GetChannel({index});\n"
            channels_code += f"  UNUSED({instance.lower()}_{channel.lower()});\n"
            _register_device(f"{instance.lower()}_{channel.lower()}", "ADC")
            index = index + 1

        return "adc", channels_code

    @staticmethod
    def _generate_dac(instance: str, config: dict) -> tuple:
        """
        Generate DAC initialization code.
        Always use variable name as <instance>_<out_name> (e.g., dac1_out2).
        """
        channels = config.get("Channels", {})
        if not channels:
            return "", ""
        dac_config = libxr_settings['DAC'].setdefault(instance.lower(), {})
        init_voltage = dac_config.setdefault('init_voltage', 0.0)
        vref = dac_config.setdefault('vref', 3.3)
        codes = []
        for out_name, channel_id in channels.items():
            if channel_id.startswith("DAC_OUT"):
                m = re.search(r'DAC_OUT(\d+)', channel_id)
                channel_id = "DAC_CHANNEL_" + m.group(1)
            var_name = f"{instance.lower()}_{out_name.lower()}"
            if var_name.startswith("dac_dac_"):
                var_name = var_name.replace("dac_dac_", "dac_")
            codes.append(
                f"  static STM32DAC {var_name}(&h{instance.lower()}, {channel_id}, {init_voltage}, {vref});"
            )
            _register_device(var_name, "DAC")
        return "main", "\n".join(codes) + "\n"

    @staticmethod
    def _generate_uart(instance: str, config: dict) -> tuple:
        tx_dma = config.get("DMA_TX", "DISABLE") == "ENABLE"
        rx_dma = config.get("DMA_RX", "DISABLE") == "ENABLE"
        tx_buf = f"{instance.lower()}_tx_buf" if tx_dma else "{nullptr, 0}"
        rx_buf = f"{instance.lower()}_rx_buf" if rx_dma else "{nullptr, 0}"

        uart_config = libxr_settings['USART'].setdefault(instance.lower(), {})
        tx_queue = uart_config.setdefault("tx_queue_size", 5)

        code = f"  static STM32UART {instance.lower()}(&h{instance.lower().replace('usart', 'uart')},\n" \
               f"              {rx_buf}, {tx_buf}, {tx_queue});\n"
        _register_device(f"{instance.lower()}", "UART")
        return "main", code

    @staticmethod
    def _generate_i2c(instance: str, config: dict) -> tuple:
        """Generate I2C initialization code with dynamic buffer configuration."""
        i2c_config = libxr_settings['I2C'].setdefault(instance.lower(), {})
        dma_min_size = i2c_config.setdefault('dma_enable_min_size', 3)
        _register_device(f"{instance.lower()}", "I2C")
        return ("main",
                f"  static STM32I2C {instance.lower()}(&h{instance.lower()}, {instance.lower()}_buf, {dma_min_size});\n")

    @staticmethod
    def _generate_tim(instance: str, config: dict) -> tuple:
        channels = config.get('Channels', {})
        if not channels:
            return "", ""
        code = ""
        for ch_name, ch_cfg in channels.items():
            ch_num = ch_name.replace('CH', '').lower()
            dev_name = f"pwm_{instance.lower()}_ch{ch_num}"
            complementary = ch_cfg.get("Complementary", False)
            if complementary:
                if ch_num.endswith("N") or ch_num.endswith("n"):
                    ch_num = ch_num[:-1]
                code += f"  static STM32PWM {dev_name}(&h{instance.lower()}, TIM_CHANNEL_{ch_num}, true);\n"
            else:
                code += f"  static STM32PWM {dev_name}(&h{instance.lower()}, TIM_CHANNEL_{ch_num}, false);\n"
            _register_device(dev_name, "PWM")
        return "pwm", code

    @staticmethod
    def _generate_canfd(instance: str, config: dict) -> tuple:
        """Generate CAN FD initialization with configurable queue size."""
        instance_cfg = libxr_settings['FDCAN'].setdefault(instance, {})
        queue_size = instance_cfg.setdefault('queue_size', 5)

        _register_device(f"{instance.lower()}", "FDCAN")
        return ("main",
                f'  static STM32CANFD {instance.lower()}(&h{instance.lower()}, {queue_size});\n')

    @staticmethod
    def _generate_can(instance: str, config: dict) -> tuple:
        """Generate classic CAN initialization with queue configuration."""
        instance_cfg = libxr_settings['CAN'].setdefault(instance, {})
        queue_size = instance_cfg.setdefault('queue_size', 5)

        _register_device(f"{instance.lower()}", "CAN", f"classic CAN peripheral {instance}")
        return ("main",
                f'  static STM32CAN {instance.lower()}(&h{instance.lower()}, {queue_size});\n')

    @staticmethod
    def _generate_spi(instance: str, config: dict) -> tuple:
        """Generate SPI initialization with DMA buffer configuration."""
        tx_enabled = config.get('DMA_TX', 'DISABLE') == 'ENABLE'
        rx_enabled = config.get('DMA_RX', 'DISABLE') == 'ENABLE'

        spi_config = libxr_settings['SPI'].setdefault(instance.lower(), {})
        dma_min_size = spi_config.setdefault('dma_enable_min_size', 3)

        tx_buf = f"{instance.lower()}_tx_buf" if tx_enabled else "{nullptr, 0}"
        rx_buf = f"{instance.lower()}_rx_buf" if rx_enabled else "{nullptr, 0}"

        _register_device(f"{instance.lower()}", "SPI")

        return ("main",
                f'  static STM32SPI {instance.lower()}(&h{instance.lower()}, {rx_buf}, {tx_buf}, {dma_min_size});\n')

    @staticmethod
    def _generate_iwdg(instance: str, config: dict) -> tuple:
        if not config.get("Enabled"):
            return "", ""
        iwdg_config = libxr_settings['IWDG'].setdefault(instance.lower(), {})
        timeout_ms = iwdg_config.setdefault("timeout_ms", config.get("Configuration", {}).get("timeout_ms", 1000))
        feed_ms = iwdg_config.setdefault("feed_interval_ms",
                                         config.get("Configuration", {}).get("feed_interval_ms", 250))
        code = (
            f"  static STM32Watchdog {instance.lower()}(&h{instance.lower()}, "
            f"{timeout_ms}, {feed_ms});\n"
        )
        _register_device(instance.lower(), "Watchdog")
        return "main", code

    @staticmethod
    def _generate_usb(instance: str, config: dict) -> tuple:
        """
        Simple version:
        - Only writes/updates the final value of libxr_settings['USB'][instance_lower]
        - Only generates device construction code (references *_buf), does not create any buffer arrays
        """
        cfg_in = config or {}

        # Normalize instance name (consistent with extern declarations)
        inst_u = (instance or "USB_FS").upper()
        inst_u = (inst_u
                .replace("USBOTG", "USB_OTG_")
                .replace("OTGFS", "OTG_FS")
                .replace("OTGHS", "OTG_HS"))
        if inst_u == "USB":
            inst_u = "USB_FS"
        if inst_u not in {"USB_FS", "USB_HS", "USB_OTG_FS", "USB_OTG_HS"}:
            inst_u = "USB_FS"

        is_otg = inst_u.startswith("USB_OTG_")
        speed = "HS" if inst_u.endswith("_HS") else "FS"
        inst_lower = inst_u.lower()      # Example: usb_fs / usb_otg_fs
        obj = f"usb_{speed.lower()}"     # Example: usb_fs / usb_hs

        # Update settings (consistent with other modules)
        usb_root = libxr_settings.setdefault("USB", {})
        inst_cfg = usb_root.setdefault(inst_lower, {})

        def _as_int(v, d):
            try:
                return int(str(v), 0)  # Support 0x (hex) style
            except Exception:
                return d

        # Enable switch
        inst_cfg.setdefault("enable", cfg_in.get("enable", False))
        if not inst_cfg["enable"]:
            logging.info(f"USB instance '{inst_lower}' is disabled. Skipping generation.")
            return "", ""

        # Packet size and FIFO setup
        ep0 = _as_int(cfg_in.get("ep0_packet_size", cfg_in.get("packet_size", inst_cfg.get("ep0_packet_size", 8))), 8)
        if ep0 not in (8, 16, 32, 64):
            ep0 = 8
        inst_cfg.setdefault("ep0_packet_size", ep0)

        # DMA buffer sizes
        inst_cfg.setdefault("tx_buffer_size", _as_int(cfg_in.get("tx_buffer_size", inst_cfg.get("tx_buffer_size", 128)), 128))
        inst_cfg.setdefault("rx_buffer_size", _as_int(cfg_in.get("rx_buffer_size", inst_cfg.get("rx_buffer_size", 128)), 128))

        # USB HW FIFO sizes
        inst_cfg.setdefault("tx_fifo_size", _as_int(cfg_in.get("tx_fifo_size", inst_cfg.get("tx_fifo_size", 128)), 128))
        inst_cfg.setdefault("rx_fifo_size", _as_int(cfg_in.get("rx_fifo_size", inst_cfg.get("rx_fifo_size", 256 if is_otg else 128)), 256 if is_otg else 128))
        # CDC FIFO
        inst_cfg.setdefault("cdc_tx_fifo_size", _as_int(cfg_in.get("cdc_tx_fifo_size", inst_cfg.get("cdc_tx_fifo_size", 128)), 128))
        inst_cfg.setdefault("cdc_rx_fifo_size", _as_int(cfg_in.get("cdc_rx_fifo_size", inst_cfg.get("cdc_rx_fifo_size", 128)), 128))
        inst_cfg.setdefault("cdc_queue_size", _as_int(cfg_in.get("cdc_queue_size", inst_cfg.get("cdc_queue_size", 3)), 3))
        if 'cdc_count' in cfg_in or 'cdc_count' in inst_cfg:
            raise ValueError("USB cdc_count is not a generator option; define composite USB in BSP user code")
        # DMA section name
        inst_cfg.setdefault("dma_section", cfg_in.get("dma_section", inst_cfg.get("dma_section", "")))

        # https://github.com/openmoko/openmoko-usb-oui/commit/27f3846d77e0d0d10271b809b831f70040c6197a
        # Descriptor information — 默认 1d50:6199 / 0x0100 / "XRUSB-DEMO-"
        inst_cfg.setdefault(
            "vid",
            _as_int(cfg_in.get("vid", inst_cfg.get("vid", 0x1d50)), 0x1d50)
        )
        inst_cfg.setdefault(
            "pid",
            _as_int(cfg_in.get("pid", inst_cfg.get("pid", 0x6199)), 0x6199)
        )
        inst_cfg.setdefault(
            "bcd",
            _as_int(cfg_in.get("bcd", inst_cfg.get("bcd", 0x0100)), 0x0100)
        )
        inst_cfg.setdefault(
            "manufacturer",
            cfg_in.get("manufacturer", inst_cfg.get("manufacturer", "XRobot"))
        )
        inst_cfg.setdefault(
            "product",
            cfg_in.get("product", inst_cfg.get("product", f"STM32 XRUSB {instance} CDC Demo"))
        )
        inst_cfg.setdefault(
            "serial",
            cfg_in.get("serial", inst_cfg.get("serial", "XRUSB-DEMO-"))
        )

        # Get the final value from settings for code generation
        ep0_sz = int(inst_cfg["ep0_packet_size"])
        tx_buf_sz = int(inst_cfg["tx_buffer_size"])   # USB DMA
        rx_buf_sz = int(inst_cfg["rx_buffer_size"])   # USB DMA
        tx_fifo_size = int(inst_cfg["tx_fifo_size"])  # EP1 HW FIFO
        rx_fifo_size = int(inst_cfg["rx_fifo_size"])  # EP1 HW FIFO
        cdc_tx_fifo_size = int(inst_cfg["cdc_tx_fifo_size"])
        cdc_rx_fifo_size = int(inst_cfg["cdc_rx_fifo_size"])
        cdc_queue_size = int(inst_cfg["cdc_queue_size"])
        vid = int(inst_cfg["vid"])
        pid = int(inst_cfg["pid"])
        bcd = int(inst_cfg["bcd"])
        manufacturer = str(inst_cfg["manufacturer"]).replace('"', '\\"')
        product = str(inst_cfg["product"]).replace('"', '\\"')
        serial = str(inst_cfg["serial"]).replace('"', '\\"')

        # Size enum for EP0
        size_enum = {8: "SIZE_8", 16: "SIZE_16", 32: "SIZE_32", 64: "SIZE_64"}[ep0_sz]
        lang_var = f"{inst_lower}_lang_pack".upper()
        cdc_var = f"{inst_lower}_cdc"
        pcd_handle = f"hpcd_USB_OTG_{speed}" if is_otg else f"hpcd_USB_{speed}"
        instance_type = "STM32USBDeviceOtgFS" if (is_otg and speed == "FS") else \
            "STM32USBDeviceOtgHS" if (is_otg and speed == "HS") else \
            "STM32USBDeviceDevFs"

        # Generate device construction code (buffer variables are defined elsewhere)
        code = []
        code.append(
            f"  static constexpr auto {lang_var} = "
            "LibXR::USB::DescriptorStrings::MakeLanguagePack("
            "LibXR::USB::DescriptorStrings::Language::EN_US, "
            f"\"{manufacturer}\", \"{product}\", \"{serial}\");"
        )
        # CDC construction with explicit endpoint numbers.
        # CDC1: EP1 IN/OUT data, EP2 IN notification.
        code.append(
            f"  static LibXR::USB::CDCUart {cdc_var}("
            "LibXR::USB::Endpoint::EPNumber::EP1, "
            "LibXR::USB::Endpoint::EPNumber::EP1, "
            "LibXR::USB::Endpoint::EPNumber::EP2, "
            f"{cdc_rx_fifo_size}, {cdc_tx_fifo_size}, {cdc_queue_size});")
        code.append("")

        if is_otg:
            out_buffers = [f"{inst_lower}_ep0_out_buf", f"{inst_lower}_ep1_out_buf"]
            in_buffers = [
                f"{{{inst_lower}_ep0_in_buf, {ep0_sz}}}",
                f"{{{inst_lower}_ep1_in_buf, {tx_fifo_size}}}",
                f"{{{inst_lower}_ep2_in_buf, 16}}",
            ]
            code.append(f"  static {instance_type} {obj}(")
            code.append(f"      &{pcd_handle},")
            code.append(f"      {rx_fifo_size},")
            code.append("      {" + ", ".join(out_buffers) + "},")
            code.append("      {" + ", ".join(in_buffers) + "},")
            code.append(f"      USB::DeviceDescriptor::PacketSize0::{size_enum},")
            code.append(f"      0x{vid:X}, 0x{pid:X}, 0x{bcd:X},")
            code.append(f"      {{&{lang_var}}},")
            code.append(f"      {{{{&{cdc_var}}}}},")
            code.append("      {reinterpret_cast<void *>(UID_BASE), 12}")
            code.append("  );")
        else:
            code.append(f"  static {instance_type} {obj}(")
            code.append(f"      &{pcd_handle},")
            code.append("      {")
            code.append(f"          {{{inst_lower}_ep0_in_buf, {inst_lower}_ep0_out_buf, {ep0_sz}, {ep0_sz}}},")
            code.append(f"          {{{inst_lower}_ep1_in_buf, {inst_lower}_ep1_out_buf, {tx_fifo_size}, {rx_buf_sz}}},")
            code.append(f"          {{{inst_lower}_ep2_in_buf, 16, true}}")
            code.append("      },")
            code.append(f"      USB::DeviceDescriptor::PacketSize0::{size_enum},")
            code.append(f"      0x{vid:X}, 0x{pid:X}, 0x{bcd:X},")
            code.append(f"      {{&{lang_var}}},")
            code.append(f"      {{{{&{cdc_var}}}}},")
            code.append("      {reinterpret_cast<void *>(UID_BASE), 12}")
            code.append("  );")

        code.append(f"  {obj}.Init(false);")
        code.append(f"  {obj}.Start(false);\n")

        _register_device(cdc_var, "UART")
        return "main", "\n".join(code)


def _generate_header_includes(use_xrobot: bool = False) -> str:
    """Generate essential header inclusions with optional XRobot components."""
    headers = [
        '#include "app_main.h"\n',
        '#include "cdc_uart.hpp"',
        '#include "libxr.hpp"',
        '#include "main.h"',
        '#include "stm32_adc.hpp"',
        '#include "stm32_can.hpp"',
        '#include "stm32_canfd.hpp"',
        '#include "stm32_dac.hpp"',
        '#include "stm32_flash.hpp"',
        '#include "stm32_gpio.hpp"',
        '#include "stm32_i2c.hpp"',
        '#include "stm32_power.hpp"',
        '#include "stm32_pwm.hpp"',
        '#include "stm32_spi.hpp"',
        '#include "stm32_timebase.hpp"',
        '#include "stm32_uart.hpp"',
        '#include "stm32_usb_dev.hpp"',
        '#include "stm32_watchdog.hpp"',
        '#include "flash_map.hpp"'
    ]

    if use_xrobot:
        headers.append('#include "xrobot_main.hpp"')

    return '\n'.join(headers) + '\n\nusing namespace LibXR;\n'


def _generate_extern_declarations(project_data: dict) -> str:
    """Generate external declarations for HAL handlers with comprehensive checks."""
    externs = set()

    # Timebase source declaration
    timebase_cfg = project_data.get('Timebase', {})
    if timebase_cfg.get('Source', 'SysTick') != 'SysTick':
        src = timebase_cfg['Source']
        if src.startswith('TIM'):
            externs.add(f'extern TIM_HandleTypeDef h{src.lower()};')
        elif src.startswith('LPTIM'):
            externs.add(f'extern LPTIM_HandleTypeDef h{src.lower()};')
        elif src.startswith('HRTIM'):
            externs.add(f'extern HRTIM_HandleTypeDef h{src.lower()};')

    # Peripheral declarations
    peripherals = project_data.get('Peripherals', {})
    for p_type, instances in peripherals.items():
        for instance in instances:
            if p_type == 'USB':
                # New USB stack uses PCD handle (e.g., hpcd_USB_FS / hpcd_USB_HS)
                if instance == 'USB':
                    instance = "USB_FS"
                externs.add(f"extern PCD_HandleTypeDef hpcd_{instance};")
            elif p_type == 'DAC':
                externs.add(f'extern DAC_HandleTypeDef h{instance.lower()};')
            else:
                handle_type = 'UART_HandleTypeDef' if p_type in ['USART', 'UART',
                                                                 'LPUART'] else f'{p_type}_HandleTypeDef'
                if p_type in ['USART', 'UART', 'LPUART']:
                    externs.add(f'extern {handle_type} h{instance.lower().replace("usart", "uart")};')
                else:
                    externs.add(f'extern {handle_type} h{instance.lower()};')

    return '/* External HAL Declarations */\n' + '\n'.join(sorted(externs)) + '\n'


def preserve_user_blocks(existing_code: str, section: int) -> str:
    """Return one numbered User Code region using the structured C++ parser."""
    document = CppDocument.parse(existing_code)
    target = str(section)
    for region in document.user_regions():
        if region.name == target:
            content = region.body_text.strip()
            return "  " + content if section != 1 and content else content
    return ""


_USER_MARKER_TEXT = re.compile(r"(?://|/\*)\s*User\s*Code\s*(?:Begin|End)\b", re.IGNORECASE)
_USER_MARKER_BEGIN = re.compile(r"/\*\s*User Code Begin(?:\s+(.+?))?\s*\*/")
_USER_MARKER_END = re.compile(r"/\*\s*User Code End(?:\s+(.+?))?\s*\*/")
_PREPROC_DIRECTIVE = re.compile(r"#\s*(\w+)")


def _source_line(source: bytes, offset: int) -> int:
    return source.count(b"\n", 0, offset) + 1


def validate_user_regions(existing_code: str, region_names) -> None:
    """Refuse a rewrite that would drop code the user placed around markers.

    Only the bodies of the generator's own User Code regions survive a
    rewrite. A marker that is malformed, renamed, duplicated, unpaired,
    missing or inside a preprocessor conditional would silently lose code or
    change what the preprocessor keeps, so every such marker is reported.
    """
    if not existing_code.strip():
        return
    document = CppDocument.parse(existing_code)
    source = document.render_bytes()
    expected = list(region_names)
    problems = []
    elements = sorted(
        (element for element in document.root.descendants(include_trivia=True)
         if element.kind == "comment" or element.kind.startswith("preproc_")),
        key=lambda element: element.span.start,
    )
    depth = 0
    open_region = None
    seen = []
    for element in elements:
        line = _source_line(source, element.span.start)
        if element.kind != "comment":
            directive = _PREPROC_DIRECTIVE.match(element.text.strip())
            keyword = directive.group(1) if directive else ""
            if keyword in ("if", "ifdef", "ifndef"):
                depth += 1
            elif keyword == "endif":
                depth = max(0, depth - 1)
            continue
        text = element.text.strip()
        if not _USER_MARKER_TEXT.match(text):
            continue
        begin = _USER_MARKER_BEGIN.fullmatch(text)
        end = _USER_MARKER_END.fullmatch(text)
        if begin is None and end is None:
            problems.append(f"line {line}: malformed User Code marker {text}")
            continue
        name = ((begin or end).group(1) or "").strip()
        if depth:
            problems.append(
                f"line {line}: {text} is inside a preprocessor conditional")
        if name not in expected:
            problems.append(
                f"line {line}: {text} names a region the generator does not emit "
                f"(expected {', '.join(expected)})")
        if begin is not None:
            if open_region is not None:
                problems.append(
                    f"line {line}: {text} opens before User Code End {open_region}")
            if name in seen:
                problems.append(f"line {line}: {text} is duplicated")
            seen.append(name)
            open_region = name
        else:
            if open_region != name:
                problems.append(f"line {line}: {text} has no matching Begin marker")
            else:
                open_region = None
    if open_region is not None:
        problems.append(f"User Code Begin {open_region} has no matching End marker")
    for name in expected:
        if name not in seen:
            problems.append(f"User Code Begin {name} / End {name} markers are missing")
    if problems:
        raise ValueError(
            "existing User Code markers cannot be preserved safely; nothing was "
            "written. Fix the markers and regenerate:\n  " + "\n  ".join(problems))


def _preserve_generated_regions(existing_code: str, generated_code: str) -> str:
    """Preserve explicit User Code bodies; regenerate format/lint-protected code.

    clang-format and NOLINT control tooling, not ownership of generated code.
    Markers nested inside User Code remain part of the preserved user body.
    """
    previous = CppDocument.parse(existing_code)
    current = CppDocument.parse(generated_code)
    validate_user_regions(
        existing_code, [region.name for region in current.user_regions()])
    used = set()
    for old_region in previous.user_regions():
        regions = list(current.user_regions())
        match = next(
            ((index, region) for index, region in enumerate(regions)
             if index not in used and region.name == old_region.name),
            None,
        )
        if match is None:
            continue
        index, region = match
        current = current.replace_region_body(region, old_region.body_text)
        used.add(index)
    return current.render_bytes().decode("utf-8", errors="surrogateescape")


def _generate_core_system(project_data: dict) -> str:
    """Generate core system initialization with timebase configuration."""
    timebase_cfg = project_data.get('Timebase', {'Source': 'SysTick'})
    source = timebase_cfg.get('Source', 'SysTick')

    timebase_init = '  static STM32Timebase timebase;'  # Default to SysTick

    if source != 'SysTick':
        timer_type = 'TIM' if source.startswith('TIM') else \
            'LPTIM' if source.startswith('LPTIM') else 'HRTIM'
        handler = f'h{source.lower()}'
        timebase_init = f'  static STM32TimerTimebase timebase(&{handler});'

    system_type = libxr_settings['SYSTEM']
    timer_cfg = libxr_settings['software_timer']

    init_args = ""
    if system_type == 'None':  # Bare-metal
        init_args = ""
    elif system_type == 'FreeRTOS' or system_type == 'ThreadX':
        init_args = f"{timer_cfg['priority']}, {timer_cfg['stack_depth']}"
    else:
        logging.error(f'Unsupported system type: {system_type}')
        sys.exit(1)

    return f"""{timebase_init}
  PlatformInit({init_args});
  static STM32PowerManager power_manager;"""


def generate_gpio_config(project_data: dict) -> str:
    """Generate GPIO initialization code with EXTI support."""
    code = '\n  /* GPIO Configuration */\n'
    for port, config in project_data.get('GPIO', {}).items():
        alias = generate_gpio_alias(port, config, project_data)
        code += f'  static STM32GPIO {alias};\n'
    return code


# Watchdog
def configure_watchdog(project_data: dict) -> str:
    code = ""
    watchdog_instances = []
    for name, cfg in project_data.get("Peripherals", {}).get("IWDG", {}).items():
        if cfg.get("Enabled"):
            watchdog_instances.append(name.lower())
    if not watchdog_instances:
        return code

    wdg_config = libxr_settings.setdefault("Watchdog", {})
    run_as_thread = wdg_config.setdefault("run_as_thread", False)
    feed_interval = wdg_config.setdefault("feed_interval_ms", 250)

    for name in watchdog_instances:
        code += f"""  {name}.Feed();
"""
        if run_as_thread:
            thread_stack = wdg_config.setdefault("thread_stack_depth", 1024)
            thread_priority = wdg_config.setdefault("thread_priority", 3)
            code += f"""  static LibXR::Thread {name}_thread;
  {name}_thread.Create(reinterpret_cast<LibXR::Watchdog *>(&{name}), {name}.ThreadFun, "{name}_wdg", {thread_stack},
                      static_cast<LibXR::Thread::Priority>({thread_priority}));
"""
        else:
            code += f"""  static auto {name}_task = Timer::CreateTask({name}.TaskFun, reinterpret_cast<LibXR::Watchdog *>(&{name}), {feed_interval});
  Timer::Add({name}_task);
  Timer::Start({name}_task);
"""
    return code


# --------------------------
# Terminal Configuration
# --------------------------
def configure_terminal(project_data: dict) -> str:
    code = "  /* Terminal Configuration */\n"
    terminal_source = libxr_settings.get("terminal_source", "").lower()

    # User-specified terminal source
    if terminal_source != "":
        dev = terminal_source.lower()
        # Device must be registered and of type UART, otherwise log a warning and skip
        if registered_devices.get(dev) != "UART":
            logging.warning(f"terminal_source '{terminal_source}' is not registered as UART, terminal will not be initialized!")
            return code
        dev = terminal_source.upper()
        code += (
            f"  STDIO::read_ = {dev.lower()}.read_port_;\n"
            f"  STDIO::write_ = {dev.lower()}.write_port_;\n"
        )

    if terminal_source != "":
        term_config = libxr_settings.setdefault("Terminal", {})
        params = [
            term_config.setdefault("read_buff_size", 32),
            term_config.setdefault("max_line_size", 32),
            term_config.setdefault("max_arg_number", 5),
            term_config.setdefault("max_history_number", 5)
        ]

        run_as_thread = term_config.setdefault("run_as_thread", False)

        if run_as_thread:
            thread_stack_depth = term_config.setdefault("thread_stack_depth", 1024)
            thread_priority = term_config.setdefault("thread_priority", 3)

        code += f"""
  static RamFS ramfs("XRobot");
  static Terminal<{', '.join(map(str, params))}> terminal(ramfs);
"""
        if run_as_thread:
            code += f"""\
  static LibXR::Thread term_thread;
  term_thread.Create(&terminal, terminal.ThreadFun, "terminal", {thread_stack_depth},
                     static_cast<LibXR::Thread::Priority>({thread_priority}));
"""
        else:
            code += f"""\
  static auto terminal_task = Timer::CreateTask(terminal.TaskFun, &terminal, 10);
  Timer::Add(terminal_task);
  Timer::Start(terminal_task);
"""
        _register_device("ramfs", "RamFS")
        _register_device("terminal", f"Terminal<{', '.join(map(str, params))}>")
    return code


# --------------------------
# XRobot Integration
# --------------------------
def generate_xrobot_registrations() -> str:
    """Expose named BSP objects to the static entry without a runtime container.

    Every generated device object is registered under its own C++ name; the
    YAML configuration selects hardware by these names.
    """
    lines = []
    for name, cpp_type in registered_devices.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise ValueError(f"Static registration needs an existing C++ name: {name}")
        if not isinstance(cpp_type, str) or not cpp_type or cpp_type == "Unknown":
            raise ValueError(f"Explicit registration type is missing for {name}")
        if not cpp_type.startswith("LibXR::"):
            cpp_type = "LibXR::" + cpp_type
        lines.append(f"  XR_REGISTER({name}, {cpp_type});")
    return "\n".join(lines) + "\n"


# --------------------------
# Main Generator
# --------------------------
def reject_user_xrobot_main(existing_code: str) -> None:
    """XROBOT_MAIN() belongs to the generator; a User Code copy is a leftover.

    Older generators emitted the call as the default body of User Code 3.
    Keeping that copy would leave a second entry call, so the user deletes it.
    """
    if not existing_code.strip():
        return
    document = CppDocument.parse(existing_code)
    for region in document.user_regions():
        for invocation in document.invocation_views("XROBOT_MAIN"):
            if region.body_span.start <= invocation.span.start < region.body_span.end:
                raise ValueError(
                    f"line {invocation.line}: User Code {region.name} still calls "
                    f"{invocation.text}. The generator now emits XROBOT_MAIN() after "
                    "the User Code regions of app_main; delete this call from the "
                    "User Code region and regenerate. Nothing was written.")


def generate_full_code(project_data: dict, use_xrobot: bool, existing_code: str) -> str:
    if use_xrobot:
        reject_user_xrobot_main(existing_code)
    user_code_def_3 = '' if use_xrobot else f"  while(true) {{\n    Thread::Sleep(UINT32_MAX);\n  }}\n"
    components = [
        _generate_header_includes(use_xrobot),
        '/* User Code Begin 1 */',
        '/* User Code End 1 */',
        '// NOLINTBEGIN',
        '// clang-format off',
        _generate_extern_declarations(project_data),

        generate_dma_resources(project_data),

        '\nextern "C" void app_main(void) {',
        '  // clang-format on',
        '  // NOLINTEND',
        '  /* User Code Begin 2 */',
        '  /* User Code End 2 */',
        '  // clang-format off',
        '  // NOLINTBEGIN',
        _generate_core_system(project_data),
        generate_gpio_config(project_data),
        generate_peripheral_instances(project_data, use_xrobot),
        configure_terminal(project_data),
        configure_watchdog(project_data),
        generate_xrobot_registrations() if use_xrobot else '',
        '  // clang-format on',
        '  // NOLINTEND',
        '  /* User Code Begin 3 */',
        user_code_def_3.rstrip('\n'),
        '  /* User Code End 3 */',
        '  XROBOT_MAIN();' if use_xrobot else '',
        '}'
    ]
    generated = '\n'.join(filter(None, components))
    check_gpio_names(project_data, generated, use_xrobot)
    return _preserve_generated_regions(existing_code, generated)


def generate_app_main_header(output_dir: str) -> None:
    """Generate app_main.h header file."""
    header_path = os.path.join(output_dir, "app_main.h")
    content = """#ifdef __cplusplus
extern "C" {
#endif

void app_main(void);

#ifdef __cplusplus
}
#endif
"""

    if not os.path.exists(header_path) or open(header_path).read() != content:
        with open(header_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        logging.info(f"Generated header: {header_path}")


def generate_flash_map_cpp(flash_info: dict) -> str:
    """
    Convert flash_info dictionary to a C++ constexpr struct array.

    :param flash_info: Output from flash_info_to_dict

    :return: C++ code as a string
    """
    lines = [
        "#include \"stm32_flash.hpp\"",
        "",
        "constexpr LibXR::FlashSector FLASH_SECTORS[] = {",
    ]

    for s in flash_info["sectors"]:
        index = s["index"]
        address = int(s["address"], 16)
        size_kb = int(s["size_kb"])
        lines.append(f"  {{0x{address:08X}, 0x{(size_kb * 1024):08X}}},")

    lines.append("};\n")
    lines.append("constexpr size_t FLASH_SECTOR_NUMBER = sizeof(FLASH_SECTORS) / sizeof(LibXR::FlashSector);")
    return "\n".join(lines)


def inject_flash_layout(project_data: dict, output_dir: str) -> None:
    """
    Automatically generate FlashLayout from project_data['Mcu']['Type']
    and inject it into libxr_settings. Also generates flash_map.hpp.

    :param project_data: Project configuration containing MCU type

    :param output_dir: Output directory for generated flash_map.hpp
    """
    try:
        from libxr.STM32FlashGenerator import layout_flash, flash_info_to_dict
        mcu_model = project_data.get("Mcu", {}).get("Type", "").strip()
        if not mcu_model:
            logging.warning("Cannot find MCU name, skipping FlashLayout generation")
            return

        flash_info = layout_flash(mcu_model)
        flash_dict = flash_info_to_dict(flash_info)
        libxr_settings["FlashLayout"] = flash_dict
        logging.info(f"FlashLayout is generated and injected, MCU: {mcu_model}")

        cpp_code = generate_flash_map_cpp(flash_dict)
        if output_dir:
            hpp_path = os.path.join(output_dir, "flash_map.hpp")
            with open(hpp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"""#pragma once
// Auto-generated Flash Layout Map
// MCU: {mcu_model}

#include "main.h"

""")
                f.write(cpp_code)
            logging.info(f"Flash layout map written to: {hpp_path}")
    except ImportError as e:
        logging.warning(f"Cannot import FlashLayout generator: {e}")
    except Exception as e:
        logging.warning(f"Cannot generate FlashLayout: {e}")


def main():
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    try:
        # Parse arguments
        args = parse_arguments()

        use_xrobot = args.xrobot

        # A bare file name writes into the current directory.
        output_dir = os.path.dirname(args.output) or os.curdir

        # Load configurations
        project_data = load_configuration(args.input, use_xrobot)
        load_libxr_config(output_dir, args.libxr_config)
        initialize_registry(use_xrobot)

        # Generate code
        existing_code = ""
        if os.path.exists(args.output):
            with open(args.output, "r", encoding="utf-8") as f:
                existing_code = f.read()

        output_code = generate_full_code(project_data, use_xrobot, existing_code)

        # Write output
        os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(output_code)

        inject_flash_layout(project_data, output_dir)

        config_path = os.path.join(output_dir, "libxr_config.yaml")

        save_libxr_config(config_path)

        logging.info(f"Successfully generated: {output_dir}")

        generate_app_main_header(output_dir)
        logging.info("Generated header file: app_main.h")

    except Exception as e:
        logging.error(f"Generation failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
