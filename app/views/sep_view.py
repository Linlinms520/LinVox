"""人声伴奏分离 & 去混响页（PyMSS）。

对应 RVC WebUI 的「人声伴奏分离&去混响」：用 PyMSS 把干声/伴奏/混响分开，
分离结果可以直接丢进「模型训练」当训练集，或拿去「文件变声」二次处理。
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
    TransparentToolButton,
)

from ..config import OUTPUT_FORMATS, cfg
from ..rvc import SEP_WORKER_SCRIPT, AUDIO_FILTER, JobController, RvcEngine
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


class SepInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sepInterface")

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.view)

        self.engine = RvcEngine(self)
        self.separator = JobController(SEP_WORKER_SCRIPT, self.engine, self)

        self._buildHeader()
        self._buildSettingCard()
        self._buildRunCard()
        self._buildLogCard()

        applyPageLayout(self.vBoxLayout)
        for widget in (self.headerRow, self.settingCard, self.runCard,
                       self.logCard):
            self.vBoxLayout.addWidget(widget)
        self.vBoxLayout.addStretch(1)

        setupScrollPage(self, self.view)

        # 先填回上次的选择再接信号：否则填「输出格式」那一步就会触发
        # collectPayload()，把还没填好的模型下拉（此时是空的）写进配置，
        # 上次的模型选择就丢了（与实时变声 DirectML 那个坑同源）
        self._restoreFromConfig()
        self._connectSignals()

    # ------------------------------------------------------------ 界面
    def _buildHeader(self):
        self.headerRow, _header = makeHeader(
            self.view, "人声伴奏分离 & 去混响 · PyMSS")

    def _buildSettingCard(self):
        self.settingCard, layout = makeCard(self.view, "分离设置")

        self.modelCombo = ComboBox(self.settingCard)
        self.inputEdit = LineEdit(self.settingCard)
        self.inputEdit.setPlaceholderText("待处理的音频文件或文件夹")
        self.fileButton = PushButton(FIF.MUSIC, "选文件", self.settingCard)
        self.folderButton = PushButton(FIF.FOLDER, "选文件夹", self.settingCard)
        self.vocalEdit = LineEdit(self.settingCard)
        self.vocalEdit.setPlaceholderText("人声输出目录（留空则不保存人声）")
        self.vocalBrowse = PushButton(FIF.FOLDER, "选择", self.settingCard)
        self.instEdit = LineEdit(self.settingCard)
        self.instEdit.setPlaceholderText("伴奏输出目录（留空则不保存伴奏）")
        self.instBrowse = PushButton(FIF.FOLDER, "选择", self.settingCard)
        self.formatCombo = ComboBox(self.settingCard)
        for value in OUTPUT_FORMATS:
            self.formatCombo.addItem(value, userData=value)

        grid = makeGrid()
        grid.addWidget(BodyLabel("分离模型", self.settingCard), 0, 0)
        grid.addWidget(self.modelCombo, 0, 1, 1, 3)
        grid.addWidget(BodyLabel("输入", self.settingCard), 1, 0)
        grid.addWidget(self.inputEdit, 1, 1, 1, 2)
        buttonRow = QHBoxLayout()
        buttonRow.addWidget(self.fileButton)
        buttonRow.addWidget(self.folderButton)
        grid.addLayout(buttonRow, 1, 3)
        grid.addWidget(BodyLabel("人声输出", self.settingCard), 2, 0)
        grid.addWidget(self.vocalEdit, 2, 1, 1, 2)
        grid.addWidget(self.vocalBrowse, 2, 3)
        grid.addWidget(BodyLabel("伴奏输出", self.settingCard), 3, 0)
        grid.addWidget(self.instEdit, 3, 1, 1, 2)
        grid.addWidget(self.instBrowse, 3, 3)
        grid.addWidget(BodyLabel("输出格式", self.settingCard), 4, 0)
        grid.addWidget(self.formatCombo, 4, 1)

        hint = CaptionLabel(
            "「去伴奏」保留人声、「去混响」保留伴奏与干净人声，按需各跑一遍。",
            self.settingCard)
        applyMuted(hint)

        layout.addLayout(grid)
        layout.addWidget(hint)

    def _buildRunCard(self):
        self.runCard, layout = makeCard(self.view, "执行")

        self.progressBar = makeProgressBar(self.runCard)

        # 底栏与任务页一致：状态在左、按钮在右
        actionRow, self.statusLabel = makeFooterRow(self.runCard)
        self.startButton = PrimaryPushButton(FIF.PLAY, "开始分离", self.runCard)
        self.stopButton = PushButton(FIF.PAUSE, "停止", self.runCard)
        self.stopButton.setEnabled(False)
        self.openButton = PushButton(FIF.FOLDER, "打开人声目录", self.runCard)
        addButtons(actionRow, self.openButton, self.stopButton, self.startButton)

        layout.addWidget(self.progressBar)
        layout.addLayout(actionRow)

    def _buildLogCard(self):
        self.logCard, self.logEdit, titleRow = makeLogCard(
            self.view, "分离日志会实时显示在这里")
        self.clearLogButton = TransparentToolButton(FIF.DELETE, self.logCard)
        titleRow.addWidget(self.clearLogButton)

    # ------------------------------------------------------------ 配置
    def _restoreFromConfig(self):
        self.inputEdit.setText(cfg.get(cfg.sepInputDir) or "")
        self.vocalEdit.setText(cfg.get(cfg.sepVocalDir) or "")
        self.instEdit.setText(cfg.get(cfg.sepInstDir) or "")
        index = self.formatCombo.findData(cfg.get(cfg.sepFormat) or "wav")
        self.formatCombo.setCurrentIndex(index if index >= 0 else 0)
        self.refreshModels()

    def refreshModels(self):
        models = self.engine.sepModels()
        self.modelCombo.blockSignals(True)
        self.modelCombo.clear()
        for label in models:
            self.modelCombo.addItem(label, userData=label)
        saved = cfg.get(cfg.sepModel) or ""
        index = self.modelCombo.findData(saved)
        self.modelCombo.setCurrentIndex(index if index >= 0 else 0)
        self.modelCombo.blockSignals(False)

    def collectPayload(self) -> dict:
        payload = {
            "model": self.modelCombo.currentData() or "",
            "inp_root": self.inputEdit.text().strip(),
            "vocal_dir": self.vocalEdit.text().strip(),
            "inst_dir": self.instEdit.text().strip(),
            "format": self.formatCombo.currentData() or "wav",
        }
        cfg.set(cfg.sepModel, payload["model"], save=False)
        cfg.set(cfg.sepInputDir, payload["inp_root"], save=False)
        cfg.set(cfg.sepVocalDir, payload["vocal_dir"], save=False)
        cfg.set(cfg.sepInstDir, payload["inst_dir"], save=False)
        cfg.set(cfg.sepFormat, payload["format"], save=False)
        return payload

    # ------------------------------------------------------------ 信号
    def _connectSignals(self):
        self.separator.logReceived.connect(self._appendLog)
        self.separator.progressChanged.connect(self._onProgress)
        self.separator.fileFinished.connect(self._onFileFinished)
        self.separator.finished.connect(self._onFinished)
        self.separator.failed.connect(self._onFailed)
        self.separator.stateChanged.connect(self._onStateChanged)

        self.fileButton.clicked.connect(self._onPickFile)
        self.folderButton.clicked.connect(self._onPickFolder)
        self.vocalBrowse.clicked.connect(lambda: self._browse(self.vocalEdit))
        self.instBrowse.clicked.connect(lambda: self._browse(self.instEdit))
        self.startButton.clicked.connect(self._onStart)
        self.stopButton.clicked.connect(self.separator.stop)
        self.openButton.clicked.connect(self._onOpenVocal)
        self.clearLogButton.clicked.connect(self.logEdit.clear)
        self.modelCombo.currentIndexChanged.connect(self.collectPayload)
        self.formatCombo.currentIndexChanged.connect(self.collectPayload)

    # ------------------------------------------------------------ 动作
    def _onPickFile(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择音频", "", AUDIO_FILTER)
        if path:
            self.inputEdit.setText(path)
            self.collectPayload()

    def _onPickFolder(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", self.inputEdit.text())
        if path:
            self.inputEdit.setText(path)
            self.collectPayload()

    def _browse(self, edit):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", edit.text())
        if path:
            edit.setText(path)
            self.collectPayload()

    def _onOpenVocal(self):
        path = self.vocalEdit.text().strip()
        if not path:
            path = os.path.join(self.engine.root, "output", "pymss", "vocal")
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _onStart(self):
        payload = self.collectPayload()
        if not payload["inp_root"]:
            InfoBar.warning(
                title="还差一点", content="请先选择要分离的音频文件或文件夹",
                orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
            )
            return
        self.statusLabel.setText("启动中…")
        setStatus(self.statusLabel, "info")
        self.separator.start("separate", payload)

    # ------------------------------------------------------------ 回调
    def _appendLog(self, text):
        self.logEdit.append(str(text))

    def _onProgress(self, done, total, message):
        if total:
            self.progressBar.setValue(int(min(done * 100 / total, 100)))
        if message:
            self.statusLabel.setText(str(message))
            setStatus(self.statusLabel, "info")

    def _onFileFinished(self, path, ok):
        if path:
            self._appendLog("%s %s" % ("完成" if ok else "失败", path))

    def _onFinished(self, ok):
        self.progressBar.setValue(100 if ok else 0)
        self.statusLabel.setText("完成" if ok else "已停止或失败，详见日志")
        setStatus(self.statusLabel, "ok" if ok else "error")
        if ok:
            InfoBar.success(
                title="分离完成", content="结果已写入输出目录",
                orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self,
            )

    def _onFailed(self, message):
        if message:
            self._appendLog("[失败] " + message)
        self.statusLabel.setText("失败")
        setStatus(self.statusLabel, "error")

    def _onStateChanged(self, running):
        self.startButton.setEnabled(not running)
        self.stopButton.setEnabled(running)
        if running:
            self.progressBar.setValue(0)

    def refreshEngine(self):
        """主窗口切到本页时调用。"""
        self.refreshModels()
