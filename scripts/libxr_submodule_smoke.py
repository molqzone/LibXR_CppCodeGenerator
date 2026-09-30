#!/usr/bin/env python3
"""用本地 Git 仓库对 add_libxr 的 LibXR 子模块更新策略做冒烟测试。
Smoke-test the LibXR submodule update policy of add_libxr with local Git repositories.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
# 检查比较英文输出，与运行机器的语言无关。
# The checks compare English output whatever the language of the machine.
os.environ["XR_LANG"] = "en"

from libxr.config_cubemx_project import add_libxr  # noqa: E402


def git(*args: str, cwd: Path | None = None) -> str:
    """在 cwd 中运行 git，返回去掉首尾空白的标准输出；失败时抛出 CalledProcessError。
    Run git in cwd and return its stripped stdout; CalledProcessError on failure.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo: Path, name: str, text: str) -> str:
    """把 text 写入 repo 中的文件 name 并以 text 为提交信息提交，返回新的 commit。
    Write text to the file name in repo, commit it with text as the message and return the new
    commit.
    """
    (repo / name).write_text(text, encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-m", text, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


def create_libxr_remote(root: Path) -> tuple[Path, str, str, str, str]:
    """创建代替 LibXR 的裸仓库：master 上依次有 old、default、newer 三个 commit，另有从 old 分出的
    divergent commit。
    Create a bare repository standing in for LibXR: old, default and newer commits in order on
    master, and a divergent commit branched from old.

    Returns:
        (裸仓库路径, old, default, newer, divergent)。
        (bare repository path, old, default, newer, divergent).
    """
    source = root / "libxr-source"
    source.mkdir()
    git("init", "-b", "master", cwd=source)
    git("config", "user.name", "Smoke Test", cwd=source)
    git("config", "user.email", "smoke@example.com", cwd=source)
    old_commit = commit_file(source, "version.txt", "old")
    default_commit = commit_file(source, "version.txt", "default")
    newer_commit = commit_file(source, "version.txt", "newer")
    git("checkout", "-b", "divergent", old_commit, cwd=source)
    divergent_commit = commit_file(source, "divergent.txt", "divergent")
    git("checkout", "master", cwd=source)

    remote = root / "libxr.git"
    git("clone", "--bare", str(source), str(remote), cwd=root)
    return remote, old_commit, default_commit, newer_commit, divergent_commit


def create_project(
    root: Path,
    name: str,
    remote: Path,
    recorded_commit: str,
    checkout_commit: str,
) -> tuple[Path, Path]:
    """创建以 remote 为 LibXR 子模块的工程：gitlink 记录 recorded_commit，检出停在 checkout_commit。
    Create a project with remote as its LibXR submodule: the gitlink records recorded_commit and
    the checkout is left at checkout_commit.

    Returns:
        (工程目录, LibXR 检出目录)。
        (project directory, LibXR checkout directory).
    """
    project = root / name
    project.mkdir()
    git("init", "-b", "master", cwd=project)
    git("config", "user.name", "Smoke Test", cwd=project)
    git("config", "user.email", "smoke@example.com", cwd=project)
    git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(remote),
        "Middlewares/Third_Party/LibXR",
        cwd=project,
    )
    checkout = project / "Middlewares" / "Third_Party" / "LibXR"
    git("checkout", recorded_commit, cwd=checkout)
    git("add", ".gitmodules", "Middlewares/Third_Party/LibXR", cwd=project)
    git("commit", "-m", "add LibXR", cwd=project)
    git("checkout", checkout_commit, cwd=checkout)
    return project, checkout


@contextlib.contextmanager
def allow_file_protocol():
    """在 with 块内把 GIT_ALLOW_PROTOCOL 设为 file，使 git 可以从本地路径克隆子模块；
    退出时恢复原值。
    Set GIT_ALLOW_PROTOCOL to file for the with block so git may clone submodules from local
    paths; restore the old value on exit.
    """
    old_value = os.environ.get("GIT_ALLOW_PROTOCOL")
    os.environ["GIT_ALLOW_PROTOCOL"] = "file"
    try:
        yield
    finally:
        if old_value is None:
            os.environ.pop("GIT_ALLOW_PROTOCOL", None)
        else:
            os.environ["GIT_ALLOW_PROTOCOL"] = old_value


def assert_head(checkout: Path, expected: str, scenario: str) -> None:
    """检出的 HEAD 不是 expected 时抛出 AssertionError，信息中带有场景名。
    Raise AssertionError, naming the scenario, when the HEAD of the checkout is not expected.
    """
    actual = git("rev-parse", "HEAD", cwd=checkout)
    if actual != expected:
        raise AssertionError(f"{scenario}: expected {expected}, got {actual}")


def run_policy_checks(root: Path) -> None:
    """逐个场景检查 add_libxr，任一场景不符合预期时抛出 AssertionError。
    Check add_libxr scenario by scenario; AssertionError when a scenario does not hold.

    已有检出（旧、当前、较新、分叉、有本地修改）保持不变；显式 commit 会切换检出；空目录按 gitlink
    初始化；含用户文件的非 Git 目录以退出码 1 拒绝且不被修改；未登记的已有克隆被原样采用。
    Existing checkouts (old, current, newer, divergent, with local changes) stay where they are;
    an explicit commit moves the checkout; an empty directory is initialized to its gitlink; a
    non-Git directory with user files is refused with exit code 1 and left unmodified; an
    unregistered existing clone is adopted as is.
    """
    remote, old_commit, default_commit, newer_commit, divergent_commit = create_libxr_remote(root)

    project, checkout = create_project(
        root, "stale-checkout", remote, recorded_commit=default_commit, checkout_commit=old_commit
    )
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, old_commit, "stale checkout with current gitlink")

    project, checkout = create_project(
        root, "old-gitlink", remote, recorded_commit=old_commit, checkout_commit=old_commit
    )
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, old_commit, "old checkout with old gitlink")

    project, checkout = create_project(
        root, "current", remote, recorded_commit=default_commit, checkout_commit=default_commit
    )
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, default_commit, "current checkout")

    project, checkout = create_project(
        root, "newer", remote, recorded_commit=default_commit, checkout_commit=newer_commit
    )
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, newer_commit, "newer checkout")

    project, checkout = create_project(
        root, "divergent", remote, recorded_commit=default_commit, checkout_commit=divergent_commit
    )
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, divergent_commit, "divergent checkout")

    project, checkout = create_project(
        root, "dirty", remote, recorded_commit=old_commit, checkout_commit=old_commit
    )
    (checkout / "version.txt").write_text("local changes", encoding="utf-8")
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, old_commit, "dirty old checkout")
    if git("status", "--porcelain", cwd=checkout) == "":
        raise AssertionError("dirty old checkout: local changes were lost")

    project, checkout = create_project(
        root, "explicit", remote, recorded_commit=old_commit, checkout_commit=newer_commit
    )
    add_libxr(project, libxr_commit=old_commit, default_libxr_commit=default_commit)
    assert_head(checkout, old_commit, "explicit commit")

    project, checkout = create_project(
        root, "empty-directory", remote, recorded_commit=old_commit, checkout_commit=old_commit
    )
    git("submodule", "deinit", "-f", "--", "Middlewares/Third_Party/LibXR", cwd=project)
    if not checkout.is_dir() or any(checkout.iterdir()):
        raise AssertionError("empty directory: deinit did not leave an empty checkout path")
    add_libxr(project, default_libxr_commit=default_commit)
    assert_head(checkout, old_commit, "empty checkout directory initialized to its gitlink")

    project, checkout = create_project(
        root, "not-a-checkout", remote, recorded_commit=old_commit, checkout_commit=old_commit
    )
    git("submodule", "deinit", "-f", "--", "Middlewares/Third_Party/LibXR", cwd=project)
    (checkout / "user_sources.cpp").write_text("keep", encoding="utf-8")
    try:
        add_libxr(project, default_libxr_commit=default_commit)
    except SystemExit as error:
        if error.code != 1:
            raise AssertionError(f"not a checkout: unexpected exit code {error.code}") from error
    else:
        raise AssertionError("not a checkout: a non-Git LibXR directory was accepted")
    if sorted(path.name for path in checkout.iterdir()) != ["user_sources.cpp"]:
        raise AssertionError("not a checkout: the existing directory was modified")

    project = root / "adopted-clone"
    project.mkdir()
    git("init", "-b", "master", cwd=project)
    git("config", "user.name", "Smoke Test", cwd=project)
    git("config", "user.email", "smoke@example.com", cwd=project)
    checkout = project / "Middlewares" / "Third_Party" / "LibXR"
    git("clone", str(remote), str(checkout), cwd=root)
    git("checkout", newer_commit, cwd=checkout)
    add_libxr(project, git_base=str(remote), default_libxr_commit=default_commit)
    assert_head(checkout, newer_commit, "adopted existing clone")


def main() -> int:
    """在临时目录中运行全部场景（屏蔽其间的标准输出），通过时打印结果并返回 0。
    Run all scenarios in a temporary directory with their stdout suppressed; print a message and
    return 0 when they pass.
    """
    with (
        allow_file_protocol(),
        tempfile.TemporaryDirectory(prefix="libxr_submodule_smoke_") as tmp,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        run_policy_checks(Path(tmp))
    print("LibXR submodule policy smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
