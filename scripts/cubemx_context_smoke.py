#!/usr/bin/env python3
"""Regression checks for CubeMX context-to-project directory discovery."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from libxr.ConfigCubemxProject import (  # noqa: E402
    SIMPLE_MULTICORE_LAYOUT_ERROR,
    detect_cube_contexts,
    select_cube_contexts,
)
from libxr.PeripheralAnalyzerSTM32 import filter_ioc_context  # noqa: E402


def _write_project(
    root: Path, ioc: str, mxproject: Optional[str], directories: Tuple[str, ...]
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "demo.ioc").write_text(ioc, encoding="utf-8")
    if mxproject is not None:
        (root / ".mxproject").write_text(mxproject, encoding="utf-8")
    for directory in directories:
        (root / directory / "Core" / "Inc").mkdir(parents=True, exist_ok=True)
        (root / directory / "Core" / "Src").mkdir(parents=True, exist_ok=True)
    return root / "demo.ioc"


def _assert_layout_error(ioc_file: Path) -> None:
    try:
        select_cube_contexts(str(ioc_file))
    except ValueError as error:
        if str(error) != SIMPLE_MULTICORE_LAYOUT_ERROR:
            raise AssertionError(f"unexpected layout error: {error}")
    else:
        raise AssertionError("unsupported CubeMX layout was accepted")


def _assert_context_filtering() -> None:
    raw_map = {
        "Mcu.Context0": "CortexM7",
        "Mcu.Context1": "CortexM4",
        "Mcu.ContextNb": "2",
        "CortexM7.IPs": "RCC,NVIC1\\:I,SYS\\:I,USART3\\:I",
        "CortexM4.IPs": "RCC,NVIC2\\:I,SYS_M4\\:I,USART1\\:I",
        "VP_SYS_VS_Systick.Mode": "SysTick",
        "VP_SYS_M4_VS_Systick.Mode": "SysTick",
        "NVIC1.SysTick_IRQn": "true",
        "NVIC2.SysTick_IRQn": "true",
        "USART3.BaudRate": "115200",
        "USART1.BaudRate": "115200",
    }
    cm7 = filter_ioc_context(raw_map, "CM7")
    cm4 = filter_ioc_context(raw_map, "Cortex_M4")
    if "VP_SYS_VS_Systick.Mode" not in cm7 or "VP_SYS_M4_VS_Systick.Mode" in cm7:
        raise AssertionError(f"CM7 virtual-pin filtering failed: {cm7!r}")
    if "VP_SYS_M4_VS_Systick.Mode" not in cm4 or "VP_SYS_VS_Systick.Mode" in cm4:
        raise AssertionError(f"CM4 virtual-pin filtering failed: {cm4!r}")
    if "USART3.BaudRate" not in cm7 or "USART1.BaudRate" in cm7:
        raise AssertionError(f"CM7 IP filtering failed: {cm7!r}")
    if "USART1.BaudRate" not in cm4 or "USART3.BaudRate" in cm4:
        raise AssertionError(f"CM4 IP filtering failed: {cm4!r}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cubemx_context_smoke_") as temporary:
        root = Path(temporary)
        ioc_file = _write_project(
            root / "h7",
            """Mcu.Context0=CortexM7
Mcu.Context1=CortexM4
Mcu.ContextNb=2
CortexM7.IPs=RCC,GPIO
CortexM4.IPs=RCC,GPIO
""",
            """[CortexM7:PreviousGenFiles]
SourcePath#0=..\\CM7\\Core\\Src
HeaderPath#0=..\\CM7\\Core\\Inc

[CortexM4:PreviousGenFiles]
SourcePath#0=..\\CM4\\Core\\Src
HeaderPath#0=..\\CM4\\Core\\Inc
""",
            ("CM7", "CM4"),
        )
        contexts = select_cube_contexts(str(ioc_file))
        expected = [root / "h7" / "CM7", root / "h7" / "CM4"]
        if [Path(context["project_dir"]) for context in contexts] != expected:
            raise AssertionError(f"unexpected context mapping: {contexts!r}")

        stale_path_ioc = _write_project(
            root / "stale-paths",
            """Mcu.Context0=CortexM7
Mcu.Context1=CortexM4
Mcu.ContextNb=2
CortexM7.IPs=RCC
CortexM4.IPs=RCC
""",
            r"""[CortexM7:PreviousGenFiles]
SourcePath#0=C:\old_workspace\demo\CM7\Core\Src

[CortexM4:PreviousGenFiles]
SourcePath#0=C:\old_workspace\demo\CM4\Core\Src
""",
            ("CM7", "CM4"),
        )
        stale_contexts = select_cube_contexts(str(stale_path_ioc))
        if [Path(context["project_dir"]).name for context in stale_contexts] != ["CM7", "CM4"]:
            raise AssertionError(f"stale .mxproject paths were not recovered: {stale_contexts!r}")

        unsupported_ioc = _write_project(
            root / "unsupported",
            """Mcu.Context0=CortexM33S
Mcu.Context1=CortexM33NS
Mcu.ContextNb=2
Mcu.ContextProject=TrustZoneEnabled
CortexM33S.IPs=RCC
CortexM33NS.IPs=RCC
""",
            """[CortexM33S:PreviousGenFiles]
SourcePath#0=..\\Secure\\Core\\Src

[CortexM33NS:PreviousGenFiles]
SourcePath#0=..\\NonSecure\\Core\\Src
""",
            ("Secure", "NonSecure"),
        )
        _assert_layout_error(unsupported_ioc)

        missing_metadata_ioc = _write_project(
            root / "missing-metadata",
            """Mcu.Context0=CortexM7
Mcu.Context1=CortexM4
Mcu.ContextNb=2
""",
            None,
            ("CM7", "CM4"),
        )
        _assert_layout_error(missing_metadata_ioc)

        single_context_ioc = _write_project(
            root / "single-context",
            """Mcu.Context0=CortexM33
Mcu.ContextNb=1
CortexM33.IPs=RCC
""",
            None,
            ("",),
        )
        if select_cube_contexts(str(single_context_ioc)) != []:
            raise AssertionError("single-core context should use the project root")

        if detect_cube_contexts(str(unsupported_ioc)) != [
            {
                "name": "CortexM33S",
                "normalized": "CORTEXM33S",
                "ips": ["RCC"],
            },
            {
                "name": "CortexM33NS",
                "normalized": "CORTEXM33NS",
                "ips": ["RCC"],
            },
        ]:
            raise AssertionError("non-context Mcu.ContextProject metadata leaked into detection")

    _assert_context_filtering()
    print("CubeMX context smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
