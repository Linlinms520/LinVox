"""主窗口：无边框 FluentWindow + 侧边导航。

FluentWindow 已经自带：无边框、自绘标题栏、窗口拖拽/拉伸、
明暗主题跟随、亚克力导航面板。你要做的只是往里塞页面。
"""
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    SplashScreen,
    FluentStyleSheet,
    setTheme,
    setThemeColor,
    Theme,
)

from .assets import appIconPath
from .config import cfg
from .idle import idleWatcher
from .wallpaper import Wallpaper
from .views.ckpt_view import CkptInterface
from .views.home_view import HomeInterface
from .views.rtc_view import RtcInterface
from .views.sep_view import SepInterface
from .views.setting_view import SettingInterface
from .views.task_view import TaskInterface
from .views.train_view import TrainInterface


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        # 托盘模式下关窗口只是藏起来（见 closeEvent），正常模式下关就是退
        self._hideOnClose = False
        self.initWindow()

        self.homeInterface = HomeInterface(self)
        self.rtcInterface = RtcInterface(self)
        self.sepInterface = SepInterface(self)
        self.trainInterface = TrainInterface(self)
        self.ckptInterface = CkptInterface(self)
        self.taskInterface = TaskInterface(self)
        self.settingInterface = SettingInterface(self)

        self.initNavigation()
        self.initBackground()
        self.initSplash()
        FluentStyleSheet.FLUENT_WINDOW.apply(self)

    # ---------- 初始化 ----------
    def initNavigation(self):
        # 侧边栏除了「设置」之外，每一项都写清楚它干什么 + 背后用的是什么
        # （实时变声 / PyMSS / RVC 训练 / ckpt 处理），别让人靠图标猜
        self.addSubInterface(self.homeInterface, FIF.HOME, "首页")
        self.addSubInterface(self.rtcInterface, FIF.MICROPHONE, "实时变声")
        self.addSubInterface(self.sepInterface, FIF.CUT, "人声伴奏分离&去混响·PyMSS")
        self.addSubInterface(self.trainInterface, FIF.TRAIN, "模型训练·RVC")
        self.addSubInterface(self.ckptInterface, FIF.BRUSH, "模型管理·ckpt处理")
        self.addSubInterface(self.taskInterface, FIF.LIBRARY, "任务管理器")

        self.navigationInterface.addSeparator()
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, "设置",
            NavigationItemPosition.BOTTOM,
        )

        # 首页横幅上的主按钮 → 没在变声时去实时变声页（switchTo 是 FluentWindow
        # 自带方法，导航高亮会自动同步）
        self.homeInterface.startRequested.connect(
            lambda: self.switchTo(self.rtcInterface)
        )
        # 接上实时变声：变声一开，首页那个按钮就变成红色的「停止变声」
        self.homeInterface.bindRtc(self.rtcInterface.rtc)

        # 任务管理器盯着各页的 RVC 子进程（pid → CPU / 内存 / 显存）
        self.taskInterface.watch([
            ("realtime", "实时变声", self.rtcInterface.rtc),
            ("separate", "人声分离", self.sepInterface.separator),
            ("train", "模型训练", self.trainInterface.trainer),
            ("ckpt", "模型管理", self.ckptInterface.worker),
        ])

        # 在设置页改过 RVC 目录后，切回变声页自动重新扫描
        self.stackedWidget.currentChanged.connect(self._onPageChanged)

        # 窗口被别的软件盖住 / 最小化时，把各页那些"只是好看"的定时活停掉
        # （进程采样、nvidia-smi 轮询、一言动画），详见 app/idle.py
        idleWatcher().watch(self)
        idleWatcher().subscribe(self._onActiveChanged)

        # 侧边栏默认收起（只留图标），想看说明点左上角 ☰。
        # 收起后文字看不见了，给每项挂上 tooltip，鼠标悬上去还认得出是哪一页。
        for item in self.navigationInterface.panel.items.values():
            item.widget.setToolTip(item.widget.text())

        # 启动时那几次 resize 发生在导航建好之前，这里补一次，保证初始状态正确
        self._syncNavCollapse()

    def initBackground(self):
        """自定义背景图：换图 / 改模糊度都实时生效，选择存在配置里。"""
        self.wallpaper = Wallpaper(self)
        cfg.bgImagePath.valueChanged.connect(self._applyBackground)
        cfg.bgBlur.valueChanged.connect(self._applyBackground)
        self._applyBackground()

    def _applyBackground(self, *_args):
        self.wallpaper.setImage(cfg.get(cfg.bgImagePath), cfg.get(cfg.bgBlur) or 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._syncNavCollapse()
        # 背景图不在任何布局里，得自己跟着窗口变大变小
        # （⚠️ 同 _syncNavCollapse：__init__ 期间的 resize 来时它还没出生）
        wallpaper = getattr(self, "wallpaper", None)
        if wallpaper is not None:
            wallpaper.fit()

    def _syncNavCollapse(self):
        """只在窗口真的放不下展开的侧边栏时才收起来。

        侧边栏**默认是收起的**：以前窗口够宽就自动展开，那些「模型训练·RVC」
        的说明把内容区白白挤窄了一截。现在交给用户 —— 点 ☰ 展开后就一直开着，
        除非窗口被拖到放不下（qfluentwidgets 自己也会收，这里补的是 MENU 态）。
        """
        # ⚠️ 坑点：FluentWindow 在 __init__ 里（导航建好之前）就会收到 resize，
        # 那时还没有 navigationInterface，直接取属性会崩在启动阶段。
        nav = getattr(self, "navigationInterface", None)
        if nav is None:
            return

        panel = nav.panel
        if self.width() < panel.minimumExpandWidth and not panel.isCollapsed():
            panel.collapse()

    def _onActiveChanged(self, active: bool):
        """窗口不在前台了：通知各页面自己收摊（没实现 setActive 的就跳过）。"""
        for page in (self.homeInterface, self.rtcInterface, self.sepInterface,
                     self.trainInterface, self.ckptInterface, self.taskInterface):
            setter = getattr(page, "setActive", None)
            if callable(setter):
                setter(active)

    def _onPageChanged(self, index: int):
        page = self.stackedWidget.widget(index)
        if page is self.rtcInterface:
            self.rtcInterface.refreshModels()
        elif page is self.sepInterface:
            self.sepInterface.refreshEngine()
        elif page is self.trainInterface:
            self.trainInterface.refreshEngine()
        elif page is self.ckptInterface:
            self.ckptInterface.refreshEngine()

    def initWindow(self):
        # 初始宽度留够：内容区放得下两列统计卡，想展开侧边栏也还有余量
        # （侧边栏默认收起，宽度不够时会自己再收一次，见 _syncNavCollapse）
        self.resize(1200, 720)
        self.setMinimumSize(860, 560)
        self._centerOnScreen()
        self.setWindowTitle("LinVox · RVC 实时变声工作台")
        # 自己的图标（app/resources/logo.ico，tools/make_brand.py 生成的）；
        # 启动闪屏直接用 windowIcon()，换这一个地方三处（标题栏 / 任务栏 / 闪屏）都跟着换
        icon = appIconPath()
        if icon:
            self.setWindowIcon(QIcon(icon))
        else:      # 素材没生成就退回库里自带的，别顶着一个空白图标
            self.setWindowIcon(QIcon(":/qfluentwidgets/images/logo.png"))

        # Win11 云母背景（非 Win11 平台会自动忽略，无需额外判断）
        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        # 主题配置变化 → 立即应用到全局
        cfg.themeChanged.connect(setTheme)
        cfg.themeColorChanged.connect(setThemeColor)

        # 云母开关在设置页改动时实时生效，不用重启
        cfg.micaEnabled.valueChanged.connect(self.setMicaEffectEnabled)

        # 界面缩放这类「重启才生效」的设置，改完给个提示，
        # 否则点了半天没反应，会以为是坏的
        cfg.appRestartSig.connect(self._onAppRestart)

    def _onAppRestart(self):
        InfoBar.warning(
            title="重启后生效", content="该设置会在下次启动应用时生效",
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
        )

    def _centerOnScreen(self):
        """把窗口移到屏幕中央（offscreen 环境下会自动跳过）。"""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )

    def initSplash(self):
        """启动闪屏：现代软件的第一印象。窗口 show 后自动淡出并销毁。"""
        if os.getenv("FLUENT_NO_SPLASH"):
            return
        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.raise_()

    def show(self):
        super().show()
        QApplication.processEvents()
        # show 之后窗口尺寸才最终定下来（缩放 / 屏幕），背景图跟着补一次
        wallpaper = getattr(self, "wallpaper", None)
        if wallpaper is not None:
            wallpaper.fit()
        self.finishSplash()

    def finishSplash(self):
        """收掉启动闪屏。

        ⚠️ 开机放托盘时不走 show()，闪屏没人来收就会一直浮在桌面上 —— 所以单独
        开一个方法，那种情况由 LinVox.py 直接调。
        """
        if not hasattr(self, "splashScreen"):
            return
        self.splashScreen.finish()
        self.splashScreen.deleteLater()
        del self.splashScreen

    def closeEvent(self, event):
        """关闭时收掉还在跑的子进程并把配置落盘。

        ⚠️ 顺序不能反：配置**先**存，再收子进程。以前先 shutdown，一旦某个
        worker 在退出时抛异常（或卡住），后面的 cfg.save() 就根本没机会执行，
        这一轮改过的选择全丢 —— 表现就是"重启后选择又没了"。
        """
        # 配置先落盘：藏起来之后用户可能一直不退出，存了才不会丢
        try:
            cfg.save()
        except Exception:
            pass

        # 托盘模式（开机自启带 --tray 启动）：关窗口 = **藏起来**，进程留在托盘里
        # 继续跑（正在变声的话不能断）。真要退出走托盘菜单的「退出」。
        # ⚠️ 这个判断必须在下面停子进程之前，不然一关窗口就把变声给断了
        if getattr(self, "_hideOnClose", False):
            self.hide()
            event.ignore()
            return

        # 叠层是独立窗口（挂在主窗口下的话，最小化时会被系统一起藏起来），
        # 所以退出时得手动收掉，别在桌面上留一个孤儿小窗
        try:
            self.rtcInterface.closeOverlay()
        except Exception:
            pass

        for name, stop in (
            ("实时变声", self.rtcInterface.rtc.shutdown),
            ("人声分离", self.sepInterface.separator.stop),
            ("模型训练", self.trainInterface.trainer.stop),
            ("模型管理", self.ckptInterface.worker.stop),
        ):
            try:
                stop()
            except Exception as exc:      # 一个页面出状况不能拖垮整个退出流程
                print("[LinVox] 停止%s子进程失败：%s" % (name, exc))

        super().closeEvent(event)
