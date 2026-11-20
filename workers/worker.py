#!/usr/bin/env python3
"""Compute Grid Worker — runs on any machine and executes tasks from the queue.

Usage:
    python worker.py --server http://pi-ip:8000 --worker-id my-worker --platform linux

The worker:
1. Registers itself with the coordinator
2. Polls for tasks every 2 seconds
3. Executes tasks (python, shell, http, file_read, file_write, compute)
4. Reports results back
5. Sends heartbeat every 30 seconds
"""
import argparse
import json
import os
import platform
import socket
import time
import urllib.request
import urllib.error


class ComputeWorker:
    def __init__(self, server_url: str, worker_id: str, platform: str = "",
                 cpu_cores: int = 1, memory_mb: int = 512):
        self.server_url = server_url.rstrip("/")
        self.worker_id = worker_id
        self.platform = platform or self._detect_platform()
        self.cpu_cores = cpu_cores
        self.memory_mb = memory_mb
        self.running = True

    def _detect_platform(self) -> str:
        system = platform.system().lower()
        if "linux" in system:
            if "android" in platform.release().lower() or os.path.exists("/system/bin/termux"):
                return "android"
            return "linux"
        elif "windows" in system:
            return "windows"
        elif "darwin" in system:
            return "macos"
        return "unknown"

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        url = f"{self.server_url}{path}"
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

    def register(self):
        """Register this worker with the coordinator."""
        import os
        hostname = socket.gethostname()
        result = self._request("POST", "/api/workers/register", {
            "worker_id": self.worker_id,
            "hostname": hostname,
            "platform": self.platform,
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
        })
        print(f"Registered: {result}")
        return result

    def heartbeat(self):
        """Send heartbeat to coordinator."""
        return self._request("POST", f"/api/workers/{self.worker_id}/heartbeat")

    def get_task(self) -> dict | None:
        """Poll for next task."""
        result = self._request("GET", f"/api/workers/next-task/{self.worker_id}")
        task = result.get("task")
        return task

    def submit_result(self, task_id: int, success: bool, result: dict = None, error: str = None):
        """Submit task result."""
        data = {"task_id": task_id, "success": success}
        if result:
            data["result"] = result
        if error:
            data["error"] = error
        return self._request("POST", f"/api/tasks/{task_id}/result", data)

    def execute_task(self, task: dict) -> tuple[bool, dict, str]:
        """Execute a task locally. Returns (success, result, error)."""
        task_type = task["task_type"]
        payload = task["payload"]

        if task_type == "python":
            return self._exec_python(payload)
        elif task_type == "shell":
            return self._exec_shell(payload)
        elif task_type == "http":
            return self._exec_http(payload)
        elif task_type == "file_read":
            return self._exec_file_read(payload)
        elif task_type == "file_write":
            return self._exec_file_write(payload)
        elif task_type == "compute":
            return self._exec_compute(payload)
        else:
            return False, {}, f"Unknown task type: {task_type}"

    def _exec_python(self, payload):
        import tempfile
        code = payload.get("code", "")
        timeout = payload.get("timeout", 60)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            import subprocess
            r = subprocess.run(["python3", path], capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0, {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}, ""
        except Exception as e:
            return False, {}, str(e)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _exec_shell(self, payload):
        import subprocess
        cmd = payload.get("command", "")
        timeout = payload.get("timeout", 60)
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0, {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}, ""
        except Exception as e:
            return False, {}, str(e)

    def _exec_http(self, payload):
        url = payload.get("url", "")
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return True, {"status_code": resp.status, "body": resp.read().decode("utf-8", "replace")[:5000]}, ""
        except Exception as e:
            return False, {}, str(e)

    def _exec_file_read(self, payload):
        path = payload.get("path", "")
        try:
            with open(path, "r") as f:
                content = f.read()
            return True, {"content": content[:10000], "size": len(content)}, ""
        except Exception as e:
            return False, {}, str(e)

    def _exec_file_write(self, payload):
        path = payload.get("path", "")
        content = payload.get("content", "")
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
            with open(path, "w") as f:
                f.write(content)
            return True, {"size": len(content)}, ""
        except Exception as e:
            return False, {}, str(e)

    def _exec_compute(self, payload):
        op = payload.get("operation", "")
        data = payload.get("data", [])
        if op == "sum":
            return True, {"result": sum(data)}, ""
        elif op == "matrix_multiply":
            a, b = data[0], data[1]
            result = [[sum(a[i][k] * b[k][j] for k in range(len(a[0]))) for j in range(len(b[0]))] for i in range(len(a))]
            return True, {"result": result}, ""
        elif op == "hash":
            import hashlib
            return True, {"result": hashlib.sha256(str(data).encode()).hexdigest()}, ""
        return False, {}, f"Unknown operation: {op}"

    def run(self, poll_interval: float = 2.0, heartbeat_interval: float = 30.0):
        """Main worker loop."""
        self.register()
        last_heartbeat = time.time()

        print(f"Worker {self.worker_id} started. Polling every {poll_interval}s...")

        while self.running:
            # Heartbeat
            if time.time() - last_heartbeat > heartbeat_interval:
                self.heartbeat()
                last_heartbeat = time.time()

            # Poll for task
            task = self.get_task()
            if task:
                print(f"Got task {task['task_id']}: {task['task_type']}")
                success, result, error = self.execute_task(task)
                self.submit_result(task["task_id"], success, result, error)
                print(f"Task {task['task_id']}: {'OK' if success else 'FAILED'}")
            else:
                time.sleep(poll_interval)

    def stop(self):
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="Compute Grid Worker")
    parser.add_argument("--server", required=True, help="Coordinator URL (e.g., http://pi-ip:8000)")
    parser.add_argument("--worker-id", required=True, help="Unique worker ID")
    parser.add_argument("--platform", default="", help="Platform (linux/windows/android/macos)")
    parser.add_argument("--cores", type=int, default=1, help="CPU cores available")
    parser.add_argument("--memory", type=int, default=512, help="Memory in MB")
    parser.add_argument("--poll", type=float, default=2.0, help="Poll interval in seconds")
    args = parser.parse_args()

    worker = ComputeWorker(
        server_url=args.server,
        worker_id=args.worker_id,
        platform=args.platform,
        cpu_cores=args.cores,
        memory_mb=args.memory,
    )
    try:
        worker.run(poll_interval=args.poll)
    except KeyboardInterrupt:
        worker.stop()
        print("Worker stopped.")


if __name__ == "__main__":
    main()
