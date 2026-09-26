"""RVC 模型管理 worker（ckpt 处理）。

对应 RVC WebUI 的「ckpt 处理」：模型信息、修改信息、提取小模型、模型融合。
这些操作只动 .pth，不需要 GPU，跑在 RVC runtime 解释器里即可
（torch.load 的版本必须和训练时一致，用界面自己的 Python 可能读不了）。

用法::

    python ckpt_worker.py --rvc-root <root> --job <job> --payload '<json>'

job: scan / info / change_info / extract / merge

输出协议（stdout，每行一个 JSON）：

    {"type": "log",    "msg": "..."}
    {"type": "result", "text": "...", "models": [...]}   结构化结果
    {"type": "done",   "ok": true}
    {"type": "error",  "msg": "...", "traceback": "..."}
"""
import argparse
import json
import os
import sys
import traceback

# 同上：runtime 解释器不会自动带上脚本目录，手动补进 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rvc_boot
from rvc_boot import emit, log


def scan_models():
    """扫描可直接选用的权重：assets/weights 下的成品 + logs/** 的训练产物。"""
    weight_root = os.getenv("weight_root", "assets/weights")
    models = []
    if os.path.isdir(weight_root):
        for name in sorted(os.listdir(weight_root)):
            if name.lower().endswith(".pth"):
                models.append(os.path.join(weight_root, name))
    logs = os.getenv("index_root", "logs")
    if os.path.isdir(logs):
        for dirpath, _dirnames, filenames in os.walk(logs):
            for name in filenames:
                if name.lower().endswith(".pth") and name.startswith("G_"):
                    models.append(os.path.join(dirpath, name))
    return sorted(models)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rvc-root", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--payload", default="{}")
    args = parser.parse_args()

    rvc_boot.setup(args.rvc_root)
    try:
        payload = json.loads(args.payload or "{}")
    except ValueError:
        payload = {}

    if args.job == "scan":
        rvc_boot.emit_result(models=scan_models())
        emit({"type": "done", "ok": True})
        return 0

    try:  # 2026/07 内嵌版
        from train.process_ckpt import (
            change_info, extract_small_model, merge, show_info,
        )
    except ImportError:  # 老版本 RVC 放在 infer/modules/train/
        from infer.modules.train.process_ckpt import (
            change_info, extract_small_model, merge, show_info,
        )

    job = args.job
    if job == "info":
        path = payload.get("path") or ""
        if not os.path.isfile(path):
            emit({"type": "error", "msg": "模型文件不存在：%s" % (path or "(未设置)")})
            return 1
        rvc_boot.emit_result(text=str(show_info(path)))
    elif job == "change_info":
        path = payload.get("path") or ""
        info = payload.get("info") or ""
        name = payload.get("name") or ""
        if not os.path.isfile(path):
            emit({"type": "error", "msg": "模型文件不存在：%s" % (path or "(未设置)")})
            return 1
        log(str(change_info(path, info, name)))
    elif job == "extract":
        path = payload.get("path") or ""
        name = payload.get("name") or ""
        if not os.path.isfile(path):
            emit({"type": "error", "msg": "模型文件不存在：%s" % (path or "(未设置)")})
            return 1
        if not name:
            emit({"type": "error", "msg": "请先填写小模型保存名（需带 .pth）"})
            return 1
        log(str(extract_small_model(
            path, name, payload.get("sr", "48k"),
            int(payload.get("if_f0", 1)), payload.get("info") or "",
            payload.get("version", "v2"),
        )))
    elif job == "merge":
        path1 = payload.get("path_a") or ""
        path2 = payload.get("path_b") or ""
        for label, path in (("模型 A", path1), ("模型 B", path2)):
            if not os.path.isfile(path):
                emit({"type": "error", "msg": "%s 不存在：%s" % (label, path or "(未设置)")})
                return 1
        log(str(merge(
            path1, path2, float(payload.get("alpha", 0.5)),
            payload.get("sr", "48k"), int(payload.get("if_f0", 1)),
            payload.get("info") or "", payload.get("name") or "",
            payload.get("version", "v2"),
        )))
    else:
        emit({"type": "error", "msg": "未知任务：%s" % job})
        return 1

    emit({"type": "done", "ok": True})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        rvc_boot.emit_error("模型处理失败", traceback.format_exc())
        raise SystemExit(1)
