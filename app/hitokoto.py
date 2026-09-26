"""一言（Hitokoto）：https://v1.hitokoto.cn/

三件事：
1. **24 小时本地缓存**（配置目录 ``quote_cache.json``）：一天只请求一次，
   断网 / 超时也能看到昨天那一句，不会开屏一片空白。
2. **联网失败自动重试**，实在拿不到就用缓存（哪怕过期）或内置兜底文案。
3. **请求在 QThread 里跑**，网络再慢也不会把界面卡住。

``requests`` 装了就用它，没装自动退回标准库 ``urllib`` —— 一言是锦上添花，
不能因为它把整个应用变成"必须联网才能装"。
"""
import json
import os
import random
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import configDir

API_URL = "https://v1.hitokoto.cn/"

# 分类：a动画 b漫画 c游戏 d影视 e小说 f原创 g网络
CATEGORIES = "abcdefg"

# ⚠️ 坑点：一言的接口会拦默认的 Python UA（urllib / requests 默认 UA 都返回 403），
# 必须带一个像浏览器的 User-Agent，否则看起来"代码没错但一直拉不到"。
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LinVox/1.0",
    "Accept": "application/json",
}

CACHE_TTL = 24 * 3600     # 一天换一句就够了
TIMEOUT = 5               # 单次请求超时（秒）
RETRIES = 3               # 最多重试几次
RETRY_GAP = 1.0           # 重试间隔（秒）

# 断网时的兜底：宁可给一句老话，也不给空白
FALLBACKS = [
    "生活就像一盒巧克力，你永远不知道下一颗是什么味道。",
    "重要的不是所处的位置，而是所朝的方向。",
    "慢慢来，比较快。",
    "把今天过好，明天自然不会太差。",
    "所谓天赋，不过是把热爱坚持得久一点。",
]


def cachePath() -> str:
    """缓存文件（跟 config.json 放一起，别在软件目录里乱拉文件）。"""
    return str(Path(configDir()) / "quote_cache.json")


def networkDisabled() -> bool:
    """测试 / 离线环境用 ``FLUENT_NO_NETWORK=1`` 关掉真实请求。"""
    return bool(os.getenv("FLUENT_NO_NETWORK"))


# ------------------------------------------------------------------ 缓存
def loadCache(ignoreExpire: bool = False):
    """读缓存。没过期（或 ignoreExpire）才返回，否则返回 None。"""
    try:
        with open(cachePath(), encoding="utf-8") as f:
            data = json.load(f)
        quote = data.get("quote") or {}
        if not quote.get("text"):
            return None
        if not ignoreExpire and time.time() - float(data.get("timestamp", 0)) > CACHE_TTL:
            return None
        return quote
    except Exception:
        return None


def saveCache(quote: dict):
    try:
        with open(cachePath(), "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "quote": quote},
                      f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 缓存写不进去不影响显示


# ------------------------------------------------------------------ 请求
def _httpGetJson(url: str, params: dict, timeout: float) -> dict:
    """GET 一个 JSON。优先 requests，没装就走标准库 urllib。"""
    try:
        import requests
        response = requests.get(url, params=params, timeout=timeout, headers=HEADERS)
        response.raise_for_status()
        return response.json()
    except ImportError:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        request = Request("%s?%s" % (url, urlencode(params)), headers=HEADERS)
        with urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


def fetchQuote(category=None, retries: int = RETRIES) -> dict:
    """同步拉一句（请在子线程里调）。拿不到也保证返回一句能显示的话。"""
    for attempt in range(max(retries, 1)):
        if networkDisabled():
            break
        try:
            params = {"encode": "json"}
            if category:
                params["c"] = category
            else:
                params["c"] = random.choice(CATEGORIES)
            data = _httpGetJson(API_URL, params, TIMEOUT)
            text = (data.get("hitokoto") or "").strip()
            if text:
                quote = {
                    "text": text,
                    "from": data.get("from") or "",
                    "creator": data.get("creator") or "",
                }
                saveCache(quote)
                return quote
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(RETRY_GAP)

    stale = loadCache(ignoreExpire=True)      # 过期缓存也比没有强
    if stale:
        return stale

    return {"text": random.choice(FALLBACKS), "from": "", "creator": ""}


def formatQuote(quote: dict) -> str:
    """拼成一行：``「正文」—— 出自《来源》 by 作者``。"""
    text = (quote or {}).get("text") or ""
    if not text:
        return ""
    source = (quote.get("from") or "").strip()
    creator = (quote.get("creator") or "").strip()

    tail = ""
    if source:
        tail = "《%s》" % source
    if creator:
        tail = "%s by %s" % (tail, creator) if tail else "by %s" % creator
    if tail:
        tail = "—— " + tail
    return "「%s」%s" % (text, tail)


# ------------------------------------------------------------------ 线程
class QuoteFetcher(QThread):
    """后台取一句。结果通过 quoteReady 回主线程，绝不抛异常。"""

    quoteReady = Signal(dict)

    def __init__(self, force: bool = False, category=None, parent=None):
        super().__init__(parent)
        self.force = force          # True：无视缓存，重新拉
        self.category = category

    def run(self):
        quote = None
        if not self.force:
            quote = loadCache()     # 24 小时内直接用缓存，一次网都不用上
        if quote is None:
            quote = fetchQuote(self.category)
        if self.isInterruptionRequested():
            return
        self.quoteReady.emit(quote)

    def stop(self):
        """退出前收掉线程：先请它中断，再等一小会儿。"""
        if not self.isRunning():
            return
        self.requestInterruption()
        self.wait(1500)
