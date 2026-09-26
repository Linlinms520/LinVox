"""首次使用指导。

第一次打开软件时自己弹一次（看完就记进配置，之后不再打扰）；
设置页里那张「使用指导」卡可以随时把它叫回来。

⚠️ 内容必须写**界面上真实存在的东西**（卡名 / 按钮名），不然就成了正确的废话：
"加载模型""开始音频转换"这些字眼改版时记得同步这里。
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    Dialog,
    FluentIcon as FIF,
    IconWidget,
    StrongBodyLabel,
)

from .config import cfg

GUIDE_TITLE = "欢迎使用 LinVox"

# 分步指导：图标 / 小标题 / 说明。顺序就是上手顺序
GUIDE_STEPS = (
    (FIF.MICROPHONE, "① 加载模型",
     "进「实时变声」，在「加载模型」里挑一个 .pth 模型；下拉里没有就点"
     "「选择.pth文件」去硬盘上找。想更像目标音色，再选一个同名的 .index 索引文件。"),
    (FIF.SPEAKERS, "② 接好声音",
     "「音频设备」里：输入设备选你的麦克风，输出设备选**虚拟声卡**（VB-CABLE 之类）。"
     "没有虚拟声卡的话，游戏 / 聊天软件里就选不到变声后的声音 —— 先装一个。"
     "只想自己听听效果，勾「输入监听」就行。"),
    (FIF.PLAY, "③ 开始变声",
     "点「开始音频转换」。第一次加载模型要十几秒，状态显示「变声中」就说明通了。"
     "戴上耳机听输出设备那一头。"),
    (FIF.SPEED_HIGH, "④ 卡的话这样调",
     "「运行控制」上的算法延迟 / 推理时间是实时的：延迟大就调小采样长度、换更快的"
     "F0 方法；A 卡 / I 卡记得去设置页打开「DirectML 加速」，改完重新开始变声。"),
    (FIF.MOVE, "⑤ 打游戏时也看得见",
     "勾上「桌面叠层」，会在游戏画面上浮一个小窗显示延迟（不抢焦点、不挡鼠标，"
     "后面的齿轮可以改它的颜色和位置）。"),
    (FIF.SETTING, "还有别的",
     "侧边栏还能做：人声伴奏分离去混响、模型训练、ckpt 模型管理。"
     "这份指导在设置页的「使用指导」里随时能再翻出来。"),
)


class GuideDialog(Dialog):
    """分步上手说明：标题 + 若干步骤 + 「开始使用」。"""

    def __init__(self, parent=None):
        super().__init__(
            GUIDE_TITLE, "照着这几步走，几分钟就能听到自己的变声", parent,
        )
        # 正文标题在 textLayout 里还有一份（Ui_MessageBox），留窗口标题那一份就够
        self.titleLabel.hide()

        self.stepRows = []
        for icon, title, text in GUIDE_STEPS:
            row = self._makeStep(icon, title, text)
            self.stepRows.append(row)
            self.textLayout.addWidget(row)

        # Dialog 构造时按「标题 + 一行正文」定死了尺寸，加完步骤得按新内容重算
        hint = self.layout().sizeHint()
        self.setFixedSize(max(480, hint.width()), hint.height() + 12)
        self.yesButton.setText("开始使用")
        self.hideCancelButton()

    # ------------------------------------------------------------------ 内部
    def _makeStep(self, icon, title, text):
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        iconWidget = IconWidget(icon, row)
        iconWidget.setFixedSize(24, 24)
        layout.addWidget(iconWidget, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(2)
        titleLabel = StrongBodyLabel(title, row)
        textLabel = BodyLabel(text, row)
        textLabel.setWordWrap(True)
        column.addWidget(titleLabel)
        column.addWidget(textLabel)
        layout.addLayout(column, 1)
        return row

    def stepCount(self) -> int:
        return len(self.stepRows)


def _markGuideDone(*_args):
    cfg.set(cfg.guideDone, True)


def maybeShowGuide(parent=None):
    """首次使用时才弹：返回要显示的对话框（调用方负责 exec），看过就返回 None。"""
    if cfg.get(cfg.guideDone):
        return None
    dialog = GuideDialog(parent)
    # 不管看完还是直接叉掉，都算"看过一次"，下次不再自动打扰
    dialog.finished.connect(_markGuideDone)
    return dialog


def showGuide(parent=None):
    """手动打开指导（设置页那张「使用指导」卡走这个）。"""
    dialog = GuideDialog(parent)
    dialog.exec()
    dialog.deleteLater()


def showFirstRunGuide(parent=None, delay=600):
    """首次使用时弹一次。

    ⚠️ 要延后一点：启动闪屏的淡出是动画，得吃事件循环，这里立刻 exec 会把闪屏
    卡在屏幕上不动。等它淡完再弹。
    """
    dialog = maybeShowGuide(parent)
    if dialog is None:
        return None
    QTimer.singleShot(delay, lambda: _execGuide(dialog))
    return dialog


def _execGuide(dialog):
    dialog.exec()
    dialog.deleteLater()
