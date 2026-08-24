#!/usr/bin/env python3
"""Generate LibXR MSPM0 application code from TI SysConfig YAML."""

import argparse
import copy
import logging
import os
import re
import sys
from typing import Any, Dict, List, Tuple

import yaml

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


DEFAULT_SETTINGS: Dict[str, Any] = {
    "SYSTEM": "None",
    "disabled_peripherals": [],
    "GPIO": {},
    "UART": {},
    "I2C": {},
    "SPI": {},
    "ADC": {},
    "PWM": {},
    "MCAN": {},
    "device_aliases": {},
}

DeviceEntry = Tuple[str, str, List[str]]


def _deep_merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_configuration(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as source:
        data = yaml.safe_load(source)
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be a mapping")
    for section in ("Mcu", "GPIO", "Peripherals"):
        if section not in data:
            raise ValueError(f"Missing required section: {section}")

    mcu = data.get("Mcu", {})
    family = str(mcu.get("Platform", mcu.get("Family", ""))).upper()
    mcu_type = str(mcu.get("Type", "")).upper()
    if family not in {"TI", "MSPM0"} or not mcu_type.startswith("MSPM0"):
        raise ValueError("GeneratorCodeMSPM0 requires an MSPM0 configuration")
    return data


def load_settings(path: str) -> Dict[str, Any]:
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if not path:
        return settings
    with open(path, "r", encoding="utf-8") as source:
        external = yaml.safe_load(source) or {}
    if not isinstance(external, dict):
        raise ValueError("LibXR configuration root must be a mapping")
    return _deep_merge(settings, external)


def _identifier(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).lower()
    result = re.sub(r"_+", "_", result).strip("_")
    if result and result[0].isdigit():
        result = "device_" + result
    return result or "device"


def _unique_identifier(value: str, used: set) -> str:
    base = _identifier(value)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _macro(value: Any) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "")).upper()
    return re.sub(r"_+", "_", result).strip("_")


def _float_literal(value: Any) -> str:
    number = float(value)
    text = f"{number:.6g}"
    if "." not in text and "e" not in text.lower():
        text += ".0"
    return text + "f"


def _settings_for(settings: Dict[str, Any], group: str, name: str) -> Dict[str, Any]:
    section = settings.setdefault(group, {})
    key = _identifier(name)
    configured = section.setdefault(key, {})
    if not isinstance(configured, dict):
        configured = {}
        section[key] = configured
    return configured


def _aliases(name: str, variable: str, settings: Dict[str, Any]) -> List[str]:
    configured = settings.get("device_aliases", {}).get(name)
    if configured is None:
        configured = settings.get("device_aliases", {}).get(variable)
    if isinstance(configured, dict):
        aliases = configured.get("aliases", [name])
    elif isinstance(configured, list):
        aliases = configured
    elif isinstance(configured, str):
        aliases = [configured]
    else:
        aliases = [name]
    result = [str(alias) for alias in aliases if str(alias)]
    return result or [name]


def _disabled(settings: Dict[str, Any]) -> set:
    configured = settings.get("disabled_peripherals", [])
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list):
        return set()
    return {str(name).lower() for name in configured if str(name)}


def _preserve_user_block(existing: str, number: int) -> str:
    pattern = re.compile(
        r"/\* User Code Begin {} \*/(.*?)/\* User Code End {} \*/".format(
            number, number
        ),
        re.DOTALL,
    )
    match = pattern.search(existing)
    if not match:
        return ""
    return match.group(1).strip("\r\n")


def _gpio_direction(config: Dict[str, Any], settings: Dict[str, Any]) -> str:
    configured = str(settings.get("direction", "")).upper()
    supported = {
        "INPUT",
        "OUTPUT_PUSH_PULL",
        "OUTPUT_OPEN_DRAIN",
        "RISING_INTERRUPT",
        "FALL_INTERRUPT",
        "BOTH_INTERRUPT",
    }
    if configured in supported:
        return configured
    if config.get("Signal") == "GPIO_Output":
        return "OUTPUT_PUSH_PULL"
    return "INPUT"


def _gpio_pull(config: Dict[str, Any], settings: Dict[str, Any]) -> str:
    configured = str(settings.get("pull", config.get("Pull", "NONE"))).upper()
    mapping = {
        "GPIO_PULLUP": "UP",
        "PULL_UP": "UP",
        "UP": "UP",
        "GPIO_PULLDOWN": "DOWN",
        "PULL_DOWN": "DOWN",
        "DOWN": "DOWN",
        "GPIO_NOPULL": "NONE",
        "NO_PULL": "NONE",
        "NONE": "NONE",
    }
    return mapping.get(configured, "NONE")


def _generate_gpio(
    gpio: Dict[str, Any], settings: Dict[str, Any], disabled: set, used: set
) -> Tuple[List[str], List[DeviceEntry]]:
    code: List[str] = []
    devices: List[DeviceEntry] = []
    for pin, config in gpio.items():
        if not isinstance(config, dict):
            continue
        display_name = str(config.get("Label", config.get("Name", pin)))
        base_variable = _identifier(display_name)
        if (
            pin.lower() in disabled
            or display_name.lower() in disabled
            or base_variable in disabled
        ):
            continue
        pin_macro = _macro(config.get("Macro"))
        group_macro = _macro(config.get("Group"))
        if not pin_macro or not group_macro:
            logging.warning("Skipping GPIO %s: missing SysConfig macro metadata", pin)
            continue
        variable = _unique_identifier(display_name, used)
        cfg = _settings_for(settings, "GPIO", display_name)
        code.extend(
            [
                f"  static LibXR::MSPM0GPIO {variable}(",
                f"      {group_macro}_PORT, {pin_macro}_PIN, {pin_macro}_IOMUX);",
                (
                    f"  (void){variable}.SetConfig({{LibXR::GPIO::Direction::"
                    f"{_gpio_direction(config, cfg)}, LibXR::GPIO::Pull::"
                    f"{_gpio_pull(config, cfg)}}});"
                ),
            ]
        )
        devices.append((variable, "GPIO", _aliases(display_name, variable, settings)))
    return code, devices


def _spi_clock_polarity(config: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    value = str(cfg.setdefault("clock_polarity", config.get("CLKPolarity", "LOW"))).upper()
    if "HIGH" in value or "POL1" in value:
        return "HIGH"
    return "LOW"


def _spi_clock_phase(config: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    value = str(cfg.setdefault("clock_phase", config.get("CLKPhase", "EDGE_1"))).upper()
    if value in {"SECOND_EDGE", "EDGE_2", "EDGE2"} or "PHA1" in value:
        return "EDGE_2"
    return "EDGE_1"


def _spi_prescaler(cfg: Dict[str, Any]) -> str:
    value = str(cfg.setdefault("prescaler", "DIV_4")).upper()
    if value.isdigit():
        value = "DIV_" + value
    supported = {f"DIV_{divider}" for divider in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512)}
    return value if value in supported else "DIV_4"


def _generate_peripherals(
    peripherals: Dict[str, Any], settings: Dict[str, Any], disabled: set, used: set
) -> Tuple[List[str], List[str], List[DeviceEntry]]:
    resources: List[str] = []
    code: List[str] = []
    devices: List[DeviceEntry] = []
    generated_types = set()

    for instance, config in peripherals.get("UART", {}).items():
        base_variable = _identifier(instance)
        if instance.lower() in disabled or base_variable in disabled:
            continue
        variable = _unique_identifier(instance, used)
        cfg = _settings_for(settings, "UART", instance)
        rx_size = int(cfg.setdefault("rx_buffer_size", 256))
        tx_queue_size = int(cfg.setdefault("tx_queue_size", 16))
        tx_buffer_size = int(cfg.setdefault("tx_buffer_size", 512))
        resources.append(f"static uint8_t {variable}_rx_stage_buffer[{rx_size}];")
        code.extend(
            [
                f"  static LibXR::MSPM0UART {variable}(",
                f"      MSPM0_UART_INIT({_macro(instance)}, {variable}_rx_stage_buffer,",
                f"                       sizeof({variable}_rx_stage_buffer), {tx_queue_size},",
                f"                       {tx_buffer_size}));",
            ]
        )
        devices.append((variable, "UART", _aliases(instance, variable, settings)))
        generated_types.add("UART")

    for instance, config in peripherals.get("I2C", {}).items():
        base_variable = _identifier(instance)
        if instance.lower() in disabled or base_variable in disabled:
            continue
        variable = _unique_identifier(instance, used)
        cfg = _settings_for(settings, "I2C", instance)
        stage_size = int(cfg.setdefault("stage_buffer_size", 256))
        dma_min_size = int(cfg.setdefault("dma_min_size", 8))
        resources.append(f"static uint8_t {variable}_stage_buffer[{stage_size}];")
        code.extend(
            [
                f"  static LibXR::MSPM0I2C {variable}(",
                f"      MSPM0_I2C_INIT({_macro(instance)}, {variable}_stage_buffer,",
                f"                      sizeof({variable}_stage_buffer), {dma_min_size}));",
            ]
        )
        devices.append((variable, "I2C", _aliases(instance, variable, settings)))
        generated_types.add("I2C")

    for instance, config in peripherals.get("SPI", {}).items():
        base_variable = _identifier(instance)
        if instance.lower() in disabled or base_variable in disabled:
            continue
        if not isinstance(config, dict):
            continue
        cfg = _settings_for(settings, "SPI", instance)
        dma = config.get("dma", {}) if isinstance(config.get("dma"), dict) else {}
        dma_rx = cfg.get("dma_rx_name") or dma.get("dma_rx", {}).get("stream")
        dma_tx = cfg.get("dma_tx_name") or dma.get("dma_tx", {}).get("stream")
        if not dma_rx or not dma_tx:
            logging.warning(
                "Skipping SPI %s: MSPM0SPI requires both RX and TX SysConfig DMA channels",
                instance,
            )
            continue
        variable = _unique_identifier(instance, used)
        buffer_size = int(cfg.setdefault("buffer_size", 256))
        dma_min_size = int(cfg.setdefault("dma_min_size", 3))
        resources.extend(
            [
                f"static uint8_t {variable}_rx_buffer[{buffer_size}];",
                f"static uint8_t {variable}_tx_buffer[{buffer_size}];",
            ]
        )
        code.extend(
            [
                f"  static LibXR::MSPM0SPI {variable}(",
                f"      MSPM0_SPI_INIT({_macro(instance)}, {_macro(dma_rx)}, {_macro(dma_tx)},",
                f"                      {variable}_rx_buffer, sizeof({variable}_rx_buffer),",
                f"                      {variable}_tx_buffer, sizeof({variable}_tx_buffer),",
                f"                      {dma_min_size}),",
                "      {LibXR::SPI::ClockPolarity::%s, LibXR::SPI::ClockPhase::%s,"
                % (_spi_clock_polarity(config, cfg), _spi_clock_phase(config, cfg)),
                f"       LibXR::SPI::Prescaler::{_spi_prescaler(cfg)}, false}});",
            ]
        )
        devices.append((variable, "SPI", _aliases(instance, variable, settings)))
        generated_types.add("SPI")

    for instance, config in peripherals.get("ADC", {}).items():
        base_variable = _identifier(instance)
        if instance.lower() in disabled or base_variable in disabled:
            continue
        if not isinstance(config, dict):
            continue
        memory_indices = config.get("MemoryIndices", [])
        if not isinstance(memory_indices, list) or not memory_indices:
            logging.warning("Skipping ADC %s: no ADC memory indices were parsed", instance)
            continue
        variable = _unique_identifier(instance, used)
        memory_indices = [int(index) for index in memory_indices]
        cfg = _settings_for(settings, "ADC", instance)
        filter_size = int(cfg.setdefault("filter_size", 4))
        sample_count = len(memory_indices) * filter_size
        resources.append(
            f"alignas(LibXR::CACHE_LINE_SIZE) static uint16_t {variable}_buffer[{sample_count}];"
        )
        mem_list = ", ".join(f"DL_ADC12_MEM_IDX_{index}" for index in memory_indices)
        code.extend(
            [
                f"  static LibXR::MSPM0ADC {variable}(",
                f"      MSPM0_ADC_INIT({_macro(instance)}, {memory_indices[0]}),",
                f"      LibXR::RawData{{{variable}_buffer, sizeof({variable}_buffer)}},",
                f"      {{{mem_list}}});",
            ]
        )
        for channel_position, memory_index in enumerate(memory_indices):
            channel_name = f"{instance}_MEM{memory_index}"
            channel_variable = f"{variable}_channel_{channel_position}"
            code.append(
                f"  auto& {channel_variable} = {variable}.GetChannel({channel_position});"
            )
            devices.append(
                (
                    channel_variable,
                    "ADC",
                    _aliases(channel_name, channel_variable, settings),
                )
            )
        generated_types.add("ADC")

    for instance, config in peripherals.get("PWM", {}).items():
        if not isinstance(config, dict):
            continue
        channels = config.get("Channels", {})
        if not isinstance(channels, dict):
            continue
        instance_cfg = _settings_for(settings, "PWM", instance)
        frequency = int(instance_cfg.setdefault("frequency_hz", 1000))
        for channel_name, channel_config in channels.items():
            match = re.search(r"(\d+)$", channel_name)
            if not match:
                logging.warning("Skipping PWM %s/%s: invalid channel name", instance, channel_name)
                continue
            channel_index = int(match.group(1))
            display_name = str(
                channel_config.get("Name", f"{instance}_C{channel_index}")
                if isinstance(channel_config, dict)
                else f"{instance}_C{channel_index}"
            )
            base_variable = _identifier(display_name)
            if display_name.lower() in disabled or base_variable in disabled:
                continue
            variable = _unique_identifier(display_name, used)
            channel_cfg = _settings_for(settings, "PWM", display_name)
            channel_frequency = int(channel_cfg.setdefault("frequency_hz", frequency))
            duty_cycle = channel_cfg.setdefault(
                "duty_cycle",
                float(channel_config.get("DutyCycle", 0)) / 100.0
                if isinstance(channel_config, dict)
                else 0.0,
            )
            code.extend(
                [
                    f"  static LibXR::MSPM0PWM {variable}(",
                    f"      MSPM0_PWM_INIT({_macro(instance)}, GPIO_{_macro(instance)}_C{channel_index}));",
                    f"  (void){variable}.SetConfig({{{channel_frequency}U}});",
                    f"  (void){variable}.SetDutyCycle({_float_literal(duty_cycle)});",
                    f"  (void){variable}.Enable();",
                ]
            )
            devices.append((variable, "PWM", _aliases(display_name, variable, settings)))
            generated_types.add("PWM")

    for instance, config in peripherals.get("MCAN", {}).items():
        base_variable = _identifier(instance)
        if instance.lower() in disabled or base_variable in disabled:
            continue
        if not isinstance(config, dict):
            continue
        cfg = _settings_for(settings, "MCAN", instance)
        interrupt_enabled = bool(cfg.get("force_interrupt", config.get("Interrupt", False)))
        if not interrupt_enabled:
            logging.warning(
                "Skipping MCAN %s: enable its SysConfig interrupt before generating MSPM0CAN",
                instance,
            )
            continue
        variable = _unique_identifier(instance, used)
        tx_pool_size = int(cfg.setdefault("tx_pool_size", 8))
        code.append(
            f"  static LibXR::MSPM0CAN {variable}(MSPM0_CAN_INIT({_macro(instance)}, {tx_pool_size}));"
        )
        devices.append((variable, "CAN", _aliases(instance, variable, settings)))
        generated_types.add("MCAN")

    ignored_types = {"DMA", "TIMER"}
    for peripheral_type, instances in peripherals.items():
        if peripheral_type in generated_types or peripheral_type in ignored_types:
            continue
        if peripheral_type in {"UART", "I2C", "SPI", "ADC", "PWM", "MCAN"}:
            continue
        if isinstance(instances, dict) and instances:
            logging.warning(
                "No MSPM0 code generator is available for %s; keeping it in YAML only",
                peripheral_type,
            )

    return resources, code, devices


def _hardware_container(devices: List[DeviceEntry]) -> List[str]:
    if not devices:
        return ["  static LibXR::HardwareContainer peripherals{};"]
    entries = []
    for variable, interface, aliases in devices:
        alias_text = ", ".join(
            '"{}"'.format(alias.replace('"', '\\"')) for alias in aliases
        )
        entries.append(
            f"      LibXR::Entry<LibXR::{interface}>{{{variable}, {{{alias_text}}}}}"
        )
    return ["  static LibXR::HardwareContainer peripherals(", ",\n".join(entries) + ");"]


def generate_code(
    project: Dict[str, Any],
    settings: Dict[str, Any],
    use_xrobot: bool = False,
    use_hw_cntr: bool = False,
    existing: str = "",
) -> str:
    use_hw_cntr = use_hw_cntr or use_xrobot
    disabled = _disabled(settings)
    used_identifiers = {"timebase", "peripherals"}
    gpio_code, gpio_devices = _generate_gpio(
        project.get("GPIO", {}), settings, disabled, used_identifiers
    )
    resources, peripheral_code, peripheral_devices = _generate_peripherals(
        project.get("Peripherals", {}), settings, disabled, used_identifiers
    )
    devices = gpio_devices + peripheral_devices

    peripheral_groups = project.get("Peripherals", {})
    headers = [
        '#include "app_main.h"',
        "#include <cstdint>",
        '#include "mspm0_timebase.hpp"',
        '#include "ti_msp_dl_config.h"',
    ]
    if gpio_code:
        headers.append('#include "mspm0_gpio.hpp"')
    generated_interfaces = {interface for _, interface, _ in peripheral_devices}
    include_map = {
        "UART": ("UART", "mspm0_uart.hpp"),
        "I2C": ("I2C", "mspm0_i2c.hpp"),
        "SPI": ("SPI", "mspm0_spi.hpp"),
        "ADC": ("ADC", "mspm0_adc.hpp"),
        "PWM": ("PWM", "mspm0_pwm.hpp"),
        "MCAN": ("CAN", "mspm0_can.hpp"),
    }
    for group, (interface, header) in include_map.items():
        if peripheral_groups.get(group) and interface in generated_interfaces:
            headers.append(f'#include "{header}"')
    if use_hw_cntr:
        headers.append('#include "app_framework.hpp"')
    if use_xrobot:
        headers.append('#include "xrobot_main.hpp"')
    else:
        headers.append('#include "thread.hpp"')

    user1 = _preserve_user_block(existing, 1)
    user2 = _preserve_user_block(existing, 2)
    user3 = _preserve_user_block(existing, 3)

    lines = headers + ["", "/* User Code Begin 1 */"]
    if user1:
        lines.append(user1)
    lines.extend(["/* User Code End 1 */", ""])
    lines.extend(resources)
    if resources:
        lines.append("")
    lines.extend(
        [
            'extern "C" void app_main(void)',
            "{",
            "  /* User Code Begin 2 */",
        ]
    )
    if user2:
        lines.append(user2)
    lines.extend(
        [
            "  /* User Code End 2 */",
            "  static LibXR::MSPM0Timebase timebase;",
            "  UNUSED(timebase);",
        ]
    )
    lines.extend(gpio_code)
    lines.extend(peripheral_code)
    if use_hw_cntr:
        lines.extend(_hardware_container(devices))
    lines.append("  /* User Code Begin 3 */")
    if user3:
        lines.append(user3)
    elif use_xrobot:
        lines.append("  XRobotMain(peripherals);")
    else:
        lines.extend(
            [
                "  while (true)",
                "  {",
                "    LibXR::Thread::Sleep(UINT32_MAX);",
                "  }",
            ]
        )
    lines.extend(["  /* User Code End 3 */", "}", ""])
    return "\n".join(lines)


def generate_header(output_dir: str) -> None:
    content = """#pragma once

#ifdef __cplusplus
extern "C" {
#endif

void app_main(void);

#ifdef __cplusplus
}
#endif
"""
    with open(os.path.join(output_dir, "app_main.h"), "w", encoding="utf-8") as target:
        target.write(content)


def write_outputs(
    project: Dict[str, Any],
    output: str,
    config_source: str = "",
    use_xrobot: bool = False,
    use_hw_cntr: bool = False,
) -> None:
    settings = load_settings(config_source)
    output = os.path.abspath(output)
    output_dir = os.path.dirname(output)
    os.makedirs(output_dir, exist_ok=True)
    existing = ""
    if os.path.isfile(output):
        with open(output, "r", encoding="utf-8") as source:
            existing = source.read()
    with open(output, "w", encoding="utf-8", newline="\n") as target:
        target.write(generate_code(project, settings, use_xrobot, use_hw_cntr, existing))
    generate_header(output_dir)
    with open(
        os.path.join(output_dir, "libxr_config.yaml"),
        "w",
        encoding="utf-8",
        newline="\n",
    ) as target:
        yaml.safe_dump(settings, target, allow_unicode=True, sort_keys=False)


def main() -> None:
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()
    parser = argparse.ArgumentParser(
        description="Generate LibXR code for a TI MSPM0 project"
    )
    parser.add_argument("-i", "--input", required=True, help="Parsed MSPM0 YAML")
    parser.add_argument("-o", "--output", required=True, help="Output app_main.cpp")
    parser.add_argument("--xrobot", action="store_true", help="Generate XRobot integration")
    parser.add_argument(
        "--hw-cntr", action="store_true", help="Generate HardwareContainer"
    )
    parser.add_argument("--libxr-config", default="", help="LibXR settings YAML")
    args = parser.parse_args()

    try:
        project = load_configuration(args.input)
        write_outputs(
            project,
            args.output,
            args.libxr_config,
            use_xrobot=args.xrobot,
            use_hw_cntr=args.hw_cntr,
        )
    except (OSError, ValueError, yaml.YAMLError) as error:
        logging.error("MSPM0 code generation failed: %s", error)
        sys.exit(1)

    logging.info("Successfully generated: %s", os.path.abspath(args.output))


if __name__ == "__main__":
    main()
