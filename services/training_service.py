"""后台训练任务服务：启动/轮询数据集合并与 YOLO 复训。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_FILE = ROOT / "data" / "train" / "training_task.json"
LOG_FILE = ROOT / "data" / "train" / "training_task.log"
RESULT_FILE = ROOT / "data" / "train" / "last_training_result.json"

_PROCS: dict[int, subprocess.Popen] = {}


class TrainingService:
    """管理端训练任务的最小后台状态机。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else ROOT

    def start_prepare(self) -> tuple[bool, str]:
        if self._active():
            return False, "已有训练任务运行中"
        cmd = [sys.executable, str(self.root / "scripts" / "prepare_combined_dataset.py")]
        return self._spawn("preparing", cmd, {"phase": "preparing"})

    def start_train(self, from_best: bool = False, version: str = "v3",
                    only: str | None = None, epochs: int | None = None) -> tuple[bool, str]:
        if self._active():
            return False, "已有训练任务运行中"
        cmd = [
            sys.executable,
            str(self.root / "scripts" / "train_combined.py"),
            "--version", version,
        ]
        if from_best:
            cmd.append("--from-best")
        if only:
            cmd += ["--only", only]
        if epochs:
            cmd += ["--epochs", str(epochs)]
        return self._spawn("running", cmd, {
            "phase": "running",
            "version": version,
            "from_best": from_best,
        })

    def _spawn(self, phase: str, cmd: list[str],
               extra: dict | None = None) -> tuple[bool, str]:
        log_path = self.root / "data" / "train" / "training_task.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with open(log_path, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                cmd, cwd=str(self.root), stdout=f,
                stderr=subprocess.STDOUT, creationflags=flags)
        _PROCS[proc.pid] = proc
        task = {
            "phase": phase,
            "pid": proc.pid,
            "command": " ".join(cmd),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "started_ts": time.time(),
            "finished_at": None,
            "returncode": None,
            "log": str(log_path),
            "message": "任务已启动",
        }
        if extra:
            task.update(extra)
        self._write_task(task)
        return True, f"任务已启动，PID={proc.pid}"

    def status(self) -> dict:
        task = self._read_task()
        if not task:
            return {"phase": "idle", "message": "暂无训练任务"}
        pid = task.get("pid")
        proc = _PROCS.get(pid) if pid else None
        if task.get("phase") in ("preparing", "running") and proc is not None:
            code = proc.poll()
            if code is not None:
                task["returncode"] = code
                task["phase"] = self._classify(code, task)
                task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                task["message"] = "任务完成" if task["phase"] == "success" else "任务失败"
                self._write_task(task)
        elif task.get("phase") in ("preparing", "running") and not self._alive(pid):
            task["phase"] = self._classify(None, task)
            task["finished_at"] = datetime.now().isoformat(timespec="seconds")
            task["message"] = "任务完成" if task["phase"] == "success" else "任务失败"
            self._write_task(task)
        return task

    @staticmethod
    def _classify(returncode: int | None, task: dict) -> str:
        if returncode not in (None, 0):
            return "failed"
        if task.get("phase") == "running" and not RESULT_FILE.exists():
            return "failed"
        if task.get("phase") == "preparing":
            log = LOG_FILE.read_text(encoding="utf-8", errors="replace")[-4000:]
            if "Traceback" in log or "Error" in log:
                return "failed"
        return "success"

    def tail_log(self, limit: int = 4000) -> str:
        try:
            text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-limit:]

    def stop(self) -> tuple[bool, str]:
        """手动早停：结束训练进程树并更新任务状态。"""
        task = self._read_task()
        pid = task.get("pid")
        proc = _PROCS.get(pid) if pid else None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        if pid and self._alive(pid):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
        task["phase"] = "early_stopped"
        task["finished_at"] = datetime.now().isoformat(timespec="seconds")
        task["message"] = "用户手动早停"
        self._write_task(task)
        return True, "训练已早停"

    def export_best(self, name: str, version: str,
                    run_dir: str | Path) -> tuple[bool, str]:
        """导出早停时 best.pt 对应的 ONNX 并写训练结果。"""
        cmd = [
            sys.executable,
            str(self.root / "scripts" / "export_best.py"),
            "--name", name,
            "--version", version,
            "--run-dir", str(run_dir),
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(self.root), capture_output=True, text=True,
                timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"导出失败: {e}"
        if proc.returncode != 0:
            return False, proc.stderr[-800:] or proc.stdout[-800:]
        return True, proc.stdout[-500:]

    def latest_result(self) -> dict | None:
        try:
            data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def alive(self, pid: int | None = None) -> bool:
        if pid is None:
            pid = self._read_task().get("pid")
        return self._alive(pid)

    def _active(self) -> bool:
        task = self._read_task()
        if task.get("phase") not in ("preparing", "running"):
            return False
        pid = task.get("pid")
        proc = _PROCS.get(pid) if pid else None
        if proc is not None:
            return proc.poll() is None
        return self._alive(pid)

    @staticmethod
    def _alive(pid: int | None) -> bool:
        if not pid:
            return False
        if os.name == "nt":
            try:
                result = subprocess.run(
                    [
                        "powershell", "-NoProfile", "-Command",
                        f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ 'alive' }} else {{ 'dead' }}",
                    ],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5)
                return "alive" in result.stdout
            except (OSError, subprocess.SubprocessError):
                return False
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    def _read_task(self) -> dict:
        try:
            data = json.loads(TASK_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_task(self, task: dict) -> None:
        TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        TASK_FILE.write_text(
            json.dumps(task, ensure_ascii=False, indent=2),
            encoding="utf-8")
