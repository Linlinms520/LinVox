"""RVC worker 公共引导：把 RVC 工程接进当前子进程。

worker.py / train_worker.py / sep_worker.py / ckpt_worker.py 都要做同一件事：
切到 RVC 根目录、把它的模块路径塞进 sys.path、把 weight_root 之类的
路径变量提前写进环境变量（configs/config.py 在 import 时就会读）。
集中在这里，避免四个文件各写一份然后慢慢跑偏。

注意：这些脚本由 ``app/rvc/`` 目录直接启动，sys.path[0] 就是 app/rvc，
所以 ``import rvc_boot`` 可用，且不会触发 ``app.rvc`` 包的初始化。
"""
import json
import os
import sys

# 内嵌 RVC（2026/07 版）不再自带 .env，这些是 webui.py 里的默认值。
# 老版本 RVC 有 .env 时以 .env 为准。
DEFAULT_ENV = {
    "weight_root": "assets/weights",
    "weight_pymss_root": "assets/pymss_weights",
    "index_root": "logs",
    "outside_index_root": "assets/indices",
    "rmvpe_root": "assets/rmvpe",
}


def setup(rvc_root: str) -> str:
    """切目录 + 铺环境变量。返回 RVC 根目录的绝对路径。"""
    # RVC 的 Config 会再跑一次 argparse 解析 sys.argv，留着我们的参数会
    # 让它当成未知参数直接 sys.exit(2)（表现为"没收到命令就退出"）。
    sys.argv = sys.argv[:1]

    root = os.path.abspath(rvc_root)
    os.chdir(root)          # RVC 大量使用相对路径（configs/、assets/、logs/）
    if root not in sys.path:
        sys.path.insert(0, root)

    dotenv = os.path.join(root, ".env")
    if os.path.isfile(dotenv):
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv, override=True)
        except Exception:
            pass
    for key, value in DEFAULT_ENV.items():
        os.environ.setdefault(key, value)

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    try:
        temp_dir = os.path.join(root, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        os.environ.setdefault("TEMP", temp_dir)
    except OSError:
        pass
    return root


def emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg):
    emit({"type": "log", "msg": str(msg)})


def emit_error(msg, detail=""):
    payload = {"type": "error", "msg": str(msg)}
    if detail:
        payload["traceback"] = str(detail)
    emit(payload)


def emit_result(**payload):
    """结构化结果回传（模型信息、扫描结果等）。"""
    payload.setdefault("type", "result")
    emit(payload)


def emit_done(**payload):
    payload.setdefault("type", "done")
    emit(payload)
