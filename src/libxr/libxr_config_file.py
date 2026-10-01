"""读取并改写 libxr_config.yaml，不丢失用户内容。
Read and rewrite libxr_config.yaml without losing user content.

生成器负责它计算的值，但文件中还有它不解释的键（例如 BSP CI 安装的 ``generator`` 版本固定项）
和用户注释。文本被解析两次：PyYAML 按以往的标量规则得到设置，ruamel.yaml 的往返文档为改写保留
注释、键顺序和引号。生成器没有改变的值保留原节点。读写都按 PyYAML 实现的 YAML 1.1 规则，写出的
文件再读回时得到相同的值。
The generator owns the values it computes, but the file also carries keys it
does not interpret (for example the ``generator`` pin that BSP CI installs)
and user comments. The text is parsed twice: PyYAML yields the settings with
the same scalar rules as before, and a ruamel.yaml round-trip document keeps
comments, key order and quoting for the rewrite. Values the generator did
not change keep their original nodes. Reading and writing both follow the
YAML 1.1 rules that PyYAML implements, so a written file reads back as the
same values.
"""

import io

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.error import YAMLError as RoundTripYAMLError
from ruamel.yaml.tokens import CommentToken
from xr_syntax.i18n import tr


class LibXRConfigError(ValueError):
    """libxr_config.yaml 无法使用；调用方不得写入该文件。
    libxr_config.yaml cannot be used; the caller must not write it.
    """


def _loader() -> YAML:
    """读取用的 ruamel.yaml 往返加载器：按 YAML 1.1 解析标量（与 PyYAML 一致），保留引号。
    The ruamel.yaml round-trip loader for reading: scalars resolve as in YAML 1.1, like PyYAML,
    and quoting is kept.
    """
    loader = YAML()
    # PyYAML 实现的是 YAML 1.1；标量（yes/no、八进制数）按同样的规则解析。
    # PyYAML implements YAML 1.1; resolve scalars (yes/no, octal) the same way.
    loader.version = (1, 1)
    loader.preserve_quotes = True
    return loader


def _dumper() -> YAML:
    """写出用的 ruamel.yaml 往返输出器：按 YAML 1.1 写出标量，保留引号，块布局与 yaml.dump()
    相同（序列不缩进）。
    The ruamel.yaml round-trip dumper for writing: scalars are written for YAML 1.1, quoting is
    kept and the block layout matches yaml.dump(), with sequences not indented.
    """
    dumper = YAML()
    # 与读取相同的 YAML 1.1：八进制写成 010，on/off/yes/no 等字符串加引号。
    # The same YAML 1.1 as for reading: octal is written as 010, and strings such as
    # on/off/yes/no are quoted.
    dumper.version = (1, 1)
    dumper.preserve_quotes = True
    # 块布局与 yaml.dump() 相同：序列不缩进。
    # Same block layout as yaml.dump(): sequences are not indented.
    dumper.indent(mapping=2, sequence=2, offset=0)
    return dumper


def new_document() -> CommentedMap:
    """新的空往返文档，顶层为映射。
    A new, empty round-trip document with a mapping at the top level.
    """
    return CommentedMap()


def parse(text: str, origin: str):
    """解析 libxr_config.yaml 文本，返回 (往返文档, 普通设置)。
    Parse a libxr_config.yaml text and return (round-trip document, plain settings).

    文本为空或只含注释时得到空设置；注释作为文档开头的注释保留。
    An empty or comment-only text gives empty settings; its comments are kept as the comment at
    the start of the document.

    Args:
        origin: 错误信息中使用的来源名（路径或 URL）。
            The source named in error messages (a path or URL).

    Raises:
        LibXRConfigError: 文本不是合法的 YAML，或顶层不是映射。
            The text is not valid YAML, or its top level is not a mapping.
    """
    try:
        settings = yaml.safe_load(text)
        document = _loader().load(text)
    except (yaml.YAMLError, RoundTripYAMLError) as error:
        raise LibXRConfigError(
            tr(f"Cannot parse {origin}: {error}", f"无法解析 {origin}：{error}")
        ) from error
    if settings is None:
        return _comment_document(text), {}
    if not isinstance(settings, dict) or not isinstance(document, CommentedMap):
        raise LibXRConfigError(
            tr(
                f"{origin} must contain a YAML mapping at the top level",
                f"{origin} 的顶层必须是 YAML 映射",
            )
        )
    return document, settings


def _comment_document(text: str) -> CommentedMap:
    """只含注释的文本对应的空文档：注释行（去掉 "# "）和空行成为文档开头的注释。
    The empty document of a comment-only text: its comment lines, without "# ", and blank lines
    become the comment at the start of the document.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            body = stripped[1:]
            lines.append(body[1:] if body.startswith(" ") else body)
        elif not stripped:
            lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    document = new_document()
    if lines:
        document.yaml_set_start_comment("\n".join(lines))
    return document


def read(path: str):
    """以 UTF-8 读取 libxr_config.yaml 文件，返回 (往返文档, 普通设置)。
    Read a libxr_config.yaml file as UTF-8 and return (round-trip document, plain settings).

    Raises:
        LibXRConfigError: 文件无法读取或解码，或 parse() 拒绝其内容。
            The file cannot be read or decoded, or parse() rejects its content.
    """
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    except (OSError, UnicodeDecodeError) as error:
        raise LibXRConfigError(
            tr(f"Cannot read {path}: {error}", f"无法读取 {path}：{error}")
        ) from error
    return parse(text, path)


def dump(document: CommentedMap) -> str:
    """按 _dumper() 的布局把往返文档输出为 YAML 文本，不带 %YAML 版本指令。
    Render a round-trip document as YAML text in the layout of _dumper(), without the %YAML
    version directive.
    """
    stream = io.StringIO()
    _dumper().dump(document, stream)
    text = stream.getvalue()
    # 文件中不写版本指令：PyYAML 本来就按 YAML 1.1 读取。
    # The file carries no version directive: PyYAML reads YAML 1.1 anyway.
    for directive in ("%YAML 1.1\n---\n", "%YAML 1.1\n--- "):
        if text.startswith(directive):
            return text[len(directive) :]
    return text


def write(path: str, document: CommentedMap) -> None:
    """把往返文档以 UTF-8 和 LF 换行写入 path，替换原有内容。
    Write a round-trip document to path as UTF-8 with LF line endings, replacing its content.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(dump(document))


def update(document: CommentedMap, values: dict) -> None:
    """使 ``document`` 恰好包含 ``values``。
    Make ``document`` hold exactly ``values``.

    未变化的值保留原节点，因此其注释和引号不变；新键追加在末尾。被删除或替换的值之后的整行注释
    留在原处。
    Unchanged values keep their nodes, so their comments and quoting stay; new keys are
    appended at the end. Comment lines that followed a removed or replaced value stay in place.
    """
    for key in [key for key in document if key not in values]:
        _delete(document, key)
    for key, value in values.items():
        if key not in document:
            document[key] = _to_node(value)
            continue
        _update_value(document, key, value)


def set_value(document: CommentedMap, key, value) -> None:
    """设置一个顶层值，保留其周围的注释；新键追加在末尾，其他键不变。
    Set one top-level value, keeping the comments around it; a new key is appended at the end
    and the other keys stay as they are.
    """
    if key in document:
        _update_value(document, key, value)
    else:
        document[key] = _to_node(value)


def _update_value(container, key, value) -> None:
    """把 ``container[key]`` 更新为 value，尽量保留原节点。
    Update ``container[key]`` to value, keeping the existing nodes where possible.

    映射按 update() 逐键更新，长度相同的序列逐项更新；其他不相等的值换成新节点，原值之后的整行
    注释移到新节点之后。
    Mappings are updated key by key as in update(), and sequences of the same length item by
    item; any other unequal value gets a new node, and the comment lines that followed the old
    value move after the new one.
    """
    current = container[key]
    if isinstance(value, dict) and isinstance(current, CommentedMap):
        update(current, value)
    elif (
        isinstance(value, (list, tuple))
        and isinstance(current, CommentedSeq)
        and len(value) == len(current)
    ):
        for index, item in enumerate(value):
            _update_value(current, index, item)
    elif not _same(current, value):
        following = _take_following(container, key)
        container[key] = _to_node(value)
        _append_following(container, key, following)


def _same(current, value) -> bool:
    """按类型严格比较两个值：映射的键顺序也须相同，bool 与其他值、字符串与非字符串视为不同。
    Compare two values strictly by type: mappings must also have the same key order, and a bool
    differs from any non-bool, a string from any non-string.
    """
    if isinstance(current, dict) or isinstance(value, dict):
        return (
            isinstance(current, dict)
            and isinstance(value, dict)
            and list(current) == list(value)
            and all(_same(current[key], value[key]) for key in value)
        )
    if isinstance(current, (list, tuple)) or isinstance(value, (list, tuple)):
        return (
            isinstance(current, (list, tuple))
            and isinstance(value, (list, tuple))
            and len(current) == len(value)
            and all(_same(a, b) for a, b in zip(current, value, strict=True))
        )
    if isinstance(current, bool) != isinstance(value, bool):
        return False
    if isinstance(current, str) != isinstance(value, str):
        return False
    return current == value


def _to_node(value):
    """把普通的 dict、list 和 tuple 递归转换为 ruamel.yaml 的 CommentedMap 和
    CommentedSeq；标量原样返回。
    Convert plain dicts, lists and tuples recursively into ruamel.yaml CommentedMap and
    CommentedSeq nodes; scalars are returned as they are.
    """
    if isinstance(value, dict):
        node = CommentedMap()
        for key, item in value.items():
            node[key] = _to_node(item)
        return node
    if isinstance(value, (list, tuple)):
        return CommentedSeq(_to_node(item) for item in value)
    return value


def _last_key(node):
    """映射的最后一个键，或序列的最后一个下标。
    The last key of a mapping, or the last index of a sequence.
    """
    return list(node.keys())[-1] if isinstance(node, CommentedMap) else len(node) - 1


def _trailing_slot(container, key):
    """查找 ``container[key]`` 之后的注释 token。
    Locate the comment token that follows ``container[key]``.

    ruamel.yaml 把一个值之后的注释行存放在该值最深层的最后一项上。返回 token 的
    (entry, position)，没有时为 None；以及这类 token 所属的最深一项 (node, key)。
    ruamel.yaml stores the comment lines after a value on the deepest last
    item of that value. Returns (entry, position) of the token, or None, and
    the deepest (node, key) where such a token belongs.
    """
    slot = None
    node, item = container, key
    while True:
        position = 2 if isinstance(node, CommentedMap) else 0
        entry = node.ca.items.get(item)
        if entry is not None and entry[position] is not None:
            slot = (entry, position)
        child = node[item]
        if isinstance(child, (CommentedMap, CommentedSeq)) and len(child):
            node, item = child, _last_key(child)
            continue
        return slot, node, item


def _take_following(container, key) -> str:
    """摘下 ``container[key]`` 之后的整行注释并返回其文本；该值自身的行尾注释保留。
    Detach the full comment lines after ``container[key]`` and return their text; the value's
    own end-of-line comment stays.
    """
    slot, _, _ = _trailing_slot(container, key)
    if slot is None:
        return ""
    entry, position = slot
    token = entry[position]
    head, separator, following = token.value.partition("\n")
    if head:
        token.value = head + separator
    else:
        entry[position] = None
    return following


def _append_following(container, key, following: str) -> None:
    """把整行注释文本接到 ``container[key]`` 之后；只含空白的文本被忽略。
    Attach comment-line text after ``container[key]``; whitespace-only text is ignored.

    已有尾随注释 token 时追加到它后面，否则在该值最深层的最后一项上新建 token。
    The text is appended to an existing trailing comment token, or a new token is created on
    the deepest last item of the value.
    """
    if not following.strip():
        return
    slot, node, item = _trailing_slot(container, key)
    if slot is not None:
        entry, position = slot
        entry[position].value += following
        return
    position = 2 if isinstance(node, CommentedMap) else 0
    entry = node.ca.items.setdefault(item, [None, None, None, None])
    entry[position] = CommentToken("\n" + following, CommentMark(0), None)


def _delete(container, key) -> None:
    """删除 ``container[key]``，其后的整行注释留在原位置。
    Delete ``container[key]`` and keep the comment lines that followed it in place.

    注释接到前一个键之后；删除的是第一个键时，注释改为下一个键之前的注释。删除唯一的键时这些注释
    随之丢弃。
    The comments are attached after the previous key; when the first key is deleted they become
    the comment before the next key. Deleting the only key drops them.
    """
    following = _take_following(container, key)
    keys = list(container.keys())
    index = keys.index(key)
    del container[key]
    if not following.strip():
        return
    if index > 0:
        _append_following(container, keys[index - 1], following)
    elif len(keys) > 1:
        lines = [
            line.lstrip()[1:].strip() if line.lstrip().startswith("#") else ""
            for line in following.splitlines()
        ]
        container.yaml_set_comment_before_after_key(keys[1], before="\n".join(lines))
