"""RVC 人声 / 伴奏分离 worker（PyMSS）。

对应 RVC WebUI 的「人声伴奏分离&去混响」：直接复用 tools/pymss_webui.py 的
分离内核，事件流通过 stdout 的 JSON 行回传给界面。

用法::

    python sep_worker.py --rvc-root <root> --job separate --payload '<json>'

payload: model / inp_root（文件或目录）/ vocal_dir / inst_dir / format

输出协议（stdout，每行一个 JSON）：

    {"type": "log",     "msg": "..."}
    {"type": "file",    "index": i, "total": n, "input": "...", "ok": true}
    {"type": "progress","done": x, "total": y, "message": "..."}
    {"type": "result",  "models": [...]}        仅 list_models
    {"type": "done",    "ok": true, "elapsed": 12.3}
    {"type": "error",   "msg": "...", "traceback": "..."}
"""
import argparse
import json
import os
import sys
import time
import traceback

# 同上：runtime 解释器不会自动带上脚本目录，手动补进 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rvc_boot
from rvc_boot import emit, log

_EVENT_TYPE = {
    "log": "log",
    "preparing": "log",
    "precision_attempt": "log",
    "progress": "progress",
    "file_start": "file",
    "file": "file",
    "done": "done",
    "cancelled": "log",
    "error": "error",
    "busy": "error",
}


def on_event(event):
    """把 PyMSS 的事件翻译成界面协议。"""
    on_event.finished = False
    if not isinstance(event, dict):
        return
    kind = _EVENT_TYPE.get(str(event.get("event", "")), "log")
    message = event.get("message") or ""
    if kind == "log":
        if message:
            log(message)
    elif kind == "progress":
        emit({
            "type": "progress",
            "done": event.get("done", 0),
            "total": event.get("total", 1),
            "index": event.get("file_index", 0),
            "message": message,
        })
    elif kind == "file":
        emit({
            "type": "file",
            "index": event.get("file_index", 0),
            "total": event.get("file_count", 1),
            "input": event.get("path", ""),
            "ok": event.get("ok", True),
        })
    elif kind == "done":
        on_event.finished = True
        emit({
            "type": "done",
            "ok": True,
            "processed": event.get("processed", 0),
            "successful": event.get("successful", 0),
            "failed": event.get("failed", 0),
        })
    elif kind == "error":
        emit({"type": "error", "msg": message or "分离失败"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rvc-root", required=True)
    parser.add_argument("--job", default="separate")
    parser.add_argument("--payload", default="{}")
    args = parser.parse_args()

    rvc_boot.setup(args.rvc_root)
    try:
        payload = json.loads(args.payload or "{}")
    except ValueError:
        payload = {}

    from tools.pymss_webui import PYMSS_MODEL_CHOICES, pymss_separate

    if args.job == "list_models":
        rvc_boot.emit_result(models=list(PYMSS_MODEL_CHOICES))
        return 0

    model = payload.get("model") or (PYMSS_MODEL_CHOICES[0] if PYMSS_MODEL_CHOICES else "")
    inp_root = payload.get("inp_root") or ""
    vocal_dir = payload.get("vocal_dir") or ""
    inst_dir = payload.get("inst_dir") or ""
    output_format = payload.get("format") or "wav"

    if not inp_root:
        emit({"type": "error", "msg": "请先选择要分离的音频文件或文件夹"})
        return 1

    log("分离模型：%s" % model)
    started = time.time()
    state = {"done": False}
    generator = pymss_separate(
        model, inp_root, vocal_dir, [], inst_dir, output_format, on_event,
    )
    try:
        # 生成器只有被迭代才会推进；真正的进度走 on_event 回调
        for _status in generator:
            if on_event.finished:
                state["done"] = True
    except Exception:
        emit({"type": "error", "msg": "分离过程中出错",
              "traceback": traceback.format_exc()})
        emit({"type": "done", "ok": False,
              "elapsed": round(time.time() - started, 2)})
        return 1

    if not state["done"]:
        emit({"type": "done", "ok": True,
              "elapsed": round(time.time() - started, 2)})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        rvc_boot.emit_error("分离任务启动失败", traceback.format_exc())
        raise SystemExit(1)
