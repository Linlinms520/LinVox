"""自定义窗口背景：一张图铺满整个窗口，并按配置做模糊。

它挂在窗口的哪一层？**窗口的子控件，且 lower() 到最底层**：

    窗口 paintEvent 画的底色      ← 最底（被它盖住）
    背景图（本文件）              ← 中间
    侧边栏 / 内容区 / 标题栏      ← 上面，照常显示

所以窗口自带的底色被盖掉、内容照常画在上面，正好是想要的效果。

为什么不直接在窗口的 paintEvent 里画？因为模糊用的是 ``QGraphicsBlurEffect``，
它只能作用在**控件**上 —— 画进 paintEvent 的像素没法挂特效。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsBlurEffect, QLabel

# QGraphicsBlurEffect 会把边缘往外糊出一段半透明的虚边（糊掉的部分和"外面没有
# 东西"混在一起，看起来像图片边上褪了一圈）。把控件向外扩这么多个半径，
# 虚边就落在窗口外面，看不出来了。
EDGE_PAD_PER_BLUR = 2


class Wallpaper(QLabel):
    """铺满父窗口的背景图，没设图片就整个隐藏（露出主题底色）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wallpaper")
        self._path = ""
        self._pixmap = QPixmap()
        self._blur = 0

        # 它压在最底层，不该吃掉鼠标事件（拖动窗口、点按钮都得照常）
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.lower()
        self.hide()

    # ------------------------------------------------------------ 对外
    def imagePath(self) -> str:
        """当前背景图的路径（没设就是空串）。"""
        return self._path

    def hasImage(self) -> bool:
        return not self._pixmap.isNull()

    def setImage(self, path: str, blur: int):
        """换背景图 / 改模糊度。两个都从配置里来，改完立刻生效。

        拖模糊度滑块会一口气发好几十次信号，所以**路径没变就不重新读文件**
        —— 一张 4K 图每次都 QPixmap(path) 一遍，滑块能拖成幻灯片。
        """
        path = path or ""
        if path != self._path:
            self._path = path
            self._pixmap = (QPixmap(path)
                            if path and os.path.isfile(path) else QPixmap())

        self._blur = max(0, int(blur or 0))
        self._installEffect(self._blur)
        self.setVisible(self.hasImage())
        self.fit()

    def _installEffect(self, radius: int):
        """挂上/摘掉模糊特效。

        ⚠️ 坑点：``setGraphicsEffect()`` 装新特效时会把旧的 **delete** 掉，
        所以换模糊度时不能复用同一个对象 —— 摘掉一次再装回去，手上那个
        Python 引用就悬空了（下次 setBlurRadius 直接 RuntimeError）。
        半径为 0 时整条特效链路都是白跑的（还要多一次离屏合成），直接摘掉。
        """
        if radius <= 0:
            self.setGraphicsEffect(None)
            return
        effect = QGraphicsBlurEffect(self)
        effect.setBlurRadius(float(radius))
        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.PerformanceHint)
        self.setGraphicsEffect(effect)

    def fit(self):
        """跟着窗口大小铺开（向外多糊出一圈，虚边推到窗口外面）。"""
        window = self.parentWidget()
        if window is None or not self.hasImage():
            return
        pad = self._blur * EDGE_PAD_PER_BLUR
        self.setGeometry(-pad, -pad,
                         window.width() + pad * 2, window.height() + pad * 2)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event):
        if not self.hasImage():
            return
        # KeepAspectRatioByExpanding：铺满但不变形，多出来的那一边裁掉居中
        scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                     Qt.SmoothTransformation)
        painter = QPainter(self)
        painter.drawPixmap((self.width() - scaled.width()) // 2,
                           (self.height() - scaled.height()) // 2, scaled)
