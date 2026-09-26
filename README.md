# LinVox

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![AI-Assisted](https://img.shields.io/badge/AI--Assisted-human%20in%20the%20loop-orange.svg)

> **AI-Assisted Project**
> 本项目源代码主要由 AI 辅助生成（人机协作方式），作者通过需求定义、架构决策、
> 代码审查与反复迭代参与创作。人工贡献清单见
> [docs/HUMAN_CONTRIBUTION.md](docs/HUMAN_CONTRIBUTION.md)，
> AI 使用痕迹存于 `docs/ai-logs/`。

基于 **PySide6 + QFluentWidgets** 的现代化桌面应用脚手架。开箱即有的能力：

- 无边框窗口 + 自绘标题栏（拖拽 / 双击最大化 / 边缘拉伸全部自带）
- 深色 / 浅色 / 跟随系统三档主题，强调色可自定义，切换不闪屏
- Win11 云母背景（Windows 11 自动启用，其他平台自动跳过）
- 侧边导航、卡片式仪表盘、Fluent 风格表格与设置面板
- 后台任务走 `QThread`，界面永不卡顿
- 配置自动持久化到用户目录（`qconfig`）
- **RVC 全功能内嵌**：实时变声控制台 / 文件批量变声 / 模型训练 / 人声伴奏分离去混响 / 模型管理，
  RVC 整个工程就放在软件目录里（`RVC20260718DML/`），不必再开网页端
  （**需自备，不随仓库分发**，见下方「自备 RVC 工程」）
- 界面配色随明暗主题自适应（`app/theme.py`），不再有写死的 `gray` / 固定 hex

## 目录结构

```
fluent_app/
├── main.py                  # 入口：高 DPI、主题恢复、启动窗口
├── requirements.txt
├── RVC20260718DML/          # 内嵌 RVC 工程（自带 runtime 解释器 + 模型目录）
└── app/
    ├── config.py            # 全局配置（qconfig 自动落盘）
    ├── theme.py             # 主题感知配色（次要文字 / 状态色）
    ├── window.py            # MainWindow：导航装配 + 云母 + 闪屏
    ├── rvc/
    │   ├── engine.py        # 引擎封装 + 实时变声控制器（QProcess 双向 JSON）
    │   ├── jobs.py          # 训练 / 分离 / 模型管理共用的任务控制器
    │   ├── rvc_boot.py      # 四个 worker 的公共引导（切目录 + 铺环境变量）
    │   ├── worker.py           # 文件变声 worker（RVC runtime 内运行）
    │   ├── realtime_worker.py  # 实时变声 worker（realtime_gui.py 的 headless 版）
    │   ├── train_worker.py     # 训练：切分 / 预处理 / 特征提取 / 训练 / 索引
    │   ├── sep_worker.py       # 人声伴奏分离 & 去混响（PyMSS）
    │   └── ckpt_worker.py      # 模型信息 / 改信息 / 提取小模型 / 融合
    └── views/
        ├── home_view.py     # 首页：横幅 + 统计卡 + 活动流
        ├── rtc_view.py      # 实时变声页：设备/参数/启停/延迟指标/日志
        ├── rvc_view.py      # 文件变声页：模型、参数、批量转换、日志
        ├── sep_view.py      # 人声分离页：模型、输入输出、进度、日志
        ├── train_view.py    # 训练页：切分/预处理/特征提取/训练/索引/一键训练
        ├── ckpt_view.py     # 模型管理页：信息/改信息/提取小模型/融合
        ├── task_view.py     # 任务管理器：实时显示 RVC 各任务的 CPU / 内存 / 显存占用
        ├── page.py          # 页面版式统一工具：卡片/标题/底栏/日志
        └── setting_view.py  # 设置页：SettingCard 系列
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate           # PowerShell / CMD
pip install -r requirements.txt
python LinVox.py
```

### 自备 RVC 工程（clone 后必读）

RVC 工程约 8.4 GB / 3.6 万个文件（内含 torch、faiss 与一套自带 runtime 解释器），
**不随仓库分发** —— 既超出 GitHub 单文件 100 MB 上限，也受上游协议约束。
clone 下来后自己放一份：

1. 把 RVC 工程目录放到**项目根目录**（与 `LinVox.py` 同级）；打包后则是
   **exe 同级目录**（`app/config.py` 的 `_projectRoot()` 按 exe 位置找）。
2. 目录名用 `RVC20260718DML` 或 `RVC20240604AMD_Intel`，启动会自动探测；
   用了别的名字就去「设置 → AI 变声 → RVC 工程目录」手动指定。
3. 不想复制 8 GB 又想本机直接跑，做目录联接即可：

   ```powershell
   mklink /J "RVC20260718DML" "D:\某个位置\RVC20260718DML"
   ```

**没有 RVC 工程时**：界面、主题、设置、导航全部正常，只有实时变声 / 文件变声 /
训练 / 分离 / 模型管理这五页会显示「引擎不可用」。

### Windows 11 用户必读

本项目的视觉效果在 Win11 上是**完整版**，其他平台会少几样东西：

| 效果 | Win11 | Win10 / macOS / Linux |
| --- | --- | --- |
| 云母（Mica）背景 | ✅ 窗口透出桌面壁纸的模糊影像 | ❌ 退化为纯色背景 |
| 无边框边缘拉伸 / 最大化按钮悬停预测 | ✅ 走 `pywin32` 原生消息 | ⚠️ 边缘拉伸变迟钝 |
| 窗口四角圆角 | ✅ 系统级圆角 | ❌ 直角 |

三个必须注意的点：

1. **必须装 `pywin32`**。它是 Win11 上的硬依赖——无边框窗口的边缘拉伸、最大化
   按钮的悬停预测、云母背景全走它。漏装不会报错，但窗口边角会失灵，排查起来很费劲。
2. **云母只在深色/浅色主题下都有效，但深色下最明显。** 想关掉去
   「设置 → 个性化 → 云母背景效果」，改完立刻生效（已接 `ConfigItem.valueChanged`）。
3. **打包后任务栏图标**：`LinVox.py` 里的 `_setWindowsAppId()` 就是为这个准备的。
   否则任务栏会显示 Python 的默认图标。这句必须在 `QApplication` 创建前调用，
   位置不能动。

> ⚠️ 不要同时安装 PyQt 版和 PySide 版的 Fluent Widgets——它们的包名都叫
> `qfluentwidgets`，混装会直接闪退。请为本项目单独建虚拟环境。

## 内置 RVC 变声

RVC 工程（默认 `RVC20260718DML/`）就放在软件目录里，启动即自动探测，
换台电脑 / 换个盘符都不会变成「引擎不可用」（仍可在「设置 → AI 变声」里改目录）。
围绕它做了五个核心页面：

### 实时变声（对标 RVC 官方实时控制台的全部功能）

| 分区 | 内容 |
| --- | --- |
| 加载模型 | 模型 .pth / 索引 .index（下拉扫描默认目录，也可浏览任意文件） |
| 音频设备 | 设备类型（MME/WASAPI/…）、独占 WASAPI、输入/输出设备、重载列表、模型/设备采样率 |
| 常规设置 | 响应阈值、音调、性别因子/声线粗细、检索特征占比、响度因子、音高算法（pm / rmvpe / fcpe） |
| 性能设置 | 采样长度、淡入淡出长度、额外推理时长、输入/输出降噪、相位声码器 |
| 运行控制 | 开始/停止音频转换、输入监听 ↔ 输出变声、算法延迟与推理时间实时显示 |
| 运行日志 | 实时日志、清空、自动滚动 |

交互细节：**热更新参数**（音调、阈值、检索占比、响度因子、音高算法、降噪、
相位声码器、监听/变声）拖动立即生效，不用重启音频流；性能参数与设备改动
提示"重新开始后生效"。所有选择和参数都会记住。

### 文件变声（批量转换）

单文件或整个文件夹加进列表 → 选模型/索引/参数 → 批量转换，
输出格式可选 wav / flac / mp3 / m4a，进度条 + 日志实时反馈，完成后一键打开输出目录。

### 模型训练（对应 WebUI「训练」页）

| 分区 | 内容 |
| --- | --- |
| 数据切分 | 输入目录、切分阈值/最小长度/ hop 等、输出目录、开始切分 |
| 数据集与实验 | 实验名（可下拉选已有）、训练集目录、采样率 32k/40k/48k、版本 v1/v2、音高引导开关与算法 |
| 训练参数 | 总 epoch、保存间隔、batch、GPU 卡号、底模 G/D、显存缓存、只存最新、每次都存 |
| 运行 | 数据预处理 / 特征提取（F0+Hubert）/ 训练模型 / 训练索引 / **一键训练** / 停止 / 进度 / 日志 |

### 人声伴奏分离 & 去混响（对应 WebUI「PyMSS」页）

选分离模型 → 选文件或整个文件夹 → 指定人声目录与伴奏目录 → 开始分离，
逐个文件回传进度与成功状态，可一键打开人声目录。

### 模型管理（对应 WebUI「ckpt 处理」页）

读取模型信息（采样率 / 版本 / 是否音高引导 / 训练轮数）、修改模型信息、
提取小模型（去掉优化器状态，体积减半）、两个模型按比例融合。

### 为什么不把 torch 装进本项目的环境？

RVC 自带一套 `runtime/` 解释器（Python 3.9 + torch + faiss），而界面跑在另一个
Python 上。所以界面与推理严格分进程，两条 worker 各管一摊：

```
界面进程（本项目的 Python）                 RVC runtime（RVC 的 Python 3.9）
┌──────────────────────────┐               ┌────────────────────────────┐
│ RtcInterface ─ RealtimeController        │ realtime_worker.py          │
│   设备下拉 ◀── devices 事件   ──▶        │ sounddevice + rtrvc 实时推理 │
│   开始/停止 ── start/stop ──▶             │ 门限/降噪/SOLA/相位声码器     │
│   延迟/耗时 ◀── metrics      ◀──          │ 算法延迟、推理耗时           │
├──────────────────────────┤               ├────────────────────────────┤
│ RvcInterface ─ RvcEngine │  ── 启动 ──▶  │ worker.py                   │
│   进度条/日志 ◀── JSON 行  ◀──           │ Config → VC → 写音频        │
├──────────────────────────┤               ├────────────────────────────┤
│ Train/Sep/Ckpt 三页      │  ── 启动 ──▶  │ train_worker / sep_worker / │
│   ─ JobController        │  ◀── JSON ──  │ ckpt_worker（切分/训练/分离/ │
│   进度/日志/结果          │               │ 索引/改信息/融合）           │
└──────────────────────────┘               └────────────────────────────┘
```

好处：界面进程零 AI 依赖；转换/变声再重界面也不卡；RVC 升级不影响界面环境。
实时 worker 还是**懒加载**的：启动只拉起 sounddevice（秒级），点"开始"才加载
torch 和模型，所以打开页面就能枚举设备。

### 踩过的坑（已修，细节留在源码注释里）

1. **模型路径拼成 `None/xxx.pth`**：`load_dotenv()` 默认按"调用者文件所在目录"
   向上找 `.env`，worker 脚本在 LinVox 目录下，必须显式
   `load_dotenv(root/.env)`。
2. **`--rvc-root` 让进程悄悄退出**：RVC 的 `Config` 内部还会跑一次 argparse，
   看到我们的参数直接 `sys.exit(2)`——两个 worker 都要在 import Config 前
   `sys.argv = sys.argv[:1]`。症状是"worker 收到 start 命令却毫无反应"。
3. **纯文件名被当成带目录的路径**：`weight_root` 只在 model 真带目录时才覆盖，
   否则保留 `.env` 里的 `assets/weights`。
4. **回调线程里别 import**：音频回调跑在 PortAudio 线程，librosa/torch 等
   引用在 start() 里提前绑定到 `self`。
5. **runtime 是隔离解释器，脚本目录不在 `sys.path` 里**：`runtime/python.exe`
   带 `pythonXX._pth`，不会把「被执行脚本所在目录」自动加进 `sys.path`，
   所以四个 worker 都得先 `sys.path.insert(0, os.path.dirname(__file__))`
   才能 `import rvc_boot`。症状是 worker 一启动就 `ModuleNotFoundError`。
6. **2026/07 版 RVC 不再自带 `.env`**：`configs/config.py` 在 import 时就要读
   `weight_root` / `index_root`，读不到就是 `None`，模型路径拼成 `None/xxx.pth`。
   由 `engine.DEFAULT_RVC_ENV` + `rvc_boot.setup()` 双重兜底。
7. **`harvest` / `crepe` 已被上游移除**：界面与 worker 统一只提供
   `pm / rmvpe / fcpe`，实时页的「harvest 进程数」一并删除。

## 运行测试

```bash
python tests/test_smoke.py
# 或：pytest tests/test_smoke.py -s

# 想连真实推理一起验（会加载 torch，慢但最实在）：
#   文件变声：FLUENT_RVC_E2E=1
FLUENT_RVC_E2E=1 python tests/test_smoke.py
```

覆盖 109 项检查：模块导入、页面装配、导航高亮同步、后台线程推进度、
配置持久化、深浅主题切换与配色自适应、设置页交互、文件变声引擎（自检 /
模型扫描 / 参数拼装 / 端到端推理）、实时变声（worker 协议握手 / 真实设备枚举 /
QProcess 通信 / 参数持久化）、训练 / 分离 / 模型管理三页装配与 worker 语法、
关闭落盘。无需显示器，走 offscreen。测试配置写到临时目录，跑完自动清理。

## 配置文件在哪

| 平台 | 路径 |
| --- | --- |
| Windows | `%APPDATA%\FluentApp\config.json` |
| macOS | `~/Library/Application Support/FluentApp/config.json` |
| Linux | `~/.config/FluentApp/config.json` |

首次运行不会生成，改过一次设置并关闭窗口后才有。

## 二次开发怎么改

| 你想做的事 | 改哪里 |
| --- | --- |
| 加一个页面 | 在 `app/views/` 新建 `XxxInterface`，再到 `window.py` 的 `initNavigation()` 里 `addSubInterface(...)` |
| 换 RVC 工程目录 | 设置 → AI 变声 → RVC 工程目录（默认按 `config.py` 的 `RVC_DIR_CANDIDATES` 自动探测） |
| 加一个 RVC 参数 | `config.py` 加 `ConfigItem` → `rvc/engine.py` 的 `InferParams`（含 `to_args`）→ `views/rvc_view.py` 加控件 |
| 改推理脚本协议 | `app/rvc/worker.py` 的 `emit()` 与 `rvc/engine.py` 的 `_handleLine()` 成对修改 |
| 加一个训练/分离类任务 | `app/rvc/xxx_worker.py` 加一个 job → `jobs.py` 的 `JobController` 复用即可；页面照 `train_view.py` 抄 |
| 改次要文字/状态色 | 一律走 `app/theme.py` 的 `applyMuted` / `setStatus`，别再写 `color: gray` |
| 保持全站版式一致 | 所有页面都走 `app/views/page.py` 的 `makeCard` / `makeHeader` / `makeFooterRow` / `makeLogCard` |
| 加一项设置 | 在 `app/config.py` 加一个 `ConfigItem`，再到 `setting_view.py` 加一张卡片 |
| 换强调色 | `config.py` 的 `themeColor` 默认值，或运行时在「设置 → 主题强调色」里挑 |
| 改窗口尺寸 | `window.py` → `initWindow()` 的 `resize()` / `setMinimumSize()` |
| 加业务逻辑 | 写在 view 里；耗时操作一律塞进 `QThread` / `QProcess`，参考 `app/rvc/` 各 worker |

## 打包分发

仓库里已经备好了 `LinVox.spec`，一条命令出 **`dist/LinVox/LinVox.exe`**（onedir，约 160 MB）：

```bash
pip install pyinstaller
pyinstaller LinVox.spec --noconfirm
```

> ⚠️ 跑完请用 **`dist/LinVox/LinVox.exe`**（最终产物）。
> 不要双击 `build/LinVox/LinVox.exe`——那是 PyInstaller 的中间壳，`_internal` 目录里
> 没放 Python DLL，直接打开会报「Failed to load Python DLL」。

`LinVox.spec` 里三条不能动的约定：

1. **RVC 工程（8 GB，含 torch）不打进包**。它是数据目录，放在 exe 旁边即可：
   `app/config.py` 的 `_projectRoot()` 在冻结状态下按 **exe 所在目录** 找它
   （不是 `_internal/`），所以一打包就"引擎不可用"的坑已经堵上了。
   本地调试不想复制 8 GB，建个目录联接就行：

   ```powershell
   New-Item -ItemType Junction -Path dist\LinVox\RVC20260718DML `
            -Target (Resolve-Path RVC20260718DML)
   ```

2. **`app/rvc/*_worker.py` 必须当数据文件带上**。它们是给子进程（RVC 自带的
   runtime 解释器）跑的源码，主程序根本不 import，漏了就是"点了开始没反应"。

3. **`console=False`**：图形程序不要黑框。qfluentwidgets 的样式/图标都在
   `resource.py`（3 MB 的 Qt 资源，本身就是 .py），跟着字节码进 PYZ，不用额外
   `--collect-all`。

想出单文件（启动会慢几秒，每次运行要解包）：把 spec 里 `EXE(...)` 的
`exclude_binaries=True` 去掉、把 `a.binaries` 挪进 `EXE`，并删掉末尾的 `COLLECT`。

### 做成安装包（Inno Setup）

PyInstaller 出的是一堆散文件，分发走 `installer\LinVox.iss`（Inno Setup 6）：

```powershell
# 装编译器（只需一次）
winget install --id JRSoftware.InnoSetup -e --scope user

.\installer\build_installer.ps1      # → installer\Output\LinVox_Setup_1.0.0.exe
```

成品 46 MB：安装向导、桌面/开始菜单快捷方式（桌面快捷方式默认不勾选）、
控制面板可卸载、卸载判决书带得走。三条约定：

1. **`Files` 段不能递归扫 `dist\LinVox\*`**。那里有个指向 8.4 GB RVC 工程的
   **目录联接**，Inno 会跟着联接往下扫，安装包直接变几个 G。脚本显式列
   `LinVox.exe` + `_internal`，天然绕开。
2. **默认装到 `%LOCALAPPDATA%\Programs\LinVox`**（`PrivilegesRequired=lowest`）。
   RVC 运行时要往自己目录里写 logs / TEMP / assets，装进 Program Files 会因
   权限不足哑火。
3. **RVC 工程（8.4 GB）不在安装包里**。安装后根目录会留一份
   `RVC_NOTICE.txt`，照着把 `RVC20260718DML\` 拷到 exe 同级即可解锁全部 AI
   功能；卸载主程序不会删它，也不会删 `%APPDATA%\LinVox` 下的用户配置。

> ⚠️ `installer\` 下带中文的脚本 / 语言文件**必须存成带 BOM 的 UTF-8**，
> 否则 Inno 与 PowerShell 都会按 ANSI 解码，中文直接乱码。

**Nuitka**（体积小、启动快、几乎不可反编译，商业分发推荐）：

```bash
python -m nuitka --standalone --enable-plugin=pyside6 ^
  --windows-disable-console --windows-icon-from-ico=app.ico main.py
```

Windows 下若亚克力/云母背景失效，补一句 `--include-package=pywin32` 或
`--collect-all qfluentwidgets` 确保资源被带上。

## 许可证

本项目以 **GPL-3.0-or-later** 整库开源，全文见 [LICENSE](LICENSE)。

- UI 组件：[PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)（GPLv3，© 2021 zhiyiYo）
- Python 绑定：[PySide6](https://doc.qt.io/qtforpython-6/)（LGPLv3 / GPLv2 / GPLv3 三选一，© The Qt Company；本项目取 LGPLv3 这一支）
- 完整依赖、版本与协议清单见 [NOTICE](NOTICE)

打包分发时的义务：保留 Qt 动态库可替换（不静态链接进单一可执行文件），
并在分发目录内附带 `LICENSE` 与 `NOTICE`。

## 效果预览

截图不随仓库分发（含本机窗口内容与个人文件）。想看真实效果直接跑起来：

```bash
python LinVox.py                    # 源码运行
dist\LinVox\LinVox.exe              # 或按上文打包后运行
```

Windows 11 上会额外获得云母 / 亚克力等系统级视觉效果。

## 已踩过并修好的坑

这两个坑在初版代码里真实存在，测试跑出来才暴露，改动都留在源码注释里了：

1. **`cfg.save()` 抛 `TypeError: Object of type Theme is not JSON serializable`**
   `QConfig` 基类自带 `themeMode` / `themeColor` 且配好了序列化器。子类为了改
   默认值而重定义这两个字段时，**必须手动补上 `EnumSerializer(Theme)` 和
   `ColorConfigItem`**，否则覆盖了基类定义却丢了序列化器 —— 表现为"改完主题
   关闭窗口，设置没保存下来"（还会抛异常）。

2. **配置路径跟着脚本名走**
   `QStandardPaths.AppConfigLocation` 在未设置 `organizationName` /
   `applicationName` 时会把 `sys.argv[0]` 拼进路径（`~/.config/main.py/...`）。
   结果从源码跑、打包成 exe 跑、或改个文件名，配置目录就变了，用户设置"丢失"。
   现在按平台自己拼固定目录，任何启动方式下路径一致。

顺带修的：云母开关此前只在启动时读一次，**设置页改了要重启才生效**；
现在监听 `ConfigItem.valueChanged`，切换即时生效。
