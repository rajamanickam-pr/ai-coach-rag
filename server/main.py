import hmac
import hashlib
import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool

from server.database import SessionLocal, engine, get_db
from server.models import Conversation, ConversationMessage, Document, LoginSession, LoginThrottle, Role, User
from server.pgvector_rag import answer_question_with_anthropic, hybrid_search, ingest_document
from server.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    clear_auth_cookies,
    get_current_user,
    hash_password,
    hash_session_token,
    issue_login_session,
    require_permission,
    seed_roles_and_bootstrap_admin,
    set_auth_cookies,
    verify_password,
)

class StructuredLogFormatter(logging.Formatter):
    fields = (
        "request_id",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "user_id",
        "session_id",
        "conversation_id",
        "document_id",
        "collection",
        "chunk_count",
        "file_size",
        "role",
        "email_domain",
    )

    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event.update({field: getattr(record, field) for field in self.fields if hasattr(record, field)})
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=True, default=str)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
for log_handler in logging.getLogger().handlers:
    log_handler.setFormatter(StructuredLogFormatter())
logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
COLLECTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,62}$")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads")).resolve()

app = FastAPI(title="AI Coach RAG API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    ).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=256)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(pattern="^(admin|member)$")


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    collection_name: str = Field(default="karumegam_collection", min_length=3, max_length=63)


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)
    collection_name: str = Field(default="karumegam_collection", min_length=3, max_length=63)


def validate_collection_name(collection_name: str) -> str:
    if not COLLECTION_NAME_PATTERN.fullmatch(collection_name):
        raise HTTPException(
            status_code=422,
            detail="Collection name must be 3-63 characters using letters, numbers, underscores, or hyphens.",
        )
    return collection_name


def require_csrf(request: Request, x_csrf_token: str | None = Header(default=None)) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not cookie_token or not x_csrf_token or not hmac.compare_digest(cookie_token, x_csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed. Refresh the page and try again.")


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = str(uuid4())
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled request exception",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        raise
    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        "request_complete",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


@app.on_event("startup")
def startup() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    with SessionLocal() as db:
        seed_roles_and_bootstrap_admin(db)


@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "vector_store": "postgresql-pgvector"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    client_host = request.client.host if request.client else "unknown"
    username = payload.username.lower()
    throttle_key = hashlib.sha256(f"{username}|{client_host}".encode("utf-8")).hexdigest()
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": throttle_key},
    )
    throttle = db.get(LoginThrottle, throttle_key)
    if throttle and throttle.blocked_until and throttle.blocked_until > now:
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Try again later.")

    user = db.scalar(
        select(User).where(func.lower(User.username) == username).options(joinedload(User.role))
    )
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        if throttle is None:
            throttle = LoginThrottle(key_hash=throttle_key, failed_attempts=1, window_started_at=now)
            db.add(throttle)
        elif throttle.window_started_at < now - timedelta(minutes=15):
            throttle.failed_attempts = 1
            throttle.window_started_at = now
            throttle.blocked_until = None
        else:
            throttle.failed_attempts += 1
        if throttle.failed_attempts >= 5:
            throttle.blocked_until = now + timedelta(minutes=15)
        db.commit()
        logger.warning("Login rejected")
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")

    if throttle:
        db.delete(throttle)
    db.execute(delete(LoginSession).where(LoginSession.expires_at < now))
    db.execute(delete(LoginThrottle).where(LoginThrottle.updated_at < now - timedelta(days=1)))
    token, csrf_token, session = issue_login_session(db, user)
    set_auth_cookies(response, token, csrf_token)
    logger.info("Login successful", extra={"user_id": str(user.id), "session_id": str(session.id)})
    return {
        "user": {"id": str(user.id), "username": user.username, "email": user.email, "role": user.role.name, "permissions": user.role.permissions},
        "csrf_token": csrf_token,
        "expires_at": session.expires_at,
    }


@app.get("/api/auth/me")
def auth_me(request: Request, user: User = Depends(get_current_user)):
    csrf_token = request.cookies.get(CSRF_COOKIE)
    return {
        "user": {"id": str(user.id), "username": user.username, "email": user.email, "role": user.role.name, "permissions": user.role.permissions},
        "csrf_token": csrf_token,
    }


@app.post("/api/auth/logout")
def logout(
    response: Response,
    request: Request,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(
            text("UPDATE login_sessions SET revoked_at = :now WHERE token_hash = :token_hash AND user_id = :user_id"),
            {"now": datetime.now(UTC), "token_hash": hash_session_token(token), "user_id": user.id},
        )
        db.commit()
    clear_auth_cookies(response)
    return {"status": "ok"}


@app.get("/api/collections")
def get_collections(
    user: User = Depends(require_permission("collections:read")),
    db: Session = Depends(get_db),
):
    names = db.scalars(select(Document.collection_name).distinct().order_by(Document.collection_name)).all()
    return {"collections": names or ["karumegam_collection"]}


@app.get("/api/admin/users")
def list_users(
    _actor: User = Depends(require_permission("users:manage")),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).options(joinedload(User.role)).order_by(User.username)).all()
    return {"users": [{"id": str(user.id), "username": user.username, "email": user.email, "role": user.role.name, "is_active": user.is_active} for user in users]}


@app.post("/api/admin/users", status_code=201)
def create_user(
    payload: UserCreateRequest,
    _csrf: None = Depends(require_csrf),
    _actor: User = Depends(require_permission("users:manage")),
    db: Session = Depends(get_db),
):
    username = payload.username.lower()
    email = payload.email.lower()
    if db.scalar(select(User.id).where((func.lower(User.email) == email) | (func.lower(User.username) == username))):
        raise HTTPException(status_code=409, detail="That username or email is already registered.")
    role = db.scalar(select(Role).where(Role.name == payload.role))
    user = User(username=username, email=email, password_hash=hash_password(payload.password), role_id=role.id)
    db.add(user)
    db.commit()
    logger.info("User created", extra={"user_id": str(user.id), "role": role.name})
    return {"id": str(user.id), "username": user.username, "email": user.email, "role": role.name}


@app.get("/api/conversations")
def list_conversations(
    user: User = Depends(require_permission("conversations:read")),
    db: Session = Depends(get_db),
):
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    ).all()
    return {
        "conversations": [
            {
                "id": str(conversation.id),
                "title": conversation.title,
                "collection_name": conversation.collection_name,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
            for conversation in conversations
        ]
    }


@app.post("/api/conversations", status_code=201)
def create_conversation(
    payload: ConversationCreateRequest,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:write")),
    db: Session = Depends(get_db),
):
    collection_name = validate_collection_name(payload.collection_name)
    conversation = Conversation(user_id=user.id, title=payload.title.strip(), collection_name=collection_name)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return {"id": str(conversation.id), "title": conversation.title, "collection_name": conversation.collection_name}


def get_owned_conversation(db: Session, conversation_id: UUID, user_id: UUID, lock: bool = False) -> Conversation:
    statement = select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    conversation = db.scalar(statement)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@app.get("/api/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    user: User = Depends(require_permission("conversations:read")),
    db: Session = Depends(get_db),
):
    conversation = get_owned_conversation(db, conversation_id, user.id)
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.sequence)
    ).all()
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "collection_name": conversation.collection_name,
        "messages": [
            {"id": str(message.id), "role": message.role, "content": message.content, "sources": message.sources, "created_at": message.created_at}
            for message in messages
        ],
    }


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: UUID,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:delete")),
    db: Session = Depends(get_db),
):
    conversation = get_owned_conversation(db, conversation_id, user.id)
    db.delete(conversation)
    db.commit()
    return Response(status_code=204)


@app.post("/api/conversations/{conversation_id}/ask")
def answer_question(
    conversation_id: UUID,
    payload: AskRequest,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:write")),
    db: Session = Depends(get_db),
):
    collection_name = validate_collection_name(payload.collection_name)
    conversation = get_owned_conversation(db, conversation_id, user.id, lock=True)
    if collection_name != conversation.collection_name:
        conversation.collection_name = collection_name

    last_sequence = db.scalar(
        select(func.max(ConversationMessage.sequence)).where(ConversationMessage.conversation_id == conversation.id)
    ) or 0
    history_rows = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.sequence.desc())
        .limit(12)
    ).all()
    history = [{"role": message.role, "content": message.content} for message in reversed(history_rows)]
    user_message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=last_sequence + 1,
        role="user",
        content=payload.question,
    )
    db.add(user_message)
    try:
        retrieved = hybrid_search(db, payload.question, collection_name, n_results=4)
        answer = answer_question_with_anthropic(payload.question, retrieved, history)
        sources = [
            {
                "id": item["id"],
                "source": item["metadata"].get("source", "unknown"),
                "chunk_index": item["metadata"].get("chunk_index", 0),
                "score": round(float(item["combined_score"]), 4),
                "text": item["document"][:600],
            }
            for item in retrieved
        ]
        db.add(
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=last_sequence + 2,
                role="assistant",
                content=answer,
                sources=sources,
            )
        )
        if last_sequence == 0:
            conversation.title = payload.question[:77] + ("..." if len(payload.question) > 80 else "")
        conversation.updated_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Question answering failed",
            extra={"user_id": str(user.id), "conversation_id": str(conversation_id)},
        )
        raise HTTPException(status_code=503, detail="Could not complete the answer. Check service health and retry.") from exc
    return {"status": "ok", "answer": answer, "sources": sources}


@app.get("/api/admin/collections")
def list_collections(
    _actor: User = Depends(require_permission("collections:read")),
    db: Session = Depends(get_db),
):
    names = db.scalars(select(Document.collection_name).distinct().order_by(Document.collection_name)).all()
    return {"collections": names or ["karumegam_collection"]}


@app.get("/api/admin/documents")
def list_documents(
    collection_name: str = Query(default="karumegam_collection"),
    _actor: User = Depends(require_permission("documents:read")),
    db: Session = Depends(get_db),
):
    collection_name = validate_collection_name(collection_name)
    documents = db.scalars(
        select(Document).where(Document.collection_name == collection_name).order_by(Document.created_at.desc())
    ).all()
    return {
        "collection": collection_name,
        "documents": [
            {
                "document_id": str(document.id),
                "name": document.source_name,
                "title": document.title,
                "description": document.description,
                "chunks": document.chunk_count,
                "page_count": document.page_count,
                "created_at": document.created_at,
            }
            for document in documents
        ],
    }


@app.post("/api/admin/documents")
async def upload_document(
    file: UploadFile = File(...),
    collection_name: str = Form(default="karumegam_collection"),
    chunk_size: int = Form(default=1000),
    chunk_overlap: int = Form(default=200),
    duplicate_behavior: str = Form(default="skip"),
    title: str = Form(default=""),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("documents:write")),
):
    collection_name = validate_collection_name(collection_name)
    if not 200 <= chunk_size <= 3000 or chunk_overlap < 0 or chunk_overlap >= chunk_size // 2:
        raise HTTPException(status_code=422, detail="Chunk size must be 200-3000; overlap must be non-negative and less than half the chunk size.")
    if duplicate_behavior not in {"skip", "replace", "allow"}:
        raise HTTPException(status_code=422, detail="Choose skip, replace, or allow for duplicate documents.")
    if len(title) > 160 or len(description) > 1000 or len(tags) > 500:
        raise HTTPException(status_code=422, detail="Title, description, or tags exceed the allowed length.")
    source_name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not source_name or Path(source_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a PDF file with a .pdf extension.")
    if file.content_type not in {None, "", "application/pdf", "application/octet-stream", "application/x-pdf"}:
        raise HTTPException(status_code=415, detail="The uploaded file must have PDF content type.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source_name)[:120].strip("._") or "document.pdf"
    upload_path = UPLOAD_DIR / f"{uuid4()}_{safe_name}"
    size = 0
    try:
        with upload_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="The maximum PDF upload size is 20 MB.")
                destination.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="The uploaded document is empty.")

        def ingest_in_worker():
            with SessionLocal() as ingestion_db:
                return ingest_document(
                    ingestion_db,
                    pdf_path=upload_path,
                    collection_name=collection_name,
                    source_name=source_name[:255],
                    title=title.strip() or safe_name,
                    description=description.strip(),
                    tags=[tag.strip() for tag in tags.split(",") if tag.strip()],
                    created_by=user.id,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    duplicate_behavior=duplicate_behavior,
                )

        result = await run_in_threadpool(ingest_in_worker)
        for retired_file in result.pop("retired_files", []):
            retired_file.unlink(missing_ok=True)
        if result["status"] == "duplicate":
            upload_path.unlink(missing_ok=True)
        logger.info(
            "document_upload_complete",
            extra={"user_id": str(user.id), "document_id": result["document_id"], "collection": collection_name, "chunk_count": result["chunks"], "file_size": size},
        )
        return {**result, "collection": collection_name, "name": source_name[:255]}
    except HTTPException:
        upload_path.unlink(missing_ok=True)
        raise
    except ValueError as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        upload_path.unlink(missing_ok=True)
        logger.exception("Document ingestion failed", extra={"user_id": str(user.id), "collection": collection_name})
        raise HTTPException(status_code=503, detail="Document ingestion failed. Check PostgreSQL and Ollama health.") from exc
    finally:
        await file.close()


@app.delete("/api/admin/documents/{document_id}", status_code=204)
def delete_document(
    document_id: UUID,
    _csrf: None = Depends(require_csrf),
    _actor: User = Depends(require_permission("documents:delete")),
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    upload_path = Path(document.upload_path)
    db.delete(document)
    db.commit()
    upload_path.unlink(missing_ok=True)
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)