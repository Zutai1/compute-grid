"""Python client for the Compute Grid API.

Usage:
    from compute_client import ComputeClient

    client = ComputeClient("http://pi-ip:8000")

    # Submit a task
    task_id = client.submit_python("print('hello from remote')")
    result = client.wait(task_id)
    print(result["result"]["stdout"])

    # Submit a shell command
    task_id = client.submit_shell("uname -a")
    result = client.wait(task_id)
    print(result["result"]["stdout"])

    # Distribute work
    task_ids = []
    for i in range(10):
        tid = client.submit_python(f"print({i}**2)")
        task_ids.append(tid)
    results = client.wait_all(task_ids)
    for r in results:
        print(r["result"]["stdout"].strip())
"""
import json
import time
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any


class ComputeClient:
    """Client for the Compute Grid API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"error": str(e), "status_code": e.code}
        except Exception as e:
            return {"error": str(e)}

    # --- Task submission ---

    def submit(self, task_type: str, payload: dict, priority: int = 0,
              timeout: int = 300) -> int:
        """Submit a task. Returns task_id."""
        result = self._request("POST", "/api/tasks", {
            "task_type": task_type,
            "payload": payload,
            "priority": priority,
            "timeout": timeout,
        })
        return result.get("task_id")

    def submit_python(self, code: str, timeout: int = 60, priority: int = 0) -> int:
        """Submit Python code for execution."""
        return self.submit("python", {"code": code, "timeout": timeout}, priority, timeout)

    def submit_shell(self, command: str, timeout: int = 60, priority: int = 0) -> int:
        """Submit a shell command for execution."""
        return self.submit("shell", {"command": command, "timeout": timeout}, priority, timeout)

    def submit_http(self, url: str, method: str = "GET", timeout: int = 30) -> int:
        """Submit an HTTP request task."""
        return self.submit("http", {"url": url, "method": method, "timeout": timeout}, 0, timeout)

    def submit_compute(self, operation: str, data: Any, timeout: int = 60) -> int:
        """Submit a computation task."""
        return self.submit("compute", {"operation": operation, "data": data}, 0, timeout)

    def submit_file_read(self, path: str) -> int:
        """Submit a file read task."""
        return self.submit("file_read", {"path": path}, 0, 30)

    def submit_file_write(self, path: str, content: str) -> int:
        """Submit a file write task."""
        return self.submit("file_write", {"path": path, "content": content}, 0, 30)

    # --- Task status ---

    def get_task(self, task_id: int) -> dict:
        """Get task status and result."""
        return self._request("GET", f"/api/tasks/{task_id}")

    def wait(self, task_id: int, poll_interval: float = 1.0, timeout: float = 300) -> dict:
        """Wait for a task to complete. Returns the task result."""
        start = time.time()
        while time.time() - start < timeout:
            task = self.get_task(task_id)
            if task.get("status") in ("completed", "failed", "cancelled"):
                return task
            time.sleep(poll_interval)
        return {"error": "Timeout waiting for task", "task_id": task_id}

    def wait_all(self, task_ids: List[int], poll_interval: float = 1.0, timeout: float = 300) -> List[dict]:
        """Wait for all tasks to complete."""
        start = time.time()
        results = {}
        pending = set(task_ids)
        while pending and time.time() - start < timeout:
            for tid in list(pending):
                task = self.get_task(tid)
                if task.get("status") in ("completed", "failed", "cancelled"):
                    results[tid] = task
                    pending.discard(tid)
            if pending:
                time.sleep(poll_interval)
        # Add any remaining
        for tid in pending:
            results[tid] = {"error": "Timeout", "task_id": tid}
        return [results[tid] for tid in task_ids]

    def cancel(self, task_id: int) -> dict:
        """Cancel a pending task."""
        return self._request("DELETE", f"/api/tasks/{task_id}")

    def list_tasks(self, status: str = None, limit: int = 50) -> List[dict]:
        """List tasks."""
        path = "/api/tasks"
        if status:
            path += f"?status={status}"
        path += f"&limit={limit}"
        return self._request("GET", path)

    # --- Worker management ---

    def register_worker(self, worker_id: str, hostname: str = "", platform: str = "",
                        cpu_cores: int = 1, memory_mb: int = 512) -> dict:
        """Register a worker."""
        return self._request("POST", "/api/workers/register", {
            "worker_id": worker_id,
            "hostname": hostname,
            "platform": platform,
            "cpu_cores": cpu_cores,
            "memory_mb": memory_mb,
        })

    def list_workers(self) -> List[dict]:
        """List all workers."""
        return self._request("GET", "/api/workers")

    def stats(self) -> dict:
        """Get cluster statistics."""
        return self._request("GET", "/api/stats")

    def health(self) -> dict:
        """Health check."""
        return self._request("GET", "/api/health")

    # --- Convenience: distribute work ---

    def map_python(self, code_template: str, items: List[Any], timeout: int = 60) -> List[dict]:
        """Map a Python function over a list of items, distributing across workers.

        code_template should contain {item} placeholder.
        Example: client.map_python("print({item} ** 2)", [1, 2, 3, 4, 5])
        """
        task_ids = []
        for item in items:
            code = code_template.format(item=repr(item))
            tid = self.submit_python(code, timeout=timeout)
            task_ids.append(tid)
        return self.wait_all(task_ids)

    def run_on_all(self, command: str, timeout: int = 60) -> List[dict]:
        """Run a shell command on all workers."""
        workers = self.list_workers()
        task_ids = []
        for w in workers:
            tid = self.submit_shell(command, timeout=timeout)
            task_ids.append(tid)
        return self.wait_all(task_ids)


# Convenience function
def compute(base_url: str = "http://localhost:8000") -> ComputeClient:
    """Create a compute client."""
    return ComputeClient(base_url=base_url)
