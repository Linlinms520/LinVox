"""会动的文字标签：给首页「一言」用。

四种效果，每次随机抽一个（也可以指定）：

* ``typewriter`` 逐字打出
* ``decode``     先是一串乱码，再逐字「解码」成正文
* ``rotate``     每个字符依次旋转着落位
* ``breath``     字间距来回呼吸（循环播放）

为什么要自己画：靠 ``setText`` 逐字改文本做不出旋转和字距变化，而且每帧改文本
会让整行宽度不停抖动。这里按字符算好位置后自己画，高度由 :meth:`sizeHint`
给出稳定值，布局不会被动画带得跳来跳去。
"""
import math
import random
import unicodedata

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from .idle import idleWatcher

TYPEWRITER = "typewriter"
DECODE = "decode"
ROTATE = "rotate"
BREATH = "breath"
EFFECTS = (TYPEWRITER, DECODE, ROTATE, BREATH)

# 解码效果用的乱码（片假名 + 符号，看着就像"还没解出来"）
SCRAMBLE_CHARS = ("アイウエオカキクケコサシスセソタチツテトナニヌネノ"
                  "ハヒフヘホマミムメモヤユヨラリルレロ0123456789#$%&@*+<>/\\")

CHAR_MS = 55           # 打字机 / 解码：每个字符多少毫秒
MAX_MS = 4200          # 一整句最长播多久（长句别让人等）
ROTATE_STAGGER = 45    # 旋转入场：相邻字符的间隔
ROTATE_SPAN = 380      # 单个字符转到位要多久
BREATH_PERIOD = 2600   # 字距呼吸一个来回
BREATH_AMP = 3.0       # 字距呼吸幅度（px）
BREATH_LOOPS = 3       # 呼吸播几轮就定住：无限循环等于让界面永远 30fps 重绘
TICK_MS = 33           # 约 30fps：够顺滑，也不至于一直烧 CPU
MIN_WIDTH = 120        # 布局给不出宽度时至少留出这么宽

_BREAK_AFTER = " ，。、！？；：）」』】》…—·"


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    return 1.0 if value > 1 else value


def _canBreakAfter(ch: str) -> bool:
    """这个字符后面能不能断行：CJK / 空格 / 标点可以，英文单词中间不行。"""
    return ch in _BREAK_AFTER or unicodedata.east_asian_width(ch) in ("W", "F")


class AnimatedTextLabel(QLabel):
    """一行（可换行）文字，自带入场动画。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._text = text or ""
        self._effect = TYPEWRITER
        self._color = None
        self._looping = False
        self._frozen = False

        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._onTick)

        self.setMinimumWidth(MIN_WIDTH)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # 窗口被别的软件盖住 / 最小化时就别再一秒重绘三十次了（见 app/idle.py）
        if QApplication.instance() is not None:
            idleWatcher().subscribe(self._onIdleChanged)

    # ------------------------------------------------------------ 对外
    def text(self) -> str:
        """始终返回完整文本（动画只影响绘制，不影响拿到的内容）。"""
        return self._text

    def setText(self, text: str):          # noqa: D102 - 保持 QLabel 的接口
        self.setAnimatedText(text)

    def setAnimatedText(self, text: str, effect=None):
        """换文本并播放动画。effect 为空就随机挑一种。"""
        self._text = text or ""
        super().setText(self._text)        # Qt 侧也留一份完整文本（可复制/可查询）
        self.start(effect)

    def start(self, effect=None):
        self._effect = effect or random.choice(EFFECTS)
        self._looping = self._effect == BREATH
        self._timer.stop()
        if not self._text:
            self.updateGeometry()
            self.update()
            return
        self._elapsed.start()
        self.updateGeometry()
        if self._frozen:      # 冻结中：只换内容，不起动画
            self.update()
            return
        self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self.update()

    def isAnimating(self) -> bool:
        return self._timer.isActive()

    def _onIdleChanged(self, active: bool):
        """窗口不在前台：动画停掉，直接按最终状态把整句画出来。

        不能在半句乱码 / 旋转到一半的地方停住 —— 那样回来看见的是残缺的一行。
        """
        if active:
            self._frozen = False
            return
        self._timer.stop()
        self._frozen = True
        self.update()

    def setTextColor(self, color):
        """自绘不走 stylesheet，颜色要显式给（切主题时跟着换）。"""
        self._color = color
        self.update()

    # ------------------------------------------------------------ 尺寸
    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return self._size(self._lineCount(self.contentsRect().width()), fm)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _size(self, lines: int, fm: QFontMetrics):
        from PySide6.QtCore import QSize
        return QSize(MIN_WIDTH, lines * fm.height() + 4)

    def _lineCount(self, width: int) -> int:
        if not self._text:
            return 1
        fm = QFontMetrics(self.font())
        limit = width or 420
        lines, used = 1, 0
        for ch in self._text:
            advance = fm.horizontalAdvance(ch)
            if used + advance > limit and used > 0:
                lines += 1
                used = advance
            else:
                used += advance
        return lines

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateGeometry()      # 宽度变了行数可能变，高度要跟着重算

    # ------------------------------------------------------------ 动画
    def _duration(self) -> int:
        count = len(self._text)
        if self._effect == ROTATE:
            return ROTATE_STAGGER * count + ROTATE_SPAN + 200
        if self._effect == BREATH:
            return BREATH_PERIOD
        return min(CHAR_MS * count, MAX_MS) + 300

    def _onTick(self):
        elapsed = self._elapsed.elapsed()
        # 呼吸是循环的：不设上限它就会一直重绘下去，界面永远占着一份 CPU
        if self._looping and elapsed >= BREATH_PERIOD * BREATH_LOOPS:
            self._timer.stop()
            self.update()
            return
        if not self._looping and elapsed >= self._duration():
            self._timer.stop()
            self.update()
            return
        if self.isVisible():       # 看不见就别画了，省点电
            self.update()

    def _displayText(self) -> str:
        """当前这一帧应该画出来的字符（解码效果会有乱码）。"""
        if self._frozen or self._effect in (ROTATE, BREATH):
            return self._text

        count = len(self._text)
        progress = _clamp01(self._elapsed.elapsed() / float(self._duration()))
        shown = int(progress * count)
        if self._effect == TYPEWRITER:
            return self._text[:shown]
        noise = "".join(random.choice(SCRAMBLE_CHARS) for _ in range(count - shown))
        return self._text[:shown] + noise

    def _extraSpacing(self) -> float:
        """字距呼吸：0 → 幅度 → 0 平滑循环。"""
        if self._frozen or self._effect != BREATH:
            return 0.0
        phase = (self._elapsed.elapsed() % BREATH_PERIOD) / float(BREATH_PERIOD)
        return BREATH_AMP * (0.5 - 0.5 * math.cos(2 * math.pi * phase))

    # ------------------------------------------------------------ 绘制
    def _textColor(self):
        if self._color is not None:
            return self._color
        return self.palette().color(QPalette.WindowText)

    def _layout(self, display: str, extra: float):
        """把文本排成 [(字符, x, 基线y, 宽)]。超宽自动换行，尽量不腰斩英文单词。"""
        fm = QFontMetrics(self.font())
        rect = self.contentsRect()
        limit = max(rect.width(), MIN_WIDTH)
        lineHeight = fm.height()

        lines, current, used, lastBreak = [], [], 0.0, 0
        for ch in display:
            advance = fm.horizontalAdvance(ch)
            if current and used + advance + extra > limit:
                cut = lastBreak if lastBreak > 0 else len(current)
                lines.append("".join(current[:cut]))
                current = current[cut:]
                used = sum(fm.horizontalAdvance(c) + extra for c in current)
                lastBreak = 0
            current.append(ch)
            used += advance + extra
            if _canBreakAfter(ch):
                lastBreak = len(current)
        if current:
            lines.append("".join(current))

        align = self.alignment()
        total = len(lines) * lineHeight
        top = rect.top()
        if align & Qt.AlignVCenter:
            top += max(0, (rect.height() - total) // 2)
        elif align & Qt.AlignBottom:
            top += max(0, rect.height() - total)

        out, y = [], top
        for line in lines:
            width = sum(fm.horizontalAdvance(c) + extra for c in line)
            width -= extra if line else 0
            x = rect.left()
            if align & Qt.AlignHCenter:
                x += max(0, (rect.width() - width) // 2)
            elif align & Qt.AlignRight:
                x += max(0, rect.width() - width)
            baseline = y + fm.ascent()
            for ch in line:
                advance = fm.horizontalAdvance(ch)
                out.append((ch, x, baseline, advance))
                x += advance + extra
            y += lineHeight
        return out

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(self._textColor())
        painter.setFont(self.font())
        if not self._text:
            return

        display = self._displayText()
        extra = self._extraSpacing()
        elapsed = self._elapsed.elapsed()
        fm = QFontMetrics(self.font())

        for index, (ch, x, baseline, width) in enumerate(self._layout(display, extra)):
            alpha, angle = 1.0, 0.0
            if self._frozen:
                pass               # 冻结：一律按最终状态画（不透明、不旋转）
            elif self._effect == ROTATE:
                t = _clamp01((elapsed - index * ROTATE_STAGGER) / float(ROTATE_SPAN))
                if t <= 0:
                    continue
                alpha, angle = t, (1.0 - t) * -90.0

            painter.setOpacity(alpha)
            if angle:
                # 绕字符中心旋转：先平移到中心，画完再还原
                painter.save()
                painter.translate(x + width / 2, baseline - fm.ascent() / 2)
                painter.rotate(angle)
                painter.drawText(int(-width / 2), int(fm.ascent() / 2), ch)
                painter.restore()
            else:
                painter.drawText(int(x), baseline, ch)
