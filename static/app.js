const tasksEl = document.getElementById("tasks");
const historyEl = document.getElementById("history");
const template = document.getElementById("task-template");
const form = document.getElementById("download-form");
const msgEl = document.getElementById("form-message");
const refreshBtn = document.getElementById("refresh-btn");

function formatBytes(bytes) {
  if (!bytes || bytes < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let value = bytes;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}

function formatEta(seconds) {
  if (!seconds || !Number.isFinite(seconds)) return "n/a";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m === 0) return `${r}s`;
  return `${m}m ${r}s`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function renderTask(task) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector(".filename").textContent = task.filename || "Pending metadata...";
  node.querySelector(".url").textContent = task.url;
  node.querySelector(".status").textContent = task.status;
  node.querySelector(".progress-bar").style.width = `${task.progress_pct || 0}%`;

  const speed = formatBytes(task.speed_bps || 0);
  const downloaded = formatBytes(task.downloaded || 0);
  const total = formatBytes(task.file_size || 0);
  const eta = formatEta(task.eta_seconds);

  node.querySelector(".stats").textContent =
    `Progress ${task.progress_pct || 0}% | ${downloaded} / ${total} | Speed ${speed}/s | ETA ${eta} | Segments ${task.segments}`;

  const controls = node.querySelectorAll("button[data-action]");
  controls.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      try {
        await api(`/api/tasks/${task.id}/${action}`, { method: "POST" });
        msgEl.textContent = "Command sent.";
      } catch (error) {
        alert(error.message);
      }
    });
  });

  const status = task.status;
  const canStart = ["created", "starting"].includes(status);
  const canPause = status === "downloading";
  const canResume = status === "paused";
  const canCancel = !["completed", "failed", "cancelled"].includes(status);

  node.querySelector("button[data-action='start']").disabled = !canStart;
  node.querySelector("button[data-action='pause']").disabled = !canPause;
  node.querySelector("button[data-action='resume']").disabled = !canResume;
  node.querySelector("button[data-action='cancel']").disabled = !canCancel;

  return node;
}

function createOrUpdateTaskNode(task) {
  let el = document.getElementById(`task-${task.id}`);
  let created = false;
  if (!el) {
    el = template.content.firstElementChild.cloneNode(true);
    el.id = `task-${task.id}`;
    el.dataset.taskId = task.id;

    const controls = el.querySelectorAll("button[data-action]");
    controls.forEach((btn) => {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.action;
        const id = el.dataset.taskId;
        try {
          await api(`/api/tasks/${id}/${action}`, { method: "POST" });
          msgEl.textContent = "Command sent.";
        } catch (error) {
          alert(error.message);
        }
      });
    });

    created = true;
  }

  el.querySelector(".filename").textContent = task.filename || "Pending metadata...";
  el.querySelector(".url").textContent = task.url;
  el.querySelector(".status").textContent = task.status;
  el.querySelector(".progress-bar").style.width = `${task.progress_pct || 0}%`;

  const speed = formatBytes(task.speed_bps || 0);
  const downloaded = formatBytes(task.downloaded || 0);
  const total = formatBytes(task.file_size || 0);
  const eta = formatEta(task.eta_seconds);
  el.querySelector(".stats").textContent =
    `Progress ${task.progress_pct || 0}% | ${downloaded} / ${total} | Speed ${speed}/s | ETA ${eta} | Segments ${task.segments}`;

  const status = task.status;
  const canStart = ["created", "starting"].includes(status);
  const canPause = status === "downloading";
  const canResume = status === "paused";
  const canCancel = !["completed", "failed", "cancelled"].includes(status);

  el.querySelector("button[data-action='start']").disabled = !canStart;
  el.querySelector("button[data-action='pause']").disabled = !canPause;
  el.querySelector("button[data-action='resume']").disabled = !canResume;
  el.querySelector("button[data-action='cancel']").disabled = !canCancel;

  return { el, created };
}

function renderHistory(items) {
  historyEl.innerHTML = "";
  if (!items.length) {
    historyEl.innerHTML = '<p class="message">No finished tasks yet.</p>';
    return;
  }

  for (const item of items) {
    const div = document.createElement("div");
    div.className = "history-item";
    const time = item.completed_at ? new Date(item.completed_at * 1000).toLocaleString() : "-";
    div.textContent = `${item.filename || item.url} | ${item.status} | ${item.progress_pct || 0}% | completed: ${time}`;
    historyEl.appendChild(div);
  }
}

async function refreshAll() {
  try {
    const [tasks, history] = await Promise.all([api("/api/tasks"), api("/api/history")]);

    // Remove placeholder message if present
    const placeholder = tasksEl.querySelector('.message');
    if (!tasks.length) {
      tasksEl.innerHTML = '<p class="message">No active task. Add a URL to begin.</p>';
    } else {
      if (placeholder) placeholder.remove();

      // Keep track of current task ids to remove stale nodes
      const currentIds = new Set();

      for (let i = 0; i < tasks.length; i++) {
        const task = tasks[i];
        const { el, created } = createOrUpdateTaskNode(task);
        currentIds.add(task.id);

        // Ensure correct ordering in the DOM
        const target = tasksEl.children[i];
        if (target !== el) {
          tasksEl.insertBefore(el, target || null);
        }
      }

      // Remove elements not present anymore
      Array.from(tasksEl.querySelectorAll('[id^="task-"]')).forEach((node) => {
        if (!currentIds.has(node.dataset.taskId)) node.remove();
      });
    }

    renderHistory(history);
  } catch (error) {
    msgEl.textContent = error.message;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  msgEl.textContent = "Adding task...";

  const payload = {
    url: document.getElementById("url").value.trim(),
    segments: Number(document.getElementById("segments").value),
    max_retries: Number(document.getElementById("max-retries").value),
    bandwidth_limit_kbps: document.getElementById("bandwidth").value.trim(),
    auto_start: true,
  };

  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    document.getElementById("segments").value = 4;
    document.getElementById("max-retries").value = 3;
    msgEl.textContent = "Task created.";
    await refreshAll();
  } catch (error) {
    msgEl.textContent = error.message;
  }
});

refreshBtn.addEventListener("click", refreshAll);

refreshAll();
setInterval(refreshAll, 1500);
