# Android and local HTTPS

The laptop must be on and reachable to synchronize. Offline use requires an
initial sign-in/catalog load and a trusted HTTPS installation. Device acceptance
has not yet been completed.

Use the laptop's private IPv4 address, preferably stable through a router DHCP
reservation. Changing address/port creates a different browser origin and does
not move pending entries. Synchronize first. Default mode stays loopback-only.

With an installed OpenSSL (the helper finds native Git-for-Windows OpenSSL),
prepare certificates using the actual laptop address:

```powershell
.\.venv\Scripts\python.exe configure_https.py --address 192.168.1.10
```

This generates `data/tls/ca.crt`, private keys, a server certificate and a disabled
network configuration. It does not install trust or change Windows settings.
The server certificate lasts one year. Rerunning keeps the CA and renews the
server certificate for the supplied address.

Transfer **only `data/tls/ca.crt`** to your phone and install it as a trusted CA
using Android certificate settings. Trust it in your Windows browser's required
trust store too. Settings vary by device and organizational policy. Never copy
`ca.key` or `server.key`. Do not treat a browser certificate warning as a completed
HTTPS setup.

After trust is configured, explicitly enable network mode:

```powershell
.\.venv\Scripts\python.exe configure_https.py --address 192.168.1.10 --enable
```

Stop old servers and reopen Start Inventory.cmd. If Windows blocks connections,
allow the server on your trusted private network. No OS trust/firewall choices
have been changed automatically.

On Android open `https://192.168.1.10:8443`, replacing the example address.
The app connection panel also shows the URL and QR; localhost cannot connect
another device. Sign in, wait for the catalog and use the browser's installation
option if offered. Actual installation and QR decoding still need device tests.

Pending acceptance sequence, using sample products:

1. Confirm opening 100 + receive 20 − issue 15 = 105.
2. Load history, disconnect Wi-Fi, queue an issue, close/reopen the app and
   verify it survives. Reconnect and confirm it posts once.
3. Create a competing issue elsewhere. The conflict must be held and dependent
   entries must wait; an authorized reviewer resolves it.
4. Revoke a user's write permission before reconnecting. Their queued entry must
   be held without a stock change.
5. Test future drafts, storage pressure, PIN idle lock/recovery, account isolation,
   laptop restart, restore and app updates with pending work.

Persistent storage is requested where supported, but browsers can still clear
data. Synchronize before clearing storage, uninstalling or changing addresses.
Server backups cannot recover unsent phone entries. The optional PIN protects
the app screen, not the underlying browser files.
