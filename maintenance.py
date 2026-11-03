"""Offline maintenance for recoverable updates and restore; full source included.

Stop ALL inventory server windows before restore. --servers-stopped is an
explicit operator assertion, not proof of process state; a restore additionally
checks all known development/LAN ports and refuses if any are listening.
"""

import argparse
import json
import os
from pathlib import Path
import socket

from app import backups,database

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["backup","restore","check"])
    parser.add_argument("--from-file",type=Path)
    parser.add_argument("--servers-stopped",action="store_true")
    args=parser.parse_args()
    path=Path(os.environ.get("INVENTORY_DB",ROOT/"data"/"inventory.sqlite3")).resolve()
    if args.action=="backup":print(backups.create(path));return
    if args.action=="check":
        with database.connect(path) as connection:print(connection.execute("PRAGMA integrity_check").fetchone()[0])
        return
    if not args.servers_stopped or not args.from_file:parser.error("Restore needs --servers-stopped and --from-file.")
    endpoints=[("127.0.0.1",port) for port in range(8000,8010)]
    network=ROOT/"data"/"network.json"
    if network.exists():
        config=json.loads(network.read_text());endpoints.append((config["address"],config["port"]))
    for endpoint in endpoints:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(endpoint)==0:parser.error(f"A server is still listening at {endpoint}. Stop it before restore.")
    print(backups.restore_offline(path,args.from_file))
    database.initialize(path)
    print("Restore verified and schema checked. Existing sessions were signed out.")


if __name__=="__main__":main()
