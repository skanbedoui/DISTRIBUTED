import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests


CHUNK_SIZE = 128 * 1024


@dataclass
class SegmentState:
    index: int
    start: int
    end: int
    downloaded: int = 0
    done: bool = False
    retries: int = 0


@dataclass
class DownloadTask:
    url: str
    output_dir: Path
    segments: int = 4
    max_retries: int = 3
    timeout: int = 15
    bandwidth_limit_kbps: Optional[int] = None
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    filename: Optional[str] = None
    file_size: int = 0
    status: str = "created"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    supports_ranges: bool = False

    _segments_state: List[SegmentState] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _manager_thread: Optional[threading.Thread] = None
    _workers: List[threading.Thread] = field(default_factory=list)
    _bytes_at_last_tick: int = 0
    _last_tick_time: float = field(default_factory=time.time)
    _speed_bps: float = 0.0

    def __post_init__(self) -> None:
        self._pause_event.set()

    @property
    def output_path(self) -> Path:
        if not self.filename:
            return self.output_dir / f"{self.task_id}.bin"
        return self.output_dir / self.filename

    def segment_path(self, index: int) -> Path:
        return self.output_dir / f".{self.task_id}.part{index}"

    def start(self) -> None:
        with self._lock:
            if self.status in {"downloading", "completed"}:
                return
            if self.status == "paused":
                self.status = "downloading"
                self._pause_event.set()
                return
            self.status = "starting"
            self.error_message = None
            self.started_at = time.time()
            self._cancel_event.clear()
            self._pause_event.set()
            self._manager_thread = threading.Thread(target=self._run, daemon=True)
            self._manager_thread.start()

    def pause(self) -> None:
        with self._lock:
            if self.status == "downloading":
                self.status = "paused"
                self._pause_event.clear()

    def resume(self) -> None:
        with self._lock:
            if self.status == "paused":
                self.status = "downloading"
                self._pause_event.set()

    def cancel(self) -> None:
        with self._lock:
            if self.status in {"completed", "failed", "cancelled"}:
                return
            self.status = "cancelled"
            self._cancel_event.set()
            self._pause_event.set()

    def total_downloaded(self) -> int:
        return sum(s.downloaded for s in self._segments_state)

    def percentage(self) -> float:
        if self.file_size <= 0:
            return 0.0
        return min(100.0, (self.total_downloaded() / self.file_size) * 100.0)

    def eta_seconds(self) -> Optional[float]:
        if self._speed_bps <= 1:
            return None
        remaining = max(0, self.file_size - self.total_downloaded())
        return remaining / self._speed_bps

    def speed_bps(self) -> float:
        return self._speed_bps

    def snapshot(self) -> Dict:
        now = time.time()
        elapsed = (now - self.started_at) if self.started_at else 0.0
        segment_items = []
        for s in self._segments_state:
            length = (s.end - s.start) + 1 if s.end >= s.start else 0
            segment_items.append(
                {
                    "index": s.index,
                    "start": s.start,
                    "end": s.end,
                    "downloaded": s.downloaded,
                    "length": length,
                    "done": s.done,
                    "retries": s.retries,
                }
            )

        return {
            "id": self.task_id,
            "url": self.url,
            "filename": self.filename,
            "output_path": str(self.output_path),
            "file_size": self.file_size,
            "downloaded": self.total_downloaded(),
            "progress_pct": round(self.percentage(), 2),
            "speed_bps": int(self.speed_bps()),
            "eta_seconds": self.eta_seconds(),
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": elapsed,
            "max_retries": self.max_retries,
            "segments": self.segments,
            "supports_ranges": self.supports_ranges,
            "error_message": self.error_message,
            "segment_states": segment_items,
        }

    def _run(self) -> None:
        try:
            self._prepare()
            with self._lock:
                if self.status not in {"cancelled", "paused"}:
                    self.status = "downloading"

            self._workers = [
                threading.Thread(target=self._download_segment, args=(s,), daemon=True)
                for s in self._segments_state
            ]
            for worker in self._workers:
                worker.start()

            while True:
                time.sleep(0.5)
                self._refresh_speed()

                if self._cancel_event.is_set():
                    self._cleanup_parts()
                    return

                all_done = all(s.done for s in self._segments_state)
                if all_done:
                    break

                if any((s.retries > self.max_retries) for s in self._segments_state):
                    raise RuntimeError("Segment retry budget exceeded")

            self._merge_segments()
            self._cleanup_parts()
            with self._lock:
                self.status = "completed"
                self.completed_at = time.time()
        except Exception as exc:
            if self.status == "cancelled":
                return
            with self._lock:
                self.status = "failed"
                self.error_message = str(exc)

    def _prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        response = requests.head(self.url, allow_redirects=True, timeout=self.timeout)
        response.raise_for_status()

        cl = response.headers.get("Content-Length")
        if not cl:
            raise ValueError("Missing Content-Length header; cannot segment file")

        self.file_size = int(cl)
        self.supports_ranges = "bytes" in response.headers.get("Accept-Ranges", "").lower()
        if self.file_size <= 0:
            raise ValueError("Invalid file size")

        self.filename = self._resolve_filename(response)

        self._segments_state = self._build_segments()
        self._load_resume_progress()
        self._last_tick_time = time.time()
        self._bytes_at_last_tick = self.total_downloaded()

    def _resolve_filename(self, response: requests.Response) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        if "filename=" in disposition:
            value = disposition.split("filename=")[-1].strip('"\' ')
            if value:
                return value

        parsed = urlparse(self.url)
        name = os.path.basename(parsed.path)
        if name:
            return name
        return f"download-{self.task_id}.bin"

    def _build_segments(self) -> List[SegmentState]:
        if not self.supports_ranges:
            return [SegmentState(index=0, start=0, end=self.file_size - 1)]

        segment_count = max(1, min(self.segments, 32))
        chunk = math.ceil(self.file_size / segment_count)
        states: List[SegmentState] = []
        for i in range(segment_count):
            start = i * chunk
            end = min((i + 1) * chunk - 1, self.file_size - 1)
            if start > end:
                continue
            states.append(SegmentState(index=i, start=start, end=end))
        return states

    def _load_resume_progress(self) -> None:
        for seg in self._segments_state:
            part = self.segment_path(seg.index)
            if part.exists():
                seg.downloaded = min(part.stat().st_size, (seg.end - seg.start) + 1)
                if seg.downloaded >= (seg.end - seg.start) + 1:
                    seg.done = True

    def _download_segment(self, segment: SegmentState) -> None:
        while not segment.done and not self._cancel_event.is_set():
            self._pause_event.wait()
            if self._cancel_event.is_set():
                return

            start_byte = segment.start + segment.downloaded
            if start_byte > segment.end:
                segment.done = True
                return

            headers = {"Range": f"bytes={start_byte}-{segment.end}"}
            if not self.supports_ranges:
                headers = {}

            try:
                with requests.get(
                    self.url,
                    headers=headers,
                    stream=True,
                    timeout=self.timeout,
                ) as response:
                    response.raise_for_status()
                    part_file = self.segment_path(segment.index)
                    with open(part_file, "ab") as output:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if not chunk:
                                continue

                            while not self._pause_event.is_set() and not self._cancel_event.is_set():
                                time.sleep(0.1)

                            if self._cancel_event.is_set():
                                return

                            output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())

                            segment.downloaded += len(chunk)
                            if self.bandwidth_limit_kbps:
                                bytes_per_sec = self.bandwidth_limit_kbps * 1024
                                sleep_time = len(chunk) / bytes_per_sec
                                if sleep_time > 0:
                                    time.sleep(sleep_time)

                            if segment.downloaded >= (segment.end - segment.start) + 1:
                                segment.done = True
                                break

                if not segment.done:
                    if segment.downloaded >= (segment.end - segment.start) + 1:
                        segment.done = True
            except Exception:
                segment.retries += 1
                if segment.retries > self.max_retries:
                    return
                time.sleep(min(2 ** segment.retries, 8))

    def _merge_segments(self) -> None:
        with open(self.output_path, "wb") as out_file:
            for seg in sorted(self._segments_state, key=lambda s: s.index):
                part = self.segment_path(seg.index)
                if not part.exists():
                    raise FileNotFoundError(f"Missing segment file {part}")
                with open(part, "rb") as in_file:
                    while True:
                        block = in_file.read(CHUNK_SIZE)
                        if not block:
                            break
                        out_file.write(block)

    def _cleanup_parts(self) -> None:
        for seg in self._segments_state:
            part = self.segment_path(seg.index)
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass

    def _refresh_speed(self) -> None:
        now = time.time()
        dt = now - self._last_tick_time
        if dt <= 0:
            return
        total = self.total_downloaded()
        self._speed_bps = max(0.0, (total - self._bytes_at_last_tick) / dt)
        self._bytes_at_last_tick = total
        self._last_tick_time = now


class DownloadManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.download_dir = base_dir / "downloads"
        self.history_path = base_dir / "history.json"
        self._tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.Lock()
        self._history_cache: List[Dict] = self._load_history()

        self.download_dir.mkdir(parents=True, exist_ok=True)

    def add_download(
        self,
        url: str,
        segments: int = 4,
        max_retries: int = 3,
        bandwidth_limit_kbps: Optional[int] = None,
        auto_start: bool = True,
    ) -> Dict:
        task = DownloadTask(
            url=url,
            output_dir=self.download_dir,
            segments=segments,
            max_retries=max_retries,
            bandwidth_limit_kbps=bandwidth_limit_kbps,
        )

        with self._lock:
            self._tasks[task.task_id] = task

        if auto_start:
            task.start()

        return task.snapshot()

    def start_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.start()
        return True

    def pause_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.pause()
        return True

    def resume_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.resume()
        return True

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.cancel()
        return True

    def get_task(self, task_id: str) -> Optional[Dict]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        self._persist_if_finished(task)
        return task.snapshot()

    def list_tasks(self) -> List[Dict]:
        snapshots = []
        for task in list(self._tasks.values()):
            self._persist_if_finished(task)
            snapshots.append(task.snapshot())
        snapshots.sort(key=lambda x: x["created_at"], reverse=True)
        return snapshots

    def history(self) -> List[Dict]:
        return list(self._history_cache)

    def _persist_if_finished(self, task: DownloadTask) -> None:
        if task.status not in {"completed", "failed", "cancelled"}:
            return

        record = task.snapshot()
        if any(existing.get("id") == record.get("id") for existing in self._history_cache):
            return

        self._history_cache.insert(0, record)
        self._history_cache = self._history_cache[:200]
        with open(self.history_path, "w", encoding="utf-8") as file:
            json.dump(self._history_cache, file, indent=2)

    def _load_history(self) -> List[Dict]:
        if not self.history_path.exists():
            return []
        try:
            with open(self.history_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []
