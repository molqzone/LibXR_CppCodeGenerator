"""命令行工具的输出：标准输出和标准错误用 UTF-8，日志格式为 [级别] 消息，级别名随输出语言变化。
Output of the command-line tools: UTF-8 stdout and stderr, and "[LEVEL] message" logs with
level names in the output language.

输出语言由 xr_syntax.i18n 决定（XR_LANG 等环境变量），与 xrobot 相同。
The output language comes from xr_syntax.i18n (XR_LANG and related variables), as for xrobot.
"""

from __future__ import annotations

import logging
import sys

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


def configure_output(level: int = logging.INFO) -> None:
    """为命令行入口设置输出：标准输出和标准错误改用 UTF-8，根日志写到标准错误，格式为 [级别] 消息。
    Set up the output of a command-line entry point: stdout and stderr become UTF-8, and the
    root logger writes "[LEVEL] message" to stderr.

    重定向或经管道输出时，Windows 默认按系统代码页（中文系统为 GBK）编码；与 xrobot 一样固定为
    UTF-8。根日志已有处理器时（例如被其他程序导入后调用）只调整级别。
    Redirected or piped output would use the Windows code page (GBK on Chinese systems);
    like xrobot, it is UTF-8 instead. When the root logger already has handlers (for example
    when another program imported the package), only the level is changed.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_Formatter("%(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
