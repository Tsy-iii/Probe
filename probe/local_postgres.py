"""Run a project-local real PostgreSQL server; optional alternative to Compose."""
import argparse
import socket
import subprocess
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
DATA = LOCAL / "postgres"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status"])
    parser.add_argument("--port", type=int, default=5433)
    args = parser.parse_args()
    try:
        import pgembed
    except ImportError:
        raise SystemExit("Install the optional binaries: python -m pip install -r requirements-local-db.txt")
    # pgembed 0.2.0 documents a root export but ships the constant in _commands.
    # Pin the optional package and isolate that compatibility detail here.
    from pgembed._commands import POSTGRES_BIN_PATH
    binaries = Path(POSTGRES_BIN_PATH)
    pg_ctl = str(binaries / "pg_ctl")
    if args.command in {"stop", "status"}:
        result = subprocess.run([pg_ctl, "-D", str(DATA), args.command], check=False)
        raise SystemExit(result.returncode)
    LOCAL.mkdir(exist_ok=True)
    if not (DATA / "PG_VERSION").exists():
        if DATA.exists() and any(DATA.iterdir()):
            raise SystemExit("Refusing to initialize a nonempty data directory without PG_VERSION")
        with tempfile.NamedTemporaryFile(mode="w", dir=LOCAL) as password:
            password.write("probe_local\n")
            password.flush()
            subprocess.run([
                str(binaries / "initdb"), "-D", str(DATA), "-U", "probe",
                "--auth-local=trust", "--auth-host=scram-sha-256",
                "--pwfile", password.name, "--encoding=UTF8", "--locale=C",
            ], check=True)
    running = subprocess.run([pg_ctl, "-D", str(DATA), "status"], capture_output=True).returncode == 0
    if not running:
        with socket.socket() as check_port:
            try:
                check_port.bind(("127.0.0.1", args.port))
            except PermissionError:
                raise
            except OSError:
                raise SystemExit(f"Port {args.port} is busy; use --port and update .env")
        sockets = LOCAL / "pg-socket"
        sockets.mkdir(exist_ok=True)
        subprocess.run([
            pg_ctl, "-D", str(DATA), "-l", str(LOCAL / "postgres.log"),
            "-o", f"-h 127.0.0.1 -p {args.port} -k {sockets}", "-w", "start",
        ], check=True)
    with psycopg.connect(
        host="127.0.0.1", port=args.port, user="probe", password="probe_local",
        dbname="postgres", connect_timeout=3, autocommit=True,
    ) as connection:
        for name in ["probe", "probe_test", "probe_platform", "probe_platform_test"]:
            if connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
                connection.execute(sql.SQL("CREATE DATABASE {} OWNER probe").format(sql.Identifier(name)))
        print(connection.execute("SELECT version()").fetchone()[0])
    print(f"Ready: 127.0.0.1:{args.port}; order and platform databases ready; data in {DATA}")


if __name__ == "__main__":
    main()
