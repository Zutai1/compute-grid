"""Task execution service.

Handles task assignment, execution, and result collection.
"""
import json
import subprocess
import sys
import time
import os
import urllib.request
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ..models.database import Task, Worker, TaskLog, TaskStatus, TaskType


def submit_task(db: Session, task_type: str, payload: dict, priority: int = 0,
                timeout: int = 300, max_retries: int = 2) -> Task:
    """Submit a new task to the queue."""
    task = Task(
        task_type=task_type,
        payload=payload,
        priority=priority,
        timeout_seconds=timeout,
        max_retries=max_retries,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_next_task(db: Session, worker_id: str) -> Task | None:
    """Get the next pending task for a worker. Assigns it to the worker."""
    worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
    if not worker or worker.status not in ("online", "busy"):
        return None

    # Find highest priority pending task
    task = (
        db.query(Task)
        .filter(Task.status == TaskStatus.PENDING)
        .order_by(Task.priority.desc(), Task.created_at.asc())
        .first()
    )
    if not task:
        return None

    # Assign task to worker
    task.status = TaskStatus.ASSIGNED
    task.worker_id = worker.id
    task.started_at = datetime.now(timezone.utc)
    worker.status = "busy"
    worker.current_task_id = task.id
    db.commit()
    db.refresh(task)
    return task


def execute_task(task: Task) -> dict:
    """Execute a task and return the result."""
    task_type = task.task_type
    payload = task.payload

    if task_type == TaskType.PYTHON:
        return _execute_python(payload)
    elif task_type == TaskType.SHELL:
        return _execute_shell(payload)
    elif task_type == TaskType.HTTP:
        return _execute_http(payload)
    elif task_type == TaskType.FILE_READ:
        return _execute_file_read(payload)
    elif task_type == TaskType.FILE_WRITE:
        return _execute_file_write(payload)
    elif task_type == TaskType.COMPUTE:
        return _execute_compute(payload)
    else:
        raise ValueError(f"Unknown task type: {task_type}")


def _execute_python(payload: dict) -> dict:
    """Execute Python code in a subprocess."""
    code = payload.get("code", "")
    timeout = payload.get("timeout", 60)
    allowed_imports = payload.get("allowed_imports", [])

    # Write code to temp file
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timeout", "returncode": -1, "success": False}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _execute_shell(payload: dict) -> dict:
    """Execute a shell command."""
    command = payload.get("command", "")
    timeout = payload.get("timeout", 60)
    shell = payload.get("shell", True)

    try:
        result = subprocess.run(
            command if shell else command.split(),
            shell=shell, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timeout", "returncode": -1, "success": False}


def _execute_http(payload: dict) -> dict:
    """Make an HTTP request."""
    url = payload.get("url", "")
    method = payload.get("method", "GET")
    headers = payload.get("headers", {})
    body = payload.get("body")
    timeout = payload.get("timeout", 30)

    try:
        req = urllib.request.Request(url, data=body.encode() if body else None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "status_code": resp.status,
                "body": resp.read().decode("utf-8", "replace"),
                "headers": dict(resp.headers),
                "success": True,
            }
    except Exception as e:
        return {"status_code": 0, "body": "", "error": str(e), "success": False}


def _execute_file_read(payload: dict) -> dict:
    """Read a file."""
    path = payload.get("path", "")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content, "success": True, "size": len(content)}
    except Exception as e:
        return {"content": "", "error": str(e), "success": False}


def _execute_file_write(payload: dict) -> dict:
    """Write a file."""
    path = payload.get("path", "")
    content = payload.get("content", "")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _execute_compute(payload: dict) -> dict:
    """Run a computation (matrix, etc.)."""
    import json as _json
    operation = payload.get("operation", "")
    data = payload.get("data", [])

    if operation == "sum":
        return {"result": sum(data), "success": True}
    elif operation == "matrix_multiply":
        # Simple matrix multiplication
        a = data[0]
        b = data[1]
        rows_a, cols_a = len(a), len(a[0]) if a else 0
        rows_b, cols_b = len(b), len(b[0]) if b else 0
        if cols_a != rows_b:
            return {"error": "Matrix dimensions don't match", "success": False}
        result = [[sum(a[i][k] * b[k][j] for k in range(cols_a)) for j in range(cols_b)] for i in range(rows_a)]
        return {"result": result, "success": True}
    elif operation == "hash":
        import hashlib
        return {"result": hashlib.sha256(str(data).encode()).hexdigest(), "success": True}
    else:
        return {"error": f"Unknown operation: {operation}", "success": False}


def complete_task(db: Session, task_id: int, result: dict, success: bool = True):
    """Mark a task as completed or failed."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return None

    task.result = result
    task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
    task.completed_at = datetime.now(timezone.utc)

    if task.worker_id:
        worker = db.query(Worker).filter(Worker.id == task.worker_id).first()
        if worker:
            worker.status = "online"
            worker.current_task_id = None
            if success:
                worker.tasks_completed += 1
            else:
                worker.tasks_failed += 1

    db.commit()
    db.refresh(task)
    return task


def register_worker(db: Session, worker_id: str, hostname: str = "", platform: str = "",
                    cpu_cores: int = 1, memory_mb: int = 512) -> Worker:
    """Register or update a worker."""
    worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
    if not worker:
        worker = Worker(
            worker_id=worker_id,
            hostname=hostname,
            platform=platform,
            cpu_cores=cpu_cores,
            memory_mb=memory_mb,
        )
        db.add(worker)
    else:
        worker.hostname = hostname
        worker.platform = platform
        worker.cpu_cores = cpu_cores
        worker.memory_mb = memory_mb
        worker.status = "online"
        worker.last_heartbeat = datetime.now(timezone.utc)
    db.commit()
    db.refresh(worker)
    return worker


def heartbeat(db: Session, worker_id: str) -> Worker | None:
    """Update worker heartbeat."""
    worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
    if not worker:
        return None
    worker.last_heartbeat = datetime.now(timezone.utc)
    if worker.status == "offline":
        worker.status = "online"
    db.commit()
    db.refresh(worker)
    return worker


def get_cluster_stats(db: Session) -> dict:
    """Get cluster statistics."""
    workers = db.query(Worker).all()
    tasks = db.query(Task).all()
    return {
        "workers": {
            "total": len(workers),
            "online": len([w for w in workers if w.status == "online"]),
            "busy": len([w for w in workers if w.status == "busy"]),
            "offline": len([w for w in workers if w.status == "offline"]),
        },
        "tasks": {
            "total": len(tasks),
            "pending": len([t for t in tasks if t.status == TaskStatus.PENDING]),
            "running": len([t for t in tasks if t.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING)]),
            "completed": len([t for t in tasks if t.status == TaskStatus.COMPLETED]),
            "failed": len([t for t in tasks if t.status == TaskStatus.FAILED]),
        },
        "worker_details": [
            {
                "worker_id": w.worker_id,
                "hostname": w.hostname,
                "platform": w.platform,
                "status": w.status,
                "cpu_cores": w.cpu_cores,
                "memory_mb": w.memory_mb,
                "tasks_completed": w.tasks_completed,
                "tasks_failed": w.tasks_failed,
                "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
            }
            for w in workers
        ],
    }
