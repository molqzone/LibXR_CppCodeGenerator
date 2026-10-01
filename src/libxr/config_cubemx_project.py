#!/usr/bin/env python

"""libxr stm32 setup：把 STM32CubeMX 工程配置为使用 LibXR 的工程。
libxr stm32 setup: set up an STM32CubeMX project to use LibXR.

依次加入 LibXR 子模块（Middlewares/Third_Party/LibXR），创建 .gitignore 和 User 目录，记录终端设备，
再像 libxr parse、libxr gen 和 libxr stm32 cmake 一样生成配置、C++ 代码和 CMakeLists.txt。
In order it adds the LibXR submodule (Middlewares/Third_Party/LibXR), creates .gitignore and the
User directory, records the terminal device, then produces the configuration, the C++ code and
CMakeLists.txt as libxr parse, libxr gen and libxr stm32 cmake do.
"""

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys

from xr_syntax.i18n import tr

DEFAULT_MIRRORS = [
    "https://gitee.com/jiu-xiao/libxr",
]

# LibXR 的正式地址，工程的 .gitmodules 记录的就是它。
# The canonical LibXR URL, which the project's .gitmodules records.
LIBXR_URL = "https://github.com/xrobot-org/libxr.git"
# LibXR 在 GitHub 上迁移前后的地址；镜像只替换这些地址。
# LibXR's GitHub URLs before and after the transfer; a mirror only stands in for these.
LIBXR_URL_PATTERN = re.compile(
    r"^https://github\.com/(xrobot-org|jiu-xiao)/libxr(\.git)?/?$", re.IGNORECASE
)
SUBMODULE_PATH = "Middlewares/Third_Party/LibXR"


def is_git_repo(path):
    """path 位于 Git 工作树中时为 True。
    True when path is inside a Git work tree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
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
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return os.path.realpath(result.stdout.strip()) == os.path.realpath(path)
    except subprocess.CalledProcessError:
        return False


def run_command(cmd: list[str], ignore_error=False):
    """不经 shell 运行命令 cmd（参数列表）并返回标准输出；日志中各参数按 shell 规则引用。
    Run the command cmd, a list of arguments, without a shell and return its stdout; the log
    shows the arguments shell-quoted.

    命令失败时，ignore_error 为 True 则记录警告并仍返回标准输出，否则记录错误并以退出码 1 结束进程。
    On failure, ignore_error logs a warning and still returns stdout; otherwise the error is
    logged and the process exits with code 1.
    """
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    command_line = " ".join(shlex.quote(str(argument)) for argument in cmd)
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


def _probe_environment() -> dict:
    """测速用的环境：git 不提示输入账号密码，地址填错时直接失败。
    The environment for probing: git asks for no credentials, so a wrong address just fails.
    """
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")


def pick_git_base(default_base="https://github.com", mirrors=None, timeout=5.0):
    """在默认源和镜像中选出 git ls-remote 响应最快的 LibXR Git 源。
    Pick the LibXR Git source whose git ls-remote answers fastest, among the default and the
    mirrors.

    default_base 和 mirrors 中的每一项可以是基础地址（如 https://github.com，探测
    <基础地址>/xrobot-org/libxr.git），也可以是以 .git 或 libxr 结尾的完整仓库地址。探测时 git
    不提示输入账号密码。
    default_base and each mirror are either a base URL such as https://github.com, probed as
    <base>/xrobot-org/libxr.git, or a full repository URL ending in .git or libxr. Probes never
    ask for credentials.

    Returns:
        最快的候选项，保持传入时的形式；全部失败或超过 timeout 秒时为 default_base。
        The fastest candidate as it was given; default_base when every probe fails or takes
        longer than timeout seconds.
    """
    import time

    candidates = [default_base] + [m.strip() for m in (mirrors or []) if m.strip()]
    scores = []
    for item in candidates:
        start = time.monotonic()
        try:
            r = subprocess.run(
                ["git", "ls-remote", "-h", make_repo_url(item)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env=_probe_environment(),
            )
            if r.returncode == 0:
                scores.append((time.monotonic() - start, item))
        except subprocess.TimeoutExpired:
            pass
    return min(scores)[1] if scores else default_base


def make_repo_url(base_or_repo: str, owner="xrobot-org", repo="libxr"):
    """完整的仓库地址：base_or_repo 以 .git 或仓库名结尾时原样返回，否则拼成
    <base_or_repo>/<owner>/<repo>.git。
    The full repository URL: base_or_repo as is when it ends in .git or the repository name,
    else <base_or_repo>/<owner>/<repo>.git.
    """
    if (
        base_or_repo.endswith(".git")
        or base_or_repo.rstrip("/").split("/")[-1].lower() == repo.lower()
    ):
        return base_or_repo
    return f"{base_or_repo.rstrip('/')}/{owner}/{repo}.git"


class LibXRSource:
    """克隆 LibXR 时实际使用的源。只有需要克隆时才测速选择，工程的 .gitmodules 始终记录 LIBXR_URL。
    The source LibXR is actually cloned from. It is chosen, by probing, only when a clone is
    needed; the project's .gitmodules always records LIBXR_URL.

    git_source 为 auto 时在 GitHub 和 mirrors 中选最快的，为 github 时用 GitHub，否则是给定的
    基础地址或仓库地址。
    With git_source auto the fastest of GitHub and the mirrors is used, with github GitHub, and
    otherwise the given base or repository URL.
    """

    def __init__(self, git_source: str = "auto", mirrors=()):
        """记录源的选择方式；此时还不测速。
        Record how the source is chosen; nothing is probed yet.
        """
        self.git_source = git_source
        self.mirrors = list(mirrors)
        self._url = None

    def url(self) -> str:
        """选中的仓库地址；第一次调用时选择并记录日志。
        The chosen repository URL; chosen and logged on the first call.
        """
        if self._url is None:
            if self.git_source == "auto":
                base = pick_git_base("https://github.com", self.mirrors, timeout=5.0)
            elif self.git_source == "github":
                base = "https://github.com"
            else:
                base = self.git_source
            self._url = make_repo_url(base)
            logging.info(tr(f"Cloning LibXR from {self._url}", f"从 {self._url} 克隆 LibXR"))
        return self._url

    def config_for(self, recorded_url: str) -> list:
        """让 recorded_url 改从选中的源获取的 git -c 参数；recorded_url 不是 LibXR 的 GitHub 地址
        （例如用户自己的分叉）或与选中的源相同时为空。
        git -c options that fetch recorded_url from the chosen source instead; empty when
        recorded_url is not a GitHub address of LibXR (a user's fork, say) or is the chosen source.

        git 2.38 起子模块默认不能从本地路径克隆；选中的源是本地仓库（路径或 file: 地址）时，参数中
        另外放行 file 协议，只作用于这一条命令。
        Since git 2.38 a submodule cannot be cloned from a local path by default; when the chosen
        source is a local repository, a path or a file: URL, the options also allow the file
        protocol for this one command.
        """
        if not LIBXR_URL_PATTERN.match(recorded_url):
            return []
        url = self.url()
        if url == recorded_url:
            return []
        options = ["-c", f"url.{url}.insteadOf={recorded_url}"]
        if url.startswith("file:") or os.path.isdir(url):
            options += ["-c", "protocol.file.allow=always"]
        return options


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
        ["git", "-C", path, "rev-parse", "HEAD"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
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


def _recorded_url(project_dir) -> str:
    """工程 .gitmodules 中 LibXR 子模块记录的地址；没有时为空字符串。
    The URL that the project's .gitmodules records for the LibXR submodule; an empty string
    when there is none.
    """
    result = subprocess.run(
        ["git", "-C", project_dir, "config", "-f", ".gitmodules", "--get-regexp", r"\.path$"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in result.stdout.splitlines():
        key, _, path = line.partition(" ")
        if path.strip() == SUBMODULE_PATH:
            name = key[: -len(".path")]
            url = subprocess.run(
                ["git", "-C", project_dir, "config", "-f", ".gitmodules", f"{name}.url"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            return url.stdout.strip()
    return ""


def _has_commit(repo_path, commit) -> bool:
    """repo_path 中已有 commit 时为 True。
    True when repo_path already has commit.
    """
    result = subprocess.run(
        ["git", "-C", repo_path, "cat-file", "-e", f"{commit}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def add_libxr(project_dir, libxr_commit=None, source=None, default_libxr_commit=None):
    """把 LibXR 作为 Git 子模块放到 Middlewares/Third_Party/LibXR，并决定检出哪个 commit。
    Put LibXR at Middlewares/Third_Party/LibXR as a Git submodule and decide which commit is
    checked out.

    工程还不是 Git 仓库时先执行 git init。已登记的子模块会同步地址，没有检出时按 gitlink 初始化；
    未登记时以 LIBXR_URL 加入子模块。需要克隆时才从 source（默认 LibXRSource()）选出的源获取，
    .gitmodules 仍记录 LIBXR_URL。已有的 LibXR 目录不会被删除、移动或重新克隆。
    A project that is not yet a Git repository gets git init. A registered submodule has its URL
    synced and, without a checkout, is initialized to its gitlink; an unregistered one is added
    as LIBXR_URL. Only a clone fetches from the source that source (LibXRSource() by default)
    chooses, and .gitmodules still records LIBXR_URL. An existing LibXR directory is never
    deleted, moved or re-cloned.

    只有给出 libxr_commit，或本次新加入且原来没有检出的子模块（此时用 default_libxr_commit）才会
    切换检出；新加入的子模块的 gitlink 随之暂存。其他情况保持现有检出，与 default_libxr_commit
    不同且不比它新时记录警告。
    Only libxr_commit, or default_libxr_commit for a submodule added by this run without an earlier
    checkout, moves the checkout; the gitlink of a newly added submodule is staged with it.
    Otherwise the existing checkout is kept, with a warning when it differs from
    default_libxr_commit and is not newer than it.

    Raises:
        SystemExit: LibXR 目录既不是 Git 检出也不是空目录，或必需的 git 命令失败。
            The LibXR directory is neither a Git checkout nor empty, or a required git command
            failed.
    """
    source = source or LibXRSource()
    libxr_path = os.path.join(project_dir, *SUBMODULE_PATH.split("/"))
    os.makedirs(os.path.dirname(libxr_path), exist_ok=True)

    def has_registered_submodule(repo_root, rel_path):
        """rel_path 已登记为子模块时为 True：索引中该路径是 gitlink（模式 160000）。只在
        .gitmodules 中出现（例如解压 ZIP 后 git init 的工程）不算登记，因为没有记录 commit。
        True when rel_path is registered as a submodule: the index holds a gitlink (mode 160000)
        there. A path only named in .gitmodules, as in a project unpacked from a ZIP and then
        given git init, does not count: no commit is recorded for it.
        """
        result = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "--stage", "--", rel_path],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return False
        return any(line.split()[:1] == ["160000"] for line in result.stdout.splitlines())

    if not is_git_repo(project_dir):
        logging.warning(
            tr(
                f"{project_dir} is not a Git repository. Initializing...",
                f"{project_dir}：不是 Git 仓库，正在初始化……",
            )
        )
        run_command(["git", "init", project_dir])

    registered = has_registered_submodule(project_dir, SUBMODULE_PATH)
    checkout_path_present = os.path.lexists(libxr_path)
    existing_checkout = checkout_path_present and is_git_worktree_root(libxr_path)
    added_submodule = False

    # 已有的 LibXR 目录可能含有用户自己的提交或源码，不会被删除、移动或重新克隆。
    # An existing LibXR directory may hold the user's own commits or sources;
    # it is never deleted, moved or re-cloned.
    if checkout_path_present and not existing_checkout and not is_empty_directory(libxr_path):
        logging.error(
            tr(
                f"{libxr_path} exists but is not a valid Git checkout; it was left untouched. "
                "Move it away or turn it into a LibXR checkout, then run again.",
                f"{libxr_path}：已存在，但不是有效的 Git 检出，未做改动。"
                "请把它移走或改成 LibXR 的检出，然后重新运行。",
            )
        )
        sys.exit(1)

    if registered:
        run_command(["git", "-C", project_dir, "submodule", "sync", "--", SUBMODULE_PATH])
        if not existing_checkout:
            options = source.config_for(_recorded_url(project_dir))
            run_command(
                ["git", *options, "-C", project_dir, "submodule", "update", "--init"]
                + ["--recursive", "--", SUBMODULE_PATH]
            )
    else:
        if checkout_path_present and not existing_checkout:
            # 空目录（例如 ZIP 中的子模块目录）挡住 submodule add；它是空的，可以删除。
            # An empty directory, such as a submodule folder from a ZIP, blocks submodule add;
            # it is empty, so it can go.
            os.rmdir(libxr_path)
        options = [] if existing_checkout else source.config_for(LIBXR_URL)
        run_command(
            ["git", *options, "-C", project_dir, "submodule", "add", LIBXR_URL, SUBMODULE_PATH]
        )
        logging.info(
            tr(f"Added the LibXR submodule ({LIBXR_URL}).", f"已加入 LibXR 子模块（{LIBXR_URL}）。")
        )
        added_submodule = True

    if not os.path.exists(libxr_path):
        return
    current_commit = get_git_head(libxr_path)
    target_commit = ""

    # LibXR 由工程的 gitlink 锁定。只有显式的 --commit 或本次新加入的子模块才会切换检出；
    # 其他情况下检出保持不变，与包内默认提交不同时只报告。
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
                    f"LibXR checkout {current_commit[:12]} is newer than this generator's "
                    "default; keeping it.",
                    f"LibXR 的检出 {current_commit[:12]} 比本生成器的默认提交新，保留不变。",
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
                    f"run `libxr stm32 setup` with --commit {default_libxr_commit} (or check out "
                    "the commit in Middlewares/Third_Party/LibXR) and commit the gitlink.",
                    f"LibXR 的检出 {current_commit[:12]} {relation}本生成器的默认提交 "
                    f"{default_libxr_commit[:12]}，未做改动。如需切换，请用 --commit "
                    f"{default_libxr_commit} 运行 `libxr stm32 setup`（或在 "
                    "Middlewares/Third_Party/LibXR 中检出该提交），然后提交 gitlink。",
                )
            )
    else:
        logging.info(
            tr(
                f"Keeping the LibXR checkout {current_commit[:12]}.",
                f"保留现有的 LibXR 检出 {current_commit[:12]}。",
            )
        )

    if target_commit:
        if not _has_commit(libxr_path, target_commit):
            origin = subprocess.run(
                ["git", "-C", libxr_path, "remote", "get-url", "origin"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            ).stdout.strip()
            options = source.config_for(origin)
            run_command(["git", *options, "-C", libxr_path, "fetch", "origin"], ignore_error=True)
        run_command(["git", "-C", libxr_path, "checkout", target_commit])
        if added_submodule:
            # submodule add 暂存的是克隆时的 HEAD；暂存检出后的 commit。
            # submodule add staged the HEAD of the clone; stage the checked-out commit.
            run_command(["git", "-C", project_dir, "add", "--", SUBMODULE_PATH])


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

    libxr gen 从这个文件读取终端设备，所以之后重新生成时沿用该设置；文件中的其他键和
    注释保持不变。文件无法按 LibXR 配置读取时记录错误并以退出码 1 结束。
    libxr gen reads the terminal device from this file, so the choice persists for later
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


def _friendly_path_name(path: str) -> str:
    """路径的末级名称，用于提示信息（'.' 显示为当前目录名）；没有末级名称（如根目录）时为绝对路径。
    The last component of a path for messages, so '.' shows the current folder name; the
    absolute path when there is none, as for a root directory.
    """
    abs_path = os.path.abspath(path)
    base = os.path.basename(abs_path.rstrip(os.sep))
    return base or abs_path


def _stop(english: str, chinese: str) -> None:
    """按当前语言记录错误，并以退出码 1 结束。
    Log the error in the current language and exit with code 1.
    """
    logging.error(tr(english, chinese))
    sys.exit(1)


def check_project(project_dir: str) -> str:
    """检查 project_dir 是 CMake 形式的 STM32CubeMX 工程，返回其中唯一的 .ioc 文件的路径。
    Check that project_dir is an STM32CubeMX project in CMake form and return the path of its
    only .ioc file.

    setup 在改动工程之前调用它。目录不存在、没有 Core/、.ioc 文件不是恰好一个，或者没有
    CMakeLists.txt（CubeMX 生成的不是 CMake 工程）时记录错误并以退出码 1 结束；提示中用目录名
    代替 '.'。
    setup calls it before it changes the project. A missing directory, no Core/, other than
    exactly one .ioc file, or no CMakeLists.txt (CubeMX generated something other than a CMake
    project) logs an error and exits with code 1; the messages show the folder name instead
    of '.'.
    """
    name = _friendly_path_name(project_dir)
    if not os.path.isdir(project_dir):
        _stop(f"Directory {name} does not exist", f"目录 {name} 不存在")
    if not os.path.isdir(os.path.join(project_dir, "Core")):
        _stop(
            f"{name} is not a valid STM32CubeMX project: missing Core/ directory",
            f"{name}：不是有效的 STM32CubeMX 工程，缺少 Core/ 目录",
        )
    ioc_files = sorted(entry for entry in os.listdir(project_dir) if entry.endswith(".ioc"))
    if not ioc_files:
        _stop(f"{name} holds no .ioc file", f"{name} 中没有 .ioc 文件")
    if len(ioc_files) > 1:
        _stop(
            f"{name} holds several .ioc files ({', '.join(ioc_files)}); a directory holds one "
            "CubeMX project",
            f"{name} 中有多个 .ioc 文件（{'、'.join(ioc_files)}）；一个目录只放一个 CubeMX 工程",
        )
    if not os.path.isfile(os.path.join(project_dir, "CMakeLists.txt")):
        _stop(
            f"{name} has no CMakeLists.txt; set Toolchain / IDE to CMake in the Project Manager "
            "of STM32CubeMX and generate the project again",
            f"{name} 中没有 CMakeLists.txt；请在 STM32CubeMX 的 Project Manager 中把 "
            "Toolchain / IDE 设为 CMake，然后重新生成工程",
        )
    return os.path.join(project_dir, ioc_files[0])


def setup_project(
    project_dir: str,
    terminal_source: str = "",
    xrobot_enable: bool | None = None,
    commit: str = "",
    git_source: str = "auto",
    git_mirrors: str = "",
) -> None:
    """加入 LibXR 子模块，再生成配置、C++ 代码和 CMakeLists.txt。
    Add the LibXR submodule, then generate the configuration, the C++ code and CMakeLists.txt.

    改动工程之前先用 check_project() 检查工程。commit 为空时以 libxr_version.py 中锁定的 commit
    为默认值。需要克隆 LibXR 时，git_source 为 auto 则在 GitHub、内置镜像、XR_GIT_MIRRORS 和
    git_mirrors（逗号分隔）中选出响应最快的源。xrobot_enable 为 None 时沿用工程现在的选择：
    User/app_main.cpp 由 --xrobot 生成时继续生成 XRobot 代码。
    check_project() checks the project before anything changes. With an empty commit, the
    commit locked in libxr_version.py is the default. When LibXR has to
    be cloned, git_source auto picks the fastest of GitHub, the built-in mirror,
    XR_GIT_MIRRORS and git_mirrors (comma-separated). With xrobot_enable None the project keeps
    its choice: XRobot code is generated again when User/app_main.cpp was generated with
    --xrobot.
    """
    from libxr.generator_code_stm32 import generate
    from libxr.generator_stm32_cmake import integrate, project_uses_xrobot
    from libxr.peripheral_analyzer_stm32 import parse_project

    if shutil.which("git") is None:
        logging.error(
            tr(
                "git was not found on PATH; LibXR is added to the project as a Git submodule",
                "PATH 中找不到 git；LibXR 以 Git 子模块的形式加入工程",
            )
        )
        sys.exit(1)

    project_dir = project_dir.rstrip("/")
    # 先检查完工程再改动它。
    # Check the whole project before changing anything.
    ioc_file = check_project(project_dir)

    libxr_commit = commit.strip()
    default_libxr_commit = ""
    if not libxr_commit:
        try:
            from libxr.libxr_version import LibXRInfo

            default_libxr_commit = LibXRInfo.COMMIT
        except ImportError:
            logging.info(
                tr(
                    "No default LibXR commit: src/libxr/libxr_version.py is missing "
                    "(scripts/gen_libxr_version.py creates it); a new submodule stays at the "
                    "commit it is cloned at.",
                    "没有默认的 LibXR 提交：缺少 src/libxr/libxr_version.py"
                    "（由 scripts/gen_libxr_version.py 生成）；新加入的子模块停在克隆时的提交。",
                )
            )

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

    if xrobot_enable is None:
        xrobot_enable = project_uses_xrobot(project_dir)
        if xrobot_enable:
            logging.info(
                tr(
                    "User/app_main.cpp uses XRobot; generating with --xrobot "
                    "(--no-xrobot turns it off).",
                    "User/app_main.cpp 使用了 XRobot，继续按 --xrobot 生成（--no-xrobot 可关闭）。",
                )
            )

    # 克隆用的源只在需要克隆时选择（auto 时对默认源和镜像测速）。
    # The source for cloning is chosen only when a clone is needed (auto probes the default and
    # the mirrors).
    env_mirrors = os.environ.get("XR_GIT_MIRRORS", "")
    cli_mirrors = [m for m in git_mirrors.split(",") if m.strip()]
    all_mirrors = (
        DEFAULT_MIRRORS
        + [m.strip() for m in (env_mirrors.split(",") if env_mirrors else []) if m.strip()]
        + cli_mirrors
    )
    add_libxr(
        project_dir,
        libxr_commit if libxr_commit else None,
        source=LibXRSource(git_source, all_mirrors),
        default_libxr_commit=default_libxr_commit if default_libxr_commit else None,
    )

    logging.info(tr(f"Found .ioc file: {ioc_file}", f"找到 .ioc 文件：{ioc_file}"))

    create_gitignore_file(project_dir)

    # 创建 User 目录。
    # Create user directory
    user_path = create_user_directory(project_dir)

    # 确定输出路径。
    # Define paths
    yaml_output = os.path.join(project_dir, ".config.yaml")
    cpp_output = os.path.join(user_path, "app_main.cpp")

    # 为代码生成器记录终端设备。
    # Record the terminal device for the code generator
    if terminal_source:
        set_terminal_source(user_path, terminal_source)

    logging.info(tr("Parsing .ioc file...", "正在解析 .ioc 文件……"))
    parse_project(project_dir, yaml_output, summary=False)

    logging.info(tr("Generating C++ code...", "正在生成 C++ 代码……"))
    generate(yaml_output, cpp_output, xrobot_enable)

    integrate(project_dir)

    logging.info(tr("[Pass] All tasks completed.", "[通过] 全部任务已完成。"))


if __name__ == "__main__":
    from libxr.legacy import run

    raise SystemExit(run("xr_cubemx_cfg"))
