"""Prepare a private local CA and HTTPS certificate using an existing OpenSSL.

This does NOT install trust certificates, change firewall/network settings, or
enable network access unless --enable is explicitly supplied. Copy only ca.crt
to Android; ca.key/server.key must stay private on the Windows server.
"""

import argparse
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parent


def openssl_path():
    """Prefer PATH, then the OpenSSL bundled with an existing Git for Windows."""
    found=shutil.which("openssl")
    if found:return found
    # Prefer Git's native Windows build. Its usr/bin version depends on the
    # MSYS runtime and may fail before OpenSSL even starts in a normal shell.
    candidate=Path("C:/Program Files/Git/mingw64/bin/openssl.exe")
    if candidate.is_file():return str(candidate)
    raise RuntimeError("OpenSSL was not found. Install a free trusted OpenSSL distribution or Git for Windows, then rerun.")


def configure(address,port=8443,enable=False):
    """Generate certificates for one private/loopback IPv4 address, without trust changes."""
    ip=ipaddress.ip_address(address)
    if ip.version!=4 or not(ip.is_private or ip.is_loopback):raise ValueError("Use the Windows laptop's local IPv4 address.")
    if not 1024<=port<=65535:raise ValueError("Choose a port between 1024 and 65535.")
    folder=ROOT/"data"/"tls";folder.mkdir(parents=True,exist_ok=True)
    openssl=openssl_path()
    def run(*arguments):subprocess.run([openssl,*map(str,arguments)],cwd=folder,check=True,capture_output=True)
    ca_key=folder/"ca.key";ca_cert=folder/"ca.crt"
    if ca_key.exists()!=ca_cert.exists():raise RuntimeError("Incomplete CA files. Recover the original CA instead of silently replacing trust.")
    if not ca_key.exists():
        run("req","-x509","-newkey","rsa:3072","-nodes","-sha256","-days","3650","-subj","/CN=Local Inventory Private CA",
            "-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,keyCertSign,cRLSign","-keyout",ca_key,"-out",ca_cert)
    extension=folder/"server.ext"
    extension.write_text(f"basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1,IP:{address}\n",encoding="ascii")
    run("req","-new","-newkey","rsa:2048","-nodes","-sha256","-subj","/CN=Local Inventory","-keyout",folder/"server.key","-out",folder/"server.csr")
    run("x509","-req","-in",folder/"server.csr","-CA",ca_cert,"-CAkey",ca_key,"-CAcreateserial","-days","365","-sha256","-extfile",extension,"-out",folder/"server.crt")
    run("verify","-CAfile",ca_cert,folder/"server.crt")
    configuration=dict(enabled=enable,address=address,port=port,certificate="tls/server.crt",key="tls/server.key",ca="tls/ca.crt")
    (ROOT/"data"/"network.json").write_text(json.dumps(configuration,indent=2),encoding="utf-8")
    return configuration


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address",required=True,help="Laptop's current local IPv4 address")
    parser.add_argument("--port",type=int,default=8443)
    parser.add_argument("--enable",action="store_true",help="Opt in to HTTPS on this LAN address when starting the app")
    arguments=parser.parse_args()
    result=configure(arguments.address,arguments.port,arguments.enable)
    print(f"Certificate verified. Address: https://{result['address']}:{result['port']}")
    print("Network mode is enabled." if result["enabled"] else "Network mode remains disabled. Configure device trust before enabling it.")
    print("Install ONLY data/tls/ca.crt as a trusted CA on your own devices. Never copy the private keys.")
