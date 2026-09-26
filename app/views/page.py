"""页面版式工具 —— 全站以「任务队列」页为基准。

任务页截图里的版式，也就是所有页面（首页 / 任务 / AI 变声 / 实时变声 / 分离 /
训练 / 模型管理 / 设置）都要遵守的版式：

    页面外边距 24，卡片间距 14
    ┌ 工具行：StrongBodyLabel 标题 ………… 搜索/工具按钮 + PrimaryPushButton      ┐
    │ 卡片：CardWidget，内边距 (20,16,20,16)、元素间距 10、标题用 StrongBodyLabel │
    │ 卡片底栏：次要文字状态 ………… PushButton + PrimaryPushButton（间距 8）      │
    └ 运行日志：标题行（运行日志 …… 工具位）+ 只读日志框（高 170）                ┘

改这里的一处常量，全站跟着变；不要再在某个页面里单独写
``setContentsMargins(20, 16, 20, 16)`` 之类的魔法数字。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ProgressBar,
    Slider,
    StrongBodyLabel,
    TextEdit,
)

from ..idle import idleWatcher
from ..theme import applyMuted, applySeparator

PAGE_MARGINS = (24, 24, 24, 24)   # 页面外边距
PAGE_SPACING = 14                 # 卡片之间
CARD_MARGINS = (20, 16, 20, 16)   # 卡片内边距
CARD_SPACING = 10                 # 卡片内元素之间
GRID_H_SPACING = 16               # 表单网格：列间距
GRID_V_SPACING = 10               # 表单网格：行间距
BUTTON_GAP = 8                    # 按钮之间（任务页就是这个值）
LOG_HEIGHT = 170                  # 日志框统一高度
PROGRESS_HEIGHT = 6               # 进度条统一厚度


# ------------------------------------------------------------------ 页面骨架
def applyPageLayout(layout):
    """页面主布局：外边距 + 卡片间距。"""
    layout.setContentsMargins(*PAGE_MARGINS)
    layout.setSpacing(PAGE_SPACING)
    return layout


def setupScrollPage(area, view):
    """ScrollArea 页面的统一收尾（七个滚动页都一样，别各写一遍）。

    ⚠️ 坑点：``QScrollArea.setWidget()`` 会把内部 view 的 ``autoFillBackground``
    打开，而系统 palette 的 Window 色是浅灰 —— 深色主题下整个页面就被自己
    刷成一片白。任务页不是 ScrollArea，所以以前只有它是对的、其余全白。
    这里统一关掉填充，让页面透明，露出窗口本身的主题底色。
    """
    area.setWidget(view)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setViewportMargins(0, 0, 0, 0)
    area.setStyleSheet("QScrollArea{background: transparent; border: none;}")

    view.setAutoFillBackground(False)
    area.viewport().setAutoFillBackground(False)
    area.viewport().setStyleSheet("background: transparent; border: none;")


def makeCard(parent, title=None, margins=CARD_MARGINS):
    """标准卡片。返回 ``(卡片, 竖向布局)``；卡片标题一律 StrongBodyLabel。"""
    card = CardWidget(parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*margins)
    layout.setSpacing(CARD_SPACING)
    if title:
        layout.addWidget(StrongBodyLabel(title, card))
    return card, layout


def makeSeparator(parent):
    """列表里的 1px 分隔线（颜色随主题，别再手写 rgba）。"""
    line = QFrame(parent)
    line.setFrameShape(QFrame.HLine)
    applySeparator(line)
    return line


def makeGrid(hSpacing=GRID_H_SPACING, vSpacing=GRID_V_SPACING):
    """表单网格：统一的行列间距，无外边距（外面已经是卡片了）。"""
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(hSpacing)
    grid.setVerticalSpacing(vSpacing)
    return grid


def makeHeader(parent, title: str):
    """页面工具行：左边大标题，右边留给调用方放按钮。

    返回 ``(容器, 布局)``，调用方::

        self.headerRow, header = makeHeader(self.view, "标题")
        header.addWidget(self.refreshButton)
    """
    header = QWidget(parent)
    layout = QHBoxLayout(header)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(StrongBodyLabel(title, header))
    layout.addStretch(1)
    return header, layout


def makeFooterRow(parent, status: str = "就绪"):
    """卡片/页面底栏（与任务页一致）：状态在左，按钮在右。

    返回 ``(布局, 状态标签)``，按钮用 :func:`addButtons` 往右追加。
    """
    row = QHBoxLayout()
    row.setSpacing(BUTTON_GAP)
    statusLabel = CaptionLabel(status, parent)
    applyMuted(statusLabel)
    row.addWidget(statusLabel)
    row.addStretch(1)
    return row, statusLabel


def addButtons(row, *buttons):
    """按任务页的方式往一行里放按钮：按钮之间固定 8px。"""
    for index, button in enumerate(buttons):
        if index:
            row.addSpacing(BUTTON_GAP)
        row.addWidget(button)
    return row


# ------------------------------------------------------------------ 控件
class PageSlider(Slider):
    """不响应滚轮的滑块。

    Qt 的滑块拿到焦点后滚轮就会改值：鼠标划过滑块时页面滚不动、参数还被顺手
    改掉了（音调、采样长度这种改一次就得重启的参数尤其坑）。这里直接忽略滚轮
    事件 —— ``ignore()`` 会把事件交还给外层 ScrollArea，页面照常滚动。
    """

    def wheelEvent(self, event: QWheelEvent):
        event.ignore()


def makeSlider(parent, orientation=Qt.Horizontal):
    """统一入口：页面的滑块都从这里建，滚轮行为全站一致。"""
    return PageSlider(orientation, parent)


def makeProgressBar(parent):
    """统一进度条：6px 高、不显示百分比（和任务页表格里那根一样）。"""
    bar = ProgressBar(parent)
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setFixedHeight(PROGRESS_HEIGHT)
    bar.setTextVisible(False)
    return bar


class IdleLogEdit(TextEdit):
    """运行日志框：窗口不在前台时先把行攒起来，回来再一次性补上。

    变声 / 训练 / 分离跑起来时，子进程一秒能吐几十行，而每一行 ``append``
    都要让 ``QTextEdit`` 重排一次文档、触发一次重绘。窗口最小化、缩在托盘里、
    或者被别的软件盖住的时候根本没人在看，这些开销全是白烧的 CPU。

    所以这里在"不在前台"期间不碰文档，只把行攒进一个列表（有上限），等窗口
    回到前台再一起补进去 —— 日志一行不丢，也没在没人看的时候白忙。
    """

    MAX_PENDING = 500      # 攒够这么多行就丢最早的：回来不会卡一下，内存也不涨

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = True          # 启动时按"在前台"算（这时窗口还没 show）
        self._pending: list = []
        self._dropped = 0
        idleWatcher().subscribe(self._onActiveChanged)

    # ------------------------------------------------------------ 对外
    def append(self, text: str):
        if self._active:
            super().append(text)
            return
        if len(self._pending) >= self.MAX_PENDING:
            self._pending.pop(0)
            self._dropped += 1
        self._pending.append(str(text))

    def clear(self):
        self._pending.clear()
        self._dropped = 0
        super().clear()

    def pendingCount(self) -> int:
        """还攒着没写进文档的行数（测试和排查用）。"""
        return len(self._pending)

    # ------------------------------------------------------------ 空闲
    def _onActiveChanged(self, active: bool):
        self._active = active
        if active:
            self._flush()

    def _flush(self):
        if self._dropped:
            super().append("…窗口不在前台期间省略了 %d 行日志，只保留最近 %d 行"
                           % (self._dropped, self.MAX_PENDING))
            self._dropped = 0
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        # 逐行补：和前台时的写入方式一致（append 会把文本当一段处理）
        for line in pending:
            super().append(line)


def makeLogCard(parent, placeholder: str, height: int = LOG_HEIGHT):
    """运行日志卡片。返回 ``(卡片, 日志框, 标题行布局)``。"""
    card, layout = makeCard(parent)
    titleRow = QHBoxLayout()
    titleRow.setSpacing(BUTTON_GAP)
    titleRow.addWidget(StrongBodyLabel("运行日志", card))
    titleRow.addStretch(1)

    logEdit = IdleLogEdit(card)
    logEdit.setReadOnly(True)
    logEdit.setFixedHeight(height)
    logEdit.setPlaceholderText(placeholder)

    layout.addLayout(titleRow)
    layout.addWidget(logEdit)
    return card, logEdit, titleRow
