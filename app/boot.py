"""开机自启：往系统真实的启动项里写一条。

以前「开机自启」开关只改了一个配置项，系统里什么都没发生 —— 点了确实没效果。
这里按平台落到各自的启动项上：

    Windows : 任务计划程序（schtasks.exe，登录时触发）
    Linux   : ~/.config/autostart/LinVox.desktop
    macOS   : ~/Library/LaunchAgents/com.LinVox.plist

Windows 为什么首选任务计划程序而不是注册表 Run 键：
    - 触发时机是「用户登录」（ONLOGON），开机到登录那段时间不跟着抢资源；
    - 可以按最高权限跑（/RL HIGHEST），管理员账号下自启不会再弹一次 UAC；
    - 登录后延迟 1 分钟再起，避开开机那阵磁盘 / CPU 争抢（延迟格式 mmmm:ss）。

⚠️ 有的机器上 ONLOGON 任务建不出来（组策略 / 权限 / 安全软件拦，schtasks 报
"Access is denied"），这时**退回注册表 Run 键** —— 开关点了没反应最糟糕，退
一步能用总比不能强。实际用了哪条路看 :func:`lastMethod`，界面拿它提示用户。

写不进去（没权限、被安全软件拦截）时不抛异常，返回 False，由界面负责提示
并把开关拨回去 —— 界面状态必须和系统实际状态一致。
"""
import os
import sys
from pathlib import Path

from .config import cfg

# 自启命令里"只放托盘、不弹窗口"的开关（LinVox.py 认这个参数）
TRAY_ARG = "--tray"

APP_NAME = "LinVox"
BUNDLE_ID = "com.LinVox"
TASK_NAME = "LinVox"          # 任务计划程序里的任务名

# 改名前留下的启动项（旧名字），开关时顺手清掉，别让两条并存
LEGACY_APP_NAMES = ("FluentApp",)

# Windows 启动项所在注册表键（任务计划程序建不出来时的**退回方案**）
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 上一次开关实际走的是哪条路：任务计划程序 / 注册表 / autostart / launchd
_lastMethod = ""
# 最近一次 schtasks 的报错（给"为什么退回了注册表"留个说法）
_lastError = ""


# 入口脚本（改名前后都留着，挑真实存在的那个 —— 指到一个不存在的文件，
# 自启起来就是"什么都没发生"）
_ENTRY_CANDIDATES = ("LinVox.py", "main.py")


def entryScript() -> str:
    """入口脚本的绝对路径。"""
    root = Path(__file__).resolve().parents[1]
    for name in _ENTRY_CANDIDATES:
        path = root / name
        if path.is_file():
            return str(path)
    return str(root / _ENTRY_CANDIDATES[0])


def launchArgs():
    """启动参数：打包后直接跑 exe；源码运行时是 ``python LinVox.py [--tray]``。

    ``--tray`` 是"开机自启时只放托盘"的开关（配置 App/StartToTray）。它必须写进
    启动命令里：自启那条命令是开机时由系统执行的，那时候已经没人来读配置了 ——
    所以改这个开关要顺手把启动项重写一遍（见 setting_view）。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, TRAY_ARG] if cfg.get(cfg.startToTray) else [sys.executable]
    args = [sys.executable, entryScript()]
    if cfg.get(cfg.startToTray):
        args.append(TRAY_ARG)
    return args


def launchCommand():
    """拼成一条命令行（schtasks / Exec 里要用的形式）。"""
    return " ".join('"%s"' % arg for arg in launchArgs())


# ------------------------------------------------------------------ Windows
def _winLaunchArgs():
    """自启用的启动参数：能不用控制台窗口就不用。

    源码运行时 ``sys.executable`` 是 python.exe，登录时它会先闪一个黑框；
    同目录下有 pythonw.exe 就换成它（图形界面本来就不需要控制台）。
    """
    args = launchArgs()
    if getattr(sys, "frozen", False):
        return args
    exe = args[0]
    if exe.lower().endswith("python.exe"):
        silent = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.isfile(silent):
            return [silent] + args[1:]
    return args


def _winCommand() -> str:
    return " ".join('"%s"' % arg for arg in _winLaunchArgs())


def _schtasks(*args: str, quiet: bool = False) -> bool:
    """跑一条 schtasks 命令，成功（返回码 0）返回 True。

    ``quiet``：查不到任务是正常情况（就是没开自启），没必要往控制台刷报错。
    """
    import subprocess

    kwargs = {}
    if sys.platform == "win32":
        # 别让 schtasks 的黑框在界面上闪一下
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(["schtasks", *args], capture_output=True,
                                timeout=15, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        print("[LinVox] schtasks 执行失败：%s" % exc)
        return False
    global _lastError
    if result.returncode != 0:
        # 中文 Windows 下 schtasks 说的是 GBK，按 ANSI 代码页解才不是乱码
        encoding = "mbcs" if sys.platform == "win32" else "utf-8"
        _lastError = result.stderr.decode(encoding, errors="replace").strip()
        if not quiet:
            print("[LinVox] schtasks %s 失败（%d）：%s" % (
                " ".join(args), result.returncode, _lastError[:200]))
    return result.returncode == 0


def _winTaskExists(name: str = TASK_NAME) -> bool:
    return _schtasks("/Query", "/TN", name, quiet=True)


def _winTaskCreate(command: str) -> bool:
    """建一条「登录时启动」的任务。

    /RL HIGHEST（最高权限）需要管理员权限才建得出来，普通账号下会失败；
    那种情况退回 LIMITED —— 自启还是要有的，只是不再带管理员令牌。
    """
    common = ("/TN", TASK_NAME, "/TR", command, "/SC", "ONLOGON", "/F")
    for runLevel in ("HIGHEST", "LIMITED"):
        # 失败是常见情况（权限 / 策略），一条条刷太吵，全都失败时只报一次
        if _schtasks("/Create", *common, "/RL", runLevel, "/DELAY", "0001:00",
                     quiet=True):
            return True
        if _schtasks("/Create", *common, "/RL", runLevel, quiet=True):
            return True
    print("[LinVox] 任务计划程序建不出来（%s），退回注册表启动项" % _lastError[:120])
    return False


def _winRemoveRunKeys():
    """清掉注册表 Run 键里的启动项（本名 + 旧名字）。"""
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            for name in (APP_NAME,) + LEGACY_APP_NAMES:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass  # 本来就没有，删它干嘛
    except OSError:
        pass


def _winHasRunKey() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def _winSetRunKey() -> bool:
    """退回方案：往 HKCU Run 键写一条（旧实现用的就是这条）。"""
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _winCommand())
        return True
    except OSError as exc:
        print("[LinVox] 写入注册表启动项失败：%s" % exc)
        return False


def _winRemoveLegacy():
    """旧名字留下的任务（改名前建的），别和新任务并存启动两次。"""
    for name in LEGACY_APP_NAMES:
        if _winTaskExists(name):
            _schtasks("/Delete", "/TN", name, "/F")


def _winSet(enabled: bool):
    global _lastMethod
    _lastMethod = ""
    _winRemoveLegacy()

    if not enabled:
        if _winTaskExists(TASK_NAME):
            _schtasks("/Delete", "/TN", TASK_NAME, "/F")
        _winRemoveRunKeys()      # 两种写法都清掉，不留残留
        return

    if _winTaskCreate(_winCommand()):
        _lastMethod = "task"
        return
    # 任务计划程序不让建 → 退回注册表启动项，否则开关点了等于没点
    _winRemoveRunKeys()
    if _winSetRunKey():
        _lastMethod = "run"
        return
    raise RuntimeError("任务计划程序和注册表都写不进去")


def _winIsEnabled() -> bool:
    """两种写法都算：任务在，或者（退回）注册表里有。"""
    return _winTaskExists(TASK_NAME) or _winHasRunKey()


# -------------------------------------------------------------------- Linux
def _desktopPath() -> Path:
    root = os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(root) / "autostart" / f"{APP_NAME}.desktop"


def _linuxSet(enabled: bool):
    path = _desktopPath()
    if not enabled:
        if path.exists():
            path.unlink()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={launchCommand()}\n"
        "Terminal=false\n"
        "Hidden=false\n"
        "NoDisplay=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


# -------------------------------------------------------------------- macOS
def _plistPath() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _macSet(enabled: bool):
    path = _plistPath()
    if not enabled:
        if path.exists():
            path.unlink()
        return

    args = "".join(f"        <string>{arg}</string>\n" for arg in launchArgs())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"    <key>Label</key>\n    <string>{BUNDLE_ID}</string>\n"
        "    <key>ProgramArguments</key>\n    <array>\n"
        f"{args}"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n    <true/>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------- 对外的
def lastMethod() -> str:
    """上一次开关实际用的启动方式，界面拿它提示用户。

    ``"task"`` 任务计划程序 / ``"run"`` 注册表（退回方案）/
    ``"desktop"`` Linux autostart / ``"plist"`` macOS launchd / ``""`` 还没设过。
    """
    return _lastMethod


def isStartOnBoot() -> bool:
    """系统启动项里现在到底有没有我们这一条。"""
    try:
        if sys.platform == "win32":
            return _winIsEnabled()
        if sys.platform == "darwin":
            return _plistPath().exists()
        return _desktopPath().exists()
    except Exception:
        return False


def setStartOnBoot(enabled: bool) -> bool:
    """开 / 关开机自启。成功返回 True，失败返回 False（不抛异常）。"""
    global _lastMethod
    try:
        if sys.platform == "win32":
            _winSet(enabled)          # 自己会写 _lastMethod
        else:
            if sys.platform == "darwin":
                _macSet(enabled)
                _lastMethod = "plist" if enabled else ""
            else:
                _linuxSet(enabled)
                _lastMethod = "desktop" if enabled else ""
        return True
    except Exception:
        return False
