"""全局配置：基于 QFluentWidgets 的 qconfig，自动持久化到用户配置目录。

目录里会有两份 json：
  · ``config.json``   用户配置（改什么存什么，下次启动自动读回）
  · ``defaults.json`` 出厂默认（每次启动重写，改坏了照着它改回来，或一键恢复）
"""
import copy
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from qfluentwidgets import (
    BoolValidator,
    ColorConfigItem,
    ConfigItem,
    EnumSerializer,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    RangeConfigItem,
    RangeValidator,
    Theme,
    qconfig,
)

# 背景模糊半径的可选范围（px）。再大就糊得只剩色块了，而且是全窗口重绘，
# 半径越大越费 CPU（只在换图 / 改数值 / 缩放窗口时重算一次）
BG_BLUR_RANGE = (0, 40)

# 桌面叠层能贴的四个角：配置里存 key，下拉里显示中文
OVERLAY_CORNERS = ("topLeft", "topRight", "bottomLeft", "bottomRight")
OVERLAY_CORNER_TEXTS = ("左上角", "右上角", "左下角", "右下角")

APP_DIR_NAME = "LinVox"
LEGACY_APP_DIR_NAME = "FluentApp"   # 改名前的目录，启动时把老配置搬过来

# ---- 内嵌 RVC 工程 ----
# RVC 整个工程就放在软件目录里（与主程序同级），开箱即用。
# 目录名按优先级依次探测，找到哪个用哪个；都找不到才回退到默认名。
RVC_DIR_CANDIDATES = ("RVC20260718DML", "RVC20240604AMD_Intel")


def _projectRoot() -> str:
    """app/config.py → 项目根目录。

    ⚠️ 打包后（PyInstaller）app/ 躺在 ``_internal/``（onedir）或临时解包目录里
    （onefile），而 RVC 工程是放在 **exe 边上** 的（8GB，不会塞进包里）——
    所以冻结状态下根目录要按 exe 的位置算，不然一打包就"引擎不可用"。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _detectRvcRoot() -> str:
    """自动定位内嵌的 RVC 工程目录。

    以前默认值写死成某台机器的绝对路径（G:\\...\\RVC20240604AMD_Intel），
    换台电脑就变成"引擎不可用"。这里改成随软件目录走。
    """
    root = _projectRoot()
    for name in RVC_DIR_CANDIDATES:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            return path
    return os.path.join(root, RVC_DIR_CANDIDATES[0])


DEFAULT_RVC_ROOT = _detectRvcRoot()

# 内嵌 RVC（2026/07 版）支持的音高提取算法。
# 老版本里的 harvest / crepe 已被移除，界面不再提供，避免点下去直接报错。
F0_METHODS = ["pm", "rmvpe", "fcpe"]

# 输出重采样率，0 表示跟随模型
RESAMPLE_OPTIONS = [0, 16000, 32000, 40000, 44100, 48000]

# 音频输出格式（推理与分离共用）
OUTPUT_FORMATS = ["wav", "flac", "mp3", "m4a"]

# 训练采样率 / 模型版本
SAMPLE_RATES = ["32k", "40k", "48k"]
MODEL_VERSIONS = ["v1", "v2"]


def _config_path() -> str:
    """配置文件存放位置。

    注意：这里**刻意不用** QStandardPaths.AppConfigLocation。
    在未显式设置 organizationName / applicationName 时，Qt 会把 sys.argv[0]
    拼进路径（例如 ~/.config/main.py/...），导致从源码跑、打包成 exe 跑、
    或改个文件名，配置目录就变了 —— 用户的设置会莫名其妙"丢失"。

    所以这里按平台自己拼一个固定目录，保证任何启动方式下路径一致：
      Windows : %APPDATA%\\LinVox\\config.json
      macOS   : ~/Library/Application Support/LinVox/config.json
      Linux   : ~/.config/LinVox/config.json

    测试时可用环境变量 FLUENT_APP_CONFIG_DIR 指向临时目录做隔离。
    """
    override = os.getenv("FLUENT_APP_CONFIG_DIR")
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        root = Path(base) / APP_DIR_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        root = Path(base) / APP_DIR_NAME

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = Path(__file__).resolve().parents[1] / ".config"

    _migrateLegacyConfig(root)
    return str(root / "config.json")


def _migrateLegacyConfig(root: Path):
    """把改名前的那份配置（FluentApp/）搬过来。

    应用改名叫 LinVox，配置目录也跟着改名；不搬的话老用户一升级会发现
    "设置全丢了"，其实是还在旧目录里躺着。
    """
    try:
        legacy = root.parent / LEGACY_APP_DIR_NAME / "config.json"
        if not (root / "config.json").exists() and legacy.is_file():
            shutil.copy(legacy, root / "config.json")
    except OSError:
        pass


class Config(QConfig):
    """应用配置项。新增配置只需在这里加一个 ConfigItem 即可，读写自动落盘。"""

    # 主题模式：亮 / 暗 / 跟随系统
    #
    # ⚠️ 坑点：QConfig 基类里已经有 themeMode / themeColor，且自带 serializer。
    # 这里为了改默认值而重新定义时，**必须手动补上序列化器**，否则把基类的覆盖掉后
    # 枚举和 QColor 无法 JSON 序列化 —— 表现是关闭窗口时 cfg.save() 直接抛 TypeError。
    themeMode = OptionsConfigItem(
        "QFluentWidgets", "ThemeMode", Theme.DARK,
        OptionsValidator(Theme), EnumSerializer(Theme), restart=False,
    )

    # 主题强调色（ColorConfigItem 自带 ColorSerializer，别用裸 ConfigItem）
    themeColor = ColorConfigItem("QFluentWidgets", "ThemeColor", "#009faa")

    # Win11 云母背景效果
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", True, BoolValidator())

    # 自定义背景图片：留空 = 用主题自带的底色
    bgImagePath = ConfigItem("MainWindow", "BgImagePath", "")
    # 背景模糊度（px，0 = 原图）。用 RangeConfigItem，设置页的滑块卡片直接认它
    bgBlur = RangeConfigItem(
        "MainWindow", "BgBlur", 0, RangeValidator(*BG_BLUR_RANGE),
    )

    # 界面缩放：跟随系统 / 100% / 125% / 150%
    dpiScale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto",
        OptionsValidator(["Auto", "1", "1.25", "1.5", "1.75", "2"]), restart=True,
    )

    # 业务配置示例：是否开机自启
    startOnBoot = ConfigItem("App", "StartOnBoot", False, BoolValidator())
    # 开机自启的附加项：自启时**不弹主窗口**，只在托盘里待着（点托盘图标再叫出来）。
    # 只有 startOnBoot 开着时它才有意义 —— 界面上会跟着置灰。
    startToTray = ConfigItem("App", "StartToTray", False, BoolValidator())

    # 首次使用的分步指导看过了没有（看过就不再自动弹，设置页里可以叫回来）
    guideDone = ConfigItem("App", "GuideDone", False, BoolValidator())

    # 启动时间：分两个字段存（上次 / 本次）
    # ⚠️ 坑点：只用一个字段的话，把「本次」写进去的瞬间「上次」就被覆盖了，
    # 首页什么时候来读都只能读到"刚启动"这一个时间。拆成两份就没这个顺序问题。
    lastLaunch = ConfigItem("App", "LastLaunch", "")   # 上一次启动
    thisLaunch = ConfigItem("App", "ThisLaunch", "")   # 本次启动

    # ---- RVC 变声引擎 ----
    # 工程目录（里面要有 runtime/、configs/、assets/、infer/）
    rvcRoot = ConfigItem("RVC", "Root", DEFAULT_RVC_ROOT)
    # 推理用的 Python 解释器，留空 = 自动用 <root>/runtime/python.exe
    rvcPython = ConfigItem("RVC", "Python", "")
    rvcOutputDir = ConfigItem("RVC", "OutputDir", "")

    # 记住上次的选择，下次打开直接接着用
    rvcModel = ConfigItem("RVC", "Model", "")
    rvcIndex = ConfigItem("RVC", "Index", "")

    # 硬件
    # 注：以前这里还有 rvcHalf（半精度）/ rvcF0Method（音高算法），它们是"文件
    # 转换"页的参数，那一页已经删了，留着就是点了没反应的开关，一并去掉。
    rvcUseDml = ConfigItem("RVC", "UseDml", False, BoolValidator())  # A 卡 / I 卡
    rvcWebPort = ConfigItem("RVC", "WebPort", 7865)

    # 推理参数
    rvcTranspose = ConfigItem("RVC", "Transpose", 0)                 # 变调，半音数
    rvcIndexRate = ConfigItem("RVC", "IndexRate", 0.66)              # 检索特征占比
    rvcFilterRadius = ConfigItem("RVC", "FilterRadius", 3)           # >=3 可去哑音
    rvcResampleSr = OptionsConfigItem(
        "RVC", "ResampleSr", 0, OptionsValidator(RESAMPLE_OPTIONS),
    )
    rvcRmsMixRate = ConfigItem("RVC", "RmsMixRate", 1.0)             # 响度对齐
    rvcProtect = ConfigItem("RVC", "Protect", 0.33)                  # 保护清辅音
    # 输出封装格式：wav / flac / mp3 / m4a
    rvcOutputFormat = OptionsConfigItem(
        "RVC", "OutputFormat", "wav", OptionsValidator(OUTPUT_FORMATS),
    )

    # ---- 训练（对应 RVC WebUI 的「训练」页）----
    trainExpDir = ConfigItem("Train", "ExpDir", "")                  # 实验名
    trainDatasetDir = ConfigItem("Train", "DatasetDir", "")          # 训练集目录
    trainSampleRate = OptionsConfigItem(
        "Train", "SampleRate", "48k", OptionsValidator(SAMPLE_RATES),
    )
    trainVersion = OptionsConfigItem(
        "Train", "Version", "v2", OptionsValidator(MODEL_VERSIONS),
    )
    trainF0 = ConfigItem("Train", "IfF0", True, BoolValidator())     # 是否音高引导
    trainF0Method = OptionsConfigItem(
        "Train", "F0Method", "rmvpe", OptionsValidator(F0_METHODS),
    )
    trainTotalEpoch = OptionsConfigItem(
        "Train", "TotalEpoch", 100, OptionsValidator(range(1, 1001)),
    )
    trainSaveEpoch = OptionsConfigItem(
        "Train", "SaveEpoch", 10, OptionsValidator(range(1, 101)),
    )
    trainBatch = OptionsConfigItem(
        "Train", "BatchSize", 4, OptionsValidator(range(1, 65)),
    )
    trainGpus = ConfigItem("Train", "Gpus", "0")                     # 训练卡号，如 0-1
    trainRmvpeGpus = ConfigItem("Train", "RmvpeGpus", "")            # rmvpe 专用卡号
    trainWorkers = ConfigItem("Train", "Workers", 4)                 # 并行进程数
    trainCache = ConfigItem("Train", "CacheGpu", True, BoolValidator())
    trainLatest = ConfigItem("Train", "SaveLatest", True, BoolValidator())
    trainSaveEvery = ConfigItem("Train", "SaveEveryWeights", False, BoolValidator())
    trainPretrainedG = ConfigItem("Train", "PretrainedG", "")        # 底模 G
    trainPretrainedD = ConfigItem("Train", "PretrainedD", "")        # 底模 D

    # ---- 人声 / 伴奏分离 & 去混响（PyMSS）----
    sepModel = ConfigItem("Sep", "Model", "")
    sepInputDir = ConfigItem("Sep", "InputDir", "")
    sepVocalDir = ConfigItem("Sep", "VocalDir", "")
    sepInstDir = ConfigItem("Sep", "InstDir", "")
    sepFormat = OptionsConfigItem(
        "Sep", "Format", "wav", OptionsValidator(OUTPUT_FORMATS),
    )

    # ---- 模型管理（ckpt 处理）----
    ckptModel = ConfigItem("Ckpt", "Model", "")
    ckptName = ConfigItem("Ckpt", "Name", "")
    ckptInfo = ConfigItem("Ckpt", "Info", "")
    ckptVersion = OptionsConfigItem(
        "Ckpt", "Version", "v2", OptionsValidator(MODEL_VERSIONS),
    )
    ckptSampleRate = OptionsConfigItem(
        "Ckpt", "SampleRate", "48k", OptionsValidator(SAMPLE_RATES),
    )
    ckptF0 = ConfigItem("Ckpt", "IfF0", True, BoolValidator())
    ckptMergeA = ConfigItem("Ckpt", "MergeA", "")
    ckptMergeB = ConfigItem("Ckpt", "MergeB", "")
    ckptAlpha = ConfigItem("Ckpt", "Alpha", 0.5)

    # ---- 实时变声（对应 RVC 实时控制台的全部参数）----
    rtcPth = ConfigItem("Rtc", "Pth", "")            # 模型完整路径
    rtcIndex = ConfigItem("Rtc", "Index", "")        # 索引完整路径
    rtcHostapi = ConfigItem("Rtc", "Hostapi", "")    # 设备类型（MME/WASAPI…）
    rtcExclusive = ConfigItem("Rtc", "Exclusive", False, BoolValidator())
    rtcInput = ConfigItem("Rtc", "Input", "")        # 输入设备名
    rtcOutput = ConfigItem("Rtc", "Output", "")      # 输出设备名
    rtcSrType = OptionsConfigItem(
        "Rtc", "SrType", "sr_model", OptionsValidator(["sr_model", "sr_device"]),
    )
    rtcFunction = OptionsConfigItem(
        "Rtc", "Function", "vc", OptionsValidator(["vc", "im"]),
    )  # vc=输出变声 im=输入监听

    # 常规设置
    rtcThreshold = ConfigItem("Rtc", "Threshold", -60)   # 响应阈值 dB
    rtcPitch = ConfigItem("Rtc", "Pitch", 0)             # 音调（半音）
    rtcFormant = ConfigItem("Rtc", "Formant", 0.0)       # 性别因子/声线粗细
    rtcIndexRate = ConfigItem("Rtc", "IndexRate", 0.3)   # 检索特征占比
    rtcRmsMixRate = ConfigItem("Rtc", "RmsMixRate", 0.18)  # 响度因子
    rtcF0Method = OptionsConfigItem(
        "Rtc", "F0Method", "fcpe", OptionsValidator(F0_METHODS),
    )

    # 性能设置
    rtcBlockTime = ConfigItem("Rtc", "BlockTime", 0.25)  # 采样长度 s
    rtcCrossfade = ConfigItem("Rtc", "Crossfade", 0.05)  # 淡入淡出 s
    rtcExtraTime = ConfigItem("Rtc", "ExtraTime", 2.5)   # 额外推理时长 s
    rtcINoise = ConfigItem("Rtc", "INoise", False, BoolValidator())
    rtcONoise = ConfigItem("Rtc", "ONoise", False, BoolValidator())
    rtcUsePv = ConfigItem("Rtc", "UsePv", False, BoolValidator())
    rtcAutoScroll = ConfigItem("Rtc", "AutoScroll", True, BoolValidator())
    # 桌面叠层：在主窗口看不见时（打游戏）显示算法延迟 / 推理时间
    rtcOverlay = ConfigItem("Rtc", "Overlay", False, BoolValidator())
    # 叠层外观：底板不透明度（%，0 = 只剩字）、文字颜色、贴屏幕哪个角
    rtcOverlayAlpha = RangeConfigItem(
        "Rtc", "OverlayAlpha", 60, RangeValidator(0, 100),
    )
    rtcOverlayColor = ColorConfigItem("Rtc", "OverlayColor", "#ffffff")
    rtcOverlayCorner = OptionsConfigItem(
        "Rtc", "OverlayCorner", "topLeft", OptionsValidator(OVERLAY_CORNERS),
    )


cfg = Config()

# ⚠️ 坑点：必须是 ``qconfig.load(文件, cfg)``，不能图省事写成 ``cfg.load(文件)``。
#
# 所有设置卡片（Switch / ComboBox / Color / Range）内部一律走全局的
# ``qconfig.set(...)``，而它只有在 ``qconfig._cfg 指向我们的 cfg`` 时才会：
#   · 把值写回**我们的**配置文件（否则写进 ./config/config.json 这种野路径，
#     重启后设置全丢）—— 项目里那个多出来的 config/ 目录就是这么来的；
#   · 发出 ``cfg.themeChanged`` / ``cfg.appRestartSig``。
#
# 少了这一行，改主题、改缩放点了完全没反应：值改了、信号不发、样式不刷。
qconfig.load(_config_path(), cfg)

# ⚠️ 第二个坑：我们为了改默认值，在 Config 里重写了 themeMode / themeColor，
# 于是 ``qconfig.themeMode``（基类那个 item）和 ``cfg.themeMode`` 是两个对象。
# 而库里的 ``setTheme()`` / ``setThemeColor()`` 写的是前者 —— 结果主题状态
# （qconfig.theme，样式靠它判断明暗）压根没变。这里把两者对上。
qconfig.themeMode = cfg.themeMode
qconfig.themeColor = cfg.themeColor


def configPath() -> str:
    """用户配置文件（config.json）的完整路径。"""
    return str(cfg.file)


def configDir() -> str:
    """配置目录：Windows 下是 %APPDATA%\\LinVox。"""
    return str(Path(cfg.file).parent)


def defaultConfigPath() -> str:
    """出厂默认配置文件（defaults.json）的完整路径。"""
    return str(Path(cfg.file).with_name("defaults.json"))


def _iterItems():
    """遍历 Config 上定义的所有配置项（含从 QConfig 继承的）。"""
    for name in dir(Config):
        item = getattr(Config, name)
        if isinstance(item, ConfigItem):
            yield item


def exportDefaults() -> str:
    """把出厂默认值写成一份 defaults.json，用户可以照着改回去。

    用 item.defaultValue（= 校验器修正过的默认值），而不是当前值，
    所以不管用户把配置改成什么样，这份文件永远是干净的出厂状态。
    """
    data = {}
    for item in _iterItems():
        group = data.setdefault(item.group, {})
        if item.name:
            group[item.name] = item.serializer.serialize(item.defaultValue)

    path = defaultConfigPath()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except OSError:
        pass
    return path


_saveTimer = None


def scheduleSave():
    """延迟落盘：1 秒内连着改十次也只写一次文件。

    各页面为了不卡界面，几乎都是 ``cfg.set(..., save=False)``，以前全靠关窗口
    时那一次 ``cfg.save()`` —— 一旦崩在关闭流程里（或被人强杀），这一轮的选择
    就全丢了。所以每次改完配置都调它兜底。
    """
    global _saveTimer
    if _saveTimer is None:
        _saveTimer = QTimer()
        _saveTimer.setSingleShot(True)
        _saveTimer.timeout.connect(cfg.save)
    _saveTimer.start(1000)


LAUNCH_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def recordLaunch() -> str:
    """记下本次启动时间，返回**上一次**启动的时间（首次启动返回空串）。

    首页「上次启动」那张卡读的是它，配合 :data:`LAUNCH_TIME_FORMAT` 解析。
    """
    previous = cfg.get(cfg.thisLaunch) or ""
    if previous != cfg.get(cfg.lastLaunch):
        cfg.set(cfg.lastLaunch, previous, save=False)
    cfg.set(cfg.thisLaunch, datetime.now().strftime(LAUNCH_TIME_FORMAT), save=False)
    scheduleSave()
    return previous


def resetConfig():
    """把所有配置恢复成出厂默认值，并立刻落盘。

    走 qconfig.set 而不是直接改 item.value：前者会发出 valueChanged，
    界面上的开关 / 下拉框才会跟着回到默认状态。
    """
    for item in _iterItems():
        default = item.defaultValue
        if cfg.get(item) == default:
            continue
        try:
            default = copy.deepcopy(default)
        except Exception:
            pass
        qconfig.set(item, default, save=False)   # 逐个不落盘，最后统一写一次
    cfg.save()


# 启动时刷新一份出厂默认，方便随时对照
exportDefaults()
