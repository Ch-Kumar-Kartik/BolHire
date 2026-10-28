# BolHire

Project state and verification results are recorded in [docs/progress.md](docs/progress.md).

Optional PostgreSQL setup, migrations, and disposable local integration tests are documented in [docs/database-development.md](docs/database-development.md).

## Deployment configuration

Keep `BOLHIRE_DEV_PROFILE_INSPECTION` unset or explicitly set to `0` in every deployment. Never deploy with the local profile inspector enabled. It is only for direct local development verification; see [local verification instructions](docs/local-profile-verification.md).
