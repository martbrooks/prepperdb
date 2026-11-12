# Release notes

## v0.1 — first public release

PrepperDB becomes a public, self-hosted household supplies app, developed using AI coding tools. AI-generated and human-written contributions are welcome.

Included: the four inventory views; local accounts; manual entry and optional Open Food Facts lookup; locally bundled barcode scanning; batches/multipacks; best-before/use-by dates; categories; product photos with editing; quick stock changes and 32-day History/Undo; dark/light themes; bundled artwork/icons; getting-started, operation and deployment guides; app screenshots; HTTP and HTTPS nginx/systemd examples.

Production defaults to HTTPS; HTTP is available explicitly through `PREPPERDB_HTTPS=0`. The new interactive `user_account` command simplifies creating/resetting/disabling accounts without exposing passwords in shell arguments.

Designed for a household and light concurrency on one SQLite instance. Every account shares the same inventory. No private tenants, admin roles, public signup, server-database support, automatic backup scheduling, CSV export or container image are provided. Live camera access depends on browser permissions and a trusted secure context. Locks target Python 3.13/Linux x86-64.

The public project starts with a clean import of the existing application and graphics. Earlier implementation history and household deployment details remain private. No household database or secrets are part of the release. Public development, suggestions and patches belong on GitHub; private household deployment remains separately controlled.
