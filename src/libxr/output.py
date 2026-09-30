"""命令行工具的日志输出：格式为 [级别] 消息，级别名随输出语言变化。
Log output of the command-line tools: "[LEVEL] message", with level names in the output
language.

输出语言由 xr_syntax.i18n 决定（XR_LANG 等环境变量），与 xrobot 相同。
The output language comes from xr_syntax.i18n (XR_LANG and related variables), as for xrobot.
"""

from __future__ import annotations

import logging

from xr_syntax.i18n import chinese

_CHINESE_LEVELS = {
    logging.DEBUG: "调试",
    logging.INFO: "信息",
    logging.WARNING: "警告",
    logging.ERROR: "错误",
    logging.CRITICAL: "严重错误",
}


class _Formatter(logging.Formatter):
    """在消息前加上 [级别]；中文环境下级别名为中文。
    Prefix the message with [LEVEL]; level names are Chinese in a Chinese environment.
    """

    def format(self, record: logging.LogRecord) -> str:
        """格式化一条日志记录。
        Format one log record.
        """
        level = record.levelname
        if chinese():
            level = _CHINESE_LEVELS.get(record.levelno, level)
        return f"[{level}] {super().format(record)}"


def configure_logging(level: int = logging.INFO) -> None:
    """为命令行入口设置根日志：输出到标准错误，格式为 [级别] 消息。
    Set up the root logger for a command-line entry point: stderr, "[LEVEL] message".

    根日志已有处理器时（例如被其他程序导入后调用）只调整级别。
    When the root logger already has handlers (for example when another program imported the
    package), only the level is changed.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_Formatter("%(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
