"""Install the pilot schema into a newly created, empty PostgreSQL database."""

from getpass import getpass
from pathlib import Path

import psycopg

from app import accounts
from app.postgres import PostgresTarget


def main():
    dsn = getpass("Empty pilot database connection URL: ")
    username = input("Pilot administrator username: ")
    password = getpass("Pilot administrator password: ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords differ.")
    # Validate the password before installing the schema.
    accounts.password_hash(password)
    schema = (Path(__file__).resolve().parent / "cloud" / "schema.sql").read_text(encoding="utf-8")
    # The connection context commits the full schema together.
    statements = schema.removeprefix("BEGIN;").strip().removesuffix("COMMIT;")
    with psycopg.connect(dsn, connect_timeout=10) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(171047112)")
        tables = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        if tables:
            raise SystemExit("This database already has tables. Use a new pilot database.")
        connection.execute(statements)
    accounts.create_user(PostgresTarget(dsn), username, password, first=True)
    print("Pilot schema and administrator created.")


if __name__ == "__main__":
    main()
