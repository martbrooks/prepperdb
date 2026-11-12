# PrepperDB contributor instructions

PrepperDB is an AI-generated household stock application. AI-assisted and AI-generated contributions are welcome. Explain the problem, describe the changes and report tests actually run. Never claim validation without evidence.

- Use GitHub issues and pull requests for development. Keep changes focused and use British English.
- Public documentation lives in `README.md` and `docs/`; update it when behaviour or setup changes.
- Preserve offline manual stock entry, local assets, authentication, CSRF checks and transactional quantities.
- SQLite is a deliberate household-workload constraint; all accounts share the same inventory.
- Keep HTTPS enabled by default in production. HTTP must be an explicit choice; never weaken authentication to enable it.
- Never commit databases, real product photos, credentials, environment files or private deployment details.
- Preserve bundled asset licences and provenance; include artwork and notices in packages.
- Run the checks in `CONTRIBUTING.md` appropriate to the change.
- No automatic deployment follows an external contribution. Household infrastructure and credentials remain private.
