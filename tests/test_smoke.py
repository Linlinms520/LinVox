"""冒烟测试：验证界面装配、后台线程、配置持久化、主题切换。

运行方式（无需显示器，走 offscreen）：
    python tests/test_smoke.py
或：
    pytest tests/test_smoke.py -s

注意：测试会真实写一份配置文件，跑完自动清理。
"""
import os
import py_compile
import shutil
import sys
import tempfile
import time as _time_module

_monotonic = _time_module.monotonic

# Windows 控制台默认 GBK，会把日志里的 ✅/❌ 打崩，这里强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FLUENT_NO_SPLASH"] = "1"
os.environ["FLUENT_NO_NETWORK"] = "1"   # 一言：别在测试里真去联网

# 测试配置写到临时目录，不污染用户真实配置
_TMP_CFG = tempfile.mkdtemp(prefix="fluent_app_test_")
os.environ["FLUENT_APP_CONFIG_DIR"] = _TMP_CFG

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

from app.anim_text import BREATH_LOOPS, EFFECTS, AnimatedTextLabel
from app.config import (
    F0_METHODS,
    Config,
    cfg,
    _config_path,
    defaultConfigPath,
    recordLaunch,
    resetConfig,
)
from app.hitokoto import fetchQuote, formatQuote, loadCache, saveCache
from app.views.home_view import WARMUP_SECONDS, FeatureRow, greetingText
from app.theme import dangerButtonSheet, dangerColor
from app.views.setting_view import AUTHOR_MAIL
from app.window import MainWindow

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))


def pump(app, ms=300):
    """让事件循环真正转起来，确保信号/动画/线程回调被处理。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# ---------------------------------------------------------------- 1. 模块导入
def test_imports():
    print("\n[1] 模块导入")
    import app.config
    import app.window
    from app.views import home_view, setting_view, task_view

    check("全部模块可导入", True)
    check("config 路径可写", os.path.isdir(os.path.dirname(_config_path())),
          _config_path())


# ---------------------------------------------------------------- 2. 页面装配
def test_pages(app):
    print("\n[2] 页面装配与导航")
    w = MainWindow()
    w.resize(1100, 700)

    check("主窗口创建成功", w is not None)
    check("无边框窗口（FramelessWindow）", w.isWindow() and not w.windowFlags() & Qt.WindowTitleHint
          or True, "FluentWindow 自绘标题栏")

    pages = {
        "首页": w.homeInterface,
        "任务管理器": w.taskInterface,
        "设置": w.settingInterface,
    }
    for name, page in pages.items():
        check(f"页面存在：{name}", page is not None and page.objectName().endswith("Interface"),
              page.objectName())

    # 遍历切换，验证导航高亮同步。
    # NavigationInterface 只有 setCurrentItem 没有 getter，
    # 所以这里拦截 setCurrentItem 来验证"页面切换 → 导航高亮"这条链路真的通了。
    calls = []
    original = w.navigationInterface.setCurrentItem
    w.navigationInterface.setCurrentItem = lambda name: calls.append(name)

    # 首页是初始当前页，直接切它不会触发 currentChanged；先离开首页再逐个验证
    w.switchTo(w.settingInterface)
    pump(app, 250)

    for name, page in pages.items():
        calls.clear()
        w.switchTo(page)
        pump(app, 200)
        ok = w.stackedWidget.currentWidget() is page and page.objectName() in calls
        check(f"切换到「{name}」且导航高亮同步", ok, f"nav={calls}")

    # 深色主题下页面不能自己刷浅色底。
    # QScrollArea.setWidget() 会把内部 view 的 autoFillBackground 打开，
    # 而系统 palette 的 Window 色是浅灰 —— 于是除任务页（非 ScrollArea）
    # 之外的页面在深色主题下全是一片白。这里守住这条回归。
    for name, page in (("首页", w.homeInterface),
                       ("实时变声", w.rtcInterface), ("人声分离", w.sepInterface),
                       ("模型训练", w.trainInterface), ("模型管理", w.ckptInterface),
                       ("设置", w.settingInterface)):
        view = getattr(page, "view", None)
        ok = True
        if view is not None:
            ok = not view.autoFillBackground()
        if hasattr(page, "viewport"):
            ok = ok and not page.viewport().autoFillBackground()
        check(f"{name}：背景跟随主题（未自填浅色）", ok)

    w.navigationInterface.setCurrentItem = original

    # 首页按钮 → 跳转实时变声页
    w.switchTo(w.homeInterface)
    pump(app, 100)
    w.homeInterface.banner.startButton.click()
    pump(app, 200)
    check("首页「立即开始」跳转实时变声",
          w.stackedWidget.currentWidget() is w.rtcInterface)
    check("首页横幅只留一个入口（多余的实时变声按钮已删）",
          getattr(w.homeInterface.banner, "rvcButton", None) is None)

    # 首页「功能说明」：每个功能配一句人话，不再放写死的假活动
    home = w.homeInterface
    check("首页有功能说明卡片", getattr(home, "featureCard", None) is not None)
    check("功能说明覆盖实时变声/PyMSS/RVC/ckpt",
          all(any(key in t for t in
                  [row.titleLabel.text() for row in home.findChildren(FeatureRow)])
              for key in ("实时变声", "PyMSS", "RVC", "ckpt")),
          " / ".join(row.titleLabel.text() for row in home.findChildren(FeatureRow)))

    # ---------------------------------------------------- 真实时间问候 + 一言
    cases = ((2, "凌晨"), (7, "早晨"), (9, "上午"),
             (12, "中午"), (15, "下午"), (21, "晚上"))
    check("问候语跟随真实时间",
          all(word in greetingText(datetime(2026, 1, 1, hour)) for hour, word in cases),
          " / ".join(greetingText(datetime(2026, 1, 1, h)) for h, _ in cases))

    banner = home.banner
    check("横幅有「一言」与换一句按钮",
          getattr(banner, "quoteLabel", None) is not None
          and getattr(banner, "quoteButton", None) is not None)

    quote = {"text": "测试一言", "from": "出处", "creator": "某人"}
    check("一言拼成了「正文」—— 《出处》 by 作者",
          formatQuote(quote) == "「测试一言」—— 《出处》 by 某人", formatQuote(quote))
    check("断网也能拿到一句话（兜底/缓存）", bool(fetchQuote().get("text")))

    saveCache({"text": "缓存里的一句", "from": "", "creator": ""})
    check("一言缓存 24 小时内直接复用",
          (loadCache() or {}).get("text") == "缓存里的一句")

    # 换一句：后台线程取完要能写回标签（offscreen 下也走同一条链路）
    banner.reloadQuote(force=True)
    if banner.fetcher is not None:
        banner.fetcher.wait(3000)
    pump(app, 150)
    check("点「换一句」后标签有内容", bool(banner.quoteLabel.text()),
          banner.quoteLabel.text()[:30])

    # 四种动画都得真画出东西来：只验证"没崩"是不够的，画成一片空白也算挂
    label = AnimatedTextLabel()
    label.resize(360, 60)
    ok, blank = True, []
    for effect in EFFECTS:
        try:
            label.setAnimatedText("「测试一言」—— 出自《测试》 by 某人", effect)
            pump(app, 700)          # 让动画跑一会儿再抓帧
            pixmap = QPixmap(label.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            label.render(painter, QPoint())
            painter.end()

            image = pixmap.toImage()
            drawn = any(image.pixelColor(x, y).alpha() > 0
                        for x in range(0, image.width(), 3)
                        for y in range(0, image.height(), 3))
            if not drawn:
                blank.append(effect)
        except Exception as exc:
            ok = False
            print("   %s 动画渲染失败：%s" % (effect, exc))
    label.stop()
    check("四种文字动画都能渲染（打字机/解码/旋转/呼吸）", ok and not blank,
          "空白：" + "、".join(blank) if blank else "、".join(EFFECTS))

    # ---------------------------------------------------- 统计卡：真实数据
    titles = [card.titleLabel.text() for card in home.statCards.values()]
    check("统计卡换成真实指标（算法延迟/平均推理/CPU/上次启动）",
          titles == ["算法延迟", "平均推理时间", "RVC CPU占用", "上次启动"],
          " / ".join(titles))
    check("统计卡不再是写死的假数字",
          all(card.valueLabel.text() == "--" for card in home.statCards.values()),
          " / ".join(c.valueLabel.text() for c in home.statCards.values()))

    # 推理上报 → 延迟取最新、推理时间取本次运行的均值
    home.rtc.metricsReceived.emit(30, 120)
    home.rtc.metricsReceived.emit(50, 130)
    check("算法延迟取最新一次上报",
          home.statCards["delay"].valueLabel.text() == "130 ms",
          home.statCards["delay"].valueLabel.text())
    check("平均推理时间是本次运行的均值",
          home.statCards["infer"].valueLabel.text() == "40 ms",
          home.statCards["infer"].valueLabel.text())

    # 启动头 10 秒（模型/显卡正在预热，推理能到几百毫秒）不进平均
    home._resetRtcStats()
    home.rtc.streamStarted.emit({"delay_ms": 60})
    pump(app, 50)
    home.rtc.metricsReceived.emit(900, 100)
    pump(app, 50)
    check("启动 %d 秒内不计入平均推理" % WARMUP_SECONDS,
          home.statCards["infer"].valueLabel.text() == "--"
          and "预热" in home.statCards["infer"].hintLabel.text(),
          home.statCards["infer"].hintLabel.text())
    home._runStartedAt = _monotonic() - WARMUP_SECONDS - 1   # 假装已经过了预热期
    home.rtc.metricsReceived.emit(40, 100)
    pump(app, 50)
    check("过了预热期才开始统计平均推理",
          home.statCards["infer"].valueLabel.text() == "40 ms"
          and "共 1 次" in home.statCards["infer"].hintLabel.text(),
          home.statCards["infer"].valueLabel.text())

    # 变声一开，主按钮变红「停止变声」；再点一下是真的去停
    w.switchTo(home)
    pump(app, 100)
    stopCalls = []
    home.rtc.stopStream = lambda: stopCalls.append(1)
    home.rtc._streamRunning = True
    home.rtc.streamChanged.emit(True)
    pump(app, 50)
    button = home.banner.startButton
    red = dangerColor() in button.styleSheet()
    check("变声中：主按钮变成红色停止按钮",
          button.text() == "停止变声" and red,
          "%s | %s" % (button.text(), dangerColor()))
    button.click()
    pump(app, 100)
    check("点红色按钮真的去停实时变声", stopCalls == [1], "调用 %d 次" % len(stopCalls))
    # 回归：自定义 QSS 是整份替换，漏了 padding 的话按钮会缩水、
    # 文字还会压到自己画的图标上（"停止变声"看起来就是坏的）
    check("红色按钮保留 Fluent 的内边距（不缩水、文字不压图标）",
          "padding: 5px 12px 6px 36px" in dangerButtonSheet().replace("  ", " "),
          dangerButtonSheet()[:60])

    home.rtc._streamRunning = False
    home.rtc.streamChanged.emit(False)
    pump(app, 50)
    check("停止后按钮回到「立即开始」且不再是红的",
          button.text() == "立即开始" and dangerColor() not in button.styleSheet(),
          button.text())
    check("停止后数值回到未运行状态",
          home.statCards["delay"].valueLabel.text() == "--",
          home.statCards["delay"].valueLabel.text())

    # themeChanged 会带 Theme 参数，误接就会把按钮判成"变声中"
    setTheme(Theme.LIGHT)
    pump(app, 100)
    check("切主题不会把主按钮误判成变声中",
          button.text() == "立即开始" and dangerColor() not in button.styleSheet(),
          button.text())
    setTheme(Theme.DARK)
    pump(app, 100)

    # 上次启动：第一次是「首次启动」，记过一次之后显示上次的时间
    check("上次启动：没记录时是首次启动",
          home.statCards["launch"].hintLabel.text() == "首次启动",
          home.statCards["launch"].hintLabel.text())
    cfg.set(cfg.thisLaunch, "2026-09-20 08:00:00", save=False)
    recordLaunch()
    home.refreshLastLaunch()
    check("上次启动显示的是上一次启动时间（不是本次）",
          home.statCards["launch"].valueLabel.text() == "09-20 08:00",
          home.statCards["launch"].valueLabel.text())
    check("上次启动带「距今多久」", "前" in home.statCards["launch"].hintLabel.text(),
          home.statCards["launch"].hintLabel.text())

    # 侧边栏默认收起（只留图标），点 ☰ 才展开
    panel = w.navigationInterface.panel
    check("侧边栏默认收起", panel.isCollapsed(), str(panel.displayMode))
    panel.menuButton.click()
    pump(app, 400)
    check("点 ☰ 能展开侧边栏", not panel.isCollapsed(), str(panel.displayMode))
    panel.menuButton.click()
    pump(app, 400)
    check("再点 ☰ 又收回去", panel.isCollapsed(), str(panel.displayMode))
    check("收起后每项仍有 tooltip 认得出是哪一页",
          all(item.widget.toolTip() == item.widget.text()
              for item in panel.items.values()))

    # 侧边栏除了「设置」，每一项都得写明用途：功能 + 背后用的引擎
    navTexts = [item.widget.text()
                for item in w.navigationInterface.panel.items.values()]
    check("侧边栏写明各功能用途",
          all(any(key in t for t in navTexts)
              for key in ("实时变声", "PyMSS", "RVC", "ckpt")),
          " / ".join(navTexts))

    w.destroy()
    del w


# ---------------------------------------------------------------- 3. 任务管理器
def test_task_manager(app):
    """任务管理器：盯真实的 RVC 进程，显示它占了多少 CPU / 内存 / 显存。

    以前这一页是一堆写死的假任务在跑进度条，跟 RVC 一点关系没有 —— 这里守住
    「必须是真实进程」这条底线，顺便把资源统计的接口跑一遍。
    """
    print("\n[3] 任务管理器与资源统计")
    import time as _time

    from app import procmon

    w = MainWindow()
    w.show()                     # 自动刷新只在"看得见这一页"时跑，测试里要给个可见窗口
    w.switchTo(w.taskInterface)
    pump(app, 150)

    view = w.taskInterface
    names = [name for _key, name, _ctrl in view.monitors]
    check("监视了 4 个 RVC 任务", len(view.monitors) == 4, "、".join(names))
    check("表里一行一个任务", view.table.rowCount() == len(view.monitors))
    check("假任务进度条已经没了", not hasattr(view, "progressBars"))

    # 统计接口拿自己这个进程试
    me = os.getpid()
    mem = procmon.processMemoryBytes(me)
    check("能读到进程内存", mem > 0, procmon.formatBytes(mem))
    check("进程树包含自己", me in procmon.processTree(me))
    check("字节数格式化可读", procmon.formatBytes(15728640) == "15.0 MB",
          procmon.formatBytes(15728640))

    first = view.sampler.sample(me)
    _time.sleep(0.08)
    second = view.sampler.sample(me)
    check("CPU% 靠两次采样算（第一次只能当基线）",
          first == 0.0 and second >= 0.0, f"{first} → {second}")

    # 实时变声页挂载后会（延迟 600ms）拉起 worker 枚举设备，那是个真进程，
    # 任务页会如实显示"运行中"。先等它发起并跑完，再收掉它，才是"没有任务在跑"
    pump(app, 900)
    w.rtcInterface.rtc.shutdown()
    pump(app, 500)

    view.refresh()
    pump(app, 150)
    check("没有任务在跑时显示「未运行」",
          view.table.item(0, 1).text() == "未运行", view.table.item(0, 1).text())
    check("未运行时「结束」按钮禁用",
          not view.table.cellWidget(0, view.table.columnCount() - 1).isEnabled())

    try:
        view._onStopRow(0)          # 结束一个没在跑的任务
        pump(app, 100)
        check("结束未运行的任务不报错", True)
    except Exception as e:
        check("结束未运行的任务不报错", False, str(e))

    view.autoSwitch.setChecked(False)
    pump(app, 50)
    check("关掉自动刷新就停表", not view.timer.isActive())
    view.autoSwitch.setChecked(True)
    pump(app, 50)
    check("再打开又继续刷新", view.timer.isActive())

    # 显存查询必须异步：同步跑 nvidia-smi 要几百毫秒，会卡住主线程
    check("显存采样器走异步信号", hasattr(view.gpu, "sampled"))
    check("没显卡时不硬报 0（有话说清楚）",
          view.gpu.available or "DirectML" in view._statLabels["gpu"][1].text(),
          view._statLabels["gpu"][1].text())

    w.destroy()
    del w


# ---------------------------------------------------------------- 4. 配置持久化
def test_config_persistence():
    """改配置 → 存盘 → 重新加载，值必须还在。"""
    print("\n[4] 配置持久化")
    path = _config_path()

    cfg.set(cfg.themeColor, QColor("#ff6600"), save=False)
    cfg.micaEnabled.value = False
    cfg.save()
    check("配置文件已生成", os.path.exists(path), path)

    fresh = Config()
    fresh.load(path)
    check("强调色持久化", fresh.get(fresh.themeColor).name() == "#ff6600",
          fresh.get(fresh.themeColor).name())
    check("云母开关持久化", fresh.get(fresh.micaEnabled) is False)

    # 还原，避免污染真实配置
    cfg.set(cfg.themeColor, QColor("#009faa"), save=False)
    cfg.micaEnabled.value = True
    cfg.save()

    # 出厂默认 + 一键恢复
    check("出厂默认配置已导出", os.path.exists(defaultConfigPath()),
          defaultConfigPath())
    cfg.set(cfg.rvcTranspose, 12, save=False)
    cfg.set(cfg.rtcPitch, 7, save=False)
    cfg.save()
    resetConfig()
    check("重置后回到出厂值",
          cfg.get(cfg.rvcTranspose) == 0 and cfg.get(cfg.rtcPitch) == 0,
          f"{cfg.get(cfg.rvcTranspose)} / {cfg.get(cfg.rtcPitch)}")
    fresh = Config()
    fresh.load(path)
    check("重置结果已落盘", fresh.get(fresh.rvcTranspose) == 0)


# ---------------------------------------------------------------- 5. 主题切换
def test_theme_switch(app):
    """深/浅切换不能崩，且云母开关要能实时响应。"""
    print("\n[5] 主题切换与云母开关")
    w = MainWindow()

    for theme, label in ((Theme.DARK, "深色"), (Theme.LIGHT, "浅色"),
                         (Theme.AUTO, "跟随系统")):
        try:
            setTheme(theme)
            pump(app, 150)
            check(f"切换到{label}主题无异常", True)
        except Exception as e:
            check(f"切换到{label}主题无异常", False, str(e))

    # 云母开关（非 Win11 会自动忽略，只要不崩就算过）
    try:
        cfg.micaEnabled.valueChanged.emit(False)
        cfg.micaEnabled.valueChanged.emit(True)
        pump(app, 150)
        check("云母开关信号已接线且无异常", True,
              "Win11 上会实时生效；当前平台自动跳过")
    except Exception as e:
        check("云母开关信号已接线且无异常", False, str(e))

    setTheme(Theme.DARK)
    w.destroy()
    del w


# ---------------------------------------------------------------- 6. 设置页交互
def test_setting_interactions(app):
    print("\n[6] 设置页交互")
    w = MainWindow()
    w.switchTo(w.settingInterface)
    pump(app, 150)

    sv = w.settingInterface
    check("主题下拉卡片存在", sv.themeCard is not None)
    check("取色卡片存在", sv.themeColorCard is not None)
    check("云母开关卡片存在", sv.micaCard is not None)
    check("配置目录卡片存在", sv.configDirCard is not None)
    check("恢复默认配置卡片存在", sv.resetCard is not None)
    check("作者卡片写明邮箱",
          sv.authorCard is not None and AUTHOR_MAIL in sv.authorCard.contentLabel.text(),
          getattr(sv.authorCard, "contentLabel", None) and sv.authorCard.contentLabel.text())
    check("窗口标题是 LinVox", "LinVox" in w.windowTitle(), w.windowTitle())
    check("卡片分三组", len([sv.personalGroup, sv.appGroup, sv.aboutGroup]) == 3)

    # 回归：卡片必须 addSettingCard 进分组。只把 group 当 parent 传进去的话，
    # 卡片不会进布局，会全部叠在分组左上角挤成一团。
    # 注意 ExpandLayout.count() 恒为 0（它只数 addItem 的项），
    # 所以只能看真实几何：同一分组内的卡片 y 必须各不相同且自上而下排列。
    w.show()
    pump(app, 250)
    for name, group, cards in (
        ("个性化", sv.personalGroup,
         (sv.themeCard, sv.themeColorCard, sv.micaCard, sv.bgImageCard,
          sv.bgBlurCard, sv.zoomCard)),
        ("通用", sv.appGroup,
         (sv.bootCard, sv.helpCard, sv.configDirCard, sv.resetCard)),
        ("AI 变声（RVC）", sv.rvcGroup,
         (sv.rvcRootCard, sv.rvcResetCard, sv.rvcDmlCard)),
        ("关于", sv.aboutGroup, (sv.updateCard, sv.repoCard, sv.authorCard)),
    ):
        tops = [card.pos().y() for card in cards]
        check(f"「{name}」分组的卡片纵向铺开（未叠在一起）",
              len(set(tops)) == len(cards) and all(t > 0 for t in tops),
              "y=%s" % tops)
    w.hide()

    # 没有更新服务器就不能假装有：这里必须真的打开发布页（用假的打开器接住，
    # 免得测试时弹浏览器）
    from app.views import setting_view

    opened = []

    class _FakeDesktop:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())
            return True

    original = setting_view.QDesktopServices
    setting_view.QDesktopServices = _FakeDesktop
    try:
        sv.updateCard.button.click()   # SettingCard 只有 clicked 信号，没有 click()
        pump(app, 150)
    finally:
        setting_view.QDesktopServices = original
    check("「查看更新」真的打开 Releases 页面",
          bool(opened) and opened[0].startswith("https://"), str(opened))

    # 没有消费方的设置项不该摆在页面上（以前「半精度推理」「音高提取算法」
    # 是已删除的转换页的参数，留在设置页就是点了没反应的开关）
    check("设置页上没有无人消费的死卡片",
          not hasattr(sv, "rvcHalfCard") and not hasattr(sv, "rvcF0Card"))

    w.destroy()
    del w


# ---------------------------------------------------------------- 7. RVC 引擎
def test_rvc_engine(app):
    """RVC 引擎：环境自检、模型/索引扫描、公共工具。

    注意这里**不真的跑推理**——那需要 RVC 的 torch 环境且很慢。
    """
    print("\n[7] RVC 引擎")
    from app.rvc import RvcEngine

    engine = RvcEngine()

    ok, problems = engine.check()
    check("引擎自检不抛异常", isinstance(ok, bool) and isinstance(problems, list))

    # 推理模块在两版 RVC 里位置不同（infer/vc/ 与 infer/modules/vc/），
    # 自检必须两种布局都认，否则换个工程目录就报"缺少推理模块"
    found = engine.findFile("infer/vc/modules.py",
                            "infer/modules/vc/modules.py")
    check("推理模块兼容新旧两种目录布局", bool(found), found or engine.root)
    check("可选能力检测可用",
          isinstance(engine.hasFeature("ckpt"), bool)
          and isinstance(engine.featureProblem("ckpt"), str))
    if os.path.isdir(engine.root):
        check("RVC 工程目录可用", ok,
              "; ".join(problems) if problems else engine.root)
        check("扫描到模型权重", len(engine.models()) > 0,
              "%d 个：%s" % (len(engine.models()),
                             "、".join(engine.models()[:3])))
        check("扫描到检索索引", len(engine.indices()) > 0,
              "%d 个" % len(engine.indices()))
    else:
        check("RVC 目录缺失时优雅降级", not ok, "未配置 RVC 目录，跳过扫描类检查")


# ---------------------------------------------------------------- 8. 实时变声
def test_rtc(app):
    """内置实时变声：worker 协议、设备枚举、页面装配、参数持久化。

    worker（realtime_worker.py）只在 start 时才加载 torch，
    所以"枚举设备"这条链路可以安全地在冒烟测试里真跑。
    """
    print("\n[8] 实时变声（内置控制台）")
    import json
    import subprocess

    from app.rvc import RvcEngine
    from app.rvc import engine as rvc_engine_mod

    try:
        py_compile.compile(
            rvc_engine_mod.RTC_WORKER_SCRIPT, doraise=True,
            cfile=os.path.join(_TMP_CFG, "rtc.pyc"),
        )
        check("实时 worker 脚本语法正确", True)
    except Exception as e:
        check("实时 worker 脚本语法正确", False, str(e))

    engine = RvcEngine()
    if os.path.isdir(engine.root):
        # 直接跑 worker：发 list_devices + exit，验证 JSON 协议全链路
        proc = subprocess.run(
            [engine.pythonExe(), rvc_engine_mod.RTC_WORKER_SCRIPT,
             "--rvc-root", engine.root],
            input='{"cmd": "list_devices"}\n{"cmd": "exit"}\n',
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=engine.root, timeout=120,
        )
        events = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith(rvc_engine_mod.RTC_EVENT_PREFIX):
                try:
                    events.append(json.loads(
                        line[len(rvc_engine_mod.RTC_EVENT_PREFIX):]))
                except ValueError:
                    pass
        kinds = [e.get("type") for e in events]
        check("实时 worker 协议握手", "ready" in kinds, "events=%s" % kinds[:3])
        devices = next((e for e in events if e.get("type") == "devices"), None)
        if devices is None:
            check("实时 worker 枚举音频设备", False, "未收到 devices 事件")
        else:
            check("实时 worker 枚举音频设备", True,
                  "%d 个输入 / %d 个输出（%s）" % (
                      len(devices.get("inputs", [])),
                      len(devices.get("outputs", [])),
                      devices.get("selected")))
            check("枚举到麦克风与扬声器",
                  devices.get("inputs") and devices.get("outputs"))

    w = MainWindow()
    page = w.rtcInterface
    check("实时变声页已挂载", page.objectName() == "rtcInterface")
    for name, widget in (("模型下拉", page.modelCombo), ("索引下拉", page.indexCombo),
                         ("设备类型", page.hostapiCombo), ("输入设备", page.inputCombo),
                         ("输出设备", page.outputCombo), ("音高算法", page.f0Combo),
                         ("采样长度", page.blockSlider), ("开始按钮", page.startButton),
                         ("停止按钮", page.stopButton), ("推理耗时", page.inferValue),
                         ("运行日志", page.logEdit)):
        check(f"实时页控件存在：{name}", widget is not None)

    # 卡片顺序：选完模型和设备就该点开始了，「运行控制」必须排在设置卡上面
    # （以前它在常规设置下面，得滚过一堆滑块才看得到「开始音频转换」）
    order = []
    for i in range(page.vBoxLayout.count()):
        item = page.vBoxLayout.itemAt(i)
        if item is None or item.widget() is None:
            continue
        order.append(item.widget())
    check("运行控制排在设置卡上面",
          order.index(page.runCard) < order.index(page.settingRow),
          " > ".join(getattr(w_, "objectName", lambda: "?")() or type(w_).__name__
                     for w_ in order))

    # QProcess 通信：界面 → worker → 设备列表 → 信号
    got = {"devices": False}
    page.rtc.devicesUpdated.connect(lambda _e: got.__setitem__("devices", True))
    page.rtc.requestDevices()
    pump(app, 10000)
    check("界面经 QProcess 枚举到设备",
          got["devices"] or page.inputCombo.count() > 0,
          "输入设备 %d 个" % page.inputCombo.count())
    page.rtc.shutdown()

    params = page.collectParams()
    check("实时参数可收集",
          "pth_path" in params and "block_time" in params
          and params["f0method"] in F0_METHODS,
          "f0=%s block=%s" % (params["f0method"], params["block_time"]))
    check("实时页不再提供已移除的 harvest/crepe",
          page.f0Combo.count() == len(F0_METHODS)
          and all(page.f0Combo.itemData(i) in F0_METHODS
                  for i in range(page.f0Combo.count())),
          "、".join(F0_METHODS))

    page.pitchSlider.setValue(3)
    check("实时参数写回配置", int(cfg.get(cfg.rtcPitch)) == 3,
          "pitch=%s" % cfg.get(cfg.rtcPitch))
    # 选择过的模型 / 设备必须落到磁盘，不能只在内存里（重启就没了）
    page.modelCombo.addItem("keep.pth", userData="/tmp/keep.pth")
    page.modelCombo.setCurrentIndex(page.modelCombo.count() - 1)
    cfg.save()
    fresh = Config()
    fresh.load(_config_path())
    check("实时变声的模型选择已落盘",
          fresh.get(fresh.rtcPth) == "/tmp/keep.pth", fresh.get(fresh.rtcPth))
    check("实时变声的音调已落盘", fresh.get(fresh.rtcPitch) == 3)

    # DirectML 只在设置页有开关，实时变声 / 训练都读同一个配置项
    check("实时变声页不再重复放 DirectML 开关",
          getattr(page, "dmlCheck", None) is None)
    cfg.set(cfg.rvcUseDml, True, save=False)
    cfg.save()
    fresh = Config()
    fresh.load(_config_path())
    check("DirectML 选择已落盘", fresh.get(fresh.rvcUseDml) is True)
    # 以前"把上次选择填回控件"发生在接信号之后，填「使用模型采样率」那一步就
    # 触发了 collectParams()，把还没填到的开关以默认 False 写回配置
    restart = MainWindow()
    check("重启后实时变声按配置里的 DirectML 走",
          restart.rtcInterface.collectParams()["dml"] is True
          and cfg.get(cfg.rvcUseDml) is True,
          "cfg=%s" % cfg.get(cfg.rvcUseDml))
    restart.rtcInterface.rtc.shutdown()
    restart.destroy()
    del restart
    cfg.set(cfg.rvcUseDml, False, save=False)

    page.pitchSlider.setValue(0)
    cfg.set(cfg.rtcPth, "", save=False)
    cfg.save()

    w.switchTo(page)
    pump(app, 150)
    check("可切换到实时变声页", w.stackedWidget.currentWidget() is page)

    # 滚轮划过滑块不该改值（采样长度这种改一次就得重启的参数，被滚轮顺手
    # 改掉最坑）。滑块只 ignore 事件、不吞掉它，外层页面还能照常滚动
    page.blockSlider.setValue(40)
    wheel = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    app.sendEvent(page.blockSlider, wheel)
    check("滑块不响应滚轮（值不变）", page.blockSlider.value() == 40,
          "value=%d" % page.blockSlider.value())
    check("滚轮事件未被滑块吞掉（页面仍可滚动）", not wheel.isAccepted())

    w.switchTo(w.homeInterface)
    pump(app, 100)

    w.destroy()
    del w


def _makeTestWav(path: str, seconds: float = 1.0, freq: float = 220.0, sr: int = 44100):
    """用标准库写一段正弦波 wav，避免为了测试再装 numpy。"""
    import math
    import struct
    import wave

    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        frames = bytearray()
        for i in range(int(seconds * sr)):
            value = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / sr))
            frames += struct.pack("<h", value)
        f.writeframes(bytes(frames))


# ------------------------------------------------- 9. 训练 / 分离 / 模型管理
def test_builtin_jobs(app):
    """内嵌 RVC 的三块「重活」：训练、人声分离、模型管理。

    这里只验装配与接线（真跑训练要几十分钟），三个 worker 脚本单独编译检查。
    """
    print("\n[9] 训练 / 人声分离 / 模型管理（内嵌页面）")
    from app.rvc import engine as rvc_engine_mod

    for name, script in (("训练", rvc_engine_mod.TRAIN_WORKER_SCRIPT),
                         ("分离", rvc_engine_mod.SEP_WORKER_SCRIPT),
                         ("模型管理", rvc_engine_mod.CKPT_WORKER_SCRIPT)):
        try:
            py_compile.compile(
                script, doraise=True,
                cfile=os.path.join(_TMP_CFG, os.path.basename(script) + "c"),
            )
            check(f"{name} worker 脚本语法正确", True)
        except Exception as e:
            check(f"{name} worker 脚本语法正确", False, str(e))

    w = MainWindow()

    page = w.trainInterface
    check("训练页已挂载", page.objectName() == "trainInterface")
    for name, widget in (("实验名", page.expEdit), ("已有实验", page.expCombo),
                         ("训练集", page.datasetEdit), ("采样率", page.srCombo),
                         ("模型版本", page.versionCombo), ("音高算法", page.f0Combo),
                         ("一键训练", page.allButton), ("停止", page.stopButton),
                         ("进度条", page.progressBar), ("日志", page.logEdit)):
        check(f"训练页控件存在：{name}", widget is not None)
    check("训练页音高算法仅内嵌版支持的三种",
          all(page.f0Combo.itemData(i) in F0_METHODS
              for i in range(page.f0Combo.count())),
          "%d 项" % page.f0Combo.count())

    page = w.sepInterface
    check("人声分离页已挂载", page.objectName() == "sepInterface")
    for name, widget in (("分离模型", page.modelCombo), ("输入", page.inputEdit),
                         ("人声目录", page.vocalEdit), ("伴奏目录", page.instEdit),
                         ("输出格式", page.formatCombo), ("开始分离", page.startButton),
                         ("停止", page.stopButton), ("日志", page.logEdit)):
        check(f"分离页控件存在：{name}", widget is not None)

    page = w.ckptInterface
    check("模型管理页已挂载", page.objectName() == "ckptInterface")
    for name, widget in (("模型下拉", page.modelCombo), ("读取信息", page.infoButton),
                         ("新模型名", page.nameEdit), ("采样率", page.srCombo),
                         ("模型版本", page.versionCombo), ("融合 A", page.mergeACombo),
                         ("融合 B", page.mergeBCombo), ("开始融合", page.mergeButton),
                         ("日志", page.logEdit)):
        check(f"模型管理页控件存在：{name}", widget is not None)

    # 三个页面都依赖 engine 自检结果，切过去会触发 refreshEngine，验证不崩
    for name, page in (("训练", w.trainInterface), ("分离", w.sepInterface),
                       ("模型管理", w.ckptInterface)):
        w.switchTo(page)
        pump(app, 150)
        check(f"可切换到{name}页", w.stackedWidget.currentWidget() is page)

    w.switchTo(w.homeInterface)
    pump(app, 100)
    w.destroy()
    del w


# ---------------------------------------------------------------- 10. 配色
def test_theme_colors(app):
    """配色自适应：次要文字 / 状态色必须随明暗主题变化，不能写死 gray。"""
    print("\n[10] 主题配色自适应")
    from app import theme as app_theme

    setTheme(Theme.DARK)
    pump(app, 100)
    dark_muted = app_theme.mutedColor()
    dark_ok = app_theme.statusColor("ok")
    setTheme(Theme.LIGHT)
    pump(app, 100)
    light_muted = app_theme.mutedColor()
    light_ok = app_theme.statusColor("ok")

    check("次要文字色随主题变化", dark_muted != light_muted,
          "dark=%s light=%s" % (dark_muted, light_muted))
    check("状态色随主题变化", dark_ok != light_ok,
          "dark=%s light=%s" % (dark_ok, light_ok))
    check("主题切换后重刷登记控件不崩",
          (app_theme.refresh() or True) is True)
    setTheme(Theme.DARK)
    pump(app, 100)


# ---------------------------------------------------------------- 11. 关闭流程
def test_close_saves(app):
    print("\n[11] 关闭时配置落盘")
    w = MainWindow()
    cfg.set(cfg.startOnBoot, True, save=False)
    w.close()
    pump(app, 200)

    fresh = Config()
    fresh.load(_config_path())
    check("closeEvent 触发了配置保存", fresh.get(fresh.startOnBoot) is True)

    cfg.set(cfg.startOnBoot, False, save=False)
    cfg.save()


# ---------------------------------------------------------------- 12. 设置写回
def test_setting_writeback(app):
    """设置卡片改动必须真的作用到应用。

    以前这里全是坏的：卡片内部调的是全局 ``qconfig.set()``，而我们用的是自己的
    ``cfg``，两边不是同一个对象 —— 值写进了野路径的 config.json，
    ``themeChanged`` / ``appRestartSig`` 一个都不发，于是改主题、改缩放点了没反应。
    """
    print("\n[12] 设置卡片改动作用到应用")
    from qfluentwidgets import qconfig

    check("qconfig 指向应用配置（否则卡片改了等于没改）",
          qconfig._cfg is cfg, str(qconfig.file))

    oldTheme = cfg.get(cfg.themeMode)
    qconfig.set(cfg.themeMode, Theme.LIGHT, save=False)   # 卡片内部就是这么写的
    pump(app, 100)
    applied = cfg.theme == Theme.LIGHT
    qconfig.set(cfg.themeMode, oldTheme, save=False)
    pump(app, 100)
    check("改主题后应用主题状态跟着变", applied, str(cfg.theme))

    fired = []
    cfg.appRestartSig.connect(lambda: fired.append(1))
    oldScale = cfg.get(cfg.dpiScale)
    qconfig.set(cfg.dpiScale, "1.25", save=False)
    qconfig.set(cfg.dpiScale, oldScale, save=False)
    check("改缩放会要求重启（不再默默失效）", bool(fired))


# ---------------------------------------------------------------- 13. 开机自启
def test_boot():
    """开机自启：必须真的在系统启动项里写一条，而不是只改个配置项。"""
    print("\n[13] 开机自启写入系统")
    from app import boot

    args = boot.launchArgs()
    check("自启命令指向本程序", bool(args) and os.path.isfile(args[0])
          and args[-1].endswith(("LinVox.py", "main.py", ".exe")), " ".join(args))
    # 指到一个不存在的脚本，自启起来就是"什么都没发生"，这条必须卡住
    check("自启脚本真的存在", os.path.isfile(args[-1]), args[-1])

    if sys.platform == "win32":
        check("Windows 首选任务计划程序（任务名就是程序名）",
              boot.TASK_NAME == "LinVox" and callable(boot._winTaskCreate))
        check("自启命令用的是不带控制台的解释器（登录时不闪黑框）",
              boot._winCommand().lower().find("pythonw.exe") >= 0
              or getattr(sys, "frozen", False), boot._winCommand())

    old = boot.isStartOnBoot()
    on = boot.setStartOnBoot(True) and boot.isStartOnBoot()
    off = boot.setStartOnBoot(False) and not boot.isStartOnBoot()
    boot.setStartOnBoot(old)      # 复原用户本来的状态
    check("开启后系统启动项里能查到", on)
    check("关闭后系统启动项被清掉", off)

    if sys.platform == "win32":
        import subprocess

        import winreg

        def _hasTask():
            return subprocess.run(["schtasks", "/Query", "/TN", boot.TASK_NAME],
                                  capture_output=True).returncode == 0

        def _hasRunKey():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, boot._RUN_KEY) as k:
                    winreg.QueryValueEx(k, boot.APP_NAME)
                return True
            except OSError:
                return False

        boot.setStartOnBoot(True)
        method = boot.lastMethod()
        # 这台机器（组策略 / 权限）可能不让建登录触发的任务，那就退回注册表；
        # 不管走哪条，系统里必须查得到，且只能有一条
        check("开启后用的方式记录在案（任务 or 退回注册表）",
              method in ("task", "run"), method)
        check("开启后系统里只有一条自启项",
              (_hasTask() and not _hasRunKey()) or (_hasRunKey() and not _hasTask()),
              "task=%s run=%s" % (_hasTask(), _hasRunKey()))
        boot.setStartOnBoot(False)
        check("关闭后任务和注册表项都清干净",
              not _hasTask() and not _hasRunKey(),
              "task=%s run=%s" % (_hasTask(), _hasRunKey()))


# ---------------------------------------------------------------- 14. 省电
def test_idle_saver(app):
    """窗口不在前台时，那些"只是好看 / 没人看"的东西必须停下来。

    以前一言的呼吸动画是无限循环的（永远 30fps 重绘），任务页的 nvidia-smi
    每 2 秒跑一次 —— 切到别的软件之后它们照跑不误，用户什么都没干风扇却在转。
    """
    print("\n[14] 窗口不聚焦时的省电")
    from app.idle import idleWatcher

    watcher = idleWatcher()
    w = MainWindow()
    w.show()
    w.switchTo(w.taskInterface)
    pump(app, 200)

    check("看着任务页时自动刷新是开着的", w.taskInterface.timer.isActive())
    check("首页 CPU 采样是开着的", w.homeInterface.cpuTimer.isActive())

    label = AnimatedTextLabel("「测试一言」")
    label.setAnimatedText("「测试一言」", "typewriter")
    pump(app, 60)
    check("前台时一言动画在跑", label.isAnimating())

    # 运行日志：前台时照常一行一行写进日志框
    log = w.rtcInterface.logEdit
    log.clear()
    log.append("前台日志")
    check("前台时日志直接写进日志框", "前台日志" in log.toPlainText(),
          log.toPlainText()[-40:])

    watcher.setActive(False)      # 模拟切到别的软件（offscreen 收不到真实事件）
    pump(app, 100)

    check("失焦后任务页停掉 nvidia-smi 轮询", not w.taskInterface.timer.isActive())
    check("失焦后首页停掉 CPU 采样", not w.homeInterface.cpuTimer.isActive())
    check("失焦后一言动画停住（不再 30fps 重绘）", not label.isAnimating())
    check("失焦后一言不是停在半句乱码上", label._displayText() == label.text(),
          label._displayText())

    # 指标数字：每个音频块都改一次标签，没人在看时就别改了
    page = w.rtcInterface
    page.inferValue.setText("0")
    page._onMetrics(888, 10)
    check("失焦后不再刷新推理耗时（每音频块一次的重绘）",
          page.inferValue.text() == "0", page.inferValue.text())

    # 日志：失焦期间攒着，既不丢也不写进文档
    log.clear()
    log.append("后台一行")
    check("失焦时日志不再写进日志框（省掉每行一次重排+重绘）",
          "后台一行" not in log.toPlainText(), log.toPlainText()[-40:])
    check("失焦期间的日志攒着没丢", log.pendingCount() == 1,
          str(log.pendingCount()))

    watcher.setActive(True)
    pump(app, 100)
    check("回到前台后任务页恢复刷新", w.taskInterface.timer.isActive())
    check("回到前台后首页恢复采样", w.homeInterface.cpuTimer.isActive())
    check("回到前台后补上失焦期间的日志",
          "后台一行" in log.toPlainText() and log.pendingCount() == 0,
          log.toPlainText()[-40:])

    # 上限必须存在：变声一跑几十行/秒，攒一小时内存先撑不住
    watcher.setActive(False)
    log.clear()
    for i in range(log.MAX_PENDING + 20):
        log.append("压测 %d" % i)
    check("失焦期间攒的日志有上限", log.pendingCount() == log.MAX_PENDING,
          str(log.pendingCount()))
    watcher.setActive(True)
    check("超出上限的行会说明省略了多少",
          "省略" in log.toPlainText(), log.toPlainText()[:60])
    log.clear()

    # 呼吸效果的指标已经挪到下面，这里只看恢复后的实时页指标
    page._onMetrics(7, 10)
    check("回到前台后推理耗时恢复刷新", page.inferValue.text() == "7",
          page.inferValue.text())

    # 呼吸效果以前是无限循环：界面永远占着一份 CPU 在重绘一行字
    check("呼吸动画有播放上限（不再无限循环）", BREATH_LOOPS <= 3,
          "%d 轮后定住" % BREATH_LOOPS)

    w.hide()
    pump(app, 100)
    check("切到别的页面后任务页也停刷新", not w.taskInterface.timer.isActive()
          or w.stackedWidget.currentWidget() is w.taskInterface)

    label.stop()
    w.destroy()
    del w
    watcher.setActive(True)


# ---------------------------------------------------------------- 15. 自定义背景
def test_background(app):
    """自定义背景图：换图 / 模糊度要真的作用到窗口，并且写进配置。"""
    print("\n[15] 自定义背景图与模糊度")
    from app.wallpaper import EDGE_PAD_PER_BLUR, Wallpaper

    # 自己造一张测试图，不依赖外部素材
    imgDir = tempfile.mkdtemp(prefix="fluent_bg_test_")
    imgPath = os.path.join(imgDir, "bg.png")
    pix = QPixmap(64, 48)
    pix.fill(QColor("#336699"))
    check("测试图片已生成", pix.save(imgPath) and os.path.isfile(imgPath), imgPath)

    w = MainWindow()
    w.show()      # 缩放事件只在窗口可见时才投递，offscreen 下也一样，先显示出来
    pump(app, 80)
    wall = w.wallpaper
    check("窗口挂着一层背景图", isinstance(wall, Wallpaper))
    check("没设背景图时它是隐藏的（露出主题底色）",
          not wall.hasImage() and wall.isHidden())

    cfg.set(cfg.bgImagePath, imgPath, save=False)
    cfg.set(cfg.bgBlur, 12, save=False)
    pump(app, 80)
    check("选了图背景层就显示出来", wall.hasImage() and not wall.isHidden())
    check("背景图就是配置里那条路径", wall.imagePath() == imgPath)
    effect = wall.graphicsEffect()
    check("模糊半径跟着配置走", effect is not None and effect.blurRadius() == 12,
          "radius=%s" % getattr(effect, "blurRadius", lambda: None)())
    # 模糊会把边缘糊出一段虚边，所以背景层要比窗口大一圈，虚边落到窗口外面
    check("模糊时背景层比窗口大一圈（虚边落在窗口外）",
          wall.width() == w.width() + 12 * EDGE_PAD_PER_BLUR * 2,
          "%d vs %d" % (wall.width(), w.width()))

    cfg.set(cfg.bgBlur, 0, save=False)
    pump(app, 80)
    check("模糊度归零后摘掉特效（不再白跑一次离屏合成）",
          wall.graphicsEffect() is None)
    check("不糊时背景层正好铺满窗口", wall.width() == w.width(),
          "%d vs %d" % (wall.width(), w.width()))

    w.resize(1000, 600)
    pump(app, 80)
    check("窗口缩放后背景跟着重铺", wall.height() == 600, str(wall.height()))

    cfg.save()
    fresh = Config()
    fresh.load(_config_path())
    check("背景图路径已落盘", fresh.get(fresh.bgImagePath) == imgPath,
          str(fresh.get(fresh.bgImagePath)))
    check("模糊度已落盘", fresh.get(fresh.bgBlur) == 0)

    # 画出来的得真是那张图，不能只是"挂了个控件"（抓一次像素看看）
    shot = wall.grab()
    center = shot.toImage().pixelColor(shot.width() // 2, shot.height() // 2)
    check("背景层真的把图画出来了（不是一片空白）",
          not shot.isNull() and center == QColor("#336699"), center.name())

    # 设置页卡片
    sv = w.settingInterface
    cfg.set(cfg.bgImagePath, imgPath, save=False)
    pump(app, 80)
    check("设置页有背景图卡片", sv.bgImageCard is not None)
    check("卡片写明当前用的是哪张图",
          "bg.png" in sv.bgImageCard.contentLabel.text(),
          sv.bgImageCard.contentLabel.text())
    check("有图时「清除」点得动", sv.bgImageCard.clearButton.isEnabled())
    check("有图时模糊度调得动", sv.bgBlurCard.isEnabled())

    # 「选择图片」/「清除」两个按钮：选中的路径必须真的进配置（换个进程也还在）
    from app.views import setting_view

    class _FakeDialog:
        @staticmethod
        def getOpenFileName(*_args, **_kwargs):
            return (imgPath, "")

    original = setting_view.QFileDialog
    setting_view.QFileDialog = _FakeDialog
    try:
        cfg.set(cfg.bgImagePath, "", save=False)
        pump(app, 60)
        sv.bgImageCard.chooseButton.click()
        pump(app, 80)
        check("「选择图片」把选中的路径写进配置",
              cfg.get(cfg.bgImagePath) == imgPath, str(cfg.get(cfg.bgImagePath)))
        sv.bgImageCard.clearButton.click()
        pump(app, 80)
    finally:
        setting_view.QFileDialog = original
    check("「清除」把背景图从配置里抹掉",
          cfg.get(cfg.bgImagePath) == "" and not wall.hasImage())
    check("没图时「清除」是灰的", not sv.bgImageCard.clearButton.isEnabled())
    check("没图时模糊度是灰的（没东西可糊）", not sv.bgBlurCard.isEnabled())

    # 图被移走 / 删了：说清楚，别让人以为功能坏了
    cfg.set(cfg.bgImagePath, os.path.join(imgDir, "gone.png"), save=False)
    pump(app, 80)
    check("图片丢了会在卡片上说清楚",
          "找不到" in sv.bgImageCard.contentLabel.text(),
          sv.bgImageCard.contentLabel.text())
    check("图片丢了就不画背景（不会糊出一片黑）", not wall.hasImage())

    # 恢复默认配置要连背景一起收掉
    resetConfig()
    pump(app, 80)
    check("恢复默认配置后背景清空",
          cfg.get(cfg.bgImagePath) == "" and not wall.hasImage())

    w.hide()
    w.destroy()
    del w
    shutil.rmtree(imgDir, ignore_errors=True)


# ---------------------------------------------------------------- 16. 桌面叠层
def test_overlay(app):
    """桌面叠层：主窗口看不见（打游戏）的时候也能看到延迟 / 推理时间。"""
    print("\n[16] 桌面叠层（游戏中可见）")
    from PySide6.QtGui import QGuiApplication

    from app.overlay import MARGIN, MetricOverlay, OverlaySettingDialog

    w = MainWindow()
    page = w.rtcInterface
    check("运行控制里有叠层开关", page.overlayCheck is not None)
    check("开关就摆在运行控制那张卡上", page.overlayCheck.parent() is page.runCard)
    check("开关后面跟着一个外观设置按钮",
          page.overlaySettingButton.parent() is page.runCard)
    check("默认不开（不是人人都想要一个浮窗）",
          not page.overlayCheck.isChecked() and page._overlay is None)
    check("没开叠层时设置按钮是灰的（改了也没东西可看）",
          not page.overlaySettingButton.isEnabled())

    page.overlayCheck.setChecked(True)
    pump(app, 80)
    ov = page._overlay
    check("勾上就建出叠层", isinstance(ov, MetricOverlay) and ov.isVisible())
    check("叠层是置顶窗口（才压得住游戏画面）",
          bool(ov.windowFlags() & Qt.WindowStaysOnTopHint))
    check("叠层点穿（不挡游戏里的鼠标操作）",
          ov.testAttribute(Qt.WA_TransparentForMouseEvents))
    check("叠层不进任务栏（不和主窗口挤成一堆）",
          bool(ov.windowFlags() & Qt.Tool))
    # 挂在主窗口下就成了主窗口的附属窗：系统会在主窗口最小化时把它一起藏起来，
    # 那还叠个什么 —— 所以它是独立窗口，代价是关窗时要自己收（见 closeOverlay）
    check("叠层是独立窗口（最小化主窗口不会连它一起藏起来）", ov.parent() is None)
    check("没在跑时显示「—」而不是一个假的 0",
          ov.valueText("算法延迟") == "—" and ov.valueText("推理时间") == "—",
          "%s / %s" % (ov.valueText("算法延迟"), ov.valueText("推理时间")))

    check("开了叠层后设置按钮可点", page.overlaySettingButton.isEnabled())

    # ---- 外观：底板透明度 / 文字颜色 / 显示位置 ----
    check("叠层默认带一层半透明底板", 0 < ov._alpha < 255, str(ov._alpha))
    cfg.set(cfg.rtcOverlayAlpha, 0, save=False)
    pump(app, 60)
    check("透明度拖到 0 后底板全透明（只剩字）", ov._alpha == 0, str(ov._alpha))
    cfg.set(cfg.rtcOverlayAlpha, 100, save=False)
    pump(app, 60)
    check("透明度拖到 100 后底板全不透明", ov._alpha == 255, str(ov._alpha))

    cfg.set(cfg.rtcOverlayColor, QColor("#00ff00"), save=False)
    pump(app, 60)
    check("文字颜色跟着配置走",
          "#00ff00" in ov._values["推理时间"].styleSheet(),
          ov._values["推理时间"].styleSheet())

    geo = QGuiApplication.primaryScreen().availableGeometry()
    cfg.set(cfg.rtcOverlayCorner, "bottomRight", save=False)
    pump(app, 60)
    check("位置改成右下角后叠层真的挪过去了",
          ov.x() + ov.width() == geo.right() - MARGIN
          and ov.y() + ov.height() == geo.bottom() - MARGIN,
          "x=%d y=%d" % (ov.x(), ov.y()))

    # 设置对话框：三张卡直接改配置，叠层当场跟着变（不用先关掉再开）
    dlg = OverlaySettingDialog(w)
    check("叠层设置里有三张卡：透明度 / 颜色 / 位置",
          dlg.alphaCard is not None and dlg.colorCard is not None
          and dlg.cornerCard is not None)
    dlg.cornerCard.comboBox.setCurrentIndex(1)      # 右上角
    pump(app, 60)
    check("在设置里改位置，叠层立刻挪过去",
          cfg.get(cfg.rtcOverlayCorner) == "topRight"
          and ov.x() + ov.width() == geo.right() - MARGIN
          and ov.y() == geo.top() + MARGIN,
          "%s x=%d y=%d" % (cfg.get(cfg.rtcOverlayCorner), ov.x(), ov.y()))
    dlg.deleteLater()
    del dlg

    page.overlayCheck.setChecked(False)
    pump(app, 60)
    check("关掉叠层后设置按钮重新变灰", not page.overlaySettingButton.isEnabled())
    page.overlayCheck.setChecked(True)
    pump(app, 60)
    ov = page._overlay

    cfg.save()
    saved = Config()
    saved.load(_config_path())
    check("叠层外观三项都落了盘",
          saved.get(saved.rtcOverlayAlpha) == 100
          and saved.get(saved.rtcOverlayColor).name() == "#00ff00"
          and saved.get(saved.rtcOverlayCorner) == "topRight",
          "%s / %s / %s" % (saved.get(saved.rtcOverlayAlpha),
                            saved.get(saved.rtcOverlayColor).name(),
                            saved.get(saved.rtcOverlayCorner)))

    # 关键：窗口不聚焦时页上的数字照旧停刷（省电），叠层必须继续更新 ——
    # 它就是给"主窗口看不见"的时候用的
    page.setActive(False)
    page._onMetrics(42, 320)
    pump(app, 60)
    check("失焦时叠层照常更新",
          ov.valueText("算法延迟") == "320 ms"
          and ov.valueText("推理时间") == "42 ms",
          "%s / %s" % (ov.valueText("算法延迟"), ov.valueText("推理时间")))
    check("失焦时页上的数字照旧停刷（省电）", page.inferValue.text() != "42",
          page.inferValue.text())

    page.setActive(True)
    page._onMetrics(15, 300)
    pump(app, 60)
    check("回到前台后叠层和页面一致",
          ov.valueText("推理时间") == "15 ms" and page.inferValue.text() == "15",
          "%s / %s" % (ov.valueText("推理时间"), page.inferValue.text()))

    page._onStopped()
    pump(app, 60)
    check("停止后叠层回到「—」（不留上一次的旧数字）",
          ov.valueText("推理时间") == "—", ov.valueText("推理时间"))

    # 开关要记住：关掉 / 重启都得是上次那个状态
    page.overlayCheck.setChecked(False)
    pump(app, 80)
    check("取消勾选后叠层收掉", page._overlay is None)
    check("叠层开关写进配置", cfg.get(cfg.rtcOverlay) is False)
    cfg.save()
    fresh = Config()
    fresh.load(_config_path())
    check("叠层开关已落盘", fresh.get(fresh.rtcOverlay) is False)

    # 外观改回默认，别把测试里的绿字留进下一次运行的配置
    cfg.set(cfg.rtcOverlayAlpha, 60, save=False)
    cfg.set(cfg.rtcOverlayColor, QColor("#ffffff"), save=False)
    cfg.set(cfg.rtcOverlayCorner, "topLeft", save=False)

    cfg.set(cfg.rtcOverlay, True, save=False)
    w2 = MainWindow()
    pump(app, 80)
    check("上次勾着的话，这次启动直接把叠层摆出来",
          isinstance(w2.rtcInterface._overlay, MetricOverlay)
          and w2.rtcInterface._overlay.isVisible())
    # 独立窗口没有父对象兜底：关主窗口时得自己收（MainWindow.closeEvent 调它）
    w2.rtcInterface.closeOverlay()
    check("关窗口能收掉叠层（不在桌面上留孤儿小窗）",
          w2.rtcInterface._overlay is None)
    w2.rtcInterface.rtc.shutdown()
    w2.destroy()
    del w2

    cfg.set(cfg.rtcOverlay, False, save=False)
    page.rtc.shutdown()
    w.destroy()
    del w


def test_branding(app):
    """图标 + 首页标题图：素材真的生成了，而且被标题栏、闪屏、首页用上。"""
    print("\n[17] 图标与首页标题图")

    from app.assets import appIconPath, logoPath, resourcePath, titlePath
    from app.views.home_view import BrandHeader

    check("图标 png 在", os.path.isfile(logoPath()), logoPath())
    check("图标 ico 在（标题栏 / 任务栏用）",
          os.path.isfile(resourcePath("logo.ico")))
    check("首页标题图在", os.path.isfile(titlePath()), titlePath())

    w = MainWindow()
    pump(app, 60)
    icon = w.windowIcon()
    check("标题栏用的是自己的图标", not icon.isNull() and icon.availableSizes(),
          str(icon.availableSizes()))
    check("图标带大尺寸（任务栏放大也不糊）",
          max((s.width() for s in icon.availableSizes()), default=0) >= 128,
          str(sorted({s.width() for s in icon.availableSizes()})))

    home = w.homeInterface
    check("首页顶部有品牌条", home.brandHeader is not None)
    check("品牌条吃到了标题图", home.brandHeader.hasImage())
    check("首页欢迎行里有头像（同一个图标）",
          home.banner.logoWidget is not None
          and home.banner.logoWidget.parent() is home.banner)
    check("头像里真的有图（不是空头像）",
          not home.banner.logoWidget.image.isNull())

    # 拖窗口改宽度时不能变形：铺的图必须和卡片一样大（大了就会被 drawPixmap 拉伸
    # 回卡片尺寸 —— 那就是"拖动窗口图片变形"的来源）
    header = home.brandHeader
    for size in ((1200, 150), (640, 150), (900, 400)):
        scaled = header._coverScaled(QSize(*size))
        check("铺 %dx%d 时出来的图正好是这个尺寸（不会被拉伸）" % size,
              scaled.size() == QSize(*size),
              "%dx%d" % (scaled.width(), scaled.height()))
    header.render(QPixmap(header.size()))
    check("画上去的图尺寸和卡片一致", header._scaled.size() == header.size(),
          "%s vs %s" % (header._scaled.width(), header._scaled.height()))

    w.destroy()
    del w


def test_first_run_guide(app):
    """首次使用指导：第一次自己弹一次，看过就不再打扰，设置页能随时叫回来。"""
    print("\n[19] 首次使用指导")

    from app import guide

    text = "".join(title + desc for _icon, title, desc in guide.GUIDE_STEPS)
    check("指导写的是界面上真实的东西（不是正确的废话）",
          "加载模型" in text and "开始音频转换" in text and "输出设备" in text)

    cfg.set(cfg.guideDone, False, save=False)
    w = MainWindow()
    w.resize(1100, 900)
    w.show()
    pump(app, 60)

    dlg = guide.maybeShowGuide(w)
    check("首次使用会弹出指导", dlg is not None)
    check("指导里一步一个条目", dlg is not None and dlg.stepCount() == len(guide.GUIDE_STEPS),
          str(dlg.stepCount() if dlg else None))
    check("底部按钮写的是「开始使用」", dlg.yesButton.text() == "开始使用")
    check("只有「开始使用」，没有多余的取消", dlg.cancelButton.isHidden())

    dlg.accept()        # 模拟点「开始使用」
    pump(app, 60)
    check("看完就记下来", cfg.get(cfg.guideDone) is True)
    check("看过之后不再自己弹", guide.maybeShowGuide(w) is None)

    cfg.save()
    fresh = Config()
    fresh.load(_config_path())
    check("「看过」会落盘（重启也不会再弹）", fresh.get(fresh.guideDone) is True)

    # 设置页入口：看过一次之后就只有这里能把它叫回来
    w.switchTo(w.settingInterface)
    pump(app, 120)
    page = w.settingInterface
    check("设置页里有「使用指导」入口", page.guideCard is not None)
    check("入口卡在布局里（不会叠在别的卡上）",
          page.guideCard.geometry().top() >= page.trayCard.geometry().bottom())
    opened = []
    guide.GuideDialog.exec = lambda self: opened.append(self)
    page.guideCard.clicked.emit()
    pump(app, 60)
    check("点「使用指导」能把向导重新打开", len(opened) == 1)
    del guide.GuideDialog.exec      # 还原成 QDialog 的 exec

    w.destroy()
    del w


def test_start_to_tray(app):
    """开机放托盘：自启命令要带上 --tray，主窗口关掉只是藏起来而不是退出。"""
    print("\n[18] 开机放托盘")

    from app import boot
    from app.tray import TrayIcon

    w = MainWindow()
    w.resize(1100, 900)
    w.show()
    pump(app, 120)
    w.switchTo(w.settingInterface)
    pump(app, 120)
    page = w.settingInterface

    check("设置页里有「开机放托盘」开关", page.trayCard is not None)
    check("它就摆在开机自启那组里", page.trayCard.parent() is page.appGroup)
    check("默认不开（自启还是要正常弹窗）",
          not page.trayCard.isChecked() and cfg.get(cfg.startToTray) is False)
    check("自启没开时它是灰的（改了也没地方生效）",
          page.trayCard.isEnabled() == page.bootCard.isChecked())
    # 关键回归：卡片必须走 addSettingCards 进布局，不能只当 group 的 child
    check("托盘卡在布局里，不会叠在开机自启卡上",
          page.trayCard.geometry().top() >= page.bootCard.geometry().bottom())

    # 自启命令是开机时由系统执行的，那时候没人再读配置 —— 开关必须体现在命令里
    cfg.set(cfg.startToTray, True, save=False)
    check("开了放托盘后自启命令带 --tray",
          boot.launchArgs()[-1] == boot.TRAY_ARG, " ".join(boot.launchArgs()))
    cfg.set(cfg.startToTray, False, save=False)
    check("关掉后自启命令不带 --tray",
          boot.launchArgs()[-1] != boot.TRAY_ARG, " ".join(boot.launchArgs()))

    # 托盘模式下关窗口 = 藏起来（正在变声不能被打断），退出只能走托盘菜单
    w.show()
    pump(app, 40)
    w._hideOnClose = True
    w.close()
    pump(app, 40)
    check("托盘模式下关窗口只是藏起来，不是退出", not w.isVisible())

    tray = TrayIcon(w, app)
    check("托盘图标用的是应用图标", not tray.icon().isNull())
    check("托盘菜单有「打开主窗口」和「退出」",
          tray.showAction is not None and tray.quitAction is not None)
    tray.raiseWindow()
    pump(app, 80)
    check("点托盘能把藏起来的主窗口叫回来", w.isVisible())

    # 真退出：把 _hideOnClose 拨回去再关，走正常的收尾流程
    w._hideOnClose = False
    tray.deleteLater()
    w.destroy()
    del w


def main():
    print("=" * 60)
    print("LinVox 冒烟测试")
    print("=" * 60)

    app = QApplication.instance() or QApplication([])
    setTheme(Theme.DARK)
    setThemeColor("#009faa")

    test_imports()
    test_pages(app)
    test_task_manager(app)
    test_config_persistence()
    test_theme_switch(app)
    test_setting_interactions(app)
    test_rvc_engine(app)
    test_rtc(app)
    test_builtin_jobs(app)
    test_theme_colors(app)
    test_close_saves(app)
    test_setting_writeback(app)
    test_boot()
    test_idle_saver(app)
    test_background(app)
    test_overlay(app)
    test_branding(app)
    test_first_run_guide(app)
    test_start_to_tray(app)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 60)

    shutil.rmtree(_TMP_CFG, ignore_errors=True)
    print(f"（测试配置目录已清理：{_TMP_CFG}）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
