"""按 LibXR 的 clang-format 风格（Google、列宽 90、Allman）排版生成的 C++ 语句。
Lay out generated C++ statements in the clang-format style of LibXR (Google, column limit 90,
Allman).

生成器不在运行时调用 clang-format（版本不同，输出就可能不同），而是在这里按 clang-format 的
规则自己断行：每个可以断开的位置有一个断行代价，取总代价最小的断行方式，代价相同时与
clang-format 一样先试不断行的状态。规则只覆盖生成器写出的语句形式：``头部(参数, ...);``，参数
是不可拆分的文本或花括号列表，花括号列表可以嵌套。测试用固定版本的 clang-format 核对输出。
The generator does not call clang-format at run time, because another version may format
differently. It breaks lines here by the rules of clang-format instead: every place where a
line may break has a break penalty, the cheapest set of breaks wins, and equal penalties try
the state that does not break first, as clang-format does. The rules cover only the statement
form the generator writes, ``head(argument, ...);``, whose arguments are unbreakable text or
braced lists; braced lists may nest. The tests check the output against a pinned
clang-format.
"""

import heapq
from collections.abc import Sequence
from dataclasses import dataclass, replace

COLUMN_LIMIT = 90
CONTINUATION_INDENT = 4
# 超出列宽的每个字符的代价；比任何断行都大，所以只有无处可断时才超出。
# The penalty of each character past the column limit; it exceeds any break, so a line goes
# past the limit only when nothing can break.
EXCESS_PENALTY = 1_000_000
# 嵌套每深一层，断行代价加 20。
# A break costs 20 more for each level of nesting.
NESTING_PENALTY = 20


class Braces:
    """一个花括号列表参数，元素是文本或另一个 Braces。
    A braced list argument whose items are text or another Braces.
    """

    def __init__(self, *items: "str | Braces") -> None:
        """记下列表的元素。
        Keep the items of the list.
        """
        self.items = items

    def text(self) -> str:
        """列表写在一行时的文本，例如 ``{a, b}``。
        The list written on one line, such as ``{a, b}``.
        """
        return "{" + ", ".join(_flat(item) for item in self.items) + "}"


def _flat(argument: "str | Braces") -> str:
    """参数写在一行时的文本。
    An argument written on one line.
    """
    return argument if isinstance(argument, str) else argument.text()


@dataclass(frozen=True)
class _Token:
    """排版用的一个词法单元。
    One token for the layout.

    kind 为 atom、comma、open、close、assign 或 semi；depth 是它外面的括号层数；items 是
    open 所开列表的元素个数，boxed 表示这个列表有元素是花括号列表。
    kind is atom, comma, open, close, assign or semi; depth is the number of brackets around
    it; items is the number of items of the list that an open opens, boxed says that some
    item of that list is a braced list, and last that the list is the last item of its level.
    """

    text: str
    kind: str
    depth: int
    items: int = 0
    boxed: bool = False
    last: bool = False


@dataclass(frozen=True)
class _Scope:
    """一层括号的排版状态：续行缩进、本层最近一行的起始列和断行标记。
    The layout state of one bracket level: the continuation indent, the start column of its
    latest line and the break flags.

    pending 为真表示这一层之内断过行，下一个参数必须换行；boxed_mode 对有元素是花括号列表的
    列表记录各元素是否都换行：0 未决定，1 都换行，2 都不换行；commas 是这一层已有的逗号数；
    late 表示这个列表接在同一行的其他参数之后开始，clang-format 不让这样的列表跨行。
    pending means a break inside this level has happened and the next argument must start a
    new line; boxed_mode records, for a list with a braced list among its items, whether every
    item starts a line: 0 undecided, 1 all, 2 none; commas is the number of commas of this
    level so far; late means the list starts after other arguments on its line, which
    clang-format does not let span lines.
    """

    indent: int
    last_space: int
    pending: bool = False
    boxed: bool = False
    boxed_mode: int = 0
    commas: int = 0
    late: bool = False


def _tokens(head: str, args: Sequence["str | Braces"]) -> list[_Token]:
    """语句 ``head(args);`` 的词法单元；head 中的 `` = `` 把它分成赋值号两侧。
    The tokens of the statement ``head(args);``; a `` = `` in head splits it around the
    assignment.
    """
    tokens: list[_Token] = []
    if " = " in head:
        target, callee = head.split(" = ", 1)
        tokens += [_Token(target, "atom", 0), _Token("=", "assign", 0), _Token(callee, "atom", 0)]
    else:
        tokens.append(_Token(head, "atom", 0))
    tokens.append(_Token("(", "open", 0, len(args)))

    def emit(items: Sequence["str | Braces"], depth: int) -> None:
        """追加一组以逗号分隔的元素。
        Append a comma-separated group of items.
        """
        for position, item in enumerate(items):
            if position:
                tokens.append(_Token(",", "comma", depth))
            if isinstance(item, str):
                tokens.append(_Token(item, "atom", depth))
                continue
            boxed = any(isinstance(inner, Braces) for inner in item.items)
            last = position == len(items) - 1
            tokens.append(_Token("{", "open", depth, len(item.items), boxed, last))
            emit(item.items, depth + 1)
            tokens.append(_Token("}", "close", depth + 1))

    emit(args, 1)
    tokens.append(_Token(")", "close", 1))
    tokens.append(_Token(";", "semi", 0))
    return tokens


def _space_before(previous: _Token, token: _Token) -> int:
    """token 与 previous 写在同一行时它们之间的空格数。
    The number of spaces between previous and token when they share a line.
    """
    if token.kind in ("comma", "close", "semi") or token.text == "(":
        return 0
    if previous.kind in ("comma", "assign") or token.kind == "assign":
        return 1
    return 0


def _break_penalty(tokens: list[_Token], index: int) -> int:
    """在 tokens[index] 之前断行的代价：嵌套层数的代价加上断行位置的代价。
    The penalty of a break before tokens[index]: the nesting penalty plus the penalty of the
    place of the break.
    """
    token, previous = tokens[index], tokens[index - 1]
    base = NESTING_PENALTY * (token.depth + 1)
    if previous.kind == "comma":
        return base + 1
    if previous.kind == "assign":
        return base + 2
    if previous.kind == "open":
        return base + 19
    return base + 3


def _can_break(tokens: list[_Token], index: int) -> bool:
    """tokens[index] 之前是否允许断行：逗号、圆括号和赋值号之后可以，花括号之后和收尾之前不可。
    Whether a break before tokens[index] is allowed: after a comma, an opening parenthesis or an
    assignment, but not after a brace or before a closer.
    """
    token, previous = tokens[index], tokens[index - 1]
    if token.kind in ("close", "semi", "comma"):
        return False
    if previous.kind == "open":
        return previous.text == "("
    return previous.kind in ("comma", "assign")


def _place(tokens: list[_Token], index: int, column: int, stack: tuple, newline: bool):
    """把 tokens[index] 放在当前行或新的一行；不允许时为 None。
    Put tokens[index] on the current line or on a new one; None when that is not allowed.

    返回 (增加的代价, 新的列, 新的括号层, 本词法单元起始的列或 None)。
    Returns (added penalty, new column, new bracket levels, the start column of the token when
    it starts a line, else None).
    """
    token, previous = tokens[index], tokens[index - 1]
    top = stack[-1]
    penalty = 0
    if newline:
        if not _can_break(tokens, index) or any(scope.late for scope in stack):
            return None
        # 有元素是花括号列表的列表，只要其中断过行，各元素就都换行。
        # A list with a braced list among its items breaks at every item as soon as a line
        # breaks inside it.
        if any(scope.boxed and scope.boxed_mode == 2 for scope in stack):
            return None
        stack = tuple(
            replace(scope, boxed_mode=1) if scope.boxed and scope.boxed_mode == 0 else scope
            for scope in stack
        )
        top = stack[-1]
        start = top.indent
        penalty += _break_penalty(tokens, index)
        outer = tuple(replace(scope, pending=True) for scope in stack[:-1])
        top = replace(top, pending=False, last_space=start)
        stack = outer + (top,)
        new_column = start + len(token.text)
        line_start = start
    else:
        # 内部断过行的一层，其下一个参数必须换行；选了各元素都换行的列表，每个元素必须换行。
        # After a break inside a level, its next argument must start a new line; a list that
        # chose to break at every item must break before each item.
        if previous.kind == "comma" and (top.pending or (top.boxed and top.boxed_mode == 1)):
            return None
        if top.boxed and previous.kind == "comma":
            top = replace(top, boxed_mode=2)
        spaces = _space_before(previous, token)
        position = column + spaces
        if previous.kind == "open":
            top = replace(top, indent=position, last_space=position)
        if token.kind == "comma":
            top = replace(top, commas=top.commas + 1)
        stack = stack[:-1] + (top,)
        new_column = position + len(token.text)
        line_start = None
    if new_column > COLUMN_LIMIT:
        penalty += EXCESS_PENALTY * (new_column - COLUMN_LIMIT)
    if token.kind == "open":
        parent = stack[-1]
        # 列表接在同一行的其他参数之后时，只有它是第二个也是最后一个参数才可以跨行。
        # A list that follows other arguments on its line may span lines only as the second and
        # last argument.
        late = previous.kind == "comma" and not newline and not (parent.commas == 1 and token.last)
        scope = _Scope(
            indent=parent.last_space + CONTINUATION_INDENT,
            last_space=parent.last_space,
            boxed=token.boxed,
            late=late,
        )
        stack = stack + (scope,)
    elif token.kind == "close":
        stack = stack[:-1]
    return penalty, new_column, stack, line_start


def layout(head: str, args: Sequence["str | Braces"], indent: int = 2) -> list[str]:
    """排版语句 ``head(args);``，返回缩进 indent 列的各行。
    Lay out the statement ``head(args);`` and return its lines indented by indent columns.

    取断行代价最小的排版，能写在一行时写在一行。head 是圆括号之前的全部文本，例如
    ``static STM32UART usart1``；它含 `` = `` 时，赋值号之后也可以断行。
    The layout with the cheapest breaks wins, and a statement that fits on one line stays on
    one line. head is all the text before the parenthesis, such as ``static STM32UART usart1``;
    when it holds `` = `` the line may also break after the assignment.
    """
    one_line = " " * indent + head + "(" + ", ".join(_flat(arg) for arg in args) + ");"
    if len(one_line) <= COLUMN_LIMIT:
        return [one_line]
    tokens = _tokens(head, args)
    start = _Scope(indent=indent + CONTINUATION_INDENT, last_space=indent)
    initial_column = indent + len(tokens[0].text)
    counter = 0
    # 队列项：(代价, 次序, 下一个词法单元, 列, 括号层, 各行的起始位置)。次序使代价相同时先展开
    # 先加入的状态，加入时先试不断行。
    # Queue entries: (penalty, sequence, next token, column, brackets, line starts). The
    # sequence makes equal penalties expand the state added first, and a state that does not
    # break is added first.
    queue = [(0, counter, 1, initial_column, (start,), ())]
    seen = set()
    while queue:
        penalty, _, index, column, stack, starts = heapq.heappop(queue)
        if index == len(tokens):
            return _render(tokens, indent, starts)
        key = (index, column, stack)
        if key in seen:
            continue
        seen.add(key)
        for newline in (False, True):
            placed = _place(tokens, index, column, stack, newline)
            if placed is None:
                continue
            added, new_column, new_stack, line_start = placed
            counter += 1
            new_starts = starts + ((index, line_start),) if newline else starts
            heapq.heappush(
                queue, (penalty + added, counter, index + 1, new_column, new_stack, new_starts)
            )
    raise AssertionError("no layout")


def _render(tokens: list[_Token], indent: int, starts: tuple) -> list[str]:
    """按各行的起始词法单元和缩进列拼出文本。
    Assemble the text from the start token and the indent column of each line.
    """
    breaks = dict(starts)
    lines: list[str] = []
    current = ""
    for index, token in enumerate(tokens):
        if index in breaks:
            lines.append(current)
            current = " " * breaks[index] + token.text
        elif index == 0:
            current = " " * indent + token.text
        else:
            current += " " * _space_before(tokens[index - 1], token) + token.text
    lines.append(current)
    return lines
