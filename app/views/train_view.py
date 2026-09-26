"""模型训练页：把 RVC 的整套训练流程内嵌进软件。

对应 RVC WebUI「训练」页的全部功能：
数据切分 → 数据预处理 → F0/Hubert 特征提取 → 模型训练 → 训练索引，
外加一键跑完整套。每一步都跑在 RVC runtime 解释器里（train_worker.py），
日志与进度实时回传，界面不会卡。
"""
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SpinBox,
    SwitchButton,
    TransparentToolButton,
)

from ..config import F0_METHODS, MODEL_VERSIONS, SAMPLE_RATES, cfg
from ..rvc import TRAIN_WORKER_SCRIPT, JobController, RvcEngine
from ..theme import applyMuted, setStatus
from .page import (
    addButtons,
    applyPageLayout,
    makeCard,
    makeFooterRow,
    makeGrid,
    makeHeader,
    makeLogCard,
    makeProgressBar,
    setupScrollPage,
)

F0_LABELS = {
    "pm": "pm（快，略糙）",
    "rmvpe": "rmvpe（推荐）",
    "fcpe": "fcpe（快且准）",
}


class TrainInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("trainInterface")

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.view)

        self.engine = RvcEngine(self)
        self.trainer = JobController(TRAIN_WORKER_SCRIPT, self.engine, self)

        self._buildHeader()
        self._buildSliceCard()
        self._buildDatasetCard()
        self._buildParamCard()
        self._buildRunCard()
        self._buildLogCard()

        applyPageLayout(self.vBoxLayout)
        for widget in (self.headerRow, self.sliceCard, self.datasetCard,
                       self.paramCard, self.runCard, self.logCard):
            self.vBoxLayout.addWidget(widget)
        self.vBoxLayout.addStretch(1)

        setupScrollPage(self, self.view)

        # 先填回上次的选择再接信号（同上：填控件触发的信号会把还没填到的
        # 参数以默认值写进配置）
        self._restoreFromConfig()
        self._connectSignals()

    # ------------------------------------------------------------ 界面
    def _buildHeader(self):
        self.headerRow, _header = makeHeader(self.view, "模型训练 · RVC")

    def _card(self, title):
        return makeCard(self.view, title)

    def _buildSliceCard(self):
        """step0：把长音频切成小段（可选，但训练集太脏时很有用）。"""
        self.sliceCard, layout = self._card("数据切分（可选）")

        self.sliceInput = LineEdit(self.sliceCard)
        self.sliceInput.setPlaceholderText("待切分的音频文件或文件夹")
        self.sliceBrowse = PushButton(FIF.FOLDER, "选择", self.sliceCard)
        self.sliceOutput = LineEdit(self.sliceCard)
        self.sliceOutput.setPlaceholderText("切分后输出目录（默认放在源文件同级的 sliced/）")
        self.sliceOutBrowse = PushButton(FIF.FOLDER, "选择", self.sliceCard)

        self.dbSpin = self._spin(-60, 0, -40)
        self.minLenSpin = self._spin(1000, 20000, 5000)
        self.minIntervalSpin = self._spin(100, 2000, 300)
        self.silKeptSpin = self._spin(0, 2000, 500)

        grid = makeGrid()
        rows = [
            ("输入音频", self.sliceInput, self.sliceBrowse),
            ("输出目录", self.sliceOutput, self.sliceOutBrowse),
        ]
        for row, (label, edit, button) in enumerate(rows):
            grid.addWidget(BodyLabel(label, self.sliceCard), row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(button, row, 2)
        grid.addWidget(BodyLabel("静音阈值 dB", self.sliceCard), 2, 0)
        grid.addWidget(self.dbSpin, 2, 1)
        grid.addWidget(BodyLabel("最短切片 ms", self.sliceCard), 2, 2)
        grid.addWidget(self.minLenSpin, 2, 3)
        grid.addWidget(BodyLabel("最短静音 ms", self.sliceCard), 3, 0)
        grid.addWidget(self.minIntervalSpin, 3, 1)
        grid.addWidget(BodyLabel("保留静音 ms", self.sliceCard), 3, 2)
        grid.addWidget(self.silKeptSpin, 3, 3)

        self.sliceButton = PushButton(FIF.CUT, "开始切分", self.sliceCard)
        actionRow = QHBoxLayout()
        addButtons(actionRow, self.sliceButton)
        actionRow.addStretch(1)

        layout.addLayout(grid)
        layout.addLayout(actionRow)

    def _buildDatasetCard(self):
        """step1：实验名 + 训练集 + 采样率/版本/音高引导。"""
        self.datasetCard, layout = self._card("数据集与模型设置")

        self.expEdit = LineEdit(self.datasetCard)
        self.expEdit.setPlaceholderText("实验名，如 my_voice（产物在 logs/<实验名>/）")
        self.expCombo = ComboBox(self.datasetCard)
        self.expCombo.setMinimumWidth(180)
        self.expCombo.setPlaceholderText("已有实验")

        self.datasetEdit = LineEdit(self.datasetCard)
        self.datasetEdit.setPlaceholderText("训练集目录（里面放干声，并建 2_Name/ 之类的子目录）")
        self.datasetBrowse = PushButton(FIF.FOLDER, "选择", self.datasetCard)

        self.srCombo = ComboBox(self.datasetCard)
        for value in SAMPLE_RATES:
            self.srCombo.addItem(value, userData=value)
        self.versionCombo = ComboBox(self.datasetCard)
        for value in MODEL_VERSIONS:
            self.versionCombo.addItem(value, userData=value)
        self.f0Combo = ComboBox(self.datasetCard)
        for method in F0_METHODS:
            self.f0Combo.addItem(F0_LABELS.get(method, method), userData=method)
        self.f0Switch = SwitchButton(self.datasetCard)
        self.f0Switch.setChecked(True)

        grid = makeGrid()
        grid.addWidget(BodyLabel("实验名", self.datasetCard), 0, 0)
        grid.addWidget(self.expEdit, 0, 1)
        grid.addWidget(self.expCombo, 0, 2)
        grid.addWidget(BodyLabel("训练集目录", self.datasetCard), 1, 0)
        grid.addWidget(self.datasetEdit, 1, 1)
        grid.addWidget(self.datasetBrowse, 1, 2)
        grid.addWidget(BodyLabel("采样率", self.datasetCard), 2, 0)
        grid.addWidget(self.srCombo, 2, 1)
        grid.addWidget(BodyLabel("模型版本", self.datasetCard), 2, 2)
        grid.addWidget(self.versionCombo, 2, 3)
        grid.addWidget(BodyLabel("音高算法", self.datasetCard), 3, 0)
        grid.addWidget(self.f0Combo, 3, 1)
        grid.addWidget(BodyLabel("音高引导", self.datasetCard), 3, 2)
        grid.addWidget(self.f0Switch, 3, 3, Qt.AlignLeft)
        layout.addLayout(grid)

    def _buildParamCard(self):
        """step2：训练超参 + 底模 + 硬件。"""
        self.paramCard, layout = self._card("训练参数")

        self.epochSpin = self._spin(1, 1000, 100)
        self.saveSpin = self._spin(1, 100, 10)
        self.batchSpin = self._spin(1, 64, 4)
        self.workerSpin = self._spin(1, 32, 4)
        self.gpuEdit = LineEdit(self.paramCard)
        self.gpuEdit.setPlaceholderText("训练卡号，多卡写 0-1")
        self.gpuEdit.setText("0")
        self.preGCombo = ComboBox(self.paramCard)
        self.preGCombo.setMinimumWidth(220)
        self.preDCombo = ComboBox(self.paramCard)
        self.preDCombo.setMinimumWidth(220)

        self.cacheSwitch = SwitchButton(self.paramCard)
        self.latestSwitch = SwitchButton(self.paramCard)
        self.everySwitch = SwitchButton(self.paramCard)
        self.cacheSwitch.setChecked(True)
        self.latestSwitch.setChecked(True)

        grid = makeGrid()
        grid.addWidget(BodyLabel("总轮数", self.paramCard), 0, 0)
        grid.addWidget(self.epochSpin, 0, 1)
        grid.addWidget(BodyLabel("保存间隔", self.paramCard), 0, 2)
        grid.addWidget(self.saveSpin, 0, 3)
        grid.addWidget(BodyLabel("批次大小", self.paramCard), 1, 0)
        grid.addWidget(self.batchSpin, 1, 1)
        grid.addWidget(BodyLabel("并行进程数", self.paramCard), 1, 2)
        grid.addWidget(self.workerSpin, 1, 3)
        grid.addWidget(BodyLabel("显卡编号", self.paramCard), 2, 0)
        grid.addWidget(self.gpuEdit, 2, 1)
        grid.addWidget(BodyLabel("底模 G", self.paramCard), 3, 0)
        grid.addWidget(self.preGCombo, 3, 1, 1, 2)
        grid.addWidget(BodyLabel("底模 D", self.paramCard), 4, 0)
        grid.addWidget(self.preDCombo, 4, 1, 1, 2)
        layout.addLayout(grid)

        switchRow = QHBoxLayout()
        for label, widget in (("缓存数据集到显存", self.cacheSwitch),
                              ("只保留最新权重", self.latestSwitch),
                              ("每轮都保存", self.everySwitch)):
            switchRow.addWidget(BodyLabel(label, self.paramCard))
            switchRow.addWidget(widget)
            switchRow.addSpacing(20)
        switchRow.addStretch(1)
        layout.addLayout(switchRow)

    def _buildRunCard(self):
        self.runCard, layout = makeCard(self.view, "执行")

        # 分步按钮一行：预处理 → 特征提取 → 训练 → 索引
        stepRow = QHBoxLayout()
        self.preprocessButton = PushButton(FIF.PLAY, "数据预处理", self.runCard)
        self.extractButton = PushButton(FIF.PLAY, "特征提取", self.runCard)
        self.trainButton = PrimaryPushButton(FIF.PLAY, "训练模型", self.runCard)
        self.indexButton = PushButton(FIF.PLAY, "训练索引", self.runCard)
        addButtons(stepRow, self.preprocessButton, self.extractButton,
                   self.trainButton, self.indexButton)
        stepRow.addStretch(1)

        self.progressBar = makeProgressBar(self.runCard)

        # 底栏与任务页一致：状态在左、按钮在右
        actionRow, self.statusLabel = makeFooterRow(self.runCard)
        self.allButton = PrimaryPushButton(FIF.ROBOT, "一键训练", self.runCard)
        self.stopButton = PushButton(FIF.PAUSE, "停止", self.runCard)
        self.stopButton.setEnabled(False)
        addButtons(actionRow, self.stopButton, self.allButton)

        hint = CaptionLabel(
            "一键训练会依次跑：预处理 → 特征提取 → 训练 → 索引，中途可随时停止。",
            self.runCard)
        applyMuted(hint)

        layout.addLayout(stepRow)
        layout.addWidget(self.progressBar)
        layout.addLayout(actionRow)
        layout.addWidget(hint)

    def _buildLogCard(self):
        self.logCard, self.logEdit, titleRow = makeLogCard(
            self.view, "训练日志会实时显示在这里")
        self.openLogButton = PushButton(FIF.FOLDER, "打开 logs", self.logCard)
        self.clearLogButton = TransparentToolButton(FIF.DELETE, self.logCard)
        addButtons(titleRow, self.openLogButton, self.clearLogButton)

    @staticmethod
    def _spin(minimum, maximum, value):
        spin = SpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(1)
        spin.setValue(value)
        return spin

    # ------------------------------------------------------------ 配置
    def _restoreFromConfig(self):
        self.expEdit.setText(cfg.get(cfg.trainExpDir) or "")
        self.datasetEdit.setText(cfg.get(cfg.trainDatasetDir) or "")
        self._select(self.srCombo, cfg.get(cfg.trainSampleRate))
        self._select(self.versionCombo, cfg.get(cfg.trainVersion))
        self._select(self.f0Combo, cfg.get(cfg.trainF0Method))
        self.f0Switch.setChecked(bool(cfg.get(cfg.trainF0)))
        self.epochSpin.setValue(int(cfg.get(cfg.trainTotalEpoch)))
        self.saveSpin.setValue(int(cfg.get(cfg.trainSaveEpoch)))
        self.batchSpin.setValue(int(cfg.get(cfg.trainBatch)))
        self.workerSpin.setValue(int(cfg.get(cfg.trainWorkers)))
        self.gpuEdit.setText(cfg.get(cfg.trainGpus) or "0")
        self.cacheSwitch.setChecked(bool(cfg.get(cfg.trainCache)))
        self.latestSwitch.setChecked(bool(cfg.get(cfg.trainLatest)))
        self.everySwitch.setChecked(bool(cfg.get(cfg.trainSaveEvery)))
        self.refreshExperiments()
        self.refreshPretrained()

    @staticmethod
    def _select(combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def refreshPretrained(self):
        """底模下拉：assets/pretrained[_v2] 下的 .pth。"""
        models = self.engine.pretrainedModels()
        for combo, saved in ((self.preGCombo, cfg.get(cfg.trainPretrainedG)),
                             (self.preDCombo, cfg.get(cfg.trainPretrainedD))):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("（使用默认底模）", userData="")
            for path in models:
                combo.addItem(os.path.basename(path), userData=path)
            index = combo.findData(saved or "")
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def refreshExperiments(self):
        names = self.engine.experiments()
        self.expCombo.blockSignals(True)
        self.expCombo.clear()
        for name in names:
            self.expCombo.addItem(name, userData=name)
        self.expCombo.blockSignals(False)

    def collectPayload(self) -> dict:
        """把界面参数收成一份 payload，顺手写回配置。"""
        payload = {
            "exp_dir": self.expEdit.text().strip(),
            "dataset_dir": self.datasetEdit.text().strip(),
            "sample_rate": self.srCombo.currentData() or "48k",
            "version": self.versionCombo.currentData() or "v2",
            "f0method": self.f0Combo.currentData() or "rmvpe",
            "if_f0": self.f0Switch.isChecked(),
            "total_epoch": self.epochSpin.value(),
            "save_epoch": self.saveSpin.value(),
            "batch_size": self.batchSpin.value(),
            "workers": self.workerSpin.value(),
            "gpus": self.gpuEdit.text().strip() or "0",
            "cache_gpu": self.cacheSwitch.isChecked(),
            "save_latest": self.latestSwitch.isChecked(),
            "save_every": self.everySwitch.isChecked(),
            "pretrained_g": self.preGCombo.currentData() or "",
            "pretrained_d": self.preDCombo.currentData() or "",
            "dml": bool(cfg.get(cfg.rvcUseDml)),
        }
        cfg.set(cfg.trainExpDir, payload["exp_dir"], save=False)
        cfg.set(cfg.trainDatasetDir, payload["dataset_dir"], save=False)
        cfg.set(cfg.trainSampleRate, payload["sample_rate"], save=False)
        cfg.set(cfg.trainVersion, payload["version"], save=False)
        cfg.set(cfg.trainF0Method, payload["f0method"], save=False)
        cfg.set(cfg.trainF0, payload["if_f0"], save=False)
        cfg.set(cfg.trainTotalEpoch, payload["total_epoch"], save=False)
        cfg.set(cfg.trainSaveEpoch, payload["save_epoch"], save=False)
        cfg.set(cfg.trainBatch, payload["batch_size"], save=False)
        cfg.set(cfg.trainWorkers, payload["workers"], save=False)
        cfg.set(cfg.trainGpus, payload["gpus"], save=False)
        cfg.set(cfg.trainCache, payload["cache_gpu"], save=False)
        cfg.set(cfg.trainLatest, payload["save_latest"], save=False)
        cfg.set(cfg.trainSaveEvery, payload["save_every"], save=False)
        cfg.set(cfg.trainPretrainedG, payload["pretrained_g"], save=False)
        cfg.set(cfg.trainPretrainedD, payload["pretrained_d"], save=False)
        return payload

    def slicePayload(self) -> dict:
        return {
            "input_dir": self.sliceInput.text().strip(),
            "out_dir": self.sliceOutput.text().strip(),
            "db_thresh": self.dbSpin.value(),
            "min_length": self.minLenSpin.value(),
            "min_interval": self.minIntervalSpin.value(),
            "max_sil_kept": self.silKeptSpin.value(),
        }

    # ------------------------------------------------------------ 信号
    def _connectSignals(self):
        self.trainer.logReceived.connect(self._appendLog)
        self.trainer.stepChanged.connect(self._onStep)
        self.trainer.finished.connect(self._onFinished)
        self.trainer.failed.connect(self._onFailed)
        self.trainer.stateChanged.connect(self._onStateChanged)

        self.sliceBrowse.clicked.connect(lambda: self._browse(self.sliceInput, True))
        self.sliceOutBrowse.clicked.connect(lambda: self._browse(self.sliceOutput))
        self.datasetBrowse.clicked.connect(lambda: self._browse(self.datasetEdit))
        self.sliceButton.clicked.connect(lambda: self._start("slice"))
        self.preprocessButton.clicked.connect(lambda: self._start("preprocess"))
        self.extractButton.clicked.connect(lambda: self._start("extract"))
        self.trainButton.clicked.connect(lambda: self._start("train"))
        self.indexButton.clicked.connect(lambda: self._start("index"))
        self.allButton.clicked.connect(lambda: self._start("all"))
        self.stopButton.clicked.connect(self.trainer.stop)
        self.openLogButton.clicked.connect(self._onOpenLogs)
        self.clearLogButton.clicked.connect(self.logEdit.clear)
        self.expCombo.currentIndexChanged.connect(self._onExperimentPicked)
        self.preGCombo.currentIndexChanged.connect(self.collectPayload)
        self.preDCombo.currentIndexChanged.connect(self.collectPayload)

    # ------------------------------------------------------------ 动作
    def _browse(self, edit, allowFile=False):
        if allowFile:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择音频文件", "", "音频文件 (*.wav *.mp3 *.flac *.ogg *.m4a)")
        else:
            path = QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
        if path:
            edit.setText(path)
            self.collectPayload()

    def _onExperimentPicked(self, index):
        if index >= 0 and not self.expCombo.signalsBlocked():
            self.expEdit.setText(self.expCombo.currentData() or "")
            self.collectPayload()

    def _start(self, job):
        if job == "slice":
            payload = self.slicePayload()
        else:
            payload = self.collectPayload()
            if not payload["exp_dir"]:
                self._warn("请先填写实验名")
                return
        self.statusLabel.setText("启动中…")
        setStatus(self.statusLabel, "info")
        self.trainer.start(job, payload)

    def _onOpenLogs(self):
        path = self.engine.logsDir()
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _warn(self, message):
        InfoBar.warning(
            title="还差一点", content=message,
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
        )

    # ------------------------------------------------------------ 回调
    def _appendLog(self, text):
        self.logEdit.append(str(text))

    def _onStep(self, name, index, total):
        self.statusLabel.setText("[%s/%s] %s" % (index, total, name))
        setStatus(self.statusLabel, "info")
        if total:
            self.progressBar.setValue(int((index - 1) * 100 / total))

    def _onFinished(self, ok):
        self.progressBar.setValue(100 if ok else 0)
        if ok:
            self.statusLabel.setText("完成")
            setStatus(self.statusLabel, "ok")
            self.refreshExperiments()
            InfoBar.success(
                title="训练任务完成", content="产物已写入 logs 目录",
                orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
            )
        else:
            self.statusLabel.setText("已停止或失败，详见日志")
            setStatus(self.statusLabel, "error")

    def _onFailed(self, message):
        if message:
            self._appendLog("[失败] " + message)
        self.statusLabel.setText("失败")
        setStatus(self.statusLabel, "error")
        InfoBar.error(
            title="任务未能完成", content=message or "请查看运行日志",
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=4000, parent=self,
        )

    def _onStateChanged(self, running):
        for button in (self.sliceButton, self.preprocessButton, self.extractButton,
                       self.trainButton, self.indexButton, self.allButton):
            button.setEnabled(not running)
        self.stopButton.setEnabled(running)
        if running:
            self.progressBar.setValue(0)

    def refreshEngine(self):
        """主窗口切到本页时调用：重新扫描实验与底模。"""
        self.refreshExperiments()
        self.refreshPretrained()
