#!/usr/bin/env python

import logging
import os
import re
import subprocess
import shlex
import shutil
import sys

import argparse

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

DEFAULT_MIRRORS = [
    "https://gitee.com/jiu-xiao/libxr",
]

SIMPLE_MULTICORE_LAYOUT_ERROR = (
    "Cannot identify an unambiguous simple multi-core CubeMX project layout"
)
_IOC_CONTEXT_KEY_RE = re.compile(r"^Mcu\.Context(\d+)$", re.IGNORECASE)
_SIMPLE_CORTEX_M_CONTEXT_RE = re.compile(r"^CORTEXM\d+(?:PLUS)?$")
_MXPROJECT_CONTEXT_SECTION_RE = re.compile(
    r"^(?P<context>.+):PreviousGenFiles$", re.IGNORECASE
)


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


def get_git_dir(path):
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "--git-dir"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        logging.warning(f"Failed to resolve git dir for {path}: {result.stderr.strip()}")
        return ""
    git_dir = result.stdout.strip()
    if not os.path.isabs(git_dir):
        git_dir = os.path.normpath(os.path.join(path, git_dir))
    return git_dir


def is_git_clean(path):
    """Check if the Git repo at `path` has no uncommitted changes."""
    result = subprocess.run(
        ["git", "-C", path, "status", "--porcelain"],
        capture_output=True,
        text=True
    )
    return result.returncode == 0 and result.stdout.strip() == ""


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
    for file in os.listdir(directory):
        if file.endswith(".ioc"):
            return os.path.join(directory, file)
    return None


def _read_ioc_map(ioc_file):
    from libxr.PeripheralAnalyzerSTM32 import _extract_key_value_pairs

    with open(ioc_file, "r", encoding="utf-8") as file:
        return _extract_key_value_pairs(file)


def _normalize_context(value):
    value = (
        str(value)
        .strip()
        .replace("_", "")
        .replace("-", "")
        .replace("+", "PLUS")
        .upper()
    )
    if re.fullmatch(r"CM\d+(?:PLUS)?", value):
        return f"CORTEXM{value[2:]}"
    return value


def detect_cube_contexts(ioc_file):
    """Return CubeMX context metadata from an IOC file."""
    raw_map = _read_ioc_map(ioc_file)
    indexed_contexts = []
    for key, value in raw_map.items():
        match = _IOC_CONTEXT_KEY_RE.fullmatch(key)
        if match is None:
            continue
        name = value.strip()
        if not name:
            continue
        ip_key = f"{name}.IPs"
        ips = []
        for item in raw_map.get(ip_key, "").split(","):
            item = item.strip().replace("\\:", ":")
            if item:
                ips.append(item.split(":", 1)[0])
        indexed_contexts.append(
            (
                int(match.group(1)),
                {"name": name, "normalized": _normalize_context(name), "ips": ips},
            )
        )
    return [context for _, context in sorted(indexed_contexts, key=lambda item: item[0])]


def _read_mxproject_sections(mxproject_file):
    """Read the small INI-like section format emitted by CubeMX."""
    sections = {}
    current_section = None

    with open(mxproject_file, "rb") as file:
        raw_content = file.read()
    content = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            content = raw_content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise UnicodeDecodeError(".mxproject", raw_content, 0, len(raw_content), "unsupported encoding")

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            sections.setdefault(current_section, {})
            continue
        if current_section is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        sections[current_section][key.strip()] = value.strip()

    return sections


def _read_mxproject_context_paths(project_dir):
    """Return generated source/header paths grouped by CubeMX context."""
    mxproject_file = os.path.join(project_dir, ".mxproject")
    try:
        sections = _read_mxproject_sections(mxproject_file)
    except (OSError, UnicodeError) as error:
        raise ValueError(SIMPLE_MULTICORE_LAYOUT_ERROR) from error

    context_paths = {}
    for section_name, values in sections.items():
        match = _MXPROJECT_CONTEXT_SECTION_RE.fullmatch(section_name)
        if match is None:
            continue

        normalized = _normalize_context(match.group("context"))
        paths = context_paths.setdefault(normalized, [])
        for key, value in values.items():
            if key.lower().startswith(("sourcepath", "headerpath")):
                paths.extend(item.strip() for item in value.split(";") if item.strip())

    return context_paths


def _is_within_directory(parent, child):
    try:
        return (
            os.path.commonpath([os.path.realpath(parent), os.path.realpath(child)])
            == os.path.realpath(parent)
        )
    except ValueError:
        # Different Windows drives cannot share a project root.
        return False


def _find_local_project_dirs(project_dir, directory_name):
    """Find local generated projects matching a directory name from .mxproject."""
    root = os.path.realpath(project_dir)
    matches = set()
    for current, directories, _ in os.walk(root):
        directories[:] = [
            directory
            for directory in directories
            if directory not in {
                ".git",
                ".history",
                "build",
                "cmake-build-debug",
                "cmake-build-release",
            }
        ]
        if os.path.basename(current).casefold() != directory_name.casefold():
            continue
        if os.path.isdir(os.path.join(current, "Core")):
            matches.add(os.path.realpath(current))
    return matches


def _project_dirs_from_mxproject_path(project_dir, generated_path):
    """Resolve one .mxproject source/header path to local project directories."""
    path_value = generated_path.strip().strip('"').strip("'")
    if not path_value:
        return set()

    local_path = path_value.replace("\\", os.sep).replace("/", os.sep)
    if os.path.isabs(local_path):
        resolved_path = os.path.realpath(local_path)
    else:
        resolved_path = os.path.realpath(os.path.join(project_dir, local_path))
    if (
        os.path.basename(os.path.dirname(resolved_path)).casefold() == "core"
        and os.path.basename(resolved_path).casefold() in {"src", "inc"}
    ):
        candidate = os.path.realpath(os.path.join(resolved_path, os.pardir, os.pardir))
        if (
            _is_within_directory(project_dir, candidate)
            and os.path.isdir(os.path.join(candidate, "Core"))
        ):
            return {candidate}

    # Older .mxproject files often contain absolute paths from the machine on
    # which CubeMX generated the project. Use the directory immediately before
    # Core as a stable hint, then resolve it within the current project root.
    path_parts = [
        part
        for part in path_value.replace("\\", "/").split("/")
        if part not in {"", ".", ".."}
    ]
    matches = set()
    for index, part in enumerate(path_parts[:-1]):
        if (
            part.casefold() != "core"
            or path_parts[index + 1].casefold() not in {"src", "inc"}
        ):
            continue
        if index == 0:
            continue
        matches.update(_find_local_project_dirs(project_dir, path_parts[index - 1]))
    return matches


def _project_dirs_for_context(project_dir, context_info, context_paths):
    paths = context_paths.get(context_info["normalized"], [])
    project_dirs = set()
    for generated_path in paths:
        project_dirs.update(_project_dirs_from_mxproject_path(project_dir, generated_path))
    return project_dirs


def _is_simple_cortex_m_context(context_info):
    return bool(_SIMPLE_CORTEX_M_CONTEXT_RE.fullmatch(context_info["normalized"]))


def select_cube_contexts(ioc_file):
    """Return all CubeMX contexts and their generated subproject directories."""
    contexts = detect_cube_contexts(ioc_file)
    if len(contexts) < 2:
        return []

    # Context entries describe generated targets, so accepting one or a
    # non-standard target here would silently treat a different CubeMX layout
    # as a normal multi-core project.
    try:
        raw_map = _read_ioc_map(ioc_file)
        declared_count = raw_map.get("Mcu.ContextNb", "").strip()
        if declared_count and (
            not declared_count.isdigit() or int(declared_count) != len(contexts)
        ):
            raise ValueError
        normalized_names = [context["normalized"] for context in contexts]
        if (
            len(set(normalized_names)) != len(normalized_names)
            or not all(_is_simple_cortex_m_context(context) for context in contexts)
        ):
            raise ValueError

        project_dir = os.path.dirname(os.path.abspath(ioc_file))
        context_paths = _read_mxproject_context_paths(project_dir)
        resolved_dirs = []
        for context in contexts:
            candidates = _project_dirs_for_context(project_dir, context, context_paths)
            if len(candidates) != 1:
                raise ValueError
            context["project_dir"] = next(iter(candidates))
            resolved_dirs.append(os.path.realpath(context["project_dir"]))
        if len(set(resolved_dirs)) != len(resolved_dirs):
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        raise ValueError(SIMPLE_MULTICORE_LAYOUT_ERROR) from error

    return contexts


def context_project_dir(project_dir, context_info):
    """Map a CubeMX context to its generated subproject directory."""
    if context_info is None:
        return project_dir

    try:
        normalized = context_info.get("normalized") or _normalize_context(context_info["name"])
        context = {"normalized": normalized}
        if not _is_simple_cortex_m_context(context):
            raise ValueError
        context_paths = _read_mxproject_context_paths(os.path.abspath(project_dir))
        candidates = _project_dirs_for_context(
            os.path.abspath(project_dir), context, context_paths
        )
        if len(candidates) != 1:
            raise ValueError
        return next(iter(candidates))
    except (KeyError, TypeError, ValueError, OSError, UnicodeError) as error:
        raise ValueError(SIMPLE_MULTICORE_LAYOUT_ERROR) from error


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


def remove_path(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def repair_submodule_checkout(project_dir, rel_path, checkout_path):
    logging.warning("LibXR submodule checkout is invalid; recreating it from registered metadata.")
    run_command(
        ["git", "-C", project_dir, "submodule", "deinit", "-f", "--", rel_path],
        ignore_error=True
    )

    git_dir = get_git_dir(project_dir)
    if git_dir:
        module_git_dir = os.path.join(git_dir, "modules", *rel_path.split("/"))
        if os.path.exists(module_git_dir):
            logging.info(f"Removing stale LibXR submodule gitdir: {module_git_dir}")
            remove_path(module_git_dir)

    if os.path.exists(checkout_path) or os.path.islink(checkout_path):
        logging.info(f"Removing stale LibXR submodule worktree: {checkout_path}")
        remove_path(checkout_path)


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

    if registered:
        if existing_checkout:
            run_command(
                ["git", "-C", project_dir, "submodule", "sync", "--", sub_rel_path_posix],
                ignore_error=False
            )
            logging.info("LibXR submodule already exists; preserving current checkout.")
        else:
            if checkout_path_present:
                repair_submodule_checkout(project_dir, sub_rel_path_posix, libxr_path)
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
        dirty = not is_git_clean(libxr_path)
        fetched = False
        target_commit = ""

        if libxr_commit:
            target_commit = libxr_commit
            logging.info(f"Checking out LibXR to requested commit {target_commit}")
        elif added_submodule and default_libxr_commit:
            target_commit = default_libxr_commit
            logging.info(f"Initializing new LibXR submodule to default commit {target_commit}")
        elif dirty:
            logging.warning("LibXR submodule has local changes; keeping current checkout.")
        elif default_libxr_commit and current_commit != default_libxr_commit:
            run_command(["git", "-C", libxr_path, "fetch", "origin"], ignore_error=True)
            fetched = True

            if is_commit_ancestor(libxr_path, current_commit, default_libxr_commit):
                target_commit = default_libxr_commit
                logging.info(f"Updating clean LibXR checkout to package default {target_commit}")
            elif is_commit_ancestor(libxr_path, default_libxr_commit, current_commit):
                logging.info("LibXR checkout is newer than the package default; keeping it.")
            else:
                logging.info("LibXR checkout has diverged from the package default; keeping it.")

        if target_commit:
            if not fetched:
                run_command(["git", "-C", libxr_path, "fetch", "origin"], ignore_error=True)
            run_command(["git", "-C", libxr_path, "checkout", target_commit])
        elif not dirty:
            logging.info("No LibXR commit requested; keeping existing submodule checkout.")


def create_user_directory(project_dir):
    """Ensure the User directory exists."""
    user_path = os.path.join(project_dir, "User")
    if not os.path.exists(user_path):
        os.makedirs(user_path)
    return user_path


def process_ioc_file(project_dir, yaml_output, context=""):
    """Parse the .ioc file and generate YAML configuration."""
    logging.info("Parsing .ioc file...")
    cmd = ["xr_parse_ioc", "-d", project_dir, "-o", yaml_output]
    if context:
        cmd.extend(["--context", context])
    run_command(cmd)


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
    parser.add_argument("-t", "--terminal", default="", help="Optional terminal device source")
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

    ioc_file = find_ioc_file(project_dir)
    if not ioc_file:
        logging.error("No .ioc file found")
        sys.exit(1)

    try:
        contexts = select_cube_contexts(ioc_file)
    except ValueError as error:
        logging.error(str(error))
        sys.exit(1)

    if contexts:
        for context in contexts:
            ensure_valid_cubemx_project(context["project_dir"])
        logging.info(
            "Detected CubeMX contexts: "
            + ", ".join(context["name"] for context in contexts)
        )
    else:
        ensure_valid_cubemx_project(project_dir)
        contexts = [{"name": "", "project_dir": project_dir}]

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

    logging.info(f"Found .ioc file: {ioc_file}")

    create_gitignore_file(project_dir)

    for context in contexts:
        context_name = context["name"]
        target_dir = context["project_dir"]
        if context_name:
            logging.info(f"Configuring CubeMX context: {context_name} ({target_dir})")

        user_path = create_user_directory(target_dir)
        yaml_output = os.path.join(target_dir, ".config.yaml")
        cpp_output = os.path.join(user_path, "app_main.cpp")

        process_ioc_file(project_dir, yaml_output, context_name)
        generate_cpp_code(yaml_output, cpp_output, xrobot_enable)
        generate_cmake_file(target_dir)

    # Handle optional terminal source
    if terminal_source:
        logging.info("Modifying terminal device source...")

    logging.info("[Pass] All tasks completed successfully!")


if __name__ == "__main__":
    main()
