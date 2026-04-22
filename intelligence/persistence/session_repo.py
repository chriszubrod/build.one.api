"""Persistence layer for agent sessions, turns, and tool calls.

Three repo classes, one per table. Sync pyodbc methods — the session_runner
wraps them with asyncio.to_thread to bridge to the async loop.

Models are pydantic BaseModels. Datetimes come out of the view pre-formatted
as ISO-style strings (CONVERT(VARCHAR(19), dt, 120)) so we keep them as
strings here rather than parsing and re-serializing.
"""
import base64
import logging
from typing import Optional

import pyodbc
from pydantic import BaseModel

from shared.database import call_procedure, get_connection, map_database_error


logger = logging.getLogger(__name__)


# ─── Models ──────────────────────────────────────────────────────────────

class AgentSession(BaseModel):
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    agent_name: Optional[str] = None
    agent_user_id: Optional[int] = None
    requesting_user_id: Optional[int] = None
    parent_session_id: Optional[int] = None
    previous_session_id: Optional[int] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    user_message: Optional[str] = None
    system_prompt: Optional[str] = None
    status: Optional[str] = None
    termination_reason: Optional[str] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class AgentTurn(BaseModel):
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    session_id: Optional[int] = None
    turn_number: Optional[int] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: Optional[str] = None
    assistant_text: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class AgentToolCall(BaseModel):
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    turn_id: Optional[int] = None
    tool_use_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[str] = None
    tool_output: Optional[str] = None
    is_error: bool = False
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ─── Helpers ─────────────────────────────────────────────────────────────

def _encode_row_version(rv) -> Optional[str]:
    if rv is None:
        return None
    return base64.b64encode(rv).decode("ascii")


def _public_id_str(pid) -> Optional[str]:
    """UUIDs may come back as uuid.UUID from pyodbc; normalize to str."""
    return str(pid) if pid is not None else None


# ─── Session repo ────────────────────────────────────────────────────────

class AgentSessionRepo:
    def _from_db(self, row: pyodbc.Row) -> Optional[AgentSession]:
        if not row:
            return None
        try:
            return AgentSession(
                id=row.Id,
                public_id=_public_id_str(row.PublicId),
                row_version=_encode_row_version(row.RowVersion),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                agent_name=row.AgentName,
                agent_user_id=row.AgentUserId,
                requesting_user_id=row.RequestingUserId,
                parent_session_id=row.ParentSessionId,
                previous_session_id=row.PreviousSessionId,
                model=row.Model,
                provider=row.Provider,
                user_message=row.UserMessage,
                system_prompt=row.SystemPrompt,
                status=row.Status,
                termination_reason=row.TerminationReason,
                total_input_tokens=row.TotalInputTokens,
                total_output_tokens=row.TotalOutputTokens,
                started_at=row.StartedAt,
                completed_at=row.CompletedAt,
                error_message=row.ErrorMessage,
            )
        except Exception as error:
            logger.error(f"AgentSession._from_db failed: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        agent_name: str,
        model: str,
        provider: str,
        user_message: str,
        agent_user_id: Optional[int] = None,
        requesting_user_id: Optional[int] = None,
        parent_session_id: Optional[int] = None,
        previous_session_id: Optional[int] = None,
        system_prompt: Optional[str] = None,
    ) -> AgentSession:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAgentSession",
                    params={
                        "AgentName": agent_name,
                        "AgentUserId": agent_user_id,
                        "RequestingUserId": requesting_user_id,
                        "ParentSessionId": parent_session_id,
                        "PreviousSessionId": previous_session_id,
                        "Model": model,
                        "Provider": provider,
                        "UserMessage": user_message,
                        "SystemPrompt": system_prompt,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateAgentSession returned no row"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error creating agent session: {error}")
            raise map_database_error(error)

    def complete(
        self,
        *,
        id: int,
        termination_reason: str,
        total_input_tokens: int,
        total_output_tokens: int,
    ) -> Optional[AgentSession]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CompleteAgentSession",
                    params={
                        "Id": id,
                        "TerminationReason": termination_reason,
                        "TotalInputTokens": total_input_tokens,
                        "TotalOutputTokens": total_output_tokens,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error completing agent session: {error}")
            raise map_database_error(error)

    def fail(
        self,
        *,
        id: int,
        error_message: str,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> Optional[AgentSession]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FailAgentSession",
                    params={
                        "Id": id,
                        "ErrorMessage": error_message,
                        "TotalInputTokens": total_input_tokens,
                        "TotalOutputTokens": total_output_tokens,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error failing agent session: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[AgentSession]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadAgentSessionById", params={"Id": id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading agent session by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[AgentSession]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentSessionByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading agent session by public_id: {error}")
            raise map_database_error(error)

    def read_recent(self, top: int = 50) -> list[AgentSession]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadRecentAgentSessions", params={"Top": top}
                )
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error reading recent agent sessions: {error}")
            raise map_database_error(error)


# ─── Turn repo ───────────────────────────────────────────────────────────

class AgentTurnRepo:
    def _from_db(self, row: pyodbc.Row) -> Optional[AgentTurn]:
        if not row:
            return None
        try:
            return AgentTurn(
                id=row.Id,
                public_id=_public_id_str(row.PublicId),
                row_version=_encode_row_version(row.RowVersion),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                session_id=row.SessionId,
                turn_number=row.TurnNumber,
                model=row.Model,
                input_tokens=row.InputTokens,
                output_tokens=row.OutputTokens,
                stop_reason=row.StopReason,
                assistant_text=row.AssistantText,
                started_at=row.StartedAt,
                completed_at=row.CompletedAt,
            )
        except Exception as error:
            logger.error(f"AgentTurn._from_db failed: {error}")
            raise map_database_error(error)

    def create(self, *, session_id: int, turn_number: int, model: str) -> AgentTurn:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAgentTurn",
                    params={
                        "SessionId": session_id,
                        "TurnNumber": turn_number,
                        "Model": model,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateAgentTurn returned no row"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error creating agent turn: {error}")
            raise map_database_error(error)

    def complete(
        self,
        *,
        id: int,
        input_tokens: int,
        output_tokens: int,
        stop_reason: Optional[str] = None,
        assistant_text: Optional[str] = None,
    ) -> Optional[AgentTurn]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CompleteAgentTurn",
                    params={
                        "Id": id,
                        "InputTokens": input_tokens,
                        "OutputTokens": output_tokens,
                        "StopReason": stop_reason,
                        "AssistantText": assistant_text,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error completing agent turn: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[AgentTurn]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadAgentTurnById", params={"Id": id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading agent turn by id: {error}")
            raise map_database_error(error)

    def read_by_session_id(self, session_id: int) -> list[AgentTurn]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentTurnsBySessionId",
                    params={"SessionId": session_id},
                )
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error reading agent turns by session id: {error}")
            raise map_database_error(error)


# ─── Tool-call repo ──────────────────────────────────────────────────────

class AgentToolCallRepo:
    def _from_db(self, row: pyodbc.Row) -> Optional[AgentToolCall]:
        if not row:
            return None
        try:
            return AgentToolCall(
                id=row.Id,
                public_id=_public_id_str(row.PublicId),
                row_version=_encode_row_version(row.RowVersion),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                turn_id=row.TurnId,
                tool_use_id=row.ToolUseId,
                tool_name=row.ToolName,
                tool_input=row.ToolInput,
                tool_output=row.ToolOutput,
                is_error=bool(row.IsError),
                started_at=row.StartedAt,
                completed_at=row.CompletedAt,
            )
        except Exception as error:
            logger.error(f"AgentToolCall._from_db failed: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        turn_id: int,
        tool_use_id: str,
        tool_name: str,
        tool_input: str,
    ) -> AgentToolCall:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAgentToolCall",
                    params={
                        "TurnId": turn_id,
                        "ToolUseId": tool_use_id,
                        "ToolName": tool_name,
                        "ToolInput": tool_input,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateAgentToolCall returned no row"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error creating agent tool call: {error}")
            raise map_database_error(error)

    def complete(
        self, *, id: int, tool_output: str, is_error: bool
    ) -> Optional[AgentToolCall]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CompleteAgentToolCall",
                    params={
                        "Id": id,
                        "ToolOutput": tool_output,
                        "IsError": 1 if is_error else 0,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error completing agent tool call: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[AgentToolCall]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadAgentToolCallById", params={"Id": id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading agent tool call by id: {error}")
            raise map_database_error(error)

    def read_by_turn_id(self, turn_id: int) -> list[AgentToolCall]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentToolCallsByTurnId",
                    params={"TurnId": turn_id},
                )
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error reading agent tool calls by turn id: {error}")
            raise map_database_error(error)
