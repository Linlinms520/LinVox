"""实时变声页：RVC 实时控制台的完整 Fluent 版。

对应 RVC 官方实时控制台（realtime_gui.py）的全部功能：
加载模型 / 音频设备 / 运行控制 / 常规设置 / 性能设置 / 运行日志。

界面在这里，推理在子进程（app/rvc/realtime_worker.py，RVC runtime 解释器）。
热更新参数（音调、阈值、检索占比等）拖动即生效；性能参数改动需要重新开始。
"""
import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    ScrollArea,
    TransparentToolButton,
)

from ..config import F0_METHODS, cfg, scheduleSave
from ..overlay import MetricOverlay, OverlaySettingDialog
from ..rvc import RealtimeController, RvcEngine
from ..theme import applyMuted, setMuted, setStatus
from .page import (
    BUTTON_GAP,
    PAGE_SPACING,
    addButtons,
    applyPageLayout,
    makeCard,
    makeFooterRow,
    makeGrid,
    makeHeader,
    makeLogCard,
    makeSlider,
    setupScrollPage,
)

# 内嵌 RVC 只支持这三种，harvest / crepe 已被上游移除
# 括号里的说明要短：这张卡只有半屏宽，写长了下拉框放不下会被切掉
F0_RTC_LABELS = {
    "pm": "pm（最快，略糙）",
    "rmvpe": "rmvpe（推荐）",
    "fcpe": "fcpe（快，低延迟）",
}

SR_TYPE_LABELS = [("使用模型采样率", "sr_model"), ("使用设备采样率", "sr_device")]

# 常规设置 / 性能设置两张卡并排所需的最小宽度。低于这个值就改成上下排：
# 并排时每张卡只有半屏宽，「性别因子/声线粗细」「启用相位声码器」这类长文字
# 会被布局压掉一截，只能显示一半
SETTING_TWO_COL_MIN_WIDTH = 750

SLIDER_MIN_WIDTH = 90          # 滑块再窄就没法拖了
SLIDER_MAX_WIDTH = 240         # 宽窗口下也别拉成一根长条
VALUE_LABEL_WIDTH = 52         # 数值列固定宽，数字跳动时滑块不会跟着抖

HOT_KEYS = {
    "threhold", "pitch", "formant", "index_rate", "rms_mix_rate",
    "f0method", "I_noise_reduce", "O_noise_reduce", "use_pv", "function",
}


class RtcInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rtcInterface")

        self.engine = RvcEngine(self)
        self.rtc = RealtimeController(self.engine, self)
        self._slowNoticed = False  # 卡顿提示每次启动只说一次，避免刷屏
        self._windowActive = True  # 窗口不在前台时连指标数字都不刷（见 setActive）
        self._overlay = None       # 桌面叠层，勾了「桌面叠层」才建
        self._lastDelay = 0        # 最近一次的算法延迟 / 推理耗时，叠层刚开时用它填
        self._lastInfer = 0

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.view)
        applyPageLayout(self.vBoxLayout)

        self._buildHeader()
        self._buildModelCard()
        self._buildDeviceCard()
        # 运行控制排在设置**上面**：选完模型和设备就该点开始了，
        # 以前它在设置卡下面，得滚过一堆滑块才看得到「开始音频转换」
        self._buildRunCard()
        self._buildSettingCards()
        self._buildLogCard()

        self.vBoxLayout.addWidget(self.headerRow)
        self.vBoxLayout.addWidget(self.modelCard)
        self.vBoxLayout.addWidget(self.deviceCard)
        self.vBoxLayout.addWidget(self.runCard)
        self.vBoxLayout.addWidget(self.settingRow)
        self.vBoxLayout.addWidget(self.logCard)
        self.vBoxLayout.addStretch(1)

        setupScrollPage(self, self.view)

        # ⚠️ 顺序不能反：必须**先把上次的选择填回控件，再接信号**。
        # 填控件本身会触发 stateChanged / currentIndexChanged，而这些信号连着的
        # 是 collectParams()（把当前所有控件的值写进配置）。先接信号的话，填
        # 「使用模型采样率」这一步就会把**还没填到的** 输入/输出降噪、相位声码器
        # 以默认 False 写进配置 —— 表现就是勾过的开关重启后自己灭了。
        self._restoreFromConfig()
        self._connectSignals()
        self._syncOverlay()   # 上次勾着的话，这次启动直接把叠层摆出来

        # 打开页面就枚举设备（worker 只拉起 sounddevice，秒级）
        QTimer.singleShot(600, self._onReloadDevices)

    # ------------------------------------------------------------ 构建界面
    def _buildHeader(self):
        self.headerRow, header = makeHeader(self.view, "实时变声 · RVC")
        self.stateLabel = CaptionLabel("未运行", self.headerRow)
        applyMuted(self.stateLabel)
        header.addWidget(self.stateLabel)

    def _buildModelCard(self):
        self.modelCard, layout = makeCard(self.view, "加载模型")

        # 模型：下拉（扫描 assets/weights）+ 浏览任意 .pth
        modelRow = QHBoxLayout()
        modelRow.addWidget(BodyLabel("模型文件", self.modelCard))
        self.modelCombo = ComboBox(self.modelCard)
        self.modelCombo.setMinimumWidth(360)
        self.modelBrowse = PushButton(FIF.FOLDER, "选择.pth文件", self.modelCard)
        modelRow.addWidget(self.modelCombo, 1)
        modelRow.addWidget(self.modelBrowse)
        layout.addLayout(modelRow)

        # 索引：下拉 + 浏览任意 .index
        indexRow = QHBoxLayout()
        indexRow.addWidget(BodyLabel("索引文件", self.modelCard))
        self.indexCombo = ComboBox(self.modelCard)
        self.indexCombo.setMinimumWidth(360)
        self.indexBrowse = PushButton(FIF.FOLDER, "选择.index文件", self.modelCard)
        indexRow.addWidget(self.indexCombo, 1)
        indexRow.addWidget(self.indexBrowse)
        layout.addLayout(indexRow)

    def _buildDeviceCard(self):
        self.deviceCard, cardLayout = makeCard(self.view, "音频设备")
        layout = makeGrid()
        cardLayout.addLayout(layout)

        layout.addWidget(BodyLabel("设备类型", self.deviceCard), 0, 0)
        self.hostapiCombo = ComboBox(self.deviceCard)
        self.hostapiCombo.setMinimumWidth(200)
        layout.addWidget(self.hostapiCombo, 0, 1, Qt.AlignLeft)
        self.exclusiveCheck = CheckBox("独占 WASAPI 设备", self.deviceCard)
        layout.addWidget(self.exclusiveCheck, 0, 2, Qt.AlignLeft)

        layout.addWidget(BodyLabel("输入设备", self.deviceCard), 1, 0)
        self.inputCombo = ComboBox(self.deviceCard)
        self.inputCombo.setMinimumWidth(360)
        layout.addWidget(self.inputCombo, 1, 1, 1, 2)
        layout.addWidget(BodyLabel("输出设备", self.deviceCard), 2, 0)
        self.outputCombo = ComboBox(self.deviceCard)
        self.outputCombo.setMinimumWidth(360)
        layout.addWidget(self.outputCombo, 2, 1, 1, 2)

        # 底栏与任务页一致：采样率（状态）在左，操作在右
        bottom, self.srLabel = makeFooterRow(self.deviceCard, "采样率: --")
        self.srModelRadio = RadioButton("使用模型采样率", self.deviceCard)
        self.srDeviceRadio = RadioButton("使用设备采样率", self.deviceCard)
        self.reloadButton = TransparentToolButton(FIF.SYNC, self.deviceCard)
        self.reloadButton.setToolTip("重载设备列表")
        addButtons(bottom, self.srModelRadio, self.srDeviceRadio,
                   self.reloadButton)
        layout.addLayout(bottom, 3, 0, 1, 3)

    def _buildSettingCards(self):
        """常规设置 + 性能设置（宽窗口并排，窄窗口自动改成上下排）。"""
        self.settingRow = QWidget(self.view)
        self.settingGrid = makeGrid(PAGE_SPACING, PAGE_SPACING)
        self.settingGrid.setColumnStretch(0, 1)
        self.settingGrid.setColumnStretch(1, 1)
        self.settingRow.setLayout(self.settingGrid)

        # ---- 常规设置 ----
        self.normalCard, cardLayout = makeCard(self.settingRow, "常规设置")
        normal = makeGrid()
        normal.setColumnStretch(1, 1)   # 多出来的宽度全给滑块，标签按原文宽度站着
        cardLayout.addLayout(normal)

        self.thresholdSlider, self.thresholdValue = self._addSlider(
            normal, 0, "响应阈值", -60, 0, int(cfg.get(cfg.rtcThreshold)), 1)
        self.pitchSlider, self.pitchValue = self._addSlider(
            normal, 1, "音调设置", -16, 16, int(cfg.get(cfg.rtcPitch)), 1)
        self.formantSlider, self.formantValue = self._addSlider(
            normal, 2, "性别因子/声线粗细", -200, 200,
            int(round(float(cfg.get(cfg.rtcFormant)) * 100)), 100)
        self.indexRateSlider, self.indexRateValue = self._addSlider(
            normal, 3, "检索特征占比", 0, 100,
            int(round(float(cfg.get(cfg.rtcIndexRate)) * 100)), 100)
        self.rmsSlider, self.rmsValue = self._addSlider(
            normal, 4, "响度因子", 0, 100,
            int(round(float(cfg.get(cfg.rtcRmsMixRate)) * 100)), 100)

        # 下拉框吃掉滑块列 + 数值列，才有地方显示完整的算法名
        normal.addWidget(BodyLabel("音高算法", self.settingRow), 5, 0)
        self.f0Combo = ComboBox(self.settingRow)
        for method in F0_METHODS:
            self.f0Combo.addItem(F0_RTC_LABELS.get(method, method), userData=method)
        index = self.f0Combo.findData(cfg.get(cfg.rtcF0Method))
        self.f0Combo.setCurrentIndex(max(index, 0))
        normal.addWidget(self.f0Combo, 5, 1, 1, 2)

        # ---- 性能设置 ----
        self.perfCard, perfLayout = makeCard(self.settingRow, "性能设置")
        perf = makeGrid()
        perf.setColumnStretch(1, 1)
        perfLayout.addLayout(perf)

        self.blockSlider, self.blockValue = self._addSlider(
            perf, 0, "采样长度", 2, 150,
            int(round(float(cfg.get(cfg.rtcBlockTime)) * 100)), 100)
        self.crossfadeSlider, self.crossfadeValue = self._addSlider(
            perf, 1, "淡入淡出长度", 1, 15,
            int(round(float(cfg.get(cfg.rtcCrossfade)) * 100)), 100)
        self.extraSlider, self.extraValue = self._addSlider(
            perf, 2, "额外推理时长", 5, 500,
            int(round(float(cfg.get(cfg.rtcExtraTime)) * 100)), 100)

        # 三个开关分两列放：全挤在一行的话，最后一个会被压得只剩一半
        # （DirectML 不在这里，设置页里有，两处共用同一个配置项）
        checkGrid = makeGrid()
        checkGrid.setColumnStretch(0, 1)
        checkGrid.setColumnStretch(1, 1)
        self.inNoiseCheck = CheckBox("输入降噪", self.settingRow)
        self.outNoiseCheck = CheckBox("输出降噪", self.settingRow)
        self.pvCheck = CheckBox("启用相位声码器", self.settingRow)
        for widget, (r, c, span) in ((self.inNoiseCheck, (0, 0, 1)),
                                     (self.outNoiseCheck, (0, 1, 1)),
                                     (self.pvCheck, (1, 0, 2))):
            checkGrid.addWidget(widget, r, c, 1, span)
        perf.addLayout(checkGrid, 3, 0, 1, 3)

        # 先按并排放，真正显示时 _updateSettingColumns() 会按宽度纠正
        self._settingTwoColumns = True
        self.settingGrid.addWidget(self.normalCard, 0, 0)
        self.settingGrid.addWidget(self.perfCard, 0, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateSettingColumns()

    def _updateSettingColumns(self):
        """按可用宽度决定两张设置卡是并排还是上下排。"""
        width = self.viewport().width()
        if width <= 0:
            return
        twoColumns = width >= SETTING_TWO_COL_MIN_WIDTH
        if twoColumns == self._settingTwoColumns:
            return
        self._settingTwoColumns = twoColumns

        grid = self.settingGrid
        grid.removeWidget(self.normalCard)
        grid.removeWidget(self.perfCard)
        if twoColumns:
            grid.addWidget(self.normalCard, 0, 0)
            grid.addWidget(self.perfCard, 0, 1)
        else:
            grid.addWidget(self.normalCard, 0, 0, 1, 2)
            grid.addWidget(self.perfCard, 1, 0, 1, 2)

    def _addSlider(self, grid, row, label, minimum, maximum, value, divisor):
        """一行：标签 + 滑块 + 数值。divisor 用于把整数滑块换算成实际值。

        用网格而不是横向布局：标签按自然宽度占位（不会被压成「性别因子/…」），
        富余宽度全给滑块，数值列固定宽，数字跳动时布局不会抖。
        """
        text = BodyLabel(label, self.settingRow)
        text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        slider = makeSlider(self.settingRow)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(1)
        slider.setValue(value)
        slider.setMinimumWidth(SLIDER_MIN_WIDTH)
        slider.setMaximumWidth(SLIDER_MAX_WIDTH)
        valueLabel = CaptionLabel(self._sliderText(value, divisor), self.settingRow)
        valueLabel.setFixedWidth(VALUE_LABEL_WIDTH)
        valueLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        applyMuted(valueLabel)
        slider.valueChanged.connect(
            lambda v: valueLabel.setText(self._sliderText(v, divisor)))

        grid.addWidget(text, row, 0)
        grid.addWidget(slider, row, 1)
        grid.addWidget(valueLabel, row, 2)
        return slider, valueLabel

    @staticmethod
    def _sliderText(value, divisor):
        if divisor == 1:
            return str(value)
        return "%.2f" % (value / divisor)

    def _buildRunCard(self):
        self.runCard, layout = makeCard(self.view, "运行控制")
        row = QHBoxLayout()
        row.setSpacing(BUTTON_GAP)

        self.startButton = PrimaryPushButton(FIF.PLAY, "开始音频转换", self.runCard)
        self.stopButton = PushButton(FIF.PAUSE, "停止音频转换", self.runCard)
        self.stopButton.setEnabled(False)
        addButtons(row, self.startButton, self.stopButton)

        row.addSpacing(20)
        self.monitorRadio = RadioButton("输入监听", self.runCard)
        self.vcRadio = RadioButton("输出变声", self.runCard)
        self.vcRadio.setChecked(True)
        row.addWidget(self.monitorRadio)
        row.addWidget(self.vcRadio)

        row.addSpacing(20)
        # 打游戏时主窗口早就在后面了：勾上这个另开一个置顶小窗显示这两个数
        self.overlayCheck = CheckBox("桌面叠层", self.runCard)
        self.overlayCheck.setToolTip(
            "在别的窗口（游戏）上面浮一层「算法延迟 / 推理时间」，不抢焦点也不挡鼠标。"
            "独占全屏的游戏会盖住它，无边框 / 窗口化全屏没问题。")
        row.addWidget(self.overlayCheck)

        # 叠层的外观（透明度 / 文字颜色 / 位置）：没开叠层时改它没有意义
        self.overlaySettingButton = TransparentToolButton(FIF.SETTING, self.runCard)
        self.overlaySettingButton.setToolTip("叠层外观：底板透明度、文字颜色、显示位置")
        self.overlaySettingButton.setEnabled(False)
        row.addWidget(self.overlaySettingButton)

        row.addStretch(1)

        metricBox = QGridLayout()
        self.delayValue = BodyLabel("0", self.runCard)
        self.inferValue = BodyLabel("0", self.runCard)
        for label, widget in (("算法延迟", self.delayValue), ("推理时间", self.inferValue)):
            box = QVBoxLayout()
            title = CaptionLabel(label, self.runCard)
            title.setAlignment(Qt.AlignCenter)
            applyMuted(title)
            widget.setAlignment(Qt.AlignCenter)
            box.addWidget(title)
            box.addWidget(widget)
            cell = QWidget(self.runCard)
            cell.setLayout(box)
            metricBox.addWidget(cell)
        row.addLayout(metricBox)
        layout.addLayout(row)

    def _buildLogCard(self):
        self.logCard, self.logEdit, titleRow = makeLogCard(
            self.view, "启动信息、推理耗时与错误信息会实时显示在这里")
        self.autoScrollCheck = CheckBox("自动滚动", self.logCard)
        self.autoScrollCheck.setChecked(bool(cfg.get(cfg.rtcAutoScroll)))
        self.clearLogButton = TransparentToolButton(FIF.DELETE, self.logCard)
        titleRow.addWidget(self.autoScrollCheck)
        titleRow.addWidget(self.clearLogButton)

    # ------------------------------------------------------------ 参数
    def collectParams(self) -> dict:
        params = {
            "pth_path": self.modelCombo.currentData() or "",
            "index_path": self.indexCombo.currentData() or "",
            "hostapi": self.hostapiCombo.currentText() or "",
            "wasapi_exclusive": self.exclusiveCheck.isChecked(),
            "input_device": self.inputCombo.currentText() or "",
            "output_device": self.outputCombo.currentText() or "",
            "sr_type": (self.srModelRadio.isChecked() and "sr_model")
            or (self.srDeviceRadio.isChecked() and "sr_device") or "sr_model",
            "function": "im" if self.monitorRadio.isChecked() else "vc",
            "threhold": self.thresholdSlider.value(),
            "pitch": self.pitchSlider.value(),
            "formant": self.formantSlider.value() / 100.0,
            "index_rate": self.indexRateSlider.value() / 100.0,
            "rms_mix_rate": self.rmsSlider.value() / 100.0,
            "f0method": self.f0Combo.currentData() or "fcpe",
            "block_time": self.blockSlider.value() / 100.0,
            "crossfade_length": self.crossfadeSlider.value() / 100.0,
            "extra_time": self.extraSlider.value() / 100.0,
            "I_noise_reduce": self.inNoiseCheck.isChecked(),
            "O_noise_reduce": self.outNoiseCheck.isChecked(),
            "use_pv": self.pvCheck.isChecked(),
            # DirectML 不在本页放开关：设置页那个「DirectML 加速」是同一个配置
            # 项（cfg.rvcUseDml），训练页也是这么读的，没必要摆第三份
            "dml": bool(cfg.get(cfg.rvcUseDml)),
        }
        self._saveToConfig(params)
        return params

    def _saveToConfig(self, params: dict):
        cfg.set(cfg.rtcPth, params["pth_path"], save=False)
        cfg.set(cfg.rtcIndex, params["index_path"], save=False)
        cfg.set(cfg.rtcHostapi, params["hostapi"], save=False)
        cfg.set(cfg.rtcExclusive, params["wasapi_exclusive"], save=False)
        cfg.set(cfg.rtcInput, params["input_device"], save=False)
        cfg.set(cfg.rtcOutput, params["output_device"], save=False)
        cfg.set(cfg.rtcSrType, params["sr_type"], save=False)
        cfg.set(cfg.rtcFunction, params["function"], save=False)
        cfg.set(cfg.rtcThreshold, params["threhold"], save=False)
        cfg.set(cfg.rtcPitch, params["pitch"], save=False)
        cfg.set(cfg.rtcFormant, params["formant"], save=False)
        cfg.set(cfg.rtcIndexRate, params["index_rate"], save=False)
        cfg.set(cfg.rtcRmsMixRate, params["rms_mix_rate"], save=False)
        cfg.set(cfg.rtcF0Method, params["f0method"], save=False)
        cfg.set(cfg.rtcBlockTime, params["block_time"], save=False)
        cfg.set(cfg.rtcCrossfade, params["crossfade_length"], save=False)
        cfg.set(cfg.rtcExtraTime, params["extra_time"], save=False)
        cfg.set(cfg.rtcINoise, params["I_noise_reduce"], save=False)
        cfg.set(cfg.rtcONoise, params["O_noise_reduce"], save=False)
        cfg.set(cfg.rtcUsePv, params["use_pv"], save=False)
        cfg.set(cfg.rtcAutoScroll, self.autoScrollCheck.isChecked(), save=False)
        scheduleSave()   # 改动立刻排一次落盘，崩了/被强杀也不丢选择

    def _restoreFromConfig(self):
        """把上次的选择填回控件。"""
        self.modelCombo.clear()
        self.indexCombo.clear()
        self.refreshModels()
        for combo, saved in ((self.inputCombo, cfg.get(cfg.rtcInput)),
                             (self.outputCombo, cfg.get(cfg.rtcOutput))):
            if saved and combo.count() == 0:
                combo.addItem(saved, userData=saved)
                combo.setCurrentIndex(0)
        sr_type = cfg.get(cfg.rtcSrType)
        (self.srModelRadio if sr_type == "sr_model" else self.srDeviceRadio).setChecked(True)
        self.exclusiveCheck.setChecked(bool(cfg.get(cfg.rtcExclusive)))
        self.overlayCheck.setChecked(bool(cfg.get(cfg.rtcOverlay)))
        self.inNoiseCheck.setChecked(bool(cfg.get(cfg.rtcINoise)))
        self.outNoiseCheck.setChecked(bool(cfg.get(cfg.rtcONoise)))
        self.pvCheck.setChecked(bool(cfg.get(cfg.rtcUsePv)))
        if cfg.get(cfg.rtcFunction) == "im":
            self.monitorRadio.setChecked(True)
            self.vcRadio.setChecked(False)
        saved_pth = cfg.get(cfg.rtcPth)
        saved_index = cfg.get(cfg.rtcIndex)
        if saved_pth:
            i = self.modelCombo.findData(saved_pth)
            if i >= 0:
                self.modelCombo.setCurrentIndex(i)
        if saved_index:
            i = self.indexCombo.findData(saved_index)
            if i >= 0:
                self.indexCombo.setCurrentIndex(i)

    def refreshModels(self):
        """模型/索引下拉：扫描默认目录，并保留上次手动浏览的文件。"""
        saved_pth = cfg.get(cfg.rtcPth) or ""
        saved_index = cfg.get(cfg.rtcIndex) or ""

        self.modelCombo.blockSignals(True)
        self.modelCombo.clear()
        for name in self.engine.models():
            path = os.path.join(self.engine.weightsDir(), name)
            self.modelCombo.addItem(name, userData=path)
        if saved_pth and self.modelCombo.findData(saved_pth) < 0:
            self.modelCombo.addItem(os.path.basename(saved_pth), userData=saved_pth)
        if self.modelCombo.count() == 0:
            self.modelCombo.addItem("（未找到模型，点右侧浏览）", userData="")
        i = self.modelCombo.findData(saved_pth)
        self.modelCombo.setCurrentIndex(i if i >= 0 else 0)
        self.modelCombo.blockSignals(False)

        self.indexCombo.blockSignals(True)
        self.indexCombo.clear()
        for path in self.engine.indices():
            try:
                display = os.path.relpath(path, self.engine.root)
            except ValueError:
                display = path
            self.indexCombo.addItem(display, userData=path)
        if saved_index and self.indexCombo.findData(saved_index) < 0:
            self.indexCombo.addItem(os.path.basename(saved_index), userData=saved_index)
        if self.indexCombo.count() == 0:
            self.indexCombo.addItem("（未找到索引，点右侧浏览）", userData="")
        i = self.indexCombo.findData(saved_index)
        self.indexCombo.setCurrentIndex(i if i >= 0 else 0)
        self.indexCombo.blockSignals(False)

    # ------------------------------------------------------------ 信号
    def _connectSignals(self):
        self.rtc.devicesUpdated.connect(self._onDevices)
        self.rtc.streamStarted.connect(self._onStarted)
        self.rtc.streamStopped.connect(self._onStopped)
        self.rtc.metricsReceived.connect(self._onMetrics)
        self.rtc.logReceived.connect(self._appendLog)
        self.rtc.errorReceived.connect(self._onError)
        self.rtc.streamChanged.connect(self._onStreamChanged)

        self.modelBrowse.clicked.connect(self._onBrowseModel)
        self.indexBrowse.clicked.connect(self._onBrowseIndex)
        # 下拉里直接选也要记住，不能只有点「浏览」才写配置
        self.modelCombo.currentIndexChanged.connect(self._onModelChanged)
        self.indexCombo.currentIndexChanged.connect(self._onIndexChanged)
        self.reloadButton.clicked.connect(self._onReloadDevices)
        self.hostapiCombo.currentTextChanged.connect(self._onHostapiChanged)

        self.startButton.clicked.connect(self._onStart)
        self.stopButton.clicked.connect(self.rtc.stopStream)
        self.clearLogButton.clicked.connect(self.logEdit.clear)
        self.autoScrollCheck.stateChanged.connect(
            lambda: self._persist(cfg.rtcAutoScroll,
                                  self.autoScrollCheck.isChecked()))
        self.overlayCheck.stateChanged.connect(self._onOverlayChanged)
        self.overlaySettingButton.clicked.connect(self._onOverlaySetting)
        # 外观三件套由设置对话框直接写配置，这里只负责把变化推给已开着的叠层
        for item in (cfg.rtcOverlayAlpha, cfg.rtcOverlayColor, cfg.rtcOverlayCorner):
            item.valueChanged.connect(self._syncOverlayStyle)

        # 热更新参数：拖动即生效
        for slider, key, scale in (
            (self.thresholdSlider, "threhold", 1),
            (self.pitchSlider, "pitch", 1),
            (self.formantSlider, "formant", 100),
            (self.indexRateSlider, "index_rate", 100),
            (self.rmsSlider, "rms_mix_rate", 100),
        ):
            slider.valueChanged.connect(
                lambda v, k=key, s=scale: self._onHotParam(k, v / s))
        self.f0Combo.currentIndexChanged.connect(self._onF0Changed)
        self.monitorRadio.toggled.connect(self._onFunctionChanged)
        self.inNoiseCheck.stateChanged.connect(
            lambda: self._onHotParam("I_noise_reduce", self.inNoiseCheck.isChecked()))
        self.outNoiseCheck.stateChanged.connect(
            lambda: self._onHotParam("O_noise_reduce", self.outNoiseCheck.isChecked()))
        self.pvCheck.stateChanged.connect(
            lambda: self._onHotParam("use_pv", self.pvCheck.isChecked()))

        # 性能参数：改了要重新开始
        for slider in (self.blockSlider, self.crossfadeSlider, self.extraSlider):
            slider.valueChanged.connect(self._onPerfChanged)
        self.exclusiveCheck.stateChanged.connect(self._onPerfChanged)
        self.srModelRadio.toggled.connect(self._onPerfChanged)
        self.inputCombo.currentIndexChanged.connect(self._onPerfChanged)
        self.outputCombo.currentIndexChanged.connect(self._onPerfChanged)

    # ------------------------------------------------------------ 事件
    def _onModelChanged(self, _index):
        if not self.modelCombo.signalsBlocked():
            self._persist(cfg.rtcPth, self.modelCombo.currentData() or "")

    def _onIndexChanged(self, _index):
        if not self.indexCombo.signalsBlocked():
            self._persist(cfg.rtcIndex, self.indexCombo.currentData() or "")

    def _onBrowseModel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型", self.engine.weightsDir(), "模型 (*.pth)")
        if path:
            self._persist(cfg.rtcPth, path)
            self.refreshModels()
            i = self.modelCombo.findData(path)
            if i >= 0:
                self.modelCombo.setCurrentIndex(i)

    def _onBrowseIndex(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择索引", os.path.join(self.engine.root, "logs"), "索引 (*.index)")
        if path:
            self._persist(cfg.rtcIndex, path)
            self.refreshModels()
            i = self.indexCombo.findData(path)
            if i >= 0:
                self.indexCombo.setCurrentIndex(i)

    def _onReloadDevices(self):
        """向 worker 请求设备列表（worker 未启动会先拉起，只加载 sounddevice）。"""
        hostapi = self.hostapiCombo.currentText()
        if not self.rtc.requestDevices(hostapi):
            self.stateLabel.setText("引擎不可用")
            setStatus(self.stateLabel, "error")

    def _onDevices(self, event: dict):
        hostapis = event.get("hostapis", [])
        selected = event.get("selected", "")

        self.hostapiCombo.blockSignals(True)
        self.hostapiCombo.clear()
        for name in hostapis:
            self.hostapiCombo.addItem(name)
        if selected in hostapis:
            self.hostapiCombo.setCurrentText(selected)
        self.hostapiCombo.blockSignals(False)

        for combo, names in ((self.inputCombo, event.get("inputs", [])),
                             (self.outputCombo, event.get("outputs", []))):
            combo.blockSignals(True)
            combo.clear()
            for name in names:
                combo.addItem(name, userData=name)
            combo.blockSignals(False)

        saved_in, saved_out = cfg.get(cfg.rtcInput), cfg.get(cfg.rtcOutput)
        i = self.inputCombo.findData(saved_in)
        if i >= 0:
            self.inputCombo.setCurrentIndex(i)
        j = self.outputCombo.findData(saved_out)
        if j >= 0:
            self.outputCombo.setCurrentIndex(j)
        # 上次那台设备这会儿不在列表里（拔了 / 换驱动）就说一声，
        # 别让人以为"明明选过又没了"
        missing = []
        if saved_in and i < 0:
            missing.append("输入：" + saved_in)
        if saved_out and j < 0:
            missing.append("输出：" + saved_out)
        if missing:
            self._appendLog("上次保存的设备现在找不到了，请重新选择 → "
                            + "；".join(missing))

        self._persist(cfg.rtcHostapi, selected or "")
        self._appendLog(
            "设备列表已刷新：%d 个输入 / %d 个输出（%s）"
            % (len(event.get("inputs", [])), len(event.get("outputs", [])), selected)
        )

    def _onHostapiChanged(self, text):
        if text and not self.hostapiCombo.signalsBlocked():
            self._onReloadDevices()

    def _onStart(self):
        params = self.collectParams()
        if not params["pth_path"]:
            self._warn("请先选择模型（.pth）文件")
            return
        if not params["index_path"]:
            self._warn("请先选择索引（.index）文件")
            return
        if not os.path.isfile(params["pth_path"]):
            self._warn("模型文件不存在：%s" % params["pth_path"])
            return
        if not os.path.isfile(params["index_path"]):
            self._warn("索引文件不存在：%s" % params["index_path"])
            return
        if not self.rtc.startStream(params):
            return
        self._slowNoticed = False
        setMuted(self.inferValue)
        self.stateLabel.setText("启动中…（首次加载模型可能需要十几秒）")
        setStatus(self.stateLabel, "warn")

    def _onStarted(self, event: dict):
        self.stateLabel.setText("变声中")
        setStatus(self.stateLabel, "ok")
        self.srLabel.setText(
            "采样率: %s" % event.get("samplerate", "--"))
        self.delayValue.setText(str(event.get("delay_ms", 0)))
        self._lastDelay = int(event.get("delay_ms") or 0)
        self._pushOverlay()
        self._appendLog(
            "变声已开始：采样率 %s Hz，通道 %s，算法延迟 %s ms，设备 %s"
            % (event.get("samplerate"), event.get("channels"),
               event.get("delay_ms"), event.get("device"))
        )

    def _onStopped(self):
        self.stateLabel.setText("未运行")
        setMuted(self.stateLabel)
        self.inferValue.setText("0")
        setMuted(self.inferValue)
        # 停了就是没数了：叠层上摆个「—」，别留下上一次的旧数字假装在跑
        self._lastDelay = 0
        self._lastInfer = 0
        self._pushOverlay()
        self._appendLog("变声已停止")

    def _onStreamChanged(self, running: bool):
        self.startButton.setEnabled(not running)
        self.stopButton.setEnabled(running)

    # ------------------------------------------------------------ 桌面叠层
    def _onOverlayChanged(self):
        self._persist(cfg.rtcOverlay, self.overlayCheck.isChecked())
        self._syncOverlay()

    def _onOverlaySetting(self):
        """叠层外观：底板透明度 / 文字颜色 / 显示位置。"""
        dialog = OverlaySettingDialog(self.window())
        dialog.exec()
        dialog.deleteLater()   # 每次用完就收，别在主窗口下越攒越多

    def _syncOverlay(self):
        """按开关建 / 收叠层。

        ⚠️ 它**不挂在主窗口下**（parent=None）：挂上去就成了主窗口的附属窗，
        系统会在主窗口最小化时把它一起藏起来 —— 那还叠个什么。代价是得自己
        收尾：关主窗口时由 :meth:`closeOverlay` 收掉。
        """
        if self.overlayCheck.isChecked():
            if self._overlay is None:
                self._overlay = MetricOverlay()
            self._syncOverlayStyle()
            self._overlay.setValues(self._lastDelay, self._lastInfer)
            self._overlay.show()
        elif self._overlay is not None:
            self.closeOverlay()
        self.overlaySettingButton.setEnabled(self.overlayCheck.isChecked())

    def _syncOverlayStyle(self, *_args):
        """把配置里的外观（透明度 / 文字颜色 / 贴角）推给叠层。"""
        if self._overlay is None:
            return
        self._overlay.setStyle(cfg.get(cfg.rtcOverlayAlpha),
                               cfg.get(cfg.rtcOverlayColor))
        self._overlay.setCorner(cfg.get(cfg.rtcOverlayCorner))

    def closeOverlay(self):
        """收掉叠层（关窗口 / 关开关都走这里）。"""
        if self._overlay is None:
            return
        self._overlay.close()
        self._overlay.deleteLater()
        self._overlay = None

    def _pushOverlay(self):
        """把当前两个数字推给叠层（开着才推）。

        ⚠️ 这里**不看** ``_windowActive``：叠层本来就是给"主窗口看不见"的
        时候用的，跟着省电一起停掉就白做了。
        """
        if self._overlay is not None:
            self._overlay.setValues(self._lastDelay, self._lastInfer)

    # ------------------------------------------------------------ 空闲省电
    def setActive(self, active: bool):
        """窗口不在前台：不再刷新指标（由 app/idle.py 经主窗口调用）。

        推理耗时 / 算法延迟是**每个音频块**（一秒好几十次）都要改一次的标签，
        没人看的时候为一个看不见的数字反复重绘不划算。日志框那边自己会攒着
        （见 :class:`~app.views.page.IdleLogEdit`）。
        """
        self._windowActive = active

    def _onMetrics(self, infer_ms: int, delay_ms: int):
        # 推理耗时超过"一个音频块"的时长 → 必然来不及出声，直接标红。
        # 这句提醒照旧写进日志（攒着也是攒，回来一样看得到），只有刷数字跳过
        budget = int(round(float(self.blockSlider.value()) / 100.0 * 1000))
        over = budget > 0 and infer_ms > budget
        if over and not self._slowNoticed:
            self._slowNoticed = True
            self._appendLog(
                "推理 %s ms 已超过采样长度 %s ms，CPU 算不过来，声音会卡顿/爆音。"
                "试着：采样长度调到 0.5~1.0 秒、额外推理时长降到 1.0 秒左右、"
                "关掉输入/输出降噪、检索特征占比先设 0。"
                % (infer_ms, budget)
            )

        self._lastInfer = int(infer_ms)
        if delay_ms:
            self._lastDelay = int(delay_ms)
        # 叠层专门给"主窗口看不见"的时候看，所以要在省电 return **之前**推
        self._pushOverlay()

        if not self._windowActive:
            return

        self.inferValue.setText(str(infer_ms))
        if delay_ms:
            self.delayValue.setText(str(delay_ms))
        if over:
            setStatus(self.inferValue, "error")
        else:
            setMuted(self.inferValue)

    def _onError(self, message: str):
        self._appendLog("[错误] " + message)
        self.stateLabel.setText("未运行")
        setStatus(self.stateLabel, "error")
        InfoBar.error(
            title="实时变声", content=message,
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=5000, parent=self,
        )

    def _warn(self, message: str):
        InfoBar.warning(
            title="还差一点", content=message,
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
        )

    def _appendLog(self, text: str):
        self.logEdit.append(str(text))
        if self.autoScrollCheck.isChecked():
            scrollbar = self.logEdit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------ 热更新
    def _onHotParam(self, key, value):
        self.rtc.setParams({key: value})
        self._persistHot(key, value)

    def _onF0Changed(self, _index):
        method = self.f0Combo.currentData()
        if method:
            self._onHotParam("f0method", method)

    def _onFunctionChanged(self):
        function = "im" if self.monitorRadio.isChecked() else "vc"
        self._onHotParam("function", function)

    def _persistHot(self, key, value):
        mapping = {
            "threhold": cfg.rtcThreshold, "pitch": cfg.rtcPitch,
            "formant": cfg.rtcFormant, "index_rate": cfg.rtcIndexRate,
            "rms_mix_rate": cfg.rtcRmsMixRate, "f0method": cfg.rtcF0Method,
            "I_noise_reduce": cfg.rtcINoise, "O_noise_reduce": cfg.rtcONoise,
            "use_pv": cfg.rtcUsePv, "function": cfg.rtcFunction,
        }
        item = mapping.get(key)
        if item is not None:
            cfg.set(item, value, save=False)
            scheduleSave()

    def _persist(self, item, value):
        """随手改一个选项：写进配置并排一次落盘。"""
        cfg.set(item, value, save=False)
        scheduleSave()

    def _onPerfChanged(self, *_args):
        """性能参数 / 设备改动：运行中则提示需要重新开始。"""
        self.collectParams()
        if self.rtc.isStreamRunning:
            self.stateLabel.setText("参数已记录，重新开始后生效")
            setStatus(self.stateLabel, "warn")

    def openOutputDevice(self):
        QDesktopServices.openUrl(QUrl("ms-settings:sound"))
