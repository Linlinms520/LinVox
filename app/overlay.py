"""桌面叠层：把「算法延迟 / 推理时间」显示在别的窗口（游戏）上面。

打游戏的时候主窗口早缩到后面去了 —— 页上那两个数字再准也没人看得到。
所以这里另开一个**置顶 + 点穿**的小窗：不抢焦点、不挡鼠标，就浮在游戏画面
上显示两个数字（跟常见的 FPS 叠层一个思路）。

⚠️ 有物理上限：真正的**独占全屏**（exclusive fullscreen）游戏独占显示输出，
普通窗口贴不上去；无边框 / 窗口化全屏没有问题。

外观（底板透明度 / 文字颜色 / 贴哪个角）在 :class:`OverlaySettingDialog` 里改，
三个值都存在配置里，改完立刻作用到已经开着的叠层上。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPainter
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from qfluentwidgets import (
    ColorSettingCard,
    ComboBoxSettingCard,
    Dialog,
    FluentIcon as FIF,
    RangeSettingCard,
)

from .config import OVERLAY_CORNER_TEXTS, cfg

MARGIN = 12          # 离屏幕边缘的距离
VALUE_WIDTH = 58     # 数值列固定宽：从 3 位数跳到 4 位数时叠层不会跟着抖
PLACEHOLDER = "—"    # 没在跑的时候显示这个，别摆一个假的 0
RADIUS = 6           # 底板圆角

# 这两个默认值要和配置里 rtcOverlayAlpha / rtcOverlayColor 的默认值对上：
# 叠层刚建出来、还没被推过样式时先按这个画
DEFAULT_ALPHA_PERCENT = 60
DEFAULT_TEXT_COLOR = "#ffffff"

# 贴角 → (横向贴右?, 纵向贴下?)。键要和 config.OVERLAY_CORNERS 对得上
CORNER_SIDES = {
    "topLeft": (False, False),
    "topRight": (True, False),
    "bottomLeft": (False, True),
    "bottomRight": (True, True),
}


def _metricText(value) -> str:
    """没在跑 / 没上报就显示「—」：摆个 0 会让人以为"快得不可思议"。"""
    return PLACEHOLDER if not value else "%d ms" % int(value)


class MetricOverlay(QWidget):
    """置顶小窗：算法延迟 + 推理时间，两行数字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LinVox 叠层")
        # Frameless：不要标题栏；Tool：不进任务栏（不然和主窗口挤成一堆）；
        # StaysOnTop：才压得住别的窗口
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        # 点穿：鼠标直接穿过去，不挡游戏里的操作
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        # 弹出来的时候别抢焦点（抢了游戏会失去输入，甚至被切回桌面）
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # 底板是自己画的半透明圆角，窗口本身得透明
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._alpha = _alphaFromPercent(DEFAULT_ALPHA_PERCENT)
        self._color = QColor(DEFAULT_TEXT_COLOR)
        self._corner = "topLeft"
        self._labels = []

        self._values = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        # 等宽字体：数字一秒一变，非等宽会让整块跟着左右抖
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        for row, title in enumerate(("算法延迟", "推理时间")):
            name = QLabel(title, self)
            value = QLabel(PLACEHOLDER, self)
            value.setFixedWidth(VALUE_WIDTH)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setFont(mono)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._values[title] = value
            self._labels.append(name)
            self._labels.append(value)

        self._applyColor()
        self.setFixedSize(self.sizeHint())
        self._place()

    # ------------------------------------------------------------ 对外
    def setValues(self, delay_ms: int, infer_ms: int):
        self._values["算法延迟"].setText(_metricText(delay_ms))
        self._values["推理时间"].setText(_metricText(infer_ms))

    def valueText(self, title: str) -> str:
        """给测试 / 排查用：某一行现在显示的字符串。"""
        return self._values[title].text()

    def setStyle(self, alphaPercent, color):
        """底板不透明度（百分数）+ 文字颜色，两个都来自配置。"""
        self._alpha = _alphaFromPercent(alphaPercent)
        picked = QColor(color)
        self._color = picked if picked.isValid() else QColor(DEFAULT_TEXT_COLOR)
        self._applyColor()
        self.update()      # 底板透明度变了，重画

    def setCorner(self, corner):
        """贴屏幕的哪个角（键见 :data:`CORNER_SIDES`）。"""
        self._corner = corner if corner in CORNER_SIDES else "topLeft"
        self._place()

    # ------------------------------------------------------------ 内部
    def _applyColor(self):
        # 文字颜色直接写在样式表里；底板是自己画的，不在这管
        sheet = "color: %s; background: transparent;" % self._color.name()
        for label in self._labels:
            label.setStyleSheet(sheet)

    def _place(self):
        """按贴角摆到主屏上（和常见 FPS 叠层一个位置）。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        toRight, toBottom = CORNER_SIDES[self._corner]
        self.move(
            geo.right() - self.width() - MARGIN if toRight else geo.left() + MARGIN,
            geo.bottom() - self.height() - MARGIN if toBottom else geo.top() + MARGIN,
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, self._alpha))
        painter.drawRoundedRect(self.rect(), RADIUS, RADIUS)


def _alphaFromPercent(percent) -> int:
    """配置里存 0~100 的百分数，Qt 的 alpha 是 0~255。"""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = DEFAULT_ALPHA_PERCENT
    return max(0, min(255, round(value * 255 / 100)))


class OverlaySettingDialog(Dialog):
    """叠层外观：底板透明度 / 文字颜色 / 贴哪个角。

    三张卡片直接绑配置（``qconfig`` 就是应用配置），改完当场写进 config.json；
    叠层那边由实时变声页监听同一批配置项实时跟着变 —— 所以这里不用管"应用"，
    也不用管"取消还原"（拖滑块就是在预览）。
    """

    def __init__(self, parent=None):
        super().__init__(
            "叠层设置", "改完立刻生效，和「桌面叠层」开关一样记在配置文件里",
            parent,
        )
        # 正文标题在 textLayout 里还有一份（Ui_MessageBox），留窗口标题那一份就够
        self.titleLabel.hide()

        self.alphaCard = RangeSettingCard(
            cfg.rtcOverlayAlpha, FIF.TRANSPARENT, "底板透明度",
            "0 = 完全透明（只剩字），100 = 全黑底", self,
        )
        self.colorCard = ColorSettingCard(
            cfg.rtcOverlayColor, FIF.PALETTE, "文字颜色",
            "亮画面上白色最稳；想要 FPS 叠层的味道可以换成绿色", self,
        )
        self.cornerCard = ComboBoxSettingCard(
            cfg.rtcOverlayCorner, FIF.MOVE, "显示位置", "叠层贴屏幕的哪个角",
            texts=OVERLAY_CORNER_TEXTS, parent=self,
        )
        for card in (self.alphaCard, self.colorCard, self.cornerCard):
            self.textLayout.addWidget(card)

        # Dialog 构造时按"标题 + 一行正文"定死了尺寸，加完卡片得按新内容重算
        hint = self.layout().sizeHint()
        self.setFixedSize(max(440, hint.width()), hint.height() + 12)
        self.yesButton.setText("完成")
        self.hideCancelButton()
