"""主题感知的配色工具。

界面里凡是「次要文字 / 状态色」都统一走这里，不要再硬编码
``color: gray``、``#0f9d58`` 之类的固定值 —— 它们在深色主题下过暗、
浅色主题下过亮，是之前界面配色异常的根因。

用法::

    from ..theme import applyMuted, applyStatus

    applyMuted(self.hintLabel)          # 次要说明文字
    applyStatus(self.stateLabel, "ok")  # ok / warn / error / info

切换主题时会自动重刷所有注册过的控件，不需要每个页面自己接信号。
"""
from __future__ import annotations

import weakref

from PySide6.QtGui import QColor
from qfluentwidgets import isDarkTheme, themeColor

from .config import cfg

# 次要文字：深色下要够亮才看得清，浅色下要压暗才有层次
_MUTED = {"dark": "#9aa0a6", "light": "#5f6368"}

# 状态色：(深色, 浅色)
_STATUS = {
    "ok": ("#3ddc84", "#0f7b45"),
    "warn": ("#ffb900", "#9a6700"),
    "error": ("#ff6b6b", "#c42b1c"),
    "info": ("#33b5e5", "#0067b8"),
}

# 危险按钮（停止运行这类）：红色填充 + 白字，比状态色再压深一点才不刺眼
_DANGER = {"dark": "#d13438", "light": "#c42b1c"}

# 分隔线 / 细边框：深色下提亮、浅色下压暗，别再写死 rgba(128,128,128,60)
_SEPARATOR = {"dark": "rgba(255,255,255,30)", "light": "rgba(0,0,0,30)"}
_BORDER = {"dark": "rgba(255,255,255,60)", "light": "rgba(0,0,0,60)"}

# [(weakref, painter)] —— painter(widget) 负责把颜色刷到控件上
_registry: list = []
_connected = False


def mutedColor() -> str:
    """当前主题下的次要文字颜色。"""
    return _MUTED["dark" if isDarkTheme() else "light"]


def statusColor(kind: str) -> str:
    """状态色：ok=成功 warn=提醒 error=失败 info=进行中。"""
    dark, light = _STATUS.get(kind, _STATUS["info"])
    return dark if isDarkTheme() else light


def dangerColor() -> str:
    """危险按钮的底色（停止运行这类 destructive 操作）。"""
    return _DANGER["dark" if isDarkTheme() else "light"]


def dangerButtonSheet() -> str:
    """红色危险按钮的样式表（含 hover / 按下两态）。

    ⚠️ 坑点一：Fluent 按钮的外观整份来自 QSS，``setStyleSheet`` 是**整体替换**，
    只写默认态会把 hover / 按下的反馈一起弄丢 —— 按下去没有任何变化，看着
    像按钮坏了。所以三个状态这里一次写全。

    ⚠️ 坑点二：**padding 必须照抄 Fluent 的**（``5px 12px 6px 36px``）。按钮文字
    是按 QSS 的 padding 排的，而图标是 PushButton 自己画在左边 12px 处的、不吃
    padding —— 少了这行，文字会压在图标上，按钮还会矮一截窄一截。以前切到
    「停止变声」时按钮突然变小、字跟图标叠在一起，就是这个原因。
    """
    base = QColor(dangerColor())
    return (
        "QPushButton{background-color: %s; color: #ffffff;"
        " border: 1px solid %s; border-radius: 5px;"
        " padding: 5px 12px 6px 36px;}"
        "QPushButton:hover{background-color: %s;}"
        "QPushButton:pressed{background-color: %s;}"
        % (base.name(), base.darker(125).name(),
           base.lighter(112).name(), base.darker(140).name())
    )


def separatorColor() -> str:
    """当前主题下的分隔线颜色（1px 细线）。"""
    return _SEPARATOR["dark" if isDarkTheme() else "light"]


def borderColor() -> str:
    """当前主题下的细边框颜色（列表、输入框这类描边）。"""
    return _BORDER["dark" if isDarkTheme() else "light"]


def accentColor() -> str:
    """主题强调色（跟随用户在设置页选的 accent）。"""
    try:
        return themeColor().name()
    except Exception:
        return "#009faa"


def stylesheet(color: str, extra: str = "") -> str:
    return "color: %s;%s" % (color, extra or "")


# ------------------------------------------------------------------ 应用
def applyMuted(widget, extra: str = ""):
    """把控件设为次要文字色，并登记到主题刷新列表。"""
    return _register(widget, lambda w: w.setStyleSheet(stylesheet(mutedColor(), extra)))


def applyStatus(widget, kind: str):
    """把控件设为状态色（ok/warn/error/info），并登记到主题刷新列表。"""
    return _register(widget, lambda w: w.setStyleSheet(stylesheet(statusColor(kind))))


def setStatus(widget, kind: str):
    """只改颜色不改登记关系（用于同一控件反复切换状态）。"""
    widget.setStyleSheet(stylesheet(statusColor(kind)))


def setMuted(widget, extra: str = ""):
    widget.setStyleSheet(stylesheet(mutedColor(), extra))


def applySeparator(widget):
    """把控件画成一条随主题变化的 1px 分隔线。"""
    painter = lambda w: w.setStyleSheet(  # noqa: E731
        "QFrame{background: %s; max-height:1px; border:none;}" % separatorColor())
    return _register(widget, painter)


def applyBorder(widget, selector: str = "QWidget", radius: int = 6):
    """给列表/容器加一圈随主题变化的细边框（透明底 + 统一圆角）。"""
    painter = lambda w: w.setStyleSheet(  # noqa: E731
        "%s{background: transparent; border: 1px solid %s; border-radius: %dpx;}"
        % (selector, borderColor(), radius))
    return _register(widget, painter)


# ------------------------------------------------------------------ 刷新
def _register(widget, painter):
    global _connected
    painter(widget)
    _registry.append((weakref.ref(widget), painter))
    if not _connected:
        try:
            cfg.themeChanged.connect(lambda *_args: refresh())
        except Exception:  # 配置未就绪时也不影响界面构建
            pass
        _connected = True
    return widget


def refresh():
    """主题切换后重刷所有登记过的控件。"""
    alive = []
    for ref, painter in _registry:
        widget = ref()
        if widget is None:
            continue
        try:
            painter(widget)
        except RuntimeError:  # 控件已被 C++ 侧销毁
            continue
        alive.append((ref, painter))
    _registry[:] = alive
