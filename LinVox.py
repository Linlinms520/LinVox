import os
import sys

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator, setTheme, setThemeColor

from app.assets import appIconPath
from app.boot import TRAY_ARG
from app.config import cfg, recordLaunch
from app.guide import showFirstRunGuide
from app.tray import TrayIcon, available as trayAvailable
from app.window import MainWindow


APP_ID = "LinVox"


def _setWindowsAppId():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _applyDpiScale():

    scale = cfg.get(cfg.dpiScale)
    if scale and scale != "Auto":
        os.environ["QT_SCALE_FACTOR"] = str(scale)


def main():
    _setWindowsAppId()
    _applyDpiScale()
    recordLaunch()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("LinVox")
    # 整个进程的默认图标：没单独设过图标的窗口（各种对话框、叠层）也都跟着用
    icon = appIconPath()
    if icon:
        app.setWindowIcon(QIcon(icon))

    app.installTranslator(FluentTranslator(QLocale(QLocale.Chinese, QLocale.China)))


    setTheme(cfg.get(cfg.themeMode))
    setThemeColor(cfg.get(cfg.themeColor))

    window = MainWindow()


    if TRAY_ARG in sys.argv[1:] and trayAvailable():
        tray = TrayIcon(window, app)
        window._hideOnClose = True        
        app.setQuitOnLastWindowClosed(False)
        window.finishSplash()              
        tray.show()
    else:
        window.show()
       
        showFirstRunGuide(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
