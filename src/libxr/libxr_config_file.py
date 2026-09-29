"""Read and rewrite libxr_config.yaml without losing user content.

The generator owns the values it computes, but the file also carries keys it
does not interpret (for example the ``generator`` pin that BSP CI installs)
and user comments. The text is parsed twice: PyYAML yields the settings with
the same scalar rules as before, and a ruamel.yaml round-trip document keeps
comments, key order and quoting for the rewrite. Values the generator did
not change keep their original nodes.
"""

import io

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark, YAMLError as RoundTripYAMLError
from ruamel.yaml.tokens import CommentToken


class LibXRConfigError(ValueError):
    """libxr_config.yaml cannot be used; the caller must not write it."""


def _loader() -> YAML:
    loader = YAML()
    # PyYAML implements YAML 1.1; resolve scalars (yes/no, octal) the same way.
    loader.version = (1, 1)
    loader.preserve_quotes = True
    return loader


def _dumper() -> YAML:
    dumper = YAML()
    dumper.preserve_quotes = True
    # Same block layout as yaml.dump(): sequences are not indented.
    dumper.indent(mapping=2, sequence=2, offset=0)
    return dumper


def new_document() -> CommentedMap:
    return CommentedMap()


def parse(text: str, origin: str):
    """Return (round-trip document, plain settings) of a libxr_config.yaml text."""
    try:
        settings = yaml.safe_load(text)
        document = _loader().load(text)
    except (yaml.YAMLError, RoundTripYAMLError) as error:
        raise LibXRConfigError(f"Cannot parse {origin}: {error}") from error
    if settings is None:
        return new_document(), {}
    if not isinstance(settings, dict) or not isinstance(document, CommentedMap):
        raise LibXRConfigError(f"{origin} must contain a YAML mapping at the top level")
    return document, settings


def read(path: str):
    """Return (round-trip document, plain settings) of a libxr_config.yaml file."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            text = stream.read()
    except (OSError, UnicodeDecodeError) as error:
        raise LibXRConfigError(f"Cannot read {path}: {error}") from error
    return parse(text, path)


def dump(document: CommentedMap) -> str:
    stream = io.StringIO()
    _dumper().dump(document, stream)
    return stream.getvalue()


def write(path: str, document: CommentedMap) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(dump(document))


def update(document: CommentedMap, values: dict) -> None:
    """Make ``document`` hold exactly ``values``.

    Unchanged values keep their nodes, so their comments and quoting stay.
    Comment lines that followed a removed or replaced value stay in place.
    """
    for key in [key for key in document if key not in values]:
        _delete(document, key)
    for key, value in values.items():
        if key not in document:
            document[key] = _to_node(value)
            continue
        _update_value(document, key, value)


def set_value(document: CommentedMap, key, value) -> None:
    """Set one top-level value, keeping the comments around it."""
    if key in document:
        _update_value(document, key, value)
    else:
        document[key] = _to_node(value)


def _update_value(container, key, value) -> None:
    current = container[key]
    if isinstance(value, dict) and isinstance(current, CommentedMap):
        update(current, value)
    elif (isinstance(value, (list, tuple)) and isinstance(current, CommentedSeq)
          and len(value) == len(current)):
        for index, item in enumerate(value):
            _update_value(current, index, item)
    elif not _same(current, value):
        following = _take_following(container, key)
        container[key] = _to_node(value)
        _append_following(container, key, following)


def _same(current, value) -> bool:
    if isinstance(current, dict) or isinstance(value, dict):
        return (isinstance(current, dict) and isinstance(value, dict)
                and list(current) == list(value)
                and all(_same(current[key], value[key]) for key in value))
    if isinstance(current, (list, tuple)) or isinstance(value, (list, tuple)):
        return (isinstance(current, (list, tuple)) and isinstance(value, (list, tuple))
                and len(current) == len(value)
                and all(_same(a, b) for a, b in zip(current, value)))
    if isinstance(current, bool) != isinstance(value, bool):
        return False
    if isinstance(current, str) != isinstance(value, str):
        return False
    return current == value


def _to_node(value):
    if isinstance(value, dict):
        node = CommentedMap()
        for key, item in value.items():
            node[key] = _to_node(item)
        return node
    if isinstance(value, (list, tuple)):
        return CommentedSeq(_to_node(item) for item in value)
    return value


def _last_key(node):
    return list(node.keys())[-1] if isinstance(node, CommentedMap) else len(node) - 1


def _trailing_slot(container, key):
    """Locate the comment token that follows ``container[key]``.

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
    """Detach the full comment lines after ``container[key]`` (not its own EOL comment)."""
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
    following = _take_following(container, key)
    keys = list(container.keys())
    index = keys.index(key)
    del container[key]
    if not following.strip():
        return
    if index > 0:
        _append_following(container, keys[index - 1], following)
    elif len(keys) > 1:
        lines = [line.lstrip()[1:].strip() if line.lstrip().startswith("#") else ""
                 for line in following.splitlines()]
        container.yaml_set_comment_before_after_key(keys[1], before="\n".join(lines))
