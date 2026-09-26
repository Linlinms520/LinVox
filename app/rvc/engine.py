"""RVC 引擎：把外部 RVC 工程接进 LinVox。

设计要点：
1. **不在界面进程里 import torch**。RVC 自带一套 runtime 解释器（Python 3.9 + torch），
   界面跑在自己的 Python 上，两者通过子进程 + JSON 行协议通信。
2. **实时变声**由 ``RealtimeController`` 管理，训练/分离/模型管理由 ``JobController`` 驱动，
   本类只负责路径解析、环境自检、模型扫描和公共进程工具。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import List

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ..config import DEFAULT_RVC_ROOT, cfg

_WORKER_DIR = os.path.dirname(os.path.abspath(__file__))
RTC_WORKER_SCRIPT = os.path.join(_WORKER_DIR, "realtime_worker.py")
TRAIN_WORKER_SCRIPT = os.path.join(_WORKER_DIR, "train_worker.py")
SEP_WORKER_SCRIPT = os.path.join(_WORKER_DIR, "sep_worker.py")
CKPT_WORKER_SCRIPT = os.path.join(_WORKER_DIR, "ckpt_worker.py")
RTC_EVENT_PREFIX = "@@RTC@@"

AUDIO_FILTER = "音频文件 (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma);;所有文件 (*.*)"

# 从源码里抠不出模型列表时的兜底项（与 PyMSS 内置模型一致）
FALLBACK_SEP_MODELS = [
    "去混响", "去混响（激进）", "去伴奏", "去伴奏（激进）", "提主旋律",
]

# 内嵌 RVC（2026/07 版）不再自带 .env，这些路径是 RVC 里的默认值。
# 必须在 import configs 之前塞进环境变量，否则 weight_root 之类全是 None。
DEFAULT_RVC_ENV = {
    "weight_root": "assets/weights",
    "weight_pymss_root": "assets/pymss_weights",
    "index_root": "logs",
    "outside_index_root": "assets/indices",
    "rmvpe_root": "assets/rmvpe",
}

# 环境自检要检查的文件（相对 RVC 根目录）：(说明, 候选路径…)
#
# 同一个模块在不同版本 RVC 里的位置不一样，命中任意一个就算通过：
#   2026/07 内嵌版：infer/vc/modules.py、train/process_ckpt.py
#   2024/06 版    ：infer/modules/vc/modules.py、infer/modules/train/process_ckpt.py
REQUIRED_FILES = (
    ("配置模块", ("configs/config.py",)),
    ("推理模块", ("infer/vc/modules.py", "infer/modules/vc/modules.py")),
    ("RVC 自带解释器", ("runtime/python.exe",)),
)

# 只影响某一页的文件：缺了不阻塞推理，但对应功能要给可操作的提示
OPTIONAL_FILES = {
    "ckpt": ("模型处理模块（模型管理页要用）",
             ("train/process_ckpt.py", "infer/modules/train/process_ckpt.py")),
    "realtime": ("实时变声模块（内置控制台要用）",
                 ("infer/rtrvc.py", "infer/lib/rtrvc.py")),
    "torchgate": ("TorchGate 降噪模块（实时变声要用）",
                  ("tools/torchgate.py", "tools/torchgate/torchgate.py")),
}


class RvcEngine(QObject):
    """RVC 引擎：路径解析、环境自检、模型扫描、公共进程工具。"""

    def __init__(self, parent=None):
        super().__init__(parent)

    # ------------------------------------------------------------ 路径
    @property
    def root(self) -> str:
        return (cfg.get(cfg.rvcRoot) or DEFAULT_RVC_ROOT).strip()

    def setRoot(self, value: str):
        cfg.set(cfg.rvcRoot, value)

    def pythonExe(self) -> str:
        """优先用 RVC 自带的 runtime 解释器，里面才有 torch。"""
        custom = (cfg.get(cfg.rvcPython) or "").strip()
        if custom and os.path.isfile(custom):
            return custom
        bundled = os.path.join(self.root, "runtime", "python.exe")
        if os.path.isfile(bundled):
            return bundled
        return sys.executable

    def pythonwExe(self) -> str:
        """无控制台解释器：启动实时变声 GUI 时用，不弹黑框。"""
        bundled = os.path.join(self.root, "runtime", "pythonw.exe")
        return bundled if os.path.isfile(bundled) else self.pythonExe()

    def _readEnv(self) -> dict:
        """RVC 的路径配置：优先读 .env，没有就用内嵌版的默认值。"""
        env = {}
        path = os.path.join(self.root, ".env")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        env[key.strip()] = value.strip()
            except OSError:
                pass
        for key, value in DEFAULT_RVC_ENV.items():
            env.setdefault(key, value)
        return env

    def _join(self, rel: str) -> str:
        return os.path.join(self.root, (rel or "").replace("/", os.sep))

    def weightsDir(self) -> str:
        return self._join(self._readEnv().get("weight_root", "assets/weights"))

    def logsDir(self) -> str:
        """训练产物目录（logs/<实验名>/）。"""
        return self._join(self._readEnv().get("index_root", "logs"))

    def outsideIndexDir(self) -> str:
        return self._join(
            self._readEnv().get("outside_index_root", "assets/indices"))

    def indexDirs(self) -> List[str]:
        return [self.outsideIndexDir(), self.logsDir()]

    @staticmethod
    def processEnvironment() -> "QProcessEnvironment":
        """所有 RVC 子进程共用的环境变量。

        weight_root 这类变量是 configs/config.py 在 import 时读的，
        必须在子进程启动前就设好，否则模型路径会变成 None。
        """
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("OPENBLAS_NUM_THREADS", "1")
        env.insert("OMP_NUM_THREADS", "4")
        env.insert("RVC_CUDA_GRAPH", "0")   # 训练脚本在子进程里禁用 CUDA graph
        return env

    def rvcEnvironment(self) -> "QProcessEnvironment":
        """进程环境 + RVC 自己的路径变量（weight_root / index_root …）。"""
        env = self.processEnvironment()
        for key, value in self._readEnv().items():
            if value:
                env.insert(key, value)
        return env

    # ------------------------------------------------------------ 自检
    def findFile(self, *candidates) -> str:
        """按候选路径在 RVC 根目录里找文件，返回第一个命中的绝对路径。"""
        for rel in candidates:
            path = self._join(rel)
            if os.path.exists(path):
                return path
        return ""

    def hasFeature(self, kind: str) -> bool:
        """某个可选能力是否可用（见 OPTIONAL_FILES）。"""
        item = OPTIONAL_FILES.get(kind)
        if not item:
            return True
        return bool(self.findFile(*item[1]))

    def featureProblem(self, kind: str) -> str:
        """可选能力缺失时的提示：说清缺什么、能在哪找到完整的工程。"""
        item = OPTIONAL_FILES.get(kind)
        if not item:
            return ""
        desc, candidates = item
        hint = ("当前工程不是完整的内嵌版，%s（找过 %s）"
                % (desc, "、".join(candidates)))
        if os.path.isdir(DEFAULT_RVC_ROOT) and \
                os.path.abspath(DEFAULT_RVC_ROOT) != os.path.abspath(self.root):
            hint += "；可在设置页把「RVC 工程目录」改回内嵌工程：%s" % DEFAULT_RVC_ROOT
        return hint

    def check(self):
        """环境自检。返回 (是否可用, 问题列表)。"""
        root = self.root
        if not root or not os.path.isdir(root):
            return False, ["RVC 工程目录不存在：%s" % (root or "(未设置)")]

        problems = []
        for desc, candidates in REQUIRED_FILES:
            if not self.findFile(*candidates):
                problems.append("缺少%s：%s" % (desc, " 或 ".join(candidates)))

        # Hubert 底模：新版是 assets/hubert_base 目录，旧版是 assets/hubert/hubert_base.pt
        hubert_dir = os.path.join(root, "assets", "hubert_base")
        hubert_pt = os.path.join(root, "assets", "hubert", "hubert_base.pt")
        if not (os.path.isdir(hubert_dir) or os.path.isfile(hubert_pt)):
            problems.append("缺少 Hubert 底模：assets/hubert_base")

        exe = self.pythonExe()
        if not os.path.isfile(exe):
            problems.append("找不到 Python 解释器：%s" % exe)
        elif not os.path.isfile(os.path.join(root, "runtime", "python.exe")):
            problems.append(
                "未使用 RVC 自带 runtime 解释器，推理可能因缺少 torch 而失败"
            )
        if not self.models():
            problems.append("模型目录里没有 .pth 权重：%s" % self.weightsDir())
        return (not problems), problems

    # ------------------------------------------------------------ 扫描
    def models(self) -> List[str]:
        """assets/weights 下的模型文件名（get_vc 只接受文件名）。"""
        directory = self.weightsDir()
        if not os.path.isdir(directory):
            return []
        return sorted(
            name for name in os.listdir(directory) if name.lower().endswith(".pth")
        )

    def indices(self) -> List[str]:
        """索引文件绝对路径：assets/indices + logs/**。"""
        found = []
        for directory in self.indexDirs():
            if not os.path.isdir(directory):
                continue
            for dirpath, _dirnames, filenames in os.walk(directory):
                for name in filenames:
                    if name.lower().endswith(".index"):
                        found.append(os.path.join(dirpath, name))
        return sorted(found)

    def experiments(self) -> List[str]:
        """logs/ 下的实验名（训练产物目录）。"""
        directory = self.logsDir()
        if not os.path.isdir(directory):
            return []
        return sorted(
            name for name in os.listdir(directory)
            if os.path.isdir(os.path.join(directory, name))
        )

    def trainedModels(self) -> List[str]:
        """logs/** 下训练出来的权重（G_*.pth），供实时变声 / 模型管理直接选。"""
        directory = self.logsDir()
        found = []
        if os.path.isdir(directory):
            for dirpath, _dirnames, filenames in os.walk(directory):
                for name in filenames:
                    if name.lower().endswith(".pth") and name.startswith("G_"):
                        found.append(os.path.join(dirpath, name))
        return sorted(found)

    def sepModels(self) -> List[str]:
        """人声分离可选模型。

        直接从 tools/pymss_webui.py 源码里抠 label，避免为了拿个列表
        就去 import torch（PyMSS 的入口会连带加载一堆重依赖）。
        """
        import re

        path = os.path.join(self.root, "tools", "pymss_webui.py")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return list(FALLBACK_SEP_MODELS)
        block = text.split("MODEL_BY_LABEL", 1)[0]
        labels = re.findall(r'label\s*=\s*"([^"]+)"', block)
        return [label for label in labels if label] or list(FALLBACK_SEP_MODELS)

    def pretrainedModels(self) -> List[str]:
        """assets/pretrained[_v2] 下的底模，训练时可选。"""
        found = []
        for folder in ("pretrained", "pretrained_v2"):
            directory = os.path.join(self.root, "assets", folder)
            if not os.path.isdir(directory):
                continue
            for name in sorted(os.listdir(directory)):
                if name.lower().endswith(".pth"):
                    found.append(os.path.join(directory, name))
        return found

    # ------------------------------------------------------------ 公共进程工具
    @staticmethod
    def killTree(pid) -> bool:
        """连同子进程一起杀掉。实时 GUI / WebUI 都会派生子进程。"""
        if not pid:
            return False
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                import signal

                os.killpg(os.getpgid(pid), signal.SIGTERM)
            return True
        except Exception:
            return False


class RealtimeController(QObject):
    """实时变声控制器：管理 headless worker 子进程，双向 JSON 通信。

    worker（RVC runtime 解释器）负责设备枚举与音频推理；
    界面只收发命令/事件，torch 全部留在子进程里。
    """

    workerReady = Signal()              # worker 起来了，可以枚举设备
    devicesUpdated = Signal(object)     # 设备列表 dict
    streamStarted = Signal(object)      # 音频流已启动（采样率/延迟）
    streamStopped = Signal()
    metricsReceived = Signal(int, int)  # 推理耗时 ms, 算法延迟 ms
    logReceived = Signal(str)
    errorReceived = Signal(str)
    streamChanged = Signal(bool)        # True=变声中
    workerChanged = Signal(bool)        # True=worker 进程存活

    def __init__(self, engine: RvcEngine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._proc: QProcess | None = None
        self._buffer = ""
        self._streamRunning = False
        self._starting = False

    # ------------------------------------------------------------ 状态
    @property
    def isWorkerAlive(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    @property
    def isStreamRunning(self) -> bool:
        return self._streamRunning

    def pid(self) -> int:
        """worker 进程 pid（没起来就是 0）。任务管理器靠它统计资源占用。"""
        if not self.isWorkerAlive:
            return 0
        return int(self._proc.processId())

    def _env(self) -> QProcessEnvironment:
        """实时变声进程的环境。

        批量推理/训练为了防抢占会限制线程数，但实时推理在 CPU 上跑时
        反而需要把核心都用上，否则单块音频根本算不完。这里单独放开。
        """
        env = self._engine.rvcEnvironment()
        n_cpu = os.cpu_count() or 4
        env.insert("OMP_NUM_THREADS", str(n_cpu))
        env.insert("OPENBLAS_NUM_THREADS", str(n_cpu))
        env.insert("MKL_NUM_THREADS", str(n_cpu))
        return env

    # ------------------------------------------------------------ 进程
    def ensureWorker(self) -> bool:
        """确保 worker 已启动（幂等）。"""
        if self.isWorkerAlive:
            return True
        ok, problems = self._engine.check()
        if not ok:
            self.errorReceived.emit("；".join(problems))
            return False
        # 实时变声还依赖 rtrvc 与 torchgate，缺一个就在子进程里 import 失败，
        # 不如在这里直接说清楚
        for feature in ("realtime", "torchgate"):
            if not self._engine.hasFeature(feature):
                self.errorReceived.emit(self._engine.featureProblem(feature))
                return False
        proc = QProcess(self)
        proc.setWorkingDirectory(self._engine.root)
        proc.setProcessEnvironment(self._env())
        proc.readyReadStandardOutput.connect(self._onStdout)
        proc.readyReadStandardError.connect(self._onStderr)
        proc.finished.connect(self._onWorkerFinished)
        proc.errorOccurred.connect(self._onWorkerError)
        self._proc = proc
        self._buffer = ""
        proc.start(self._engine.pythonExe(),
                   [RTC_WORKER_SCRIPT, "--rvc-root", self._engine.root])
        self.workerChanged.emit(True)
        self.logReceived.emit("实时变声引擎已启动")
        return True

    def shutdown(self):
        """退出程序时调用：结束音频流并关闭 worker。"""
        if not self.isWorkerAlive:
            return
        try:
            self.send({"cmd": "exit"})
            if not self._proc.waitForFinished(2000):
                self._proc.kill()
        except Exception:
            if self._proc:
                self._proc.kill()
        self._proc = None
        self._streamRunning = False
        self.workerChanged.emit(False)
        self.streamChanged.emit(False)

    def send(self, payload: dict) -> bool:
        if not self.isWorkerAlive:
            return False
        self._proc.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        return True

    # ------------------------------------------------------------ 高层命令
    def requestDevices(self, hostapi: str = "") -> bool:
        """枚举音频设备；worker 没起会自动拉起（只加载 sounddevice，很快）。"""
        if not self.ensureWorker():
            return False
        cmd = {"cmd": "list_devices"}
        if hostapi:
            cmd["hostapi"] = hostapi
        return self.send(cmd)

    def startStream(self, params: dict) -> bool:
        if not self.ensureWorker():
            return False
        if self._streamRunning:
            self.errorReceived.emit("变声已在运行中，请先停止")
            return False
        self._starting = True
        return self.send({"cmd": "start", "params": params})

    def stopStream(self):
        self._starting = False
        self.send({"cmd": "stop"})

    def setParams(self, patch: dict):
        """热更新：只对响应阈值/音调/检索占比等即时参数生效。"""
        if self._streamRunning and patch:
            self.send({"cmd": "set", "params": patch})

    # ------------------------------------------------------------ 输出解析
    def _onStdout(self):
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += data.replace("\r\n", "\n")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith(RTC_EVENT_PREFIX):
                self._handleEvent(line[len(RTC_EVENT_PREFIX):].strip())
            else:
                self.logReceived.emit(line)

    def _onStderr(self):
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        for line in data.replace("\r\n", "\n").split("\n"):
            if line.strip():
                self.logReceived.emit("[stderr] " + line.strip())

    def _handleEvent(self, raw: str):
        try:
            event = json.loads(raw)
        except ValueError:
            self.logReceived.emit(raw)
            return
        kind = event.get("type")
        if kind == "ready":
            self.workerReady.emit()
        elif kind == "devices":
            self.devicesUpdated.emit(event)
        elif kind == "started":
            self._streamRunning = True
            self._starting = False
            self.streamChanged.emit(True)
            self.streamStarted.emit(event)
        elif kind == "stopped":
            self._streamRunning = False
            self.streamChanged.emit(False)
            self.streamStopped.emit()
        elif kind == "metrics":
            self.metricsReceived.emit(
                int(event.get("infer_ms", 0)), int(event.get("delay_ms", 0)))
        elif kind == "log":
            self.logReceived.emit(str(event.get("msg", "")))
        elif kind == "error":
            self._starting = False
            self.errorReceived.emit(str(event.get("msg", "")))
            detail = event.get("traceback")
            if detail:
                self.logReceived.emit(str(detail))

    def _onWorkerFinished(self, code, status):
        was_running = self._streamRunning
        self._streamRunning = False
        self._starting = False
        self._proc = None
        self.workerChanged.emit(False)
        if was_running:
            self.streamChanged.emit(False)
            self.streamStopped.emit()
        self.logReceived.emit("实时变声引擎退出（code=%s）" % code)

    def _onWorkerError(self, error):
        if error == QProcess.FailedToStart:
            self._starting = False
            self.errorReceived.emit(
                "无法启动实时变声引擎（%s），请检查 RVC 工程目录"
                % self._engine.pythonExe()
            )
