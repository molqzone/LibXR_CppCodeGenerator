"""libxr stm32 setup（libxr.config_cubemx_project）：写入 libxr_config.yaml 的终端设备，以及 LibXR
子模块的检出策略。
libxr stm32 setup (libxr.config_cubemx_project): the terminal device written to libxr_config.yaml,
and the checkout policy of the LibXR submodule.
"""

import contextlib
import importlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import GeneratorTestCase, TestCase

from libxr import config_cubemx_project as cubemx_cfg
from libxr import generator_code_stm32 as generator


class TerminalOption(TestCase):
    """--terminal 记录的 terminal_source 被生成器使用。
    The terminal_source that --terminal records is the one the generator uses.
    """

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.user = Path(self.temporary.name)
        self.path = self.user / "libxr_config.yaml"

    def effective_terminal(self):
        """生成器读取 libxr_config.yaml 后使用的 terminal_source。
        The terminal_source the generator uses after reading libxr_config.yaml.
        """
        importlib.reload(generator)
        generator.load_libxr_config(str(self.user), "")
        return generator.libxr_settings["terminal_source"]

    def test_terminal_is_recorded_for_a_new_project(self):
        cubemx_cfg.set_terminal_source(str(self.user), "usart1")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "terminal_source: usart1\n")
        self.assertEqual(self.effective_terminal(), "usart1")

    def test_terminal_replaces_the_configured_one_and_keeps_comments(self):
        self.path.write_text(
            "# pinned\ngenerator: 6.0.0\n# console\nterminal_source: usart1  # debug port\n"
            "SYSTEM: None\n",
            encoding="utf-8",
        )
        cubemx_cfg.set_terminal_source(str(self.user), "usb_fs_cdc")
        self.assertRegex(
            self.path.read_text(encoding="utf-8"),
            r"\A# pinned\ngenerator: 6\.0\.0\n# console\nterminal_source: usb_fs_cdc +# debug port\n"
            r"SYSTEM: None\n\Z",
        )
        self.assertEqual(self.effective_terminal(), "usb_fs_cdc")

    def test_unparsable_config_stops_without_writing(self):
        self.path.write_text("terminal_source: [\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cubemx_cfg.set_terminal_source(str(self.user), "usart1")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "terminal_source: [\n")


def git(*args, cwd=None):
    """以固定身份在 cwd 中运行 git，返回去掉首尾空白的标准输出。
    Run git in cwd with a fixed identity and return its stripped stdout.
    """
    identity = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
    result = subprocess.run(
        ["git", *identity, "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo, name, text):
    """把 text 写入 repo 中的 name 并提交，返回新的 commit。
    Write text to name in repo, commit it and return the new commit.
    """
    (repo / name).write_text(text, encoding="utf-8")
    git("add", name, cwd=repo)
    git("commit", "-q", "-m", text, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


class LibXRSubmodule(TestCase):
    """add_libxr 保留已有的 LibXR 检出，只在明确要求或目录为空时检出指定 commit。
    add_libxr keeps an existing LibXR checkout and checks out a commit only when asked or when
    the directory is empty.
    """

    @classmethod
    def setUpClass(cls):
        """创建代替 LibXR 的裸仓库：master 上依次是 old、default、newer，另有从 old 分出的
        divergent。
        Create a bare repository standing in for LibXR: old, default and newer in order on
        master, and divergent branched from old.
        """
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        source = cls.root / "libxr-source"
        source.mkdir()
        git("init", "-q", "-b", "master", cwd=source)
        cls.old = commit_file(source, "version.txt", "old")
        cls.default = commit_file(source, "version.txt", "default")
        cls.newer = commit_file(source, "version.txt", "newer")
        git("checkout", "-q", "-b", "divergent", cls.old, cwd=source)
        cls.divergent = commit_file(source, "divergent.txt", "divergent")
        cls.remote = cls.root / "libxr.git"
        git("clone", "-q", "--bare", str(source), str(cls.remote), cwd=cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        patcher = mock.patch.dict(os.environ, GIT_ALLOW_PROTOCOL="file")
        patcher.start()
        self.addCleanup(patcher.stop)

    def project(self, recorded, checked_out):
        """以 LibXR 为子模块的工程：gitlink 记录 recorded，检出停在 checked_out；返回
        (工程目录, 检出目录)。
        A project with LibXR as a submodule whose gitlink records recorded and whose checkout
        is left at checked_out; return (project directory, checkout directory).
        """
        project = Path(tempfile.mkdtemp(dir=self.tmp)) / "project"
        project.mkdir()
        git("init", "-q", "-b", "master", cwd=project)
        git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(self.remote),
            "Middlewares/Third_Party/LibXR",
            cwd=project,
        )
        checkout = project / "Middlewares" / "Third_Party" / "LibXR"
        git("checkout", "-q", recorded, cwd=checkout)
        git("add", ".gitmodules", "Middlewares/Third_Party/LibXR", cwd=project)
        git("commit", "-q", "-m", "add LibXR", cwd=project)
        git("checkout", "-q", checked_out, cwd=checkout)
        return project, checkout

    def add_libxr(self, project, **options):
        """以本生成器的默认 commit 为 default 运行 add_libxr，屏蔽它的标准输出。
        Run add_libxr with default as the generator's default commit, its stdout suppressed.
        """
        with contextlib.redirect_stdout(io.StringIO()):
            cubemx_cfg.add_libxr(project, default_libxr_commit=self.default, **options)

    def head(self, checkout):
        """检出的 HEAD。
        HEAD of the checkout.
        """
        return git("rev-parse", "HEAD", cwd=checkout)

    def test_existing_checkouts_stay_where_they_are(self):
        def warning(relation):
            return (
                f"WARNING:root:LibXR checkout {checked_out[:12]} is {relation} this generator's "
                f"default {self.default[:12]}; it was left unchanged. To switch, run "
                f"`libxr stm32 setup` with --commit {self.default} (or check out the commit in "
                "Middlewares/Third_Party/LibXR) and commit the gitlink."
            )

        for recorded, checked_out, relation in (
            (self.default, self.old, "older than"),
            (self.old, self.old, "older than"),
            (self.default, self.default, None),
            (self.default, self.newer, None),
            (self.default, self.divergent, "different from"),
        ):
            with self.subTest(recorded=recorded, checked_out=checked_out):
                project, checkout = self.project(recorded, checked_out)
                if relation is None:
                    with self.assertNoLogs(level="WARNING"):
                        self.add_libxr(project)
                else:
                    with self.assertLogs(level="WARNING") as logs:
                        self.add_libxr(project)
                    self.assertEqual(logs.output, [warning(relation)])
                self.assertEqual(self.head(checkout), checked_out)

    def test_local_changes_are_kept(self):
        project, checkout = self.project(self.old, self.old)
        (checkout / "version.txt").write_text("local changes", encoding="utf-8")
        self.add_libxr(project)
        self.assertEqual(self.head(checkout), self.old)
        self.assertEqual(git("status", "--porcelain", cwd=checkout), "M version.txt")

    def test_an_explicit_commit_moves_the_checkout(self):
        project, checkout = self.project(self.old, self.newer)
        self.add_libxr(project, libxr_commit=self.old)
        self.assertEqual(self.head(checkout), self.old)

    def test_an_empty_directory_is_initialized_to_its_gitlink(self):
        project, checkout = self.project(self.old, self.old)
        git("submodule", "deinit", "-q", "-f", "--", "Middlewares/Third_Party/LibXR", cwd=project)
        self.assertEqual(list(checkout.iterdir()), [])
        self.add_libxr(project)
        self.assertEqual(self.head(checkout), self.old)

    def test_a_directory_with_user_files_is_refused_untouched(self):
        project, checkout = self.project(self.old, self.old)
        git("submodule", "deinit", "-q", "-f", "--", "Middlewares/Third_Party/LibXR", cwd=project)
        (checkout / "user_sources.cpp").write_text("keep", encoding="utf-8")
        with self.assertLogs(level="ERROR"), self.assertRaises(SystemExit) as exit:
            self.add_libxr(project)
        self.assertEqual(exit.exception.code, 1)
        self.assertEqual([p.name for p in checkout.iterdir()], ["user_sources.cpp"])

    def test_an_existing_clone_is_adopted(self):
        project = self.tmp / "project"
        project.mkdir()
        git("init", "-q", "-b", "master", cwd=project)
        checkout = project / "Middlewares" / "Third_Party" / "LibXR"
        git("clone", "-q", str(self.remote), str(checkout))
        git("checkout", "-q", self.newer, cwd=checkout)
        self.add_libxr(project, source=cubemx_cfg.LibXRSource(str(self.remote)))
        self.assertEqual(self.head(checkout), self.newer)

    def test_a_new_submodule_records_github_and_stages_the_default(self):
        project = self.tmp / "project"
        project.mkdir()
        git("init", "-q", "-b", "master", cwd=project)
        self.add_libxr(project, source=cubemx_cfg.LibXRSource(str(self.remote)))
        checkout = project / "Middlewares" / "Third_Party" / "LibXR"
        self.assertEqual(
            git(
                "config",
                "-f",
                ".gitmodules",
                f"submodule.{cubemx_cfg.SUBMODULE_PATH}.url",
                cwd=project,
            ),
            cubemx_cfg.LIBXR_URL,
        )
        self.assertEqual(self.head(checkout), self.default)
        staged = git("ls-files", "-s", "--", cubemx_cfg.SUBMODULE_PATH, cwd=project).split()[1]
        self.assertEqual(staged, self.default)

    def test_an_existing_checkout_needs_no_source(self):
        project, checkout = self.project(self.default, self.default)
        with mock.patch.object(cubemx_cfg, "pick_git_base", side_effect=AssertionError):
            self.add_libxr(project)
        self.assertEqual(self.head(checkout), self.default)

    def test_a_local_source_is_cloned_without_a_global_file_permission(self):
        # git 2.38 起子模块默认不能从本地路径克隆；这里不设 GIT_ALLOW_PROTOCOL。
        # Since git 2.38 a submodule cannot be cloned from a local path by default; no
        # GIT_ALLOW_PROTOCOL is set here.
        project = self.tmp / "project"
        project.mkdir()
        git("init", "-q", "-b", "master", cwd=project)
        with mock.patch.dict(os.environ):
            del os.environ["GIT_ALLOW_PROTOCOL"]
            self.add_libxr(project, source=cubemx_cfg.LibXRSource(str(self.remote)))
        checkout = project / "Middlewares" / "Third_Party" / "LibXR"
        self.assertEqual(self.head(checkout), self.default)
        self.assertEqual(
            cubemx_cfg.LibXRSource(str(self.remote)).config_for(cubemx_cfg.LIBXR_URL),
            [
                "-c",
                f"url.{self.remote}.insteadOf={cubemx_cfg.LIBXR_URL}",
                "-c",
                "protocol.file.allow=always",
            ],
        )

    def test_a_mirror_only_stands_in_for_the_github_url(self):
        source = cubemx_cfg.LibXRSource("https://gitee.com/jiu-xiao/libxr")
        self.assertEqual(
            source.config_for("https://github.com/Jiu-Xiao/libxr"),
            [
                "-c",
                "url.https://gitee.com/jiu-xiao/libxr.insteadOf=https://github.com/Jiu-Xiao/libxr",
            ],
        )
        self.assertEqual(source.config_for("https://github.com/someone/libxr-fork.git"), [])


class SetupProject(GeneratorTestCase):
    """setup_project：改动之前先检查工程，XRobot 模式默认沿用工程现在的选择，没有 git 时报错。
    setup_project: the project is checked before anything changes, the XRobot mode keeps the
    project's choice by default, and a missing git is an error.
    """

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "Core").mkdir()
        (self.root / "User").mkdir()
        (self.root / "demo.ioc").write_text("", encoding="utf-8")
        (self.root / "CMakeLists.txt").write_text("", encoding="utf-8")

    def test_the_project_is_checked_before_anything_changes(self):
        cases = (
            (
                "missing Core",
                lambda root: shutil.rmtree(root / "Core"),
                "{} is not a valid STM32CubeMX project: missing Core/ directory",
            ),
            ("no .ioc", lambda root: (root / "demo.ioc").unlink(), "{} holds no .ioc file"),
            (
                "two .ioc",
                lambda root: (root / "other.ioc").write_text("", encoding="utf-8"),
                "{} holds several .ioc files (demo.ioc, other.ioc); a directory holds one CubeMX "
                "project",
            ),
            (
                "not CMake",
                lambda root: (root / "CMakeLists.txt").unlink(),
                "{} has no CMakeLists.txt; set Toolchain / IDE to CMake in the Project Manager of "
                "STM32CubeMX and generate the project again",
            ),
        )
        for case, prepare, message in cases:
            with self.subTest(case=case):
                self.setUp()
                prepare(self.root)
                before = sorted(path.name for path in self.root.iterdir())
                with (
                    mock.patch.object(cubemx_cfg, "add_libxr") as add_libxr,
                    self.assertLogs(level="ERROR") as logs,
                    self.assertRaises(SystemExit) as exit,
                ):
                    cubemx_cfg.setup_project(str(self.root))
                self.assertEqual(exit.exception.code, 1)
                self.assertEqual(logs.output, ["ERROR:root:" + message.format(self.root.name)])
                add_libxr.assert_not_called()
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), before)

    def xrobot_mode(self, existing, option):
        """在 app_main 由 existing 模式生成的工程上运行 setup_project（选项为 option），返回生成时
        的 XRobot 模式。
        Run setup_project with option on a project whose app_main was generated in the existing
        mode; return the XRobot mode it generates with.
        """
        code = self.generate(use_xrobot=existing)
        (self.root / "User" / "app_main.cpp").write_text(code, encoding="utf-8")
        with (
            mock.patch.object(cubemx_cfg, "add_libxr"),
            mock.patch("libxr.peripheral_analyzer_stm32.parse_project"),
            mock.patch("libxr.generator_code_stm32.generate") as generate,
            mock.patch("libxr.generator_stm32_cmake.integrate"),
        ):
            cubemx_cfg.setup_project(str(self.root), xrobot_enable=option)
        return generate.call_args.args[2]

    def test_the_xrobot_mode_follows_the_project_unless_given(self):
        for existing, option, mode in (
            (True, None, True),
            (False, None, False),
            (True, False, False),
            (False, True, True),
        ):
            with self.subTest(existing=existing, option=option):
                self.assertIs(self.xrobot_mode(existing, option), mode)

    def test_a_missing_git_is_an_error(self):
        with (
            mock.patch("shutil.which", return_value=None),
            self.assertLogs(level="ERROR") as logs,
            self.assertRaises(SystemExit) as exit,
        ):
            cubemx_cfg.setup_project(str(self.root))
        self.assertEqual(exit.exception.code, 1)
        self.assertEqual(
            logs.output,
            [
                "ERROR:root:git was not found on PATH; LibXR is added to the project as a Git "
                "submodule"
            ],
        )


if __name__ == "__main__":
    unittest.main()
