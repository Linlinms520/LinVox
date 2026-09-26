"""进程资源统计：CPU / 内存 / GPU 显存。

界面进程里没装 psutil，也不该为了显示几个数字就引入一个新依赖，所以这里
直接用系统原生接口：

    Windows : kernel32.GetProcessTimes + psapi.GetProcessMemoryInfo
              + toolhelp 快照建进程树
    Linux   : /proc/<pid>/stat、/proc/<pid>/status、/proc/<pid>/stat 里的 ppid

两个容易踩的口径问题：

1. CPU% 必须靠**两次采样的差值**算，只采一次永远是 0 —— 所以 CPU% 由
   :class:`ProcessSampler` 保存上一次的快照，隔一段时间采第二次才有意义。
2. 这里按「占全部核心的百分比」算（和 Windows 任务管理器一致：所有核心
   跑满才是 100%），不是 psutil 那种能超过 100% 的口径。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, QProcess, Signal

IS_WINDOWS = os.name == "nt"
_UNITS = ("B", "KB", "MB", "GB", "TB")


# ------------------------------------------------------------------ Windows
if IS_WINDOWS:  # pragma: no cover - 只在 Windows 上跑
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_VM_READ = 0x0010
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _TH32CS_SNAPPROCESS = 0x00000002

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                      wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                   wintypes.DWORD]
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                          ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                         ctypes.POINTER(_PROCESSENTRY32W)]
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD]

    def _openProcess(pid: int):
        handle = _kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, pid)
        return handle if handle else 0

    def processCpuSeconds(pid: int) -> float:
        """进程占用的 CPU 总时间（用户态 + 内核态，秒）。取不到就是 0。"""
        handle = _openProcess(pid)
        if not handle:
            return 0.0
        try:
            creation, exitTime = _FILETIME(), _FILETIME()
            kernel, user = _FILETIME(), _FILETIME()
            ok = _kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exitTime),
                ctypes.byref(kernel), ctypes.byref(user))
            if not ok:
                return 0.0
            return ((_toSeconds(kernel) + _toSeconds(user)))
        finally:
            _kernel32.CloseHandle(handle)

    def _toSeconds(stamp: "_FILETIME") -> float:
        return (((stamp.dwHighDateTime << 32) | stamp.dwLowDateTime) / 1e7)

    def processMemoryBytes(pid: int) -> int:
        """进程工作集大小（Windows 任务管理器「内存」那一列）。"""
        handle = _openProcess(pid)
        if not handle:
            return 0
        try:
            counters = _PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            if not _psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb):
                return 0
            return int(counters.WorkingSetSize)
        finally:
            _kernel32.CloseHandle(handle)

    def processChildren() -> dict:
        """一次 toolhelp 快照，得到 {父进程: [子进程…]}。"""
        children: dict = {}
        snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE_VALUE:
            return children
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                children.setdefault(entry.th32ParentProcessID, []).append(
                    entry.th32ProcessID)
                entry = _PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(entry)
                ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snap)
        return children

else:  # Linux / macOS
    _CLOCK_TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def _statFields(pid: int):
        with open("/proc/%d/stat" % pid, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        return text.rsplit(")", 1)[1].split()

    def processCpuSeconds(pid: int) -> float:
        try:
            fields = _statFields(pid)
            return (int(fields[11]) + int(fields[12])) / _CLOCK_TICK
        except Exception:
            return 0.0

    def processMemoryBytes(pid: int) -> int:
        try:
            with open("/proc/%d/status" % pid, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        except Exception:
            pass
        return 0

    def processChildren() -> dict:
        children: dict = {}
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                ppid = int(_statFields(int(name))[1])
            except Exception:
                continue
            children.setdefault(ppid, []).append(int(name))
        return children


# ------------------------------------------------------------------ 公共
def cpuCount() -> int:
    return os.cpu_count() or 1


def processTree(pid: int) -> list:
    """进程及其所有子孙的 pid。

    RVC 的 worker 还会再派生子进程（训练尤其如此），只统计父进程会漏掉大头。
    """
    if not pid:
        return []
    children = processChildren()
    tree, stack = [], [pid]
    while stack:
        current = stack.pop()
        tree.append(current)
        stack.extend(children.get(current, ()))
    return tree


def formatBytes(value) -> str:
    """123456789 → '117.7 MB'。"""
    value = float(value or 0)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return "%.0f %s" % (value, unit) if unit == "B" \
                else "%.1f %s" % (value, unit)
        value /= 1024.0
    return "0 B"


class ProcessSampler:
    """CPU 采样器：内部记着上一次的累计时间，隔一段时间再采才能算出百分比。"""

    def __init__(self):
        self._last: dict = {}

    def sample(self, pid: int) -> float:
        """整棵进程树的 CPU 占用（%，占全部核心的比例）。"""
        if not pid:
            return 0.0
        now = time.monotonic()
        total = sum(processCpuSeconds(p) for p in processTree(pid))
        previous = self._last.get(pid)
        self._last[pid] = (total, now)
        if not previous:
            return 0.0            # 第一次采样只能当作基线
        lastTotal, lastTime = previous
        elapsed = now - lastTime
        if elapsed <= 0 or total < lastTotal:
            return 0.0
        return (total - lastTotal) / elapsed / cpuCount() * 100.0

    def forget(self, pid: int):
        """进程结束了就把基线丢掉，免得下次重启时拿旧基线算出个负数。"""
        self._last.pop(pid, None)


# ------------------------------------------------------------------ 显卡
def findNvidiaSmi() -> str:
    """nvidia-smi 的路径；没有 NVIDIA 显卡 / 驱动就返回空串。"""
    import shutil

    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    if IS_WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for path in (os.path.join(root, "System32", "nvidia-smi.exe"),
                     r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.isfile(path):
                return path
    return ""


class GpuSampler(QObject):
    """异步查 nvidia-smi。

    同步跑一次要 0.3~1 秒，直接放主线程里界面会明显卡一下，所以走 QProcess
    在后台取，结果用信号回传。没显卡 / 没驱动时整个类安静失效（available=False）。
    """
    sampled = Signal(dict, dict)   # {pid: MiB}, 显卡概况 dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exe = findNvidiaSmi()
        self._apps: dict = {}
        self._info: dict = {}
        self._busy = False
        self._appsProc = QProcess(self)
        self._infoProc = QProcess(self)
        self._appsProc.finished.connect(self._onAppsFinished)
        self._infoProc.finished.connect(self._onInfoFinished)

    @property
    def available(self) -> bool:
        return bool(self._exe)

    def refresh(self):
        """发起一次查询（上一次没回来就直接跳过，别把进程堆起来）。"""
        if not self._exe or self._busy:
            return
        self._busy = True
        self._appsProc.start(self._exe, [
            "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits"])
        self._infoProc.start(self._exe, [
            "--query-gpu=name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits"])

    # ------------------------------------------------------------ 解析
    def _text(self, proc: QProcess) -> str:
        return bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")

    def _onAppsFinished(self):
        apps = {}
        for line in self._text(self._appsProc).splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit():
                try:
                    apps[int(parts[0])] = float(parts[1])
                except ValueError:
                    continue
        self._apps = apps
        self._tryEmit()

    def _onInfoFinished(self):
        info = {}
        for line in self._text(self._infoProc).splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 4:
                info = {"name": parts[0],
                        "used": _toFloat(parts[1]),
                        "total": _toFloat(parts[2]),
                        "util": _toFloat(parts[3])}
                break
        self._info = info
        self._tryEmit()

    def _tryEmit(self):
        # 两个进程都收工了才算这一轮采样结束
        if self._appsProc.state() != QProcess.NotRunning or \
                self._infoProc.state() != QProcess.NotRunning:
            return
        self._busy = False
        self.sampled.emit(dict(self._apps), dict(self._info))


def _toFloat(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0
