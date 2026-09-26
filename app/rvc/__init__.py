"""内置 RVC 引擎（整个 RVC 工程已随软件分发，开箱即用）。

- ``realtime_worker.py``：实时变声的无界面引擎（实时控制台的 headless 版）
- ``train_worker.py``：训练流水线（切分 / 预处理 / 特征提取 / 训练 / 索引）
- ``sep_worker.py``：人声伴奏分离 & 去混响（PyMSS）
- ``ckpt_worker.py``：模型管理（模型信息 / 改信息 / 提取小模型 / 融合）
- ``engine.py``：界面侧引擎，QProcess 驱动以上 worker 并转发信号
- ``jobs.py``：训练 / 分离 / 模型管理共用的任务控制器

从本包外部只需要：

    from app.rvc import RvcEngine, RealtimeController, JobController
"""
from .engine import (
    DEFAULT_RVC_ROOT,
    AUDIO_FILTER,
    CKPT_WORKER_SCRIPT,
    RealtimeController,
    RvcEngine,
    SEP_WORKER_SCRIPT,
    TRAIN_WORKER_SCRIPT,
)
from .jobs import JobController

__all__ = [
    "RvcEngine", "RealtimeController", "JobController",
    "DEFAULT_RVC_ROOT", "AUDIO_FILTER",
    "TRAIN_WORKER_SCRIPT", "SEP_WORKER_SCRIPT", "CKPT_WORKER_SCRIPT",
]
