"""xr_stm32_flash：由 STM32 型号推算内部 Flash 容量和擦除扇区布局。
xr_stm32_flash: derive the internal flash size and erase sector layout of an STM32 model.

容量取自型号中的容量代码；扇区布局按 STM32FlashLayoutRules.xml 中第一条匹配的规则生成。
The size comes from the capacity code in the model; the sector layout follows the first matching
rule in STM32FlashLayoutRules.xml.
"""

import re
import sys
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from re import Pattern

import yaml


@dataclass
class FlashSector:
    """一个 Flash 擦除扇区：序号、起始地址和字节数。
    One flash erase sector: index, start address and size in bytes.
    """

    index: int
    address: int
    size: int


@dataclass
class FlashInfo:
    """一个型号的 Flash 布局：型号、Flash 基地址、全部擦除扇区和容量（KB）。
    The flash layout of a model: model, flash base address, all erase sectors and size in KB.
    """

    model: str
    flash_base: int
    flash_sectors: list[FlashSector]
    flash_size_kb: int | None = None


@dataclass(frozen=True)
class FlashRule:
    """STM32FlashLayoutRules.xml 中的一条规则。
    One rule of STM32FlashLayoutRules.xml.

    prefix、contains、regex 和 flash_kb 系列字段是匹配条件，为 None 时不参与匹配。layout_type 为
    uniform、sequence 或 banked，决定使用 size_bytes、pattern_kb 和 bank 字段中的哪些。
    prefix, contains, regex and the flash_kb fields are match conditions, ignored when None.
    layout_type is uniform, sequence or banked and selects which of size_bytes, pattern_kb and
    the bank fields apply.
    """

    name: str
    prefix: str | None
    contains: str | None
    regex: Pattern[str] | None
    flash_kb: int | None
    flash_kb_min: int | None
    flash_kb_max: int | None
    layout_type: str
    size_bytes: int | None
    pattern_kb: tuple[int, ...]
    bank_kb: int | None
    bank_address_stride_kb: int | None


FLASH_SIZE_CODES = {
    "4": 16,
    "6": 32,
    "8": 64,
    "B": 128,
    "C": 256,
    "D": 384,
    "E": 512,
    "F": 768,
    "G": 1024,
    "H": 1536,
    "I": 2048,
    "J": 3072,
    "K": 4096,
    "L": 6144,
    "M": 8192,
    "N": 12288,
    "P": 16384,
    "Q": 512,
    "R": 640,
    "S": 768,
    "T": 1024,
    "Z": 512,
    "5": 512,
    "7": 2048,
    "A": 1024,
    "V": 1024,
    "W": 512,
    "X": 1024,
    "Y": 2048,
    "1": 256,
    "3": 8,
}

U5_FLASH_SIZE_CODES = dict(FLASH_SIZE_CODES)
U5_FLASH_SIZE_CODES["J"] = 4096

_FLASH_BASE = 0x08000000


def get_flash_kb(model: str) -> int:
    """由 STM32 型号中的容量代码得到 Flash 容量（KB）。
    The flash size in KB from the capacity code in an STM32 model.

    一般型号取第 11 个字符查 FLASH_SIZE_CODES；STM32U5 查 U5_FLASH_SIZE_CODES（J 为 4096），
    代码不在表中时为 None；STM32WBA 取第 12 个字符，查不到时为 512；STM32WL 和 STM32WB 按型号中
    的个别字符判断。
    Most models look up the 11th character in FLASH_SIZE_CODES; STM32U5 uses
    U5_FLASH_SIZE_CODES (where J is 4096) and gives None for a code missing from it; STM32WBA
    uses the 12th character and falls back to 512; STM32WL and STM32WB are decided by specific
    characters in the model.

    Raises:
        ValueError: 型号为 STM32N6（没有内部用户 Flash），或容量代码无法识别。
            The model is an STM32N6, which has no internal user flash, or the capacity code is
            not recognized.
    """
    model = model.strip().upper()

    if model.startswith("STM32N6"):
        raise ValueError("STM32N6 devices do not provide internal user flash")
    if model.startswith("STM32WL"):
        if "X" in model:
            return 1024
        if any(code in model for code in ["55", "54", "53", "52"]):
            return 512
        return 512
    if model.startswith("STM32WBA"):
        code = model[11]
        return FLASH_SIZE_CODES.get(code, 512)
    if model.startswith("STM32WB"):
        if "7" in model:
            return 2048
        if "V" in model:
            return 1024
        return 512
    if model.startswith("STM32U5"):
        code = model[10]
        return U5_FLASH_SIZE_CODES.get(code)

    try:
        return FLASH_SIZE_CODES[model[10]]
    except (KeyError, IndexError):
        raise ValueError(f"Unrecognized capacity code for {model}") from None


def layout_flash(model: str) -> FlashInfo:
    """STM32 型号的 Flash 布局：容量由型号得到，扇区按 STM32FlashLayoutRules.xml 中第一条匹配的
    规则生成，没有匹配规则时为 1 KB 的均匀扇区。
    The flash layout of an STM32 model: the size comes from the model and the sectors from the
    first matching rule in STM32FlashLayoutRules.xml, or 1 KB uniform sectors without a match.

    Raises:
        ValueError: 无法从型号得到容量，或规则无法恰好覆盖该容量。
            The size cannot be derived from the model, or the rule does not cover it exactly.
    """
    model = model.strip().upper()
    flash_kb = get_flash_kb(model)

    rule = _match_flash_rule(model, flash_kb)
    if rule is None:
        sector_entries = _build_contiguous_sector_entries(
            _build_uniform_sector_sizes(flash_kb, 1024)
        )
    else:
        sector_entries = _build_sector_entries_from_rule(rule, flash_kb)

    return _build_flash_info(model, flash_kb, sector_entries)


def flash_info_to_dict(info: FlashInfo) -> dict:
    """把 FlashInfo 转为用于 YAML 输出的字典：地址写成 0x 开头的 8 位十六进制，扇区大小以 KB 表示
    并保留 3 位小数。
    Convert a FlashInfo to a dict for YAML output: addresses as 8-digit 0x hexadecimal and sector
    sizes in KB rounded to 3 decimals.
    """
    return {
        "model": info.model,
        "flash_base": f"0x{info.flash_base:08X}",
        "flash_size_kb": info.flash_size_kb,
        "sectors": [
            {
                "index": sector.index,
                "address": f"0x{sector.address:08X}",
                "size_kb": round(sector.size / 1024, 3),
            }
            for sector in info.flash_sectors
        ],
    }


def _build_flash_info(
    model: str,
    flash_kb: int,
    sector_entries: list[tuple[int, int]],
) -> FlashInfo:
    """由 (地址, 字节数) 列表构造 FlashInfo：扇区按顺序从 0 编号，基地址为 0x08000000。
    Build a FlashInfo from (address, size in bytes) entries: sectors are numbered in order from
    0, and the base address is 0x08000000.
    """
    sectors = []

    for address, size in sector_entries:
        sectors.append(
            FlashSector(
                index=len(sectors),
                address=address,
                size=size,
            )
        )

    return FlashInfo(
        model=model,
        flash_base=_FLASH_BASE,
        flash_sectors=sectors,
        flash_size_kb=flash_kb,
    )


def _build_contiguous_sector_entries(
    sector_sizes: list[int],
    start_address: int = _FLASH_BASE,
) -> list[tuple[int, int]]:
    """从 start_address 起依次紧接排列扇区，返回 (地址, 字节数) 列表。
    Lay sectors out back to back from start_address and return (address, size in bytes) entries.
    """
    sector_entries = []
    address = start_address

    for size in sector_sizes:
        sector_entries.append((address, size))
        address += size

    return sector_entries


def _build_uniform_sector_sizes(flash_kb: int, size_bytes: int) -> list[int]:
    """把 flash_kb 均分为每个 size_bytes 字节的扇区，返回各扇区字节数。
    Split flash_kb into sectors of size_bytes each and return their sizes in bytes.

    Raises:
        ValueError: 容量不能被扇区大小整除。
            The flash size is not a multiple of the sector size.
    """
    total_bytes = flash_kb * 1024
    if total_bytes % size_bytes != 0:
        raise ValueError(f"Flash size {flash_kb}KB is not divisible by erase size {size_bytes}B")
    return [size_bytes] * (total_bytes // size_bytes)


def _build_sequence_sector_sizes(flash_kb: int, pattern_kb: tuple[int, ...]) -> list[int]:
    """按 pattern_kb 的顺序取扇区直到填满 flash_kb，返回各扇区字节数。
    Take sectors in pattern_kb order until flash_kb is filled and return their sizes in bytes.

    Raises:
        ValueError: pattern_kb 没有哪一段前缀之和恰好等于 flash_kb。
            No leading part of pattern_kb adds up to exactly flash_kb.
    """
    remaining_kb = flash_kb
    sector_sizes = []

    for size_kb in pattern_kb:
        if remaining_kb < size_kb:
            break
        sector_sizes.append(size_kb * 1024)
        remaining_kb -= size_kb

    if remaining_kb != 0:
        raise ValueError(f"Flash size {flash_kb}KB is not fully covered by sequence {pattern_kb}")
    return sector_sizes


def _build_banked_sector_entries(
    flash_kb: int,
    bank_kb: int,
    pattern_kb: tuple[int, ...],
    bank_address_stride_kb: int,
) -> list[tuple[int, int]]:
    """按 bank 生成 (地址, 字节数) 列表：第 n 个 bank 从 0x08000000 + n * bank_address_stride_kb KB
    开始，按 pattern_kb 排列扇区；最后一个 bank 可以不满，但必须恰好由 pattern_kb 的前几项填满。
    Build (address, size in bytes) entries bank by bank: bank n starts at
    0x08000000 + n * bank_address_stride_kb KB and follows pattern_kb; the last bank may be
    partial but must be filled exactly by a leading part of pattern_kb.

    Raises:
        ValueError: pattern_kb 之和不等于 bank_kb，或某个 bank 无法被恰好填满。
            pattern_kb does not add up to bank_kb, or a bank cannot be filled exactly.
    """
    if sum(pattern_kb) != bank_kb:
        raise ValueError(f"Bank pattern {pattern_kb} does not sum to bank size {bank_kb}KB")

    remaining_kb = flash_kb
    bank_index = 0
    sector_entries = []

    while remaining_kb > 0:
        bank_remaining_kb = min(remaining_kb, bank_kb)
        bank_used_kb = 0
        address = _FLASH_BASE + bank_index * bank_address_stride_kb * 1024

        for size_kb in pattern_kb:
            if bank_used_kb + size_kb > bank_remaining_kb:
                break
            size_bytes = size_kb * 1024
            sector_entries.append((address, size_bytes))
            address += size_bytes
            bank_used_kb += size_kb

        if bank_used_kb != bank_remaining_kb:
            raise ValueError(f"Bank pattern {pattern_kb} does not cover {bank_remaining_kb}KB")

        remaining_kb -= bank_remaining_kb
        bank_index += 1

    return sector_entries


def _build_sector_entries_from_rule(
    rule: FlashRule,
    flash_kb: int,
) -> list[tuple[int, int]]:
    """按规则的布局类型生成 (地址, 字节数) 列表：uniform 为均匀扇区，sequence 为按序列紧接排列的
    扇区，banked 为分 bank 的扇区（地址步长默认等于 bank 大小）。
    Build (address, size in bytes) entries by the rule's layout type: uniform sectors, a
    back-to-back sequence, or banked sectors whose address stride defaults to the bank size.

    Raises:
        ValueError: 规则缺少所需字段、布局类型不受支持，或无法恰好覆盖 flash_kb。
            The rule lacks a required field, has an unsupported layout type, or does not cover
            flash_kb exactly.
    """
    if rule.layout_type == "uniform":
        if rule.size_bytes is None:
            raise ValueError(f"Rule {rule.name} is missing size_bytes")
        return _build_contiguous_sector_entries(
            _build_uniform_sector_sizes(flash_kb, rule.size_bytes)
        )

    if rule.layout_type == "sequence":
        return _build_contiguous_sector_entries(
            _build_sequence_sector_sizes(flash_kb, rule.pattern_kb)
        )

    if rule.layout_type == "banked":
        if rule.bank_kb is None:
            raise ValueError(f"Rule {rule.name} is missing bank_kb")
        bank_address_stride_kb = rule.bank_address_stride_kb or rule.bank_kb
        return _build_banked_sector_entries(
            flash_kb,
            rule.bank_kb,
            rule.pattern_kb,
            bank_address_stride_kb,
        )

    raise ValueError(f"Unsupported flash layout type: {rule.layout_type}")


@lru_cache(maxsize=1)
def _load_flash_rules() -> tuple[FlashRule, ...]:
    """读取并解析与本模块同目录的 STM32FlashLayoutRules.xml，按文件中的顺序返回规则；只解析一次。
    Read and parse STM32FlashLayoutRules.xml next to this module and return its rules in file
    order; the file is parsed only once.

    Raises:
        ValueError: 某条规则不是恰好含一个布局元素，或布局元素不是 uniform、sequence、banked 之一。
            A rule does not hold exactly one layout element, or the element is not uniform,
            sequence or banked.
    """
    rule_path = Path(__file__).resolve().with_name("STM32FlashLayoutRules.xml")
    root = ET.parse(rule_path).getroot()
    rules = []

    for rule_elem in root.findall("rule"):
        child_elements = list(rule_elem)
        if len(child_elements) != 1:
            raise ValueError("Each flash rule must contain exactly one layout element")

        layout_elem = child_elements[0]
        layout_tag = _local_name(layout_elem.tag)
        size_bytes = None
        pattern_kb: tuple[int, ...] = tuple()
        bank_kb = None

        if layout_tag == "uniform":
            size_bytes = _parse_rule_size_bytes(layout_elem)
        elif layout_tag == "sequence":
            pattern_kb = _parse_pattern_kb(layout_elem.attrib["sizes_kb"])
        elif layout_tag == "banked":
            bank_kb = int(layout_elem.attrib["bank_kb"], 0)
            pattern_kb = _parse_pattern_kb(layout_elem.attrib["sizes_kb"])
        else:
            raise ValueError(f"Unsupported layout tag: {layout_tag}")

        regex = rule_elem.attrib.get("regex")
        rules.append(
            FlashRule(
                name=rule_elem.attrib["name"],
                prefix=_normalize_optional(rule_elem.attrib.get("prefix")),
                contains=_normalize_optional(rule_elem.attrib.get("contains")),
                regex=re.compile(regex) if regex else None,
                flash_kb=_parse_optional_int(rule_elem.attrib.get("flash_kb")),
                flash_kb_min=_parse_optional_int(rule_elem.attrib.get("flash_kb_min")),
                flash_kb_max=_parse_optional_int(rule_elem.attrib.get("flash_kb_max")),
                layout_type=layout_tag,
                size_bytes=size_bytes,
                pattern_kb=pattern_kb,
                bank_kb=bank_kb,
                bank_address_stride_kb=_parse_optional_int(
                    layout_elem.attrib.get("address_stride_kb")
                ),
            )
        )

    return tuple(rules)


def _match_flash_rule(model: str, flash_kb: int) -> FlashRule | None:
    """按顺序返回第一条与型号和容量都匹配的规则；没有时为 None。
    The first rule, in order, that matches both the model and the flash size; None when there is
    none.
    """
    for rule in _load_flash_rules():
        if rule.prefix is not None and not model.startswith(rule.prefix):
            continue
        if rule.contains is not None and rule.contains not in model:
            continue
        if rule.regex is not None and not rule.regex.search(model):
            continue
        if rule.flash_kb is not None and flash_kb != rule.flash_kb:
            continue
        if rule.flash_kb_min is not None and flash_kb < rule.flash_kb_min:
            continue
        if rule.flash_kb_max is not None and flash_kb > rule.flash_kb_max:
            continue
        return rule
    return None


def _parse_rule_size_bytes(elem: ET.Element) -> int:
    """uniform 元素的扇区字节数：取 size_bytes 属性，没有时取 size_kb * 1024。
    The sector size in bytes of a uniform element: its size_bytes attribute, else size_kb * 1024.

    Raises:
        ValueError: 两个属性都没有。
            Neither attribute is present.
    """
    if "size_bytes" in elem.attrib:
        return int(elem.attrib["size_bytes"], 0)
    if "size_kb" in elem.attrib:
        return int(elem.attrib["size_kb"], 0) * 1024
    raise ValueError("uniform layout must provide size_bytes or size_kb")


def _parse_pattern_kb(spec: str) -> tuple[int, ...]:
    """解析 sizes_kb 序列，如 "16x4,64,128x7"（大小x个数），返回展开后的各扇区 KB 数。
    Parse a sizes_kb sequence such as "16x4,64,128x7" (size x count) into the expanded sector
    sizes in KB.

    Raises:
        ValueError: 序列为空。
            The sequence is empty.
    """
    pattern = []
    for token in spec.replace(" ", "").split(","):
        if not token:
            continue

        if "x" in token.lower():
            value_text, count_text = re.split(r"[xX]", token, maxsplit=1)
            value_kb = int(value_text, 0)
            count = int(count_text, 0)
            pattern.extend([value_kb] * count)
        else:
            pattern.append(int(token, 0))

    if not pattern:
        raise ValueError(f"Invalid pattern specification: {spec}")
    return tuple(pattern)


def _normalize_optional(value: str | None) -> str | None:
    """非空字符串转为大写；空字符串或 None 为 None。
    A non-empty string in upper case; None for an empty string or None.
    """
    return value.upper() if value else None


def _parse_optional_int(value: str | None) -> int | None:
    """把属性值按 Python 整数字面量解析（可带 0x 前缀）；None 仍为 None。
    Parse an attribute value as a Python integer literal, a 0x prefix allowed; None stays None.
    """
    return int(value, 0) if value is not None else None


def _local_name(tag: str) -> str:
    """去掉 XML 标签的 {命名空间} 前缀。
    Strip the {namespace} prefix from an XML tag.
    """
    return tag.split("}", 1)[-1]


def main():
    """xr_stm32_flash 命令入口：检查型号格式，以 YAML 输出 Flash 布局。
    Entry point of xr_stm32_flash: check the model format and print the flash layout as YAML.

    参数个数不对时打印用法并以退出码 1 结束；处理失败时打印原因和调用栈并以退出码 2 结束。
    A wrong argument count prints the usage and exits with code 1; a failure prints the reason
    and the stack trace and exits with code 2.
    """
    from libxr.package_info import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    def validate_model(model: str) -> bool:
        """型号不含 '-'、以 STM32 开头且长度超过 8 个字符时为 True。
        True when the model has no '-', starts with STM32 and is longer than 8 characters.
        """
        return "-" not in model and model.upper().startswith("STM32") and len(model) > 8

    if len(sys.argv) != 2:
        print("STM32 Flash Information Tool")
        print("Usage:")
        print("  xr_stm32_flash <STM32_MODEL>")
        print("\nExamples:")
        print("  xr_stm32_flash STM32F103C8T6")
        print("  xr_stm32_flash STM32L476RG")
        sys.exit(1)

    model = sys.argv[1].strip().upper()

    try:
        if not validate_model(model):
            raise ValueError(f"Invalid STM32 model format: {model}")

        info = layout_flash(model)
        print(
            yaml.safe_dump(
                flash_info_to_dict(info),
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        )

    except Exception as error:
        print(f"\nERROR: Failed to process model {model}")
        print(f"Reason: {str(error)}")
        print("\nStack trace:")
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
