"""Database models for the Compute Grid.

Tables:
  - tasks: submitted tasks with status, payload, result
  - workers: registered compute nodes
  - task_logs: execution logs per task
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON, Boolean, Enum
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, enum.Enum):
    PYTHON = "python"
    SHELL = "shell"
    HTTP = "http"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    COMPUTE = "compute"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(20), nullable=False)
    payload = Column(JSON, nullable=False)  # task-specific data
    status = Column(String(20), default=TaskStatus.PENDING, index=True)
    priority = Column(Integer, default=0)  # higher = more urgent
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    timeout_seconds = Column(Integer, default=300)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=2)

    worker = relationship("Worker", back_populates="tasks")
    logs = relationship("TaskLog", back_populates="task", cascade="all, delete-orphan")


class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String(100), unique=True, nullable=False, index=True)
    hostname = Column(String(255), nullable=True)
    platform = Column(String(50), nullable=True)  # linux, windows, android
    cpu_cores = Column(Integer, default=1)
    memory_mb = Column(Integer, default=512)
    status = Column(String(20), default="online")  # online, offline, busy
    last_heartbeat = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    current_task_id = Column(Integer, nullable=True)
    tasks_completed = Column(Integer, default=0)
    tasks_failed = Column(Integer, default=0)

    tasks = relationship("Task", back_populates="worker")


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    level = Column(String(10), default="info")  # info, warn, error
    message = Column(Text, nullable=False)

    task = relationship("Task", back_populates="logs")
