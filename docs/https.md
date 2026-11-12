# HTTP and HTTPS

HTTPS is **recommended and enabled by default in production**, but it is optional. A local loopback preview defaults to HTTP. You can also run a permanent HTTP installation by explicitly setting `PREPPERDB_HTTPS=0`; no development key or debug mode is needed.

| Choice | App configuration | Browser behaviour |
| --- | --- | --- |
| HTTPS | `PREPPERDB_HTTPS=1` (production default) | HTTPS redirect, Secure session/CSRF cookies and HSTS |
| HTTP | `PREPPERDB_HTTPS=0` | No HTTPS redirect, cookies usable over HTTP, no HSTS |

On HTTP, network observers can read or modify passwords, sessions and stock traffic. Use HTTPS whenever clients cross an untrusted network. Browsers normally restrict live camera access to secure contexts; HTTP on a phone over the LAN is not localhost. Manual entry and choosing a barcode image do not need live camera access. See [browser requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

## HTTP setup

Complete [deployment](deployment.md), set `PREPPERDB_HTTPS=0`, and install the HTTP configuration:

```sh
sudo install -m 0644 /opt/prepperdb/deploy/examples/nginx-http.conf /etc/nginx/sites-available/prepperdb
sudoedit /etc/nginx/sites-available/prepperdb
sudo ln -s /etc/nginx/sites-available/prepperdb /etc/nginx/sites-enabled/prepperdb
sudo nginx -t
sudo systemctl restart prepperdb nginx
```

Replace `stock.example.org` with your chosen hostname/IP and keep it in `PREPPERDB_ALLOWED_HOSTS`. If the symlink already exists, retain it rather than creating another. The HTTP frontend needs no certificate. It still uses the private Unix socket and serves only the static docroot publicly. Other nginx rules, an outer proxy, or previously stored HSTS can still cause a browser to use HTTPS.

## HTTPS with an existing certificate

Use a certificate covering the hostname(s) people will enter. Install its full certificate chain and private key into protected files on the nginx host; the private key must be root-readable, not world-readable. The examples expect `/etc/prepperdb/tls/fullchain.pem` and `/etc/prepperdb/tls/privkey.pem`:

```sh
sudo install -d -m 0700 /etc/prepperdb/tls
sudo install -m 0644 /path/to/fullchain.pem /etc/prepperdb/tls/fullchain.pem
sudo install -m 0600 /path/to/privkey.pem /etc/prepperdb/tls/privkey.pem
sudo install -m 0644 /opt/prepperdb/deploy/examples/nginx-https.conf /etc/nginx/sites-available/prepperdb
sudoedit /etc/nginx/sites-available/prepperdb
sudo ln -s /etc/nginx/sites-available/prepperdb /etc/nginx/sites-enabled/prepperdb
sudo nginx -t
sudo systemctl restart prepperdb nginx
```

Keep `PREPPERDB_HTTPS=1`, edit server names/certificate paths, and retain an existing enabled-site symlink if present. Arrange certificate renewal and nginx reload with your certificate provider. A public hostname can use an ACME provider such as Let's Encrypt; follow its current issuance/renewal instructions and ensure its DNS or HTTP challenge can succeed. An HTTP challenge may need a dedicated challenge location or temporarily adjusted redirect. PrepperDB does not issue or renew certificates itself. [nginx HTTPS reference](https://nginx.org/en/docs/http/configuring_https_servers.html).

## LAN certificates

For a private LAN name, use a private CA and install its trust root on each browser/device that needs trusted HTTPS. A self-signed leaf can encrypt traffic after an explicit browser exception, but it is not automatically trusted; some devices/browsers will still block camera features. Do not promise camera compatibility merely because a URL begins with `https://`.

For a temporary self-signed certificate with OpenSSL 3 (replace the example name everywhere):

```sh
sudo install -d -m 0700 /etc/prepperdb/tls
sudo openssl req -x509 -newkey rsa:3072 -noenc -days 90 \
  -keyout /etc/prepperdb/tls/privkey.pem \
  -out /etc/prepperdb/tls/fullchain.pem \
  -subj /CN=stock.example.test \
  -addext subjectAltName=DNS:stock.example.test \
  -addext basicConstraints=critical,CA:FALSE
sudo chmod 0600 /etc/prepperdb/tls/privkey.pem
```

This example neither creates a CA nor distributes browser trust. Never commit a private key. For a separate edge proxy, trust and verify the origin certificate there too; see [deployment](deployment.md#optional-separate-public-edge-proxy).

## Changing an existing HTTPS installation to HTTP

Set `PREPPERDB_HTTPS=0`, switch the nginx frontend to HTTP and restart both services. HSTS sent by the prior HTTPS installation may remain in browsers for up to a year; a browser can continue upgrading that hostname to HTTPS after the server changes. Prefer retaining HTTPS or using a different HTTP-only hostname. Clearing HSTS on one browser is not a deployment-wide migration. Renew/reissue sessions as needed, and check every outer proxy for redirects.

Django's [HTTPS settings](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-ssl-redirect) explain the redirects, HSTS and cookie controls. PrepperDB ties those controls to one explicit setting, while keeping strong production secrets, host validation and CSRF protection required in either mode.
