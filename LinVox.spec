# -*- mode: python ; coding: utf-8 -*-
"""LinVox 打包配置（PyInstaller 6.x）。

用法::

    pyinstaller LinVox.spec --noconfirm       # 产出 dist/LinVox/LinVox.exe

几个**必须这么写**的点：

1. RVC 工程（8GB，含 torch）**不打进包**：它是数据目录，放在 exe 旁边就行，
   ``app.config._projectRoot()`` 在冻结状态下按 exe 位置去找它。
2. ``app/rvc/*_worker.py`` 是给**子进程**跑的源码（跑在 RVC 自带的 runtime
   解释器里），主程序根本不 import 它们 —— 不当数据文件带上，subprocess 就
   找不到脚本，实时变声 / 训练 / 分离全哑火。
3. qfluentwidgets 的样式 / 图标都在 ``qfluentwidgets/resource.py``（3MB 的 Qt
   资源，本身就是 .py），跟着字节码进 PYZ，不需要额外 collect_data_files。
4. ``console=False``：图形程序不要黑框。
"""
# 给 RVC runtime 解释器跑的 worker 源码（见上面第 2 点）
worker_datas = [
    (src, "app/rvc")
    for src in (
        "app/rvc/realtime_worker.py",
        "app/rvc/sep_worker.py",
        "app/rvc/train_worker.py",
        "app/rvc/ckpt_worker.py",
        "app/rvc/rvc_boot.py",
    )
]

datas = [("app/resources", "app/resources")] + worker_datas

a = Analysis(  # noqa: F821
    ["LinVox.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    # 跟随系统主题靠它（qfluentwidgets 内部 import，PyInstaller 静态分析不到）
    hiddenimports=["darkdetect"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # torch 等只在 RVC 自己的 runtime 里用，界面进程不许碰
    excludes=["torch", "tensorflow", "scipy", "matplotlib", "tkinter",
              "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinVox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # 图形界面：不要控制台黑框
    disable_windowed_traceback=False,
    icon="app/resources/logo.ico",
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LinVox",
)
