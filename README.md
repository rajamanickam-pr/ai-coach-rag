# AI Coach RAG

Document-grounded chat with PostgreSQL/pgvector, persistent user conversations, and database-backed RBAC.

## Local setup

1. Local `.env` is configured for development with username `raja`, email `raja@gmail.com`, and password `12354`. This weak password is development-only; never use it outside a private local environment. For production set `APP_ENV=production` and provide a unique password of at least 12 characters.

```bash
cp .env.example .env
```

2. Start PostgreSQL 16 with pgvector from the repository root:

```bash
sudo docker compose up -d postgres
```

3. Install the Python project and apply migrations:

```bash
uv sync --extra test
uv run alembic upgrade head
```

If using the already-created `.venv` instead of uv, install the project with `pip install -e '.[test]'` and run `.venv/bin/alembic upgrade head`.

4. Install/run Ollama locally and fetch the configured embedding model:

```bash
ollama pull nomic-embed-text
```

Ollama must be reachable at `OLLAMA_BASE_URL` (default `http://localhost:11434`). The configured embedding model must return 768-dimensional vectors. Scanned PDFs need OCR before upload; automatic OCR is not included.

5. Start the API and client in separate terminals:

```bash
uv run uvicorn server.main:app --host 0.0.0.0 --port 8000
```

```bash
cd client && npm install && npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173`. The admin account is created on API startup from `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_EMAIL`, and `BOOTSTRAP_ADMIN_PASSWORD`. Admins can create member/admin accounts in the Admin view. Members can create conversations and read documents; document upload/delete and account management require the admin role.

## Connect with pgAdmin

Register a server in pgAdmin (right-click **Servers** > **Register** > **Server**). Use a display name such as `AI Coach Local`; under **Connection**, enter:

| Setting | Value |
| --- | --- |
| Host name/address | `localhost` |
| Port | `5433` |
| Maintenance database | `ai_coach` |
| Username | `ai_coach` |
| Password | `local_dev_password` |

These are the local Compose development credentials. If pgAdmin itself runs in Docker on the same Compose network, use host `postgres` and port `5432` instead. Do not expose the development database port or credentials in production.

## Persistence and privacy

- PostgreSQL stores roles, users, password hashes, hashed/revocable login sessions, conversations, messages, document metadata, and 768-dimension vectors.
- Conversation reads and writes are always scoped to the authenticated owner. Model memory is limited to the latest 12 messages in the selected conversation; transcripts from other conversations are not sent to the model.
- Login uses HttpOnly, SameSite=Strict cookies, a CSRF token for mutations, Argon2id password hashes, and a shared database-backed five-failure/15-minute throttle.
- Uploaded files are stored under `data/uploads/`; only their metadata and chunks are in PostgreSQL. The upload directory is ignored by Git.
- PostgreSQL data persists in the `ai_coach_postgres_data` Docker volume. Back it up independently of the application.

## Production deployment

Use a managed PostgreSQL service or a secured, backed-up PostgreSQL deployment. Set `APP_ENV=production`, replace all example credentials (including `12354`), restrict network access, terminate TLS at a trusted reverse proxy, set `SESSION_COOKIE_SECURE=true`, configure exact `CORS_ORIGINS`, and provide `DATABASE_URL`, `BOOTSTRAP_ADMIN_*`, `ANTHROPIC_API_KEY`, and Ollama service configuration through a secret manager. Use a dedicated database role with only required privileges. Run Alembic migrations as a deployment step before starting API workers; do not use the Compose development password in production.

Configure `LOG_LEVEL` as needed. The API emits structured JSON request/action logs with request IDs, status, durations, and relevant identifiers; secrets and prompt text are not logged. `/api/health` checks PostgreSQL connectivity.

## Tests

```bash
uv run pytest
cd client && npm run build && npm run lint
```

The database-backed API flows can be tested against the local Compose database. PDF validation and auth helper tests do not require Ollama or Anthropic; a real embedding/answer requires both services.
