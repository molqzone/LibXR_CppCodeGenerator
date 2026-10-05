"""scripts/gen_libxr_version.py：写出 libxr stm32 setup 默认使用的 LibXR 提交。
scripts/gen_libxr_version.py: writing the LibXR commit that libxr stm32 setup uses by default.
"""

import ast
import importlib.util
import runpy
import tempfile
import unittest
from pathlib import Path

from fixtures import TestCase
from test_docstrings import REPOSITORY, bilingual, definitions


def load_script():
    """以模块形式加载 scripts/gen_libxr_version.py（它不在包里）。
    Load scripts/gen_libxr_version.py as a module; it is not part of the package.
    """
    spec = importlib.util.spec_from_file_location(
        "gen_libxr_version", REPOSITORY / "scripts" / "gen_libxr_version.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WriteVersion(TestCase):
    """生成的 libxr_version.py 会放进 src/libxr，要满足包里每个模块的要求。
    The generated libxr_version.py goes into src/libxr and must meet what every module of the
    package does.
    """

    def test_the_file_holds_the_commit_with_bilingual_docstrings(self):
        # CI 先把这个文件放进 src/libxr 再跑测试；以前它没有 docstring，docstring 测试失败。
        # The CI puts this file into src/libxr before the tests; it used to have no docstrings,
        # and the docstring test failed.
        commit = "0123456789abcdef0123456789abcdef01234567"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "libxr_version.py"
            load_script().write_version(commit, path)
            self.assertEqual(runpy.run_path(str(path))["LibXRInfo"].COMMIT, commit)
            tree = ast.parse(path.read_text(encoding="utf-8"))
        self.assertEqual(
            [
                name
                for node, name, _ in definitions(tree, tests=False)
                if not bilingual(ast.get_docstring(node) or "")
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
