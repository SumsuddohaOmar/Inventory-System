"""Initialize the first administrator for a fresh hosted inventory database."""

from getpass import getpass
import os
from pathlib import Path

from app import accounts, database
from app.postgres import PostgresTarget


def main():
    # Require an explicit destination so this cannot accidentally open live data.
    dsn = os.environ.get("INVENTORY_POSTGRES_URL")
    path = PostgresTarget(dsn) if dsn else Path(os.environ["INVENTORY_DB"]).resolve()
    username = input("Administrator username: ")
    password = getpass("Administrator password: ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords differ.")
    if not dsn:
        database.initialize(path)
    try:
        accounts.create_user(path, username, password, first=True)
    except accounts.AccountError as error:
        raise SystemExit(str(error)) from error
    print("Administrator created.")


if __name__ == "__main__":
    main()
