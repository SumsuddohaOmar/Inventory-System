"""Run the PostgreSQL pilot behind Render's HTTPS proxy."""

import os
from urllib.parse import urlsplit

import uvicorn


def main():
    hostname = os.environ["RENDER_EXTERNAL_HOSTNAME"]
    if not hostname.endswith(".onrender.com"):
        raise SystemExit("The pilot requires a Render hostname.")
    dsn = urlsplit(os.environ["INVENTORY_POSTGRES_URL"])
    if dsn.scheme not in {"postgres", "postgresql"} or not dsn.hostname:
        raise SystemExit("Configure a dedicated pilot PostgreSQL database.")
    os.environ["INVENTORY_ALLOWED_HOSTS"] = hostname
    os.environ["INVENTORY_PUBLIC_ORIGIN"] = f"https://{hostname}"
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ["PORT"]),
                proxy_headers=False)


if __name__ == "__main__":
    main()
