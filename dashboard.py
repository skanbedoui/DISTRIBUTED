from pathlib import Path

from flask import Flask, jsonify, render_template, request

from sdm import DownloadManager


app = Flask(__name__)
manager = DownloadManager(base_dir=Path(__file__).parent)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/tasks")
def list_tasks():
    return jsonify(manager.list_tasks())


@app.get("/api/tasks/<task_id>")
def get_task(task_id: str):
    task = manager.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.post("/api/tasks")
def add_task():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    segments = int(payload.get("segments", 4))
    max_retries = int(payload.get("max_retries", 3))
    bandwidth_limit_kbps = payload.get("bandwidth_limit_kbps")
    auto_start = bool(payload.get("auto_start", True))

    if bandwidth_limit_kbps in ("", None):
        bandwidth_limit_kbps = None
    else:
        bandwidth_limit_kbps = int(bandwidth_limit_kbps)

    if segments < 1 or segments > 32:
        return jsonify({"error": "segments must be between 1 and 32"}), 400
    if max_retries < 0 or max_retries > 20:
        return jsonify({"error": "max_retries must be between 0 and 20"}), 400

    try:
        task = manager.add_download(
            url=url,
            segments=segments,
            max_retries=max_retries,
            bandwidth_limit_kbps=bandwidth_limit_kbps,
            auto_start=auto_start,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(task), 201


@app.post("/api/tasks/<task_id>/start")
def start_task(task_id: str):
    if not manager.start_task(task_id):
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"status": "ok"})


@app.post("/api/tasks/<task_id>/pause")
def pause_task(task_id: str):
    if not manager.pause_task(task_id):
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"status": "ok"})


@app.post("/api/tasks/<task_id>/resume")
def resume_task(task_id: str):
    if not manager.resume_task(task_id):
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"status": "ok"})


@app.post("/api/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    if not manager.cancel_task(task_id):
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"status": "ok"})


@app.get("/api/history")
def history():
    return jsonify(manager.history())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
