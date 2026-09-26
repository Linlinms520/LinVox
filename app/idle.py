"""窗口不在前台时，把"只是好看 / 没人看"的东西停下来。

这个界面里有几样东西是一直在动的：

* 首页「一言」的入场动画 —— 约 30fps 重绘，而呼吸效果是**无限循环**的
* 首页每 2 秒一次的进程 CPU 采样
* 任务页每 2 秒一次的资源刷新，里面还夹着一次 nvidia-smi（几百毫秒）
* 各页的运行日志 —— 变声/训练一秒能吐几十行，每行都要让日志框重排重绘
  （由 :class:`app.views.page.IdleLogEdit` 攒着，回来再补）

窗口被别的软件盖住、或者最小化到任务栏之后，这些还在照跑：用户看的是别的
窗口，风扇却在为本应用的装饰转 —— 这就是"什么都没干 CPU 也不低"的来源。
:class:`IdleWatcher` 负责盯住"应用还在不在用户眼前"，谁想省电就订阅它::

    from ..idle import idleWatcher

    idleWatcher().watch(window)              # 主窗口里装一次
    idleWatcher().subscribe(page.setActive)  # 各页面自己决定停什么

订阅走**弱引用**：页面/窗口销毁后自动失效，不会把已经删掉的对象再叫起来
（用普通信号连接的话，C++ 侧对象已销毁就会抛 RuntimeError）。
"""
from __future__ import annotations

import inspect
import weakref

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

_watcher = None


class IdleWatcher(QObject):
    """跟踪"这个应用还在不在用户眼前"，状态变了通知订阅者。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = True
        self._callbacks: list = []

    @property
    def active(self) -> bool:
        return self._active

    # ------------------------------------------------------------ 安装
    def watch(self, window):
        """盯住一个主窗口。

        两条线索都要接：切到别的软件（甚至窗口还看得见）会发 ``WindowDeactivate``；
        最小化 / 锁屏走的是 ``applicationStateChanged`` —— 只接一条会漏。
        初始按"在前台"算，等真的收到失焦事件再停（启动时窗口还没显示，
        这时候问 ``isActiveWindow()`` 永远是 False，会把所有东西一上来就停掉）。
        """
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.applicationStateChanged.connect(self._onAppState)
        window.installEventFilter(self)

    def subscribe(self, callback):
        """登记一个 ``callback(active: bool)``，并立刻同步一次当前状态。"""
        if inspect.ismethod(callback):
            self._callbacks.append(weakref.WeakMethod(callback))
        else:
            # 普通函数 / lambda 没法弱引用，只能强留一份（订阅方通常是页面方法）
            self._callbacks.append(lambda _cb=callback: _cb)
        try:
            callback(self._active)
        except RuntimeError:
            pass

    def setActive(self, active: bool):
        """手动切换状态（offscreen 测试收不到真实激活事件，用它模拟）。"""
        self._set(active)

    # ------------------------------------------------------------ 事件
    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.WindowActivate:
            self._set(True)
        elif kind == QEvent.WindowDeactivate:
            self._set(False)
        return False

    def _onAppState(self, state):
        self._set(state == Qt.ApplicationActive)

    def _set(self, active: bool):
        if active == self._active:
            return
        self._active = active
        self._notify(active)

    def _notify(self, active: bool):
        alive = []
        for ref in self._callbacks:
            slot = ref()
            if slot is None:                 # 订阅的对象已经销毁了
                continue
            try:
                slot(active)
            except RuntimeError:              # C++ 侧已销毁，但 Python 对象还在
                continue
            alive.append(ref)
        self._callbacks[:] = alive


def idleWatcher() -> IdleWatcher:
    """全局唯一的那一个（没有 QApplication 也能建，只是不会收到事件）。"""
    global _watcher
    if _watcher is None:
        _watcher = IdleWatcher()
    return _watcher
