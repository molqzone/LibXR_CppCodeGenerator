#!/usr/bin/env python3

import argparse
import logging
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import yaml

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


class ConfigurationManager:
    """Container for normalized MSPM0 parse output."""

    def __init__(self) -> None:
        self.gpio_pins: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.peripherals: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self.dma_requests: Dict[str, Any] = {}
        self.dma_configs: Dict[str, Any] = {}
        self.timebase: Dict[str, Optional[str]] = {"Source": "SysTick", "IRQ": None}
        self.mcu_config: Dict[str, Optional[str]] = {"Family": "TI", "Type": None}
        self.freertos_config: Dict[str, Any] = {
            "RTOS": "FreeRTOS",
            "Enabled": False,
            "AllocationMethod": None,
            "MemPoolSize": None,
            "CorePresent": None,
            "Tasks": {},
            "Heap": None,
            "Features": {},
        }

    def clean_structure(self) -> Dict[str, Any]:
        """Apply the same top-level cleanup pattern used by STM32 parser."""
        cleaned_data = {
            "GPIO": self._clean_gpio(),
            "Peripherals": self._clean_peripherals(),
            "DMA": {
                "Requests": self.dma_requests,
                "Configurations": self._clean_dma_configs(),
            },
            "Timebase": self.timebase,
            "Mcu": self.mcu_config,
        }

        cleaned_freertos = self._clean_freertos()
        if any(
            [
                cleaned_freertos["Enabled"],
                cleaned_freertos["Tasks"],
                cleaned_freertos["Heap"],
                cleaned_freertos["Features"],
            ]
        ):
            cleaned_data["FreeRTOS"] = cleaned_freertos

        return cleaned_data

    def _clean_gpio(self) -> Dict[str, Dict[str, Any]]:
        return {
            pin: config
            for pin, config in self.gpio_pins.items()
            if self._is_valid_gpio(config)
        }

    def _is_valid_gpio(self, config: Dict[str, Any]) -> bool:
        return config.get("Signal") in {"GPIO_Output", "GPIO_Input"} or config.get(
            "Signal", ""
        ).startswith("GPXTI")

    def _clean_peripherals(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return {
            p_type: {
                p_name: self._clean_peripheral_config(cfg)
                for p_name, cfg in p_group.items()
            }
            for p_type, p_group in self.peripherals.items()
        }

    @staticmethod
    def _clean_peripheral_config(config: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in config.items() if v not in (None, "", [], {})}

    def _clean_dma_configs(self) -> Dict[str, Any]:
        return {k: v for k, v in self.dma_configs.items() if v}

    def _clean_freertos(self) -> Dict[str, Any]:
        return {
            "RTOS": self.freertos_config.get("RTOS", "FreeRTOS"),
            "Enabled": self.freertos_config.get("Enabled", False),
            "AllocationMethod": self.freertos_config.get("AllocationMethod"),
            "MemPoolSize": self.freertos_config.get("MemPoolSize"),
            "CorePresent": self.freertos_config.get("CorePresent"),
            "Tasks": self.freertos_config.get("Tasks", {}),
            "Heap": self.freertos_config.get("Heap"),
            "Features": [
                feat.replace("INCLUDE_", "")
                for feat, enabled in self.freertos_config.get("Features", {}).items()
                if enabled
            ],
        }


def _parse_literal(raw_value: str) -> Any:
    """Parse a simple SysConfig literal value."""
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value


def _extract_module_aliases(syscfg_text: str) -> Dict[str, str]:
    """Extract variable -> module path mappings from addModule calls."""
    aliases: Dict[str, str] = {}
    for var, module_path in re.findall(
        r'const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*scripting\.addModule\(\s*["\']([^"\']+)["\']',
        syscfg_text,
    ):
        aliases[var] = module_path
    return aliases


def _extract_cli_board(syscfg_text: str) -> Optional[str]:
    """Extract board path from SysConfig @cliArgs/@v2CliArgs metadata."""
    match = re.search(r'--board\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))', syscfg_text)
    if not match:
        return None
    board_path = next((item for item in match.groups() if item), "").strip()
    if not board_path:
        return None
    return os.path.basename(board_path)


def _extract_device_from_board(board_name: str) -> Optional[str]:
    """Infer MCU model from board name, e.g. LP_MSPM0G3507 -> MSPM0G3507."""
    match = re.search(r"(MSPM0[A-Za-z0-9]+)", board_name, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def _extract_cli_device(syscfg_text: str) -> Optional[str]:
    """Extract device name from @v2CliArgs/@cliArgs metadata."""
    v2_matches = re.findall(
        r'@v2CliArgs[^\n\r]*--device\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))',
        syscfg_text,
        re.IGNORECASE,
    )
    if v2_matches:
        v2_value = next((item for item in v2_matches[-1] if item), "").strip()
        if v2_value:
            return v2_value.upper()

    matches = re.findall(
        r'--device\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))',
        syscfg_text,
        re.IGNORECASE,
    )
    if not matches:
        return None

    cli_device = next((item for item in matches[-1] if item), "").strip().upper()
    if not cli_device:
        return None

    if cli_device.endswith("X"):
        board_name = _extract_cli_board(syscfg_text)
        if board_name:
            board_device = _extract_device_from_board(board_name)
            if board_device and board_device.startswith(cli_device[:-1]):
                return board_device
    return cli_device


def extract_ti_device(syscfg_text: str) -> Tuple[str, Optional[str]]:
    """Extract TI device identifier and board name from a SysConfig file."""
    board_name = _extract_cli_board(syscfg_text)
    cli_device = _extract_cli_device(syscfg_text)
    if cli_device:
        return cli_device, board_name

    if board_name:
        device = _extract_device_from_board(board_name)
        if device:
            return device, board_name
        return board_name, board_name

    return "UNKNOWN_TI_DEVICE", None


def extract_rtos(syscfg_text: str) -> str:
    """Infer RTOS type from SysConfig text."""
    match = re.search(r'--rtos\s+["\']?([A-Za-z0-9\-_]+)["\']?', syscfg_text, re.IGNORECASE)
    if match:
        value = match.group(1).lower()
        if "free" in value:
            return "FreeRTOS"
        if "ti" in value:
            return "TI-RTOS"
        if "no" in value:
            return "NoRTOS"

    lowered = syscfg_text.lower()
    if "freertos" in lowered:
        return "FreeRTOS"
    if "ti_rtos" in lowered or "ti-rtos" in lowered:
        return "TI-RTOS"
    if "nortos" in lowered:
        return "NoRTOS"
    return "Unknown"


def extract_modules(syscfg_text: str) -> List[str]:
    """Extract module identifiers from SysConfig script text."""
    modules = set()
    for pattern in [
        r'addModule\(\s*"([^"]+)"',
        r"addModule\(\s*'([^']+)'",
        r'moduleName\s*:\s*"([^"]+)"',
        r"moduleName\s*:\s*'([^']+)'",
    ]:
        for item in re.findall(pattern, syscfg_text):
            cleaned = item.strip()
            if cleaned:
                modules.add(cleaned)
    return sorted(modules)


def _normalize_pull(raw_pull: str) -> Optional[str]:
    """Map TI pull setting to CubeMX-style pull value."""
    pull = raw_pull.strip().upper().replace("-", "_")
    mapping = {
        "PULL_UP": "GPIO_PULLUP",
        "PULLUP": "GPIO_PULLUP",
        "PULL_DOWN": "GPIO_PULLDOWN",
        "PULLDOWN": "GPIO_PULLDOWN",
        "NO_PULL": "GPIO_NOPULL",
        "NOPULL": "GPIO_NOPULL",
        "NONE": "GPIO_NOPULL",
    }
    return mapping.get(pull)


def _resolve_pin_key(pin_cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve pin name to format like PA8 / PB22."""
    assigned = str(pin_cfg.get("pin.$assign", "")).strip().upper()
    if re.match(r"^P[A-Z]\d+$", assigned):
        return assigned

    assigned_port = str(pin_cfg.get("assignedPort", "")).strip().upper()
    assigned_pin = str(pin_cfg.get("assignedPin", "")).strip()
    port_match = re.match(r"^PORT([A-Z])$", assigned_port)
    if port_match and assigned_pin.isdigit():
        return f"P{port_match.group(1)}{assigned_pin}"
    return None


def _resolve_signal(pin_cfg: Dict[str, Any]) -> str:
    """Infer GPIO signal type from pin configuration."""
    direction = str(pin_cfg.get("direction", "")).strip().upper()
    if direction == "OUTPUT":
        return "GPIO_Output"
    if direction == "INPUT":
        return "GPIO_Input"
    if "initialValue" in pin_cfg:
        return "GPIO_Output"
    if bool(pin_cfg.get("interruptEn", False)):
        return "GPIO_Input"
    return "GPIO_Output"


def extract_gpio_pins(syscfg_text: str) -> Dict[str, Dict[str, Any]]:
    """Extract GPIO pins using per-pin config instead of GPIO group names."""
    module_aliases = _extract_module_aliases(syscfg_text)
    instance_aliases: Dict[str, str] = {}
    pin_data: Dict[Tuple[str, str], Dict[str, Any]] = {}
    gpio_config: Dict[str, Dict[str, Any]] = {}

    for var, parent in re.findall(
        r'const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.addInstance\(',
        syscfg_text,
    ):
        instance_aliases[var] = parent

    for instance_var, index, prop, raw_value in re.findall(
        r'([A-Za-z_][A-Za-z0-9_]*)\.associatedPins\[(\d+)\]\.([A-Za-z0-9_.$]+)\s*=\s*([^;\n]+);',
        syscfg_text,
    ):
        parent_var = instance_aliases.get(instance_var, "")
        module_path = module_aliases.get(parent_var, "")
        if os.path.basename(module_path).upper() != "GPIO":
            continue

        key = (instance_var, index)
        pin_data.setdefault(key, {})
        pin_data[key][prop] = _parse_literal(raw_value)

    for pin_cfg in pin_data.values():
        pin_key = _resolve_pin_key(pin_cfg)
        if not pin_key:
            continue

        details: Dict[str, Any] = {"Signal": _resolve_signal(pin_cfg)}
        raw_name = str(pin_cfg.get("$name", "")).strip()
        if raw_name and not re.match(r"^PIN_\d+$", raw_name):
            details["Label"] = raw_name

        raw_pull = pin_cfg.get("internalResistor")
        if isinstance(raw_pull, str):
            pull = _normalize_pull(raw_pull)
            details["Pull"] = pull or "GPIO_NOPULL"
        else:
            details["Pull"] = "GPIO_NOPULL"

        if bool(pin_cfg.get("interruptEn", False)):
            details["interruptEn"] = True

        gpio_config[pin_key] = details

    return {pin: gpio_config[pin] for pin in sorted(gpio_config)}


def _sanitize_numeric(value: Any) -> Any:
    """Convert numeric-like strings to int/float when possible."""
    if isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return value

    text = value.strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", text):
        return float(text)
    return value


def _parse_bool(value: Any) -> Optional[bool]:
    """Normalize bool-like values found in SysConfig assignments."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None

    lowered = value.strip().lower()
    if lowered in {"true", "enable", "enabled", "1"}:
        return True
    if lowered in {"false", "disable", "disabled", "0"}:
        return False
    return None


def _parse_string_list(value: Any) -> Optional[List[str]]:
    """Parse list-like string values such as [\"RX\",\"TX\"] into string lists."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return None

    inner = text[1:-1].strip()
    if not inner:
        return []

    items: List[str] = []
    for raw_item in inner.split(","):
        token = raw_item.strip()
        if not token:
            continue
        if (
            (token.startswith('"') and token.endswith('"'))
            or (token.startswith("'") and token.endswith("'"))
        ) and len(token) >= 2:
            token = token[1:-1]
        items.append(token)
    return items


def _build_uart_config(instance_props: Dict[str, Any]) -> Dict[str, Any]:
    """Extract UART/LPUART/USART instance parameters from SysConfig values."""
    uart_cfg: Dict[str, Any] = {}

    scalar_map = {
        "targetBaudRate": "BaudRate",
        "wordLength": "WordLength",
        "parity": "Parity",
        "stopBits": "StopBits",
        "uartMode": "Mode",
        "direction": "Direction",
        "flowControl": "FlowControl",
        "rxFifoThreshold": "RXFifoThreshold",
        "txFifoThreshold": "TXFifoThreshold",
        "enabledDMARXTriggers": "DMARXTrigger",
        "enabledDMATXTriggers": "DMATXTrigger",
        "peripheral.$assign": "Instance",
    }
    for source_key, target_key in scalar_map.items():
        if source_key not in instance_props:
            continue
        value = instance_props[source_key]
        if source_key == "targetBaudRate":
            uart_cfg[target_key] = _sanitize_numeric(value)
        else:
            uart_cfg[target_key] = value

    # Keep TI UART default behavior explicit when WordLength is not configured.
    if not uart_cfg.get("WordLength"):
        uart_cfg["WordLength"] = "8_BITS"

    bool_map = {
        "enableFIFO": "FIFO",
        "enableDMARX": "DMA_RX",
        "enableDMATX": "DMA_TX",
        "enableInternalLoopback": "InternalLoopback",
        "enableManchester": "Manchester",
        "enableIrda": "IrDA",
    }
    for source_key, target_key in bool_map.items():
        if source_key not in instance_props:
            continue
        parsed = _parse_bool(instance_props[source_key])
        uart_cfg[target_key] = (
            instance_props[source_key] if parsed is None else parsed
        )

    interrupts = _parse_string_list(instance_props.get("enabledInterrupts"))
    if interrupts is not None:
        uart_cfg["Interrupts"] = interrupts

    pins: Dict[str, str] = {}
    pin_map = {
        "peripheral.rxPin.$assign": "RX",
        "peripheral.txPin.$assign": "TX",
        "peripheral.rtsPin.$assign": "RTS",
        "peripheral.ctsPin.$assign": "CTS",
    }
    for source_key, pin_label in pin_map.items():
        raw_pin = instance_props.get(source_key)
        if isinstance(raw_pin, str) and raw_pin.strip():
            pins[pin_label] = raw_pin.strip()
    if pins:
        uart_cfg["Pins"] = pins

    return uart_cfg


def _is_uart_type(peripheral_type: str) -> bool:
    """Check if module type should use UART parser."""
    return (
        peripheral_type in {"UART", "LPUART", "USART", "UARTLIN"}
        or peripheral_type.startswith("UART")
        or peripheral_type.startswith("LPUART")
        or peripheral_type.startswith("USART")
    )


def _build_pwm_config(instance_props: Dict[str, Any]) -> Dict[str, Any]:
    """Extract PWM instance parameters aligned to STM32 TIM core fields."""
    pwm_cfg: Dict[str, Any] = {}

    scalar_map = {
        "pwmMode": "Mode",
        "timerCount": "Period",
        "clockDivider": "Prescaler",
        "clockPrescale": "ClockPrescaler",
    }
    for source_key, target_key in scalar_map.items():
        if source_key not in instance_props:
            continue

        value = instance_props[source_key]
        pwm_cfg[target_key] = _sanitize_numeric(value)

    channel_data: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for prop, raw_value in instance_props.items():
        channel_match = re.match(r"PWM_CHANNEL_(\d+)\.([A-Za-z0-9_.$]+)$", prop)
        if not channel_match:
            continue
        channel_index, channel_prop = channel_match.groups()
        channel_data[channel_index][channel_prop] = raw_value

    channels: Dict[str, Dict[str, Any]] = {}
    for channel_index in sorted(channel_data, key=lambda item: int(item)):
        channel_props = channel_data[channel_index]
        channel_cfg: Dict[str, Any] = {"PWM": True}

        if "dutyCycle" in channel_props:
            channel_cfg["DutyCycle"] = _sanitize_numeric(channel_props["dutyCycle"])

        channels[f"PWM_CHANNEL_{channel_index}"] = channel_cfg

    if channels:
        pwm_cfg["Channels"] = channels

    return pwm_cfg


def extract_peripherals(syscfg_text: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Extract peripheral instances and selected module parameters from SysConfig."""
    module_aliases = _extract_module_aliases(syscfg_text)
    instance_aliases: Dict[str, str] = {}
    instance_props: Dict[str, Dict[str, Any]] = defaultdict(dict)
    display_names: Dict[str, str] = {}
    peripherals: Dict[str, Dict[str, Dict[str, Any]]] = {}
    modules_with_instances = set()
    module_name_map = {
        "ADC12": "ADC",
        "CANFD": "MCAN",
        "FDCAN": "MCAN",
        "I2CSMBUS": "I2C",
    }
    module_presence_whitelist = {
        "ADC",
        "DMA",
        "I2C",
        "LPUART",
        "MCAN",
        "PWM",
        "SPI",
        "TIMER",
        "UART",
        "UARTLIN",
        "USART",
    }

    for var, parent in re.findall(
        r'const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.addInstance\(',
        syscfg_text,
    ):
        instance_aliases[var] = parent
        modules_with_instances.add(parent)

    for var, name in re.findall(
        r'([A-Za-z_][A-Za-z0-9_]*)\.\$name\s*=\s*["\']([^"\']+)["\']',
        syscfg_text,
    ):
        display_names[var] = name

    for instance_var, prop, raw_value in re.findall(
        r'([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_.$]+)\s*=\s*([^;\n]+);',
        syscfg_text,
    ):
        if instance_var not in instance_aliases:
            continue
        instance_props[instance_var][prop] = _parse_literal(raw_value)

    for instance_var, parent_var in instance_aliases.items():
        module_path = module_aliases.get(parent_var, "")
        peripheral_type = os.path.basename(module_path).upper()
        peripheral_type = module_name_map.get(peripheral_type, peripheral_type)
        if not peripheral_type:
            continue
        if peripheral_type == "GPIO":
            continue
        instance_name = display_names.get(instance_var, instance_var)
        peripherals.setdefault(peripheral_type, {})
        if peripheral_type == "PWM":
            peripherals[peripheral_type][instance_name] = _build_pwm_config(
                instance_props.get(instance_var, {})
            )
        elif _is_uart_type(peripheral_type):
            peripherals[peripheral_type][instance_name] = _build_uart_config(
                instance_props.get(instance_var, {})
            )
        else:
            peripherals[peripheral_type][instance_name] = {}

    for module_var, module_path in module_aliases.items():
        if module_var in modules_with_instances:
            continue

        peripheral_type = os.path.basename(module_path).upper()
        peripheral_type = module_name_map.get(peripheral_type, peripheral_type)
        if peripheral_type not in module_presence_whitelist:
            continue

        fallback_name = display_names.get(module_var, module_var)
        peripherals.setdefault(peripheral_type, {})
        peripherals[peripheral_type].setdefault(fallback_name, {})

    return peripherals


def parse_syscfg_text(syscfg_text: str, syscfg_file: Optional[str] = None) -> Dict[str, Any]:
    """Parse SysConfig text and return normalized LibXR configuration."""
    config = ConfigurationManager()
    mcu_type, _ = extract_ti_device(syscfg_text)
    config.mcu_config["Type"] = mcu_type

    rtos = extract_rtos(syscfg_text)
    if rtos == "FreeRTOS":
        config.freertos_config["Enabled"] = True

    for pin, cfg in extract_gpio_pins(syscfg_text).items():
        config.gpio_pins[pin] = cfg

    for p_type, instances in extract_peripherals(syscfg_text).items():
        config.peripherals[p_type].update(instances)

    return config.clean_structure()


def parse_syscfg_file(syscfg_path: str) -> Optional[Dict[str, Any]]:
    """Parse a .syscfg file and return normalized LibXR configuration."""
    try:
        with open(syscfg_path, "r", encoding="utf-8") as f:
            return parse_syscfg_text(f.read(), syscfg_path)
    except (UnicodeDecodeError, OSError) as err:
        logging.error(f"File processing failed: {err}")
        return None


def save_to_yaml(data: Dict[str, Any], output_path: str = "parsed_syscfg.yaml") -> bool:
    """Serialize configuration data to YAML with error handling."""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                indent=2,
            )
        logging.info(f"Configuration exported to: {output_path}")
        return True
    except (OSError, yaml.YAMLError) as err:
        logging.error(f"YAML export failed: {err}")
        return False


def print_summary(data: Dict[str, Any]) -> None:
    """Generate human-readable configuration summary."""
    print("\n===== [Configuration Summary] =====")

    mcu = data.get("Mcu", {})
    print(f"\nMCU: {mcu.get('Family', 'Unknown')} {mcu.get('Type', '')}")

    gpio = data.get("GPIO", {})
    print(f"\nGPIO ({len(gpio)} pins):")
    print(
        f"  Outputs: {sum(1 for c in gpio.values() if c.get('Signal') == 'GPIO_Output')}"
    )
    print(
        f"  Inputs: {sum(1 for c in gpio.values() if c.get('Signal') == 'GPIO_Input')}"
    )
    print(f"  External Interrupts: {sum(1 for c in gpio.values() if c.get('GPXTI'))}")

    print("\nActive Peripherals:")
    for p_type, group in data.get("Peripherals", {}).items():
        print(f"  {p_type}: {len(group)} instance(s)")
        for name in group:
            print(f"    {name}: ")


def main() -> None:
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(
        description="TI SysConfig Parser v2.0",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-d", "--directory", required=True, help="Input directory containing .syscfg files"
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Custom output YAML file path (default: <input_file>.yaml)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not os.path.isdir(args.directory):
        logging.error(f"Invalid input directory: {args.directory}")
        sys.exit(1)

    syscfg_files = sorted(f for f in os.listdir(args.directory) if f.endswith(".syscfg"))
    if not syscfg_files:
        logging.error("No .syscfg files found in target directory")
        sys.exit(1)

    for syscfg_file in syscfg_files:
        input_path = os.path.join(args.directory, syscfg_file)
        logging.info(f"Processing {syscfg_file}...")

        config_data = parse_syscfg_file(input_path)
        if not config_data:
            continue

        output_path = args.output or os.path.splitext(input_path)[0] + ".yaml"
        if save_to_yaml(config_data, output_path):
            print_summary(config_data)


if __name__ == "__main__":
    main()
