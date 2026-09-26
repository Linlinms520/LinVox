"""RVC 训练 worker —— 把 RVC WebUI「训练」页的整套流程搬到软件里。

界面只负责收集参数，具体命令在这里拼、子进程在这里跑、日志一行一行回传。
命令模板与 RVC 自带的 webui.py 保持一致，避免两套实现慢慢跑偏。

用法::

    python train_worker.py --rvc-root <root> --job <job> --payload '<json>'

job 取值：slice（数据切分）/ preprocess（数据预处理）/ extract（F0+Hubert 特征提取）
         train（模型训练）/ index（训练索引）/ all（一键跑完整套）

输出协议（stdout，每行一个 JSON）：

    {"type": "step", "name": "数据预处理", "index": 1, "total": 3}
    {"type": "log",  "msg": "..."}
    {"type": "done", "ok": true, "elapsed": 12.3}
    {"type": "error","msg": "...", "traceback": "..."}
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rvc_boot
from rvc_boot import emit, log

AUDIO_EXT = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus")


# ------------------------------------------------------------------ 命令拼装
def _s(value):
    return str(value)


def build_slice(p, python):
    """数据切分：train/dataset/slicer2.py（目录就逐个文件跑）。"""
    out_dir = _s(p.get("out_dir") or "").strip()
    source = _s(p.get("input_dir") or "").strip()
    if not source:
        raise ValueError("请先选择要切分的音频文件或文件夹")
    if not out_dir:
        out_dir = os.path.join(os.path.dirname(source), "sliced")
    os.makedirs(out_dir, exist_ok=True)

    files = []
    if os.path.isdir(source):
        for name in sorted(os.listdir(source)):
            if name.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(source, name))
    elif os.path.isfile(source):
        files.append(source)
    if not files:
        raise ValueError("目录里没有音频文件：%s" % source)

    commands = []
    for path in files:
        argv = [
            python, "train/dataset/slicer2.py", path,
            "--out", out_dir,
            "--db_thresh", _s(p.get("db_thresh", -40)),
            "--min_length", _s(p.get("min_length", 5000)),
            "--min_interval", _s(p.get("min_interval", 300)),
            "--hop_size", _s(p.get("hop_size", 10)),
            "--max_sil_kept", _s(p.get("max_sil_kept", 500)),
        ]
        commands.append(("数据切分", argv))
    return commands


def build_preprocess(p, python, config):
    """数据预处理：train/preprocess.py 做切片 + 去噪，产出 2_Name/ 目录。"""
    exp = _s(p.get("exp_dir") or "").strip()
    dataset = _s(p.get("dataset_dir") or "").strip()
    if not exp:
        raise ValueError("请先填写实验名")
    if not dataset or not os.path.isdir(dataset):
        raise ValueError("训练集目录不存在：%s" % (dataset or "(未设置)"))
    argv = [
        python, "train/preprocess.py", dataset,
        _s(p.get("sample_rate", "48k")),
        _s(p.get("workers", 4)),
        os.path.join("logs", exp),
        "False" if p.get("parallel", True) else "True",
        _s(config.preprocess_per),
    ]
    return [("数据预处理", argv)]


def build_extract(p, python, config):
    """F0 与 Hubert 特征提取。"""
    exp = _s(p.get("exp_dir") or "").strip()
    if not exp:
        raise ValueError("请先填写实验名")
    logs = os.path.join("logs", exp)
    f0method = _s(p.get("f0method", "rmvpe"))
    version = _s(p.get("version", "v2"))
    gpus = [g for g in _s(p.get("gpus", "0")).split("-") if g != ""]
    commands = []

    if p.get("if_f0", True):
        if p.get("dml", False):
            argv = [python, "train/dataset/extract_f0.py", "dml", logs]
        elif f0method == "pm" or not gpus:
            argv = [python, "train/dataset/extract_f0.py", "cpu", logs,
                    _s(p.get("workers", 4)), f0method]
        else:
            argv = [python, "train/dataset/extract_f0.py", "cuda",
                    _s(len(gpus)), "0", gpus[0], logs, _s(config.is_half)]
        commands.append(("F0 特征提取", argv))

    commands.append((
        "Hubert 特征提取",
        [python, "train/dataset/extract_hubert_feature.py", _s(config.device),
         "1", "0", logs, version, _s(config.is_half)],
    ))
    return commands


def build_train(p, python):
    """模型训练：train/train.py。"""
    exp = _s(p.get("exp_dir") or "").strip()
    if not exp:
        raise ValueError("请先填写实验名")
    argv = [
        python, "train/train.py",
        "-e", exp,
        "-sr", _s(p.get("sample_rate", "48k")),
        "-f0", "1" if p.get("if_f0", True) else "0",
        "-bs", _s(p.get("batch_size", 4)),
        "-g", _s(p.get("gpus", "0")),
        "-te", _s(p.get("total_epoch", 100)),
        "-se", _s(p.get("save_epoch", 10)),
    ]
    if p.get("pretrained_g"):
        argv += ["-pg", _s(p.get("pretrained_g"))]
    if p.get("pretrained_d"):
        argv += ["-pd", _s(p.get("pretrained_d"))]
    argv += [
        "-l", "1" if p.get("save_latest", True) else "0",
        "-c", "1" if p.get("cache_gpu", True) else "0",
        "-sw", "1" if p.get("save_every", False) else "0",
        "-v", _s(p.get("version", "v2")),
    ]
    return [("模型训练", argv)]


def build_index(p, python, env):
    """训练索引：train/train_index.py。"""
    exp = _s(p.get("exp_dir") or "").strip()
    if not exp:
        raise ValueError("请先填写实验名")
    argv = [
        python, "train/train_index.py", exp,
        _s(p.get("version", "v2")),
        env.get("outside_index_root", "assets/indices"),
        _s(p.get("workers", 4)),
    ]
    return [("训练索引", argv)]


def build_commands(job, p, python, config, env):
    if job == "slice":
        return build_slice(p, python)
    if job == "preprocess":
        return build_preprocess(p, python, config)
    if job == "extract":
        return build_extract(p, python, config)
    if job == "train":
        return build_train(p, python)
    if job == "index":
        return build_index(p, python, env)
    if job == "all":
        commands = build_preprocess(p, python, config)
        commands += build_extract(p, python, config)
        commands += build_train(p, python)
        commands += build_index(p, python, env)
        return commands
    raise ValueError("未知任务：%s" % job)


# ------------------------------------------------------------------ 执行
def run_command(name, argv, index, total):
    """跑一条命令，把子进程 stdout 逐行回传。返回退出码。"""
    emit({"type": "step", "name": name, "index": index, "total": total,
          "cmd": " ".join(argv)})
    log("$ %s" % " ".join(argv[1:]))

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        # 放进独立进程组，界面点停止时可以整棵树一起杀
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(argv, **popen_kwargs)
    with proc.stdout as stream:
        for line in stream:
            line = line.rstrip("\r\n")
            if line.strip():
                log(line)
    return proc.wait()


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

    from configs.config import Config

    config = Config()
    payload.setdefault("dml", bool(config.dml))

    env = {
        "outside_index_root": os.getenv("outside_index_root", "assets/indices"),
    }
    commands = build_commands(args.job, payload, sys.executable, config, env)

    started = time.time()
    total = len(commands)
    for index, (name, argv) in enumerate(commands, start=1):
        code = run_command(name, argv, index, total)
        if code != 0:
            emit({
                "type": "error",
                "msg": "「%s」失败（退出码 %s），后续步骤已跳过" % (name, code),
            })
            emit({"type": "done", "ok": False,
                  "elapsed": round(time.time() - started, 2)})
            return code
    emit({"type": "done", "ok": True, "elapsed": round(time.time() - started, 2)})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        rvc_boot.emit_error("训练任务启动失败", traceback.format_exc())
        raise SystemExit(1)
