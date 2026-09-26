"""RVC 实时变声 worker —— 跑在 **RVC 自带 runtime 解释器** 里。

这是 RVC 官方实时控制台（realtime_gui.py / 旧版 gui_v1.py）的"无界面版"：
把它的实时推理链路（音频流采集 → 响应门限 → 降噪 → Hubert/模型推理 →
响度对齐 → SOLA → 相位声码器 → 播放）完整搬过来，但界面部分换成 JSON 协议，
由 LinVox 的「实时变声」页遥控。这样界面进程依旧不用装 torch。

协议（stdout，每行一个事件，均带前缀 ``@@RTC@@``，方便与库的杂散 print 区分）：

    {"type": "ready"}
    {"type": "devices", "hostapis": [...], "selected": "MME",
     "inputs": [...], "outputs": [...],
     "input_indices": [...], "output_indices": [...],
     "defaults": [输入设备号, 输出设备号]}
    {"type": "started", "samplerate": 40000, "channels": 2,
     "delay_ms": 320, "device": "cpu"}
    {"type": "stopped"}
    {"type": "metrics", "infer_ms": 42, "delay_ms": 320}
    {"type": "log", "msg": "..."}
    {"type": "error", "msg": "...", "traceback": "..."}

命令（stdin，每行一个 JSON）：

    {"cmd": "list_devices", "hostapi": "MME"}
    {"cmd": "start", "params": {...}}   参数含义见 RealtimeCore.start
    {"cmd": "stop"}
    {"cmd": "set", "params": {...}}     只对热更新参数生效
    {"cmd": "exit"}

模型加载、hubert、音频流等重依赖都是懒加载：worker 启动只拉起
sounddevice（秒级），所以打开界面就能枚举设备，点"开始"才加载模型。
"""
import inspect
import json
import os
import queue
import sys
import time
import traceback

# 同上：runtime 解释器不会自动带上脚本目录，手动补进 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rvc_boot

PREFIX = "@@RTC@@"
_OUT = sys.__stdout__  # 真正的标准输出，emit 专用


def emit(payload):
    _OUT.write(PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    _OUT.flush()


def emit_log(msg):
    emit({"type": "log", "msg": str(msg)})


def emit_error(msg, detail=""):
    payload = {"type": "error", "msg": str(msg)}
    if detail:
        payload["traceback"] = str(detail)
    emit(payload)


class _LogRedirect:
    """把库的 print（torch/librosa/sounddevice…）转成日志事件，防污染 JSON 通道。"""

    def write(self, text):
        if text and text.strip():
            emit_log(text.rstrip())
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def printt(strr, *args):
    emit_log(strr % args if args else strr)


def has_non_ascii(path):
    """RVC 原版同款检查：torch/faiss 读取非 ASCII 路径的权重会挂。"""
    return any(ord(ch) > 127 for ch in str(path))


def phase_vocoder(a, b, fade_out, fade_in):
    """SOLA 交叉淡化的相位声码器实现（来自 DDSP-SVC，与原版一致）。"""
    import numpy as np
    import torch

    window = torch.sqrt(fade_out * fade_in)
    fa = torch.fft.rfft(a * window)
    fb = torch.fft.rfft(b * window)
    absab = torch.abs(fa) + torch.abs(fb)
    n = a.shape[0]
    if n % 2 == 0:
        absab[1:-1] *= 2
    else:
        absab[1:] *= 2
    phia = torch.angle(fa)
    phib = torch.angle(fb)
    deltaphase = phib - phia
    deltaphase = deltaphase - 2 * np.pi * torch.floor(deltaphase / 2 / np.pi + 0.5)
    w = 2 * np.pi * torch.arange(n // 2 + 1).to(a) + deltaphase
    t = torch.arange(n).unsqueeze(-1).to(a) / n
    result = (
        a * (fade_out ** 2)
        + b * (fade_in ** 2)
        + torch.sum(absab * torch.cos(w * t + phia), -1) * window / n
    )
    return result


# 热更新参数：拖动滑块立即生效，不用重启音频流
HOT_KEYS = {
    "threhold", "pitch", "formant", "index_rate", "rms_mix_rate",
    "f0method", "I_noise_reduce", "O_noise_reduce", "use_pv", "function",
}

# 内嵌 RVC（2026/07）只保留这三种音高算法，harvest / crepe 已移除
F0_METHODS = ("pm", "rmvpe", "fcpe")


class RealtimeCore:
    """实时变声核心：设备管理 + RVC 推理 + 音频流。"""

    def __init__(self):
        self.params = {}
        self.config = None
        self.rvc = None
        self.stream = None
        self.running = False
        self.function = "vc"
        self.infer_ms = 0
        self.delay_ms = 0
        self._last_metric = 0.0
        # 一个音频块的理论时长（ms）：推理耗时超过它就会出现爆音/卡顿
        self.budget_ms = 0
        self._last_slow_warn = 0.0

        # sounddevice 设备表（list_devices 后才有值）
        self.hostapis = []
        self.current_hostapi = ""
        self.input_devices = []
        self.output_devices = []
        self.input_indices = []
        self.output_indices = []



    # ------------------------------------------------------------ 设备
    def list_devices(self, hostapi_name=None):
        import sounddevice as sd

        sd._terminate()
        sd._initialize()  # 重新扫描，插拔耳机/麦克风后也能拿到新列表
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        for api in hostapis:
            for device_idx in api["devices"]:
                devices[device_idx]["hostapi_name"] = api["name"]
        self.hostapis = [api["name"] for api in hostapis]
        if hostapi_name not in self.hostapis:
            hostapi_name = self.hostapis[0] if self.hostapis else ""
        self.current_hostapi = hostapi_name

        # 记录真实的设备号：同名设备会在 MME / DirectSound / WASAPI 下各出现
        # 一次，sounddevice 按名字查设备会直接报 "Multiple ... devices found"
        inputs = [(i, d) for i, d in enumerate(devices)
                  if d["max_input_channels"] > 0
                  and d.get("hostapi_name") == hostapi_name]
        outputs = [(i, d) for i, d in enumerate(devices)
                   if d["max_output_channels"] > 0
                   and d.get("hostapi_name") == hostapi_name]

        self.input_devices = [d["name"] for _, d in inputs]
        self.output_devices = [d["name"] for _, d in outputs]
        self.input_indices = [i for i, _ in inputs]
        self.output_indices = [i for i, _ in outputs]

        emit({
            "type": "devices",
            "hostapis": self.hostapis,
            "selected": hostapi_name,
            "inputs": self.input_devices,
            "outputs": self.output_devices,
            "input_indices": self.input_indices,
            "output_indices": self.output_indices,
            "defaults": list(sd.default.device),
        })

    def set_devices(self, input_device, output_device):
        import sounddevice as sd

        if input_device in self.input_devices:
            sd.default.device[0] = self.input_indices[
                self.input_devices.index(input_device)]
        if output_device in self.output_devices:
            sd.default.device[1] = self.output_indices[
                self.output_devices.index(output_device)]
        emit_log("输入设备: %s" % input_device)
        emit_log("输出设备: %s" % output_device)

    def _device_id(self, kind):
        """把 ``sd.default.device[kind]`` 统一解析成唯一的设备号。

        它里面装的可能是名字也可能是号；装名字时，同名设备在多个 host API
        下都有一份，sounddevice 会抛 "Multiple input/output devices found"。
        这里优先用号，其次按当前 host API 下的名字反查，最后退回列表第一个。
        """
        import sounddevice as sd

        raw = sd.default.device[kind]
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass

        names, ids = ((self.input_devices, self.input_indices) if kind == 0
                      else (self.output_devices, self.output_indices))
        if raw in names:
            return ids[names.index(raw)]
        if ids:
            return ids[0]
        raise ValueError("找不到可用的%s设备" % ("输入" if kind == 0 else "输出"))

    def _device_samplerate(self):
        import sounddevice as sd

        return int(sd.query_devices(device=self._device_id(0))["default_samplerate"])

    def _device_channels(self):
        import sounddevice as sd

        max_in = sd.query_devices(device=self._device_id(0))["max_input_channels"]
        max_out = sd.query_devices(device=self._device_id(1))["max_output_channels"]
        return min(max_in, max_out, 2)

    # ------------------------------------------------------------ 启停
    def start(self, params):
        import sounddevice as sd
        import torch

        # CPU 实时推理需要把所有核心都用上；父进程为了训练/批量推理会限制
        # OMP / OpenBLAS / MKL 线程数，这里按启动时的环境变量已经放开，
        # 再显式 set_num_threads 兜底，避免 torch 内部还按保守值跑。
        torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", os.cpu_count() or 4)))
        emit_log("torch 线程数: %s" % torch.get_num_threads())

        if self.running:
            emit_error("已有变声任务在运行，请先停止")
            return

        pth_path = str(params.get("pth_path", "")).strip()
        index_path = str(params.get("index_path", "")).strip()
        if not pth_path:
            emit_error("请先选择模型（.pth）文件")
            return
        if not index_path:
            emit_error("请先选择索引（.index）文件")
            return
        if has_non_ascii(pth_path) or has_non_ascii(index_path):
            emit_error("模型 / 索引路径不能包含中文或全角字符（torch/faiss 读不了）")
            return
        for path, name in ((pth_path, "模型"), (index_path, "索引")):
            if not os.path.isfile(path):
                emit_error("%s文件不存在：%s" % (name, path))
                return

        self.params = dict(params)
        self.function = params.get("function", "vc")
        emit_log("开始加载模型：%s" % os.path.basename(pth_path))

        try:
            self.set_devices(params.get("input_device", ""),
                             params.get("output_device", ""))
        except Exception:
            emit_error("输入 / 输出设备无效，请在设备列表里重新选择",
                       traceback.format_exc())
            return

        # ---- 懒加载重依赖（torch / hubert / torchgate）----
        try:
            import librosa  # noqa: F401  audio_callback 里要用
            import numpy as np
            import torch
            import torchaudio.transforms as tat
            from configs.config import Config
            try:  # 2026/07 版把实时推理模块挪到了 infer/rtrvc.py
                from infer import rtrvc as rvc_for_realtime
            except ImportError:
                from infer.lib import rtrvc as rvc_for_realtime
            from tools.torchgate import TorchGate
        except Exception:
            emit_error("加载 RVC 运行库失败（runtime/python.exe 环境不完整？）",
                       traceback.format_exc())
            return

        # 回调跑在 PortAudio 的线程里，引用一律提前绑定到 self，
        # 不在回调里做 import（既慢又有风险）
        self.librosa = librosa
        self.np = np
        self.torch = torch
        self.F = torch.nn.functional

        emit_log("cuda_is_available: %s" % torch.cuda.is_available())
        emit_log("构建推理配置…")
        self.config = Config()
        if params.get("dml"):
            self.config.dml = True
        if self.config.dml:
            self.config.is_half = False
            # ⚠️ device 是 Config.__init__ 里按当时的 dml 算好的，事后改 dml
            #    不会自动变；必须重跑 device_config()，否则还是 CPU 推理，
            #    比原版慢一个数量级（原版就是靠 --dml 走 DirectML 才 40ms）
            try:
                (self.config.x_pad, self.config.x_query,
                 self.config.x_center, self.config.x_max) = self.config.device_config()
            except Exception:
                self.config.dml = False
                emit_log("DirectML 初始化失败，回退 CPU：\n%s"
                         % traceback.format_exc())
        self.config.use_jit = False
        emit_log("推理设备: %s（DirectML=%s）"
                 % (self.config.device, bool(self.config.dml)))

        f0method = str(params.get("f0method", "fcpe"))
        if f0method not in F0_METHODS:
            emit_error("音高算法 %s 不被内嵌 RVC 支持（可选：%s）"
                       % (f0method, " / ".join(F0_METHODS)))
            return

        # ---- 构建实时 RVC（复用上次的 hubert / rmvpe / fcpe 缓存）----
        # ⚠️ 各版本 RVC 的 RVC.__init__ 参数不一样，写死会 TypeError：
        #   2024/06 版：key, formant, pth_path, index_path, index_rate,
        #               n_cpu, inp_q, opt_q, config, last_rvc
        #   2026/07 版：key, formant, pth_path, index_path, index_rate,
        #               config, last_rvc
        # 所以按真实签名挑参数；多出来的 n_cpu / inp_q / opt_q 只有
        # harvest 音高算法会用到（我们只用 fcpe / rmvpe），给占位值即可。
        emit_log("加载 Hubert / 模型权重 / 音高模型（首次较慢）…")
        kwargs = {
            "key": int(params.get("pitch", 0)),
            "formant": float(params.get("formant", 0.0)),
            "pth_path": pth_path,
            "index_path": index_path,
            "index_rate": float(params.get("index_rate", 0.0)),
            "config": self.config,
            "last_rvc": self.rvc,
            "n_cpu": int(getattr(self.config, "n_cpu", 0) or os.cpu_count() or 1),
            "inp_q": queue.Queue(),
            "opt_q": queue.Queue(),
        }
        try:
            accepted = inspect.signature(
                rvc_for_realtime.RVC.__init__).parameters
        except (TypeError, ValueError):  # 极少数版本拿不到签名，那就全传
            accepted = None
        if accepted is not None:
            kwargs = {k: v for k, v in kwargs.items() if k in accepted}
        self.rvc = rvc_for_realtime.RVC(**kwargs)
        if getattr(self.rvc, "net_g", None) is None:
            emit_error("模型加载失败，请检查 .pth 文件是否完整（详见日志）")
            return

        sr_type = params.get("sr_type", "sr_model")
        block_time = float(params.get("block_time", 0.25))
        crossfade_time = float(params.get("crossfade_length", 0.05))
        extra_time = float(params.get("extra_time", 2.5))
        i_noise = bool(params.get("I_noise_reduce", False))

        self.samplerate = (
            self.rvc.tgt_sr if sr_type == "sr_model" else self._device_samplerate()
        )
        self.channels = self._device_channels()
        self.zc = self.samplerate // 100
        self.block_frame = (
            int(round(block_time * self.samplerate / self.zc)) * self.zc
        )
        self.block_frame_16k = 160 * self.block_frame // self.zc
        self.crossfade_frame = (
            int(round(crossfade_time * self.samplerate / self.zc)) * self.zc
        )
        self.sola_buffer_frame = min(self.crossfade_frame, 4 * self.zc)
        self.sola_search_frame = self.zc
        self.budget_ms = int(round(self.block_frame / self.samplerate * 1000))
        self.extra_frame = (
            int(round(extra_time * self.samplerate / self.zc)) * self.zc
        )

        self.input_wav = torch.zeros(
            self.extra_frame + self.crossfade_frame + self.sola_search_frame
            + self.block_frame,
            device=self.config.device, dtype=torch.float32,
        )
        self.input_wav_denoise = self.input_wav.clone()
        self.input_wav_res = torch.zeros(
            160 * self.input_wav.shape[0] // self.zc,
            device=self.config.device, dtype=torch.float32,
        )
        self.rms_buffer = np.zeros(4 * self.zc, dtype="float32")
        self.sola_buffer = torch.zeros(
            self.sola_buffer_frame, device=self.config.device, dtype=torch.float32,
        )
        self.nr_buffer = self.sola_buffer.clone()
        self.output_buffer = self.input_wav.clone()
        self.skip_head = self.extra_frame // self.zc
        self.return_length = (
            self.block_frame + self.sola_buffer_frame + self.sola_search_frame
        ) // self.zc
        self.fade_in_window = (
            torch.sin(0.5 * np.pi * torch.linspace(
                0.0, 1.0, steps=self.sola_buffer_frame,
                device=self.config.device, dtype=torch.float32,
            )) ** 2
        )
        self.fade_out_window = 1 - self.fade_in_window
        self.resampler = tat.Resample(
            orig_freq=self.samplerate, new_freq=16000, dtype=torch.float32,
        ).to(self.config.device)
        if self.rvc.tgt_sr != self.samplerate:
            self.resampler2 = tat.Resample(
                orig_freq=self.rvc.tgt_sr, new_freq=self.samplerate,
                dtype=torch.float32,
            ).to(self.config.device)
        else:
            self.resampler2 = None
        self.tg = TorchGate(
            sr=self.samplerate, n_fft=4 * self.zc, prop_decrease=0.9,
        ).to(self.config.device)

        # ---- 打开音频流 ----
        emit_log("打开音频设备…")
        hostapi = str(params.get("hostapi", ""))
        if "WASAPI" in hostapi and params.get("wasapi_exclusive"):
            extra_settings = sd.WasapiSettings(exclusive=True)
        else:
            extra_settings = None
        try:
            self.stream = sd.Stream(
                callback=self.audio_callback,
                # 显式给设备号：只靠 sd.default 时同名设备仍可能被解析成名字
                device=(self._device_id(0), self._device_id(1)),
                blocksize=self.block_frame,
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="float32",
                extra_settings=extra_settings,
            )
            self.stream.start()
        except Exception:
            self.stream = None
            emit_error("打开音频设备失败（被占用？试试换设备类型 MME/WASAPI）",
                       traceback.format_exc())
            return

        self.running = True
        delay = self.stream.latency[-1] + block_time + crossfade_time + 0.01
        if i_noise:
            delay += min(crossfade_time, 0.04)
        self.delay_ms = int(round(delay * 1000))
        emit({
            "type": "started",
            "samplerate": self.samplerate,
            "channels": self.channels,
            "delay_ms": self.delay_ms,
            "device": str(self.config.device),
        })

    def stop(self, quiet=False):
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        was_running = self.running
        self.running = False
        if was_running and not quiet:
            emit({"type": "stopped"})

    # ------------------------------------------------------------ 热更新
    def apply_params(self, patch):
        if not self.running or not self.params:
            return
        for key in HOT_KEYS:
            if key in patch:
                self.params[key] = patch[key]
        if "function" in patch:
            self.function = patch["function"]
        if self.rvc is not None:
            if "pitch" in patch:
                self.rvc.change_key(int(patch["pitch"]))
            if "formant" in patch:
                self.rvc.change_formant(float(patch["formant"]))
            if "index_rate" in patch:
                self.rvc.change_index_rate(float(patch["index_rate"]))

    # ------------------------------------------------------------ 音频回调
    def audio_callback(self, indata, outdata, frames, times, status):
        start_time = time.perf_counter()
        params = self.params
        indata = self.librosa.to_mono(indata.T)

        # 响应门限：低于阈值的帧直接置零（防止环境噪声触发变声）
        if params.get("threhold", -60) > -60:
            indata = self.np.append(self.rms_buffer, indata)
            rms = self.librosa.feature.rms(
                y=indata, frame_length=4 * self.zc, hop_length=self.zc)[:, 2:]
            self.rms_buffer[:] = indata[-4 * self.zc:]
            indata = indata[2 * self.zc - self.zc // 2:]
            db_threhold = (
                self.librosa.amplitude_to_db(rms, ref=1.0)[0]
                < params.get("threhold", -60)
            )
            for i in range(db_threhold.shape[0]):
                if db_threhold[i]:
                    indata[i * self.zc:(i + 1) * self.zc] = 0
            indata = indata[self.zc // 2:]

        self.input_wav[:-self.block_frame] = self.input_wav[self.block_frame:].clone()
        self.input_wav[-indata.shape[0]:] = self.torch.from_numpy(indata).to(
            self.config.device)
        self.input_wav_res[:-self.block_frame_16k] = self.input_wav_res[
            self.block_frame_16k:].clone()

        # 输入降噪 + 重采样到 16k
        if params.get("I_noise_reduce", False):
            self.input_wav_denoise[:-self.block_frame] = self.input_wav_denoise[
                self.block_frame:].clone()
            input_wav = self.input_wav[-self.sola_buffer_frame - self.block_frame:]
            input_wav = self.tg(
                input_wav.unsqueeze(0), self.input_wav.unsqueeze(0)).squeeze(0)
            input_wav[:self.sola_buffer_frame] *= self.fade_in_window
            input_wav[:self.sola_buffer_frame] += self.nr_buffer * self.fade_out_window
            self.input_wav_denoise[-self.block_frame:] = input_wav[:self.block_frame]
            self.nr_buffer[:] = input_wav[self.block_frame:]
            self.input_wav_res[-self.block_frame_16k - 160:] = self.resampler(
                self.input_wav_denoise[-self.block_frame - 2 * self.zc:])[160:]
        else:
            self.input_wav_res[-160 * (indata.shape[0] // self.zc + 1):] = (
                self.resampler(self.input_wav[-indata.shape[0] - 2 * self.zc:])[160:]
            )

        # 推理（输出变声）或直接监听
        if self.function == "vc":
            infer_wav = self.rvc.infer(
                self.input_wav_res,
                self.block_frame_16k,
                self.skip_head,
                self.return_length,
                params.get("f0method", "fcpe"),
            )
            if self.resampler2 is not None:
                infer_wav = self.resampler2(infer_wav)
        elif params.get("I_noise_reduce", False):
            infer_wav = self.input_wav_denoise[self.extra_frame:].clone()
        else:
            infer_wav = self.input_wav[self.extra_frame:].clone()

        # 输出降噪
        if params.get("O_noise_reduce", False) and self.function == "vc":
            self.output_buffer[:-self.block_frame] = self.output_buffer[
                self.block_frame:].clone()
            self.output_buffer[-self.block_frame:] = infer_wav[-self.block_frame:]
            infer_wav = self.tg(
                infer_wav.unsqueeze(0), self.output_buffer.unsqueeze(0)).squeeze(0)

        # 响度包络对齐
        if params.get("rms_mix_rate", 0) < 1 and self.function == "vc":
            if params.get("I_noise_reduce", False):
                input_wav = self.input_wav_denoise[self.extra_frame:]
            else:
                input_wav = self.input_wav[self.extra_frame:]
            rms1 = self.librosa.feature.rms(
                y=input_wav[:infer_wav.shape[0]].cpu().numpy(),
                frame_length=4 * self.zc, hop_length=self.zc,
            )
            rms1 = self.torch.from_numpy(rms1).to(self.config.device)
            rms1 = self.F.interpolate(
                rms1.unsqueeze(0), size=infer_wav.shape[0] + 1,
                mode="linear", align_corners=True,
            )[0, 0, :-1]
            rms2 = self.librosa.feature.rms(
                y=infer_wav[:].cpu().numpy(),
                frame_length=4 * self.zc, hop_length=self.zc,
            )
            rms2 = self.torch.from_numpy(rms2).to(self.config.device)
            rms2 = self.F.interpolate(
                rms2.unsqueeze(0), size=infer_wav.shape[0] + 1,
                mode="linear", align_corners=True,
            )[0, 0, :-1]
            rms2 = self.torch.max(rms2, self.torch.zeros_like(rms2) + 1e-3)
            infer_wav *= self.torch.pow(
                rms1 / rms2,
                self.torch.tensor(1 - params.get("rms_mix_rate", 0)),
            )

        # SOLA 拼接（来自 DDSP-SVC）
        conv_input = infer_wav[
            None, None, :self.sola_buffer_frame + self.sola_search_frame]
        cor_nom = self.F.conv1d(conv_input, self.sola_buffer[None, None, :])
        cor_den = self.torch.sqrt(
            self.F.conv1d(
                conv_input ** 2,
                self.torch.ones(1, 1, self.sola_buffer_frame,
                                device=self.config.device),
            ) + 1e-8
        )
        sola_offset = self.torch.argmax(cor_nom[0, 0] / cor_den[0, 0])
        infer_wav = infer_wav[sola_offset:]
        if "privateuseone" in str(self.config.device) or not params.get("use_pv", False):
            infer_wav[:self.sola_buffer_frame] *= self.fade_in_window
            infer_wav[:self.sola_buffer_frame] += self.sola_buffer * self.fade_out_window
        else:
            infer_wav[:self.sola_buffer_frame] = phase_vocoder(
                self.sola_buffer,
                infer_wav[:self.sola_buffer_frame],
                self.fade_out_window,
                self.fade_in_window,
            )
        self.sola_buffer[:] = infer_wav[
            self.block_frame:self.block_frame + self.sola_buffer_frame]
        outdata[:] = (
            infer_wav[:self.block_frame]
            .repeat(self.channels, 1)
            .t()
            .cpu()
            .numpy()
        )

        # 推理耗时 → 界面（限流约 1 次/秒，避免刷屏）
        self.infer_ms = int((time.perf_counter() - start_time) * 1000)
        now = time.time()
        if now - self._last_metric >= 1.0:
            self._last_metric = now
            emit({"type": "metrics", "infer_ms": self.infer_ms,
                  "delay_ms": self.delay_ms})
            # 算不过来就说出来：这是"卡/爆音"的直接原因，别让人干猜
            if (self.budget_ms and self.infer_ms > self.budget_ms
                    and now - self._last_slow_warn >= 15.0):
                self._last_slow_warn = now
                emit_log(
                    "推理 %s ms > 块长 %s ms，CPU 跟不上，声音会卡顿/爆音。"
                    "调大「采样长度」（如 0.5~1.0 秒）、调小「额外推理时长」"
                    "（如 1.0 秒）、关掉输入/输出降噪，或把「检索特征占比」降到 0。"
                    % (self.infer_ms, self.budget_ms)
                )


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--rvc-root", required=True)
    args = parser.parse_args()

    # 切到 RVC 根目录 + 铺好 weight_root 等环境变量
    #（内部还会顺手清掉 sys.argv，否则 RVC 的 Config 看到 --rvc-root 会 sys.exit(2)）
    rvc_boot.setup(args.rvc_root)
    os.environ.setdefault("OMP_NUM_THREADS", "4")

    # 之后所有 print（含 rtrvc.py 的 printt）都会走这里 → 日志事件
    sys.stdout = _LogRedirect()

    core = RealtimeCore()
    emit({"type": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except ValueError:
            continue
        action = cmd.get("cmd")
        emit_log("收到命令: %s" % action)
        try:
            if action == "list_devices":
                core.list_devices(cmd.get("hostapi"))
            elif action == "start":
                core.start(cmd.get("params") or {})
            elif action == "stop":
                core.stop()
            elif action == "set":
                core.apply_params(cmd.get("params") or {})
        except Exception:
            emit_error("命令执行失败：%s" % action, traceback.format_exc())
        if action == "exit":
            break

    core.stop(quiet=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        emit_error("worker 崩溃", traceback.format_exc())
        sys.exit(1)
