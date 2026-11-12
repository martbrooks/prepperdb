# Deploying PrepperDB

The reference architecture is deliberately small: **one Debian 13 VM → nginx → one Waitress process → SQLite on local disk**. A separate edge proxy may sit in front. Proxmox is optional: an ordinary Linux host/VM works. There is no container image in v0.1. The examples use systemd and Debian's `www-data` nginx account; adapt these if your distribution differs.

The original household instance has one vCPU and about 1 GiB RAM. This is a useful starting point, not a guaranteed capacity limit. Size disk space for stock images, database growth and backups. Start with one app instance; see [SQLite and multiple users](operations.md#sqlite-and-multiple-users).

## 1. Prepare the host and application

On a fresh Debian 13 host:

```sh
sudo apt-get update
sudo apt-get install python3-venv sqlite3 nginx ca-certificates
sudo groupadd --system prepperdb-web
sudo useradd --system --user-group --home-dir /var/lib/prepperdb --shell /usr/sbin/nologin prepperdb
sudo usermod -a -G prepperdb-web prepperdb
sudo usermod -a -G prepperdb-web www-data
sudo install -d -m 0755 /opt/prepperdb /srv/prepperdb/static
sudo install -d -o prepperdb -g prepperdb -m 0700 /var/lib/prepperdb
sudo install -d -o root -g prepperdb -m 0750 /etc/prepperdb
sudo chown prepperdb:prepperdb /srv/prepperdb/static
```

These account commands are for a new host; do not recreate existing accounts or overwrite an existing installation. Download and extract the **v0.1 source archive**. From its extracted directory, copy the application into `/opt/prepperdb` (the application directory, not a parent containing another nested release directory):

```sh
sudo cp -a . /opt/prepperdb/
sudo chown -R root:root /opt/prepperdb
sudo python3.13 -m venv /opt/prepperdb/.venv
sudo /opt/prepperdb/.venv/bin/python -m pip install --require-hashes -r /opt/prepperdb/requirements.lock
```

The source directory must not contain your own database or environment file before copying. For a release with offline wheels, `--no-index --find-links /path/to/wheels` can be added to pip; wheels must match the supplied lock and platform. The source release requires package download access during installation. Runtime assets are local.

## 2. Configure the app

```sh
sudo install -o root -g prepperdb -m 0640 /opt/prepperdb/deploy/examples/runtime.env.example /etc/prepperdb/runtime.env
sudoedit /etc/prepperdb/runtime.env
```

Generate a unique secret with `python3.13 -c 'import secrets; print(secrets.token_urlsafe(64))'` in your own terminal. Put it in `PREPPERDB_SECRET_KEY`; never commit or share that value. Set `PREPPERDB_ALLOWED_HOSTS` to the exact hostnames/IPs you will use, comma-separated, with **no scheme, port or wildcard**. Keep the state/static/socket paths from the example for these instructions. Keep `PREPPERDB_DEVELOPMENT` unset.

Choose **HTTPS** (default, `PREPPERDB_HTTPS=1`) or **HTTP** (`PREPPERDB_HTTPS=0`) before enabling nginx. [HTTPS setup](https.md) explains certificates and the consequences of HTTP. Both choices retain password authentication, CSRF protection and DEBUG=False.

`PREPPERDB_TRUSTED_PROXIES=localhost` is for this permission-restricted Waitress Unix socket. It does not mean “trust every network client”: do not expose the app socket or accept these headers directly from users. The nginx example overwrites `X-Prepperdb-Proto` and `X-Prepperdb-Client-IP` and clears standard forwarded headers. If adapting to TCP or another WSGI server, verify its peer address before changing this trust list.

For deliberately different browser origins or nonstandard ports, `PREPPERDB_CSRF_TRUSTED_ORIGINS` accepts exact comma-separated origins such as `https://stock.example.org:8443`. Normal same-origin setups do not need it. Configure nginx to preserve the browser Host including its port.

## 3. Initialise and create an account

```sh
sudo install -o root -g root -m 0755 /opt/prepperdb/deploy/examples/prepperdb-manage /usr/local/sbin/prepperdb-manage
sudo prepperdb-manage migrate --noinput
sudo prepperdb-manage collectstatic --noinput
sudo prepperdb-manage user_account alex
```

The management wrapper loads your protected environment and runs Django as the app account. It does not print credentials. There is no default/test account and no open registration. `user_account` prompts privately; use the same command to replace a forgotten password. All accounts share the inventory. [Account administration](operations.md#accounts).

For HTTPS, also run `sudo prepperdb-manage check --deploy --fail-level WARNING`. An explicitly HTTP deployment will report expected warnings about redirects, secure cookies and HSTS when running Django's deployment checker: these are real HTTP limitations, not reasons to silence all checks.

## 4. Install services and nginx

```sh
sudo install -m 0644 /opt/prepperdb/deploy/examples/prepperdb.service /etc/systemd/system/
sudo install -m 0644 /opt/prepperdb/deploy/examples/prepperdb-history.service /etc/systemd/system/
sudo install -m 0644 /opt/prepperdb/deploy/examples/prepperdb-history.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prepperdb.service prepperdb-history.timer
sudo install -m 0644 /opt/prepperdb/deploy/examples/nginx-app.conf /etc/nginx/snippets/prepperdb-app.conf
```

Install **one** frontend configuration: [HTTP or HTTPS](https.md). Change its `server_name` to match your hosts. On a dedicated fresh VM, disable Debian's default site if it would capture your requests; on a shared nginx host, keep unrelated sites untouched. Then:

```sh
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl status prepperdb.service prepperdb-history.timer --no-pager
```

Restarting nginx after changing its group membership ensures workers acquire access to the socket. Visit your configured URL, log in, add a disposable item, adjust/undo it and delete it. Check styling, image upload and the intended HTTP/HTTPS behaviour from an actual browser. Camera access requires a browser-trusted secure origin. Restrict firewall access appropriately; expose nginx, not the Unix socket or database.

## Optional separate public edge proxy

A layout resembling the original installation is:

```text
Browser → public edge nginx (HTTPS) → VM nginx (HTTPS) → private Unix socket → Waitress → local SQLite
LAN browser ───────────────────────→ VM nginx
```

`deploy/examples/external-proxy.conf` is a starting point with documentation-only names/IPs. Configure real DNS, certificates and routing yourself. It verifies the origin certificate against a supplied trusted CA; do not copy the private household certificate or disable verification to hide a trust error. A publicly trusted origin certificate or a private CA trusted by the edge are both possible. Add an HTTP-to-HTTPS redirect on the edge if needed.

To retain actual client addresses through the second nginx, configure **only the edge's exact address** as `set_real_ip_from` in the VM server block and use `real_ip_header X-Real-IP`. The edge must overwrite that header. Without this, login throttling sees the edge address and clients may share its attempt limit. Do not trust arbitrary incoming proxy headers or an entire LAN without reason. Permit the VM origin only from your intended LAN/edge clients. Keep the 11 MiB request limit at every proxy hop.

Production deployment remains your responsibility: the public repository and pull-request checks contain no access to an existing household VM. See [operations](operations.md) for updates, backups and troubleshooting.
