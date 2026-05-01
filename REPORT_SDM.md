# Simple Download Manager (SDM) - Project Report

## 1. Project Overview

This project implements a **Simple Download Manager** with support for segmented downloads, pause/resume/cancel controls, retry logic, and live monitoring through a web dashboard.

Main objective:
- Build a distributed/concurrent download system that splits files into multiple segments and downloads them in parallel threads.

## 2. Features Implemented

- Download files from URL
- Multi-threaded segmented downloads using HTTP `Range`
- Pause / Resume / Cancel controls
- Automatic retry per segment with exponential backoff
- Live dashboard metrics:
  - progress percentage
  - download speed
  - ETA
  - per-task status
- Persistent task history in `history.json`
- Optional per-task bandwidth limiting

## 3. System Architecture

The implementation uses a layered architecture:

1. **UI Layer**
- Flask + HTML/CSS/JS dashboard
- User creates and controls download tasks via browser

2. **Download Manager Layer**
- `DownloadManager` tracks active tasks
- routes start/pause/resume/cancel operations
- persists terminal states (completed/failed/cancelled)

3. **Task Controller + Segment Workers**
- `DownloadTask` prepares metadata with HTTP HEAD
- builds byte-range segments
- starts one worker thread per segment
- each worker writes to a `.part` temporary file

4. **File Assembler Layer**
- merges `.part` files in order into final file
- cleans temporary files on completion/cancel

5. **Persistence Layer**
- stores recent finished tasks in `history.json`

## 4. Communication and Concurrency

### 4.1 HTTP Communication

- `HEAD` request is used to fetch metadata (e.g., `Content-Length`, `Accept-Ranges`)
- `GET` requests with `Range` headers are used for segmented transfer

### 4.2 Concurrency Model

- Python `threading` is used for parallel segment downloads
- Shared task state is synchronized with lock/event primitives:
  - lock for safe state transitions
  - pause event for cooperative pausing
  - cancel event for immediate stop behavior

### 4.3 Retry Strategy

- Segment workers retry transient failures
- backoff delay increases exponentially (bounded)
- task fails if retry budget is exceeded for any segment

## 5. API Summary

- `GET /api/tasks` : list active tasks
- `GET /api/tasks/<id>` : get one task snapshot
- `POST /api/tasks` : create a new task
- `POST /api/tasks/<id>/start` : start task
- `POST /api/tasks/<id>/pause` : pause task
- `POST /api/tasks/<id>/resume` : resume task
- `POST /api/tasks/<id>/cancel` : cancel task
- `GET /api/history` : list persisted finished tasks

## 6. Performance Discussion (Single vs Multi-Segment)

Expected behavior:
- `segments = 1` uses one stream and lower CPU/thread overhead
- `segments = 4` or `8` can improve throughput when server/network allows parallel ranges
- speedup depends on server-side throttling and network conditions

Recommended experiment:
1. Download the same file with `segments=1`
2. Repeat with `segments=4` and `segments=8`
3. Record completion time and average speed
4. Compare gains and discuss bottlenecks

### Result Table (fill during demo)

| File URL | Size | Segments | Avg Speed | Completion Time |
|---|---:|---:|---:|---:|
|  |  | 1 |  |  |
|  |  | 4 |  |  |
|  |  | 8 |  |  |

## 7. Known Limitations

- If `Content-Length` is missing, segmented preparation cannot proceed
- If `Accept-Ranges` is unavailable, task falls back to a single segment
- Resume support is scoped to temporary segment files available in `downloads/`
- TLS/certificate issues on some hosts can cause HTTPS failures

## 8. Validation Scenario

A successful validation run should demonstrate:
- Task creation from dashboard
- Active progress updates (speed, ETA, percentage)
- Pause and resume during transfer
- Completion and merge into final file
- Entry persisted into history

## 9. Conclusion

The project delivers a practical multi-threaded download manager with monitoring and control features suitable for a distributed systems assignment. It demonstrates key concepts: concurrent workers, REST coordination, range-based data partitioning, retry resilience, and persistent task state.

## 10. How to Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python dashboard.py
```

Open: `http://127.0.0.1:5000`
