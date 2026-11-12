# Operating PrepperDB

## SQLite and multiple users

v0.0.1 is designed for one household and light concurrent access. Multiple local user accounts are supported, **but every user has full access to the same inventory**. There are no read-only roles, private inventories or tenant isolation. Do not offer this instance to unrelated households expecting privacy from one another.

SQLite allows many readers but only one writer at a time. PrepperDB uses WAL, short IMMEDIATE transactions, a five-second lock timeout and FULL synchronisation. Those choices help occasional users take turns; they do not make it a busy multiuser service. Concurrent writes can produce lock errors at higher load. Use one Waitress process with two threads as supplied. Do not add multiple replicas or mount the database over NFS/SMB. Keep SQLite, its WAL and shared-memory files together on local disk.

For a busy club, organisation or public multiuser service, assess a server database and load-test the application before adopting it. PostgreSQL support is **not implemented or validated** in v0.0.1; changing Django's database ENGINE is not sufficient proof that the queries, constraints, migration history or concurrency behaviour work there. There is no guaranteed user-count limit: write frequency matters more than how many accounts exist. [SQLite's guidance](https://www.sqlite.org/whentouse.html) and [WAL requirements](https://www.sqlite.org/wal.html) explain these constraints.

## Accounts

There is no default account, public signup, admin website or email password recovery. On the reference installation:

```sh
sudo prepperdb-manage user_account alex
sudo prepperdb-manage user_account alex --disable
sudo prepperdb-manage user_account alex --enable
```

The first command creates an account, or replaces an existing account's password after prompting twice. Password validation requires at least 12 characters and rejects common/numeric passwords. Passwords are salted and hashed; no plaintext password goes in command arguments or SQL. Replacement rejects existing authenticated sessions on their next request. It does not reactivate a disabled account; use `--enable` explicitly if intended.

Disabling rejects existing sessions while the account is disabled. Re-enabling without changing the password may make an unexpired old session usable again, so reset the password when permanent revocation is required. Account disable/enable flags do not prompt for a password; use them only with host administration access. All accounts can modify/delete shared stock.

The older `password_hash` command remains available for manual database administration. Prefer `user_account` for ordinary setup rather than copying SQL. Never share the database, account hashes, environment or session cookies in a public issue.

## Backups

The SQLite database contains stock, accounts and **authoritative image bytes**. The generated `static/products/` files are a disposable cache and can be recreated from the database by authenticated application use. The bundled artwork/source can be obtained again from the release; they are not a substitute for backing up the database. Back up protected configuration and your secret key separately through your own secure mechanism.

Use SQLite's backup API or `.backup`, or a correctly coordinated offline backup. Copying only a live `stock.sqlite3` file can miss committed data still in its WAL. Example consistent backup on the reference installation:

```sh
sudo install -d -o prepperdb -g prepperdb -m 0700 /var/lib/prepperdb/backups
sudo -u prepperdb sqlite3 /var/lib/prepperdb/stock.sqlite3 ".backup '/var/lib/prepperdb/backups/before-upgrade.sqlite3'"
sudo -u prepperdb sqlite3 /var/lib/prepperdb/backups/before-upgrade.sqlite3 'PRAGMA integrity_check;'
```

Choose a new backup filename per backup so you do not overwrite your only recovery point. The integrity result should be `ok`. Copy backups to independent private storage; a backup beside the database does not protect against VM/disk loss. Supply your own schedule, retention, encryption and monitoring. v0.0.1 does not include backup scheduling or CSV export.

Before a restore, stop the app **and** the history timer/service and any management writers. Validate the backup; retain the existing main/WAL/SHM files together as a separate recovery set before replacing them. Restore the verified database with private app-account ownership and permissions. Never combine an old main file with leftover WAL/SHM files from another database. Keep a copy of the matching application version and environment. Run migrations only when intentionally upgrading, restart the app/history timer, and verify login, stock totals, dates and images. Test this recovery on a separate instance before relying on it. v0.0.1's tests verify consistent checkpoints and populated forward migrations; they do not establish your infrastructure backup or disaster recovery.

## Upgrades

Read the release notes first. Take and verify a consistent backup. Stop writers, retain the previous code/environment and install the selected release in a separate directory or carefully replace source without replacing state. Recreate/install the locked virtual environment, then run `migrate --noinput` and `collectstatic --noinput` with your **existing** protected production environment as the app account. Restart services and verify login and stock workflows. Do not copy an example environment over your real secret or replace your database with an empty one.

If migration fails, keep the service stopped, preserve the backup and diagnose the error. Rolling back code across a schema change may be unsafe; compatible forward fixes are preferable until you have tested a complete version-matched restore. Do not claim an untested rollback is safe. A public code release or accepted pull request does not automatically deploy to your VM.

## Housekeeping and troubleshooting

The supplied hourly `prepperdb-history.timer` runs `prune_stock_history`. History reads and stock writes also prune old records; this is not a backup or a database VACUUM. Expired sessions can be removed with `sudo prepperdb-manage clearsessions`.

```sh
sudo systemctl status prepperdb nginx prepperdb-history.timer --no-pager
sudo journalctl -u prepperdb --since '1 hour ago'
sudo nginx -t
sudo prepperdb-manage check
```

- **502:** check the app unit, socket path/group, parent directory permissions and nginx worker group membership.
- **403 CSRF / redirect loops:** check exact host, scheme and port, overwrite proxy headers, and configure the correct trusted WSGI peer. Do not disable CSRF.
- **413 photo upload:** every proxy hop needs an 11 MiB request envelope. Individual photos remain limited to 10 MiB and 12.5 million pixels.
- **Static 404:** run collectstatic with the release's environment, check docroot permissions and nginx's location. Only `static/` should be public, never `/var/lib/prepperdb`, `.env`, backups or the whole source tree.
- **Database locked:** check for concurrent management writers and long transactions; do not delete WAL/journal files to clear a lock. Review the workload against the SQLite limits above.
- **Lookup unavailable:** use manual entry or a saved local product. Optional Open Food Facts coverage, network access and rate limits do not affect offline stock management.

Keep the base OS and public-facing nginx/Python dependencies patched using your host's maintenance policy. The v0.0.1 lock is reproducible, not a promise of automatic dependency upgrades. Follow future releases and test dependency updates. For a Proxmox/KVM VM, install/start `qemu-guest-agent` if your hypervisor exposes its channel and configure automatic OS security updates; those host-level actions are separate from app installation.
