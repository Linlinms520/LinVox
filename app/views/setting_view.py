"""设置页：用官方 SettingCard 系列组件，几行代码拼出 Win11 风格设置面板。

这一页的每一张卡片都得有真实效果 —— 开关写下去了却没人读、按钮点了只是弹个
假提示，比没有这个设置还糟。改动前先确认：配置项到底被谁消费？
"""
import os

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from qfluentwidgets import (
    ColorSettingCard,
    ComboBoxSettingCard,
    FluentIcon as FIF,
    HyperlinkCard,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushSettingCard,
    PushButton,
    PushSettingCard,
    RangeSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SwitchSettingCard,
)

from ..boot import isStartOnBoot, lastMethod, setStartOnBoot
from ..config import DEFAULT_RVC_ROOT, cfg, configDir, configPath, resetConfig
from ..guide import showGuide
from .page import applyPageLayout, makeHeader, setupScrollPage

HELP_URL = "https://qfluentwidgets.com"
REPO_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets"
RELEASE_URL = REPO_URL + "/releases"
VERSION = "v1.0.0"
AUTHOR_NAME = "浅野晚夏"
AUTHOR_MAIL = "linlinms520@foxmail.com"

# RVC 工程目录至少得有这两个子目录，否则引擎起不来
RVC_REQUIRED_DIRS = ("infer", "configs")

# 选背景图时能打开的常见格式（Qt 自带这些解码器，装了插件的 webp 也能读）
BG_IMAGE_FILTER = "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*.*)"


class BackgroundImageCard(SettingCard):
    """背景图卡片：一个位子上放「选择图片」+「清除」两个按钮。

    ``PushSettingCard`` 只有**一个**按钮位，而"选了图就得能取消" —— 少了
    「清除」，选错的图就只能在配置目录里手动改 json。
    """

    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent)
        self.chooseButton = PushButton("选择图片", self)
        self.clearButton = PushButton("清除", self)
        self.hBoxLayout.addWidget(self.chooseButton, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(self.clearButton, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)


class SettingInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingInterface")

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.view)

        # ---- 个性化 ----
        self.personalGroup = SettingCardGroup("个性化", self.view)
        self.themeCard = ComboBoxSettingCard(
            cfg.themeMode, FIF.BRUSH, "应用主题", "调整应用的外观表现",
            texts=["浅色", "深色", "跟随系统"], parent=self.personalGroup,
        )
        self.themeColorCard = ColorSettingCard(
            cfg.themeColor, FIF.PALETTE, "主题强调色", "更改按钮、图标等元素的强调色",
            parent=self.personalGroup,
        )
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT, "云母背景效果", "为窗口应用半透明材质（仅 Windows 11）",
            cfg.micaEnabled, self.personalGroup,
        )
        # 自定义背景：图片路径和模糊度都存配置，关掉再开还在
        self.bgImageCard = BackgroundImageCard(
            FIF.PHOTO, "自定义背景图片", "（未设置，用主题自带的底色）",
            self.personalGroup,
        )
        self.bgBlurCard = RangeSettingCard(
            cfg.bgBlur, FIF.BACKGROUND_FILL, "背景模糊度",
            "0 = 原图；越大越糊，背景太花时把文字衬出来",
            parent=self.personalGroup,
        )
        self.zoomCard = ComboBoxSettingCard(
            cfg.dpiScale, FIF.ZOOM, "界面缩放", "调整窗口与控件的大小（重启后生效）",
            texts=["跟随系统", "100%", "125%", "150%", "175%", "200%"],
            parent=self.personalGroup,
        )

        # ---- 通用 ----
        self.appGroup = SettingCardGroup("通用", self.view)
        self.bootCard = SwitchSettingCard(
            FIF.POWER_BUTTON, "开机自启",
            "登录后自动运行（Windows 走任务计划程序，延迟 1 分钟启动）",
            cfg.startOnBoot, self.appGroup,
        )
        # 「开机放托盘」是开机自启的附加项：自启时不弹主窗口，只在托盘里待着。
        # 自启没开时它没有意义，跟着置灰（省得改了个没用的开关还以为生效了）
        self.trayCard = SwitchSettingCard(
            FIF.MINIMIZE, "开机放托盘",
            "自启后不弹主窗口，只在系统托盘里待着（点托盘图标打开，右键可退出）",
            cfg.startToTray, self.appGroup,
        )
        self.trayCard.setToolTip("需要先打开上面的「开机自启」")
        # 首次那份分步指导看完就没了入口，这里留一个随时能翻回来的按钮
        self.guideCard = PushSettingCard(
            "查看", FIF.EDUCATION, "使用指导",
            "重新打开首次使用的分步说明（装模型 → 接设备 → 开始变声）",
            self.appGroup,
        )
        self.helpCard = HyperlinkCard(
            HELP_URL, "打开文档", FIF.HELP, "使用帮助",
            "查阅组件用法与最佳实践", self.appGroup,
        )
        # 配置目录：让"配置存在哪"这件事可见，而不是让人满盘找
        self.configDirCard = PushSettingCard(
            "打开目录", FIF.FOLDER, "配置目录", configDir(), self.appGroup,
        )
        # 改坏了能一键回到出厂值（出厂值就是 defaults.json 里那份）
        self.resetCard = PushSettingCard(
            "恢复默认", FIF.SYNC, "恢复默认配置",
            "把所有设置恢复成出厂值（config.json 会被 defaults.json 覆盖）",
            self.appGroup,
        )

        # ---- AI 变声（RVC）----
        self.rvcGroup = SettingCardGroup("AI 变声（RVC）", self.view)
        self.rvcRootCard = PushSettingCard(
            "选择目录", FIF.FOLDER, "RVC 工程目录",
            cfg.get(cfg.rvcRoot) or "（未设置）", self.rvcGroup,
        )
        # 换过目录后可能指向一个"精简版"RVC（没有 ckpt / torchgate 之类），
        # 这里留一个一键回退到随软件自带的内嵌工程的入口
        self.rvcResetCard = PushSettingCard(
            "使用内嵌工程", FIF.SYNC, "恢复内嵌 RVC 工程",
            DEFAULT_RVC_ROOT, self.rvcGroup,
        )
        # A 卡 / I 卡走 DirectML，N 卡走 CUDA。实时变声和训练都读这一个开关
        # （两页不再各摆一个），改完之后要重新开始变声 / 训练才生效
        self.rvcDmlCard = SwitchSettingCard(
            FIF.SPEED_HIGH, "DirectML 加速",
            "A 卡 / I 卡请开启；N 卡请关闭（实时变声与训练共用，改了要重新开始）",
            cfg.rvcUseDml, self.rvcGroup,
        )

        # ---- 关于 ----
        self.aboutGroup = SettingCardGroup("关于", self.view)
        # 没有内置更新服务器，这里就老老实实打开发布页，别弹一个假的「已是最新」
        self.updateCard = PrimaryPushSettingCard(
            "查看更新", FIF.UPDATE, "检查版本更新",
            f"当前版本 {VERSION}，点击前往 Releases 查看新版", self.aboutGroup,
        )
        self.repoCard = HyperlinkCard(
            REPO_URL, "访问仓库", FIF.GITHUB, "QFluentWidgets 开源仓库",
            "GPLv3 协议，非商业项目免费使用", self.aboutGroup,
        )
        # 作者：点「发送邮件」会用系统默认邮件客户端打开一封新邮件
        self.authorCard = HyperlinkCard(
            f"mailto:{AUTHOR_MAIL}", "发送邮件", FIF.MAIL, "作者",
            f"{AUTHOR_NAME} · {AUTHOR_MAIL}", self.aboutGroup,
        )

        self.headerRow, _header = makeHeader(self.view, "设置")

        applyPageLayout(self.vBoxLayout)
        self.vBoxLayout.addWidget(self.headerRow)
        # 卡片必须走 SettingCardGroup.addSettingCard：只把 group 当 parent 传进去，
        # 卡片只是"挂"在它下面、没进布局，结果就是全叠在分组左上角挤成一团。
        for group, cards in (
            (self.personalGroup, (self.themeCard, self.themeColorCard,
                                  self.micaCard, self.bgImageCard,
                                  self.bgBlurCard, self.zoomCard)),
            (self.appGroup, (self.bootCard, self.trayCard, self.guideCard,
                             self.helpCard, self.configDirCard, self.resetCard)),
            (self.rvcGroup, (self.rvcRootCard, self.rvcResetCard,
                             self.rvcDmlCard)),
            (self.aboutGroup, (self.updateCard, self.repoCard, self.authorCard)),
        ):
            group.addSettingCards(list(cards))
            self.vBoxLayout.addWidget(group)
        self.vBoxLayout.addStretch(1)

        setupScrollPage(self, self.view)

        self.updateCard.clicked.connect(self._onCheckUpdate)
        self.guideCard.clicked.connect(self._onShowGuide)
        self.rvcRootCard.clicked.connect(self._onChooseRvcRoot)
        self.rvcResetCard.clicked.connect(self._onResetRvcRoot)
        self.configDirCard.clicked.connect(self._onOpenConfigDir)
        self.resetCard.clicked.connect(self._onResetConfig)
        self.bgImageCard.chooseButton.clicked.connect(self._onChooseBackground)
        self.bgImageCard.clearButton.clicked.connect(self._onClearBackground)
        # 配置被别处改动（比如「恢复默认配置」）时，卡片跟着回到一致的状态
        cfg.bgImagePath.valueChanged.connect(self._syncBackgroundCards)
        self._syncBackgroundCards()

        # 开机自启：以系统里真实存在的启动项为准（用户可能在外面手动删过），
        # 不能只看上次存的配置值，否则开关和实际状态对不上。
        self.bootCard.setChecked(isStartOnBoot())
        cfg.set(cfg.startOnBoot, self.bootCard.isChecked(), save=False)
        self.bootCard.checkedChanged.connect(self._onStartOnBootChanged)

        self.trayCard.setEnabled(self.bootCard.isChecked())
        self.trayCard.checkedChanged.connect(self._onStartToTrayChanged)

    def _onShowGuide(self):
        """重看首次那份分步说明（看过一次之后就只有这个入口了）。"""
        showGuide(self.window())

    def _onStartToTrayChanged(self, enabled: bool):
        """开机放托盘：改完要把自启命令**重写一遍**。

        ⚠️ 自启是开机时由系统执行那条命令的，改一个配置项不会影响已经写好的启动
        项 —— 必须带着新参数（--tray）重新写一次，否则这个开关等于没生效。
        """
        cfg.set(cfg.startToTray, enabled)
        if not isStartOnBoot():
            return
        if setStartOnBoot(True):
            self._infoBar("success", "已更新开机方式",
                          "自启时会缩到托盘，不弹主窗口" if enabled
                          else "自启时会正常打开主窗口")
            return
        self._infoBar("error", "设置失败", "开机放托盘没能写进启动项")

    def _onStartOnBootChanged(self, enabled: bool):
        """真的往系统启动项里写一条（任务计划程序 / autostart / launchd）。"""
        # 托盘开关是自启的附加项，自启关了它就跟着没意义
        self.trayCard.setEnabled(enabled)
        if setStartOnBoot(enabled):
            if not enabled:
                self._infoBar("success", "已关闭", "已取消开机自启")
                return
            # 说清楚到底写在哪儿：退回注册表的话用户心里得有数
            method = lastMethod()
            self._infoBar(
                "success", "已开启",
                "已建任务计划「LinVox」：登录后 1 分钟自动启动" if method == "task"
                else "任务计划程序不可用（权限 / 策略限制），已改写入注册表启动项")
            return

        # 写不进去就把开关拨回去，别让界面显示的和系统里的不一样
        self.bootCard.checkedChanged.disconnect(self._onStartOnBootChanged)
        self.bootCard.setChecked(not enabled)
        self.bootCard.checkedChanged.connect(self._onStartOnBootChanged)
        self._infoBar("error", "设置失败", "无法写入系统启动项（可能被安全软件拦截）")

    def _onChooseRvcRoot(self):
        """选择 RVC 工程目录（里面要有 runtime/、configs/、assets/、infer/）。"""
        path = QFileDialog.getExistingDirectory(
            self, "选择 RVC 工程目录", cfg.get(cfg.rvcRoot) or ""
        )
        if not path:
            return
        cfg.set(cfg.rvcRoot, path)
        self.rvcRootCard.setContent(path)

        # 顺手校验一下，选错目录要当场说，别等切到变声页才发现引擎起不来
        missing = [d for d in RVC_REQUIRED_DIRS
                   if not os.path.isdir(os.path.join(path, d))]
        if missing:
            self._infoBar("warning", "目录可能不对",
                          f"里面没有 {'、'.join(missing)}，引擎很可能起不来")
        else:
            self._infoBar("success", "已切换",
                          "切到分离 / 训练 / 模型管理页会自动重新载入引擎")

    def _onResetRvcRoot(self):
        """回退到随软件自带的内嵌工程（功能最全的那个）。"""
        cfg.set(cfg.rvcRoot, DEFAULT_RVC_ROOT)
        self.rvcRootCard.setContent(DEFAULT_RVC_ROOT)
        self._infoBar("success", "已切换",
                      "RVC 工程目录已恢复为内嵌工程，切到变声页会自动重新扫描")

    def _onChooseBackground(self):
        """选一张图当窗口背景（路径进配置，下次启动还在）。"""
        current = cfg.get(cfg.bgImagePath) or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", os.path.dirname(current), BG_IMAGE_FILTER,
        )
        if not path:
            return
        cfg.set(cfg.bgImagePath, path)     # 直接落盘：换完就重启也不丢
        self._infoBar("success", "背景已更换", "图片路径写进了配置，下次启动还在")

    def _onClearBackground(self):
        """去掉背景图，回到主题自带的底色。"""
        cfg.set(cfg.bgImagePath, "")
        self._infoBar("success", "已清除背景", "已恢复主题自带的底色")

    def _syncBackgroundCards(self, *_args):
        """卡片文字 / 按钮可用性跟着配置走。"""
        path = cfg.get(cfg.bgImagePath) or ""
        if not path:
            self.bgImageCard.setContent("（未设置，用主题自带的底色）")
        elif os.path.isfile(path):
            self.bgImageCard.setContent(os.path.basename(path))
        else:
            # 图被移走 / 删了：说清楚，别让人以为背景功能坏了
            self.bgImageCard.setContent(f"（这张图找不到了）{os.path.basename(path)}")
        self.bgImageCard.setToolTip(path)
        # 没有背景图时，模糊度滑了也没东西可糊
        self.bgImageCard.clearButton.setEnabled(bool(path))
        self.bgBlurCard.setEnabled(bool(path))

    def _onOpenConfigDir(self):
        """在系统文件管理器里打开配置目录（config.json + defaults.json）。"""
        QDesktopServices.openUrl(QUrl.fromLocalFile(configDir()))

    def _onResetConfig(self):
        """恢复出厂配置：先二次确认，再整体回退并落盘。"""
        box = MessageBox(
            "恢复默认配置？",
            "所有设置（主题、缩放、RVC / 训练 / 分离参数…）都会回到出厂值，"
            "改过的内容找不回来。",
            self.window(),
        )
        box.yesButton.setText("恢复默认")
        box.cancelButton.setText("取消")
        if not box.exec():
            return

        resetConfig()
        # 这两张卡片的内容是手写的文本、没绑配置项，得手动同步回默认
        self.rvcRootCard.setContent(cfg.get(cfg.rvcRoot) or "（未设置）")
        # 开机自启以系统里的真实状态为准（重置会连带把启动项删掉）
        self.bootCard.setChecked(isStartOnBoot())
        self.trayCard.setEnabled(self.bootCard.isChecked())
        self._infoBar("success", "已恢复默认",
                      f"配置已重置并写入 {configPath()}，部分页面重开后生效")

    def _onCheckUpdate(self):
        """没有更新服务器，就打开发布页让人自己看 —— 别假装有检查。"""
        QDesktopServices.openUrl(QUrl(RELEASE_URL))

    def _infoBar(self, level: str, title: str, content: str):
        """统一弹提示（右上角，2.5s 自动消失）。"""
        getattr(InfoBar, level)(
            title=title, content=content,
            orient=Qt.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=2500, parent=self,
        )
