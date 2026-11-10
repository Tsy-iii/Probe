"""Execute pytest with an independent parent/lease/cancel watchdog on POSIX."""
import argparse
import os
import threading
import time
from pathlib import Path
from uuid import UUID

import pytest

from probe.db import Database
from probe.platform.config import PlatformSettings
from probe.platform.processes import stop_current_group, write_stop_reason
from probe.platform.repository import RunRepository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--attempt-id", type=UUID, required=True)
    parser.add_argument("--worker-id", type=UUID, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args, pytest_args = parser.parse_known_args()
    settings = PlatformSettings()
    database = Database(settings.database_url, schema=settings.database_schema)
    repository = RunRepository(database)
    finished = threading.Event()
    started = time.monotonic()

    def watchdog():
        last_contact = time.monotonic()
        while not finished.wait(0.1):
            reason = None
            if os.getppid() != args.parent_pid:
                reason = "worker_lost"
            elif time.monotonic() - started >= args.timeout:
                reason = "timed_out"
            else:
                try:
                    control = repository.control(args.run_id, args.attempt_id, args.worker_id)
                    last_contact = time.monotonic()
                    if not control["owned"]:
                        reason = "worker_lost"
                    elif control["cancelled"]:
                        reason = "cancelled"
                except Exception:
                    if time.monotonic() - last_contact >= settings.worker_lease_seconds:
                        reason = "worker_lost"
            if reason:
                write_stop_reason(args.directory, reason)
                print(f"Probe execution stopped: {reason}; attempt_id={args.attempt_id}", flush=True)
                stop_current_group(args.attempt_id)

    guard = threading.Thread(target=watchdog, daemon=True, name="probe-execution-watchdog")
    guard.start()
    try:
        code = pytest.main(pytest_args)
    finally:
        finished.set()
        guard.join(timeout=0.5)
        database.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
