"""系统托盘：开机自启「放进托盘」时的落脚点。

自启带上 ``--tray`` 时主窗口不弹出来，就靠这个托盘图标：
    - 左键单击：把主窗口叫出来（最小化 / 隐藏状态都能叫回来）
    - 右键菜单：打开主窗口 / 退出

⚠️ 不是每台机器都有托盘（Linux 某些桌面、无头的 offscreen 环境），
``available()`` 先问一句，没有就老实弹主窗口，别把程序变成谁也找不到的后台进程。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from qfluentwidgets import Action, FluentIcon as FIF, RoundMenu

from .assets import appIconPath

APP_TITLE = "LinVox · RVC 实时变声工作台"


def available() -> bool:
    """这台机器上有没有系统托盘（没有就别玩"启动到托盘"这一套）。"""
    return QSystemTrayIcon.isSystemTrayAvailable()


class TrayIcon(QSystemTrayIcon):
    """托盘图标：负责把主窗口叫回来，以及真正退出程序。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window

        icon = appIconPath()
        if icon:
            self.setIcon(QIcon(icon))
        self.setToolTip(APP_TITLE)

        # ⚠️ 菜单不给 parent：setContextMenu 会自己接管，而 QMenu 的 parent 必须是
        # QWidget —— 这里传进来的 parent 可能是 QApplication，会直接报参数错误
        self.menu = RoundMenu()
        self.showAction = Action(FIF.VIEW, "打开主窗口")
        self.showAction.triggered.connect(self.raiseWindow)
        self.quitAction = Action(FIF.POWER_BUTTON, "退出 LinVox")
        self.quitAction.triggered.connect(self.quitApp)
        self.menu.addAction(self.showAction)
        self.menu.addAction(self.quitAction)
        self.setContextMenu(self.menu)

        self.activated.connect(self._onActivated)

    # ------------------------------------------------------------------ 行为
    def raiseWindow(self):
        """把主窗口叫到前台：隐藏 / 最小化两种情况都得能叫回来。"""
        window = self._window
        if window is None:
            return
        if window.isMinimized():
            window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
        window.show()
        window.raise_()
        window.activateWindow()

    def quitApp(self):
        """真的退出：走主窗口的关闭流程（停子进程 + 存配置），不是硬 kill。"""
        window = self._window
        if window is not None:
            window._hideOnClose = False     # 这次是真的要关，别再藏起来
            window.close()
        QApplication.instance().quit()

    def _onActivated(self, reason):
        # 单击 / 双击都叫出主窗口（不同系统的"习惯动作"不一样）
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.raiseWindow()
