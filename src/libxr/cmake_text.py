"""把 CMake 脚本切成顶层语句，供 libxr stm32 cmake 迁移已有的 LibXR.CMake。
Split a CMake script into top-level statements, so that libxr stm32 cmake can migrate an
existing LibXR.CMake.

只做这件事需要的最少解析：注释（# 行注释和 #[[ ]] 括号注释）、引号和括号参数、嵌套的圆括号，
以及 if/foreach/while/function/macro 到各自结束命令的块。不展开变量，也不检查语法。
It parses only as much as that needs: comments (# line comments and #[[ ]] bracket comments),
quoted and bracket arguments, nested parentheses, and the blocks from if, foreach, while,
function and macro to their end commands. Variables are not expanded and the syntax is not
checked.
"""

import re
from dataclasses import dataclass

# 开启一个块的命令及其结束命令。
# The commands that open a block and the commands that end it.
_BLOCKS = {
    "if": "endif",
    "foreach": "endforeach",
    "while": "endwhile",
    "function": "endfunction",
    "macro": "endmacro",
    "block": "endblock",
}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BRACKET_OPEN = re.compile(r"\[(=*)\[")


@dataclass(frozen=True)
class Statement:
    """一条顶层语句：一个命令，或 if/foreach 等一直到结束命令的块。
    One top-level statement: a command, or a block such as if or foreach up to its end command.

    name 是第一个命令的名字（小写），args 是它括号中的文本；start 和 end 是语句在原文中的范围，
    不含行末的换行；leading 为语句紧上方连续的注释行起点，没有注释时为语句所在行的行首。
    name is the name of the first command, in lower case, and args the text in its parentheses;
    start and end are the range of the statement in the text, without the newline at its end;
    leading is the start of the comment lines directly above the statement, the start of the
    line of the statement without any.
    """

    name: str
    args: str
    start: int
    end: int
    leading: int


def _skip_comment(text: str, index: int) -> int:
    """index 处的 # 注释结束的位置：括号注释到对应的 ]]，行注释到行尾。
    The end of the # comment at index: a bracket comment ends at its closing ]], a line comment
    at the end of the line.
    """
    opener = _BRACKET_OPEN.match(text, index + 1)
    if opener:
        close = "]" + opener.group(1) + "]"
        found = text.find(close, opener.end())
        return len(text) if found < 0 else found + len(close)
    found = text.find("\n", index)
    return len(text) if found < 0 else found


def _skip_arguments(text: str, index: int) -> int:
    """index 处的 ( 对应的 ) 之后的位置；引号、括号参数和注释中的括号不计。
    The position after the ) that matches the ( at index; parentheses in quotes, bracket
    arguments and comments do not count.

    没有匹配的 ) 时为文本末尾。
    At the end of the text when no ) matches.
    """
    depth = 0
    position = index
    while position < len(text):
        char = text[position]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position + 1
        elif char == '"':
            position += 1
            while position < len(text) and text[position] != '"':
                position += 2 if text[position] == "\\" else 1
        elif char == "#":
            position = _skip_comment(text, position) - 1
        elif char == "[":
            opener = _BRACKET_OPEN.match(text, position)
            if opener:
                close = "]" + opener.group(1) + "]"
                found = text.find(close, opener.end())
                position = (len(text) if found < 0 else found + len(close)) - 1
        position += 1
    return len(text)


def commands(text: str) -> list[tuple[str, str, int, int]]:
    """文本中的全部命令（含块内的），依次为 (名字, 参数文本, 起点, 终点)；名字为小写。
    All commands of the text, those inside blocks included, as (name, argument text, start,
    end) in order; the name is in lower case.
    """
    found = []
    position = 0
    while position < len(text):
        char = text[position]
        if char == "#":
            position = _skip_comment(text, position)
            continue
        match = _NAME.match(text, position)
        if match is None:
            position += 1
            continue
        after = match.end()
        while after < len(text) and text[after] in " \t":
            after += 1
        if after < len(text) and text[after] == "(":
            end = _skip_arguments(text, after)
            found.append((match.group().lower(), text[after + 1 : end - 1], position, end))
            position = end
        else:
            position = match.end()
    return found


def statements(text: str) -> list[Statement]:
    """文本的顶层语句：命令，以及 if/foreach/while/function/macro/block 到结束命令的整块。
    The top-level statements of the text: commands, and whole blocks from if, foreach, while,
    function, macro or block to their end command.

    块中的命令不单独列出。leading 记下语句紧上方没有空行隔开的注释行，迁移时随语句一起保留。
    Commands inside a block are not listed on their own. leading records the comment lines
    directly above a statement with no blank line between, which move along with the statement
    when it is kept.
    """
    result = []
    depth = 0
    opener: tuple[str, str, int] | None = None
    for name, args, start, end in commands(text):
        if depth == 0:
            if name in _BLOCKS:
                opener = (name, args, start)
                depth = 1
                continue
            result.append(Statement(name, args, start, end, _leading(text, start)))
            continue
        if name in _BLOCKS:
            depth += 1
        elif name in _BLOCKS.values():
            depth -= 1
            if depth == 0 and opener is not None:
                result.append(
                    Statement(opener[0], opener[1], opener[2], end, _leading(text, opener[2]))
                )
                opener = None
    if opener is not None:
        result.append(
            Statement(opener[0], opener[1], opener[2], len(text), _leading(text, opener[2]))
        )
    return result


def _leading(text: str, start: int) -> int:
    """start 所在行紧上方连续的注释行的起点；上方不是注释行时为 start。
    The start of the comment lines directly above the line of start; start itself when the line
    above is not a comment.
    """
    line_start = text.rfind("\n", 0, start) + 1
    leading = line_start
    while leading > 0:
        previous = text.rfind("\n", 0, leading - 1) + 1
        if not text[previous : leading - 1].lstrip().startswith("#"):
            break
        leading = previous
    return leading if text[line_start:start].strip() == "" else start
