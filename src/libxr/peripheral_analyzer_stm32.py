#!/usr/bin/env python
"""libxr parse 的 STM32 解析器：把 STM32CubeMX 的 .ioc 文件解析成 libxr gen 读取的 YAML 配置。
The STM32 parser of libxr parse: parses an STM32CubeMX .ioc file into the YAML configuration
that libxr gen reads.

每类外设由一个 PeripheralParser 子类从 .ioc 的 key=value 表中读取，结果汇总到
ConfigurationManager，清理后写成 .config.yaml。
Each kind of peripheral is read from the key=value map of the .ioc file by a PeripheralParser
subclass; the results are collected in a ConfigurationManager, cleaned and written as
.config.yaml.
"""

import logging
import os
import re
import sys
from collections import defaultdict
from re import Pattern
from typing import Any, TextIO

import yaml
from xr_syntax.i18n import tr


# --------------------------
# 工具函数 / Utility Functions
# --------------------------
def sanitize_numeric(value: str) -> int | float | str:
    """把字符串转成数值：全是数字时为 int，其余能解析的为 float，都不能解析时原样返回。
    Convert a string to a number: all digits give an int, other parseable text a float, and
    anything else is returned unchanged.
    """
    try:
        return int(value) if value.isdigit() else float(value)
    except ValueError:
        return value


# --------------------------
# 配置容器 / Configuration Containers
# --------------------------
class ConfigurationManager:
    """保存各解析器写入的配置：引脚、外设、DMA、FreeRTOS、ThreadX、时基和 MCU 信息。
    Holds the configuration the parsers write: pins, peripherals, DMA, FreeRTOS, ThreadX, the
    timebase and the MCU.
    """

    def __init__(self) -> None:
        """创建空的配置容器；时基默认为 SysTick。
        Create empty configuration containers; the timebase defaults to SysTick.

        gpio_pins 是 pin_registry 的别名，供旧调用方使用。
        gpio_pins is an alias of pin_registry kept for older callers.
        """
        self.pin_registry: defaultdict[str, dict[str, Any]] = defaultdict(dict)
        # 兼容旧调用方的别名；引脚配置以 pin_registry 为准。
        # Compatibility alias for older callers; pin_registry is the canonical store.
        self.gpio_pins: defaultdict[str, dict[str, Any]] = self.pin_registry
        self.peripherals: defaultdict[str, defaultdict[str, dict]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self.dma_types: dict[str, str] = {}
        self.dma_requests: dict[str, str] = {}
        self.dma_configs: defaultdict[str, list[dict]] = defaultdict(list)
        self.freertos_config: dict[str, Any] = {
            "Tasks": {},
            "Heap": None,
            "Features": {},
        }
        self.threadx_config: dict[str, Any] = {
            "AllocationMethod": None,
            "MemPoolSize": None,
            "CorePresent": None,
            "Tasks": {},
        }
        self.timebase: dict[str, str | None] = {"Source": "SysTick", "IRQ": None}
        self.mcu_config: dict[str, str | None] = {"Family": None, "Type": None}

    def clean_structure(self) -> dict[str, Any]:
        """返回写入 YAML 的最终结构：GPIO、Peripherals、DMA、Timebase 和 Mcu。
        Return the final structure written to YAML: GPIO, Peripherals, DMA, Timebase and Mcu.

        记录到 ThreadX 内存分配方式时加入 ThreadX 段；FreeRTOS 有任务、堆大小或功能开关时
        加入 FreeRTOS 段。
        A ThreadX section is added when a ThreadX allocation method was found, and a FreeRTOS
        section when FreeRTOS has tasks, a heap size or feature flags.
        """
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

        # 记录到内存分配方式时才加入 ThreadX 段。
        # Add ThreadX only if an allocation method was found
        cleaned_data_threadx = self._clean_threadx()
        if cleaned_data_threadx["AllocationMethod"]:
            cleaned_data["ThreadX"] = cleaned_data_threadx

        # 任务、堆大小或功能开关有一项非空时才加入 FreeRTOS 段。
        # Add FreeRTOS only if any fields exist
        cleaned_freertos = self._clean_freertos()
        if any([cleaned_freertos["Tasks"], cleaned_freertos["Heap"], cleaned_freertos["Features"]]):
            cleaned_data["FreeRTOS"] = cleaned_freertos

        return cleaned_data

    def _clean_gpio(self) -> dict[str, dict]:
        """GPIO 段：只含 GPIO 输入、输出和 GPXTI 外部中断引脚，字段限于 Signal、Label、Pull
        和 GPXTI。
        The GPIO section: only GPIO input, output and GPXTI external interrupt pins, with the
        fields Signal, Label, Pull and GPXTI.
        """
        return {
            pin: {k: v for k, v in config.items() if k in {"Signal", "Label", "Pull", "GPXTI"}}
            for pin, config in self.pin_registry.items()
            if self._is_valid_gpio(config)
        }

    def _is_valid_gpio(self, config: dict) -> bool:
        """引脚信号为 GPIO_Output、GPIO_Input 或以 GPXTI 开头时为真。
        True when the pin signal is GPIO_Output, GPIO_Input or starts with GPXTI.
        """
        return config.get("Signal") in {"GPIO_Output", "GPIO_Input"} or config.get(
            "Signal", ""
        ).startswith("GPXTI")

    def _clean_peripherals(self) -> dict[str, dict]:
        """Peripherals 段：按外设类型和实例名组织，每个实例去掉空值字段，没有实例的类型不写出。
        The Peripherals section, by peripheral type and instance name, with empty fields
        removed from each instance and types without instances left out.
        """
        return {
            p_type: {p: self._clean_peripheral_config(cfg) for p, cfg in p_group.items()}
            for p_type, p_group in self.peripherals.items()
            if p_group
        }

    def _clean_peripheral_config(self, config: dict) -> dict:
        """去掉值为 None、空字符串、空列表或空字典的字段。
        Drop the fields whose value is None, an empty string, an empty list or an empty dict.
        """
        return {k: v for k, v in config.items() if v not in (None, "", [], {})}

    def _clean_dma_configs(self) -> dict[str, list]:
        """DMA 配置中非空的条目。
        The DMA configurations that are not empty.
        """
        return {k: v for k, v in self.dma_configs.items() if v}

    def _clean_freertos(self) -> dict:
        """FreeRTOS 段：任务、堆大小和已启用的功能，功能名去掉 INCLUDE_ 前缀。
        The FreeRTOS section: tasks, heap size and the enabled features, with INCLUDE_ removed
        from feature names.

        RTOS 默认为 FreeRTOS，Enabled 默认为 False。
        RTOS defaults to FreeRTOS and Enabled to False.
        """
        return {
            "RTOS": self.freertos_config.get("RTOS", "FreeRTOS"),
            "Enabled": self.freertos_config.get("Enabled", False),
            "AllocationMethod": self.freertos_config.get("AllocationMethod"),
            "MemPoolSize": self.freertos_config.get("MemPoolSize"),
            "CorePresent": self.freertos_config.get("CorePresent"),
            "Tasks": self.freertos_config["Tasks"],
            "Heap": self.freertos_config["Heap"],
            "Features": [
                feat.replace("INCLUDE_", "")
                for feat, enabled in self.freertos_config["Features"].items()
                if enabled
            ],
        }

    def _clean_threadx(self) -> dict:
        """ThreadX 段：内存分配方式、内存池大小、是否包含内核，以及任务。
        The ThreadX section: allocation method, memory pool size, whether the core is present,
        and the tasks.
        """
        return {
            "AllocationMethod": self.threadx_config.get("AllocationMethod"),
            "MemPoolSize": self.threadx_config.get("MemPoolSize"),
            "CorePresent": self.threadx_config.get("CorePresent"),
            "Tasks": self.threadx_config["Tasks"],
        }


# --------------------------
# 解析器基类 / Base Parser Class
# --------------------------
class PeripheralParser:
    """外设解析器基类：提供 .ioc key 的拆分与归一化工具和 GPIO 解析，子类实现 parse。
    Base class of the peripheral parsers: helpers that split and normalize .ioc keys, GPIO
    parsing, and a parse method that each subclass implements.
    """

    _PIN_PROPERTY_PATTERN = re.compile(r"^((?:P[A-K]\d+)[^.]*)\.(Signal|GPIO_Label|GPIO_PuPd)$")
    _PERIPHERAL_ROOT_PATTERN = re.compile(
        r"^((?:USART|LPUART|UART|I2C|SPI|TIM|LPTIM|HRTIM|ADC|DAC|FDCAN|CAN|USB)\d*)"
    )

    def __init__(
        self,
        config: ConfigurationManager,
        raw_map: dict[str, str],
        gpio_pattern: Pattern = _PIN_PROPERTY_PATTERN,
    ) -> None:
        """绑定要写入的配置和 .ioc 文件的 key=value 表。
        Bind the configuration to write to and the key=value map of the .ioc file.

        gpio_pattern 匹配引脚属性 key，默认匹配 PxN 引脚的 Signal、GPIO_Label 和 GPIO_PuPd。
        gpio_pattern matches pin property keys; by default the Signal, GPIO_Label and GPIO_PuPd
        of PxN pins.
        """
        self.config = config
        self.raw_map = raw_map
        self.gpio_pattern = gpio_pattern

    @staticmethod
    def _split_ioc_key(key: str) -> list[str]:
        """按点号拆分 .ioc 属性 key，各段保持原样。
        Split an .ioc property key at dots, keeping each token verbatim.
        """
        return str(key).split(".")

    @staticmethod
    def _ioc_key_root(key: str) -> str:
        """.ioc 属性 key 的第一段。
        The first token of an .ioc property key.
        """
        return PeripheralParser._split_ioc_key(key)[0]

    @staticmethod
    def _ioc_key_prop(key: str, default: str | None = None) -> str | None:
        """.ioc 属性 key 的第二段；key 只有一段时为 default。
        The second token of an .ioc property key; default when the key has a single token.
        """
        parts = PeripheralParser._split_ioc_key(key)
        return parts[1] if len(parts) > 1 else default

    @staticmethod
    def _has_ioc_prefix(key: str, prefix: str) -> bool:
        """key 等于 prefix 或以 "prefix." 开头时为真，即前缀只在段边界处匹配。
        True when the key equals prefix or starts with "prefix.", so the prefix matches only on
        a token boundary.
        """
        key = str(key)
        return key == prefix or key.startswith(f"{prefix}.")

    @staticmethod
    def _ioc_root_startswith(key: str, stem: str) -> bool:
        """key 的第一段以 stem 开头时为真。
        True when the first dot-separated token of the key starts with stem.
        """
        return PeripheralParser._ioc_key_root(key).startswith(stem)

    @staticmethod
    def _ioc_key_startswith(key: str, stem: str) -> bool:
        """整个 key 以 stem 开头时为真，用于 Mcu.IP0、Mcu.IPNb 这类带编号的 key。
        True when the whole key starts with stem; used for numbered keys such as Mcu.IP0 and
        Mcu.IPNb.
        """
        return str(key).startswith(stem)

    @staticmethod
    def _dma_request_id(key: str, prefix: str) -> str | None:
        """<prefix>.RequestN 形式的 key（如 Dma.Request0、Bdma.Request1）中的请求号 N；
        其他 key 为 None。
        The request id N of a <prefix>.RequestN key such as Dma.Request0 or Bdma.Request1;
        None for any other key.
        """
        match = re.fullmatch(rf"{re.escape(prefix)}\.Request(\d+)", str(key))
        return match.group(1) if match else None

    @staticmethod
    def _dma_request_key(prefix: str, req_id: str) -> str:
        """组成 "<prefix>.Request<req_id>" 形式的请求 key，使 DMA 与 BDMA 的同号请求互不冲突。
        Build a "<prefix>.Request<req_id>" request key, so DMA and BDMA requests with the same
        number stay apart.
        """
        return f"{prefix}.Request{req_id}"

    @staticmethod
    def _normalize_gpio_pin_token(pin: str) -> str:
        """把 CubeMX 引脚名归一为物理引脚名 PxN，例如 PC14-OSC32_IN 变为 PC14；不匹配时原样返回。
        Normalize a CubeMX pin token to the physical PxN name, e.g. PC14-OSC32_IN becomes PC14;
        a token that does not match is returned unchanged.
        """
        match = re.match(r"^(P[A-K]\d+)", pin)
        return match.group(1) if match else pin

    @staticmethod
    def _normalize_ioc_key_pin(key: str) -> str:
        """取 .ioc 属性 key 的第一段并归一为 PxN 引脚名。
        Take the first token of an .ioc property key and normalize it to a PxN pin name.
        """
        return PeripheralParser._normalize_gpio_pin_token(PeripheralParser._ioc_key_root(key))

    @staticmethod
    def _normalize_signal_token(signal: str) -> str:
        """推导外设名之前归一 CubeMX 信号名：去掉首尾空白，转为大写，并去掉 S_ 别名前缀。
        Normalize a CubeMX signal name before peripheral names are derived from it: strip
        whitespace, convert to upper case and remove the S_ alias prefix.
        """
        signal = str(signal).strip().upper()
        return signal[2:] if signal.startswith("S_") else signal

    @staticmethod
    def _normalize_tim_channel_token(channel: str) -> str | None:
        """把 TIM_CHANNEL_x 或 CHx（均可带 N 后缀）归一为 CHx / CHxN；其他写法为 None。
        Normalize TIM_CHANNEL_x or CHx, each with an optional N suffix, to CHx / CHxN; None for
        any other form.
        """
        channel = str(channel).strip().upper()
        match = re.fullmatch(r"TIM_CHANNEL_(\d+)(N?)", channel)
        if match:
            return f"CH{match.group(1)}{match.group(2)}"
        match = re.fullmatch(r"CH(\d+)(N?)", channel)
        if match:
            return f"CH{match.group(1)}{match.group(2)}"
        return None

    @staticmethod
    def _signal_root(signal: str) -> str:
        """CubeMX 信号名中的外设实例名；已知外设按前缀匹配，其余取第一个下划线之前的部分。
        The peripheral instance in a CubeMX signal name; known peripherals match by prefix, and
        other names give the text before the first underscore.

        例如 USART1_TX 为 USART1，I2C2_SCL 为 I2C2，TIM1_CH1N 为 TIM1。
        For example USART1_TX gives USART1, I2C2_SCL gives I2C2 and TIM1_CH1N gives TIM1.
        """
        signal = PeripheralParser._normalize_signal_token(signal)
        match = PeripheralParser._PERIPHERAL_ROOT_PATTERN.match(signal)
        return match.group(1) if match else signal.split("_")[0]

    @staticmethod
    def _signal_suffix(signal: str) -> str:
        """CubeMX 信号名最后一个下划线之后的部分（大写）；没有下划线时为整个信号名。
        The part of a CubeMX signal name after the last underscore, in upper case; the whole
        name when it has no underscore.
        """
        signal = PeripheralParser._normalize_signal_token(signal)
        return signal.split("_")[-1].upper() if "_" in signal else signal.upper()

    @staticmethod
    def _parse_dma_request_endpoint(peripheral_full: str) -> tuple[str, str]:
        """把 USART1_TX 这类 DMA 请求目标拆成外设名和小写方向，例如 ("USART1", "tx")。
        Split a DMA request target such as USART1_TX into the peripheral and a lower-case
        direction, e.g. ("USART1", "tx").

        最后一个下划线之后全为字母时作为方向，否则整个目标为外设名，方向为 "general"。
        The letters after the last underscore are the direction; without such a suffix the
        whole target is the peripheral and the direction is "general".
        """
        endpoint = str(peripheral_full).strip()
        match = re.match(r"^(.*)_([A-Z]+)$", endpoint.upper())
        if not match:
            return endpoint, "general"
        return match.group(1), match.group(2).lower()

    @staticmethod
    def _normalize_dma_direction(value: str) -> str:
        """完整 DMA 方向的小写形式，只去掉 DMA_ 前缀，例如 DMA_PERIPH_TO_MEMORY 变为
        periph_to_memory。
        The complete DMA direction in lower case with only the DMA_ prefix removed, e.g.
        DMA_PERIPH_TO_MEMORY becomes periph_to_memory.
        """
        direction = str(value).strip().upper()
        if direction.startswith("DMA_"):
            direction = direction[4:]
        return direction.lower()

    def parse_gpio(self) -> None:
        """读取匹配 gpio_pattern 的引脚属性，按归一后的 PxN 引脚名写入 pin_registry。
        Read the pin properties that match gpio_pattern into pin_registry, keyed by the
        normalized PxN pin name.
        """
        for key, value in self.raw_map.items():
            if match := self.gpio_pattern.match(key):
                pin, prop = match.groups()
                self._process_gpio_property(self._normalize_gpio_pin_token(pin), prop, value)

    def _process_gpio_property(self, pin: str, prop: str, value: str) -> None:
        """把一个引脚属性写入 pin_registry：Signal 原样保存，GPIO_Label 的第一个词存为 Label，
        GPIO_PuPd 存为 Pull；值中含 GPXTI 时同时置 GPXTI 标记。
        Write one pin property to pin_registry: Signal as is, the first word of GPIO_Label as
        Label and GPIO_PuPd as Pull; a value containing GPXTI also sets the GPXTI flag.
        """
        prop_map = {
            "Signal": ("Signal", value),
            "GPIO_Label": ("Label", str(value).split()[0] if str(value).split() else ""),
            "GPIO_PuPd": ("Pull", value),
        }
        field, val = prop_map[prop]
        self.config.pin_registry[pin][field] = val
        if "GPXTI" in value:
            self.config.pin_registry[pin]["GPXTI"] = True

    def parse(self, p_type: str) -> None:
        """解析 p_type 类外设的配置，由子类实现；基类调用时抛出 NotImplementedError。
        Parse the configuration of peripheral type p_type; implemented by subclasses, and the
        base class raises NotImplementedError.
        """
        raise NotImplementedError


# --------------------------
# MCU 解析器 / MCU Parser
# --------------------------
class McuParser(PeripheralParser):
    """读取 MCU 系列和型号。
    Reads the MCU family and part number.
    """

    def parse(self, p_type: str) -> None:
        """从 Mcu.* key 读取系列（Family）和型号（CPN），写入 mcu_config。
        Read the family (Family) and part number (CPN) from Mcu.* keys into mcu_config.
        """
        for key, value in self.raw_map.items():
            if not self._ioc_root_startswith(key, "Mcu"):
                continue
            prop = self._ioc_key_prop(key, "")
            if "Family" in prop:
                self.config.mcu_config["Family"] = value
            elif "CPN" in prop:
                self.config.mcu_config["Type"] = value


# --------------------------
# TIM 解析器 / TIM Parser
# --------------------------
class TIMParser(PeripheralParser):
    """读取定时器的模式、周期、预分频和 PWM 通道。
    Reads timer mode, period, prescaler and PWM channels.
    """

    def parse(self, p_type: str) -> None:
        """读取 TIMx.* 属性：Channel-PWM 和 Channel 属性记为 PWM 通道，属性名含 Period、
        Prescaler 或 Mode 时写入对应字段，周期和预分频转为数值。
        Read TIMx.* properties: Channel-PWM and Channel properties become PWM channels, and a
        property name containing Period, Prescaler or Mode sets that field, with period and
        prescaler converted to numbers.
        """
        for key, value in self.raw_map.items():
            tim_name = self._ioc_key_root(key)
            if not tim_name.startswith("TIM"):
                continue

            parts = self._split_ioc_key(key)
            if len(parts) < 2:
                continue

            self._ensure_tim_instance(p_type, tim_name)

            if "Channel-PWM" in key:
                self._handle_pwm_channel(tim_name, parts, value)
            elif parts[1] == "Channel":
                # 简化格式，如 TIM10.Channel → TIM_CHANNEL_1。
                # Simplified format like TIM10.Channel → TIM_CHANNEL_1
                ch_name = self._normalize_tim_channel_token(value)
                if ch_name:
                    label, is_n = self._get_associated_pin_label(tim_name, ch_name)
                    self.config.peripherals["TIM"][tim_name]["Channels"][ch_name] = {
                        "Label": label,
                        "PWM": True,
                        "Complementary": is_n,
                    }
            elif "Period" in parts[1]:
                self.config.peripherals[p_type][tim_name]["Period"] = sanitize_numeric(value)
            elif "Prescaler" in parts[1]:
                self.config.peripherals[p_type][tim_name]["Prescaler"] = sanitize_numeric(value)
            elif "Mode" in parts[1]:
                self.config.peripherals[p_type][tim_name]["Mode"] = value

    def _ensure_tim_instance(self, p_type: str, tim_name: str) -> None:
        """TIM 实例不存在时创建，模式、周期、预分频为空，通道和脉宽表为空。
        Create the TIM instance when it does not exist, with empty mode, period and prescaler
        and empty channel and pulse maps.
        """
        if not self.config.peripherals[p_type].get(tim_name):
            self.config.peripherals[p_type][tim_name] = {
                "Mode": None,
                "ClockPrescaler": None,
                "Period": None,
                "Prescaler": None,
                "Channels": {},
                "Pulses": {},
            }

    def _handle_pwm_channel(self, tim_name: str, parts: list, value: str) -> None:
        """从 TIMx.Channel-PWM Generation2 CH2N=TIM_CHANNEL_2 这类条目记录一个 PWM 通道。
        Record one PWM channel from an entry such as TIMx.Channel-PWM Generation2
        CH2N=TIM_CHANNEL_2.

        通道取 key 末尾的 CHx / CHxN，CHxN 标记为互补输出；Label 为该通道所连引脚的标签；
        值全是数字时存为 DutyCycle。
        The channel is the trailing CHx / CHxN of the key, and CHxN is marked complementary;
        Label is the label of the pin wired to the channel; an all-digit value is stored as
        DutyCycle.
        """
        # 用正则表达式取出末尾的 CHx 或 CHxN。
        # Use regex to capture CHx or CHxN
        match = re.search(r"(CH\d+N?)$", parts[1])
        if not match:
            return

        channel_id = self._normalize_tim_channel_token(match.group(1))
        if not channel_id:
            return
        is_n = channel_id.endswith("N")
        pin_label, _ = self._get_associated_pin_label(tim_name, channel_id)

        self.config.peripherals["TIM"][tim_name]["Channels"][channel_id] = {
            "Label": pin_label,
            "PWM": True,
            "Complementary": is_n,
            "DutyCycle": sanitize_numeric(value) if value.isdigit() else None,
        }

    def _get_associated_pin_label(self, timer_name: str, channel_id: str) -> tuple[str, bool]:
        """定时器通道所连引脚的 (标签, 是否互补输出)；引脚信号以 N 结尾时为互补输出。
        The (label, is_complementary) of the pin wired to a timer channel; the output is
        complementary when the pin signal ends with N.

        引脚没有标签时用引脚名；CH1 也匹配 TIMx_CH1_ETR 信号。通道名无效或找不到引脚时返回
        (timer_name, False)。
        A pin without a label gives its pin name; CH1 also matches the TIMx_CH1_ETR signal. An
        invalid channel, or no matching pin, gives (timer_name, False).
        """
        normalized_channel = self._normalize_tim_channel_token(channel_id)
        if not normalized_channel:
            return timer_name, False
        signal_candidates = {f"{timer_name}_{normalized_channel}"}
        if normalized_channel == "CH1":
            signal_candidates.add(f"{timer_name}_CH1_ETR")

        config = {}
        matched_pin = timer_name
        for pin_name, pin_cfg in self.config.pin_registry.items():
            normalized_signal = self._normalize_signal_token(pin_cfg.get("Signal", ""))
            if normalized_signal in signal_candidates:
                config = pin_cfg
                matched_pin = pin_name
                break

        label = config.get("Label", matched_pin)
        signal = self._normalize_signal_token(config.get("Signal", ""))
        is_complementary = signal.endswith("N")  # 例如 TIM1_CH1N / e.g., TIM1_CH1N
        return label, is_complementary


# --------------------------
# ADC 解析器 / ADC Parser
# --------------------------
class ADCParser(PeripheralParser):
    """读取 ADC 实例的规则转换通道、内部通道、连续模式、DMA 和 EOC 设置。
    Reads ADC instances: regular conversion channels, internal channels, continuous mode, DMA
    and EOC selection.
    """

    _CHANNEL_PATTERN = re.compile(r"^ADC_CHANNEL_[A-Z0-9_]+$")

    def parse(self, p_type: str) -> None:
        """先读取 ADCx.* 属性，再把 VP_*.Signal 虚拟引脚映射为内部通道，最后对通道去重。
        Read ADCx.* properties, then map VP_*.Signal virtual pins to internal channels, then
        deduplicate the channels.
        """
        for key, value in self.raw_map.items():
            if self._ioc_root_startswith(key, "ADC"):
                self._parse_adc_property(key, value)
        for key, value in self.raw_map.items():
            if self._ioc_root_startswith(key, "VP_") and key.endswith(".Signal"):
                self._parse_vp_adc_signal(key, value)
        self._deduplicate_channels()

    def _map_internal_channel(self, value: str) -> str | None:
        """把 VP_* 虚拟引脚的 ADC 内部信号映射为 HAL 通道宏；无法识别时为 None。
        Map the internal ADC signal of a VP_* virtual pin to a HAL channel macro; None when the
        signal is not recognized.

        VREF 映射为 ADC_CHANNEL_VREFINT，VBAT 映射为 ADC_CHANNEL_VBAT，OPAMPn 映射为
        ADC_CHANNEL_VOPAMPn。温度传感器优先取该 ADC 的 CommonPathInternal 中带后缀的宏
        （如 ADC_CHANNEL_TEMPSENSOR_ADC1），否则为 ADC_CHANNEL_TEMPSENSOR；选择不依赖 MCU 系列。
        VREF maps to ADC_CHANNEL_VREFINT, VBAT to ADC_CHANNEL_VBAT and OPAMPn to
        ADC_CHANNEL_VOPAMPn. The temperature sensor takes a suffixed macro such as
        ADC_CHANNEL_TEMPSENSOR_ADC1 from that ADC's CommonPathInternal, and otherwise
        ADC_CHANNEL_TEMPSENSOR; the choice does not depend on the MCU family.

        ADC 实例取信号开头的 ADCn，没有时取第一个已记录的 ADC 实例。
        The ADC instance is the ADCn at the start of the signal, or else the first recorded ADC
        instance.
        """
        v_upper = value.upper()

        # 从 VP_* 的值（如 "ADC1_TempSensor"）取 ADC 实例，
        # 取不到时用第一个已记录的 ADC 实例。
        # Try to derive the ADC instance from the VP_* value (e.g., "ADC1_TempSensor"),
        # otherwise fall back to the first known ADC instance.
        m_adc = re.match(r"(ADC\d+)_", v_upper)
        adc_name = (m_adc.group(1) if m_adc else self._get_adc_instance_name()).upper()

        # 读取属性解析时记录的 CommonPathInternal（如有）。
        # Read CommonPathInternal (if present) captured during property parsing.
        adc_cfg = self.config.peripherals.get("ADC", {}).get(adc_name, {})
        cp_list = adc_cfg.get("CommonPathInternal", []) or []

        # 直接映射
        # Direct maps
        if "VREF" in v_upper:
            return "ADC_CHANNEL_VREFINT"
        if "VBAT" in v_upper:
            return "ADC_CHANNEL_VBAT"

        # 温度传感器：优先取 CommonPathInternal 中带后缀的宏，其次用通用宏。
        # TempSensor: prefer suffixed macros from CommonPathInternal, then generic
        if "TEMP" in v_upper or "TEMPSENSOR" in v_upper:
            for tok in cp_list:
                if re.match(r"ADC_CHANNEL_TEMPSENSOR_ADC\d+$", tok):
                    return tok
            if "ADC_CHANNEL_TEMPSENSOR" in cp_list:
                return "ADC_CHANNEL_TEMPSENSOR"
            return "ADC_CHANNEL_TEMPSENSOR"

        # 运算放大器 OPAMPn
        # OPAMPn
        m = re.search(r"OPAMP(\d+)", v_upper)
        if m:
            return f"ADC_CHANNEL_VOPAMP{m.group(1)}"

        return None

    def _parse_adc_property(self, key: str, value: str) -> None:
        """读取一个 ADCx.<setting> 属性并写入该 ADC 实例。
        Read one ADCx.<setting> property into that ADC instance.

        ChannelRegularConversion 的通道加入规则转换列表；ContinuousConvMode 存为布尔值
        ContinuousMode；DMARegular 和 DMAContinuousRequests 归一为 "ENABLE" / "DISABLE" 存入
        DMA；EOCSelection 原样保存。
        Channels of ChannelRegularConversion join the regular conversion list;
        ContinuousConvMode is stored as the boolean ContinuousMode; DMARegular and
        DMAContinuousRequests are normalized to "ENABLE" / "DISABLE" in DMA; EOCSelection is
        stored as is.

        CommonPathInternal（如 "null|ADC_CHANNEL_TEMPSENSOR_ADC1|null|null"）按 | 拆分，去掉
        null 后以大写列表保存，供之后选择温度传感器宏，选择不依赖 MCU 系列。
        CommonPathInternal, e.g. "null|ADC_CHANNEL_TEMPSENSOR_ADC1|null|null", is split at |
        and kept as an upper-case list without null entries, so the temperature sensor macro
        can be chosen later without depending on the MCU family.
        """
        parts = self._split_ioc_key(key)
        if len(parts) < 2:
            return

        adc_name = parts[0]
        setting = parts[1]
        self._ensure_adc_instance(adc_name)

        def _to_enable_str(v: str) -> str:
            """值为 ENABLE（忽略大小写和首尾空白）时为 "ENABLE"，否则为 "DISABLE"。
            "ENABLE" when the value is ENABLE, ignoring case and surrounding whitespace;
            otherwise "DISABLE".
            """
            return "ENABLE" if str(v).strip().upper() == "ENABLE" else "DISABLE"

        if "ChannelRegularConversion" in setting:
            self._process_conversion_entry(adc_name, value)
        elif setting == "ContinuousConvMode":
            self.config.peripherals["ADC"][adc_name]["ContinuousMode"] = value == "ENABLE"
        elif setting == "DMARegular" or setting == "DMAContinuousRequests":
            self.config.peripherals["ADC"][adc_name]["DMA"] = _to_enable_str(value)
        elif setting == "EOCSelection":
            self.config.peripherals["ADC"][adc_name]["EOCSelection"] = value
        elif setting == "CommonPathInternal":
            # 值形如 "null|ADC_CHANNEL_TEMPSENSOR_ADC1|null|null"。
            # Example string: "null|ADC_CHANNEL_TEMPSENSOR_ADC1|null|null"
            raw = str(value)
            tokens = [t.strip() for t in raw.split("|")]
            tokens = [t.upper() for t in tokens if t and t.lower() != "null"]
            self.config.peripherals["ADC"][adc_name]["CommonPathInternal"] = tokens

    def _get_adc_instance_name(self) -> str:
        """第一个已记录的 ADC 实例名；还没有 ADC 实例时为 "ADC"。
        The first recorded ADC instance name; "ADC" while there is none.
        """
        adc_instances = self.config.peripherals.get("ADC", {})
        return list(adc_instances.keys())[0] if adc_instances else "ADC"

    def _parse_vp_adc_signal(self, key: str, value: str) -> None:
        """把信号以 ADC 开头的 VP_* 虚拟引脚映射为内部通道宏，加入对应 ADC 实例的 Channels。
        Map a VP_* virtual pin whose signal starts with ADC to an internal channel macro and add
        it to Channels of that ADC instance.

        这里得到的通道只加入 Channels，不加入 RegularConversions。ADC 实例取信号开头的
        ADCn，没有时取第一个已记录的 ADC 实例。
        Channels found here go to Channels only, not to RegularConversions. The ADC instance is
        the ADCn at the start of the signal, or else the first recorded ADC instance.
        """
        if not self._normalize_signal_token(value).startswith("ADC"):
            return

        parts = self._normalize_signal_token(value).split("_")
        if parts[0].startswith("ADC") and parts[0][-1].isdigit():
            adc_name = parts[0]  # 例如 ADC1 / e.g., ADC1
        else:
            adc_name = self._get_adc_instance_name()

        self._ensure_adc_instance(adc_name)

        mapped_channel = self._map_internal_channel(value)
        if mapped_channel:
            # 只加入 Channels，不加入 RegularConversions。
            # Only add to Channels (not RegularConversions)
            self._add_unique_entry(adc_name, "Channels", mapped_channel)

    def _process_conversion_entry(self, adc_name: str, raw_value: str) -> None:
        """拆分逗号分隔的 ChannelRegularConversion 值；符合 ADC_CHANNEL_* 格式的通道同时加入
        Channels 和 RegularConversions，其余非空项记录 debug 日志后忽略。
        Split a comma-separated ChannelRegularConversion value; channels of the ADC_CHANNEL_*
        form join both Channels and RegularConversions, and other non-empty items are logged
        at debug level and ignored.
        """
        for entry in raw_value.split(","):
            cleaned_entry = entry.strip()
            # 用正则表达式检查条目格式。
            # Validate entry format using regex
            if self._is_valid_channel(cleaned_entry):
                # 显式配置的规则转换通道同时加入两个列表。
                # Regular conversions explicitly configured go to both lists
                self._add_unique_entry(adc_name, "Channels", cleaned_entry)
                self._add_unique_entry(adc_name, "RegularConversions", cleaned_entry)
            elif cleaned_entry:
                logging.debug(f"Ignored invalid ADC entry: {cleaned_entry}")

    def _is_valid_channel(self, entry: str) -> bool:
        """entry 为 ADC_CHANNEL_ 后接大写字母、数字或下划线时为真。
        True when entry is ADC_CHANNEL_ followed by upper-case letters, digits or underscores.
        """
        return bool(self._CHANNEL_PATTERN.match(entry))

    def _ensure_adc_instance(self, adc_name: str) -> None:
        """ADC 实例不存在时创建：连续模式关闭，通道列表为空，DMA 为 "DISABLE"。
        Create the ADC instance when it does not exist: continuous mode off, empty channel
        lists and DMA "DISABLE".
        """
        if adc_name not in self.config.peripherals["ADC"]:
            self.config.peripherals["ADC"][adc_name] = {
                "ContinuousMode": False,
                "RegularConversions": [],
                "Channels": [],
                "DMA": "DISABLE",
            }

    def _add_unique_entry(self, adc_name: str, field: str, value: str) -> None:
        """value 不在 ADC 实例的 field 列表中时追加到末尾。
        Append value to the field list of the ADC instance when it is not already there.
        """
        target_list = self.config.peripherals["ADC"][adc_name][field]
        if value not in target_list:
            target_list.append(value)

    def _deduplicate_channels(self) -> None:
        """对每个 ADC 实例的 Channels 和 RegularConversions 保序去重，并优先带后缀的温度传感器宏。
        Deduplicate Channels and RegularConversions of each ADC instance in order, preferring
        suffixed temperature sensor macros.

        通道列表或 CommonPathInternal 中出现 ADC_CHANNEL_TEMPSENSOR_ADCn 时，去掉通用的
        ADC_CHANNEL_TEMPSENSOR。
        When ADC_CHANNEL_TEMPSENSOR_ADCn appears in a channel list or in CommonPathInternal,
        the generic ADC_CHANNEL_TEMPSENSOR is removed.
        """
        for adc_cfg in self.config.peripherals["ADC"].values():
            # 保序去重。
            # Basic dedupe
            chs = list(dict.fromkeys(adc_cfg.get("Channels", [])))
            regs = list(dict.fromkeys(adc_cfg.get("RegularConversions", [])))

            cp_list = adc_cfg.get("CommonPathInternal", []) or []
            # 在通道列表和 CommonPathInternal 中查找带后缀的温度传感器宏。
            # Detect any suffixed TempSensor macro from any source
            has_suffixed = any(
                re.match(r"ADC_CHANNEL_TEMPSENSOR_ADC\d+$", x) for x in (chs + regs + cp_list)
            )

            if has_suffixed:
                chs = [x for x in chs if x != "ADC_CHANNEL_TEMPSENSOR"]
                regs = [x for x in regs if x != "ADC_CHANNEL_TEMPSENSOR"]

            adc_cfg["Channels"] = chs
            adc_cfg["RegularConversions"] = regs


# --------------------------
# DAC 解析器 / DAC Parser
# --------------------------
class DACParser(PeripheralParser):
    """读取 DAC 实例的输出通道、触发源、DMA 和输出缓冲设置。
    Reads DAC instances: output channels, trigger, DMA and output buffer.
    """

    def parse(self, p_type: str) -> None:
        """读取两种 DAC 条目：SH.COMP_DAC<n>_group.<k> 和 DACx.* 属性。
        Read two kinds of DAC entries: SH.COMP_DAC<n>_group.<k> and DACx.* properties.

        SH.COMP_DAC 条目的值在第一个逗号处拆成通道和别名。一位编号（如 COMP_DAC2_group）归入
        实例 DAC，通道键为值中的通道；两位编号（如 COMP_DAC12_group）归入 DAC1 的 OUT2。
        The value of an SH.COMP_DAC entry is split at the first comma into a channel and an
        alias. A one-digit number such as COMP_DAC2_group goes to the instance DAC under the
        channel from the value; a two-digit number such as COMP_DAC12_group goes to OUT2 of
        DAC1.
        """
        for key, value in self.raw_map.items():
            # 1. SH.COMP_DAC*_group 条目，单通道和多通道 DAC 都能识别。
            # 1. SH.COMP_DAC*_group. Compatible with single/multi-channel DAC recognition
            m = re.match(r"^SH\.COMP_DAC(\d{1,2})_group\.\d+$", key)
            if m:
                digits = m.group(1)
                out, alias = value.split(",", 1)
                if len(digits) == 1:
                    # 一位数字（如 COMP_DAC2_group）：唯一的 DAC，通道 OUTx（通常为 OUT1/OUT2）。
                    # Only one digit (e.g., COMP_DAC2_group): unique DAC, OUTx (usually DAC's OUT1/OUT2)
                    self._ensure_dac_instance("DAC")
                    self.config.peripherals["DAC"]["DAC"]["Channels"][out] = alias
                elif len(digits) == 2:
                    # 两位数字（如 COMP_DAC12_group）：DAC1 的 OUT2。
                    # Two digits (e.g., COMP_DAC12_group): DAC1's OUT2
                    dac_idx = digits[0]
                    out_idx = digits[1]
                    dac_name = f"DAC{dac_idx}"
                    out_name = f"OUT{out_idx}"
                    self._ensure_dac_instance(dac_name)
                    self.config.peripherals["DAC"][dac_name]["Channels"][out_name] = alias
                continue

            # 2. 兼容 CubeMX 的新格式（如 DAC1.DAC_Channel-DAC_OUT1=DAC_CHANNEL_1）。
            # 2. Compatible with new CubeMX format (e.g. DAC1.DAC_Channel-DAC_OUT1=DAC_CHANNEL_1)
            if self._ioc_key_root(key).startswith("DAC"):
                self._parse_dac_property(key, value)

    def _parse_dac_property(self, key: str, value: str) -> None:
        """读取一个 DACx.<setting> 属性：DAC_Channel-DAC_OUTn 记为通道 OUTn，名字含 Trigger、
        DMA 或 OutputBuffer 的属性原样存入对应字段。
        Read one DACx.<setting> property: DAC_Channel-DAC_OUTn becomes channel OUTn, and a
        setting whose name contains Trigger, DMA or OutputBuffer is stored as is in that field.
        """
        parts = self._split_ioc_key(key)
        if len(parts) < 2:
            return
        dac_name = parts[0]
        setting = parts[1]
        self._ensure_dac_instance(dac_name)
        if "Channel" in setting:
            match = re.match(r"DAC_Channel-DAC_OUT(\d+)", setting)
            if match:
                ch_num = match.group(1)
                ch_key = f"OUT{ch_num}"
                ch_val = value.strip()
                self.config.peripherals["DAC"][dac_name]["Channels"][ch_key] = ch_val
        elif "Trigger" in setting:
            self.config.peripherals["DAC"][dac_name]["Trigger"] = value
        elif "DMA" in setting:
            self.config.peripherals["DAC"][dac_name]["DMA"] = value
        elif "OutputBuffer" in setting:
            self.config.peripherals["DAC"][dac_name]["OutputBuffer"] = value

    def _ensure_dac_instance(self, dac_name: str) -> None:
        """DAC 实例不存在时创建，通道表为空，触发源、DMA 和输出缓冲为空。
        Create the DAC instance when it does not exist, with no channels and empty trigger, DMA
        and output buffer.
        """
        if dac_name not in self.config.peripherals["DAC"]:
            self.config.peripherals["DAC"][dac_name] = {
                "Channels": {},
                "Trigger": None,
                "DMA": None,
                "OutputBuffer": None,
            }


# --------------------------
# SPI 解析器 / SPI Parser
# --------------------------
class SPIParser(PeripheralParser):
    """读取 SPI 实例的波特率、方向、时钟极性和时钟相位。
    Reads SPI instances: baud rate, direction, clock polarity and clock phase.
    """

    def parse(self, p_type: str) -> None:
        """读取 SPIx.* 属性：属性名含 BaudRate、Direction、CLKPolarity 或 CLKPhase 时写入对应
        字段，BaudRate 转为数值。
        Read SPIx.* properties: a property name containing BaudRate, Direction, CLKPolarity or
        CLKPhase sets that field, with BaudRate converted to a number.
        """
        for key, value in self.raw_map.items():
            spi_name = self._ioc_key_root(key)
            if not spi_name.startswith("SPI"):
                continue

            parts = self._split_ioc_key(key)
            if len(parts) < 2:
                continue

            self._ensure_spi_instance(p_type, spi_name)

            prop = parts[1]
            if "BaudRate" in prop:
                self.config.peripherals[p_type][spi_name]["BaudRate"] = sanitize_numeric(value)
            elif "Direction" in prop:
                self.config.peripherals[p_type][spi_name]["Direction"] = value
            elif "CLKPolarity" in prop:
                self.config.peripherals[p_type][spi_name]["CLKPolarity"] = value
            elif "CLKPhase" in prop:
                self.config.peripherals[p_type][spi_name]["CLKPhase"] = value

    def _ensure_spi_instance(self, p_type: str, spi_name: str) -> None:
        """SPI 实例不存在时创建，各字段为空，DMA 表为空。
        Create the SPI instance when it does not exist, with empty fields and an empty DMA map.
        """
        if not self.config.peripherals[p_type].get(spi_name):
            self.config.peripherals[p_type][spi_name] = {
                "BaudRate": None,
                "Direction": None,
                "CLKPolarity": None,
                "CLKPhase": None,
                "DMA": {},
            }


# --------------------------
# USART/UART 解析器 / USART/UART Parser
# --------------------------
class USARTParser(PeripheralParser):
    """读取 USART、UART 和 LPUART 实例，包括只在引脚信号中出现的实例。
    Reads USART, UART and LPUART instances, including instances named only by pin signals.
    """

    def parse(self, p_type: str) -> None:
        """读取 USART、UART 和 LPUART 配置；只有引脚信号的实例也会创建。
        Read USART, UART and LPUART configurations; an instance with only pin signals is created
        too.

        第一遍读取 USARTx/UARTx/LPUARTx.* 属性中的波特率、字长、校验、停止位和模式；第二遍从
        含 _TX 或 _RX 的引脚信号推断实例名，创建尚未出现的实例。
        The first pass reads baud rate, word length, parity, stop bits and mode from
        USARTx/UARTx/LPUARTx.* properties; the second pass derives instance names from pin
        signals containing _TX or _RX and creates the instances not seen yet.
        """
        found_instances = set()

        # 第一遍：按 USART/UART/LPUART 的属性 key 正常解析。
        # First pass: normal parsing from USART/UART/LPUART property keys
        for key, value in self.raw_map.items():
            uart_name = self._ioc_key_root(key)
            if uart_name.startswith(("USART", "UART", "LPUART")):
                parts = self._split_ioc_key(key)
                if len(parts) < 2:
                    continue

                found_instances.add(uart_name)
                self._ensure_uart_instance(p_type, uart_name)

                prop = parts[1]
                if "BaudRate" in prop:
                    self.config.peripherals[p_type][uart_name]["BaudRate"] = sanitize_numeric(value)
                elif "WordLength" in prop:
                    self.config.peripherals[p_type][uart_name]["WordLength"] = value
                elif "Parity" in prop:
                    self.config.peripherals[p_type][uart_name]["Parity"] = value
                elif "StopBits" in prop:
                    self.config.peripherals[p_type][uart_name]["StopBits"] = value
                elif "Mode" in prop:
                    self._handle_operation_mode(p_type, uart_name, value)

        # 第二遍：根据 GPIO 引脚信号推断缺少的 UART 实例。
        # Second pass: infer missing UART instances based on GPIO signals
        for pin_cfg in self.config.pin_registry.values():
            signal = pin_cfg.get("Signal", "")
            if "_TX" in signal or "_RX" in signal:
                uart_root = self._signal_root(signal)
                if (
                    uart_root.startswith(("USART", "UART", "LPUART"))
                    and uart_root not in found_instances
                ):
                    # 仅凭引脚信号发现了新的 UART 实例。
                    # Found a new UART based only on pin signals
                    logging.debug(f"Inferred USART instance from pin: {uart_root}")
                    self._ensure_uart_instance(p_type, uart_root)

    def _ensure_uart_instance(self, p_type: str, uart_name: str) -> None:
        """UART/USART/LPUART 实例不存在时创建，模式为 Asynchronous，其余字段为空。
        Create the UART/USART/LPUART instance when it does not exist, with mode Asynchronous
        and the other fields empty.
        """
        if uart_name not in self.config.peripherals[p_type]:
            self.config.peripherals[p_type][uart_name] = {
                "BaudRate": None,
                "WordLength": None,
                "Parity": None,
                "StopBits": None,
                "Mode": "Asynchronous",
                "DMA": {},
            }

    def _handle_operation_mode(self, p_type: str, uart_name: str, value: str) -> None:
        """值中含 IrDA、LIN 或 SmartCard 时把模式设为该名字；其他值保留原模式。
        Set the mode to IrDA, LIN or SmartCard when the value contains that name; any other
        value keeps the current mode.
        """
        if "IrDA" in value:
            self.config.peripherals[p_type][uart_name]["Mode"] = "IrDA"
        elif "LIN" in value:
            self.config.peripherals[p_type][uart_name]["Mode"] = "LIN"
        elif "SmartCard" in value:
            self.config.peripherals[p_type][uart_name]["Mode"] = "SmartCard"


# --------------------------
# I2C 解析器 / I2C Parser
# --------------------------
class I2CParser(PeripheralParser):
    """读取 I2C 实例的时钟速度、时序、寻址方式和 SCL/SDA 引脚。
    Reads I2C instances: clock speed, timing, addressing mode and SCL/SDA pins.
    """

    def parse(self, p_type: str) -> None:
        """从三类条目读取 I2C：Mcu.IP* 中列出的 I2C 实例；信号含 I2C 的引脚，记为该实例的 SCL
        或 SDA；I2Cx.* 属性。
        Read I2C from three kinds of entries: I2C instances listed in Mcu.IP*; pins whose signal
        contains I2C, recorded as SCL or SDA of that instance; and I2Cx.* properties.

        属性按 key 的最后一段匹配：ClockSpeed 转为数值，DualAddressMode 转为布尔值，Timing
        存为字符串，DutyCycle 和 AddressingMode 原样保存。
        Properties match on the last token of the key: ClockSpeed becomes a number,
        DualAddressMode a boolean and Timing a string; DutyCycle and AddressingMode are stored
        as is.
        """
        for key, value in self.raw_map.items():
            if self._ioc_key_startswith(key, "Mcu.IP"):
                val = str(value)
                if val.startswith("I2C"):
                    self._ensure_i2c_instance(p_type, val)
                continue

            if key.endswith(".Signal") and "I2C" in self._normalize_signal_token(value):
                portpin = self._normalize_ioc_key_pin(key)
                per_sig = self._normalize_signal_token(value)
                i2c_name = self._signal_root(per_sig)
                self._ensure_i2c_instance(p_type, i2c_name)
                cfg = self.config.peripherals[p_type][i2c_name]
                pins = cfg.setdefault("Pins", {"SCL": None, "SDA": None})
                suffix = self._signal_suffix(per_sig)
                if suffix == "SCL":
                    pins["SCL"] = portpin
                if suffix == "SDA":
                    pins["SDA"] = portpin
                continue

            i2c_name = self._ioc_key_root(key)
            if not i2c_name.startswith("I2C"):
                continue

            parts = self._split_ioc_key(key)
            if len(parts) < 2:
                continue

            self._ensure_i2c_instance(p_type, i2c_name)

            prop = parts[-1]
            if "ClockSpeed" in prop:
                self.config.peripherals[p_type][i2c_name]["ClockSpeed"] = sanitize_numeric(value)
            elif "DutyCycle" in prop:
                self.config.peripherals[p_type][i2c_name]["DutyCycle"] = value
            elif "AddressingMode" in prop:
                self.config.peripherals[p_type][i2c_name]["AddressingMode"] = value
            elif "DualAddressMode" in prop:
                self.config.peripherals[p_type][i2c_name]["DualAddressMode"] = value == "ENABLE"
            elif "Timing" in prop:
                self.config.peripherals[p_type][i2c_name]["Timing"] = str(value)

    def _ensure_i2c_instance(self, p_type: str, i2c_name: str) -> None:
        """I2C 实例不存在时创建：7 位寻址，双地址和 NoStretchMode 关闭，引脚未定。
        Create the I2C instance when it does not exist: 7-bit addressing, dual address and
        NoStretchMode off, and no pins.
        """
        if not self.config.peripherals[p_type].get(i2c_name):
            self.config.peripherals[p_type][i2c_name] = {
                "ClockSpeed": None,
                "Timing": None,
                "DutyCycle": None,
                "AddressingMode": "7-bit",
                "DualAddressMode": False,
                "NoStretchMode": False,
                "DMA": {},
                "Pins": {"SCL": None, "SDA": None},
            }


# --------------------------
# CAN/FDCAN 解析器 / CAN/FDCAN Parser
# --------------------------
class CANParser(PeripheralParser):
    """读取 CAN 与 FDCAN 实例；实例名以 FDCAN 开头时归入 FDCAN，否则归入 CAN。
    Reads CAN and FDCAN instances; a name starting with FDCAN goes to FDCAN, any other to CAN.
    """

    def parse(self, p_type: str) -> None:
        """读取 CANx.* 和 FDCANx.* 属性；外设类型由实例名决定，传入的 p_type 被覆盖。
        Read CANx.* and FDCANx.* properties; the peripheral type follows the instance name and
        overrides the p_type argument.

        两类都把 CalculateBaudRate 存为 BaudRate、把 Mode 存为 Mode，其余参数交给 CAN 2.0 或
        FDCAN 专用处理。
        Both store CalculateBaudRate as BaudRate and Mode as Mode; other parameters go to the
        CAN 2.0 or the FDCAN handler.
        """
        for key, value in self.raw_map.items():
            can_name = self._ioc_key_root(key)
            if not can_name.startswith(("CAN", "FDCAN")):
                continue

            p_type = "FDCAN" if can_name.startswith("FDCAN") else "CAN"
            parts = self._split_ioc_key(key)
            if len(parts) < 2:
                continue

            self._ensure_can_instance(p_type, can_name)
            prop = parts[1]

            # 通用参数
            # Common parameters
            if "CalculateBaudRate" in prop:
                self.config.peripherals[p_type][can_name]["BaudRate"] = value
            elif "Mode" in prop:
                self.config.peripherals[p_type][can_name]["Mode"] = value

            # CAN 专用参数
            # CAN-specific parameters
            if p_type == "CAN":
                self._handle_legacy_can_params(can_name, prop, value)

            # FDCAN 专用参数
            # FDCAN-specific parameters
            if p_type == "FDCAN":
                self._handle_fdcan_params(can_name, prop, value)

    def _ensure_can_instance(self, p_type: str, can_name: str) -> None:
        """实例不存在时按类型创建默认字段。
        Create the instance with the default fields of its type when it does not exist.

        CAN 为波特率、模式、两个时间段和自动重传/自动唤醒；FDCAN 为标称预分频、标称波特率、
        帧格式和标准/扩展滤波器数量。
        CAN gets baud rate, mode, two time segments and auto retransmission/wakeup; FDCAN gets
        nominal prescaler, nominal baud rate, frame format and standard/extended filter counts.
        """
        if can_name not in self.config.peripherals[p_type]:
            defaults = {
                "CAN": {
                    "BaudRate": None,
                    "Mode": None,
                    "TimeSeg1": None,
                    "TimeSeg2": None,
                    "AutoRetransmission": True,
                    "AutoBusOff": False,
                    "AutoWakeup": False,
                },
                "FDCAN": {
                    "NominalPrescaler": None,
                    "FrameFormat": None,
                    "StdFilters": 0,
                    "ExtFilters": 0,
                },
            }
            self.config.peripherals[p_type][can_name] = defaults[p_type].copy()

    def _handle_legacy_can_params(self, can_name: str, prop: str, value: str) -> None:
        """读取 CAN 2.0 参数：BS1、BS2 原样存为 TimeSeg1、TimeSeg2；ABOM、AWUM 转为布尔值存为
        AutoBusOff、AutoWakeup；NART（禁止自动重传）取反后存为 AutoRetransmission。
        Read CAN 2.0 parameters: BS1 and BS2 are stored as is in TimeSeg1 and TimeSeg2; ABOM
        and AWUM become the booleans AutoBusOff and AutoWakeup; NART (no automatic
        retransmission) is inverted into AutoRetransmission.
        """
        param_map = {
            "BS1": "TimeSeg1",
            "BS2": "TimeSeg2",
            "ABOM": ("AutoBusOff", lambda v: v == "ENABLE"),
            "AWUM": ("AutoWakeup", lambda v: v == "ENABLE"),
            "NART": ("AutoRetransmission", lambda v: v != "ENABLE"),
        }

        if mapping := param_map.get(prop):
            if isinstance(mapping, tuple):
                key, converter = mapping
                self.config.peripherals["CAN"][can_name][key] = converter(value)
            else:
                self.config.peripherals["CAN"][can_name][mapping] = value

    def _handle_fdcan_params(self, can_name: str, prop: str, value: str) -> None:
        """读取 FDCAN 参数并转换类型；转换失败时记录警告。
        Read FDCAN parameters with type conversion; a failed conversion is logged as a warning.

        NominalPrescaler 转为浮点数；FrameFormat 为字符串；StdFiltersNbr、ExtFiltersNbr 转为
        整数，存为 StdFilters、ExtFilters。波特率由 CalculateBaudRate* 的通用处理读取。
        NominalPrescaler becomes a float; FrameFormat a string; StdFiltersNbr and ExtFiltersNbr
        integers stored as StdFilters and ExtFilters. The baud rate is read by the common
        CalculateBaudRate* handling.
        """
        param_map = {
            "NominalPrescaler": ("NominalPrescaler", float),
            "FrameFormat": ("FrameFormat", str),
            "StdFiltersNbr": ("StdFilters", int),
            "ExtFiltersNbr": ("ExtFilters", int),
        }

        if mapping := param_map.get(prop):
            key, converter = mapping
            try:
                self.config.peripherals["FDCAN"][can_name][key] = converter(value)
            except ValueError:
                logging.warning(
                    tr(
                        f"Invalid {key} value for {can_name}: {value}",
                        f"{can_name} 的 {key} 值无效：{value}",
                    )
                )


# --------------------------
# USB 解析器 / USB Parser
# --------------------------
class USBParser(PeripheralParser):
    """把 USB 相关 IP 的 .ioc 属性原样收集到 USB 类型下，带 -<profile> 后缀的参数按 profile 分组。
    Collects the .ioc properties of USB-related IPs as is under the USB type; parameters with
    a -<profile> suffix are grouped by profile.
    """

    def parse(self, p_type: str) -> None:
        """找出 USB 相关实例，并收集每个实例的全部属性。
        Find the USB-related instances and collect all properties of each.

        实例来自 Mcu.IP* 中含 USB 的 IP 名，以及以 USB.、USB_OTG_FS.、USB_OTG_HS. 开头的
        key，按 .ioc 中的出现顺序。
        The instances come from Mcu.IP* entries that contain USB and from keys starting with
        USB., USB_OTG_FS. or USB_OTG_HS., in .ioc order.

        "<param>-<profile>" 形式的参数存入 profiles[profile][param]，其余存在实例下；
        IPParameters 拆成列表。
        A "<param>-<profile>" parameter is stored in profiles[profile][param] and any other
        under the instance; IPParameters is split into a list.
        """
        # 1. 按 .ioc 中的顺序找出 raw_map 里的全部 USB 外设名。
        # 1. Find all USB peripheral names in the raw_map, in .ioc order
        usb_names = {}
        for key, value in self.raw_map.items():
            if self._ioc_key_startswith(key, "Mcu.IP") and "USB" in value:
                usb_names.setdefault(value)
            elif re.match(r"^USB(_OTG(_FS|_HS))?\.", key):
                usb_names.setdefault(self._ioc_key_root(key))

        logging.info(
            tr(
                f"[USBParser] Detected USB peripherals: {list(usb_names)}",
                f"[USBParser] 检测到的 USB 外设：{list(usb_names)}",
            )
        )

        for usb_name in usb_names:
            self._ensure_usb_instance(usb_name)
            logging.info(
                tr(
                    f"[USBParser] Parsing configuration for: {usb_name}",
                    f"[USBParser] 正在解析配置：{usb_name}",
                )
            )

            for key, value in self.raw_map.items():
                if not self._has_ioc_prefix(key, usb_name):
                    continue

                # 去掉 "USB_OTG_FS." 这类实例名前缀。
                # Remove the "USB_OTG_FS." prefix
                rest_key = key[len(usb_name) + 1 :]
                # 2.1 处理按 profile 区分的参数。
                # 2.1 Handle profile-specific parameters
                if "-" in rest_key:
                    param, profile = rest_key.split("-", 1)
                    logging.debug(
                        f"[USBParser] Profile param: {usb_name}.{param} (profile={profile}), value={value}"
                    )
                    self.config.peripherals["USB"][usb_name].setdefault("profiles", {})
                    self.config.peripherals["USB"][usb_name]["profiles"].setdefault(profile, {})
                    if param == "IPParameters":
                        self.config.peripherals["USB"][usb_name]["profiles"][profile][param] = (
                            value.split(",")
                        )
                        parameters = self.config.peripherals["USB"][usb_name]["profiles"][profile][
                            param
                        ]
                        logging.info(
                            tr(
                                f"[USBParser] IPParameters for profile={profile}: {parameters}",
                                f"[USBParser] profile={profile} 的 IPParameters：{parameters}",
                            )
                        )
                    else:
                        self.config.peripherals["USB"][usb_name]["profiles"][profile][param] = value
                else:
                    # 2.2 处理全局参数。
                    # 2.2 Handle global parameters
                    logging.debug(f"[USBParser] Global param: {usb_name}.{rest_key} = {value}")
                    if rest_key == "IPParameters":
                        self.config.peripherals["USB"][usb_name][rest_key] = value.split(",")
                        parameters = self.config.peripherals["USB"][usb_name][rest_key]
                        logging.info(
                            tr(
                                f"[USBParser] IPParameters: {parameters}",
                                f"[USBParser] IPParameters：{parameters}",
                            )
                        )
                    else:
                        self.config.peripherals["USB"][usb_name][rest_key] = value

    def _ensure_usb_instance(self, usb_name: str) -> None:
        """USB 实例不存在时创建空字典。
        Create an empty dict for the USB instance when it does not exist.
        """
        if usb_name not in self.config.peripherals["USB"]:
            self.config.peripherals["USB"][usb_name] = {}
            logging.debug(f"[USBParser] Initialized USB instance: {usb_name}")


# --------------------------
# DMA 解析器 / DMA Parser
# --------------------------
class DMAParser(PeripheralParser):
    """读取 DMA 和 BDMA 的请求与 stream 配置，并挂到对应的外设实例下。
    Reads DMA and BDMA requests and stream configurations and attaches them to the peripheral
    instances they serve.
    """

    # CubeMX DMA 配置属性到内部字段名和转换函数的映射。
    # Maps CubeMX DMA config properties to internal fields and conversion logic
    _PROPERTY_MAP = {
        "Instance": ("stream", str),
        "Direction": ("direction", lambda v: v.split("_")[-1]),
        "PeriphInc": ("periph_inc", lambda v: v == "ENABLE"),
        "MemInc": ("mem_inc", lambda v: v == "ENABLE"),
        "PeriphDataAlignment": ("periph_align", lambda v: v.split("_")[-1].lower()),
        "MemDataAlignment": ("mem_align", lambda v: v.split("_")[-1].lower()),
        "Mode": ("mode", lambda v: v.split("_")[-1].capitalize()),
        "Priority": (
            "priority",
            lambda v: v.split("_")[-1].replace("VERY", "").strip().capitalize(),
        ),
        "FIFOMode": ("fifo", lambda v: "Enabled" if "ENABLE" in v else "Disabled"),
    }

    def parse(self, p_type: str) -> None:
        """依次解析 Dma. 与 Bdma. 前缀下的请求和配置，然后把配置挂到外设实例下。
        Parse the requests and configurations under the Dma. and Bdma. prefixes, then attach
        the configurations to the peripheral instances.
        """
        for prefix, dma_type in (("Dma", "DMA"), ("Bdma", "BDMA")):
            self._parse_requests(prefix, dma_type)
            self._parse_configs(prefix, dma_type)
        self._link_configs()

    def _parse_requests(self, prefix="Dma", dma_type="DMA") -> None:
        """记录每个 <prefix>.RequestN 条目：请求 key 到目标外设信号的映射，以及 DMA 类型。
        Record each <prefix>.RequestN entry: the mapping from the request key to the target
        peripheral signal, and the DMA type.
        """
        for key, value in self.raw_map.items():
            req_id = self._dma_request_id(key, prefix)
            if req_id is not None:
                request_key = self._dma_request_key(prefix, req_id)
                # 请求目标外设以字符串保存。
                # Store peripheral as string
                self.config.dma_requests[request_key] = value
                # 记录 DMA 类型（DMA 或 BDMA）。
                # Store DMA type (DMA or BDMA)
                self.config.dma_types[request_key] = dma_type

    def _parse_configs(self, prefix="Dma", dma_type="DMA") -> None:
        """读取 <prefix>.<外设>.<N>[.<属性>] 条目，为每个外设与请求号生成一份结构化配置，
        存入 dma_configs["<外设>_<N>"]。
        Read <prefix>.<Periph>.<N>[.<Prop>] entries and build one structured configuration
        per peripheral and request number, stored in dma_configs["<Periph>_<N>"].

        配置包含请求号、请求目标、DMA 类型、stream 和经 _PROPERTY_MAP 转换的属性；有 Direction
        时另存完整方向 direction_full。转换失败的属性记录警告后跳过。
        A configuration holds the request number, the request target, the DMA type, the stream
        and the properties converted through _PROPERTY_MAP; a Direction property also gives
        direction_full, the complete direction. A property that fails to convert is logged as
        a warning and skipped.
        """
        config_map = defaultdict(dict)
        for key, value in self.raw_map.items():
            # 只处理格式为 <prefix>.<Periph>.<ReqID>[.<Prop>] 的 key。
            # Only process keys of format <prefix>.<Periph>.<ReqID>[.<Prop>]
            if not self._ioc_root_startswith(key, prefix):
                continue
            parts = self._split_ioc_key(key)
            if len(parts) < 3 or parts[0] != prefix:
                continue
            peripheral = parts[1]
            req_id = parts[2]
            if not req_id.isdigit():
                continue
            prop = parts[3] if len(parts) > 3 else "Instance"
            config_key = f"{peripheral}_{req_id}"
            config_map[config_key][prop] = value
            config_map[config_key]["_request_id"] = req_id

        # 把识别出的属性映射、转换成结构化的字典。
        # Map and convert all recognized properties into a structured dictionary
        for config_key, props in config_map.items():
            req_id = props.get("_request_id", "")
            request_key = self._dma_request_key(prefix, req_id)
            dma_type = self.config.dma_types.get(request_key, "DMA")
            structured = {
                "request_id": req_id,
                "peripheral": self.config.dma_requests.get(request_key, "Unknown"),
                "dma_type": dma_type,
                "stream": props.get("Instance", ""),
            }
            # 其余属性按 _PROPERTY_MAP 转换。
            # Convert all other properties using the property map
            for cube_prop, (field, converter) in self._PROPERTY_MAP.items():
                if cube_prop in props:
                    try:
                        structured[field] = converter(props[cube_prop])
                    except Exception as e:
                        logging.warning(
                            tr(
                                f"DMA property conversion failed for {config_key}.{cube_prop}: "
                                f"{str(e)}",
                                f"{config_key}.{cube_prop} 的 DMA 属性转换失败：{str(e)}",
                            )
                        )
            if "Direction" in props:
                structured["direction_full"] = self._normalize_dma_direction(props["Direction"])
            self.config.dma_configs[config_key] = structured

    def _link_configs(self) -> None:
        """把每份 DMA 配置挂到请求目标对应的外设实例下。
        Attach each DMA configuration to the peripheral instance its request targets.

        依次在 SPI、I2C、USART、LPUART、ADC、TIM 中查找同名实例，把配置存入其 dma 字典的
        dma_<方向> 或 dma 键；方向为 tx/rx 时另设 DMA_TX/DMA_RX 为 ENABLE 并记录 DMA 类型，
        供生成缓冲区使用。
        The instance is looked up in SPI, I2C, USART, LPUART, ADC and TIM in that order, and
        the configuration goes into its dma dict under dma_<direction> or dma; a tx/rx
        direction also sets DMA_TX/DMA_RX to ENABLE and records the DMA type for buffer
        generation.
        """
        for cfg in self.config.dma_configs.values():
            peripheral_full = cfg["peripheral"]
            dma_type = cfg.get("dma_type", "DMA")
            p_name, direction = self._parse_dma_request_endpoint(peripheral_full)
            # 依次在各外设类型中查找该外设。
            # Search for the peripheral in all possible types
            for p_type in ["SPI", "I2C", "USART", "LPUART", "ADC", "TIM"]:
                if p_name in self.config.peripherals.get(p_type, {}):
                    dir_key = f"dma_{direction}" if direction != "general" else "dma"
                    if "dma" not in self.config.peripherals[p_type][p_name]:
                        self.config.peripherals[p_type][p_name]["dma"] = {}
                    # 保存带 DMA 类型标记的 DMA 配置。
                    # Store DMA config with type marking
                    self.config.peripherals[p_type][p_name]["dma"][dir_key] = cfg
                    # 自动把 DMA_TX/DMA_RX 设为 ENABLE，供生成缓冲区使用。
                    # Automatically enable DMA_TX/DMA_RX flags for buffer generation
                    if direction == "tx":
                        self.config.peripherals[p_type][p_name]["DMA_TX"] = "ENABLE"
                        self.config.peripherals[p_type][p_name]["DMA_TX_TYPE"] = dma_type
                    elif direction == "rx":
                        self.config.peripherals[p_type][p_name]["DMA_RX"] = "ENABLE"
                        self.config.peripherals[p_type][p_name]["DMA_RX_TYPE"] = dma_type
                    break  # 找到后停止查找 / Stop searching once found


class ThreadXParser(PeripheralParser):
    """读取 ThreadX（Azure RTOS）的内存池大小、内存分配方式、内核选择和任务栈大小。
    Reads ThreadX (Azure RTOS) memory pool size, allocation method, core selection and task
    stack sizes.
    """

    def parse(self, p_type: str) -> None:
        """读取 ThreadX 条目写入 threadx_config。
        Read ThreadX entries into threadx_config.

        以 TX_APP_MEM_POOL_SIZE 结尾的 key 为 MemPoolSize；以 AZRTOS_APP_MEM_ALLOCATION_METHOD
        结尾的 key 为 AllocationMethod（1 为 Static，0 为 Dynamic）；含
        ThreadXCcRTOSJjThreadXJjCore 的 key（ThreadX Core 组件选择）为布尔值 CorePresent；
        AZRTOS.ThreadX.<任务>.StackSize 为任务栈大小。大小值带 B 后缀。
        A key ending in TX_APP_MEM_POOL_SIZE gives MemPoolSize; one ending in
        AZRTOS_APP_MEM_ALLOCATION_METHOD gives AllocationMethod (1 is Static, 0 Dynamic); a key
        containing ThreadXCcRTOSJjThreadXJjCore, the ThreadX Core component selection, gives
        the boolean CorePresent; AZRTOS.ThreadX.<task>.StackSize gives a task stack size. Sizes
        carry a B suffix.
        """
        for key, value in self.raw_map.items():
            if key.endswith("TX_APP_MEM_POOL_SIZE"):
                self.config.threadx_config["MemPoolSize"] = f"{sanitize_numeric(value)}B"

            elif key.endswith("AZRTOS_APP_MEM_ALLOCATION_METHOD"):
                method_map = {
                    "1": "Static",
                    "0": "Dynamic",
                }
                self.config.threadx_config["AllocationMethod"] = method_map.get(value, value)

            elif "ThreadXCcRTOSJjThreadXJjCore" in key:
                self.config.threadx_config["CorePresent"] = value.lower() == "true"

            elif self._has_ioc_prefix(key, "AZRTOS.ThreadX") and key.endswith(".StackSize"):
                parts = self._split_ioc_key(key)
                if len(parts) == 3:
                    task = parts[1]
                    self.config.threadx_config["Tasks"][task] = {
                        "StackSize": f"{sanitize_numeric(value)}B"
                    }


# --------------------------
# 看门狗解析器 / Watchdog Parser
# --------------------------
class WatchdogParser(PeripheralParser):
    """读取 IWDG 与 WWDG 实例的启用状态、预分频、重载值、窗口和计数值。
    Reads IWDG and WWDG instances: enable state, prescaler, reload, window and counter.
    """

    def parse(self, p_type: str) -> None:
        """读取看门狗条目，数值属性转为数值，Enable 转为布尔值 Enabled。
        Read watchdog entries; numeric properties become numbers and Enable becomes the
        boolean Enabled.

        VP_IWDG*_VS_IWDG.Mode 为 IWDG_Activate 时启用对应 IWDG 实例；IWDGx.* 读取 Prescaler、
        Reload、Window 和 Enable；WWDGx.* 读取 Prescaler、Window、Counter 和 Enable。
        VP_IWDG*_VS_IWDG.Mode set to IWDG_Activate enables that IWDG instance; IWDGx.* gives
        Prescaler, Reload, Window and Enable; WWDGx.* gives Prescaler, Window, Counter and
        Enable.
        """
        for key, value in self.raw_map.items():
            # 独立看门狗 IWDG
            # IWDG
            if (
                self._ioc_root_startswith(key, "VP_IWDG")
                and ".Mode" in key
                and value == "IWDG_Activate"
            ):
                # key 通常为 VP_IWDG_VS_IWDG.Mode；没有编号时归入实例 IWDG。
                # The key is usually VP_IWDG_VS_IWDG.Mode; no number means instance IWDG.
                match = re.match(r"VP_(IWDG\d*)_VS_IWDG\.Mode", key)
                if match:
                    # group(1) 为 "IWDG1"、"IWDG2" 或 ""。
                    # group(1) is "IWDG1", "IWDG2" or "".
                    wdg_name = match.group(1) or "IWDG"
                    if not wdg_name:
                        wdg_name = "IWDG"
                    self._ensure_wdg_instance("IWDG", wdg_name)
                    self.config.peripherals["IWDG"][wdg_name]["Enabled"] = True
            elif self._ioc_key_root(key).startswith("IWDG"):
                iwdg_name = self._ioc_key_root(key)  # IWDG 或 IWDG1 / IWDG or IWDG1
                self._ensure_wdg_instance("IWDG", iwdg_name)
                prop = self._ioc_key_prop(key)

                if prop == "Prescaler":
                    self.config.peripherals["IWDG"][iwdg_name]["Prescaler"] = sanitize_numeric(
                        value
                    )
                elif prop == "Reload":
                    self.config.peripherals["IWDG"][iwdg_name]["Reload"] = sanitize_numeric(value)
                elif prop == "Window":
                    self.config.peripherals["IWDG"][iwdg_name]["Window"] = sanitize_numeric(value)
                elif prop == "Enable":
                    self.config.peripherals["IWDG"][iwdg_name]["Enabled"] = value == "ENABLE"

            # 窗口看门狗 WWDG
            # WWDG
            elif self._ioc_key_root(key).startswith("WWDG"):
                wwdg_name = self._ioc_key_root(key)
                self._ensure_wdg_instance("WWDG", wwdg_name)
                prop = self._ioc_key_prop(key)

                if prop == "Prescaler":
                    self.config.peripherals["WWDG"][wwdg_name]["Prescaler"] = sanitize_numeric(
                        value
                    )
                elif prop == "Window":
                    self.config.peripherals["WWDG"][wwdg_name]["Window"] = sanitize_numeric(value)
                elif prop == "Counter":
                    self.config.peripherals["WWDG"][wwdg_name]["Counter"] = sanitize_numeric(value)
                elif prop == "Enable":
                    self.config.peripherals["WWDG"][wwdg_name]["Enabled"] = value == "ENABLE"

    def _ensure_wdg_instance(self, wdg_type: str, wdg_name: str) -> None:
        """wdg_type 类型下没有该实例时创建空字典。
        Create an empty dict for the instance under wdg_type when it does not exist.
        """
        if wdg_name not in self.config.peripherals[wdg_type]:
            self.config.peripherals[wdg_type][wdg_name] = {}


# --------------------------
# FreeRTOS 解析器 / FreeRTOS Parser
# --------------------------
class FreeRTOSParser(PeripheralParser):
    """读取 FreeRTOS 的任务、堆大小和 INCLUDE_ 功能开关。
    Reads FreeRTOS tasks, heap size and INCLUDE_ feature flags.
    """

    def parse(self, p_type: str) -> None:
        """读取 FREERTOS.* 条目：Tasks* 为任务定义，configTOTAL_HEAP_SIZE（或含 HeapSize 的
        key）为堆大小（带 B 后缀），key 含 INCLUDE_ 时为功能开关；有任何条目时 Enabled 为真。
        Read FREERTOS.* entries: Tasks* holds task definitions, configTOTAL_HEAP_SIZE (or a
        key containing HeapSize) the heap size with a B suffix, and a key containing INCLUDE_ a
        feature flag; any entry makes Enabled true.
        """
        for key, value in self.raw_map.items():
            if not self._ioc_root_startswith(key, "FREERTOS"):
                continue

            parts = self._split_ioc_key(key)
            if len(parts) < 2:
                continue
            self.config.freertos_config["Enabled"] = True
            if parts[1].startswith("Tasks"):
                self._process_task_configuration(value)
            elif parts[1] == "configTOTAL_HEAP_SIZE" or "HeapSize" in key:
                self.config.freertos_config["Heap"] = f"{sanitize_numeric(value)}B"
            elif "INCLUDE_" in key:
                self._process_feature_flag(parts[1], value)

    def _process_task_configuration(self, task_data: str) -> None:
        """读取任务定义：任务之间用分号分隔，每个任务按逗号拆分并去掉空项和 NULL，至少五项时
        记录。
        Read task definitions: tasks are separated by semicolons, each is split at commas with
        empty and NULL items dropped, and recorded when at least five items remain.

        前五项依次为任务名、优先级、栈大小（带 B 后缀）、入口函数和类型。
        The first five items are the task name, priority, stack size with a B suffix, entry
        function and type.
        """
        for task in task_data.split(";"):
            elements = [x for x in task.split(",") if x and x != "NULL"]
            if len(elements) >= 5:
                self.config.freertos_config["Tasks"][elements[0]] = {
                    "Priority": elements[1],
                    "StackSize": f"{elements[2]}B",
                    "EntryFunction": elements[3],
                    "Type": elements[4],
                }

    def _process_feature_flag(self, feature: str, state: str) -> None:
        """记录一个 FreeRTOS 功能开关；CubeMX 写 1 或 ENABLE 时为 True。
        Record one FreeRTOS feature flag; True when CubeMX writes 1 or ENABLE.
        """
        self.config.freertos_config["Features"][feature] = state in ("1", "ENABLE")


# --------------------------
# 解析主流程 / Core Parsing Workflow
# --------------------------
def parse_ioc_file(ioc_path: str) -> dict[str, Any] | None:
    """解析一个 .ioc 文件，返回清理后的配置结构；读取或解析失败时记录错误并返回 None。
    Parse one .ioc file and return the cleaned configuration; a read or parse failure is
    logged as an error and gives None.

    依次读取时基（NVIC.TimeBaseIP、NVIC.TimeBase）和 GPIO，运行各外设解析器，最后按 DMA
    请求设置对应外设的 DMA 开关。
    The timebase (NVIC.TimeBaseIP, NVIC.TimeBase) and GPIO are read first, then each
    peripheral parser runs, and finally the DMA flags of the peripherals named by DMA
    requests are set.
    """
    config = ConfigurationManager()

    try:
        with open(ioc_path, encoding="utf-8") as f:
            raw_map = _extract_key_value_pairs(f)
    except (OSError, UnicodeDecodeError) as e:
        logging.error(tr(f"File processing failed: {str(e)}", f"文件处理失败：{str(e)}"))
        return None

    # 解析时基的特殊字段。
    # Timebase special fields parsing
    for key, value in raw_map.items():
        if key.startswith("NVIC.TimeBaseIP"):
            config.timebase["Source"] = value
        elif key.startswith("NVIC.TimeBase"):
            config.timebase["IRQ"] = value
    _check_timebase(raw_map, config.timebase)

    # 创建全部解析器。
    # Instantiate all parsers
    parsers = [
        ThreadXParser(config, raw_map),
        FreeRTOSParser(config, raw_map),
        McuParser(config, raw_map),
        TIMParser(config, raw_map),
        ADCParser(config, raw_map),
        DACParser(config, raw_map),
        SPIParser(config, raw_map),
        USARTParser(config, raw_map),
        I2CParser(config, raw_map),
        CANParser(config, raw_map),
        USBParser(config, raw_map),
        WatchdogParser(config, raw_map),
        DMAParser(config, raw_map),
    ]

    # 执行解析流程。
    # Execute parsing workflow
    try:
        # 阶段 1：通用的 GPIO 解析。
        # Phase 1: Common GPIO parsing
        parsers[0].parse_gpio()  # 各解析器都继承 GPIO 解析 / All parsers inherit GPIO capability

        # 阶段 2：各外设专用的解析。
        # Phase 2: Peripheral-specific parsing
        for parser in parsers:
            if isinstance(parser, McuParser):
                parser.parse("Mcu")
            else:
                # 去掉类名末尾的 'Parser'。
                # Strip 'Parser' suffix
                parser.parse(parser.__class__.__name__[:-6])

        # 阶段 3：后处理。
        # Phase 3: Post-processing
        _link_dma_requests(config)

        return config.clean_structure()
    except Exception as e:
        logging.error(tr(f"Parsing failed: {str(e)}", f"解析失败：{str(e)}"))
        return None


def _check_timebase(raw_map: dict[str, str], timebase: dict[str, str | None]) -> None:
    """HAL 时基仍是 SysTick，或其定时器中断的抢占优先级不是最高（0）时，记录警告。
    Warn when the HAL timebase is still SysTick, or when the preemption priority of its timer
    interrupt is not the highest (0).
    """
    source = timebase.get("Source") or "SysTick"
    if source == "SysTick":
        logging.warning(
            tr(
                "The HAL timebase is SysTick. In STM32CubeMX, set SYS > Timebase Source to a "
                "general-purpose timer (such as TIM6) and give its interrupt the highest "
                "preemption priority (0) in NVIC.",
                "HAL 时基仍是 SysTick。请在 STM32CubeMX 的 SYS 中把 Timebase Source 改为普通"
                "定时器（例如 TIM6），并在 NVIC 中把它的中断抢占优先级设为最高（0）。",
            )
        )
        return
    irq = timebase.get("IRQ")
    # NVIC.<IRQ> 的值形如 true\:5\:0\:...，第二项是抢占优先级。
    # NVIC.<IRQ> reads like true\:5\:0\:..., the second field being the preemption priority.
    fields = raw_map.get(f"NVIC.{irq}", "").replace("\\:", ":").split(":")
    if len(fields) > 1 and fields[1] != "0":
        logging.warning(
            tr(
                f"The HAL timebase interrupt {irq} ({source}) has preemption priority "
                f"{fields[1]}. In STM32CubeMX, set it to the highest (0) in NVIC.",
                f"HAL 时基中断 {irq}（{source}）的抢占优先级为 {fields[1]}。请在 STM32CubeMX "
                "的 NVIC 中把它设为最高（0）。",
            )
        )


def _extract_key_value_pairs(file_handler: TextIO) -> dict[str, str]:
    """把 .ioc 文本读成 key=value 表，key 和值去掉首尾空白。
    Read .ioc text into a key=value map, with keys and values stripped.

    跳过空行和以 # 开头的行；key 中转义的 # 连同反斜杠一起去掉；没有 = 的行记录警告后跳过。
    Empty lines and lines starting with # are skipped; an escaped # in a key is removed
    together with its backslash; a line without = is logged as a warning and skipped.
    """
    raw_map = {}
    for line_num, line in enumerate(file_handler, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        try:
            key, value = map(str.strip, line.split("=", 1))
            raw_map[key.replace("\\#", "")] = value
        except ValueError:
            logging.warning(
                tr(
                    f"Ignored malformed entry at line {line_num}: {line}",
                    f"忽略第 {line_num} 行格式错误的条目：{line}",
                )
            )

    return raw_map


def _link_dma_requests(config: ConfigurationManager) -> None:
    """按每个 DMA 请求的目标，把 USART、SPI、ADC、I2C 中同名实例的 DMA_<方向> 设为 ENABLE。
    For each DMA request target, set DMA_<DIRECTION> to ENABLE on the USART, SPI, ADC and I2C
    instances of that name.

    请求目标没有方向后缀时设置的字段为 DMA。
    A request target without a direction suffix sets the field DMA.
    """
    for peripheral in config.dma_requests.values():
        p_name, direction = PeripheralParser._parse_dma_request_endpoint(peripheral)
        direction_key = f"DMA_{direction.upper()}" if direction != "general" else "DMA"

        for p_type in ["USART", "SPI", "ADC", "I2C"]:
            if p_name in config.peripherals[p_type]:
                config.peripherals[p_type][p_name][direction_key] = "ENABLE"


# --------------------------
# 输出生成 / Output Generation
# --------------------------
def save_to_yaml(data: dict[str, Any], output_path: str = "parsed_ioc.yaml") -> bool:
    """把配置写成 YAML 文件，首行为生成文件说明注释，键保持插入顺序；成功时为 True。
    Write the configuration as a YAML file with a generated-file comment on the first line and
    keys in insertion order; True on success.

    写入或序列化失败时记录错误并返回 False。
    A write or serialization failure is logged as an error and gives False.
    """
    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "# Generated by `libxr parse` from the CubeMX .ioc file; do not edit by hand.\n"
            )
            yaml.dump(
                data,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                indent=2,
            )
        logging.info(
            tr(f"Configuration exported to: {output_path}", f"配置已导出到：{output_path}")
        )
        return True
    except (OSError, yaml.YAMLError) as e:
        logging.error(tr(f"YAML export failed: {str(e)}", f"YAML 导出失败：{str(e)}"))
        return False


def print_summary(data: dict[str, Any]) -> None:
    """向标准输出打印配置摘要：MCU、GPIO 输出/输入/外部中断数量、各外设实例和看门狗。
    Print a configuration summary to standard output: the MCU, GPIO output/input/external
    interrupt counts, each peripheral instance and the watchdogs.
    """
    print(tr("\n===== [Configuration Summary] =====", "\n===== [配置摘要] ====="))

    # MCU 信息
    # MCU Info
    mcu = data.get("Mcu", {})
    family = mcu.get("Family") or tr("Unknown", "未知")
    print(tr(f"\nMCU: {family} {mcu.get('Type', '')}", f"\nMCU：{family} {mcu.get('Type', '')}"))

    # GPIO 摘要
    # GPIO Summary
    gpio = data.get("GPIO", {})
    outputs = sum(1 for c in gpio.values() if c.get("Signal") == "GPIO_Output")
    inputs = sum(1 for c in gpio.values() if c.get("Signal") == "GPIO_Input")
    interrupts = sum(1 for c in gpio.values() if c.get("GPXTI"))
    print(tr(f"\nGPIO ({len(gpio)} pins):", f"\nGPIO（{len(gpio)} 个引脚）："))
    print(tr(f"  Outputs: {outputs}", f"  输出：{outputs}"))
    print(tr(f"  Inputs: {inputs}", f"  输入：{inputs}"))
    print(tr(f"  External Interrupts: {interrupts}", f"  外部中断：{interrupts}"))

    # 外设摘要
    # Peripheral Summary
    print(tr("\nActive Peripherals:", "\n已启用的外设："))
    for p_type, group in data.get("Peripherals", {}).items():
        print(tr(f"  {p_type}: {len(group)} instance(s)", f"  {p_type}：{len(group)} 个实例"))
        for name, cfg in group.items():
            print(f"    {name}: {_format_peripheral_config(p_type, cfg)}")

    iwdgs = data.get("Peripherals", {}).get("IWDG", {})
    wwdgs = data.get("Peripherals", {}).get("WWDG", {})
    if iwdgs or wwdgs:
        print(tr("\nWatchdogs:", "\n看门狗："))
        for k, v in iwdgs.items():
            print(
                f"  {k}: Enabled={v.get('Enabled', False)}, Prescaler={v.get('Prescaler')}, Reload={v.get('Reload')}"
            )
        for k, v in wwdgs.items():
            print(
                f"  {k}: Enabled={v.get('Enabled', False)}, Prescaler={v.get('Prescaler')}, Window={v.get('Window')}, Counter={v.get('Counter')}"
            )


def _format_peripheral_config(p_type: str, config: dict) -> str:
    """外设实例的一行摘要：TIM 为模式和周期，ADC 为规则转换通道数，DAC 为通道列表，SPI、
    I2C、USART 为波特率或时钟速度，其他类型为空字符串。
    A one-line summary of a peripheral instance: mode and period for TIM, the number of
    regular conversion channels for ADC, the channel list for DAC, the baud rate or clock
    speed for SPI, I2C and USART, and an empty string for other types.
    """
    if p_type == "TIM":
        return f"Mode={config.get('Mode')} | Period={config.get('Period')}"
    elif p_type == "ADC":
        return f"Channels={len(config.get('RegularConversions', []))}"
    elif p_type == "DAC":
        chs = config.get("Channels", {})
        return f"Channels={list(chs.keys())}" if chs else "Channels=0"
    elif p_type in ["SPI", "I2C", "USART"]:
        return f"Baud={config.get('BaudRate') or config.get('ClockSpeed')}"
    return ""


# --------------------------
# 工程入口 / Project Entry
# --------------------------
def parse_project(directory: str, output: str | None = None, summary: bool = True) -> None:
    """解析 directory 中唯一的 .ioc 文件并写出 YAML；summary 为真时打印摘要。
    Parse the single .ioc file in directory and write the YAML; print a summary when summary is
    set.

    输出路径默认为该目录下的 .config.yaml。目录不存在、没有 .ioc 文件或有多个 .ioc 文件时
    以状态 1 退出。
    The output defaults to .config.yaml in that directory. A missing directory, no .ioc file
    or several .ioc files exit with status 1.
    """
    if not os.path.isdir(directory):
        logging.error(tr(f"Invalid input directory: {directory}", f"无效的输入目录：{directory}"))
        sys.exit(1)

    ioc_files = sorted(f for f in os.listdir(directory) if f.endswith(".ioc"))
    if not ioc_files:
        logging.error(tr("No .ioc files found in target directory", "目标目录中没有 .ioc 文件"))
        sys.exit(1)
    if len(ioc_files) > 1:
        # 一个目录对应一个 CubeMX 工程；多个 .ioc 会写进同一个输出文件。
        # One directory is one CubeMX project; several .ioc files would share the output.
        logging.error(
            tr(
                f"{directory} holds several .ioc files ({', '.join(ioc_files)}); "
                "run `libxr parse` on a directory with one CubeMX project",
                f"{directory} 中有多个 .ioc 文件（{'、'.join(ioc_files)}）；"
                "请对只含一个 CubeMX 工程的目录运行 `libxr parse`",
            )
        )
        sys.exit(1)

    ioc_file = ioc_files[0]
    logging.info(tr(f"Processing {ioc_file}...", f"正在处理 {ioc_file}……"))
    config_data = parse_ioc_file(os.path.join(directory, ioc_file))
    output_path = output or os.path.join(directory, ".config.yaml")
    if not config_data or not save_to_yaml(config_data, output_path):
        sys.exit(1)
    if summary:
        print_summary(config_data)


if __name__ == "__main__":
    from libxr.legacy import run

    raise SystemExit(run("xr_parse_ioc"))
