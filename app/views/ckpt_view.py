"""模型管理页：对应 RVC WebUI 的「ckpt 处理」。

四件事都能在软件里做：看模型信息、改模型名/说明、提取小模型（去掉训练状态，
体积砍半）、把两个模型按权重融合成一个。
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
    DoubleSpinBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SwitchButton,
    TextEdit,
    TransparentToolButton,
)

from ..config import MODEL_VERSIONS, SAMPLE_RATES, cfg
from ..rvc import CKPT_WORKER_SCRIPT, JobController, RvcEngine
from ..theme import applyMuted, setStatus
from .page import (
    addButtons,
    applyPageLayout,
    makeCard,
    makeFooterRow,
    makeGrid,
    makeHeader,
    makeLogCard,
    setupScrollPage,
)

MODEL_FILTER = "模型文件 (*.pth);;所有文件 (*.*)"


class CkptInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ckptInterface")

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.view)

        self.engine = RvcEngine(self)
        self.worker = JobController(CKPT_WORKER_SCRIPT, self.engine, self,
                                    feature="ckpt")

        self._buildHeader()
        self._buildInfoCard()
        self._buildEditCard()
        self._buildMergeCard()
        self._buildLogCard()

        applyPageLayout(self.vBoxLayout)
        for widget in (self.headerRow, self.infoCard, self.editCard,
                       self.mergeCard, self.logCard):
            self.vBoxLayout.addWidget(widget)
        self.vBoxLayout.addStretch(1)

        setupScrollPage(self, self.view)

        # 先填回上次的选择再接信号（同上：填控件触发的信号会把还没填到的
        # 参数以默认值写进配置）
        self._restoreFromConfig()
        self._connectSignals()
        self.refreshModels()

    # ------------------------------------------------------------ 界面
    def _buildHeader(self):
        self.headerRow, header = makeHeader(
            self.view, "模型管理 · ckpt 处理")
        self.refreshButton = TransparentToolButton(FIF.SYNC, self.headerRow)
        self.refreshButton.setToolTip("重新扫描可用模型")
        header.addWidget(self.refreshButton)

    def _buildInfoCard(self):
        self.infoCard, layout = makeCard(self.view, "模型信息")

        self.modelCombo = ComboBox(self.infoCard)
        self.modelCombo.setMinimumWidth(280)
        self.browseButton = PushButton(FIF.FOLDER, "浏览", self.infoCard)
        self.infoButton = PrimaryPushButton(FIF.SEARCH, "读取信息", self.infoCard)

        row = QHBoxLayout()
        row.addWidget(BodyLabel("模型", self.infoCard))
        row.addWidget(self.modelCombo)
        addButtons(row, self.browseButton)
        row.addStretch(1)
        row.addWidget(self.infoButton)

        self.infoEdit = TextEdit(self.infoCard)
        self.infoEdit.setReadOnly(True)
        self.infoEdit.setFixedHeight(150)
        self.infoEdit.setPlaceholderText("点「读取信息」查看模型的采样率、版本、是否音高引导")

        layout.addLayout(row)
        layout.addWidget(self.infoEdit)

    def _buildEditCard(self):
        """改信息 + 提取小模型。"""
        self.editCard, layout = makeCard(self.view, "修改信息 / 提取小模型")

        self.nameEdit = LineEdit(self.editCard)
        self.nameEdit.setPlaceholderText("新模型名（如 my_voice_v2.pth）")
        self.infoLineEdit = LineEdit(self.editCard)
        self.infoLineEdit.setPlaceholderText("模型说明，随便写点什么方便以后认")
        self.srCombo = ComboBox(self.editCard)
        for value in SAMPLE_RATES:
            self.srCombo.addItem(value, userData=value)
        self.versionCombo = ComboBox(self.editCard)
        for value in MODEL_VERSIONS:
            self.versionCombo.addItem(value, userData=value)
        self.f0Switch = SwitchButton(self.editCard)
        self.f0Switch.setChecked(True)

        grid = makeGrid()
        grid.addWidget(BodyLabel("新模型名", self.editCard), 0, 0)
        grid.addWidget(self.nameEdit, 0, 1)
        grid.addWidget(BodyLabel("模型说明", self.editCard), 0, 2)
        grid.addWidget(self.infoLineEdit, 0, 3)
        grid.addWidget(BodyLabel("采样率", self.editCard), 1, 0)
        grid.addWidget(self.srCombo, 1, 1)
        grid.addWidget(BodyLabel("版本", self.editCard), 1, 2)
        grid.addWidget(self.versionCombo, 1, 3)
        grid.addWidget(BodyLabel("音高引导", self.editCard), 2, 0)
        grid.addWidget(self.f0Switch, 2, 1, Qt.AlignLeft)

        actionRow = QHBoxLayout()
        self.changeButton = PushButton(FIF.EDIT, "修改信息", self.editCard)
        self.extractButton = PushButton(FIF.CLOUD_DOWNLOAD, "提取小模型", self.editCard)
        self.openWeightButton = PushButton(FIF.FOLDER, "打开模型目录", self.editCard)
        addButtons(actionRow, self.changeButton, self.extractButton,
                   self.openWeightButton)
        actionRow.addStretch(1)

        hint = CaptionLabel(
            "提取小模型会去掉训练状态，体积大约减半，方便分发（不能再续训）。",
            self.editCard)
        applyMuted(hint)

        layout.addLayout(grid)
        layout.addLayout(actionRow)
        layout.addWidget(hint)

    def _buildMergeCard(self):
        self.mergeCard, layout = makeCard(self.view, "模型融合")

        self.mergeACombo = ComboBox(self.mergeCard)
        self.mergeBCombo = ComboBox(self.mergeCard)
        self.alphaSpin = DoubleSpinBox(self.mergeCard)
        self.alphaSpin.setRange(0.0, 1.0)
        self.alphaSpin.setSingleStep(0.05)
        self.alphaSpin.setValue(0.5)
        self.mergeName = LineEdit(self.mergeCard)
        self.mergeName.setPlaceholderText("融合后的模型名（如 merged.pth）")

        grid = makeGrid()
        grid.addWidget(BodyLabel("模型 A", self.mergeCard), 0, 0)
        grid.addWidget(self.mergeACombo, 0, 1)
        grid.addWidget(BodyLabel("模型 B", self.mergeCard), 0, 2)
        grid.addWidget(self.mergeBCombo, 0, 3)
        grid.addWidget(BodyLabel("混合比例", self.mergeCard), 1, 0)
        grid.addWidget(self.alphaSpin, 1, 1)
        grid.addWidget(BodyLabel("保存名", self.mergeCard), 1, 2)
        grid.addWidget(self.mergeName, 1, 3)

        # 底栏与任务页一致：状态在左、按钮在右
        actionRow, self.statusLabel = makeFooterRow(self.mergeCard)
        self.mergeButton = PrimaryPushButton(FIF.MIX_VOLUMES, "开始融合", self.mergeCard)
        actionRow.addWidget(self.mergeButton)

        hint = CaptionLabel(
            "混合比例 0.5 = 各占一半；滑到 1 就完全变成模型 A。两个模型必须是同采样率、同版本。",
            self.mergeCard)
        applyMuted(hint)

        layout.addLayout(grid)
        layout.addLayout(actionRow)
        layout.addWidget(hint)

    def _buildLogCard(self):
        self.logCard, self.logEdit, titleRow = makeLogCard(
            self.view, "操作结果会显示在这里")
        self.clearLogButton = TransparentToolButton(FIF.DELETE, self.logCard)
        titleRow.addWidget(self.clearLogButton)

    # ------------------------------------------------------------ 配置
    def _restoreFromConfig(self):
        self.nameEdit.setText(cfg.get(cfg.ckptName) or "")
        self.infoLineEdit.setText(cfg.get(cfg.ckptInfo) or "")
        index = self.srCombo.findData(cfg.get(cfg.ckptSampleRate))
        self.srCombo.setCurrentIndex(index if index >= 0 else 0)
        index = self.versionCombo.findData(cfg.get(cfg.ckptVersion))
        self.versionCombo.setCurrentIndex(index if index >= 0 else 0)
        self.f0Switch.setChecked(bool(cfg.get(cfg.ckptF0)))
        self.mergeName.setText(cfg.get(cfg.ckptName) or "")
        self.alphaSpin.setValue(float(cfg.get(cfg.ckptAlpha) or 0.5))

    def refreshModels(self):
        """模型下拉：assets/weights 下的成品 + logs/** 的训练产物。"""
        paths = self.engine.trainedModels()
        try:
            paths += [os.path.join(self.engine.weightsDir(), name)
                      for name in self.engine.models()]
        except Exception:
            pass
        paths = sorted(set(paths))

        saved = cfg.get(cfg.ckptModel) or ""
        for combo in (self.modelCombo, self.mergeACombo, self.mergeBCombo):
            combo.blockSignals(True)
            combo.clear()
            for path in paths:
                combo.addItem(self._display(path), userData=path)
            combo.blockSignals(False)
        self._select(self.modelCombo, saved)
        self._select(self.mergeACombo, cfg.get(cfg.ckptMergeA) or "")
        self._select(self.mergeBCombo, cfg.get(cfg.ckptMergeB) or "")

    @staticmethod
    def _display(path):
        """logs/<exp>/G_xx.pth → exp/G_xx.pth，下拉里好认。"""
        try:
            parent = os.path.basename(os.path.dirname(path))
            return "%s / %s" % (parent, os.path.basename(path))
        except Exception:
            return path

    @staticmethod
    def _select(combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _currentModel(self) -> str:
        return self.modelCombo.currentData() or ""

    def collectPayload(self) -> dict:
        payload = {
            "path": self._currentModel(),
            "name": self.nameEdit.text().strip(),
            "info": self.infoLineEdit.text().strip(),
            "sr": self.srCombo.currentData() or "48k",
            "version": self.versionCombo.currentData() or "v2",
            "if_f0": 1 if self.f0Switch.isChecked() else 0,
            "path_a": self.mergeACombo.currentData() or "",
            "path_b": self.mergeBCombo.currentData() or "",
            "alpha": float(self.alphaSpin.value()),
        }
        cfg.set(cfg.ckptModel, payload["path"], save=False)
        cfg.set(cfg.ckptName, payload["name"], save=False)
        cfg.set(cfg.ckptInfo, payload["info"], save=False)
        cfg.set(cfg.ckptSampleRate, payload["sr"], save=False)
        cfg.set(cfg.ckptVersion, payload["version"], save=False)
        cfg.set(cfg.ckptF0, bool(self.f0Switch.isChecked()), save=False)
        cfg.set(cfg.ckptMergeA, payload["path_a"], save=False)
        cfg.set(cfg.ckptMergeB, payload["path_b"], save=False)
        cfg.set(cfg.ckptAlpha, payload["alpha"], save=False)
        return payload

    # ------------------------------------------------------------ 信号
    def _connectSignals(self):
        self.worker.logReceived.connect(self._appendLog)
        self.worker.resultReceived.connect(self._onResult)
        self.worker.finished.connect(self._onFinished)
        self.worker.failed.connect(self._onFailed)
        self.worker.stateChanged.connect(self._onStateChanged)

        self.refreshButton.clicked.connect(self.refreshModels)
        self.browseButton.clicked.connect(self._onBrowse)
        self.infoButton.clicked.connect(lambda: self._start("info"))
        self.changeButton.clicked.connect(lambda: self._start("change_info"))
        self.extractButton.clicked.connect(lambda: self._start("extract"))
        self.mergeButton.clicked.connect(lambda: self._start("merge"))
        self.openWeightButton.clicked.connect(self._onOpenWeights)
        self.clearLogButton.clicked.connect(self.logEdit.clear)
        self.modelCombo.currentIndexChanged.connect(self.collectPayload)

    # ------------------------------------------------------------ 动作
    def _onBrowse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型", self.engine.weightsDir(), MODEL_FILTER)
        if not path:
            return
        self.modelCombo.addItem(self._display(path), userData=path)
        self._select(self.modelCombo, path)
        self.collectPayload()

    def _onOpenWeights(self):
        path = self.engine.weightsDir()
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _start(self, job):
        payload = self.collectPayload()
        if job == "extract" and not payload["name"]:
            self._warn("提取小模型前请填写新模型名（需带 .pth）")
            return
        if job != "merge" and not payload["path"]:
            self._warn("请先选择模型")
            return
        self.statusLabel.setText("执行中…")
        setStatus(self.statusLabel, "info")
        self.worker.start(job, payload)

    def _warn(self, message):
        InfoBar.warning(
            title="还差一点", content=message,
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
        )

    # ------------------------------------------------------------ 回调
    def _appendLog(self, text):
        self.logEdit.append(str(text))

    def _onResult(self, payload):
        if isinstance(payload, dict) and payload.get("text"):
            self.infoEdit.setPlainText(str(payload["text"]))

    def _onFinished(self, ok):
        self.statusLabel.setText("完成" if ok else "失败，详见日志")
        setStatus(self.statusLabel, "ok" if ok else "error")
        if ok:
            self.refreshModels()

    def _onFailed(self, message):
        if message:
            self._appendLog("[失败] " + message)
        self.statusLabel.setText("失败")
        setStatus(self.statusLabel, "error")

    def _onStateChanged(self, running):
        for button in (self.infoButton, self.changeButton, self.extractButton,
                       self.mergeButton):
            button.setEnabled(not running)

    def refreshEngine(self):
        """主窗口切到本页时调用。"""
        self.refreshModels()
