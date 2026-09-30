#!/usr/bin/env python

"""xr_cubemx_cfg：把 STM32CubeMX 工程配置为使用 LibXR 的工程。
xr_cubemx_cfg: set up an STM32CubeMX project to use LibXR.

依次加入 LibXR 子模块（Middlewares/Third_Party/LibXR），创建 .gitignore 和 User 目录，记录终端设备，
再调用 xr_parse_ioc、xr_gen_code_stm32 和 xr_stm32_cmake 生成配置、C++ 代码和 CMakeLists.txt。
In order it adds the LibXR submodule (Middlewares/Third_Party/LibXR), creates .gitignore and the
User directory, records the terminal device, then runs xr_parse_ioc, xr_gen_code_stm32 and
xr_stm32_cmake to produce the configuration, the C++ code and CMakeLists.txt.
"""

import argparse
import logging
import os
import shlex
import subprocess
import sys

from xr_syntax.i18n import localize_argparse, tr

DEFAULT_MIRRORS = [
    "https://gitee.com/jiu-xiao/libxr",
]


def is_git_repo(path):
    """path 位于 Git 工作树中时为 True。
    True when path is inside a Git work tree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


def is_git_worktree_root(path):
    """path 本身是 Git 工作树的顶层目录时为 True（按真实路径比较），位于上层仓库之中时为 False。
    True when path itself is the top level of a Git work tree, compared by real path; False
    when it lies inside an enclosing repository.
    """
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return os.path.realpath(result.stdout.strip()) == os.path.realpath(path)
    except subprocess.CalledProcessError:
        return False


def _fmt_cmd(cmd):
    """把命令写成日志中的一行：列表或元组的各项按 shell 规则引用后以空格连接，字符串原样返回。
    Format a command as one line for logs: list or tuple items shell-quoted and space-joined, a
    string as is.
    """
    if isinstance(cmd, (list, tuple)):
        return " ".join(shlex.quote(str(x)) for x in cmd)
    return str(cmd)


def run_command(cmd, ignore_error=False):
    """运行命令并返回标准输出；列表或元组不经 shell 运行（推荐），字符串经 shell 运行。
    Run a command and return its stdout; a list or tuple runs without a shell (preferred), a
    string through the shell.

    命令失败时，ignore_error 为 True 则记录警告并仍返回标准输出，否则记录错误并以退出码 1 结束进程。
    On failure, ignore_error logs a warning and still returns stdout; otherwise the error is
    logged and the process exits with code 1.
    """
    if isinstance(cmd, (list, tuple)):
        result = subprocess.run(cmd, capture_output=True, text=True)
    else:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    command_line = _fmt_cmd(cmd)
    if result.returncode == 0:
        logging.info(tr(f"[OK] {command_line}", f"[完成] {command_line}"))
        return result.stdout
    if ignore_error:
        logging.warning(
            tr(
                f"[IGNORED FAILURE] {command_line}\n{result.stderr}",
                f"[已忽略的失败] {command_line}\n{result.stderr}",
            )
        )
        return result.stdout
    logging.error(
        tr(f"[FAILED] {command_line}\n{result.stderr}", f"[失败] {command_line}\n{result.stderr}")
    )
    sys.exit(1)


def find_ioc_file(directory):
    """目录中按文件名排序的第一个 .ioc 文件的路径；没有时为 None。
    The path of the first .ioc file in the directory by file name order; None when there is none.
    """
    for file in sorted(os.listdir(directory)):
        if file.endswith(".ioc"):
            return os.path.join(directory, file)
    return None


def pick_git_base(default_base="https://github.com", mirrors=None, timeout=5.0):
    """在默认源和镜像中选出 git ls-remote 响应最快的 LibXR Git 源。
    Pick the LibXR Git source whose git ls-remote answers fastest, among the default and the
    mirrors.

    default_base 和 mirrors 中的每一项可以是基础地址（如 https://github.com，探测
    <基础地址>/Jiu-Xiao/libxr.git），也可以是以 .git 或 libxr 结尾的完整仓库地址。
    default_base and each mirror are either a base URL such as https://github.com, probed as
    <base>/Jiu-Xiao/libxr.git, or a full repository URL ending in .git or libxr.

    Returns:
        最快的候选项，保持传入时的形式；全部失败或超过 timeout 秒时为 default_base。
        The fastest candidate as it was given; default_base when every probe fails or takes
        longer than timeout seconds.
    """
    import time

    def is_repo_url(s: str) -> bool:
        """s 以 .git 结尾，或最后一段为 libxr（不区分大小写）时视为完整仓库地址。
        Treat s as a full repository URL when it ends in .git or its last path segment is
        libxr, ignoring case.
        """
        return s.endswith(".git") or s.rstrip("/").split("/")[-1].lower() == "libxr"

    def to_probe_url(base_or_repo: str) -> str:
        """探测用的仓库地址：完整仓库地址原样使用，基础地址后加 /Jiu-Xiao/libxr.git。
        The repository URL to probe: a full repository URL as is, a base URL with
        /Jiu-Xiao/libxr.git appended.
        """
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
                timeout=timeout,
            )
            if r.returncode == 0:
                scores.append((time.time() - start, item))
        except subprocess.TimeoutExpired:
            pass
    return min(scores)[1] if scores else default_base


def make_repo_url(base_or_repo: str, owner="Jiu-Xiao", repo="libxr"):
    """完整的仓库地址：base_or_repo 以 .git 或仓库名结尾时原样返回，否则拼成
    <base_or_repo>/<owner>/<repo>.git。
    The full repository URL: base_or_repo as is when it ends in .git or the repository name,
    else <base_or_repo>/<owner>/<repo>.git.
    """
    # If a full repository URL is provided (.git or ends with repo name), return it as-is
    if (
        base_or_repo.endswith(".git")
        or base_or_repo.rstrip("/").split("/")[-1].lower() == repo.lower()
    ):
        return base_or_repo
    return f"{base_or_repo.rstrip('/')}/{owner}/{repo}.git"


def create_gitignore_file(project_dir):
    """工程目录没有 .gitignore 时创建一个，忽略 build、.history、.cache、CMakeFiles 和
    .config.yaml；已有的 .gitignore 不改动。
    Create a .gitignore in the project directory that ignores build, .history, .cache,
    CMakeFiles and .config.yaml; an existing .gitignore is left unchanged.
    """
    gitignore_path = os.path.join(project_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        logging.info(tr("Creating .gitignore file...", "正在创建 .gitignore 文件……"))
        with open(gitignore_path, "w", encoding="utf-8", newline="\n") as gitignore_file:
            gitignore_file.write("""build/**
.history/**
.cache/**
.config.yaml
CMakeFiles/**
""")


def get_git_head(path):
    """path 处仓库的 HEAD commit；git 执行失败时为空字符串。
    The HEAD commit of the repository at path; an empty string when git fails.
    """
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def is_commit_ancestor(repo_path, older_commit, newer_commit):
    """older_commit 是 newer_commit 的祖先或与其相同时为 True；任一为空或 git 执行失败时为 False。
    True when older_commit is an ancestor of, or the same as, newer_commit; False when either is
    empty or git fails.
    """
    if not older_commit or not newer_commit:
        return False
    result = subprocess.run(
        ["git", "-C", repo_path, "merge-base", "--is-ancestor", older_commit, newer_commit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def is_empty_directory(path):
    """path 是空目录且不是符号链接时为 True。
    True when path is an empty directory and not a symbolic link.
    """
    return os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path)


def add_libxr(
    project_dir, libxr_commit=None, git_base="https://github.com", default_libxr_commit=None
):
    """把 LibXR 作为 Git 子模块放到 Middlewares/Third_Party/LibXR，并决定检出哪个 commit。
    Put LibXR at Middlewares/Third_Party/LibXR as a Git submodule and decide which commit is
    checked out.

    工程还不是 Git 仓库时先执行 git init。已登记的子模块会同步地址，没有检出时按 gitlink 初始化；
    未登记时从 git_base 加入子模块。已有的 LibXR 目录不会被删除、移动或重新克隆。
    A project that is not yet a Git repository gets git init. A registered submodule has its URL
    synced and, without a checkout, is initialized to its gitlink; an unregistered one is added
    from git_base. An existing LibXR directory is never deleted, moved or re-cloned.

    只有给出 libxr_commit，或本次新加入且原来没有检出的子模块（此时用 default_libxr_commit）才会
    切换检出；其他情况保持现有检出，与 default_libxr_commit 不同且不比它新时记录警告。
    Only libxr_commit, or default_libxr_commit for a submodule added by this run without an earlier
    checkout, moves the checkout; otherwise the existing checkout is kept, with a warning when it
    differs from default_libxr_commit and is not newer than it.

    Raises:
        SystemExit: LibXR 目录既不是 Git 检出也不是空目录，或必需的 git 命令失败。
            The LibXR directory is neither a Git checkout nor empty, or a required git command
            failed.
    """
    sub_rel_path_posix = "Middlewares/Third_Party/LibXR"
    libxr_path = os.path.join(project_dir, "Middlewares", "Third_Party", "LibXR")

    midware_path = os.path.join(project_dir, "Middlewares")
    third_party_path = os.path.join(midware_path, "Third_Party")

    def has_registered_submodule(repo_root, rel_path):
        """rel_path 已登记为子模块时为 True：.gitmodules 中有该路径，或索引中该路径是 gitlink
        （模式 160000）。
        True when rel_path is registered as a submodule: .gitmodules names the path, or the index
        holds a gitlink (mode 160000) there.
        """
        if os.path.exists(os.path.join(repo_root, ".gitmodules")):
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_root,
                    "config",
                    "-f",
                    ".gitmodules",
                    "--get-regexp",
                    r"^submodule\..*\.path$",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2 and parts[1].strip() == rel_path:
                        return True

        result = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "--stage", "--", rel_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "160000":
                return True
        return False

    if not os.path.exists(midware_path):
        logging.info(tr("Creating the Middlewares folder...", "正在创建 Middlewares 目录……"))
        os.makedirs(midware_path)
    if not os.path.exists(third_party_path):
        logging.info(tr("Creating the Third_Party folder...", "正在创建 Third_Party 目录……"))
        os.makedirs(third_party_path)

    if not is_git_repo(project_dir):
        logging.warning(
            tr(
                f"{project_dir} is not a Git repository. Initializing...",
                f"{project_dir}: 不是 Git 仓库，正在初始化……",
            )
        )
        run_command(["git", "init", project_dir])

    registered = has_registered_submodule(project_dir, sub_rel_path_posix)
    checkout_path_present = os.path.lexists(libxr_path)
    existing_checkout = checkout_path_present and is_git_worktree_root(libxr_path)
    added_submodule = False

    # An existing LibXR directory may hold the user's own commits or sources;
    # it is never deleted, moved or re-cloned.
    if checkout_path_present and not existing_checkout and not is_empty_directory(libxr_path):
        logging.error(
            tr(
                f"{libxr_path} exists but is not a valid Git checkout; it was left untouched. "
                "Move it away or turn it into a LibXR checkout, then run again.",
                f"{libxr_path}: 已存在，但不是有效的 Git 检出，未做改动。"
                "请把它移走或改成 LibXR 的检出，然后重新运行。",
            )
        )
        sys.exit(1)

    if registered:
        if existing_checkout:
            run_command(
                ["git", "-C", project_dir, "submodule", "sync", "--", sub_rel_path_posix],
                ignore_error=False,
            )
            logging.info(
                tr(
                    "LibXR submodule already exists; preserving current checkout.",
                    "LibXR 子模块已存在，保留当前检出。",
                )
            )
        else:
            run_command(
                ["git", "-C", project_dir, "submodule", "sync", "--", sub_rel_path_posix],
                ignore_error=False,
            )
            run_command(
                [
                    "git",
                    "-C",
                    project_dir,
                    "submodule",
                    "update",
                    "--init",
                    "--recursive",
                    "--",
                    sub_rel_path_posix,
                ],
                ignore_error=False,
            )
    else:
        logging.info(
            tr(
                "LibXR submodule not registered yet; skipping preemptive update.",
                "LibXR 子模块尚未登记，跳过预先更新。",
            )
        )

    repo_url = make_repo_url(git_base, "Jiu-Xiao", "libxr")
    if not registered:
        logging.info(
            tr(
                f"Adding LibXR as submodule from {repo_url} ...",
                f"正在从 {repo_url} 加入 LibXR 子模块……",
            )
        )
        run_command(["git", "-C", project_dir, "submodule", "add", repo_url, sub_rel_path_posix])
        logging.info(tr("LibXR submodule added and initialized.", "LibXR 子模块已加入并初始化。"))
        added_submodule = True
    else:
        logging.info(tr("LibXR submodule already registered.", "LibXR 子模块已登记。"))

    if os.path.exists(libxr_path):
        logging.info(tr("LibXR submodule path exists.", "LibXR 子模块路径已存在。"))
        current_commit = get_git_head(libxr_path)
        target_commit = ""

        # The project's gitlink pins LibXR. Only an explicit --commit or a
        # submodule added by this run moves the checkout; otherwise the
        # checkout stays where it is and a different package default is only
        # reported.
        if libxr_commit:
            target_commit = libxr_commit
            logging.info(
                tr(
                    f"Checking out LibXR to requested commit {target_commit}",
                    f"把 LibXR 检出到指定的提交 {target_commit}",
                )
            )
        elif added_submodule and not existing_checkout and default_libxr_commit:
            target_commit = default_libxr_commit
            logging.info(
                tr(
                    f"Initializing new LibXR submodule to default commit {target_commit}",
                    f"把新加入的 LibXR 子模块初始化到默认提交 {target_commit}",
                )
            )
        elif default_libxr_commit and current_commit != default_libxr_commit:
            if is_commit_ancestor(libxr_path, default_libxr_commit, current_commit):
                logging.info(
                    tr(
                        "LibXR checkout is newer than this generator's default; keeping it.",
                        "LibXR 的检出比本生成器的默认提交新，保留不变。",
                    )
                )
            else:
                relation = (
                    tr("older than", "早于")
                    if is_commit_ancestor(libxr_path, current_commit, default_libxr_commit)
                    else tr("different from", "不同于")
                )
                logging.warning(
                    tr(
                        f"LibXR checkout {current_commit[:12]} is {relation} this generator's "
                        f"default {default_libxr_commit[:12]}; it was left unchanged. To switch, "
                        f"run xr_cubemx_cfg with --commit {default_libxr_commit} (or check out "
                        "the commit in Middlewares/Third_Party/LibXR) and commit the gitlink.",
                        f"LibXR 的检出 {current_commit[:12]} {relation}本生成器的默认提交 "
                        f"{default_libxr_commit[:12]}，未做改动。如需切换，请用 --commit "
                        f"{default_libxr_commit} 运行 xr_cubemx_cfg（或在 "
                        "Middlewares/Third_Party/LibXR 中检出该提交），然后提交 gitlink。",
                    )
                )
        else:
            logging.info(tr("Keeping the existing LibXR checkout.", "保留现有的 LibXR 检出。"))

        if target_commit:
            run_command(["git", "-C", libxr_path, "fetch", "origin"], ignore_error=True)
            run_command(["git", "-C", libxr_path, "checkout", target_commit])


def create_user_directory(project_dir):
    """确保工程中有 User 目录，并返回其路径。
    Make sure the project has a User directory and return its path.
    """
    user_path = os.path.join(project_dir, "User")
    if not os.path.exists(user_path):
        os.makedirs(user_path)
    return user_path


def set_terminal_source(user_path, terminal_source):
    """把 -t/--terminal 指定的终端设备写入 User/libxr_config.yaml 的 terminal_source，
    文件不存在时新建。
    Record the -t/--terminal device as terminal_source in User/libxr_config.yaml, creating the
    file when it does not exist.

    xr_gen_code_stm32 从这个文件读取终端设备，所以之后重新生成时沿用该设置；文件中的其他键和
    注释保持不变。文件无法按 LibXR 配置读取时记录错误并以退出码 1 结束。
    xr_gen_code_stm32 reads the terminal device from this file, so the choice persists for later
    regenerations. Other keys and comments are kept. A file that cannot be read as a LibXR
    configuration logs an error and exits with code 1.
    """
    from libxr import libxr_config_file

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
    logging.info(
        tr(
            f"Set terminal_source to {terminal_source} in {config_path}",
            f"已在 {config_path} 中把 terminal_source 设为 {terminal_source}",
        )
    )


def process_ioc_file(project_dir, yaml_output):
    """调用 xr_parse_ioc 解析工程目录中的 .ioc 文件，把 YAML 配置写到 yaml_output。
    Run xr_parse_ioc to parse the .ioc file of the project and write the YAML configuration to
    yaml_output.
    """
    logging.info(tr("Parsing .ioc file...", "正在解析 .ioc 文件……"))
    run_command(
        [
            sys.executable,
            "-m",
            "libxr.peripheral_analyzer_stm32",
            "-d",
            project_dir,
            "-o",
            yaml_output,
        ]
    )


def generate_cpp_code(yaml_output, cpp_output, xrobot_enable=False):
    """调用 xr_gen_code_stm32，根据 YAML 配置把 C++ 代码生成到 cpp_output；xrobot_enable 时
    加 --xrobot。
    Run xr_gen_code_stm32 to generate C++ code from the YAML configuration into cpp_output, with
    --xrobot when xrobot_enable is set.
    """
    logging.info(tr("Generating C++ code...", "正在生成 C++ 代码……"))
    cmd = [sys.executable, "-m", "libxr.generator_code_stm32", "-i", yaml_output, "-o", cpp_output]
    if xrobot_enable:
        cmd.append("--xrobot")
    run_command(cmd)


def generate_cmake_file(project_dir):
    """调用 xr_stm32_cmake 为工程生成 CMakeLists.txt。
    Run xr_stm32_cmake to generate the CMakeLists.txt of the project.
    """
    run_command([sys.executable, "-m", "libxr.generator_stm32_cmake", project_dir])


def _friendly_path_name(path: str) -> str:
    """路径的末级名称，用于提示信息（'.' 显示为当前目录名）；没有末级名称（如根目录）时为绝对路径。
    The last component of a path for messages, so '.' shows the current folder name; the
    absolute path when there is none, as for a root directory.
    """
    abs_path = os.path.abspath(path)
    base = os.path.basename(abs_path.rstrip(os.sep))
    return base or abs_path


def ensure_valid_cubemx_project(path: str):
    """path 没有 Core/ 目录、不像 STM32CubeMX 工程时记录错误并以退出码 1 结束；提示中用
    目录名代替 '.'。
    Log an error and exit with code 1 when path has no Core/ directory and so does not look like
    an STM32CubeMX project; the message shows the folder name instead of '.'.
    """
    display_name = _friendly_path_name(path)
    core_dir = os.path.join(path, "Core")
    if not os.path.isdir(core_dir):
        logging.error(
            tr(
                f"{display_name} is not a valid STM32CubeMX project: missing Core/ directory",
                f"{display_name}: 不是有效的 STM32CubeMX 工程，缺少 Core/ 目录",
            )
        )
        sys.exit(1)


def main():
    """xr_cubemx_cfg 命令入口：选择 Git 源，加入 LibXR 子模块，再生成配置、C++ 代码和
    CMakeLists.txt。
    Entry point of xr_cubemx_cfg: choose the Git source, add the LibXR submodule, then generate
    the configuration, the C++ code and CMakeLists.txt.

    未给出 --commit 时以 libxr_version.py 中锁定的 commit 为默认值。--git-source 为 auto 时，
    在 GitHub、内置镜像、XR_GIT_MIRRORS 和 --git-mirrors 中选出响应最快的源。
    Without --commit, the commit locked in libxr_version.py is the default. With --git-source
    auto, the fastest of GitHub, the built-in mirror, XR_GIT_MIRRORS and --git-mirrors is chosen.
    """
    from libxr.output import configure_logging
    from libxr.package_info import LibXRPackageInfo

    configure_logging()
    LibXRPackageInfo.check_and_print()

    localize_argparse()
    parser = argparse.ArgumentParser(
        description=tr("Automate STM32CubeMX project setup", "自动配置 STM32CubeMX 工程")
    )
    parser.add_argument(
        "-d",
        "--directory",
        required=True,
        help=tr("STM32CubeMX project directory", "STM32CubeMX 工程目录"),
    )
    parser.add_argument(
        "-t",
        "--terminal",
        default="",
        help=tr(
            "Terminal device (e.g. usart1, usb_fs_cdc); stored as "
            "terminal_source in User/libxr_config.yaml",
            "终端设备（例如 usart1、usb_fs_cdc），记录为 User/libxr_config.yaml 中的 "
            "terminal_source",
        ),
    )
    parser.add_argument("--xrobot", action="store_true", help=tr("Support XRobot", "支持 XRobot"))
    parser.add_argument(
        "--commit",
        default="",
        help=tr("Specify locked LibXR commit hash", "指定锁定的 LibXR 提交哈希"),
    )
    parser.add_argument(
        "--git-source",
        default="auto",
        help=tr(
            "Git source base URL or full repo URL, or 'auto'/'github' (default: auto)",
            "Git 源的基础地址或完整仓库地址，或 'auto'/'github'（默认：auto）",
        ),
    )
    parser.add_argument(
        "--git-mirrors",
        default="",
        help=tr(
            "Comma-separated mirror base/repo URLs (will be tried when --git-source=auto)",
            "以逗号分隔的镜像基础地址或仓库地址（--git-source=auto 时参与选择）",
        ),
    )

    args = parser.parse_args()

    project_dir = args.directory.rstrip("/")
    terminal_source = args.terminal
    xrobot_enable = bool(args.xrobot)

    libxr_commit = args.commit.strip()
    default_libxr_commit = ""
    if not libxr_commit:
        try:
            from libxr.libxr_version import LibXRInfo

            default_libxr_commit = LibXRInfo.COMMIT
        except ImportError as e:
            logging.info(
                tr(
                    f"No lock commit found in src/libxr/libxr_version.py: {e}",
                    f"src/libxr/libxr_version.py 中没有锁定的提交：{e}",
                )
            )
            default_libxr_commit = ""

    if libxr_commit:
        logging.info(
            tr(f"Requested LibXR commit: {libxr_commit}", f"指定的 LibXR 提交：{libxr_commit}")
        )
    elif default_libxr_commit:
        logging.info(
            tr(
                f"Default LibXR commit: {default_libxr_commit}",
                f"默认的 LibXR 提交：{default_libxr_commit}",
            )
        )

    if not os.path.isdir(project_dir):
        display_name = _friendly_path_name(project_dir)
        logging.error(tr(f"Directory {display_name} does not exist", f"目录 {display_name} 不存在"))
        sys.exit(1)

    # Validate STM32CubeMX project structure (must have Core/ directory)
    ensure_valid_cubemx_project(project_dir)

    # Select Git source (auto benchmarks default and mirrors)
    env_mirrors = os.environ.get("XR_GIT_MIRRORS", "")
    cli_mirrors = [m for m in args.git_mirrors.split(",") if m.strip()]
    all_mirrors = (
        DEFAULT_MIRRORS
        + [m.strip() for m in (env_mirrors.split(",") if env_mirrors else []) if m.strip()]
        + cli_mirrors
    )

    if args.git_source == "auto":
        git_base = pick_git_base(
            default_base="https://github.com", mirrors=all_mirrors, timeout=5.0
        )
    elif args.git_source == "github":
        git_base = "https://github.com"
    else:
        git_base = args.git_source
    logging.info(tr(f"Selected Git base/repo: {git_base}", f"选用的 Git 源：{git_base}"))

    # Add Git submodule if necessary
    add_libxr(
        project_dir,
        libxr_commit if libxr_commit else None,
        git_base=git_base,
        default_libxr_commit=default_libxr_commit if default_libxr_commit else None,
    )

    # Find .ioc file
    ioc_file = find_ioc_file(project_dir)
    if not ioc_file:
        logging.error(tr("No .ioc file found", "找不到 .ioc 文件"))
        sys.exit(1)

    logging.info(tr(f"Found .ioc file: {ioc_file}", f"找到 .ioc 文件：{ioc_file}"))

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

    logging.info(tr("[Pass] All tasks completed successfully!", "[通过] 全部任务已完成！"))


if __name__ == "__main__":
    main()
