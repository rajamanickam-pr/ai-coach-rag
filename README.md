# AI Coach RAG

## Project layout

- `server/` contains the FastAPI app, RAG service, and ingestion/retrieval pipeline.
- `client/` contains the React/Vite interface.
- `data/` contains the source PDF; `collection_db/` stores the Chroma index.
- `src/` retains the original experiments and notebook, separate from the application backend.

## Run locally

Start the API from the repository root:

```bash
.venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

In a second terminal, start the web client:

```bash
cd client
npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173`. Set `ANTHROPIC_API_KEY` in the environment or the root `.env` file before asking questions.
