#!/usr/bin/env python3

import argparse
import logging
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def _friendly_path_name(path: str) -> str:
    """Return a human-friendly name for a path."""
    abs_path = os.path.abspath(path)
    base = os.path.basename(abs_path.rstrip(os.sep))
    return base or abs_path


def find_syscfg_file(directory: str):
    """Return the first .syscfg file path in the specified directory, or None."""
    for filename in sorted(os.listdir(directory)):
        if filename.endswith(".syscfg"):
            return os.path.join(directory, filename)
    return None


def _extract_module_aliases(syscfg_text: str) -> Dict[str, str]:
    """Extract variable -> module path mappings from addModule calls."""
    aliases: Dict[str, str] = {}
    for var, module_path in re.findall(
        r'const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*scripting\.addModule\(\s*["\']([^"\']+)["\']',
        syscfg_text,
    ):
        aliases[var] = module_path
    return aliases


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


def _normalize_pull(raw_pull: str) -> Optional[str]:
    """Map TI pull setting to CubeMX-style pull value."""
    pull = raw_pull.strip().upper()
    pull = pull.replace("-", "_")
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

        details: Dict[str, Any] = {
            "Signal": _resolve_signal(pin_cfg),
        }
        raw_name = str(pin_cfg.get("$name", "")).strip()
        if raw_name and not re.match(r"^PIN_\d+$", raw_name):
            details["Label"] = raw_name

        raw_pull = pin_cfg.get("internalResistor")
        if isinstance(raw_pull, str):
            pull = _normalize_pull(raw_pull)
            if pull:
                details["Pull"] = pull

        gpio_config[pin_key] = details

    return {pin: gpio_config[pin] for pin in sorted(gpio_config)}


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
    match = re.search(r'(MSPM0[A-Za-z0-9]+)', board_name, re.IGNORECASE)
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


def extract_peripherals(syscfg_text: str) -> Dict[str, Dict[str, Dict[str, bool]]]:
    """Extract minimal peripheral instances from SysConfig script text."""
    module_aliases = _extract_module_aliases(syscfg_text)
    instance_aliases: Dict[str, str] = {}
    display_names: Dict[str, str] = {}
    peripherals: Dict[str, Dict[str, Dict[str, bool]]] = {}
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

    for instance_var, parent_var in instance_aliases.items():
        module_path = module_aliases.get(parent_var, "")
        peripheral_type = os.path.basename(module_path).upper()
        peripheral_type = module_name_map.get(peripheral_type, peripheral_type)
        if not peripheral_type:
            continue
        if peripheral_type == "GPIO":
            # GPIO is represented in top-level "GPIO" using per-pin shape.
            continue

        instance_name = display_names.get(instance_var, instance_var)
        peripherals.setdefault(peripheral_type, {})
        peripherals[peripheral_type][instance_name] = {"Enabled": True}

    # Some SysConfig modules (for example DMA) are configured without addInstance().
    # For these, keep existence-only entries so downstream can see they are enabled.
    for module_var, module_path in module_aliases.items():
        if module_var in modules_with_instances:
            continue

        peripheral_type = os.path.basename(module_path).upper()
        peripheral_type = module_name_map.get(peripheral_type, peripheral_type)
        if peripheral_type not in module_presence_whitelist:
            continue

        fallback_name = display_names.get(module_var, module_var)
        peripherals.setdefault(peripheral_type, {})
        peripherals[peripheral_type].setdefault(fallback_name, {"Enabled": True})

    return peripherals


def build_yaml_config(syscfg_file: str, terminal_source: str, syscfg_text: str) -> Dict:
    """Build a baseline .config.yaml structure from SysConfig metadata."""
    mcu_type, board_name = extract_ti_device(syscfg_text)
    rtos = extract_rtos(syscfg_text)
    modules = extract_modules(syscfg_text)
    gpio = extract_gpio_pins(syscfg_text)
    peripherals = extract_peripherals(syscfg_text)

    config = {
        "Mcu": {
            "Family": "TI",
            "Type": mcu_type,
            "Source": "TI SysConfig",
        },
        "GPIO": gpio,
        "Peripherals": peripherals,
        "Timebase": {
            "Source": "SysTick",
        },
        "SysConfig": {
            "File": os.path.basename(syscfg_file),
            "RTOS": rtos,
            "Modules": modules,
        },
    }
    if board_name:
        config["SysConfig"]["Board"] = board_name

    if terminal_source:
        config["terminal_source"] = terminal_source

    return config


def render_template_command(template: str, values: Dict[str, str]) -> str:
    """Render shell command template with known placeholders."""
    try:
        return template.format(**values)
    except KeyError as exc:
        missing = str(exc).strip("'")
        raise ValueError(f"Unknown placeholder in command template: {missing}") from exc


def run_command(cmd: str):
    """Run shell command and exit on error."""
    logging.info(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        logging.error(f"Command failed with exit code {result.returncode}: {cmd}")
        sys.exit(result.returncode)


def main():
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(description="Automate TI SysConfig project setup")
    parser.add_argument("-d", "--directory", required=True, help="TI SysConfig project directory")
    parser.add_argument("-o", "--output", default=".config.yaml", help="Output YAML path (default: .config.yaml)")
    parser.add_argument("-t", "--terminal", default="", help="Optional terminal device source")
    parser.add_argument("--xrobot", action="store_true", help="Reserved flag, kept for CLI compatibility")
    parser.add_argument("--commit", default="", help="Specify locked LibXR commit hash (reserved)")
    parser.add_argument("--git-source", default="auto",
                        help="Git source base URL or full repo URL, or 'auto'/'github' (reserved)")
    parser.add_argument("--git-mirrors", default="",
                        help="Comma-separated mirror base/repo URLs (reserved)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output YAML")
    parser.add_argument("--post-cmd", default="",
                        help="Optional command after YAML generation. Placeholders: "
                             "{project_dir}, {syscfg_file}, {yaml_output}")
    parser.add_argument("--dry-run", action="store_true", help="Only inspect project and print summary")

    args = parser.parse_args()

    project_dir = args.directory.rstrip("/")
    if not os.path.isdir(project_dir):
        logging.error(f"Directory {_friendly_path_name(project_dir)} does not exist")
        sys.exit(1)

    syscfg_file = find_syscfg_file(project_dir)
    if not syscfg_file:
        logging.error(f"No .syscfg file found in {_friendly_path_name(project_dir)}")
        sys.exit(1)

    logging.info(f"Found .syscfg file: {syscfg_file}")

    with open(syscfg_file, "r", encoding="utf-8") as f:
        syscfg_text = f.read()

    config = build_yaml_config(syscfg_file, args.terminal, syscfg_text)
    output_yaml = args.output
    if not os.path.isabs(output_yaml):
        output_yaml = os.path.join(project_dir, output_yaml)
    output_yaml = os.path.abspath(output_yaml)

    logging.info(f"Detected TI device: {config['Mcu']['Type']}")
    logging.info(f"Detected RTOS: {config['SysConfig']['RTOS']}")
    logging.info(f"Detected modules: {len(config['SysConfig']['Modules'])}")
    logging.info(f"YAML output: {output_yaml}")

    if args.dry_run:
        logging.info("Dry-run mode: no file was written.")
        sys.exit(0)

    if os.path.exists(output_yaml) and not args.force:
        logging.error(f"Output already exists: {output_yaml}. Use --force to overwrite.")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_yaml), exist_ok=True)
    with open(output_yaml, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
    logging.info("Generated TI baseline YAML successfully.")

    if args.post_cmd:
        values = {
            "project_dir": project_dir,
            "syscfg_file": syscfg_file,
            "yaml_output": output_yaml,
        }
        try:
            command = render_template_command(args.post_cmd, values)
        except ValueError as err:
            logging.error(str(err))
            sys.exit(1)
        run_command(command)

    logging.info("[Pass] TI SysConfig entry workflow completed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
