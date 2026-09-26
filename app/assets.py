"""程序自带的静态资源（图标 / 首页标题图）。

素材是 :mod:`tools.make_brand` 从一张原图生成的，产出就躺在 ``app/resources/``：
``logo.png`` / ``logo.ico``（图标）、``title.png``（带「LinVox」标题的横幅图）。
这个模块只负责把路径算对 —— 缺文件的时候返回空串，调用方自己判断，
别因为一张图没生成就让整个界面起不来。
"""
import os
import sys

_RESOURCES = "resources"


def resourceDir() -> str:
    """资源目录。PyInstaller 单文件模式下会解包到 _MEIPASS，优先找那里。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "app", _RESOURCES)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _RESOURCES)


def resourcePath(name: str) -> str:
    """资源文件的完整路径（文件不在也照样返回，交给调用方判空）。"""
    return os.path.join(resourceDir(), name)


def hasResource(name: str) -> bool:
    return os.path.isfile(resourcePath(name))


def appIconPath() -> str:
    """应用图标：优先 .ico（16~256 多尺寸，标题栏和任务栏都清楚），否则退回 png。"""
    for name in ("logo.ico", "logo.png"):
        if hasResource(name):
            return resourcePath(name)
    return ""


def logoPath() -> str:
    """图标的 png 版：要画到界面上（首页头像）时用这个，.ico 取出来的图太小。"""
    return resourcePath("logo.png")


def titlePath() -> str:
    """首页顶部那张带「LinVox」标题的横幅图。"""
    return resourcePath("title.jpg")
