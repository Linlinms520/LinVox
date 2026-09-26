"""通用任务控制器：训练 / 分离 / 模型管理的进程与信号管理。

三个 worker（train_worker.py / sep_worker.py / ckpt_worker.py）跑的都是
「启动子进程 → 逐行收 JSON → 转信号」，区别只在脚本和任务名，
所以这里做一个通用控制器，各页面按需实例化即可。

    sep = JobController(SEP_WORKER_SCRIPT, engine)
    sep.logReceived.connect(self._appendLog)
    sep.start("separate", {"model": ..., "inp_root": ...})
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, QProcess, Signal


class JobController(QObject):
    """驱动一个 RVC worker 子进程，把 JSON 行翻译成 Qt 信号。"""

    logReceived = Signal(str)                  # 一行日志
    stepChanged = Signal(str, int, int)        # 步骤名, 第几步, 共几步
    progressChanged = Signal(float, float, str)  # 已完成, 总数, 说明
    fileFinished = Signal(str, bool)           # 文件, 是否成功
    resultReceived = Signal(object)            # 结构化结果（dict）
    finished = Signal(bool)                    # ok?
    failed = Signal(str)                       # 启动失败
    stateChanged = Signal(bool)                # True = 运行中

    def __init__(self, script: str, engine, parent=None, feature: str = ""):
        """``feature`` 指定本任务依赖的可选能力（见 RvcEngine.OPTIONAL_FILES）。

        传了就会在启动前额外检查一次，缺了直接给出可操作的提示，
        而不是等 worker 在子进程里 import 失败。
        """
        super().__init__(parent)
        self.script = script
        self._engine = engine
        self._feature = feature
        self._proc: QProcess | None = None
        self._buffer = ""

    # ------------------------------------------------------------ 状态
    def isRunning(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def pid(self) -> int:
        """当前 worker 的 pid（没在跑就是 0）。任务管理器靠它统计资源占用。"""
        if not self.isRunning():
            return 0
        return int(self._proc.processId())

    # ------------------------------------------------------------ 启停
    def start(self, job: str, payload: dict) -> bool:
        if self.isRunning():
            self.failed.emit("该任务正在运行，请先停止")
            return False
        ok, problems = self._engine.check()
        if not ok:
            self.failed.emit("；".join(problems))
            return False
        if self._feature and not self._engine.hasFeature(self._feature):
            self.failed.emit(self._engine.featureProblem(self._feature))
            return False

        args = [self.script, "--rvc-root", self._engine.root,
                "--job", job, "--payload",
                json.dumps(payload or {}, ensure_ascii=False)]

        proc = QProcess(self)
        proc.setWorkingDirectory(self._engine.root)
        proc.setProcessEnvironment(self._engine.rvcEnvironment())
        proc.readyReadStandardOutput.connect(self._onStdout)
        proc.readyReadStandardError.connect(self._onStderr)
        proc.finished.connect(self._onFinished)
        proc.errorOccurred.connect(self._onError)
        self._proc = proc
        self._buffer = ""
        self.logReceived.emit("$ %s --job %s"
                              % (self.script.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], job))
        proc.start(self._engine.pythonExe(), args)
        self.stateChanged.emit(True)
        return True

    def stop(self):
        """停止任务：整棵进程树一起杀（worker 还会再派生子进程）。"""
        if not self._proc:
            return
        pid = self._proc.processId()
        self._engine.killTree(pid)
        try:
            if self._proc.state() != QProcess.NotRunning:
                self._proc.kill()
                self._proc.waitForFinished(3000)
        except Exception:
            pass
        self.logReceived.emit("已请求停止当前任务")
        self.stateChanged.emit(False)
        self._proc = None

    # ------------------------------------------------------------ 输出解析
    def _onStdout(self):
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += data.replace("\r\n", "\n")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handleLine(line.strip())

    def _onStderr(self):
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        for line in data.replace("\r\n", "\n").split("\n"):
            if line.strip():
                self.logReceived.emit("[stderr] " + line.strip())

    def _handleLine(self, line: str):
        if not line:
            return
        try:
            payload = json.loads(line)
        except ValueError:
            self.logReceived.emit(line)   # 第三方库的杂散 print
            return
        if not isinstance(payload, dict):
            self.logReceived.emit(line)
            return

        kind = payload.get("type")
        if kind == "log":
            self.logReceived.emit(str(payload.get("msg", "")))
        elif kind == "step":
            self.logReceived.emit("[%s/%s] %s"
                                  % (payload.get("index", 0), payload.get("total", 0),
                                     payload.get("name", "")))
            self.stepChanged.emit(str(payload.get("name", "")),
                                  int(payload.get("index", 0)),
                                  int(payload.get("total", 0)))
        elif kind == "progress":
            self.progressChanged.emit(float(payload.get("done", 0)),
                                      float(payload.get("total", 1)),
                                      str(payload.get("message", "")))
        elif kind == "file":
            self.fileFinished.emit(str(payload.get("input", "")),
                                   bool(payload.get("ok", True)))
        elif kind == "result":
            self.resultReceived.emit(payload)
        elif kind == "error":
            self.logReceived.emit("[错误] %s" % payload.get("msg", ""))
            detail = payload.get("traceback")
            if detail:
                self.logReceived.emit(str(detail))
            self.failed.emit(str(payload.get("msg", "")))
        elif kind == "done":
            ok = bool(payload.get("ok", True))
            elapsed = payload.get("elapsed")
            if elapsed is not None:
                self.logReceived.emit("任务结束（耗时 %ss）" % elapsed)
            if ok:
                self.progressChanged.emit(1, 1, "完成")
            self.finished.emit(ok)

    def _onFinished(self, code, status):
        self.stateChanged.emit(False)
        self._proc = None
        self.logReceived.emit("进程退出（code=%s）" % code)

    def _onError(self, error):
        if error == QProcess.FailedToStart:
            self.stateChanged.emit(False)
            self.failed.emit(
                "无法启动 RVC 引擎（%s），请检查工程目录"
                % self._engine.pythonExe()
            )
