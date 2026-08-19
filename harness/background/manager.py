"""harness.background.manager — BackgroundManager（后台 bash 任务）。"""

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field


@dataclass
class BackgroundTask:
    task_id: str
    command: str
    status: str = "running"
    output: str = ""
    started_at: str = ""
    finished_at: str = ""


class BackgroundManager:
    """后台任务管理：start 启 daemon 线程，collect 出队 <task_notification>。"""

    def __init__(self):
        self.tasks: dict[str, BackgroundTask] = {}
        self._ready: list[str] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._bash = shutil.which("bash")

    def start(self, command: str) -> str:
        """启动后台 bash 命令，立即返回 task_id。"""
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Bash command cannot be empty")

        with self._lock:
            self._counter += 1
            task_id = f"bg_{self._counter:04d}"
            from datetime import datetime
            self.tasks[task_id] = BackgroundTask(
                task_id=task_id, command=command,
                started_at=datetime.now().isoformat(),
            )

        thread = threading.Thread(target=self._run, args=(task_id, command), daemon=True)
        try:
            thread.start()
        except Exception:
            with self._lock:
                self.tasks.pop(task_id, None)
            raise
        print(f"  \033[90m[background] started {task_id}: {command[:60]}\033[0m", flush=True)
        return task_id

    def _run(self, task_id: str, command: str):
        """daemon 线程：执行命令 + 收集结果。"""
        try:
            if self._bash:
                proc = subprocess.Popen(
                    [self._bash, "-c", command],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, errors="replace",
                    start_new_session=True,
                )
            else:
                proc = subprocess.Popen(
                    command, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, errors="replace",
                )
            with self._lock:
                self._processes[task_id] = proc
            output, _ = proc.communicate(timeout=300)
            exit_code = proc.returncode
            result = output.strip() if output.strip() else "(no output)"
            if len(result) > 50000:
                result = result[:50000] + "\n... (truncated)"
            status = "completed" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            self._kill_process(task_id)
            result = "Error: command timed out (300s)"
            status = "failed"
        except Exception as error:
            result = f"Error: {type(error).__name__}: {error}"
            status = "failed"

        from datetime import datetime
        with self._lock:
            task = self.tasks.get(task_id)
            if task is not None:
                task.status = status
                task.output = result
                task.finished_at = datetime.now().isoformat()
            self._processes.pop(task_id, None)
            self._ready.append(task_id)

    def _kill_process(self, task_id: str):
        """杀后台进程（Windows: taskkill /F /T）。"""
        proc = self._processes.get(task_id)
        if proc is None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=5)
            else:
                proc.kill()
        except Exception:
            pass

    def collect(self) -> list[str]:
        """返回 [<task_notification> XML] 列表。"""
        with self._lock:
            ready = []
            for tid in self._ready:
                task = self.tasks.pop(tid, None)
                if task is not None:
                    ready.append(task)
            self._ready.clear()

        notifications = []
        for task in ready:
            notifications.append(
                f"<task_notification>\n"
                f"  <task_id>{task.task_id}</task_id>\n"
                f"  <status>{task.status}</status>\n"
                f"  <command>{task.command}</command>\n"
                f"  <summary>{task.output[:500]}</summary>\n"
                f"</task_notification>"
            )
            print(f"  \033[90m[background] collected {task.task_id}: {task.status}\033[0m", flush=True)
        return notifications

    def stop_all(self):
        """atexit 时杀所有后台进程。"""
        with self._lock:
            pids = list(self._processes.keys())
        for tid in pids:
            self._kill_process(tid)


# Module-level singleton
BACKGROUND = BackgroundManager()
