"""把远端 xrobot-org/libxr 仓库 master 分支的当前 commit 写入 src/libxr/libxr_version.py。
Write the current commit of the master branch of the remote xrobot-org/libxr repository into
src/libxr/libxr_version.py.

生成的文件只含 LibXRInfo.COMMIT，libxr stm32 setup 以它作为 LibXR 的默认 commit。
The generated file holds only LibXRInfo.COMMIT, which libxr stm32 setup uses as the default LibXR
commit.
"""

import logging
import os
import subprocess

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def get_remote_commit(url, ref="refs/heads/main"):
    """用 git ls-remote 查询远端仓库 url 中 ref 指向的 commit。
    The commit that ref points to in the remote repository at url, from git ls-remote.

    Raises:
        RuntimeError: 远端没有该 ref。
            The remote has no such ref.
        subprocess.CalledProcessError: git ls-remote 执行失败。
            git ls-remote failed.
    """
    result = subprocess.run(
        ["git", "ls-remote", url, ref], capture_output=True, text=True, check=True
    )
    if result.stdout:
        return result.stdout.split()[0]
    raise RuntimeError("Remote ref not found")


if __name__ == "__main__":
    url = "https://github.com/xrobot-org/libxr.git"
    ref = "refs/heads/master"
    commit = get_remote_commit(url, ref)
    out_path = os.path.join(os.path.dirname(__file__), "..", "src", "libxr", "libxr_version.py")
    out_path = os.path.abspath(out_path)
    with open(out_path, "w") as f:
        f.write(f"class LibXRInfo:\n    COMMIT = '{commit}'\n")
    logging.info(f"Wrote commit {commit} to {out_path}")
