#!/usr/bin/env python3

import logging
import os
import sys
import subprocess
import argparse

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

def main():
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(description="Parse a supported embedded project configuration.")
    parser.add_argument(
        "-d", "--directory",
        required=True,
        help="Input project directory"
    )
    args, extra_args = parser.parse_known_args()

    target_dir = os.path.abspath(args.directory)

    if not os.path.isdir(target_dir):
        logging.error(f"Specified directory does not exist: {target_dir}")
        sys.exit(1)

    ioc_files = [f for f in os.listdir(target_dir) if f.endswith(".ioc")]
    syscfg_files = [f for f in os.listdir(target_dir) if f.endswith(".syscfg")]
    if ioc_files and syscfg_files:
        logging.error("Both STM32 .ioc and TI .syscfg files were found; use a platform-specific parser")
        sys.exit(1)

    if ioc_files:
        module = "libxr.PeripheralAnalyzerSTM32"
        detected_files = ioc_files
    elif syscfg_files:
        module = "libxr.PeripheralAnalyzerMSPM0"
        detected_files = syscfg_files
    else:
        logging.error(f"No .ioc or .syscfg files found in directory: {target_dir}")
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m", module,
        "-d", target_dir,
        *extra_args
    ]

    logging.info(f"Detected {len(detected_files)} configuration file(s) in '{target_dir}':")
    for f in detected_files:
        logging.info(f"       - {f}")
    logging.debug(f"CMD: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Peripheral analyzer exited with code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
