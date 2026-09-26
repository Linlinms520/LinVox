#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从一张原图生成 LinVox 的品牌素材：图标 + 带标题的图。

产出（都写在 app/resources/ 下）：
  logo.png    256×256，四角按 Win11 图标的圆角抠成透明
  logo.ico    16/20/24/32/40/48/64/128/256 多尺寸，标题栏 + 任务栏用
  title.jpg   1280×400 的横幅：原图 + 压暗 + 「LinVox」标题和副标题

用法（不满意构图就调参数重跑，原图不会被改动）：

    python tools/make_brand.py --src D:/a.jpg        # 用默认裁切
    python tools/make_brand.py --src D:/a.jpg
    python tools/make_brand.py --cx 0.42 --cy 0.35   # 图标裁到人物上半身
    python tools/make_brand.py --banner-y 0.20       # 横幅取更靠上的一条

所有裁切参数都是**占原图的比例**（0~1），换图也不用改成像素。
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 原图是个人素材、不随仓库分发，所以不写死任何本机路径 —— 用 --src 自己指定
DEFAULT_SRC = ""
OUT_DIR = os.path.join(ROOT, "app", "resources")

ICON_SIZE = 256
ICON_RADIUS = 0.22          # 圆角半径占边长比例（Win11 图标观感）
TITLE_W, TITLE_H = 1280, 400
TITLE_TEXT = "LinVox"
TITLE_SUB = "RVC 实时变声工作台"

# 微软雅黑 Bold：中文副标题必须有它；Segoe UI Bold 没有中文字形
FONT_CANDIDATES = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/seguisb.ttf",
                   "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf")


def loadFont(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def roundedMask(size: int, radiusRatio: float) -> Image.Image:
    """圆形/圆角遮罩：图标在任务栏和标题栏上不会是一个硬邦邦的方块。"""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * radiusRatio), fill=255)
    # 4 倍超采样下再缩回来 = 免费的抗锯齿
    return mask.resize((size, size), Image.LANCZOS)


def cropRatio(img: Image.Image, x, y, w, h) -> Image.Image:
    """按比例裁一块出来（超出边界会自动夹住）。"""
    W, H = img.size
    left = max(0, int(W * x))
    top = max(0, int(H * y))
    return img.crop((left, top, min(W, left + int(W * w)), min(H, top + int(H * h))))


def cropCover(img: Image.Image, width: int, height: int) -> Image.Image:
    """等比放大铺满目标尺寸，多出来的裁掉（和 Qt 的 KeepAspectRatioByExpanding 一样）。"""
    src = img.width / img.height
    dst = width / height
    if src > dst:                      # 原图更宽 → 裁左右
        newW = int(img.height * dst)
        left = (img.width - newW) // 2
        img = img.crop((left, 0, left + newW, img.height))
    else:                              # 原图更高 → 裁上下
        newH = int(img.width / dst)
        top = (img.height - newH) // 2
        img = img.crop((0, top, img.width, top + newH))
    return img.resize((width, height), Image.LANCZOS)


def makeIcon(src: Image.Image, cx, cy, side) -> Image.Image:
    """图标：以 (cx, cy) 为中心裁一个正方形，再抠圆角。"""
    square = min(src.size) * side if side <= 1 else side
    half = square / 2
    px, py = src.width * cx, src.height * cy
    box = (max(0, px - half), max(0, py - half),
           min(src.width, px + half), min(src.height, py + half))
    crop = src.crop(tuple(int(v) for v in box)).resize(
        (ICON_SIZE, ICON_SIZE), Image.LANCZOS)

    icon = Image.new("RGBA", (ICON_SIZE, ICON_SIZE))
    icon.paste(crop, (0, 0), roundedMask(ICON_SIZE, ICON_RADIUS))
    return icon


def makeTitle(src: Image.Image, x, y, w, h) -> Image.Image:
    """带标题的横幅：原图 + 压暗 + 文字。

    压暗是必须的：原图有亮有暗，白字直接压上去，压到亮处就是一片糊。
    这里从左到右拉一层渐变遮罩，左边最深 —— 文字靠左，正好落在暗的那半边。
    """
    base = cropCover(cropRatio(src, x, y, w, h), TITLE_W, TITLE_H).convert("RGBA")

    # 从左到右拉一层黑纱：左边最深（0.62），右边渐隐到 0.12 留点原图亮部
    scrim = Image.new("RGBA", (TITLE_W, TITLE_H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for i in range(TITLE_W):
        ratio = min(1.0, i / (TITLE_W * 0.55))
        sd.line(((i, 0), (i, TITLE_H)),
                fill=(0, 0, 0, int(255 * (0.62 - 0.5 * ratio))))
    base = Image.alpha_composite(base, scrim)

    # 文字先画一层模糊黑影，再画白字：亮底上也读得清
    #
    # ⚠️ 位置要**竖向居中**：首页那张卡是"等比放大铺满 + 裁掉多余"的，窗口越宽
    # 裁掉的上下越多 —— 只有贴着中线排的文字才保证不会被裁掉。
    titleFont = loadFont(68)
    subFont = loadFont(28)
    blockH = titleFont.size + 12 + subFont.size
    x0, y0 = 72, (TITLE_H - blockH) // 2
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.text((x0 + 3, y0 + 3), TITLE_TEXT, font=titleFont, fill=(0, 0, 0, 200))
    sdraw.text((x0 + 2, y0 + titleFont.size + 14), TITLE_SUB, font=subFont,
               fill=(0, 0, 0, 180))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))

    draw = ImageDraw.Draw(base)
    draw.text((x0, y0), TITLE_TEXT, font=titleFont, fill=(255, 255, 255, 255))
    draw.text((x0, y0 + titleFont.size + 12), TITLE_SUB, font=subFont,
              fill=(235, 245, 245, 235))
    return base.convert("RGB")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="生成 LinVox 图标与标题图")
    p.add_argument("--src", default=DEFAULT_SRC, help="原图路径")
    p.add_argument("--out", default=OUT_DIR, help="输出目录")
    p.add_argument("--cx", type=float, default=0.5, help="图标中心（横向比例）")
    p.add_argument("--cy", type=float, default=0.42, help="图标中心（纵向比例）")
    p.add_argument("--side", type=float, default=0.62,
                   help="图标正方形边长占短边的比例")
    p.add_argument("--banner-x", type=float, default=0.18)
    p.add_argument("--banner-y", type=float, default=0.22)
    p.add_argument("--banner-w", type=float, default=0.64)
    p.add_argument("--banner-h", type=float, default=0.56)
    args = p.parse_args(argv)

    if not args.src or not os.path.exists(args.src):
        print("找不到原图：%s" % (args.src or "<未指定>"))
        print("用法：python tools/make_brand.py --src D:/a.jpg")
        return 1

    src = ImageOps.exif_transpose(Image.open(args.src)).convert("RGB")
    print("原图：%dx%d  %s" % (src.width, src.height, args.src))

    os.makedirs(args.out, exist_ok=True)
    icon = makeIcon(src, args.cx, args.cy, args.side)
    iconPath = os.path.join(args.out, "logo.png")
    icon.save(iconPath)
    icon.save(os.path.join(args.out, "logo.ico"),
              sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
                     (48, 48), (64, 64), (128, 128), (256, 256)])
    print("图标：%s + logo.ico（16~256 多尺寸）" % iconPath)

    title = makeTitle(src, args.banner_x, args.banner_y,
                      args.banner_w, args.banner_h)
    titlePath = os.path.join(args.out, "title.jpg")
    # 存 jpg：这是一张照片，png 要 600KB+，jpg 质量 88 只有它五分之一，看不出区别
    title.save(titlePath, quality=88, optimize=True, progressive=True)
    print("标题图：%s  %dx%d" % (titlePath, title.width, title.height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
