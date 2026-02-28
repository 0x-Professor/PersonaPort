from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from personaport.models import Conversation, PersonaProfile, ProcessedHistory

SCHEMA_VERSION = 1


class ConversationCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    source_platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS personas (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    extracted_facts TEXT NOT NULL,
                    style_notes TEXT NOT NULL,
                    source_conversation_ids TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_histories (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    target_platform TEXT NOT NULL,
                    condensed_history TEXT NOT NULL,
                    full_history TEXT NOT NULL,
                    persona_prompt TEXT NOT NULL,
                    output_files TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_schema_version(conn)

    def _ensure_schema_version(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return

        try:
            current_version = int(row["value"])
        except (TypeError, ValueError):
            current_version = 0

        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                "Database schema is newer than this PersonaPort build. "
                "Upgrade PersonaPort before using this database."
            )

        if current_version < SCHEMA_VERSION:
            self._migrate_schema(conn, from_version=current_version, to_version=SCHEMA_VERSION)
            conn.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )

    def _migrate_schema(
        self,
        conn: sqlite3.Connection,
        *,
        from_version: int,
        to_version: int,
    ) -> None:
        version = from_version
        while version < to_version:
            next_version = version + 1
            # Reserved for future migrations.
            version = next_version

    def save_conversation(self, conversation: Conversation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, source_platform, title, created_at, updated_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_platform=excluded.source_platform,
                    title=excluded.title,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    raw_json=excluded.raw_json
                """,
                (
                    conversation.id,
                    str(conversation.source_platform),
                    conversation.title,
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                    json.dumps(conversation.model_dump(mode="json")),
                ),
            )

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            return Conversation.model_validate_json(row["raw_json"])

    def get_latest_conversation(self, source_platform: str | None = None) -> Conversation | None:
        query = "SELECT raw_json FROM conversations"
        params: tuple[str, ...] = tuple()
        if source_platform:
            query += " WHERE source_platform = ?"
            params = (source_platform,)
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            return Conversation.model_validate_json(row["raw_json"])

    def list_conversations(self, limit: int = 20) -> list[Conversation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT raw_json FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Conversation.model_validate_json(row["raw_json"]) for row in rows]

    def save_persona(self, persona: PersonaProfile) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO personas (
                    id, name, system_prompt, extracted_facts, style_notes,
                    source_conversation_ids, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    system_prompt=excluded.system_prompt,
                    extracted_facts=excluded.extracted_facts,
                    style_notes=excluded.style_notes,
                    source_conversation_ids=excluded.source_conversation_ids,
                    created_at=excluded.created_at
                """,
                (
                    persona.id,
                    persona.name,
                    persona.system_prompt,
                    json.dumps(persona.extracted_facts),
                    json.dumps(persona.style_notes),
                    json.dumps(persona.source_conversation_ids),
                    persona.created_at.isoformat(),
                ),
            )

    def get_latest_persona(self) -> PersonaProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM personas ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return PersonaProfile(
            id=row["id"],
            name=row["name"],
            system_prompt=row["system_prompt"],
            extracted_facts=json.loads(row["extracted_facts"]),
            style_notes=json.loads(row["style_notes"]),
            source_conversation_ids=json.loads(row["source_conversation_ids"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_processed_history(self, processed: ProcessedHistory) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_histories (
                    id, conversation_id, target_platform, condensed_history,
                    full_history, persona_prompt, output_files, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,
                    target_platform=excluded.target_platform,
                    condensed_history=excluded.condensed_history,
                    full_history=excluded.full_history,
                    persona_prompt=excluded.persona_prompt,
                    output_files=excluded.output_files,
                    created_at=excluded.created_at
                """,
                (
                    processed.id,
                    processed.conversation_id,
                    str(processed.target_platform),
                    processed.condensed_history,
                    processed.full_history,
                    processed.persona_prompt,
                    json.dumps(processed.output_files),
                    processed.created_at.isoformat(),
                ),
            )

    def latest_processed_history(self) -> ProcessedHistory | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM processed_histories ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return ProcessedHistory(
            id=row["id"],
            conversation_id=row["conversation_id"],
            target_platform=row["target_platform"],
            condensed_history=row["condensed_history"],
            full_history=row["full_history"],
            persona_prompt=row["persona_prompt"],
            output_files=json.loads(row["output_files"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
