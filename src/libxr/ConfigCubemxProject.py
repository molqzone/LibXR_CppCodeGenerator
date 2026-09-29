#!/usr/bin/env python

import logging
import os
import subprocess
import shlex
import sys

import argparse

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

DEFAULT_MIRRORS = [
    "https://gitee.com/jiu-xiao/libxr",
]


def is_git_repo(path):
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


def is_git_worktree_root(path):
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True
        )
        return os.path.realpath(result.stdout.strip()) == os.path.realpath(path)
    except subprocess.CalledProcessError:
        return False


def _fmt_cmd(cmd):
    if isinstance(cmd, (list, tuple)):
        return " ".join(shlex.quote(str(x)) for x in cmd)
    return str(cmd)


def run_command(cmd, ignore_error=False):
    """Run a command. Accepts either a list/tuple (preferred, shell=False) or a string (shell=True)."""
    if isinstance(cmd, (list, tuple)):
        result = subprocess.run(cmd, capture_output=True, text=True)
    else:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        logging.info(f"[OK] {_fmt_cmd(cmd)}")
        return result.stdout
    if ignore_error:
        logging.warning(f"[IGNORED FAILURE] {_fmt_cmd(cmd)}\n{result.stderr}")
        return result.stdout
    logging.error(f"[FAILED] {_fmt_cmd(cmd)}\n{result.stderr}")
    sys.exit(1)


def find_ioc_file(directory):
    """Search for a .ioc file in the specified directory."""
    for file in sorted(os.listdir(directory)):
        if file.endswith(".ioc"):
            return os.path.join(directory, file)
    return None


def pick_git_base(default_base="https://github.com", mirrors=None, timeout=5.0):
    """
    Select the fastest accessible Git source among the default and mirrors.
    Returns either a base URL or a full repository URL.
    - default_base: e.g. https://github.com
    - mirrors: a list of base URLs or full repo URLs
    """
    import time

    def is_repo_url(s: str) -> bool:
        return s.endswith(".git") or s.rstrip("/").split("/")[-1].lower() == "libxr"

    def to_probe_url(base_or_repo: str) -> str:
        if is_repo_url(base_or_repo):
            return base_or_repo
        return f"{base_or_repo.rstrip('/')}/Jiu-Xiao/libxr.git"

    candidates = [default_base] + [m.strip() for m in (mirrors or []) if m.strip()]
    scores = []
    for item in candidates:
        url = to_probe_url(item)
        start = time.time()
        try:
            r = subprocess.run(
                ["git", "ls-remote", "-h", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout
            )
            if r.returncode == 0:
                scores.append((time.time() - start, item))
        except subprocess.TimeoutExpired:
            pass
    return min(scores)[1] if scores else default_base


def make_repo_url(base_or_repo: str, owner="Jiu-Xiao", repo="libxr"):
    # If a full repository URL is provided (.git or ends with repo name), return it as-is
    if base_or_repo.endswith(".git") or base_or_repo.rstrip("/").split("/")[-1].lower() == repo.lower():
        return base_or_repo
    return f"{base_or_repo.rstrip('/')}/{owner}/{repo}.git"


def create_gitignore_file(project_dir):
    gitignore_path = os.path.join(project_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        logging.info("Creating .gitignore file...")
        with open(gitignore_path, "w", encoding="utf-8", newline="\n") as gitignore_file:
            gitignore_file.write("""build/**
.history/**
.cache/**
.config.yaml
CMakeFiles/**
""")


def get_git_head(path):
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def is_commit_ancestor(repo_path, older_commit, newer_commit):
    if not older_commit or not newer_commit:
        return False
    result = subprocess.run(
        [
            "git", "-C", repo_path, "merge-base", "--is-ancestor",
            older_commit, newer_commit
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0


def is_empty_directory(path):
    return os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path)


def add_libxr(project_dir, libxr_commit=None, git_base="https://github.com",
              default_libxr_commit=None):
    sub_rel_path_posix = "Middlewares/Third_Party/LibXR"
    libxr_path = os.path.join(project_dir, "Middlewares", "Third_Party", "LibXR")

    midware_path = os.path.join(project_dir, "Middlewares")
    third_party_path = os.path.join(midware_path, "Third_Party")

    def has_registered_submodule(repo_root, rel_path):
        if os.path.exists(os.path.join(repo_root, ".gitmodules")):
            result = subprocess.run(
                [
                    "git", "-C", repo_root, "config", "-f", ".gitmodules",
                    "--get-regexp", r"^submodule\..*\.path$"
                ],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2 and parts[1].strip() == rel_path:
                        return True

        result = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "--stage", "--", rel_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "160000":
                return True
        return False

    if not os.path.exists(midware_path):
        logging.info("Creating Middleware folder...")
        os.makedirs(midware_path)
    if not os.path.exists(third_party_path):
        logging.info("Creating Third Party folder...")
        os.makedirs(third_party_path)

    if not is_git_repo(project_dir):
        logging.warning(f"{project_dir} is not a Git repository. Initializing...")
        run_command(["git", "init", project_dir])

    registered = has_registered_submodule(project_dir, sub_rel_path_posix)
    checkout_path_present = os.path.lexists(libxr_path)
    existing_checkout = checkout_path_present and is_git_worktree_root(libxr_path)
    added_submodule = False

    # An existing LibXR directory may hold the user's own commits or sources;
    # it is never deleted, moved or re-cloned.
    if checkout_path_present and not existing_checkout and not is_empty_directory(libxr_path):
        logging.error(
            f"{libxr_path} exists but is not a valid Git checkout; it was left untouched. "
            "Move it away or turn it into a LibXR checkout, then run again.")
        sys.exit(1)

    if registered:
        if existing_checkout:
            run_command(
                ["git", "-C", project_dir, "submodule", "sync", "--", sub_rel_path_posix],
                ignore_error=False
            )
            logging.info("LibXR submodule already exists; preserving current checkout.")
        else:
            run_command(
                ["git", "-C", project_dir, "submodule", "sync", "--", sub_rel_path_posix],
                ignore_error=False
            )
            run_command(
                [
                    "git", "-C", project_dir, "submodule", "update",
                    "--init", "--recursive", "--", sub_rel_path_posix
                ],
                ignore_error=False
            )
    else:
        logging.info("LibXR submodule not registered yet; skipping preemptive update.")

    repo_url = make_repo_url(git_base, "Jiu-Xiao", "libxr")
    if not registered:
        logging.info(f"Adding LibXR as submodule from {repo_url} ...")
        run_command(
            ["git", "-C", project_dir, "submodule", "add", repo_url, sub_rel_path_posix]
        )
        logging.info("LibXR submodule added and initialized.")
        added_submodule = True
    else:
        logging.info("LibXR submodule already registered.")

    if os.path.exists(libxr_path):
        logging.info("LibXR submodule path exists.")
        current_commit = get_git_head(libxr_path)
        target_commit = ""

        # The project's gitlink pins LibXR. Only an explicit --commit or a
        # submodule added by this run moves the checkout; otherwise the
        # checkout stays where it is and a different package default is only
        # reported.
        if libxr_commit:
            target_commit = libxr_commit
            logging.info(f"Checking out LibXR to requested commit {target_commit}")
        elif added_submodule and not existing_checkout and default_libxr_commit:
            target_commit = default_libxr_commit
            logging.info(f"Initializing new LibXR submodule to default commit {target_commit}")
        elif default_libxr_commit and current_commit != default_libxr_commit:
            if is_commit_ancestor(libxr_path, default_libxr_commit, current_commit):
                logging.info("LibXR checkout is newer than this generator's default; keeping it.")
            else:
                relation = ("older than" if is_commit_ancestor(
                    libxr_path, current_commit, default_libxr_commit) else "different from")
                logging.warning(
                    f"LibXR checkout {current_commit[:12]} is {relation} this generator's default "
                    f"{default_libxr_commit[:12]}; it was left unchanged. To switch, run "
                    f"xr_cubemx_cfg with --commit {default_libxr_commit} (or check out the commit "
                    "in Middlewares/Third_Party/LibXR) and commit the gitlink.")
        else:
            logging.info("Keeping the existing LibXR checkout.")

        if target_commit:
            run_command(["git", "-C", libxr_path, "fetch", "origin"], ignore_error=True)
            run_command(["git", "-C", libxr_path, "checkout", target_commit])


def create_user_directory(project_dir):
    """Ensure the User directory exists."""
    user_path = os.path.join(project_dir, "User")
    if not os.path.exists(user_path):
        os.makedirs(user_path)
    return user_path


def set_terminal_source(user_path, terminal_source):
    """Record the -t/--terminal device as terminal_source in User/libxr_config.yaml.

    xr_gen_code_stm32 reads the terminal device from this file, so the choice
    persists for later regenerations. Other keys and comments are kept.
    """
    from libxr import LibXRConfigFile as libxr_config_file

    config_path = os.path.join(user_path, "libxr_config.yaml")
    try:
        if os.path.exists(config_path):
            document, _ = libxr_config_file.read(config_path)
        else:
            document = libxr_config_file.new_document()
    except libxr_config_file.LibXRConfigError as error:
        logging.error(str(error))
        sys.exit(1)
    libxr_config_file.set_value(document, "terminal_source", terminal_source)
    libxr_config_file.write(config_path, document)
    logging.info(f"Set terminal_source to {terminal_source} in {config_path}")


def process_ioc_file(project_dir, yaml_output):
    """Parse the .ioc file and generate YAML configuration."""
    logging.info("Parsing .ioc file...")
    run_command(f"xr_parse_ioc -d {project_dir} -o {yaml_output}")


def generate_cpp_code(yaml_output, cpp_output, xrobot_enable=False):
    """Generate C++ code from YAML configuration, with optional XRobot support."""
    logging.info("Generating C++ code...")
    cmd = f"xr_gen_code_stm32 -i {yaml_output} -o {cpp_output}"
    if xrobot_enable:
        cmd += " --xrobot"
    run_command(cmd)


def generate_cmake_file(project_dir):
    """Generate CMakeLists.txt for STM32 project with selected compiler."""
    run_command(f"xr_stm32_cmake {project_dir}")


def _friendly_path_name(path: str) -> str:
    """
    Return a human-friendly name for a path.
    If path is '.', show the current folder name instead of '.'.
    Falls back to absolute path for root-like cases.
    """
    abs_path = os.path.abspath(path)
    base = os.path.basename(abs_path.rstrip(os.sep))
    return base or abs_path


def ensure_valid_cubemx_project(path: str):
    """
    Exit if `path` is not a typical STM32CubeMX project (must contain Core/).
    Display a friendly name instead of '.' when logging.
    """
    display_name = _friendly_path_name(path)
    core_dir = os.path.join(path, "Core")
    if not os.path.isdir(core_dir):
        logging.error(f"{display_name} is not a valid STM32CubeMX project: missing Core/ directory")
        sys.exit(1)


def main():
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(description="Automate STM32CubeMX project setup")
    parser.add_argument("-d", "--directory", required=True, help="STM32CubeMX project directory")
    parser.add_argument("-t", "--terminal", default="",
                        help="Terminal device (e.g. usart1, usb_fs_cdc); stored as "
                             "terminal_source in User/libxr_config.yaml")
    parser.add_argument("--xrobot", action="store_true", help="Support XRobot")
    parser.add_argument("--commit", default="", help="Specify locked LibXR commit hash")
    parser.add_argument("--git-source", default="auto",
                        help="Git source base URL or full repo URL, or 'auto'/'github' (default: auto)")
    parser.add_argument("--git-mirrors", default="",
                        help="Comma-separated mirror base/repo URLs (will be tried when --git-source=auto)")

    args = parser.parse_args()

    project_dir = args.directory.rstrip("/")
    terminal_source = args.terminal
    xrobot_enable = bool(args.xrobot)

    libxr_commit = args.commit.strip()
    default_libxr_commit = ""
    if not libxr_commit:
        try:
            sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))
            from libxr.libxr_version import LibXRInfo
            default_libxr_commit = LibXRInfo.COMMIT
        except Exception as e:
            logging.info(f"No lock commit found in src/libxr/libxr_version.py: {e}")
            default_libxr_commit = ""

    if libxr_commit:
        logging.info(f"Requested LibXR commit: {libxr_commit}")
    elif default_libxr_commit:
        logging.info(f"Default LibXR commit: {default_libxr_commit}")

    if not os.path.isdir(project_dir):
        logging.error(f"Directory {_friendly_path_name(project_dir)} does not exist")
        sys.exit(1)

    # Validate STM32CubeMX project structure (must have Core/ directory)
    ensure_valid_cubemx_project(project_dir)

    # Select Git source (auto benchmarks default and mirrors)
    env_mirrors = os.environ.get("XR_GIT_MIRRORS", "")
    cli_mirrors = [m for m in args.git_mirrors.split(",") if m.strip()]
    all_mirrors = DEFAULT_MIRRORS + \
                  [m.strip() for m in (env_mirrors.split(",") if env_mirrors else []) if m.strip()] + \
                  cli_mirrors

    if args.git_source == "auto":
        git_base = pick_git_base(default_base="https://github.com", mirrors=all_mirrors, timeout=5.0)
    elif args.git_source == "github":
        git_base = "https://github.com"
    else:
        git_base = args.git_source
    logging.info(f"Selected Git base/repo: {git_base}")

    # Add Git submodule if necessary
    add_libxr(
        project_dir,
        libxr_commit if libxr_commit else None,
        git_base=git_base,
        default_libxr_commit=default_libxr_commit if default_libxr_commit else None
    )

    # Find .ioc file
    ioc_file = find_ioc_file(project_dir)
    if not ioc_file:
        logging.error("No .ioc file found")
        sys.exit(1)

    logging.info(f"Found .ioc file: {ioc_file}")

    create_gitignore_file(project_dir)

    # Create user directory
    user_path = create_user_directory(project_dir)

    # Define paths
    yaml_output = os.path.join(project_dir, ".config.yaml")
    cpp_output = os.path.join(user_path, "app_main.cpp")

    # Record the terminal device for the code generator
    if terminal_source:
        set_terminal_source(user_path, terminal_source)

    # Process .ioc file
    process_ioc_file(project_dir, yaml_output)

    # Generate C++ code
    generate_cpp_code(yaml_output, cpp_output, xrobot_enable)

    # Generate CMakeLists.txt with selected compiler
    generate_cmake_file(project_dir)

    logging.info("[Pass] All tasks completed successfully!")


if __name__ == "__main__":
    main()
