"""任务管理器：实时看 RVC 各任务占了多少 CPU / 内存 / 显存。

以前这一页是一堆写死的假任务在跑进度条——看着热闹，其实跟 RVC 一点关系
没有。现在它盯着各页真实的 RVC 子进程（连它们派生的子进程一起算）：

    实时变声 / 人声分离 / 模型训练 / 模型管理  →  各占多少资源
    CPU% / 内存 / 显存每 2 秒刷新一次，卡住的任务可以直接在这里结束。

采样本身很轻（原生 API，毫秒级），但 nvidia-smi 一次要几百毫秒，所以显存
走异步 QProcess（见 :class:`app.procmon.GpuSampler`），主线程不卡。
"""
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TransparentToolButton,
)

from ..procmon import (
    GpuSampler,
    ProcessSampler,
    formatBytes,
    processMemoryBytes,
    processTree,
)
from ..theme import applyMuted
from .page import addButtons, applyPageLayout, makeCard, makeFooterRow, makeHeader

REFRESH_INTERVAL_MS = 2000
COLUMNS = ("任务", "状态", "PID", "CPU", "内存", "显存", "操作")


class TaskInterface(QWidget):
    """RVC 任务管理器：一行一个任务，实时显示它吃掉的资源。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskInterface")

        self.monitors: list = []          # [(key, 名称, 控制器)]
        self.sampler = ProcessSampler()
        self.gpu = GpuSampler(self)
        self._gpuApps: dict = {}
        self._gpuInfo: dict = {}
        self._stopButtons: dict = {}
        self._pids: dict = {}          # 行号 → 上一次的 pid（用于换进程时重置基线）
        self._windowActive = True      # 窗口不在前台 / 不在这一页时不刷新（见 app/idle.py）

        self.headerRow, header = makeHeader(self, "任务管理器")
        self.autoSwitch = SwitchButton(self)
        self.autoSwitch.setChecked(True)
        self.autoSwitch.setText("自动刷新")
        self.refreshButton = TransparentToolButton(FIF.SYNC, self)
        self.refreshButton.setToolTip("立即刷新")
        header.addWidget(BodyLabel("自动刷新", self))
        header.addWidget(self.autoSwitch)
        header.addWidget(self.refreshButton)

        # ---- 资源总览 ----
        self.overviewCard, overviewLayout = makeCard(self, "RVC 资源占用")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(2)
        self._statLabels = {}
        for index, (key, caption) in enumerate((
                ("cpu", "CPU（全部核心）"), ("memory", "内存"),
                ("gpu", "显存"), ("processes", "RVC 进程数"))):
            value = StrongBodyLabel("—", self.overviewCard)
            label = CaptionLabel(caption, self.overviewCard)
            applyMuted(label)
            grid.addWidget(value, 0, index)
            grid.addWidget(label, 1, index)
            self._statLabels[key] = (value, label)
        overviewLayout.addLayout(grid)

        # ---- 任务表 ----
        self.tableCard, tableLayout = makeCard(self, margins=(0, 0, 0, 0))
        self.table = TableWidget(self.tableCard)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(len(COLUMNS) - 1,
                                                          QHeaderView.Fixed)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(len(COLUMNS) - 1, 80)
        self.table.setEditTriggers(TableWidget.NoEditTriggers)
        tableLayout.addWidget(self.table)

        # ---- 底栏 ----
        self.stopAllButton = PushButton(FIF.CLOSE, "结束全部任务", self)
        self.bottomLayout, self.statusLabel = makeFooterRow(self, "尚未监视任何任务")
        addButtons(self.bottomLayout, self.stopAllButton)

        self.vBoxLayout = QVBoxLayout(self)
        applyPageLayout(self.vBoxLayout)
        self.vBoxLayout.addWidget(self.headerRow)
        self.vBoxLayout.addWidget(self.overviewCard)
        self.vBoxLayout.addWidget(self.tableCard, 1)
        self.vBoxLayout.addLayout(self.bottomLayout)

        # ---- 刷新 ----
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)

        self.refreshButton.clicked.connect(self.refresh)
        self.autoSwitch.checkedChanged.connect(self._onAutoChanged)
        self.stopAllButton.clicked.connect(self._onStopAll)
        self.gpu.sampled.connect(self._onGpuSampled)

        self._onAutoChanged(True)

    # ------------------------------------------------------------ 空闲省电
    def setActive(self, active: bool):
        """窗口被别的软件盖住 / 最小化时停掉轮询（由 app/idle.py 调用）。"""
        self._windowActive = active
        self._syncTimer()

    def showEvent(self, event):
        super().showEvent(event)
        self._syncTimer()       # 切回这一页才需要刷新

    def hideEvent(self, event):
        super().hideEvent(event)
        self._syncTimer()       # 切走了就别再刷了

    def _syncTimer(self):
        """自动刷新只在「开关开着 + 窗口在前台 + 正看着这一页」时才跑。

        nvidia-smi 一次要几百毫秒，没人看的时候每 2 秒白跑一次，纯属浪费。
        """
        if self.autoSwitch.isChecked() and self._windowActive and self.isVisible():
            self.timer.start()
            return
        self.timer.stop()
        for pid in self._pids.values():
            # 停了很久再回来，拿旧基线算出来的百分比没有意义，下次重新取基线
            self.sampler.forget(pid)

    # ------------------------------------------------------------ 监视对象
    def watch(self, monitors: list):
        """登记要盯的任务：``[(key, 名称, 控制器), …]``。

        控制器只要提供 ``pid()``（没在跑返回 0）和 ``stop()`` / ``shutdown()``
        即可，四个 RVC 页面各自的控制器都满足。
        """
        self.monitors = list(monitors)
        self._buildRows()

    def _buildRows(self):
        self.table.setRowCount(len(self.monitors))
        self._stopButtons.clear()
        for row, (_key, name, _ctrl) in enumerate(self.monitors):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            for column in range(1, len(COLUMNS) - 1):
                self.table.setItem(row, column, QTableWidgetItem("—"))
            button = TransparentToolButton(FIF.CLOSE, self.table)
            button.setToolTip("结束该任务（连同它派生的子进程）")
            button.setEnabled(False)
            button.clicked.connect(lambda _c=False, r=row: self._onStopRow(r))
            self.table.setCellWidget(row, len(COLUMNS) - 1, button)
            self._stopButtons[row] = button

    # ------------------------------------------------------------ 刷新
    def refresh(self):
        """采一次所有任务的资源占用并刷新表格。"""
        self.gpu.refresh()          # 异步的，结果回来会再刷一次
        self._updateRows()
        self.statusLabel.setText(
            "已刷新 %s" % time.strftime("%H:%M:%S"))

    def _updateRows(self):
        totalCpu = 0.0
        totalMemory = 0
        totalGpu = 0.0
        running = 0

        for row, (_key, _name, ctrl) in enumerate(self.monitors):
            pid = ctrl.pid()
            lastPid = self._pids.get(row, 0)
            if pid != lastPid:
                self.sampler.forget(lastPid)   # 换了进程，旧基线作废
                self._pids[row] = pid

            if not pid:
                self._setIdleRow(row)
                continue

            tree = processTree(pid)
            cpu = self.sampler.sample(pid)
            memory = sum(processMemoryBytes(p) for p in tree)
            gpu = sum(self._gpuApps.get(p, 0.0) for p in tree)

            totalCpu += cpu
            totalMemory += memory
            totalGpu += gpu
            running += 1

            extra = len(tree) - 1
            self.table.item(row, 1).setText(self._statusOf(ctrl))
            self.table.item(row, 2).setText(
                "%d (+%d)" % (pid, extra) if extra else str(pid))
            self.table.item(row, 3).setText("%.1f%%" % cpu)
            self.table.item(row, 4).setText(formatBytes(memory))
            self.table.item(row, 5).setText("%d MiB" % gpu if gpu else "—")
            self._stopButtons[row].setEnabled(True)

        self._setOverview(totalCpu, totalMemory, totalGpu, running)

    def _statusOf(self, ctrl) -> str:
        """实时变声要区分「引擎空闲」和「正在变声」。"""
        if getattr(ctrl, "isStreamRunning", False):
            return "变声中"
        return "运行中"

    def _setIdleRow(self, row: int):
        self.table.item(row, 1).setText("未运行")
        for column in (2, 3, 4, 5):
            self.table.item(row, column).setText("—")
        self._stopButtons[row].setEnabled(False)

    def _setOverview(self, cpu: float, memory: int, gpu: float, running: int):
        """总览：前三项是 RVC 自己吃掉的，显卡那一行顺带标出整卡占用。"""
        self._statLabels["cpu"][0].setText("%.1f%%" % cpu if running else "—")
        self._statLabels["memory"][0].setText(
            formatBytes(memory) if running else "—")
        self._statLabels["processes"][0].setText(
            "%d / %d" % (running, len(self.monitors)))

        value, caption = self._statLabels["gpu"]
        if self._gpuInfo:
            used = self._gpuInfo.get("used", 0.0)
            total = self._gpuInfo.get("total", 0.0)
            caption.setText("显存（%s 整卡 %.1f/%.1f GB）"
                            % (self._gpuInfo.get("name", "GPU"),
                               used / 1024, total / 1024))
            value.setText("%.0f MiB" % gpu if gpu else "—")
        elif self.gpu.available:
            caption.setText("显存")
            value.setText("%.0f MiB" % gpu if gpu else "—")
        else:
            # A 卡 / I 卡走 DirectML，nvidia-smi 查不到，说清楚而不是装作 0
            caption.setText("显存（DirectML 无法统计）")
            value.setText("—")

    def _onGpuSampled(self, apps: dict, info: dict):
        """显存是异步查回来的：数据到了补刷一次表格。

        这里只能刷表格，不能再调 ``refresh()`` —— 那会再发起一次查询，
        查完又触发本函数，nvidia-smi 就变成每秒都在跑了。
        """
        self._gpuApps = apps
        self._gpuInfo = info
        if self.isVisible():
            self._updateRows()

    def _onAutoChanged(self, enabled: bool):
        if enabled:
            self._syncTimer()
            self.refresh()      # 打开就立刻刷一次，别让用户等两秒
        else:
            self.timer.stop()
        self.statusLabel.setText("自动刷新已开启" if enabled else "自动刷新已暂停")

    # ------------------------------------------------------------ 结束任务
    def _onStopRow(self, row: int):
        if row >= len(self.monitors):
            return
        _key, name, ctrl = self.monitors[row]
        self._stop(ctrl)
        self.statusLabel.setText("已请求结束：%s" % name)
        self.refresh()

    def _onStopAll(self):
        for _key, _name, ctrl in self.monitors:
            self._stop(ctrl)
        self.statusLabel.setText("已请求结束全部 RVC 任务")
        self.refresh()

    @staticmethod
    def _stop(ctrl):
        """结束一个任务：走控制器自己的停止流程，它会顺带杀掉整棵进程树。"""
        stop = getattr(ctrl, "stop", None)
        if callable(stop):
            stop()
            return
        shutdown = getattr(ctrl, "shutdown", None)
        if callable(shutdown):
            shutdown()
