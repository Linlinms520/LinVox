"""首页：横幅（真实时间问候 + 一言） + 统计卡片 + 功能说明。

演示的核心手法：
1. 用 ScrollArea 承载可滚动内容（内容超长时自动出滚动条，不撑爆窗口）
2. CardWidget 做卡片容器，天然带圆角与阴影
3. IconWidget 自动跟随主题强调色，换主题色时无需改代码
"""
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    FluentStyleSheet,
    IconWidget,
    PrimaryPushButton,
    ScrollArea,
    StrongBodyLabel,
    Theme,
    TransparentToolButton,
)

from ..anim_text import AnimatedTextLabel
from ..assets import logoPath, titlePath
from ..config import LAUNCH_TIME_FORMAT, cfg
from ..hitokoto import QuoteFetcher, formatQuote
from ..procmon import ProcessSampler
from ..theme import applyMuted, dangerButtonSheet, mutedColor
from .page import (
    BUTTON_GAP,
    CARD_MARGINS,
    CARD_SPACING,
    PAGE_SPACING,
    addButtons,
    applyPageLayout,
    makeCard,
    makeHeader,
    makeSeparator,
    setupScrollPage,
)

# 首页数值/行高这类"仪表盘"尺寸也放这里，别散落在各处
STAT_VALUE_SIZE = 20        # 统计卡数值字号
STAT_CARD_HEIGHT = 108      # 统计卡高度

# 四张统计卡全部是**真实数据**：前两张来自实时变声的推理上报，
# 第三张采自 RVC 子进程，第四张读配置里记下的上次启动时间。
PLACEHOLDER = "--"
IDLE_HINT = "实时变声未运行"
RUNNING_HINT = "实时变声运行中"

CPU_REFRESH_MS = 2000      # CPU 采样间隔（跟任务管理器一致）
CLOCK_REFRESH_MS = 30000   # 「上次启动」的「距今多久」重新算一遍

# RVC 刚起来的头 10 秒：模型刚加载完、CUDA / DirectML 还在预热，这几次推理
# 能到几百毫秒（甚至几秒），算进均值就把"正常水平"带偏成一次性的冷启动数字。
# 所以这 10 秒只更新延迟，不进平均。
WARMUP_SECONDS = 10

STATS = [
    ("delay", FIF.SPEED_HIGH, "算法延迟"),
    ("infer", FIF.ROBOT, "平均推理时间"),
    ("cpu", FIF.DEVELOPER_TOOLS, "RVC CPU占用"),
    ("launch", FIF.HISTORY, "上次启动"),
]

# 侧边栏每一个功能（除了「设置」）是干嘛的，名字跟侧边栏保持一致
FEATURES = [
    (FIF.MICROPHONE, "实时变声",
     "麦克风进、音箱出：选好模型和输入输出设备，边说边听变声后的声音"),
    (FIF.CUT, "人声伴奏分离&去混响·PyMSS",
     "把一首歌拆成人声和伴奏，顺手去掉混响，导出 wav / flac / mp3 / m4a"),
    (FIF.TRAIN, "模型训练·RVC",
     "切片 → 数据预处理 → 提取特征 → 训练，把素材做成自己的音色模型"),
    (FIF.BRUSH, "模型管理·ckpt处理",
     "查看并修改 ckpt 的信息（采样率、版本等），抽取小模型或融合模型"),
    (FIF.LIBRARY, "任务管理器",
     "看后台 RVC 进程占了多少 CPU / 内存 / 显存，随时可以停掉"),
]


class StatCard(CardWidget):
    """单个统计卡片：图标 + 数值 + 说明（数值会随真实数据刷新）。"""

    def __init__(self, icon, title: str, value: str = PLACEHOLDER,
                 hint: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(STAT_CARD_HEIGHT)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(44, 44)

        self.titleLabel = CaptionLabel(title, self)
        self.valueLabel = StrongBodyLabel(value, self)
        self.hintLabel = CaptionLabel(hint, self)
        applyMuted(self.hintLabel)

        font: QFont = self.valueLabel.font()
        font.setPointSize(STAT_VALUE_SIZE)
        font.setBold(True)
        self.valueLabel.setFont(font)

        self.vLayout = QVBoxLayout()
        self.vLayout.addWidget(self.titleLabel)
        self.vLayout.addWidget(self.valueLabel)
        self.vLayout.addWidget(self.hintLabel)
        self.vLayout.setSpacing(2)
        self.vLayout.setContentsMargins(0, 0, 0, 0)

        self.hLayout = QHBoxLayout(self)
        self.hLayout.addWidget(self.iconWidget, 0, Qt.AlignVCenter)
        self.hLayout.addSpacing(14)
        self.hLayout.addLayout(self.vLayout, 1)
        self.hLayout.setContentsMargins(*CARD_MARGINS)

    # ------------------------------------------------------------ 刷新
    def setValue(self, text: str):
        self.valueLabel.setText(text)

    def setHint(self, text: str):
        self.hintLabel.setText(text)


# 真实时间打招呼（按中国人日常说法分六段）
GREETINGS = (
    (0, "凌晨好"), (5, "早晨好"), (8, "上午好"),
    (11, "中午好"), (13, "下午好"), (18, "晚上好"),
)

QUOTE_REFRESH_DELAY = 500   # 启动后先让窗口出来，再去取一言


def lastLaunchText(stamp: str, now=None) -> tuple:
    """把「上次启动时间」翻成卡片上的两行：(数值, 说明)。

    数值放 ``09-20 14:03`` 这种短格式（卡片宽度有限），
    说明里写「距今多久」，比一串绝对时间更好读。
    """
    if not stamp:
        return PLACEHOLDER, "首次启动"
    try:
        moment = datetime.strptime(stamp, LAUNCH_TIME_FORMAT)
    except ValueError:
        return PLACEHOLDER, "首次启动"

    current = now or datetime.now()
    seconds = max((current - moment).total_seconds(), 0)
    if seconds < 60:
        ago = "刚刚"
    elif seconds < 3600:
        ago = "%d 分钟前" % (seconds // 60)
    elif seconds < 86400:
        ago = "%d 小时前" % (seconds // 3600)
    else:
        ago = "%d 天前" % (seconds // 86400)
    return moment.strftime("%m-%d %H:%M"), ago


def greetingText(now=None) -> str:
    """按真实时间打招呼。``now`` 可传 datetime（测试用）。"""
    hour = (now or datetime.now()).hour
    word = GREETINGS[0][1]
    for start, text in GREETINGS:
        if hour >= start:
            word = text
    return word + "，欢迎回来"


class BrandHeader(CardWidget):
    """首页顶部的品牌条：那张带「LinVox」标题的图。

    图是 app/resources/title.png（tools/make_brand.py 从一张原图烙好文字生成的）。
    卡片高度是定死的，图按**等比放大铺满 + 裁掉多余**来画（跟 Qt 的
    KeepAspectRatioByExpanding 一样）：窗口再宽也不会被拉变形，代价是上下会被裁
    一点 —— 所以生成脚本里那行标题是**竖向居中**排的，裁也裁不到它。

    图不在（没跑过 make_brand.py）就退化成一张普通空卡，不报错。
    """

    HEADER_HEIGHT = 150
    RADIUS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEADER_HEIGHT)
        self._source = QPixmap(titlePath())
        self._scaled = QPixmap()

    def hasImage(self) -> bool:
        return not self._source.isNull()

    def paintEvent(self, event):
        super().paintEvent(event)      # 卡片自己的圆角底 + 阴影
        if self._source.isNull():
            return
        # 缩放只在尺寸变了的时候做一次：paintEvent 每秒都来，别每次都重采样
        if self._scaled.size() != self.size():
            self._scaled = self._coverScaled(self.size())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)
        painter.setClipPath(clip)
        # ⚠️ 画的是已经裁好的图，尺寸和卡片一致 —— 不能写
        # drawPixmap(rect, 大图)：那会把大图拉伸回 rect，拖动窗口就变形了
        painter.drawPixmap(0, 0, self._scaled)

    def _coverScaled(self, size) -> QPixmap:
        """等比放大铺满再居中裁掉多余的一边（和 Qt 的 KeepAspectRatioByExpanding 等价）。

        ⚠️ 坑点：``scaled(size, KeepAspectRatioByExpanding)`` 出来的图是**比 size
        大一圈**的（多出来的那一边还没裁），直接拿去画就会被拉伸。这里自己把多余
        的部分裁掉，保证返回的图和卡片一样大。
        """
        expanded = self._source.scaled(size, Qt.KeepAspectRatioByExpanding,
                                       Qt.SmoothTransformation)
        if expanded.size() == size:
            return expanded
        cropped = expanded.copy(
            max(0, (expanded.width() - size.width()) // 2),
            max(0, (expanded.height() - size.height()) // 2),
            min(expanded.width(), size.width()),
            min(expanded.height(), size.height()),
        )
        # 极端取整下可能差 1px：补一次拉伸（1px 看不出来）。不补的话尺寸一直
        # 对不上，paintEvent 里那个"尺寸变了才重算"的判断就永远成立 —— 每帧重采样
        return cropped if cropped.size() == size else cropped.scaled(
            size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class BannerWidget(CardWidget):
    """顶部欢迎卡：真实时间问候 + 一句「一言」+ 一个入口。

    一言走后台线程 + 24 小时缓存（详见 app/hitokoto.py），断网也有话说。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.titleLabel = StrongBodyLabel(greetingText(), self)
        # 头像用的是同一个图标（app/resources/logo.png），AvatarWidget 自己切成圆形
        self.logoWidget = AvatarWidget(self)
        self.logoWidget.setRadius(22)
        if os.path.isfile(logoPath()):
            self.logoWidget.setImage(logoPath())
        self.logoWidget.setToolTip("LinVox")

        self.greetingLayout = QHBoxLayout()
        self.greetingLayout.setSpacing(CARD_SPACING)
        self.greetingLayout.addWidget(self.logoWidget, 0, Qt.AlignVCenter)
        self.greetingLayout.addWidget(self.titleLabel, 0, Qt.AlignVCenter)
        self.greetingLayout.addStretch(1)
        self.greetingLayout.setContentsMargins(0, 0, 0, 0)
        self.descLabel = BodyLabel(
            "点下面的按钮直接去实时变声；其它功能都在左侧边栏，"
            "首页最下面一张卡片写明了它们各自是干嘛的。", self
        )
        applyMuted(self.descLabel)
        self.startButton = PrimaryPushButton(FIF.PLAY, "立即开始", self)
        # 只留一个入口：点了直接去实时变声，跟侧边栏那一项对应，不再摆两个按钮

        # 一言：每次换一句随机挑一种入场动画（打字机 / 解码 / 旋转 / 呼吸）
        self.quoteLabel = AnimatedTextLabel("", self)
        self.quoteButton = TransparentToolButton(FIF.SYNC, self)
        self.quoteButton.setToolTip("换一句")
        self.quoteButton.setFixedSize(32, 32)

        self.quoteLayout = QHBoxLayout()
        self.quoteLayout.setSpacing(CARD_SPACING)
        self.quoteLayout.addWidget(self.quoteLabel, 1)
        self.quoteLayout.addWidget(self.quoteButton, 0, Qt.AlignTop)

        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(BUTTON_GAP)
        addButtons(self.buttonLayout, self.startButton)
        self.buttonLayout.addStretch(1)

        self.vLayout = QVBoxLayout()
        self.vLayout.setContentsMargins(0, 0, 0, 0)
        self.vLayout.setSpacing(CARD_SPACING)
        self.vLayout.addLayout(self.greetingLayout)
        self.vLayout.addLayout(self.quoteLayout)
        self.vLayout.addWidget(self.descLabel)
        self.vLayout.addLayout(self.buttonLayout)

        self.hLayout = QHBoxLayout(self)
        self.hLayout.addLayout(self.vLayout, 1)
        self.hLayout.setContentsMargins(*CARD_MARGINS)

        self.fetcher = None

        # 跨过零点 / 中午也不用重启：每半分钟对一次时间
        self.clockTimer = QTimer(self)
        self.clockTimer.setInterval(30 * 1000)
        self.clockTimer.timeout.connect(self._syncGreeting)
        self.clockTimer.start()

        self.quoteButton.clicked.connect(lambda: self.reloadQuote(force=True))
        QTimer.singleShot(QUOTE_REFRESH_DELAY, self.reloadQuote)

        # 一言颜色要跟着主题走（自绘不走 stylesheet，所以手动刷）
        cfg.themeChanged.connect(self._syncQuoteColor)
        self._syncQuoteColor()

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stopFetcher)

    # ------------------------------------------------------------ 主按钮
    def setRunning(self, running: bool):
        """变声开着的时候，这个按钮就是「停止」：红色 + 换图标换字。

        红色走 :func:`app.theme.dangerButtonSheet`；变回普通状态要重新
        ``FluentStyleSheet.BUTTON.apply`` —— 自定义 QSS 是整体替换，
        不还原就一直是红的。
        """
        button = self.startButton
        if running:
            button.setText("停止变声")
            # 白图标：PrimaryPushButton 会按主题把图标反色，深色主题下会画成
            # 黑图标，压在红底上几乎看不见 —— 这里直接给白色的，两个主题都对
            button.setIcon(FIF.PAUSE.icon(Theme.DARK))
            button.setToolTip("停止实时变声")
            button.setStyleSheet(dangerButtonSheet())
        else:
            button.setText("立即开始")
            button.setIcon(FIF.PLAY)
            button.setToolTip("去实时变声")
            FluentStyleSheet.BUTTON.apply(button)
        button.update()

    # ------------------------------------------------------------ 问候
    def _syncGreeting(self):
        text = greetingText()
        if text != self.titleLabel.text():
            self.titleLabel.setText(text)

    def _syncQuoteColor(self):
        self.quoteLabel.setTextColor(QColor(mutedColor()))

    # ------------------------------------------------------------ 一言
    def reloadQuote(self, force: bool = False):
        """取一句。force=True 表示无视缓存，重新联网拉。"""
        if force:
            self.quoteLabel.setAnimatedText("正在取一句…")
        self._stopFetcher()
        self.fetcher = QuoteFetcher(force=force, parent=self)
        self.fetcher.quoteReady.connect(self._onQuoteReady)
        self.fetcher.start()

    def _onQuoteReady(self, quote: dict):
        self.quoteLabel.setAnimatedText(formatQuote(quote))

    def _stopFetcher(self):
        """退出时收掉取一言的线程，别带着一条还在跑的线程关窗口。"""
        if self.fetcher is not None:
            self.fetcher.stop()
            self.fetcher = None


class FeatureRow(QFrame):
    """功能说明的一行：图标 + 功能名 + 一句话说明。"""

    def __init__(self, icon, title: str, desc: str, parent=None):
        super().__init__(parent)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(24, 24)

        self.titleLabel = StrongBodyLabel(title, self)
        self.descLabel = CaptionLabel(desc, self)
        applyMuted(self.descLabel)
        # 窗口拖窄时说明会换行，别把第二行截掉
        self.descLabel.setWordWrap(True)

        self.textLayout = QVBoxLayout()
        self.textLayout.setSpacing(2)
        self.textLayout.setContentsMargins(0, 0, 0, 0)
        self.textLayout.addWidget(self.titleLabel)
        self.textLayout.addWidget(self.descLabel)

        # 行内不再自己留左右边距：卡片已经有 CARD_MARGINS
        self.hLayout = QHBoxLayout(self)
        self.hLayout.setContentsMargins(0, 0, 0, 0)
        self.hLayout.setSpacing(CARD_SPACING)
        self.hLayout.addWidget(self.iconWidget, 0, Qt.AlignTop)
        self.hLayout.addLayout(self.textLayout, 1)


class HomeInterface(ScrollArea):
    """首页接口（一个可滚动页面）。"""

    startRequested = Signal()   # 没在变声时点主按钮 → 请主窗口切到实时变声页

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("homeInterface")

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.view)

        self.headerRow, _header = makeHeader(self.view, "总览")

        self.brandHeader = BrandHeader(self.view)
        self.banner = BannerWidget(self.view)

        self.statLayout = QHBoxLayout()
        self.statLayout.setSpacing(PAGE_SPACING)
        self.statCards: dict = {}
        for key, icon, title in STATS:
            card = StatCard(icon, title, PLACEHOLDER, IDLE_HINT, self.view)
            self.statCards[key] = card
            self.statLayout.addWidget(card)

        # 实时数据：推理指标（延迟 / 平均推理）
        self.rtc = None
        self.sampler = ProcessSampler()
        self._inferSum = 0
        self._inferCount = 0
        self._lastPid = 0
        self._runStartedAt = 0.0   # 本次变声真正跑起来的时刻（预热窗口从它算起）
        self._windowActive = True  # 窗口不在前台时不再采样（见 app/idle.py）

        self.cpuTimer = QTimer(self)
        self.cpuTimer.setInterval(CPU_REFRESH_MS)
        self.cpuTimer.timeout.connect(self._onCpuTick)

        self.clockTimer = QTimer(self)
        self.clockTimer.setInterval(CLOCK_REFRESH_MS)
        self.clockTimer.timeout.connect(self.refreshLastLaunch)

        self.banner.startButton.clicked.connect(self._onStartClicked)
        # 切主题时红色按钮的底色要跟着换。
        # ⚠️ themeChanged 会带上 Theme 参数，直接连到 _syncStartButton(running)
        # 会被当成"在变声中"，按钮一下就变红了 —— 所以这里把参数丢掉。
        cfg.themeChanged.connect(lambda *_args: self._syncStartButton())
        self._syncStartButton()
        self.refreshLastLaunch()

        # 功能说明：卡片自带标题，不再额外挂一个裸标签
        self.featureCard, self.featureLayout = makeCard(self.view, "功能说明")
        self.featureLayout.setSpacing(CARD_SPACING)
        for i, (icon, title, desc) in enumerate(FEATURES):
            row = FeatureRow(icon, title, desc, self.featureCard)
            self.featureLayout.addWidget(row)
            if i != len(FEATURES) - 1:
                self.featureLayout.addWidget(makeSeparator(self.featureCard))

        applyPageLayout(self.vBoxLayout)
        self.vBoxLayout.addWidget(self.headerRow)
        self.vBoxLayout.addWidget(self.brandHeader)
        self.vBoxLayout.addWidget(self.banner)
        self.vBoxLayout.addLayout(self.statLayout)
        self.vBoxLayout.addWidget(self.featureCard)
        self.vBoxLayout.addStretch(1)

        setupScrollPage(self, self.view)

    # ------------------------------------------------------------ 实时变声
    def bindRtc(self, rtc):
        """把首页接到实时变声控制器上（数值全部来自真实上报，没有假数据）。"""
        self.rtc = rtc
        rtc.streamChanged.connect(self._onStreamChanged)
        rtc.streamStarted.connect(self._onStreamStarted)
        rtc.metricsReceived.connect(self._onMetrics)
        self._syncStartButton()
        self._syncTimers()

    def _onStartClicked(self):
        """主按钮：变声开着就是「停止」，没开就请主窗口带我去变声页。"""
        if self.rtc is not None and self.rtc.isStreamRunning:
            self.rtc.stopStream()
        else:
            self.startRequested.emit()

    def _onStreamChanged(self, running: bool):
        self._syncStartButton(running)
        if not running:
            self._resetRtcStats()

    def _onStreamStarted(self, event: dict):
        self._runStartedAt = time.monotonic()   # 预热窗口从"真的跑起来了"这一刻算
        delay = int((event or {}).get("delay_ms") or 0)
        if delay:
            self.statCards["delay"].setValue("%d ms" % delay)
            self.statCards["delay"].setHint(RUNNING_HINT)

    def _onMetrics(self, infer_ms: int, delay_ms: int):
        """每次推理上报一次：延迟取最新，推理时间取本次运行的平均值。

        启动头 :data:`WARMUP_SECONDS` 秒**不进平均**（见那里的说明）：
        那几次是冷启动的数字，混进来会让"平均推理时间"永远虚高。
        """
        delay = self.statCards["delay"]
        if delay_ms:
            delay.setValue("%d ms" % int(delay_ms))
        delay.setHint(RUNNING_HINT)

        infer = self.statCards["infer"]
        warmup = self._warmupLeft()
        if warmup:
            infer.setHint("预热中，%d 秒后开始统计" % warmup)
            return

        self._inferSum += int(infer_ms)
        self._inferCount += 1
        infer.setValue("%d ms" % round(self._inferSum / self._inferCount))
        infer.setHint("共 %d 次均值" % self._inferCount)

    def _warmupLeft(self) -> int:
        """还在预热期就返回剩余秒数（0 = 已过预热期 / 压根没在跑）。"""
        if not self._runStartedAt:
            return 0
        left = WARMUP_SECONDS - (time.monotonic() - self._runStartedAt)
        return max(0, int(left + 0.999))     # 向上取整：显示还剩几秒

    def _resetRtcStats(self):
        self._inferSum = 0
        self._inferCount = 0
        self._runStartedAt = 0.0
        for key in ("delay", "infer", "cpu"):
            self.statCards[key].setValue(PLACEHOLDER)
            self.statCards[key].setHint(IDLE_HINT)

    def _syncStartButton(self, running=None):
        if running is None:
            running = bool(self.rtc is not None and self.rtc.isStreamRunning)
        self.banner.setRunning(running)

    def _onCpuTick(self):
        """RVC 子进程（含它派生的子进程）的 CPU 占用。

        CPU% 必须两次采样相减才算得出来，第一次只能拿到基线 —— 所以第一拍
        显示「采样中」，第二拍开始才是真数字。
        """
        card = self.statCards["cpu"]
        if self.rtc is None:
            return
        pid = self.rtc.pid() if self.rtc.isStreamRunning else 0
        if pid != self._lastPid:
            self.sampler.forget(self._lastPid)   # 换了进程，旧基线作废
            self._lastPid = pid
        if not pid:
            card.setValue(PLACEHOLDER)
            card.setHint(IDLE_HINT)
            return

        cpu = self.sampler.sample(pid)
        if cpu <= 0:
            card.setValue(PLACEHOLDER)
            card.setHint("采样中…")
            return
        card.setValue("%.0f%%" % cpu)
        card.setHint("实时变声进程（含子进程）")

    # ------------------------------------------------------------ 空闲省电
    def setActive(self, active: bool):
        """窗口不在前台时停掉采样（由 app/idle.py 调用）：没人看就别一直量。"""
        self._windowActive = active
        self._syncTimers()

    def _syncTimers(self):
        if self.rtc is not None and self._windowActive:
            self.cpuTimer.start()
            self.clockTimer.start()
            return
        self.cpuTimer.stop()
        self.clockTimer.stop()
        # 停了很久再回来，拿几秒前的旧基线算出来的百分比没有意义，下次重新取
        self.sampler.forget(self._lastPid)

    # ------------------------------------------------------------ 上次启动
    def refreshLastLaunch(self):
        """「上次启动」那张卡（读配置里记下的时间，顺便刷新"距今多久"）。"""
        value, hint = lastLaunchText(cfg.get(cfg.lastLaunch) or "")
        self.statCards["launch"].setValue(value)
        self.statCards["launch"].setHint(hint)
