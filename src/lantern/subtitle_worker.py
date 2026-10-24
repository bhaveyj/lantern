"""Short-lived local inference process; imported only when a job is requested."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from .captions import Word, write_srt
from .speech_models import MODELS, ensure_model


def write_state(path: Path, **state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.02)


def watch_parent(parent_pid: int):
    if os.name != "nt" or not parent_pid:
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, parent_pid)
    if not handle:
        os._exit(1)
    def wait():
        try:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel.CloseHandle(handle)
        os._exit(1)
    threading.Thread(target=wait, name="lantern-parent-watch", daemon=True).start()


def transcribe(request: dict, state: Path):
    model_name, threads = request["model"], int(request["threads"])
    language = request.get("language")
    if model_name not in MODELS or threads not in (2, 4) or language not in (None, "en", "hi"):
        raise ValueError("Unsupported local transcription settings.")
    for setting in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[setting] = str(threads)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HOME"] = str(Path(request["models"]) / "hub")

    # No neural runtime is imported by the GUI or its network loop.
    from faster_whisper import WhisperModel
    write_state(state, phase="Loading local speech model…", percent=None)
    model_path = ensure_model(model_name, Path(request["models"]), bool(request.get("allow_download")), lambda phase, percent: write_state(state, phase=phase, percent=percent))
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1, local_files_only=True)
    write_state(state, phase="Recognizing speech on this PC…", percent=0)
    segments, info = model.transcribe(
        request["audio"], language=language, task="transcribe", word_timestamps=True,
        vad_filter=True, beam_size=3, condition_on_previous_text=False,
    )
    duration = max(float(request["duration"]), info.duration, 1)

    def words():
        for segment in segments:
            for word in segment.words or []:
                yield Word(word.start, word.end, word.word)
            write_state(state, phase="Recognizing speech on this PC…", percent=min(99, segment.end / duration * 100))

    count = write_srt(words(), Path(request["output"]))
    write_state(state, complete=True, language=info.language, cues=count, percent=100)


def main(request_path: Path) -> int:
    state = request_path.parent / "state.json"
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        watch_parent(int(request.get("parent_pid", 0)))
        transcribe(request, state)
        return 0
    except Exception as error:
        write_state(state, error=str(error)[:1200])
        return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
