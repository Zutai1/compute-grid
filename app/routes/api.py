"""API routes for the Compute Grid."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import os

from ..main import get_db
from ..models.database import Task, Worker
from ..services.tasks import (
    submit_task, get_next_task, execute_task, complete_task,
    register_worker, heartbeat, get_cluster_stats,
)

router = APIRouter()


# --- Request/Response models ---

class TaskSubmit(BaseModel):
    task_type: str = Field(..., pattern="^(python|shell|http|file_read|file_write|compute)$")
    payload: dict = Field(default={})
    priority: int = Field(default=0, ge=0, le=10)
    timeout: int = Field(default=300, ge=10, le=3600)
    max_retries: int = Field(default=2, ge=0, le=5)


class WorkerRegister(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=100)
    hostname: str = Field(default="", max_length=255)
    platform: str = Field(default="", pattern="^(linux|windows|android|macos|cloud)$")
    cpu_cores: int = Field(default=1, ge=1, le=128)
    memory_mb: int = Field(default=512, ge=128, le=1048576)


class TaskResult(BaseModel):
    task_id: int
    success: bool = True
    result: Optional[dict] = None
    error: Optional[str] = None


# --- Task endpoints ---

@router.post("/tasks")
def submit(request: TaskSubmit, db: Session = Depends(get_db)):
    """Submit a new task to the queue."""
    task = submit_task(db, request.task_type, request.payload, request.priority,
                      request.timeout, request.max_retries)
    return {
        "task_id": task.id,
        "status": task.status,
        "created_at": task.created_at.isoformat(),
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    """Get task status and result."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "worker_id": task.worker_id,
        "retry_count": task.retry_count,
    }


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db), status: str = None, limit: int = 50):
    """List tasks, optionally filtered by status."""
    query = db.query(Task)
    if status:
        query = query.filter(Task.status == status)
    tasks = query.order_by(Task.created_at.desc()).limit(limit).all()
    return [
        {
            "task_id": t.id,
            "task_type": t.task_type,
            "status": t.status,
            "priority": t.priority,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@router.post("/tasks/{task_id}/result")
def submit_result(task_id: int, request: TaskResult, db: Session = Depends(get_db)):
    """Submit a task result (from a worker)."""
    task = complete_task(db, task_id, request.result or {}, request.success)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task.id, "status": task.status}


@router.delete("/tasks/{task_id}")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """Cancel a pending task."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("pending", "assigned"):
        raise HTTPException(status_code=400, detail="Cannot cancel a running or completed task")
    task.status = "cancelled"
    db.commit()
    return {"task_id": task.id, "status": "cancelled"}


# --- Worker endpoints ---

@router.post("/workers/register")
def register(request: WorkerRegister, db: Session = Depends(get_db)):
    """Register a worker node."""
    worker = register_worker(db, request.worker_id, request.hostname,
                            request.platform, request.cpu_cores, request.memory_mb)
    return {
        "worker_id": worker.worker_id,
        "status": worker.status,
        "registered_at": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
    }


@router.post("/workers/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str, db: Session = Depends(get_db)):
    """Update worker heartbeat."""
    worker = heartbeat(db, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not registered")
    return {"worker_id": worker.worker_id, "status": worker.status}


@router.get("/workers/next-task/{worker_id}")
def next_task(worker_id: str, db: Session = Depends(get_db)):
    """Get the next task for a worker. Workers poll this endpoint."""
    task = get_next_task(db, worker_id)
    if not task:
        return {"task": None}
    return {
        "task": {
            "task_id": task.id,
            "task_type": task.task_type,
            "payload": task.payload,
            "timeout": task.timeout_seconds,
        }
    }


@router.get("/workers")
def list_workers(db: Session = Depends(get_db)):
    """List all registered workers."""
    workers = db.query(Worker).all()
    return [
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
    ]


# --- Cluster endpoints ---

@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """Get cluster statistics."""
    return get_cluster_stats(db)


@router.get("/health")
def health():
    """Health check."""
    return {"status": "ok", "service": "compute-grid"}
