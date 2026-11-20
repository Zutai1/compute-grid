# Compute Grid

Distributed task execution across multiple machines. Submit tasks from any device,
execute on any worker.

## Quick start

### Coordinator (Pi or any machine)

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/docs for the interactive API.

### Worker (any machine)

```bash
python workers/worker.py --server http://coordinator:8000 --worker-id my-worker --platform linux --cores 4 --memory 4096
```

### Client (submit tasks)

```python
from compute_client import ComputeClient

client = ComputeClient("http://coordinator:8000")

# Submit Python code
task_id = client.submit_python("print('hello from remote')")
result = client.wait(task_id)
print(result["result"]["stdout"])

# Submit shell command
task_id = client.submit_shell("uname -a")
result = client.wait(task_id)
print(result["result"]["stdout"])

# Distribute work
task_ids = [client.submit_python(f"print({i}**2)") for i in range(10)]
results = client.wait_all(task_ids)
for r in results:
    print(r["result"]["stdout"].strip())

# Cluster stats
print(client.stats())
```

## Docker

```bash
docker build -t compute-grid .
docker run -d -p 8000:8000 -v compute-grid-data:/app/data compute-grid
```

## Task types

| Type | Description | Payload |
|------|-------------|---------|
| `python` | Run Python code | `{"code": "print('hi')", "timeout": 60}` |
| `shell` | Run shell command | `{"command": "ls -la", "timeout": 60}` |
| `http` | HTTP request | `{"url": "https://...", "method": "GET"}` |
| `file_read` | Read a file | `{"path": "/path/to/file"}` |
| `file_write` | Write a file | `{"path": "/path/to/file", "content": "..."}` |
| `compute` | Computation | `{"operation": "sum", "data": [1,2,3]}` |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/tasks | Submit a task |
| GET | /api/tasks/{id} | Get task status/result |
| GET | /api/tasks | List tasks |
| POST | /api/tasks/{id}/result | Submit result (worker) |
| DELETE | /api/tasks/{id} | Cancel task |
| POST | /api/workers/register | Register worker |
| POST | /api/workers/{id}/heartbeat | Worker heartbeat |
| GET | /api/workers/next-task/{id} | Poll for task (worker) |
| GET | /api/workers | List workers |
| GET | /api/stats | Cluster statistics |
| GET | /api/health | Health check |

## Architecture

```
Coordinator (Pi or any machine)
├── Task Queue (SQLite)
├── Worker Registry
└── Result Store

Workers (any machine)
├── Poll for tasks
├── Execute (python, shell, http, file, compute)
└── Report results

Clients (any machine)
├── Submit tasks
├── Wait for results
└── Distribute work
```

## Security notes

- No authentication by default (add API key for production)
- Workers can execute arbitrary code (trust your workers)
- File access is unrestricted (sandbox in production)
- Use Tailscale or VPN for secure inter-machine communication