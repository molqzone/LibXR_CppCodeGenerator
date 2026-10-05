"""CMake 脚本的顶层语句（libxr.cmake_text）。
The top-level statements of a CMake script (libxr.cmake_text).
"""

from fixtures import TestCase

from libxr import cmake_text


class Statements(TestCase):
    """语句的划分：命令、块、注释和方括号参数。
    The split into statements: commands, blocks, comments and bracket arguments.
    """

    def test_commands_and_blocks_are_split_at_the_top_level(self):
        text = (
            "set(A 1)\n"
            "# about B\n"
            "if(X)\n  set(B 2)\n  if(Y)\n    set(C 3)\n  endif()\nendif()\n"
            'message("a (b")\n'
            "foreach(i IN LISTS L)\n  message(${i})\nendforeach()\n"
        )
        statements = cmake_text.statements(text)
        self.assertEqual([s.name for s in statements], ["set", "if", "message", "foreach"])
        block = statements[1]
        self.assertEqual(
            text[block.leading : block.end],
            "# about B\nif(X)\n  set(B 2)\n  if(Y)\n    set(C 3)\n  endif()\nendif()",
        )
        self.assertEqual(statements[2].args, '"a (b"')

    def test_comments_and_brackets_hide_what_they_hold(self):
        text = (
            "# set(HIDDEN 1)\n"
            "#[[ set(HIDDEN 2)\n set(HIDDEN 3) ]]\n"
            "set(SHOWN [=[ ) set(HIDDEN 4) ]=])  # trailing )\n"
            "SET(second_one 1)\n"
        )
        statements = cmake_text.statements(text)
        self.assertEqual([s.name for s in statements], ["set", "set"])
        self.assertEqual(statements[0].args, "SHOWN [=[ ) set(HIDDEN 4) ]=]")

    def test_the_comment_directly_above_goes_with_the_statement(self):
        text = "set(A 1)\n\n# one\n# two\nset(B 2)\n\n# apart\n\nset(C 3)\n"
        a, b, c = cmake_text.statements(text)
        self.assertEqual(text[b.leading : b.end], "# one\n# two\nset(B 2)")
        self.assertEqual(text[c.leading : c.end], "set(C 3)")
