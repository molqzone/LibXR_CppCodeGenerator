#!/usr/bin/env python
"""libxr gen 的 STM32 生成器：由 libxr parse 写出的 CubeMX 工程 YAML 生成 LibXR 的 app_main 源文件。
The STM32 generator of libxr gen: generate the LibXR app_main source file from the CubeMX
project YAML that libxr parse writes.

同时生成 app_main.h 和 flash_map.hpp，并更新 libxr_config.yaml；已有 app_main 源文件中 User Code
区域的内容被保留。
It also generates app_main.h and flash_map.hpp and updates libxr_config.yaml; the User Code
bodies of an existing app_main source file are kept.
"""

import copy
import logging
import math
import os
import re
import sys
import urllib.request

import yaml
from xr_syntax.cpp import CppDocument, identifier_occurrences
from xr_syntax.i18n import tr

from libxr import libxr_config_file, update_notice
from libxr.libxr_config_file import LibXRConfigError

# --------------------------
# 全局配置 / Global Configuration
# --------------------------
# 生成的设备对象及其 LibXR 接口，用 XR_REGISTER 登记。
# Generated device objects and their LibXR interface, registered with XR_REGISTER.
registered_devices = {"power_manager": "PowerManager"}
# 每个登记的名字由什么产生，用于冲突诊断。
# What produced each registered name, for collision diagnostics.
registered_origins = {}
# 生效设置的默认值；每次生成都从这里重新开始，再合并 libxr_config.yaml。
# Defaults of the effective settings; every generation starts again from here and then merges
# libxr_config.yaml.
DEFAULT_SETTINGS = {
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
        "max_history_number": 5,
    },
    "SYSTEM": "None",
}
# 生效的设置：DEFAULT_SETTINGS 合并 libxr_config.yaml，生成过程中再补上缺少的默认值。
# The effective settings: DEFAULT_SETTINGS merged with libxr_config.yaml, completed with
# missing defaults during generation.
libxr_settings = copy.deepcopy(DEFAULT_SETTINGS)
# 已加载的 libxr_config.yaml 的往返文档（含注释和用户的键）。
# Round-trip document of the loaded libxr_config.yaml (comments, user keys).
libxr_config_document = None
# 从 URL 下载 libxr_config.yaml 的时限（秒）。
# Time limit in seconds for downloading libxr_config.yaml from a URL.
CONFIG_DOWNLOAD_TIMEOUT = 30


# --------------------------
# 配置初始化 / Configuration Initialization
# --------------------------
def reset_settings() -> None:
    """把生效的设置恢复为 DEFAULT_SETTINGS，并丢掉已加载的 libxr_config.yaml 文档，使同一进程中
    的下一次生成不带上一次的设置。
    Restore the effective settings to DEFAULT_SETTINGS and drop the loaded libxr_config.yaml
    document, so the next generation in the same process carries nothing over.
    """
    global libxr_config_document
    libxr_settings.clear()
    libxr_settings.update(copy.deepcopy(DEFAULT_SETTINGS))
    libxr_config_document = None


def initialize_registry(use_xrobot: bool) -> None:
    """清空生成对象的登记表；use_xrobot 为真时先登记 power_manager（PowerManager）。
    Reset the registry of generated objects; with use_xrobot, power_manager (PowerManager) is
    registered first.
    """
    registered_devices.clear()
    registered_origins.clear()
    if use_xrobot:
        _register_device("power_manager", "PowerManager", tr("power manager", "电源管理器"))


# --------------------------
# 设备登记 / Device Registration
# --------------------------
def _register_device(name: str, dev_type: str, origin: str = ""):
    """登记一个生成的对象及其 LibXR 接口类型；每个名字只登记一种类型。
    Record one generated object with its LibXR interface type; one name has exactly one
    registered type.

    Args:
        origin: 冲突信息中对该对象的描述；为空时为 "<dev_type> object"。
            How collision messages describe the object; "<dev_type> object" when empty.

    Raises:
        ValueError: 该名字已经登记。
            The name is already registered.
    """
    origin = origin or tr(f"{dev_type} object", f"{dev_type} 对象")
    if name in registered_devices:
        existing = registered_origins.get(name, registered_devices[name])
        raise ValueError(
            tr(
                f"Generated name '{name}' ({origin}) collides with the existing "
                f"'{name}' ({existing}); every generated object needs its own name",
                f"生成的名字 '{name}'（{origin}）与已有的 '{name}'（{existing}）冲突；"
                "每个生成的对象都需要自己的名字",
            )
        )
    registered_devices[name] = dev_type
    registered_origins[name] = origin


def _generate_fdcan_can_alias(instance: str) -> str:
    """让 FDCAN 对象也能通过经典 CAN 接口使用，返回声明引用 canN 的 C++ 行。
    Expose an FDCAN object under the classic CAN interface as well and return the C++ line
    that declares the reference canN.

    fdcanN 仍登记为 LibXR::FDCAN；引用 canN 以 LibXR::CAN 指向同一对象，因此每个登记的名字只有
    一种类型。
    fdcanN stays registered as LibXR::FDCAN; the reference canN names the
    same object as LibXR::CAN, so each registered name keeps one type.

    Raises:
        ValueError: 实例名不是 fdcan<N> 形式，或 canN 已经登记。
            The instance name is not of the form fdcan<N>, or canN is already registered.
    """
    fdcan_name = instance.lower()
    match = re.fullmatch(r"fdcan(\d+)", fdcan_name)
    if match is None:
        raise ValueError(
            tr(
                f"Cannot derive the CAN alias of FDCAN instance '{instance}'",
                f"无法推导 FDCAN 实例 '{instance}' 的 CAN 别名",
            )
        )
    can_name = f"can{match.group(1)}"
    _register_device(
        can_name,
        "CAN",
        tr(f"LibXR::CAN alias of {fdcan_name}", f"{fdcan_name} 的 LibXR::CAN 别名"),
    )
    return f"  LibXR::CAN& {can_name} = {fdcan_name};\n"


# --------------------------
# 外设实例生成 / Peripheral Instance Generation
# --------------------------
def generate_peripheral_instances(project_data: dict, use_xrobot: bool = False) -> str:
    """生成所有外设对象的构造代码，按 ADC、PWM、其他外设的顺序拼接。
    Generate the construction code of all peripheral objects, joined in the order ADC, PWM,
    then the other peripherals.

    启用 XRobot 时每个 FDCAN 对象另有一个 LibXR::CAN 引用；没有生成方法的外设类型不产生代码。
    With XRobot each FDCAN object also gets a LibXR::CAN reference; peripheral types without a
    generator method produce no code.
    """
    code_sections = {"adc": [], "pwm": [], "main": []}

    for p_type, instances in project_data.get("Peripherals", {}).items():
        for instance_name, config in instances.items():
            section, code = PeripheralFactory.create(p_type, instance_name, config)
            if use_xrobot and p_type.upper() == "FDCAN" and code:
                code += _generate_fdcan_can_alias(instance_name)
            if section in code_sections:
                code_sections[section].append(code)

    # 按正确的顺序拼接代码：ADC 配置 -> PWM -> 其他外设（main 段）
    # Assemble code in correct order: ADC config -> PWM -> Main peripherals
    return "\n".join(
        [
            "\n".join(code_sections["adc"]),
            "\n".join(code_sections["pwm"]),
            "\n".join(code_sections["main"]),
        ]
    )


# --------------------------
# 配置加载 / Configuration Loading
# --------------------------
def load_configuration(file_path: str) -> dict:
    """读取工程 YAML，检查必需的 Mcu、GPIO 和 Peripherals 段，并返回其内容。
    Read the project YAML, check the required Mcu, GPIO and Peripherals sections and return
    its content.

    同时按 FreeRTOS 或 ThreadX 段设置 libxr_settings 的 SYSTEM，并删除空的外设条目。文件不存在、
    YAML 语法错误、内容不是映射或缺少必需段时记录错误并以状态 1 退出。
    It also sets SYSTEM in libxr_settings from the FreeRTOS or ThreadX section and deletes
    empty peripheral entries. A missing file, a YAML syntax error, content that is not a
    mapping or a missing section logs an error and exits with status 1.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

            if not isinstance(config, dict):
                raise ValueError(
                    tr(
                        f"{file_path} must contain a YAML mapping written by `libxr parse`",
                        f"{file_path} 的内容必须是 `libxr parse` 写出的 YAML 映射",
                    )
                )

            # 基本的结构检查
            # Basic schema validation
            required_sections = ["Mcu", "GPIO", "Peripherals"]
            for section in required_sections:
                if section not in config:
                    raise ValueError(
                        tr(
                            f"Missing required section: {section}",
                            f"缺少必需的段：{section}",
                        )
                    )

            # 检测 RTOS
            # Detect RTOS
            if "FreeRTOS" in config:
                libxr_settings["SYSTEM"] = "FreeRTOS"
                logging.info(tr("System: FreeRTOS", "系统：FreeRTOS"))
            elif "ThreadX" in config:
                libxr_settings["SYSTEM"] = "ThreadX"
                logging.info(tr("System: ThreadX", "系统：ThreadX"))
            else:
                libxr_settings["SYSTEM"] = "None"
                logging.info(tr("System: bare metal", "系统：裸机"))

            for key in [k for k, v in config["Peripherals"].items() if not v]:
                logging.info(
                    tr(f"Skipping empty peripheral config: {key}", f"跳过空的外设配置：{key}")
                )
                del config["Peripherals"][key]

            return config
    except FileNotFoundError:
        logging.error(
            tr(f"Configuration file not found: {file_path}", f"找不到配置文件：{file_path}")
        )
        sys.exit(1)
    except yaml.YAMLError as e:
        logging.error(tr(f"YAML syntax error: {str(e)}", f"YAML 语法错误：{str(e)}"))
        sys.exit(1)
    except ValueError as e:
        logging.error(tr(f"Configuration validation failed: {str(e)}", f"配置校验失败：{str(e)}"))
        sys.exit(1)


# --------------------------
# 库配置 / Library Configuration
# --------------------------
def load_libxr_config(output_dir: str, config_source: str) -> None:
    """把 output_dir 中的 libxr_config.yaml（或 --libxr-config 给出的路径或 URL）合并进生效的设置。
    Merge libxr_config.yaml in output_dir, or the path or URL given by --libxr-config, into the
    effective settings.

    文件中的 SYSTEM 被忽略，它由工程 YAML 决定；config_version 大于 1 时给出警告。URL 的下载
    时限为 CONFIG_DOWNLOAD_TIMEOUT 秒。没有配置文件时保留默认设置并使用新的空文档。已存在但
    无法读取或解析的配置会中止生成，而不是换用默认值。
    SYSTEM from the file is ignored because the project YAML decides it; a config_version
    above 1 is warned about. A URL download times out after CONFIG_DOWNLOAD_TIMEOUT seconds.
    Without a configuration file the defaults stay and a new, empty document is used. A
    configuration that exists but cannot be read or parsed stops generation instead of falling
    back to the defaults.

    Raises:
        LibXRConfigError: 配置无法下载、找到、读取或解析，或其值的类型与默认设置冲突。
            The configuration cannot be downloaded, located, read or parsed, or the type of a
            value conflicts with the default settings.
    """
    global libxr_settings, libxr_config_document
    config_path = os.path.join(output_dir, "libxr_config.yaml")

    if config_source:
        if config_source.startswith("http://") or config_source.startswith("https://"):
            logging.info(
                tr(
                    f"Downloading libxr_config.yaml from {config_source}",
                    f"正在从 {config_source} 下载 libxr_config.yaml",
                )
            )
            try:
                with urllib.request.urlopen(
                    config_source, timeout=CONFIG_DOWNLOAD_TIMEOUT
                ) as response:
                    text = response.read().decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise LibXRConfigError(
                    tr(
                        f"Cannot download {config_source}: {error}",
                        f"无法下载 {config_source}：{error}",
                    )
                ) from error
            document, saved_config = libxr_config_file.parse(text, config_source)
        elif os.path.exists(config_source):
            logging.info(
                tr(
                    f"Using external libxr_config.yaml from {config_source}",
                    f"使用外部的 libxr_config.yaml：{config_source}",
                )
            )
            document, saved_config = libxr_config_file.read(config_source)
        else:
            raise LibXRConfigError(
                tr(
                    f"Cannot locate config source: {config_source}",
                    f"找不到配置来源：{config_source}",
                )
            )
    elif os.path.exists(config_path):
        config_source = config_path
        document, saved_config = libxr_config_file.read(config_path)
    else:
        logging.info(
            tr(
                f"{config_path} does not exist; creating it with the default settings",
                f"{config_path} 不存在，按默认设置新建",
            )
        )
        libxr_config_document = libxr_config_file.new_document()
        return

    version = saved_config.get("config_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version > 1:
        logging.warning(
            tr(
                f"{config_source} has config_version {version!r}, but this libxr supports "
                "version 1; settings of a newer format may have no effect",
                f"{config_source} 的 config_version 是 {version!r}，本版本 libxr 只支持 1；"
                "较新格式的设置可能不起作用",
            )
        )
    saved_config.pop("SYSTEM", None)
    libxr_settings = _deep_merge(libxr_settings, saved_config)
    libxr_config_document = document


def _report_dropped_device_aliases(aliases) -> None:
    """以警告列出已移除的 device_aliases 表中的每个别名（别名 -> 设备），便于迁移配置。
    Log a warning that names every alias of the removed device_aliases table (alias -> device),
    for migrating configurations.

    表中没有可识别的别名时列出表的 repr。
    When the table holds no recognizable alias, its repr is listed instead.
    """
    pairs = []
    if isinstance(aliases, dict):
        for device, entry in aliases.items():
            names = entry.get("aliases", []) if isinstance(entry, dict) else entry
            if isinstance(names, str):
                names = [names]
            for name in names or []:
                pairs.append(f"{name} -> {device}")
    logging.warning(
        tr(
            "Removed the legacy device_aliases table from libxr_config.yaml; generated "
            "objects are registered only under their own names. Update configurations "
            "that used these aliases (alias -> device):",
            "已从 libxr_config.yaml 中删除旧的 device_aliases 表；"
            "生成的对象只以自己的名字登记。请更新使用了以下别名的配置（别名 -> 设备）：",
        )
    )
    for pair in pairs or [repr(aliases)]:
        logging.warning(f"  {pair}")


def save_libxr_config(config_path: str) -> bool:
    """把 libxr_config_text() 写入 config_path；内容相同时不写。写入时为 True。
    Write libxr_config_text() to config_path, unless the file already holds it; True when
    written.
    """
    return _write_if_changed(config_path, libxr_config_text())


def _write_if_changed(path: str, text: str) -> bool:
    """以 UTF-8 和 LF 换行把 text 写入 path；文件内容已相同时不写，修改时间不变。写入时为 True。
    Write text to path as UTF-8 with LF line endings; a file that already holds it is left
    alone, keeping its modification time. True when written.
    """
    data = text.encode("utf-8")
    try:
        with open(path, "rb") as stream:
            if stream.read() == data:
                return False
    except FileNotFoundError:
        pass
    with open(path, "wb") as stream:
        stream.write(data)
    return True


def libxr_config_text() -> str:
    """libxr_config.yaml 的新内容：生效的设置，去掉生成器补出来的空段和旧的 device_aliases 表。
    The new content of libxr_config.yaml: the effective settings without the empty sections the
    generator added and without the legacy device_aliases table.

    生成器不解释的键（例如 ``generator`` 版本固定项）、文件里原有的键（包括空映射）和注释被保留；
    device_aliases 中的别名以警告列出。
    Keys the generator does not interpret (such as the ``generator`` pin), keys already in the
    file, empty mappings included, and comments are kept; the aliases of device_aliases are
    listed in a warning.
    """
    # device_aliases 是旧的运行时别名表，已不再使用。
    # device_aliases was the legacy runtime alias table; it is no longer used.
    if "device_aliases" in libxr_settings:
        _report_dropped_device_aliases(libxr_settings["device_aliases"])
    document = libxr_config_document
    if document is None:
        document = libxr_config_file.new_document()
    cleaned_config = {}
    for key, value in libxr_settings.items():
        if key == "device_aliases":
            continue
        if isinstance(value, dict) and not value:
            # 生成器补出来的空段不写；文件里原有的键保留，只写了 "KEY:" 的仍写成 null。
            # Empty sections the generator added are left out; keys already in the file
            # stay, and a bare "KEY:" stays null.
            if key not in document:
                continue
            if document[key] is None:
                value = None
        cleaned_config[key] = value
    libxr_config_file.update(document, cleaned_config)
    return libxr_config_file.dump(document)


def _deep_merge(base: dict, update: dict) -> dict:
    """把 update 递归合并进 base 并返回 base；映射逐键合并，其他值直接覆盖，base 中映射对应的
    null 视为空映射。
    Merge update into base recursively and return base; mappings are merged key by key, other
    values overwrite, and a null meeting a mapping in base counts as an empty mapping.

    Raises:
        LibXRConfigError: update 中的映射对应 base 中的非映射值，或 update 中的非映射值对应 base
            中的映射。
            A mapping in update meets a non-mapping value in base, or a non-mapping value in
            update meets a mapping in base.
    """
    for key, value in update.items():
        if isinstance(value, dict):
            node = base.setdefault(key, {})
            if isinstance(node, dict):
                _deep_merge(node, value)
            else:
                expected = type(node).__name__
                raise LibXRConfigError(
                    tr(
                        f"Config type conflict for key '{key}': expected {expected}, got a mapping",
                        f"配置键 '{key}' 的类型冲突：应为 {expected}，实际是映射",
                    )
                )
        elif isinstance(base.get(key), dict):
            if value is None:
                # 空的段（例如只写了 "I2C:"）等同于空映射。
                # An empty section such as a bare "I2C:" counts as an empty mapping.
                continue
            actual = type(value).__name__
            raise LibXRConfigError(
                tr(
                    f"Config type conflict for key '{key}': expected a mapping, got {actual}",
                    f"配置键 '{key}' 的类型冲突：应为映射，实际是 {actual}",
                )
            )
        else:
            base[key] = value
    return base


# --------------------------
# GPIO 配置 / GPIO Configuration
# --------------------------
def _sanitize_cpp_identifier(name: str) -> str:
    """把名字转换为 C++ 标识符：非单词字符替换为下划线，以数字开头时在前面加下划线。
    Turn a name into a C++ identifier: non-word characters become underscores, and a leading
    digit gets an underscore in front.
    """
    return re.sub(r"\W|^(?=\d)", "_", name)


CPP_KEYWORDS = frozenset(
    [
        "alignas",
        "alignof",
        "and",
        "and_eq",
        "asm",
        "auto",
        "bitand",
        "bitor",
        "bool",
        "break",
        "case",
        "catch",
        "char",
        "char8_t",
        "char16_t",
        "char32_t",
        "class",
        "compl",
        "concept",
        "const",
        "consteval",
        "constexpr",
        "constinit",
        "const_cast",
        "continue",
        "co_await",
        "co_return",
        "co_yield",
        "decltype",
        "default",
        "delete",
        "do",
        "double",
        "dynamic_cast",
        "else",
        "enum",
        "explicit",
        "export",
        "extern",
        "false",
        "float",
        "for",
        "friend",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "mutable",
        "namespace",
        "new",
        "noexcept",
        "not",
        "not_eq",
        "nullptr",
        "operator",
        "or",
        "or_eq",
        "private",
        "protected",
        "public",
        "register",
        "reinterpret_cast",
        "requires",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "static_cast",
        "struct",
        "switch",
        "template",
        "this",
        "thread_local",
        "throw",
        "true",
        "try",
        "typedef",
        "typeid",
        "typename",
        "union",
        "unsigned",
        "using",
        "virtual",
        "void",
        "volatile",
        "wchar_t",
        "while",
        "xor",
        "xor_eq",
    ]
)

# CMSIS/HAL 的对象式宏；与之同名的 GPIO 对象名会被预处理器展开。
# Object-like CMSIS/HAL macros a GPIO object name would be expanded into.
_CMSIS_INSTANCE_MACRO = re.compile(
    r"GPIO[A-Z]|(?:ADC|DAC|TIM|LPTIM|HRTIM|SPI|I2S|I2C|I3C|USART|UART|LPUART|"
    r"CAN|FDCAN|DMA|BDMA|GPDMA|HPDMA|LPDMA|MDMA|DMAMUX|DMA2D|SAI|SDMMC|SDIO|"
    r"QUADSPI|OCTOSPI|OCTOSPIM|XSPI|FMC|FSMC|COMP|OPAMP|DFSDM|MDF|ADF|IWDG|"
    r"WWDG|RTC|TAMP|CRC|RNG|HASH|CRYP|AES|SAES|PKA|ETH|LTDC|DCMI|DCMIPP|PSSI|"
    r"USB_OTG_FS|USB_OTG_HS|USB|UCPD|TSC|LCD|CEC|SPDIFRX|SWPMI|MDIOS|RCC|PWR|"
    r"FLASH|EXTI|SYSCFG|DBGMCU|SCB|NVIC|SysTick|MPU|FPU|ITM|DWT|CoreDebug|TPI|"
    r"ICACHE|DCACHE|GTZC|VREFBUF|CORDIC|FMAC|JPEG|RAMCFG|OTFDEC|IPCC|HSEM)\d*"
)
_HAL_MACROS = frozenset({"NULL", "UNUSED", "UID_BASE"})


def _gpio_object_name(port: str, gpio_data: dict) -> str:
    """GPIO 对象的 C++ 名字：CubeMX 标签，没有标签时为 GPIO 段中的引脚键，经
    _sanitize_cpp_identifier() 处理。
    The C++ name of a GPIO object: its CubeMX label, or its pin key in the GPIO section without
    a label, passed through _sanitize_cpp_identifier().
    """
    return _sanitize_cpp_identifier(gpio_data.get("Label", "") or port)


def check_gpio_names(project_data: dict, generated_code: str, use_xrobot: bool) -> None:
    """拒绝生成的 app_main 无法声明的 GPIO 对象名。
    Reject GPIO object names that the generated app_main cannot declare.

    GPIO 对象以其 CubeMX 标签命名。标签若是 C++ 关键字或保留标识符、CMSIS/HAL 宏或 IRQ 名、
    CubeMX 由其他标签派生的宏，或生成代码中用到的其他名字，就会编译失败或在 app_main 中静默遮蔽
    该名字。
    A GPIO object is named after its CubeMX label. A label that is a C++
    keyword or reserved identifier, a CMSIS/HAL macro or IRQ name, a macro CubeMX derives
    from another label, or any other name the generated code uses would fail
    to compile or silently shadow that name inside app_main.

    Raises:
        ValueError: 至少一个 GPIO 名字有上述问题；信息列出全部问题。
            At least one GPIO name has one of these problems; the message lists all of them.
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
    # 声明处出现一次，启用 --xrobot 时 XR_REGISTER 行再出现一次。
    # Declaration, plus the XR_REGISTER line with --xrobot.
    expected_uses = 2 if use_xrobot else 1
    problems = []
    for port, data in gpio.items():
        name = _gpio_object_name(port, data)
        pin = port.split("-")[0]
        where = tr(f"GPIO object '{name}' (pin {pin})", f"GPIO 对象 '{name}'（引脚 {pin}）")
        if name in CPP_KEYWORDS:
            problems.append(tr(f"{where} is a C++ keyword", f"{where}是 C++ 关键字"))
        elif "__" in name or re.match(r"_[A-Z]", name):
            problems.append(
                tr(f"{where} is a reserved C++ identifier", f"{where}是 C++ 保留标识符")
            )
        elif name in label_macros:
            label = label_macros[name]
            problems.append(
                tr(
                    f"{where} is the CubeMX macro of GPIO label '{label}'",
                    f"{where}是 GPIO 标签 '{label}' 的 CubeMX 宏",
                )
            )
        elif name in _HAL_MACROS or name.endswith("_IRQn") or _CMSIS_INSTANCE_MACRO.fullmatch(name):
            problems.append(
                tr(f"{where} is a CMSIS/HAL macro or IRQ name", f"{where}是 CMSIS/HAL 宏或 IRQ 名")
            )
        elif counts.get(name, 0) > expected_uses:
            problems.append(
                tr(
                    f"{where} collides with a name the generated code uses",
                    f"{where}与生成代码使用的名字冲突",
                )
            )
    if problems:
        raise ValueError(
            tr(
                "rename these GPIO labels in CubeMX:\n  ",
                "请在 CubeMX 中重命名以下 GPIO 标签：\n  ",
            )
            + "\n  ".join(problems)
        )


def generate_gpio_alias(port: str, gpio_data: dict, project_data: dict) -> str:
    """生成一个 GPIO 对象的声明符 ``name(port, pin[, irq])`` 并登记该对象。
    Generate the declarator ``name(port, pin[, irq])`` of one GPIO object and register the
    object.

    有标签时使用 CubeMX 的 <label>_GPIO_Port 和 <label>_Pin 宏；配置为 EXTI 的引脚另带中断号。
    With a label the CubeMX macros <label>_GPIO_Port and <label>_Pin are used; a pin configured
    for EXTI also gets its IRQ number.
    """
    base_port = port.split("-")[0]
    port_define = f"GPIO{base_port[1]}"
    pin_num = int(base_port[2:])
    pin_define = f"GPIO_PIN_{pin_num}"
    label = gpio_data.get("Label", "")

    if label:
        port_define = f"{label}_GPIO_Port"
        pin_define = f"{label}_Pin"

    irq_define = _get_exti_irq(
        pin_num,
        base_port,
        gpio_data.get("GPXTI", False),
        project_data.get("Mcu", {}).get("Family", "STM32F4"),
        project_data.get("Mcu", {}).get("Type") or "",
    )
    irq_str = f", {irq_define}" if irq_define else ""

    var_name = _gpio_object_name(port, gpio_data)

    _register_device(
        var_name,
        "GPIO",
        tr(f"GPIO label {label} on {base_port}", f"{base_port} 上的 GPIO 标签 {label}")
        if label
        else f"GPIO {base_port}",
    )

    return f"{var_name}({port_define}, {pin_define}{irq_str})"


# 各 CubeMX 系列（Mcu.Family）的 EXTI 中断向量，取自器件的向量表。
# 以下系列共用 EXTI0_1/EXTI2_3/EXTI4_15：
# EXTI interrupt vectors per CubeMX family (Mcu.Family), from the device
# vector tables. These families share EXTI0_1/EXTI2_3/EXTI4_15:
_EXTI_SHARED_LINE_FAMILIES = frozenset({"STM32F0", "STM32G0", "STM32L0", "STM32C0", "STM32U0"})
# 以下系列每条线一个向量，即 EXTI0_IRQn..EXTI15_IRQn（STM32H7 系列中的 STM32H7R/S 器件
# 也是如此）；其余系列共用 EXTI9_5 和 EXTI15_10。
# These have one vector per line, EXTI0_IRQn..EXTI15_IRQn (as do the STM32H7R/S
# parts of the STM32H7 family); the others share EXTI9_5 and EXTI15_10.
_EXTI_PER_LINE_FAMILIES = frozenset(
    {"STM32H5", "STM32U3", "STM32U5", "STM32L5", "STM32WBA", "STM32N6"}
)


def _get_exti_irq(
    pin_num: int, port: str, is_exti: bool, mcu_family: str, mcu_type: str = ""
) -> str:
    """按 MCU 系列返回引脚的 EXTI 中断号名字；不是 EXTI 引脚时为空字符串。
    The EXTI IRQ name of a pin for its MCU family; an empty string when the pin is not an EXTI
    pin.

    STM32WB0 的 PA/PB 引脚使用 GPIOA_IRQn/GPIOB_IRQn；共享向量的系列使用 EXTI0_1_IRQn、
    EXTI2_3_IRQn 和 EXTI4_15_IRQn；每线一个向量的系列以及 STM32H7R/S 使用 EXTI<n>_IRQn；其余系列
    的 0~4 线使用 EXTI<n>_IRQn，5~9 线和 10~15 线分别使用 EXTI9_5_IRQn 和 EXTI15_10_IRQn。
    STM32WB0 PA/PB pins use GPIOA_IRQn/GPIOB_IRQn; the shared-vector families use EXTI0_1_IRQn,
    EXTI2_3_IRQn and EXTI4_15_IRQn; the per-line families and STM32H7R/S use EXTI<n>_IRQn; the
    other families use EXTI<n>_IRQn for lines 0 to 4, EXTI9_5_IRQn for lines 5 to 9 and
    EXTI15_10_IRQn for lines 10 to 15.
    """
    if not is_exti:
        return ""

    if mcu_family.startswith("STM32WB0"):
        if port.startswith("PA"):
            return "GPIOA_IRQn"
        elif port.startswith("PB"):
            return "GPIOB_IRQn"

    if mcu_family in _EXTI_SHARED_LINE_FAMILIES:
        if pin_num <= 1:
            return "EXTI0_1_IRQn"
        if pin_num <= 3:
            return "EXTI2_3_IRQn"
        return "EXTI4_15_IRQn"
    elif mcu_family in _EXTI_PER_LINE_FAMILIES or mcu_type.startswith(("STM32H7R", "STM32H7S")):
        return f"EXTI{pin_num}_IRQn"
    else:
        if 5 <= pin_num <= 9:
            return "EXTI9_5_IRQn"
        if 10 <= pin_num <= 15:
            return "EXTI15_10_IRQn"
        return f"EXTI{pin_num}_IRQn"


# --------------------------
# DMA 配置 / DMA Configuration
# --------------------------
DMA_DEFAULT_SIZES = {
    "SPI": {"tx": 32, "rx": 32},
    "USART": {"tx": 128, "rx": 128},
    "I2C": {"buffer": 32},
    "ADC": {"buffer": 32},
}

# CubeMX 的 USB 实例名到规范名；USB（FSDEV）视为 USB_FS。
# CubeMX USB instance names to their normalized names; USB (FSDEV) is USB_FS.
_USB_INSTANCES = {
    "USB": "USB_FS",
    "USB_FS": "USB_FS",
    "USB_HS": "USB_HS",
    "USB_OTG_FS": "USB_OTG_FS",
    "USB_OTG_HS": "USB_OTG_HS",
}


def _integer(key: str, value, minimum: int = 1, maximum: int | None = None) -> int:
    """设置 key 的整数值；字符串按 Python 整数字面量解析（如 0x1D50）。
    The integer value of the setting key; a string is read as a Python integer literal, such
    as 0x1D50.

    数值写进生成的 C++ 代码，所以在这里检查，而不是等到编译时。
    The value goes into the generated C++ code, so it is checked here rather than at compile
    time.

    Raises:
        ValueError: value 不是整数，或不在 minimum 到 maximum 之间；信息中写出 key。
            value is not an integer, or lies outside minimum to maximum; the message names key.
    """
    number = None
    if not isinstance(value, bool):
        if isinstance(value, int):
            number = value
        else:
            try:
                number = int(str(value).strip(), 0)
            except ValueError:
                number = None
    if number is not None and number >= minimum and (maximum is None or number <= maximum):
        return number
    if maximum is not None:
        english, chinese = (
            f"an integer from {minimum} to {maximum}",
            f"{minimum} 到 {maximum} 的整数",
        )
    elif minimum == 1:
        english, chinese = "a positive integer", "正整数"
    else:
        english, chinese = "a non-negative integer", "非负整数"
    raise ValueError(tr(f"{key} {value!r} is not {english}", f"{key} {value!r} 不是{chinese}"))


def _number(key: str, value) -> float | int:
    """设置 key 的数值（整数或小数）；字符串按小数解析。
    The numeric value, integer or decimal, of the setting key; a string is read as a decimal.

    Raises:
        ValueError: value 不是有限的数；信息中写出 key。
            value is not a finite number; the message names key.
    """
    if not isinstance(value, bool):
        number = value
        if not isinstance(value, (int, float)):
            try:
                number = float(str(value).strip())
            except ValueError:
                number = None
        if number is not None and math.isfinite(number):
            return number
    raise ValueError(tr(f"{key} {value!r} is not a number", f"{key} {value!r} 不是数字"))


def _usb_settings(instance: str) -> tuple[str, dict] | None:
    """USB 实例的规范名和它在 libxr_settings["USB"] 中的设置；其他实例名（如 USB_DEVICE 中间件）
    为 None。
    The normalized name of a USB instance and its settings in libxr_settings["USB"]; None for
    any other instance name, such as the USB_DEVICE middleware.

    未启用的实例只补上 enable: false。已启用的实例按固定顺序补上缺少的设置：包大小、缓冲区和
    FIFO 大小、dma_section、CDC 的 FIFO 和队列长度，以及描述符（默认 1d50:6199 / 0x0100 /
    "XRUSB-DEMO-"，1d50:6199 的分配记录见
    https://github.com/openmoko/openmoko-usb-oui/commit/27f3846d77e0d0d10271b809b831f70040c6197a）。
    ep0_packet_size 不是 8、16、32、64 时给出警告并改为 8；其余大小必须是正整数，vid、pid、bcd
    必须在 0 到 0xFFFF 之间。
    A disabled instance only gets enable: false. An enabled instance gets its missing settings
    in a fixed order: packet size, buffer and FIFO sizes, dma_section, CDC FIFO and queue
    lengths, and the descriptor (default 1d50:6199 / 0x0100 / "XRUSB-DEMO-"; see the link
    above for the allocation of 1d50:6199). An ep0_packet_size other than 8, 16, 32 or 64 is
    warned about and becomes 8; the other sizes must be positive integers, and vid, pid and
    bcd must lie between 0 and 0xFFFF.

    Raises:
        ValueError: 已启用的实例设置了 cdc_count（复合 USB 设备应在 BSP 用户代码中定义），或某个
            大小或描述符数值无效。
            An enabled instance sets cdc_count (a composite USB device belongs in BSP user code),
            or a size or descriptor number is invalid.
    """
    name = _USB_INSTANCES.get((instance or "").upper())
    if name is None:
        return None
    cfg = libxr_settings.setdefault("USB", {}).setdefault(name.lower(), {})
    cfg.setdefault("enable", False)
    if not cfg["enable"]:
        return name, cfg
    if "cdc_count" in cfg:
        raise ValueError(
            tr(
                "USB cdc_count is not a generator option; define composite USB in BSP user code",
                "USB 的 cdc_count 不是生成器选项；复合 USB 设备请在 BSP 用户代码中定义",
            )
        )
    try:
        ep0 = _integer("ep0_packet_size", cfg.get("ep0_packet_size", 8))
    except ValueError:
        ep0 = None
    if ep0 in (8, 16, 32, 64):
        cfg.setdefault("ep0_packet_size", ep0)
    else:
        logging.warning(
            tr(
                f"USB {name.lower()}: ep0_packet_size {cfg['ep0_packet_size']} is not 8, 16, 32 "
                "or 64; using 8",
                f"USB {name.lower()}：ep0_packet_size {cfg['ep0_packet_size']} 不是 8、16、32 "
                "或 64，改用 8",
            )
        )
        cfg["ep0_packet_size"] = 8
    is_otg = name.startswith("USB_OTG_")
    defaults = {
        "tx_buffer_size": 128,
        "rx_buffer_size": 128,
        "rx_fifo_size": 256 if is_otg else 128,
        "tx_fifo_size": 128,
        "dma_section": "",
        "cdc_tx_fifo_size": 128,
        "cdc_rx_fifo_size": 128,
        "cdc_queue_size": 3,
        "vid": 0x1D50,
        "pid": 0x6199,
        "bcd": 0x0100,
        "manufacturer": "XRobot",
        "product": f"STM32 XRUSB {instance} CDC Demo",
        "serial": "XRUSB-DEMO-",
    }
    for key, value in defaults.items():
        cfg.setdefault(key, value)
    for key in (
        "tx_buffer_size",
        "rx_buffer_size",
        "rx_fifo_size",
        "tx_fifo_size",
        "cdc_tx_fifo_size",
        "cdc_rx_fifo_size",
        "cdc_queue_size",
    ):
        _integer(f"USB.{name.lower()}.{key}", cfg[key])
    for key in ("vid", "pid", "bcd"):
        _integer(f"USB.{name.lower()}.{key}", cfg[key], 0, 0xFFFF)
    return name, cfg


def generate_dma_resources(project_data: dict) -> str:
    """生成外设 DMA 缓冲区的定义，返回 C++ 代码文本；没有缓冲区时返回一行说明注释。
    Generate the definitions of the peripheral DMA buffers and return them as C++ code; without
    any buffer a one-line comment says so.

    SPI 和 USART（含 UART、LPUART）为开启 DMA 的方向各生成一个缓冲区；I2C 和 ADC 各生成一个缓冲区，
    ADC 的元素数为通道数乘以每通道元素数；已启用的 USB 实例生成端点缓冲区。缓冲区大小和 dma_section
    取自 libxr_settings，缺少时写入默认值，大小必须是正整数；dma_section 非空时声明带
    __attribute__((section("...")))。
    SPI and USART, UART and LPUART included, get one buffer per direction with DMA enabled; I2C
    and ADC get one buffer each, the ADC one holding the channel count times the elements per
    channel; enabled USB instances get endpoint buffers. Buffer sizes and dma_section come from
    libxr_settings, which receives the defaults for missing values, and sizes must be positive
    integers; a non-empty dma_section adds __attribute__((section("..."))) to the declarations.

    有数据 cache 的目标使用按 cache 行对齐并补齐的存储，缓冲区两端不与其他数据共用 cache 行；数组
    长度不变，因此 DMA 长度和端点容量不变。
    On targets with a data cache the storage is aligned and padded to the cache line, so no
    other data shares a cache line with either end of a buffer; the array extent stays, so DMA
    lengths and endpoint capacities do not change.

    Raises:
        ValueError: 已启用的 USB 实例设置了 cdc_count，或某个大小不是正整数。
            An enabled USB instance sets cdc_count, or a size is not a positive integer.
    """
    dma_code = []

    def section_attribute(instance_config: dict) -> str:
        """实例设置中 dma_section 对应的段属性文本；没有设置时为空字符串，并把 dma_section 记为
        空字符串，使 libxr_config.yaml 列出这一项。
        The section attribute text for dma_section in the instance settings; an empty string
        when it is not set, and dma_section is then recorded as an empty string so that
        libxr_config.yaml lists it.
        """
        dma_section = instance_config.get("dma_section") or ""
        instance_config["dma_section"] = dma_section
        return f' __attribute__((section("{dma_section}")))' if dma_section else ""

    def buffer_declaration(data_type: str, name: str, count, section: str) -> str:
        """生成一个 DMA 缓冲区的声明，按 __DCACHE_PRESENT 分为两种写法。
        Generate the declaration of one DMA buffer in two forms selected by __DCACHE_PRESENT.

        有数据 cache 时数组放在按 XR_DCACHE_LINE_SIZE 对齐的结构体 <name>_storage 中，并以
        constexpr 引用 <name> 指向该数组；否则为 4 字节对齐的静态数组 <name>。两种写法的数组长度
        都是 count。
        With a data cache the array sits in the struct <name>_storage aligned to
        XR_DCACHE_LINE_SIZE, and the constexpr reference <name> refers to it; otherwise <name>
        is a static array aligned to 4 bytes. Both forms have count elements.

        Args:
            section: 加在声明上的段属性文本，可为空字符串。
                The section attribute text added to the declaration; may be empty.
        """
        # 对齐的是存储类型而不只是其对象，这样 sizeof 包含尾部填充。保留数组长度，
        # 使 RawData 和拆分后的缓冲区不变。
        # Align the storage type, not only its object: sizeof then includes tail
        # padding. Keep the array extent so RawData and split buffers are unchanged.
        return "\n".join(
            [
                "#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)",
                "static struct alignas(XR_DCACHE_LINE_SIZE)",
                "{",
                f"  {data_type} data[{count}];",
                f"}} {name}_storage{section};",
                f"static constexpr auto& {name} = {name}_storage.data;",
                "#else",
                f"alignas(4) static {data_type} {name}[{count}]{section};",
                "#endif",
            ]
        )

    # 遍历所有外设
    # Iterate all peripherals
    for p_type_raw, instances in project_data.get("Peripherals", {}).items():
        # 规范化外设类型（例如 "spi1" -> "SPI"）
        # Normalize peripheral type (e.g. "spi1" -> "SPI")
        match = re.match(r"([A-Za-z0-9]+?)(\d*)$", p_type_raw)
        p_type_base = match.group(1).upper() if match else p_type_raw.upper()

        # 确保该外设的设置字典存在
        # Ensure settings dict exists for this peripheral
        if p_type_base not in libxr_settings:
            libxr_settings[p_type_base] = {}

        # SPI 和 USART 外设；parse 把 USART、UART 和 LPUART 实例都放在 USART 下。
        # SPI and USART; parse puts USART, UART and LPUART instances all under USART.
        if p_type_base in ["SPI", "USART"]:
            for instance, config in instances.items():
                # 检查 DMA 使能标志
                # Check DMA enable flags
                tx_dma = config.get("DMA_TX", "DISABLE") == "ENABLE"
                rx_dma = config.get("DMA_RX", "DISABLE") == "ENABLE"
                instance_lower = instance.lower()
                instance_config = libxr_settings[p_type_base].setdefault(instance_lower, {})
                key = f"{p_type_base}.{instance_lower}"
                tx_size = _integer(
                    f"{key}.tx_buffer_size",
                    instance_config.setdefault(
                        "tx_buffer_size", DMA_DEFAULT_SIZES[p_type_base]["tx"]
                    ),
                )
                rx_size = _integer(
                    f"{key}.rx_buffer_size",
                    instance_config.setdefault(
                        "rx_buffer_size", DMA_DEFAULT_SIZES[p_type_base]["rx"]
                    ),
                )
                sec_str = section_attribute(instance_config)

                buf_code = []
                if tx_dma:
                    buf_code.append(
                        buffer_declaration("uint8_t", f"{instance_lower}_tx_buf", tx_size, sec_str)
                    )
                if rx_dma:
                    buf_code.append(
                        buffer_declaration("uint8_t", f"{instance_lower}_rx_buf", rx_size, sec_str)
                    )
                if buf_code:
                    dma_code.append("\n".join(buf_code))

        # I2C/ADC 外设
        # I2C/ADC
        elif p_type_base in ["I2C", "ADC"]:
            for instance, config in instances.items():
                instance_lower = instance.lower()
                instance_config = libxr_settings[p_type_base].setdefault(instance_lower, {})
                buf_size = _integer(
                    f"{p_type_base}.{instance_lower}.buffer_size",
                    instance_config.setdefault(
                        "buffer_size", DMA_DEFAULT_SIZES[p_type_base]["buffer"]
                    ),
                )
                sec_str = section_attribute(instance_config)

                # ADC 缓冲区为 uint16_t，I2C 为 uint8_t
                # ADC buffer is uint16_t, I2C is uint8_t
                if p_type_base == "ADC":
                    # 通道选择规则：DMA 开启→RegularConversions，否则→Channels
                    # Channel selection: RegularConversions with DMA enabled, otherwise Channels
                    active_channels = (
                        config.get("RegularConversions", [])
                        if config.get("DMA") == "ENABLE"
                        else config.get("Channels", [])
                    )
                    # 至少保留 1 份缓冲
                    # Keep at least one channel's share of the buffer
                    ch_cnt = max(1, len(active_channels))
                    # 每通道的 uint16_t 元素数
                    # uint16_t elements per channel
                    elems_per_channel = max(1, int(buf_size // 2))
                    # 总元素数 = 通道数 × 每通道元素数
                    # Total elements = channel count × elements per channel
                    total_elems = ch_cnt * elems_per_channel
                    dma_code.append(
                        buffer_declaration(
                            "uint16_t", f"{instance_lower}_buf", total_elems, sec_str
                        )
                    )
                else:
                    dma_code.append(
                        buffer_declaration("uint8_t", f"{instance_lower}_buf", buf_size, sec_str)
                    )

        elif p_type_base == "USB":
            # 为每个已启用的 USB 实例生成端点缓冲区（所在段由 dma_section 决定）。
            # Endpoint buffers for each enabled USB instance, in the section dma_section names.
            for instance in instances:
                usb = _usb_settings(instance)
                if usb is None or not usb[1]["enable"]:
                    continue
                name, usb_cfg = usb
                inst_lower = name.lower()
                ep0 = _integer("ep0_packet_size", usb_cfg["ep0_packet_size"])
                tx_sz = _integer("tx_buffer_size", usb_cfg["tx_buffer_size"])
                rx_sz = _integer("rx_buffer_size", usb_cfg["rx_buffer_size"])
                dma_section = usb_cfg["dma_section"]
                sec_str = f' __attribute__((section("{dma_section}")))' if dma_section else ""

                # 每个变量单独声明，避免属性只作用于最后一个变量
                # One line per variable to avoid attribute only on the last one
                dma_code.append(
                    buffer_declaration("uint8_t", f"{inst_lower}_ep0_in_buf", ep0, sec_str)
                )
                dma_code.append(
                    buffer_declaration("uint8_t", f"{inst_lower}_ep0_out_buf", ep0, sec_str)
                )
                dma_code.append(
                    buffer_declaration("uint8_t", f"{inst_lower}_ep1_in_buf", tx_sz, sec_str)
                )
                dma_code.append(
                    buffer_declaration("uint8_t", f"{inst_lower}_ep1_out_buf", rx_sz, sec_str)
                )
                dma_code.append(
                    buffer_declaration("uint8_t", f"{inst_lower}_ep2_in_buf", 16, sec_str)
                )

    # 最终输出；生成了代码时加上本段的头部
    # Final output with section header if any code generated
    if dma_code:
        # 较旧的 CMSIS core_cm7.h（例如 STM32F7 的 Cube 包）没有 cache 行长度的宏；
        # Cortex-M7 的数据 cache 行固定为 32 字节。
        # Older CMSIS core_cm7.h (e.g. STM32F7 Cube packs) lacks the line-size
        # macro; the Cortex-M7 data cache line is fixed at 32 bytes.
        output = (
            "\n".join(
                [
                    "/* DMA Resources */",
                    "#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)",
                    "#if defined(__SCB_DCACHE_LINE_SIZE)",
                    "#define XR_DCACHE_LINE_SIZE __SCB_DCACHE_LINE_SIZE",
                    "#else",
                    "#define XR_DCACHE_LINE_SIZE 32U",
                    "#endif",
                    "#endif",
                ]
            )
            + "\n"
        )
        output += "\n".join(dma_code)
    else:
        output = "/* No DMA Resources generated. */"
    return output


# --------------------------
# 外设生成 / Peripheral Generation
# --------------------------
def _instance_settings(group: str, instance: str) -> dict:
    """libxr_settings[group] 中实例的设置，键为小写的实例名；不存在时创建。
    The settings of an instance in libxr_settings[group], keyed by the lower-case instance
    name; created when missing.

    旧版本按 CubeMX 的写法保存的键（如 CAN 下的 CAN1）改为小写，并记录一条提示。
    A key that older versions saved as CubeMX writes it, such as CAN1 under CAN, is renamed to
    lower case with a notice.
    """
    settings = libxr_settings.setdefault(group, {})
    key = instance.lower()
    for old_key in [k for k in settings if k != key and str(k).lower() == key]:
        value = settings.pop(old_key)
        if key not in settings:
            settings[key] = value
        logging.info(
            tr(
                f"libxr_config.yaml: renamed {group}.{old_key} to {group}.{key}",
                f"libxr_config.yaml：已把 {group}.{old_key} 改为 {group}.{key}",
            )
        )
    return settings.setdefault(key, {})


class PeripheralFactory:
    """按外设类型生成 LibXR 外设对象的构造代码，并登记生成的对象。
    Generate the construction code of LibXR peripheral objects by peripheral type and register
    the generated objects.

    各生成方法返回 (段, 代码)：段为 "adc"、"pwm" 或 "main"，决定代码在 app_main 中的位置；
    ("", "") 表示不生成代码。缺少的设置以默认值写入 libxr_settings。
    Each generator method returns (section, code): the section, "adc", "pwm" or "main", decides
    where the code goes in app_main, and ("", "") means no code. Missing settings are written
    to libxr_settings with their defaults.
    """

    @staticmethod
    def create(p_type: str, instance: str, config: dict) -> str:
        """调用 p_type 对应的生成方法并返回 (段, 代码)；类型名不区分大小写，没有对应方法时
        为 ("", "")。
        Call the generator method of p_type, case-insensitively, and return (section, code);
        ("", "") when the type has none.
        """
        handler_map = {
            "ADC": PeripheralFactory._generate_adc,
            "DAC": PeripheralFactory._generate_dac,
            "TIM": PeripheralFactory._generate_tim,
            "FDCAN": PeripheralFactory._generate_canfd,
            "CAN": PeripheralFactory._generate_can,
            "SPI": PeripheralFactory._generate_spi,
            # parse 把 USART、UART 和 LPUART 实例都放在 USART 下。
            # parse puts USART, UART and LPUART instances all under USART.
            "USART": PeripheralFactory._generate_uart,
            "I2C": PeripheralFactory._generate_i2c,
            "IWDG": PeripheralFactory._generate_iwdg,
            "USB": PeripheralFactory._generate_usb,
        }
        generator = handler_map.get(p_type.upper())
        return generator(instance, config) if generator else ("", "")

    @staticmethod
    def _generate_adc(instance: str, config: dict) -> tuple:
        """生成 STM32ADC 对象和每个通道的引用；DMA 开启时用 RegularConversions，否则用 Channels。
        Generate the STM32ADC object and a reference per channel; RegularConversions with DMA
        enabled, Channels otherwise.

        参考电压取 libxr_settings 中的 vref（默认 3.3）；每个通道引用 <adc>_<channel> 登记为 ADC。
        The reference voltage is vref from libxr_settings (default 3.3); each channel reference
        <adc>_<channel> is registered as ADC.
        """
        conversions = (
            config.get("RegularConversions", [])
            if config.get("DMA") == "ENABLE"
            else config.get("Channels", [])
        )
        adc_config = libxr_settings["ADC"].setdefault(instance.lower(), {})
        vref = _number(f"ADC.{instance.lower()}.vref", adc_config.setdefault("vref", 3.3))

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
        """为每个 DAC 输出通道生成一个 STM32DAC 对象，名字为 <instance>_<out_name>（如 dac1_out2）。
        Generate one STM32DAC object per DAC output channel, named <instance>_<out_name>, for
        example dac1_out2.

        DAC_OUT<n> 写作 DAC_CHANNEL_<n>，名字开头的 dac_dac_ 缩为 dac_。初始电压和参考电压取自
        libxr_settings（默认 0.0 和 3.3）。没有通道时不生成代码。
        DAC_OUT<n> is written as DAC_CHANNEL_<n>, and a name starting with dac_dac_ is shortened
        to dac_. The initial and reference voltages come from libxr_settings (defaults 0.0 and
        3.3). No channel means no code.
        """
        channels = config.get("Channels", {})
        if not channels:
            return "", ""
        dac_config = libxr_settings["DAC"].setdefault(instance.lower(), {})
        init_voltage = _number(
            f"DAC.{instance.lower()}.init_voltage", dac_config.setdefault("init_voltage", 0.0)
        )
        vref = _number(f"DAC.{instance.lower()}.vref", dac_config.setdefault("vref", 3.3))
        codes = []
        for out_name, channel_id in channels.items():
            if channel_id.startswith("DAC_OUT"):
                m = re.search(r"DAC_OUT(\d+)", channel_id)
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
        """生成 STM32UART 对象；未开启 DMA 的方向使用空缓冲区 {nullptr, 0}。
        Generate the STM32UART object; a direction without DMA gets the empty buffer
        {nullptr, 0}.

        HAL 句柄名中的 usart 写作 uart；发送队列长度取 libxr_settings 中 USART 下的
        tx_queue_size（默认 5）。
        The HAL handle name writes usart as uart; the transmit queue length is tx_queue_size
        under USART in libxr_settings (default 5).
        """
        tx_dma = config.get("DMA_TX", "DISABLE") == "ENABLE"
        rx_dma = config.get("DMA_RX", "DISABLE") == "ENABLE"
        tx_buf = f"{instance.lower()}_tx_buf" if tx_dma else "{nullptr, 0}"
        rx_buf = f"{instance.lower()}_rx_buf" if rx_dma else "{nullptr, 0}"

        uart_config = libxr_settings["USART"].setdefault(instance.lower(), {})
        tx_queue = _integer(
            f"USART.{instance.lower()}.tx_queue_size", uart_config.setdefault("tx_queue_size", 5)
        )

        code = (
            f"  static STM32UART {instance.lower()}(&h{instance.lower().replace('usart', 'uart')},\n"
            f"              {rx_buf}, {tx_buf}, {tx_queue});\n"
        )
        _register_device(f"{instance.lower()}", "UART")
        return "main", code

    @staticmethod
    def _generate_i2c(instance: str, config: dict) -> tuple:
        """生成使用 <instance>_buf 缓冲区的 STM32I2C 对象；dma_enable_min_size 默认为 3。
        Generate the STM32I2C object with the <instance>_buf buffer; dma_enable_min_size
        defaults to 3.
        """
        i2c_config = libxr_settings["I2C"].setdefault(instance.lower(), {})
        dma_min_size = _integer(
            f"I2C.{instance.lower()}.dma_enable_min_size",
            i2c_config.setdefault("dma_enable_min_size", 3),
            0,
        )
        _register_device(f"{instance.lower()}", "I2C")
        return (
            "main",
            f"  static STM32I2C {instance.lower()}(&h{instance.lower()}, {instance.lower()}_buf, {dma_min_size});\n",
        )

    @staticmethod
    def _generate_tim(instance: str, config: dict) -> tuple:
        """为定时器的每个通道生成一个 STM32PWM 对象 pwm_<tim>_ch<n>；没有通道时不生成代码。
        Generate one STM32PWM object pwm_<tim>_ch<n> per timer channel; no channel means no
        code.

        互补通道使用去掉末尾 N 的 TIM_CHANNEL_<n>，并传入 true 表示互补输出。
        A complementary channel uses TIM_CHANNEL_<n> without the trailing N and passes true for
        the complementary output.
        """
        channels = config.get("Channels", {})
        if not channels:
            return "", ""
        code = ""
        for ch_name, ch_cfg in channels.items():
            ch_num = ch_name.replace("CH", "").lower()
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
        """生成 STM32CANFD 对象；队列长度取 libxr_settings 中 FDCAN 下的 queue_size（默认 5）。
        Generate the STM32CANFD object; the queue length is queue_size under FDCAN in
        libxr_settings (default 5).
        """
        instance_cfg = _instance_settings("FDCAN", instance)
        queue_size = _integer(
            f"FDCAN.{instance.lower()}.queue_size", instance_cfg.setdefault("queue_size", 5)
        )

        _register_device(f"{instance.lower()}", "FDCAN")
        return (
            "main",
            f"  static STM32CANFD {instance.lower()}(&h{instance.lower()}, {queue_size});\n",
        )

    @staticmethod
    def _generate_can(instance: str, config: dict) -> tuple:
        """生成经典 CAN 外设的 STM32CAN 对象。
        Generate the STM32CAN object of a classic CAN peripheral.

        队列长度取 libxr_settings 中 CAN 下的 queue_size（默认 5）。
        The queue length is queue_size under CAN in libxr_settings (default 5).
        """
        instance_cfg = _instance_settings("CAN", instance)
        queue_size = _integer(
            f"CAN.{instance.lower()}.queue_size", instance_cfg.setdefault("queue_size", 5)
        )

        _register_device(
            f"{instance.lower()}",
            "CAN",
            tr(f"classic CAN peripheral {instance}", f"经典 CAN 外设 {instance}"),
        )
        return (
            "main",
            f"  static STM32CAN {instance.lower()}(&h{instance.lower()}, {queue_size});\n",
        )

    @staticmethod
    def _generate_spi(instance: str, config: dict) -> tuple:
        """生成 STM32SPI 对象；未开启 DMA 的方向使用空缓冲区 {nullptr, 0}。
        Generate the STM32SPI object; a direction without DMA gets the empty buffer
        {nullptr, 0}.

        dma_enable_min_size 取自 libxr_settings 中 SPI 下的设置，默认为 3。
        dma_enable_min_size comes from the SPI settings in libxr_settings and defaults to 3.
        """
        tx_enabled = config.get("DMA_TX", "DISABLE") == "ENABLE"
        rx_enabled = config.get("DMA_RX", "DISABLE") == "ENABLE"

        spi_config = libxr_settings["SPI"].setdefault(instance.lower(), {})
        dma_min_size = _integer(
            f"SPI.{instance.lower()}.dma_enable_min_size",
            spi_config.setdefault("dma_enable_min_size", 3),
            0,
        )

        tx_buf = f"{instance.lower()}_tx_buf" if tx_enabled else "{nullptr, 0}"
        rx_buf = f"{instance.lower()}_rx_buf" if rx_enabled else "{nullptr, 0}"

        _register_device(f"{instance.lower()}", "SPI")

        return (
            "main",
            f"  static STM32SPI {instance.lower()}(&h{instance.lower()}, {rx_buf}, {tx_buf}, {dma_min_size});\n",
        )

    @staticmethod
    def _generate_iwdg(instance: str, config: dict) -> tuple:
        """生成已启用 IWDG 的 STM32Watchdog 对象；未启用时不生成代码。
        Generate the STM32Watchdog object of an enabled IWDG; a disabled one produces no code.

        超时和喂狗间隔取自 libxr_settings，默认为 1000 ms 和 250 ms。
        Timeout and feed interval come from libxr_settings and default to 1000 ms and 250 ms.
        """
        if not config.get("Enabled"):
            return "", ""
        iwdg_config = libxr_settings["IWDG"].setdefault(instance.lower(), {})
        key = f"IWDG.{instance.lower()}"
        timeout_ms = _integer(f"{key}.timeout_ms", iwdg_config.setdefault("timeout_ms", 1000))
        feed_ms = _integer(
            f"{key}.feed_interval_ms", iwdg_config.setdefault("feed_interval_ms", 250)
        )
        code = (
            f"  static STM32Watchdog {instance.lower()}(&h{instance.lower()}, "
            f"{timeout_ms}, {feed_ms});\n"
        )
        _register_device(instance.lower(), "Watchdog")
        return "main", code

    @staticmethod
    def _generate_usb(instance: str, config: dict) -> tuple:
        """生成 USB 设备对象及其 CDC 串口，并把最终的 USB 设置写入 libxr_settings。
        Generate the USB device object with its CDC serial port and write the final USB settings
        to libxr_settings.

        实例名和设置来自 _usb_settings()；其他实例名（如 USB_DEVICE 中间件）和未启用的实例不生成
        代码。设备对象 usb_fs 或 usb_hs 引用 generate_dma_resources() 定义的端点缓冲区，本方法
        不定义缓冲区。CDC 串口（如 usb_otg_fs_cdc）使用 EP1 收发数据、EP2 发送通知，并登记为
        UART。
        The instance name and settings come from _usb_settings(); other instance names, such as
        the USB_DEVICE middleware, and disabled instances produce no code. The device object,
        usb_fs or usb_hs, references the endpoint buffers that generate_dma_resources()
        defines; this method defines no buffer. The CDC serial port, for example usb_otg_fs_cdc,
        uses EP1 for data and EP2 for notifications and is registered as UART.

        Raises:
            ValueError: 设置了 cdc_count；复合 USB 设备应在 BSP 用户代码中定义。
                cdc_count is set; a composite USB device belongs in BSP user code.
        """
        usb = _usb_settings(instance)
        if usb is None:
            return "", ""
        name, inst_cfg = usb
        inst_lower = name.lower()  # 例如 usb_fs、usb_otg_fs / e.g. usb_fs, usb_otg_fs
        if not inst_cfg["enable"]:
            logging.info(
                tr(
                    f"USB instance '{inst_lower}' is disabled. Skipping generation.",
                    f"USB 实例 '{inst_lower}' 未启用，跳过生成。",
                )
            )
            return "", ""

        is_otg = name.startswith("USB_OTG_")
        speed = "HS" if name.endswith("_HS") else "FS"
        obj = f"usb_{speed.lower()}"  # 例如 usb_fs、usb_hs / e.g. usb_fs, usb_hs

        ep0_sz = _integer("ep0_packet_size", inst_cfg["ep0_packet_size"])
        # _usb_settings() 已检查这些数值，这里只取出整数。
        # _usb_settings() has checked these numbers; this only reads the integers.
        number = {
            key: _integer(key, inst_cfg[key], 0)
            for key in (
                "rx_buffer_size",
                "tx_fifo_size",
                "rx_fifo_size",
                "cdc_tx_fifo_size",
                "cdc_rx_fifo_size",
                "cdc_queue_size",
                "vid",
                "pid",
                "bcd",
            )
        }
        rx_buf_sz = number["rx_buffer_size"]  # USB DMA 缓冲区 / USB DMA
        tx_fifo_size = number["tx_fifo_size"]  # EP1 硬件 FIFO / EP1 HW FIFO
        # OTG 共享的接收 FIFO / OTG shared RX FIFO
        rx_fifo_size = number["rx_fifo_size"]
        cdc_tx_fifo_size = number["cdc_tx_fifo_size"]
        cdc_rx_fifo_size = number["cdc_rx_fifo_size"]
        cdc_queue_size = number["cdc_queue_size"]
        vid = number["vid"]
        pid = number["pid"]
        bcd = number["bcd"]
        manufacturer = str(inst_cfg["manufacturer"]).replace('"', '\\"')
        product = str(inst_cfg["product"]).replace('"', '\\"')
        serial = str(inst_cfg["serial"]).replace('"', '\\"')

        # EP0 包大小的枚举值
        # Size enum for EP0
        size_enum = {8: "SIZE_8", 16: "SIZE_16", 32: "SIZE_32", 64: "SIZE_64"}[ep0_sz]
        lang_var = f"{inst_lower}_lang_pack".upper()
        cdc_var = f"{inst_lower}_cdc"
        pcd_handle = f"hpcd_USB_OTG_{speed}" if is_otg else f"hpcd_USB_{speed}"
        instance_type = (
            "STM32USBDeviceOtgFS"
            if (is_otg and speed == "FS")
            else "STM32USBDeviceOtgHS"
            if (is_otg and speed == "HS")
            else "STM32USBDeviceDevFs"
        )

        # 生成设备的构造代码（缓冲区变量在别处定义）
        # Generate device construction code (buffer variables are defined elsewhere)
        code = []
        code.append(
            f"  static constexpr auto {lang_var} = "
            "LibXR::USB::DescriptorStrings::MakeLanguagePack("
            "LibXR::USB::DescriptorStrings::Language::EN_US, "
            f'"{manufacturer}", "{product}", "{serial}");'
        )
        # 以显式的端点号构造 CDC。
        # CDC1：EP1 IN/OUT 传数据，EP2 IN 传通知。
        # CDC construction with explicit endpoint numbers.
        # CDC1: EP1 IN/OUT data, EP2 IN notification.
        code.append(
            f"  static LibXR::USB::CDCUart {cdc_var}("
            "LibXR::USB::Endpoint::EPNumber::EP1, "
            "LibXR::USB::Endpoint::EPNumber::EP1, "
            "LibXR::USB::Endpoint::EPNumber::EP2, "
            f"{cdc_rx_fifo_size}, {cdc_tx_fifo_size}, {cdc_queue_size});"
        )
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
            code.append(
                f"          {{{inst_lower}_ep0_in_buf, {inst_lower}_ep0_out_buf, {ep0_sz}, {ep0_sz}}},"
            )
            code.append(
                f"          {{{inst_lower}_ep1_in_buf, {inst_lower}_ep1_out_buf, {tx_fifo_size}, {rx_buf_sz}}},"
            )
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


def _generate_header_includes(use_xrobot: bool = False, flash_map: bool = True) -> str:
    """生成 app_main 的 #include 行和 ``using namespace LibXR;``；flash_map 为真时 include
    flash_map.hpp，启用 XRobot 时另外 include xrobot_main.hpp。
    Generate the #include lines of app_main and ``using namespace LibXR;``; flash_map.hpp is
    included when flash_map is set, and xrobot_main.hpp as well with XRobot.
    """
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
    ]
    if flash_map:
        headers.append('#include "flash_map.hpp"')

    if use_xrobot:
        headers.append('#include "xrobot_main.hpp"')

    return "\n".join(headers) + "\n\nusing namespace LibXR;\n"


def _generate_extern_declarations(project_data: dict) -> str:
    """生成 HAL 句柄的 extern 声明，按字母顺序排列且不重复。
    Generate the extern declarations of the HAL handles, sorted and without duplicates.

    包括非 SysTick 时基使用的 TIM、LPTIM 或 HRTIM 句柄和每个外设实例的句柄；USB 实例（不含
    USB_DEVICE 等中间件）使用 PCD 句柄，USART 段（USART、UART 和 LPUART）使用 UART_HandleTypeDef。
    They cover the TIM, LPTIM or HRTIM handle of a timebase other than SysTick and the handle
    of every peripheral instance; USB instances, not middleware such as USB_DEVICE, use their
    PCD handle, and the USART section (USART, UART and LPUART) uses UART_HandleTypeDef.
    """
    externs = set()

    # 时基来源的声明
    # Timebase source declaration
    timebase_cfg = project_data.get("Timebase", {})
    if timebase_cfg.get("Source", "SysTick") != "SysTick":
        src = timebase_cfg["Source"]
        if src.startswith("TIM"):
            externs.add(f"extern TIM_HandleTypeDef h{src.lower()};")
        elif src.startswith("LPTIM"):
            externs.add(f"extern LPTIM_HandleTypeDef h{src.lower()};")
        elif src.startswith("HRTIM"):
            externs.add(f"extern HRTIM_HandleTypeDef h{src.lower()};")

    # 外设的声明
    # Peripheral declarations
    peripherals = project_data.get("Peripherals", {})
    for p_type, instances in peripherals.items():
        for instance in instances:
            if p_type == "USB":
                # USB 使用 PCD 句柄，例如 hpcd_USB_FS、hpcd_USB_OTG_HS。
                # USB uses its PCD handle, e.g. hpcd_USB_FS or hpcd_USB_OTG_HS.
                name = _USB_INSTANCES.get(instance.upper())
                if name is not None:
                    externs.add(f"extern PCD_HandleTypeDef hpcd_{name};")
            elif p_type == "DAC":
                externs.add(f"extern DAC_HandleTypeDef h{instance.lower()};")
            else:
                if p_type == "USART":
                    # USART、UART 和 LPUART 实例都在 USART 下，句柄类型都是 UART_HandleTypeDef。
                    # USART, UART and LPUART instances are all under USART with UART_HandleTypeDef.
                    handle = instance.lower().replace("usart", "uart")
                    externs.add(f"extern UART_HandleTypeDef h{handle};")
                else:
                    externs.add(f"extern {p_type}_HandleTypeDef h{instance.lower()};")

    return "/* External HAL Declarations */\n" + "\n".join(sorted(externs)) + "\n"


_USER_MARKER_TEXT = re.compile(r"(?://|/\*)\s*User\s*Code\s*(?:Begin|End)\b", re.IGNORECASE)
_USER_MARKER_BEGIN = re.compile(r"/\*\s*User Code Begin(?:\s+(.+?))?\s*\*/")
_USER_MARKER_END = re.compile(r"/\*\s*User Code End(?:\s+(.+?))?\s*\*/")
_PREPROC_DIRECTIVE = re.compile(r"#\s*(\w+)")


def _source_line(source: bytes, offset: int) -> int:
    """source 中字节偏移 offset 所在的行号，从 1 开始。
    The 1-based line number of byte offset offset in source.
    """
    return source.count(b"\n", 0, offset) + 1


def validate_user_regions(existing_code: str, region_names) -> None:
    """若改写会丢掉用户放在标记附近的代码，则拒绝改写；只含空白的代码直接通过。
    Refuse a rewrite that would drop code the user placed around markers; code that is only
    whitespace passes.

    改写只保留生成器自己的 User Code 区域的内容。格式错误、改名、重复、不成对、缺失或位于预处理
    条件之内的标记会静默丢失代码或改变预处理器保留的内容，因此每个这样的标记都会被报告。
    Only the bodies of the generator's own User Code regions survive a
    rewrite. A marker that is malformed, renamed, duplicated, unpaired,
    missing or inside a preprocessor conditional would silently lose code or
    change what the preprocessor keeps, so every such marker is reported.

    Args:
        region_names: 生成器输出的 User Code 区域名。
            The names of the User Code regions the generator emits.

    Raises:
        ValueError: 标记有问题；信息逐条列出全部问题。
            A marker has a problem; the message lists every problem.
    """
    if not existing_code.strip():
        return
    document = CppDocument.parse(existing_code)
    source = document.render_bytes()
    expected = list(region_names)
    problems = []
    elements = sorted(
        (
            element
            for element in document.root.descendants(include_trivia=True)
            if element.kind == "comment" or element.kind.startswith("preproc_")
        ),
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
            problems.append(
                tr(
                    f"line {line}: malformed User Code marker {text}",
                    f"第 {line} 行：User Code 标记格式错误：{text}",
                )
            )
            continue
        name = ((begin or end).group(1) or "").strip()
        if depth:
            problems.append(
                tr(
                    f"line {line}: {text} is inside a preprocessor conditional",
                    f"第 {line} 行：{text} 位于预处理条件之内",
                )
            )
        if name not in expected:
            names = ", ".join(expected)
            problems.append(
                tr(
                    f"line {line}: {text} names a region the generator does not emit "
                    f"(expected {names})",
                    f"第 {line} 行：{text} 指定的区域不是生成器输出的区域（应为 {names}）",
                )
            )
        if begin is not None:
            if open_region is not None:
                problems.append(
                    tr(
                        f"line {line}: {text} opens before User Code End {open_region}",
                        f"第 {line} 行：{text} 出现在 User Code End {open_region} 之前",
                    )
                )
            if name in seen:
                problems.append(
                    tr(f"line {line}: {text} is duplicated", f"第 {line} 行：{text} 重复")
                )
            seen.append(name)
            open_region = name
        else:
            if open_region != name:
                problems.append(
                    tr(
                        f"line {line}: {text} has no matching Begin marker",
                        f"第 {line} 行：{text} 没有对应的 Begin 标记",
                    )
                )
            else:
                open_region = None
    if open_region is not None:
        problems.append(
            tr(
                f"User Code Begin {open_region} has no matching End marker",
                f"User Code Begin {open_region} 没有对应的 End 标记",
            )
        )
    for name in expected:
        if name not in seen:
            problems.append(
                tr(
                    f"User Code Begin {name} / End {name} markers are missing",
                    f"缺少 User Code Begin {name} / End {name} 标记",
                )
            )
    if problems:
        raise ValueError(
            tr(
                "existing User Code markers cannot be preserved safely; nothing was "
                "written. Fix the markers and regenerate:\n  ",
                "已有的 User Code 标记无法安全保留，未写入任何文件。请修正这些标记后重新生成：\n  ",
            )
            + "\n  ".join(problems)
        )


def _preserve_generated_regions(existing_code: str, generated_code: str) -> str:
    """把已有代码中 User Code 区域的内容填回新生成的代码并返回结果；其余代码全部重新生成，包括
    clang-format 和 NOLINT 保护的代码。
    Put the User Code bodies of the existing code into the newly generated code and return the
    result; everything else is regenerated, including code protected by clang-format and NOLINT
    markers.

    clang-format 和 NOLINT 控制的是工具，不表示生成代码的归属；嵌套在 User Code 中的标记仍属于保留
    的用户内容。
    clang-format and NOLINT control tooling, not ownership of generated code.
    Markers nested inside User Code remain part of the preserved user body.

    Raises:
        ValueError: validate_user_regions() 拒绝已有代码中的标记。
            validate_user_regions() rejects the markers of the existing code.
    """
    previous = CppDocument.parse(existing_code)
    current = CppDocument.parse(generated_code)
    validate_user_regions(existing_code, [region.name for region in current.user_regions()])
    used = set()
    for old_region in previous.user_regions():
        regions = list(current.user_regions())
        match = next(
            (
                (index, region)
                for index, region in enumerate(regions)
                if index not in used and region.name == old_region.name
            ),
            None,
        )
        if match is None:
            continue
        index, region = match
        current = current.replace_region_body(region, old_region.body_text)
        used.add(index)
    return current.render_bytes().decode("utf-8", errors="surrogateescape")


# LibXR 的线程优先级等级，下标即配置中的数值 0-4。
# The thread priority levels of LibXR; the index is the configuration value 0-4.
_PRIORITY_LEVELS = ("IDLE", "LOW", "MEDIUM", "HIGH", "REALTIME")


def _priority_level(key: str, value) -> str:
    """配置中的线程优先级 value 对应的 LibXR::Thread::Priority 枚举名。
    The LibXR::Thread::Priority enumerator of the thread priority value of the configuration.

    value 是 0-4 的整数或大小写不限的等级名。LibXR 按 RTOS 的优先级数把等级换算为 RTOS 优先级
    （FreeRTOS 数值越大越高，ThreadX 数值越小越高），所以生成的代码写枚举而不写数值。
    value is an integer 0-4 or a level name in any case. LibXR converts the levels to RTOS
    priorities by the RTOS priority count (higher numbers are higher on FreeRTOS, lower numbers
    on ThreadX), so the generated code names the enumerator instead of a number.

    Raises:
        ValueError: value 既不是 0-4 也不是等级名；key 是出错的设置。
            value is neither 0-4 nor a level name; key names the setting.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(_PRIORITY_LEVELS):
            return _PRIORITY_LEVELS[value]
    elif isinstance(value, str) and value.strip().upper() in _PRIORITY_LEVELS:
        return value.strip().upper()
    raise ValueError(
        tr(
            f"{key} {value!r} is not a priority level; use 0-4 or {', '.join(_PRIORITY_LEVELS)}",
            f"{key} {value!r} 不是优先级等级；请使用 0-4 或 {'、'.join(_PRIORITY_LEVELS)}",
        )
    )


def _generate_core_system(project_data: dict) -> str:
    """生成时基对象、PlatformInit() 调用和 power_manager 对象的代码。
    Generate the code of the timebase object, the PlatformInit() call and the power_manager
    object.

    时基来源为 SysTick 时使用 STM32Timebase，否则使用该定时器的 STM32TimerTimebase。FreeRTOS 和
    ThreadX 下 PlatformInit() 取软件定时器的优先级等级（见 _priority_level()）和栈深度；不支持的
    SYSTEM 记录错误并以状态 1 退出。
    SysTick gives STM32Timebase and any other source gives STM32TimerTimebase on that timer.
    Under FreeRTOS and ThreadX PlatformInit() takes the priority level (see _priority_level())
    and stack depth of the software timer; an unsupported SYSTEM logs an error and exits with
    status 1.
    """
    timebase_cfg = project_data.get("Timebase", {"Source": "SysTick"})
    source = timebase_cfg.get("Source", "SysTick")

    timebase_init = "  static STM32Timebase timebase;"  # 默认使用 SysTick / Default to SysTick

    if source != "SysTick":
        handler = f"h{source.lower()}"
        timebase_init = f"  static STM32TimerTimebase timebase(&{handler});"

    system_type = libxr_settings["SYSTEM"]
    timer_cfg = libxr_settings["software_timer"]

    init_args = ""
    if system_type == "None":  # 裸机 / Bare-metal
        init_args = ""
    elif system_type == "FreeRTOS" or system_type == "ThreadX":
        level = _priority_level("software_timer.priority", timer_cfg["priority"])
        stack_depth = _integer("software_timer.stack_depth", timer_cfg["stack_depth"])
        init_args = f"static_cast<uint32_t>(LibXR::Thread::Priority::{level}), {stack_depth}"
    else:
        logging.error(
            tr(f"Unsupported system type: {system_type}", f"不支持的系统类型：{system_type}")
        )
        sys.exit(1)

    return f"""{timebase_init}
  PlatformInit({init_args});
  static STM32PowerManager power_manager;"""


def generate_gpio_config(project_data: dict) -> str:
    """为 GPIO 段中的每个引脚生成一个 STM32GPIO 对象并登记；EXTI 引脚带中断号。
    Generate one STM32GPIO object per pin of the GPIO section and register it; EXTI pins get
    their IRQ number.
    """
    code = "\n  /* GPIO Configuration */\n"
    for port, config in project_data.get("GPIO", {}).items():
        alias = generate_gpio_alias(port, config, project_data)
        code += f"  static STM32GPIO {alias};\n"
    return code


# 看门狗
# Watchdog
def configure_watchdog(project_data: dict) -> str:
    """为每个已启用的 IWDG 生成首次喂狗和周期喂狗的代码；没有已启用的 IWDG 时为空字符串。
    Generate the first feed and the periodic feeding of every enabled IWDG; an empty string
    when no IWDG is enabled.

    libxr_settings 中 Watchdog 的 run_as_thread 为真时由独立线程喂狗，线程优先级见
    _priority_level()；否则由软件定时器任务每隔 feed_interval_ms（默认 250）喂狗一次。
    With run_as_thread of Watchdog in libxr_settings a thread of its own feeds the watchdog,
    its priority given as in _priority_level(); otherwise a software timer task feeds it every
    feed_interval_ms (default 250).
    """
    code = ""
    watchdog_instances = []
    for name, cfg in project_data.get("Peripherals", {}).get("IWDG", {}).items():
        if cfg.get("Enabled"):
            watchdog_instances.append(name.lower())
    if not watchdog_instances:
        return code

    wdg_config = libxr_settings.setdefault("Watchdog", {})
    run_as_thread = wdg_config.setdefault("run_as_thread", False)
    feed_interval = _integer(
        "Watchdog.feed_interval_ms", wdg_config.setdefault("feed_interval_ms", 250)
    )

    for name in watchdog_instances:
        code += f"""  {name}.Feed();
"""
        if run_as_thread:
            thread_stack = _integer(
                "Watchdog.thread_stack_depth", wdg_config.setdefault("thread_stack_depth", 1024)
            )
            level = _priority_level(
                "Watchdog.thread_priority", wdg_config.setdefault("thread_priority", 3)
            )
            code += f"""  static LibXR::Thread {name}_thread;
  {name}_thread.Create(reinterpret_cast<LibXR::Watchdog *>(&{name}), {name}.ThreadFun, "{name}_wdg", {thread_stack},
                      LibXR::Thread::Priority::{level});
"""
        else:
            code += f"""  static auto {name}_task = Timer::CreateTask({name}.TaskFun, reinterpret_cast<LibXR::Watchdog *>(&{name}), {feed_interval});
  Timer::Add({name}_task);
  Timer::Start({name}_task);
"""
    return code


# --------------------------
# 终端配置 / Terminal Configuration
# --------------------------
def configure_terminal(project_data: dict) -> str:
    """把 terminal_source 指定的串口设为标准输入输出，并生成 RamFS、Terminal 对象及运行终端的代码。
    Make the UART named by terminal_source the standard I/O and generate the RamFS and Terminal
    objects and the code that runs the terminal.

    terminal_source 为空时只输出注释行；它未登记为 UART 时记录警告，不初始化终端。Terminal 的
    run_as_thread 为真时终端运行于独立线程（优先级见 _priority_level()），否则由软件定时器任务每
    10 ms 运行一次。
    With an empty terminal_source only the comment line is produced; when it is not registered
    as UART a warning is logged and the terminal is not initialized. With run_as_thread of
    Terminal the terminal runs in a thread of its own, its priority given as in
    _priority_level(); otherwise a software timer task runs it every 10 ms.
    """
    code = "  /* Terminal Configuration */\n"
    terminal_source = libxr_settings.get("terminal_source", "").lower()

    # 用户指定的终端来源
    # User-specified terminal source
    if terminal_source != "":
        # 设备必须已登记且类型为 UART，否则记录警告并跳过
        # Device must be registered and of type UART, otherwise log a warning and skip
        if registered_devices.get(terminal_source) != "UART":
            logging.warning(
                tr(
                    f"terminal_source '{terminal_source}' is not registered as UART, terminal "
                    "will not be initialized.",
                    f"terminal_source '{terminal_source}' 没有登记为 UART，不初始化终端。",
                )
            )
            return code
        code += (
            f"  STDIO::read_ = {terminal_source}.read_port_;\n"
            f"  STDIO::write_ = {terminal_source}.write_port_;\n"
        )

    if terminal_source != "":
        term_config = libxr_settings.setdefault("Terminal", {})
        params = [
            _integer(f"Terminal.{key}", term_config.setdefault(key, default))
            for key, default in (
                ("read_buff_size", 32),
                ("max_line_size", 32),
                ("max_arg_number", 5),
                ("max_history_number", 5),
            )
        ]

        run_as_thread = term_config.setdefault("run_as_thread", False)

        if run_as_thread:
            thread_stack_depth = _integer(
                "Terminal.thread_stack_depth", term_config.setdefault("thread_stack_depth", 1024)
            )
            level = _priority_level(
                "Terminal.thread_priority", term_config.setdefault("thread_priority", 3)
            )

        code += f"""
  static RamFS ramfs("XRobot");
  static Terminal<{", ".join(map(str, params))}> terminal(ramfs);
"""
        if run_as_thread:
            code += f"""\
  static LibXR::Thread term_thread;
  term_thread.Create(&terminal, terminal.ThreadFun, "terminal", {thread_stack_depth},
                     LibXR::Thread::Priority::{level});
"""
        else:
            code += """\
  static auto terminal_task = Timer::CreateTask(terminal.TaskFun, &terminal, 10);
  Timer::Add(terminal_task);
  Timer::Start(terminal_task);
"""
        _register_device("ramfs", "RamFS")
        _register_device("terminal", f"Terminal<{', '.join(map(str, params))}>")
    return code


# --------------------------
# XRobot 集成 / XRobot Integration
# --------------------------
def generate_xrobot_registrations() -> str:
    """为每个登记的对象生成一行 XR_REGISTER，使静态入口无需运行时容器即可按名字取得 BSP 对象。
    Generate one XR_REGISTER line per registered object, exposing the named BSP objects to the
    static entry without a runtime container.

    每个生成的设备对象以自己的 C++ 名字登记，类型缺少 LibXR:: 前缀时补上；YAML 配置按这些名字
    选择硬件。
    Every generated device object is registered under its own C++ name, with LibXR:: added to
    a type that lacks it; the YAML configuration selects hardware by these names.

    Raises:
        ValueError: 名字不是合法的 C++ 标识符，或缺少类型。
            A name is not a valid C++ identifier, or its type is missing.
    """
    lines = []
    for name, cpp_type in registered_devices.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise ValueError(
                tr(
                    f"Static registration needs an existing C++ name: {name}",
                    f"静态登记需要已有的 C++ 名字：{name}",
                )
            )
        if not isinstance(cpp_type, str) or not cpp_type or cpp_type == "Unknown":
            raise ValueError(
                tr(
                    f"Explicit registration type is missing for {name}",
                    f"{name} 缺少显式的登记类型",
                )
            )
        if not cpp_type.startswith("LibXR::"):
            cpp_type = "LibXR::" + cpp_type
        lines.append(f"  XR_REGISTER({name}, {cpp_type});")
    return "\n".join(lines) + "\n"


# --------------------------
# 主生成流程 / Main Generator
# --------------------------
def reject_user_xrobot_main(existing_code: str) -> None:
    """拒绝在 User Code 区域中调用 XROBOT_MAIN() 的已有代码；该调用属于生成器，区域中的副本是遗留。
    Reject existing code that calls XROBOT_MAIN() inside a User Code region; the call belongs
    to the generator, and a User Code copy is a leftover.

    旧版生成器把该调用作为 User Code 3 的默认内容。保留这份副本会产生第二个入口调用，因此由用户
    删除。
    Older generators emitted the call as the default body of User Code 3.
    Keeping that copy would leave a second entry call, so the user deletes it.

    Raises:
        ValueError: 某个 User Code 区域调用了 XROBOT_MAIN()。
            A User Code region calls XROBOT_MAIN().
    """
    if not existing_code.strip():
        return
    document = CppDocument.parse(existing_code)
    for region in document.user_regions():
        for invocation in document.invocation_views("XROBOT_MAIN"):
            if region.body_span.start <= invocation.span.start < region.body_span.end:
                raise ValueError(
                    tr(
                        f"line {invocation.line}: User Code {region.name} still calls "
                        f"{invocation.text}. The generator now emits XROBOT_MAIN() after "
                        "the User Code regions of app_main; delete this call from the "
                        "User Code region and regenerate. Nothing was written.",
                        f"第 {invocation.line} 行：User Code {region.name} 仍然调用 "
                        f"{invocation.text}。生成器现在在 app_main 的 User Code 区域之后输出 "
                        "XROBOT_MAIN()；请从 User Code 区域中删除这一调用后重新生成。"
                        "未写入任何文件。",
                    )
                )


GENERATED_NOTICE = "// Generated by `libxr gen`; do not edit by hand."
APP_MAIN_NOTICE = (
    '// Generated by `libxr gen`. Edit only between "User Code Begin" and\n'
    '// "User Code End"; everything else is rewritten when the file is regenerated.'
)


def generate_full_code(
    project_data: dict, use_xrobot: bool, existing_code: str, flash_map: bool = True
) -> str:
    """生成 app_main 源文件的完整内容，并填回已有代码中 User Code 区域的内容。
    Generate the full content of the app_main source file and put back the User Code bodies of
    the existing code.

    启用 XRobot 时为每个生成的对象输出 XR_REGISTER，并在 User Code 3 之后调用 XROBOT_MAIN()；
    否则 User Code 3 的默认内容是一个无限休眠的循环。flash_map 为假时不 include flash_map.hpp。
    With XRobot every generated object gets an XR_REGISTER line and XROBOT_MAIN() is called
    after User Code 3; otherwise the default body of User Code 3 is a loop that sleeps forever.
    Without flash_map, flash_map.hpp is not included.

    Raises:
        ValueError: 生成的对象名冲突，或 GPIO 名字、User Code 标记、遗留的 XROBOT_MAIN() 调用
            不合要求。
            Generated object names collide, or a GPIO name, a User Code marker or a leftover
            XROBOT_MAIN() call is rejected.
    """
    if use_xrobot:
        reject_user_xrobot_main(existing_code)
    user_code_def_3 = "" if use_xrobot else "  while(true) {\n    Thread::Sleep(UINT32_MAX);\n  }\n"
    components = [
        APP_MAIN_NOTICE,
        _generate_header_includes(use_xrobot, flash_map),
        "/* User Code Begin 1 */",
        "/* User Code End 1 */",
        "// NOLINTBEGIN",
        "// clang-format off",
        _generate_extern_declarations(project_data),
        generate_dma_resources(project_data),
        '\nextern "C" void app_main(void) {',
        "  // clang-format on",
        "  // NOLINTEND",
        "  /* User Code Begin 2 */",
        "  /* User Code End 2 */",
        "  // clang-format off",
        "  // NOLINTBEGIN",
        _generate_core_system(project_data),
        generate_gpio_config(project_data),
        generate_peripheral_instances(project_data, use_xrobot),
        configure_terminal(project_data),
        configure_watchdog(project_data),
        generate_xrobot_registrations() if use_xrobot else "",
        "  // clang-format on",
        "  // NOLINTEND",
        "  /* User Code Begin 3 */",
        user_code_def_3.rstrip("\n"),
        "  /* User Code End 3 */",
        "  XROBOT_MAIN();" if use_xrobot else "",
        "}",
    ]
    generated = "\n".join(filter(None, components))
    check_gpio_names(project_data, generated, use_xrobot)
    return _preserve_generated_regions(existing_code, generated)


APP_MAIN_HEADER = (
    GENERATED_NOTICE
    + "\n"
    + """#ifdef __cplusplus
extern "C" {
#endif

void app_main(void);

#ifdef __cplusplus
}
#endif
"""
)


def generate_flash_map_cpp(flash_info: dict) -> str:
    """把 Flash 布局字典转换为 C++ 代码：constexpr 数组 FLASH_SECTORS 和扇区数 FLASH_SECTOR_NUMBER。
    Convert a Flash layout dictionary into C++ code: the constexpr array FLASH_SECTORS and the
    sector count FLASH_SECTOR_NUMBER.

    扇区大小按字节写出；STM32L0、L1 的页小于 1 KB（如 0.125 KB 即 128 字节）。
    Sector sizes are written in bytes; STM32L0 and L1 pages are below 1 KB, such as 0.125 KB,
    that is 128 bytes.

    Args:
        flash_info: flash_info_to_dict() 的输出；每个扇区有十六进制的 address 和 size_kb。
            The output of flash_info_to_dict(); each sector has a hexadecimal address and
            size_kb.
    """
    lines = [
        '#include "stm32_flash.hpp"',
        "",
        "constexpr LibXR::FlashSector FLASH_SECTORS[] = {",
    ]

    for s in flash_info["sectors"]:
        address = int(s["address"], 16)
        size = round(float(s["size_kb"]) * 1024)
        lines.append(f"  {{0x{address:08X}, 0x{size:08X}}},")

    lines.append("};\n")
    lines.append(
        "constexpr size_t FLASH_SECTOR_NUMBER = sizeof(FLASH_SECTORS) / sizeof(LibXR::FlashSector);"
    )
    return "\n".join(lines)


def flash_layout(project_data: dict) -> dict | None:
    """project_data['Mcu']['Type'] 的 Flash 布局，即 flash_info_to_dict() 的输出。
    The Flash layout of project_data['Mcu']['Type'], as flash_info_to_dict() returns it.

    缺少 MCU 型号或推算不出布局时记录警告并返回 None；这时不生成 flash_map.hpp，app_main 也不
    include 它。
    A missing MCU type or a layout that cannot be derived logs a warning and gives None;
    flash_map.hpp is then not generated and app_main does not include it.
    """
    from libxr.stm32_flash_generator import flash_info_to_dict, layout_flash

    mcu_model = (project_data.get("Mcu", {}).get("Type") or "").strip()
    if not mcu_model:
        logging.warning(
            tr(
                "Cannot find the MCU type; flash_map.hpp is not generated",
                "找不到 MCU 型号；不生成 flash_map.hpp",
            )
        )
        return None
    try:
        return flash_info_to_dict(layout_flash(mcu_model))
    except ValueError as error:
        logging.warning(
            tr(
                f"Cannot derive the flash layout of {mcu_model}: {error}; flash_map.hpp is not "
                "generated",
                f"无法推算 {mcu_model} 的 Flash 布局：{error}；不生成 flash_map.hpp",
            )
        )
        return None


def flash_map_header(flash_info: dict, mcu_model: str) -> str:
    """flash_map.hpp 的内容：生成说明、MCU 型号和 generate_flash_map_cpp() 的代码。
    The content of flash_map.hpp: the generated-file notice, the MCU type and the code of
    generate_flash_map_cpp().
    """
    return (
        f'#pragma once\n{GENERATED_NOTICE}\n// MCU: {mcu_model}\n\n#include "main.h"\n\n'
        + generate_flash_map_cpp(flash_info)
    )


def _remove_generated(path: str) -> bool:
    """删除 libxr gen 生成的文件 path（第二行是生成说明）；不存在或不是生成的文件时不删。删除时
    为 True。
    Delete path when libxr gen generated it, its second line being the generated-file notice;
    a missing file or one written by hand stays. True when deleted.
    """
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return False
    if GENERATED_NOTICE not in lines[:2]:
        return False
    os.remove(path)
    return True


def check_generator_pin() -> None:
    """libxr_config.yaml 固定的 generator 版本与已安装的 libxr 不同时给出警告。
    Warn when the generator version pinned in libxr_config.yaml differs from the installed libxr.

    BSP 的 CI 安装固定的版本重新生成并与提交的文件比较，用其他版本生成的文件可能与之不同。固定为
    commit 时不比较。
    BSP CI installs the pinned version, regenerates and compares with the committed files, so
    files generated by another version may differ. A pin to a commit is not compared.
    """
    pin = libxr_settings.get("generator")
    installed = update_notice.installed_version()
    if pin is None or installed is None:
        return
    pin = str(pin)
    if pin == installed or re.fullmatch(r"[0-9a-f]{40}", pin):
        return
    logging.warning(
        tr(
            f"libxr_config.yaml pins generator {pin}, but libxr {installed} is installed; "
            f"the BSP CI generates with {pin}",
            f"libxr_config.yaml 固定的 generator 是 {pin}，已安装的 libxr 是 {installed}；"
            f"BSP 的 CI 用 {pin} 生成",
        )
    )


def generate(
    input_path: str, output_path: str, use_xrobot: bool = False, libxr_config: str = ""
) -> None:
    """生成 output_path 指定的 app_main 源文件，以及同一目录中的 app_main.h、flash_map.hpp 和
    libxr_config.yaml。
    Generate the app_main source file output_path, and app_main.h, flash_map.hpp and
    libxr_config.yaml in the same directory.

    每次从默认设置开始，合并 libxr_config（libxr_config.yaml 的路径或 URL，为空时读取输出目录中
    的文件）。全部文件先在内存中生成，没有错误时才写出，并且只写内容有变化的文件，其余文件的修改
    时间不变。推算不出 Flash 布局时删除以前生成的 flash_map.hpp。已有输出文件中 User Code 区域的
    内容被保留。出错时记录错误（调试日志另记调用栈）并以状态 1 退出。
    Every run starts from the default settings and merges libxr_config, the path or URL of
    libxr_config.yaml, or the file in the output directory when empty. All files are generated
    in memory first and written only when nothing failed, and only the files whose content
    changed are written, so the others keep their modification time. When no Flash layout can
    be derived, a previously generated flash_map.hpp is deleted. The User Code bodies of an
    existing output file are kept. An error is logged, with the traceback at debug level, and
    exits with status 1.
    """
    try:
        # 只给出文件名时写入当前目录。
        # A bare file name writes into the current directory.
        output_dir = os.path.dirname(output_path) or os.curdir

        reset_settings()
        project_data = load_configuration(input_path)
        load_libxr_config(output_dir, libxr_config)
        check_generator_pin()
        initialize_registry(use_xrobot)

        existing_code = ""
        if os.path.exists(output_path):
            with open(output_path, encoding="utf-8") as f:
                existing_code = f.read()

        layout = flash_layout(project_data)
        files = {
            os.path.basename(output_path): generate_full_code(
                project_data, use_xrobot, existing_code, flash_map=layout is not None
            ),
            "app_main.h": APP_MAIN_HEADER,
        }
        if layout is None:
            libxr_settings.pop("FlashLayout", None)
            files["flash_map.hpp"] = None
        else:
            libxr_settings["FlashLayout"] = layout
            files["flash_map.hpp"] = flash_map_header(layout, project_data["Mcu"]["Type"].strip())
        files["libxr_config.yaml"] = libxr_config_text()

        os.makedirs(output_dir, exist_ok=True)
        written, unchanged, removed = [], [], []
        for name, text in files.items():
            path = os.path.join(output_dir, name)
            if text is None:
                if _remove_generated(path):
                    removed.append(name)
            elif _write_if_changed(path, text):
                written.append(name)
            else:
                unchanged.append(name)
        _report_files(output_dir, written, unchanged, removed)

    except Exception as e:
        logging.error(tr(f"Generation failed: {str(e)}", f"生成失败：{str(e)}"))
        logging.debug(tr("Traceback:", "调用栈："), exc_info=True)
        sys.exit(1)


def _report_files(output_dir: str, written: list, unchanged: list, removed: list) -> None:
    """用一行日志列出 output_dir 中写入、未变化和删除的文件。
    Log in one line which files of output_dir were written, unchanged or deleted.
    """
    english = []
    chinese = []
    if written:
        english.append("wrote " + ", ".join(written))
        chinese.append("已写入 " + "、".join(written))
    if unchanged:
        english.append("unchanged " + ", ".join(unchanged))
        chinese.append("未变化 " + "、".join(unchanged))
    if removed:
        english.append("removed " + ", ".join(removed))
        chinese.append("已删除 " + "、".join(removed))
    directory = os.path.normpath(output_dir)
    logging.info(
        tr(
            f"Generated {directory}: " + "; ".join(english),
            f"已生成 {directory}：" + "；".join(chinese),
        )
    )


if __name__ == "__main__":
    from libxr.legacy import run

    raise SystemExit(run("xr_gen_code_stm32"))
