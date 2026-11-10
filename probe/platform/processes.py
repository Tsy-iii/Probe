import json
import os
import signal
from pathlib import Path
from uuid import UUID

import psutil


def write_stop_reason(directory: Path, reason: str):
    temporary = directory / f"stop-{os.getpid()}.tmp"
    temporary.write_text(json.dumps({"reason": reason}))
    temporary.replace(directory / "stop.json")


def read_stop_reason(directory: Path) -> str | None:
    path = directory / "stop.json"
    try:
        reason = json.loads(path.read_text())["reason"] if path.exists() else None
    except (OSError, ValueError, KeyError):
        return None
    return reason if reason in {"worker_lost", "cancelled", "timed_out"} else None


def attempt_processes(attempt_id: UUID) -> list[psutil.Process]:
    # Tag each execution environment with an unguessable attempt UUID. psutil
    # validates process identity on signalling; never kill a saved PID blindly.
    matches = []
    for process in psutil.process_iter():
        if process.pid == os.getpid():
            continue
        try:
            if process.status() != psutil.STATUS_ZOMBIE and process.environ().get("PROBE_ATTEMPT_ID") == str(attempt_id):
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def terminate_attempt(attempt_id: UUID):
    processes = attempt_processes(attempt_id)
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(processes, timeout=1)
    if attempt_processes(attempt_id):
        raise RuntimeError("Execution processes could not be stopped")


def stop_current_group(attempt_id: UUID):
    terminate_attempt(attempt_id)
    os.killpg(os.getpgrp(), signal.SIGKILL)
