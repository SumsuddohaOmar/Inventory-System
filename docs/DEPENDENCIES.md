# Free/open-source components

The application source is fully available in this folder. No service credentials,
cloud account or paid package is required. Third-party components retain their
licenses; source access does not transfer their copyrights.

| Component | Role | Upstream license / source |
|---|---|---|
| Python | Runtime and standard library | PSF — https://www.python.org/psf/license/ |
| SQLite | Local database, bundled with Python | Public domain — https://sqlite.org/copyright.html |
| FastAPI | HTTP API and validation integration | MIT — https://github.com/fastapi/fastapi |
| Uvicorn | Local HTTP server | BSD-3-Clause — https://github.com/Kludex/uvicorn |
| Pydantic | Input/output models | MIT — https://github.com/pydantic/pydantic |
| Starlette | FastAPI's web toolkit | BSD-3-Clause — https://github.com/Kludex/starlette |
| HTTPX | Development/test HTTP client | BSD-3-Clause — https://github.com/encode/httpx |

`requirements-lock.txt` records all installed versions, including transitive
dependencies. Their distribution metadata includes individual license details.
No third-party source has been copied into our app. Review dependency licenses
again when packaging/distributing a release; this is an initial inventory.

Learning references used for the implementation:

- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)

The preview adds no mandatory Python dependencies. XLSX/CSV handling uses the
Python standard library; it never executes workbook formulas or macros. HTTPS
preparation invokes an already-installed OpenSSL (optional, not bundled), whose
license depends on that distribution. Node.js is optional for developer checks.

The small original QR encoder implements byte-mode version 3-L. Design references:

- [DENSO WAVE QR versions](https://www.qrcode.com/en/about/version.html)
- [Nayuki QR algorithm reference](https://github.com/nayuki/QR-Code-generator)

Only the narrow encoder needed for a local server URL is implemented. Algebraic
tests pass; real-device camera decoding is still pending.
