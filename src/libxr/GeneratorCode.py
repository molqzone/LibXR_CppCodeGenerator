#!/usr/bin/env python3

import logging
import os
import sys
import subprocess
import argparse
import importlib.util
import yaml
from typing import List

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

def is_stm32_project(path: str) -> bool:
    """Check if the given path contains any .ioc file."""
    try:
        return any(f.endswith(".ioc") for f in os.listdir(path))
    except Exception as e:
        logging.error(f"Cannot check directory '{path}': {e}")
        return False


def detect_platform(input_path: str) -> str:
    """Detect the generator backend from normalized MCU metadata."""
    try:
        with open(input_path, "r", encoding="utf-8") as source:
            config = yaml.safe_load(source) or {}
        mcu = config.get("Mcu", {}) if isinstance(config, dict) else {}
        platform = str(mcu.get("Platform", mcu.get("Family", ""))).upper()
        mcu_type = str(mcu.get("Type", "")).upper()
        if platform == "HPM" and importlib.util.find_spec("libxr.GeneratorCodeHPM"):
            return "HPM"
        if platform in {"TI", "MSPM0"} and mcu_type.startswith("MSPM0"):
            return "MSPM0"
        if platform.startswith("STM32"):
            return "STM32"
    except (OSError, yaml.YAMLError):
        pass
    return "STM32" if is_stm32_project(os.path.dirname(input_path)) else ""


def main():
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()
    
    parser = argparse.ArgumentParser(description="Wrapper for supported LibXR code generators.")
    parser.add_argument("-i", "--input", required=True,
                        help="Input YAML configuration file path")

    # We don't parse all args because we want to forward unknown ones later
    known_args, unknown_args = parser.parse_known_args()

    input_path = os.path.abspath(known_args.input)
    if not os.path.isfile(input_path):
        logging.error(f"YAML configuration file not found: {input_path}")
        sys.exit(1)

    platform = detect_platform(input_path)
    if not platform:
        logging.info("Skipped: Unsupported or unidentified project platform.")
        sys.exit(0)

    module_map = {
        "STM32": "libxr.GeneratorCodeSTM32",
        "HPM": "libxr.GeneratorCodeHPM",
        "MSPM0": "libxr.GeneratorCodeMSPM0",
    }
    module = module_map.get(platform)
    if not module:
        logging.error("No code generator is installed for platform: %s", platform)
        sys.exit(1)
    cmd: List[str] = [sys.executable, "-m", module, *sys.argv[1:]]

    logging.info("%s project detected.", platform)
    logging.debug(f"CMD: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Code generation failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
