#!/usr/bin/env python3

import argparse
import logging
import os
import subprocess
import sys
from typing import Dict

import yaml

from libxr.PeripheralAnalyzerMSPM0 import (
    extract_gpio_pins,
    extract_modules,
    extract_peripherals,
    extract_rtos,
    extract_ti_device,
    parse_syscfg_text,
)

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


def build_yaml_config(syscfg_file: str, terminal_source: str, syscfg_text: str) -> Dict:
    """Build a baseline .config.yaml structure from SysConfig metadata."""
    config = parse_syscfg_text(syscfg_text, syscfg_file)
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
    logging.info(f"Detected RTOS: {extract_rtos(syscfg_text)}")
    logging.info(f"Detected modules: {len(extract_modules(syscfg_text))}")
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
