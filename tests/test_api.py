"""Tests for the Compute Grid API."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app, get_db
from app.models.database import Base

TEST_DB_URL = "sqlite:///./test_compute_grid.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


client = TestClient(app)


class TestHealth:
    def test_health(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestTasks:
    def test_submit_python(self):
        resp = client.post("/api/tasks", json={
            "task_type": "python",
            "payload": {"code": "print('hello')", "timeout": 10}
        })
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_submit_shell(self):
        resp = client.post("/api/tasks", json={
            "task_type": "shell",
            "payload": {"command": "echo hello", "timeout": 10}
        })
        assert resp.status_code == 200

    def test_submit_compute(self):
        resp = client.post("/api/tasks", json={
            "task_type": "compute",
            "payload": {"operation": "sum", "data": [1, 2, 3]}
        })
        assert resp.status_code == 200

    def test_get_task(self):
        resp = client.post("/api/tasks", json={
            "task_type": "python",
            "payload": {"code": "print('test')", "timeout": 10}
        })
        task_id = resp.json()["task_id"]
        resp2 = client.get(f"/api/tasks/{task_id}")
        assert resp2.status_code == 200
        assert resp2.json()["task_id"] == task_id

    def test_list_tasks(self):
        client.post("/api/tasks", json={"task_type": "python", "payload": {"code": "print(1)"}})
        client.post("/api/tasks", json={"task_type": "python", "payload": {"code": "print(2)"}})
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_cancel_task(self):
        resp = client.post("/api/tasks", json={"task_type": "python", "payload": {"code": "print(1)"}})
        task_id = resp.json()["task_id"]
        resp2 = client.delete(f"/api/tasks/{task_id}")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "cancelled"

    def test_invalid_task_type(self):
        resp = client.post("/api/tasks", json={"task_type": "invalid", "payload": {}})
        assert resp.status_code == 422


class TestWorkers:
    def test_register_worker(self):
        resp = client.post("/api/workers/register", json={
            "worker_id": "test-worker",
            "hostname": "test-host",
            "platform": "linux",
            "cpu_cores": 4,
            "memory_mb": 4096
        })
        assert resp.status_code == 200
        assert resp.json()["worker_id"] == "test-worker"

    def test_list_workers(self):
        client.post("/api/workers/register", json={"worker_id": "w1", "platform": "linux"})
        client.post("/api/workers/register", json={"worker_id": "w2", "platform": "windows"})
        resp = client.get("/api/workers")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_worker_heartbeat(self):
        client.post("/api/workers/register", json={"worker_id": "hb-worker", "platform": "linux"})
        resp = client.post("/api/workers/hb-worker/heartbeat")
        assert resp.status_code == 200

    def test_unknown_worker_heartbeat(self):
        resp = client.post("/api/workers/unknown/heartbeat")
        assert resp.status_code == 404


class TestStats:
    def test_stats(self):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "workers" in data
        assert "tasks" in data


class TestExecution:
    def test_execute_python_task(self):
        from app.services.tasks import execute_task
        from app.models.database import Task, TaskStatus
        # Submit task
        resp = client.post("/api/tasks", json={
            "task_type": "python",
            "payload": {"code": "print('hello world')", "timeout": 10}
        })
        task_id = resp.json()["task_id"]
        # Execute directly (simulating worker)
        db = TestSessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()
        result = execute_task(task)
        assert result["success"] is True
        assert "hello world" in result["stdout"]
        db.close()

    def test_execute_compute_sum(self):
        from app.services.tasks import execute_task
        from app.models.database import Task
        resp = client.post("/api/tasks", json={
            "task_type": "compute",
            "payload": {"operation": "sum", "data": [1, 2, 3, 4, 5]}
        })
        task_id = resp.json()["task_id"]
        db = TestSessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()
        result = execute_task(task)
        assert result["success"] is True
        assert result["result"] == 15
        db.close()

    def test_execute_compute_matrix(self):
        from app.services.tasks import execute_task
        from app.models.database import Task
        resp = client.post("/api/tasks", json={
            "task_type": "compute",
            "payload": {"operation": "matrix_multiply", "data": [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]}
        })
        task_id = resp.json()["task_id"]
        db = TestSessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()
        result = execute_task(task)
        assert result["success"] is True
        assert result["result"] == [[19, 22], [43, 50]]
        db.close()
