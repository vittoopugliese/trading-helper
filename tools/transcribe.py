"""Transcribe course videos (or any audio/video) to timestamped text.

Two engines:
  gpu  whisper.cpp + Vulkan (AMD RX 590). Needs a working GPU driver. Uses ~2 CPU threads.
  cpu  faster-whisper, int8, with a capped number of threads so the PC stays usable.
  auto (default) uses gpu when whisper.cpp actually sees a GPU, else cpu.

Built for unattended runs: skips files already transcribed (resume anytime), runs at
below-normal priority, keeps Windows from sleeping while it works, logs progress + ETA
to knowledge_raw/transcribe.log and keeps going if one file fails.

Output (gitignored, course material stays local):
  knowledge_raw/transcripts/<source folder>/<relative path>.txt   "[mm:ss] text" lines
  knowledge_raw/transcripts/<source folder>/<relative path>.json  segments

Runs in .venv-transcribe (Python 3.12):
  .venv-transcribe/Scripts/python.exe tools/transcribe.py --sources-file tools/transcribe_sources.txt
  .venv-transcribe/Scripts/python.exe tools/transcribe.py "D:/otro curso" --engine cpu --cpu-threads 4
  .venv-transcribe/Scripts/python.exe tools/transcribe.py --check      # which engine would run, no work
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "knowledge_raw" / "transcripts"
LOG_PATH = ROOT / "knowledge_raw" / "transcribe.log"
WHISPER_CPP = ROOT.parent / "whisper.cpp"
WHISPER_CLI = WHISPER_CPP / "build" / "bin" / "whisper-cli.exe"
GGML_MODEL = WHISPER_CPP / "models" / "ggml-large-v3-turbo.bin"
VAD_MODEL = WHISPER_CPP / "models" / "ggml-silero-v6.2.0.bin"
MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus"}
SR = 16000

# Vocabulary hint so Whisper spells trading jargon correctly.
INITIAL_PROMPT = (
    "Clase de trading en español. Términos: Smart Money, BOS, CHOCH, order block, POI, "
    "cluster, liquidez, imbalance, mitigación, oferta y demanda, perfil de volumen, POC, "
    "VAH, VAL, Elliott Waves, onda 1, onda 2, onda 3, onda 4, onda 5, ABC, zig zag, flat, "
    "expanding flat, running flat, diagonal, triángulo, WXY, WXYXZ, nested 1-2, "
    "Fibonacci, retroceso 0.618, extensión 1.618, canales Kennedy, base channel, "
    "trend channel, end channel, Wyckoff, spring, upthrust, order flow, delta, CVD, "
    "footprint, open interest, funding, TradingView, Bitcoin, stop loss, take profit."
)

BELOW_NORMAL = 0x00004000
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def be_polite() -> None:
    """Below-normal priority (children inherit it) and no sleep while running."""
    if os.name != "nt":
        return
    k32 = ctypes.windll.kernel32
    k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
    k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


# --------------------------------------------------------------------------- audio


def load_audio(path: Path, limit_seconds: float = 0) -> np.ndarray:
    """Decode to 16 kHz mono float32. Done here because faster-whisper's own
    decoder breaks with PyAV 19 (only version with wheels for this setup)."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
    chunks, total = [], 0
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.size
            if limit_seconds and total >= limit_seconds * SR:
                break
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    return audio[: int(limit_seconds * SR)] if limit_seconds else audio


def write_wav(audio: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


# --------------------------------------------------------------------------- engines


def gpu_status() -> tuple[bool, str]:
    """Run whisper.cpp on 1 s of silence and read which backend it picked."""
    if not WHISPER_CLI.exists() or not GGML_MODEL.exists():
        return False, f"whisper.cpp no compilado o modelo faltante ({WHISPER_CLI.name})"
    tmp = OUT_DIR / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    wav = tmp / "silence.wav"
    write_wav(np.zeros(SR, dtype=np.float32), wav)
    r = subprocess.run([str(WHISPER_CLI), "-m", str(GGML_MODEL), "-f", str(wav), "-t", "1"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=BELOW_NORMAL if os.name == "nt" else 0)
    out = r.stdout + r.stderr
    gpu_lines = [ln.strip() for ln in out.splitlines() if "whisper_backend_init_gpu" in ln or "ggml_vulkan" in ln]
    if "no GPU found" in out or not gpu_lines:
        return False, "whisper.cpp no ve la GPU (revisar driver AMD / Vulkan): " + " | ".join(gpu_lines[:2])
    return True, " | ".join(gpu_lines[:2])


class GpuEngine:
    name = "gpu"

    def __init__(self, threads: int = 2):
        self.threads = threads

    def transcribe(self, media: Path, limit_seconds: float) -> list[dict]:
        tmp = OUT_DIR / "_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        # ASCII temp names: whisper-cli on Windows chokes on accented paths.
        key = hashlib.md5(str(media).encode()).hexdigest()[:12]
        wav, base = tmp / f"{key}.wav", tmp / key
        write_wav(load_audio(media, limit_seconds), wav)
        try:
            cmd = [str(WHISPER_CLI), "-m", str(GGML_MODEL), "-f", str(wav), "-l", "es",
                   "-t", str(self.threads), "-bs", "5", "-mc", "0", "--prompt", INITIAL_PROMPT,
                   "-oj", "-of", str(base), "-np"]
            if VAD_MODEL.exists():
                cmd += ["--vad", "-vm", str(VAD_MODEL)]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               creationflags=BELOW_NORMAL if os.name == "nt" else 0)
            js = base.with_suffix(".json")
            if r.returncode != 0 or not js.exists():
                raise RuntimeError(f"whisper-cli rc={r.returncode}: {(r.stderr or r.stdout)[-300:]}")
            data = json.loads(js.read_text(encoding="utf-8", errors="replace"))
            js.unlink()
        finally:
            wav.unlink(missing_ok=True)
        return [{"start": round(s["offsets"]["from"] / 1000, 2), "end": round(s["offsets"]["to"] / 1000, 2),
                 "text": s["text"].strip()} for s in data.get("transcription", []) if s.get("text", "").strip()]


class CpuEngine:
    name = "cpu"

    def __init__(self, threads: int, model: str = "large-v3-turbo"):
        from faster_whisper import WhisperModel  # heavy import, only when used
        self.threads = threads
        self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, media: Path, limit_seconds: float) -> list[dict]:
        segments, _ = self.model.transcribe(
            load_audio(media, limit_seconds), language="es", beam_size=5, vad_filter=True,
            initial_prompt=INITIAL_PROMPT,
            condition_on_previous_text=False,  # avoids repetition loops on long files
        )
        return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]


# --------------------------------------------------------------------------- jobs


def read_sources(args: argparse.Namespace) -> list[Path]:
    srcs = list(args.sources)
    if args.sources_file:
        for line in Path(args.sources_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                srcs.append(Path(line))
    return srcs


def collect_jobs(sources: list[Path]) -> list[tuple[Path, Path]]:
    """(media file, output .txt path) for every media file under each source."""
    jobs = []
    for src in sources:
        if not src.exists():
            log(f"AVISO fuente no existe: {src}")
            continue
        if src.is_file():
            jobs.append((src, OUT_DIR / src.parent.name / f"{src.stem}.txt"))
            continue
        for f in sorted(src.rglob("*")):
            if f.suffix.lower() in MEDIA_EXT:
                rel = f.relative_to(src).with_suffix(".txt")
                jobs.append((f, OUT_DIR / src.name / rel))
    return jobs


def duration_s(path: Path) -> float:
    try:
        with av.open(str(path)) as c:
            return (c.duration or 0) / 1e6
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", type=Path, help="folders or files")
    ap.add_argument("--sources-file", help="text file, one folder per line (# comments)")
    ap.add_argument("--engine", choices=["auto", "gpu", "cpu"], default="auto")
    ap.add_argument("--cpu-threads", type=int, default=4, help="cpu engine threads (of 16). 4 = ~25%% CPU")
    ap.add_argument("--limit-seconds", type=float, default=0, help="test: only first N seconds, output to _test/")
    ap.add_argument("--check", action="store_true", help="report engine + pending work, transcribe nothing")
    args = ap.parse_args()

    be_polite()
    jobs = collect_jobs(read_sources(args))
    if args.limit_seconds:
        jobs = [(m, OUT_DIR / "_test" / t.name) for m, t in jobs[:1]]
    pending = [(m, t) for m, t in jobs if not t.exists()]
    pending_audio = sum(duration_s(m) for m, _ in pending)

    ok, detail = gpu_status() if args.engine in ("auto", "gpu") else (False, "cpu forzado")
    if args.engine == "gpu" and not ok:
        log(f"ERROR --engine gpu pero {detail}")
        return 2
    engine_name = "gpu" if ok else "cpu"
    log(f"motor={engine_name} ({detail}) | archivos={len(jobs)} pendientes={len(pending)} "
        f"audio pendiente={pending_audio / 3600:.1f} h")
    if args.check or not pending:
        return 0

    engine = GpuEngine() if ok else CpuEngine(args.cpu_threads)
    done_audio, t_start = 0.0, time.time()
    for i, (media, txt) in enumerate(pending, 1):
        dur = args.limit_seconds or duration_s(media)
        t0 = time.time()
        try:
            segs = engine.transcribe(media, args.limit_seconds)
        except Exception as e:  # keep the overnight run alive
            log(f"[{i}/{len(pending)}] FALLO {media.name}: {e}")
            continue
        txt.parent.mkdir(parents=True, exist_ok=True)
        header = [f"# {media.stem}", f"# fuente: {media.name} | motor: {engine.name} | duracion: {fmt(dur)}", ""]
        tmp = txt.with_suffix(".tmp")
        tmp.write_text("\n".join(header + [f"[{fmt(s['start'])}] {s['text']}" for s in segs]) + "\n",
                       encoding="utf-8")
        txt.with_suffix(".json").write_text(
            json.dumps({"source": str(media), "engine": engine.name, "duration": dur, "segments": segs},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(txt)  # .txt appears only when complete, so resume never skips a half file
        el = time.time() - t0
        done_audio += dur
        speed = done_audio / max(time.time() - t_start, 1)
        eta = timedelta(seconds=int((pending_audio - done_audio) / speed)) if speed else "?"
        log(f"[{i}/{len(pending)}] {media.name}: {dur / 60:.1f} min en {el / 60:.1f} min "
            f"({dur / max(el, 1):.2f}x) | ETA resto {eta}")
    log("FIN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
