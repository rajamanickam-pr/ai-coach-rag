from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from server.database import get_db
from server.models import User
from server.security import require_permission
from server.services.conversations import (
    AnswerGenerationError,
    ConversationNotFoundError,
    ConversationService,
)
from server.api.dependencies import require_csrf

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    collection_name: str = Field(default="karumegam_collection", min_length=3, max_length=63)


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)
    collection_name: str = Field(default="karumegam_collection", min_length=3, max_length=63)
    mode: Literal["auto", "rag", "direct"] = "auto"


def get_conversation_service(db: Session = Depends(get_db)) -> ConversationService:
    return ConversationService(db)


@router.get("")
def list_conversations(
    user: User = Depends(require_permission("conversations:read")),
    service: ConversationService = Depends(get_conversation_service),
):
    return {"conversations": [_conversation_summary(item) for item in service.list_for_user(user.id)]}


@router.post("", status_code=201)
def create_conversation(
    payload: ConversationCreateRequest,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:write")),
    service: ConversationService = Depends(get_conversation_service),
):
    try:
        conversation = service.create(user.id, payload.title, payload.collection_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "collection_name": conversation.collection_name,
    }


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    user: User = Depends(require_permission("conversations:read")),
    service: ConversationService = Depends(get_conversation_service),
):
    try:
        conversation, messages = service.get(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "collection_name": conversation.collection_name,
        "messages": [
            {
                "id": str(message.id),
                "role": message.role,
                "content": message.content,
                "sources": message.sources,
                "answer_mode": message.answer_mode,
                "created_at": message.created_at,
            }
            for message in messages
        ],
    }


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: UUID,
    payload: ConversationRenameRequest,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:write")),
    service: ConversationService = Depends(get_conversation_service),
):
    try:
        conversation = service.rename(conversation_id, user.id, payload.title)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": str(conversation.id), "title": conversation.title, "updated_at": conversation.updated_at}


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: UUID,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:delete")),
    service: ConversationService = Depends(get_conversation_service),
):
    try:
        service.delete(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    return Response(status_code=204)


@router.post("/{conversation_id}/ask")
def answer_question(
    conversation_id: UUID,
    payload: AskRequest,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_permission("conversations:write")),
    service: ConversationService = Depends(get_conversation_service),
):
    try:
        return service.ask(conversation_id, user.id, payload.question, payload.collection_name, payload.mode)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AnswerGenerationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not complete the answer. Check service health and retry.",
        ) from exc


def _conversation_summary(conversation):
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "collection_name": conversation.collection_name,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }