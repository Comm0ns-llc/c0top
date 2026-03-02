"""Storage abstraction.

This bot can run in two modes:
- memory: in-memory storage for quick local runs
- supabase: persistent storage via Supabase

The bot uses a minimal subset of operations needed for scoring and ranking.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import config
from .database import Database, DatabaseError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage(Protocol):
    async def upsert_channel(self, channel_id: int, name: str, channel_type: str | None = None) -> dict[str, Any] | None: ...

    async def upsert_user(self, user_id: int, username: str) -> dict[str, Any] | None: ...

    async def get_user(self, user_id: int) -> dict[str, Any] | None: ...

    async def update_user_score(self, user_id: int, score_delta: float) -> dict[str, Any] | None: ...

    async def get_user_rank(self, user_id: int) -> tuple[int, int] | None: ...

    async def get_leaderboard(self, limit: int = 10, weekly: bool = False) -> list[dict[str, Any]]: ...

    async def insert_message(
        self,
        message_id: int,
        user_id: int,
        channel_id: int,
        guild_id: int,
        content: str | None,
        nlp_score_multiplier: float = 1.0,
        base_score: float = 1.0,
    ) -> dict[str, Any] | None: ...

    async def get_user_messages_stats(self, user_id: int) -> dict[str, Any]: ...

    async def get_message(self, message_id: int) -> dict[str, Any] | None: ...

    async def check_reaction_exists(self, message_id: int, user_id: int, reaction_type: str) -> bool: ...

    async def insert_reaction(self, message_id: int, user_id: int, reaction_type: str, weight: float) -> dict[str, Any] | None: ...

    async def update_message_reaction_score(self, message_id: int, reaction_score_delta: float) -> dict[str, Any] | None: ...

    async def get_metadata(self, key: str) -> str | None: ...

    async def update_metadata(self, key: str, value: str) -> bool: ...

    async def compare_and_set_metadata(self, key: str, expected_value: str | None, new_value: str) -> bool: ...

    async def reset_weekly_scores(self) -> bool: ...


class SupabaseStorage:
    def __init__(self) -> None:
        self._db = Database()

    async def upsert_channel(self, channel_id: int, name: str, channel_type: str | None = None) -> dict[str, Any] | None:
        return await self._db.upsert_channel(channel_id=channel_id, name=name, channel_type=channel_type)

    async def upsert_user(self, user_id: int, username: str) -> dict[str, Any] | None:
        return await self._db.upsert_user(user_id=user_id, username=username)

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        return await self._db.get_user(user_id=user_id)

    async def update_user_score(self, user_id: int, score_delta: float) -> dict[str, Any] | None:
        return await self._db.update_user_score(user_id=user_id, score_delta=score_delta)

    async def get_user_rank(self, user_id: int) -> tuple[int, int] | None:
        return await self._db.get_user_rank(user_id=user_id)

    async def get_leaderboard(self, limit: int = 10, weekly: bool = False) -> list[dict[str, Any]]:
        return await self._db.get_leaderboard(limit=limit, weekly=weekly)

    async def insert_message(
        self,
        message_id: int,
        user_id: int,
        channel_id: int,
        guild_id: int,
        content: str | None,
        nlp_score_multiplier: float = 1.0,
        base_score: float = 1.0,
    ) -> dict[str, Any] | None:
        return await self._db.insert_message(
            message_id=message_id,
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
            content=content,
            nlp_score_multiplier=nlp_score_multiplier,
            base_score=base_score,
        )

    async def get_user_messages_stats(self, user_id: int) -> dict[str, Any]:
        return await self._db.get_user_messages_stats(user_id=user_id)

    async def get_message(self, message_id: int) -> dict[str, Any] | None:
        return await self._db.get_message(message_id=message_id)

    async def check_reaction_exists(self, message_id: int, user_id: int, reaction_type: str) -> bool:
        return await self._db.check_reaction_exists(
            message_id=message_id,
            user_id=user_id,
            reaction_type=reaction_type,
        )

    async def insert_reaction(self, message_id: int, user_id: int, reaction_type: str, weight: float) -> dict[str, Any] | None:
        return await self._db.insert_reaction(
            message_id=message_id,
            user_id=user_id,
            reaction_type=reaction_type,
            weight=weight,
        )

    async def update_message_reaction_score(self, message_id: int, reaction_score_delta: float) -> dict[str, Any] | None:
        return await self._db.update_message_reaction_score(
            message_id=message_id,
            reaction_score_delta=reaction_score_delta,
        )

    async def get_metadata(self, key: str) -> str | None:
        return await self._db.get_metadata(key=key)

    async def update_metadata(self, key: str, value: str) -> bool:
        return await self._db.update_metadata(key=key, value=value)

    async def compare_and_set_metadata(self, key: str, expected_value: str | None, new_value: str) -> bool:
        return await self._db.compare_and_set_metadata(
            key=key,
            expected_value=expected_value,
            new_value=new_value,
        )

    async def reset_weekly_scores(self) -> bool:
        return await self._db.reset_weekly_scores()


@dataclass
class _MemoryUser:
    user_id: int
    username: str
    current_score: float
    weekly_score: float
    created_at: str
    updated_at: str


@dataclass
class _MemoryMessage:
    message_id: int
    user_id: int
    channel_id: int
    guild_id: int
    base_score: float
    nlp_score_multiplier: float
    reply_count: int
    reaction_score: float
    total_score: float
    timestamp: str
    created_at: str


class MemoryStorage:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._users: dict[int, _MemoryUser] = {}
        self._messages: dict[int, _MemoryMessage] = {}
        self._channels: dict[int, dict[str, Any]] = {}
        self._reactions: set[tuple[int, int, str]] = set()  # (message_id, user_id, reaction_type)
        self._metadata: dict[str, str] = {}

    async def upsert_channel(self, channel_id: int, name: str, channel_type: str | None = None) -> dict[str, Any] | None:
        async with self._lock:
            now = _now_iso()
            self._channels[channel_id] = {
                "channel_id": channel_id,
                "name": name,
                "type": channel_type,
                "created_at": now,
                "updated_at": now,
            }
            return self._channels[channel_id]

    async def upsert_user(self, user_id: int, username: str) -> dict[str, Any] | None:
        async with self._lock:
            now = _now_iso()
            existing = self._users.get(user_id)
            if existing:
                existing.username = username
                existing.updated_at = now
            else:
                self._users[user_id] = _MemoryUser(
                    user_id=user_id,
                    username=username,
                    current_score=0.0,
                    weekly_score=0.0,
                    created_at=now,
                    updated_at=now,
                )
            return self._user_to_record(self._users[user_id])

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        async with self._lock:
            user = self._users.get(user_id)
            return self._user_to_record(user) if user else None

    async def update_user_score(self, user_id: int, score_delta: float) -> dict[str, Any] | None:
        async with self._lock:
            now = _now_iso()
            user = self._users.get(user_id)
            if not user:
                return None
            user.current_score = float(user.current_score) + float(score_delta)
            user.weekly_score = float(user.weekly_score) + float(score_delta)
            user.updated_at = now
            return self._user_to_record(user)

    async def get_user_rank(self, user_id: int) -> tuple[int, int] | None:
        async with self._lock:
            if user_id not in self._users:
                return None
            ordered = sorted(self._users.values(), key=lambda u: u.current_score, reverse=True)
            total = len(ordered)
            for idx, user in enumerate(ordered, start=1):
                if user.user_id == user_id:
                    return (idx, total)
            return None

    async def get_leaderboard(self, limit: int = 10, weekly: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            key = (lambda u: u.weekly_score) if weekly else (lambda u: u.current_score)
            ordered = sorted(self._users.values(), key=key, reverse=True)[:limit]
            rows: list[dict[str, Any]] = []
            for idx, user in enumerate(ordered, start=1):
                rows.append(
                    {
                        "user_id": user.user_id,
                        "username": user.username,
                        "current_score": float(user.current_score),
                        "weekly_score": float(user.weekly_score),
                        "rank": idx,
                    }
                )
            return rows

    async def insert_message(
        self,
        message_id: int,
        user_id: int,
        channel_id: int,
        guild_id: int,
        content: str | None,
        nlp_score_multiplier: float = 1.0,
        base_score: float = 1.0,
    ) -> dict[str, Any] | None:
        async with self._lock:
            now = _now_iso()
            total_score = float(base_score) * float(nlp_score_multiplier)
            self._messages[message_id] = _MemoryMessage(
                message_id=message_id,
                user_id=user_id,
                channel_id=channel_id,
                guild_id=guild_id,
                base_score=float(base_score),
                nlp_score_multiplier=float(nlp_score_multiplier),
                reply_count=0,
                reaction_score=0.0,
                total_score=total_score,
                timestamp=now,
                created_at=now,
            )
            return self._message_to_record(self._messages[message_id])

    async def get_user_messages_stats(self, user_id: int) -> dict[str, Any]:
        async with self._lock:
            user_messages = [m for m in self._messages.values() if m.user_id == user_id]
            total_messages = len(user_messages)
            total_base_score = sum(m.base_score for m in user_messages)
            total_reaction_score = sum(m.reaction_score for m in user_messages)
            total_score = float(total_base_score) + float(total_reaction_score)
            return {
                "total_messages": total_messages,
                "total_base_score": float(total_base_score),
                "total_nlp_adjusted_score": float(total_base_score),
                "total_reply_score": 0.0,
                "total_reaction_score": float(total_reaction_score),
                "total_score": float(total_score),
            }

    async def get_message(self, message_id: int) -> dict[str, Any] | None:
        async with self._lock:
            message = self._messages.get(message_id)
            return self._message_to_record(message) if message else None

    async def check_reaction_exists(self, message_id: int, user_id: int, reaction_type: str) -> bool:
        async with self._lock:
            return (message_id, user_id, reaction_type) in self._reactions

    async def insert_reaction(self, message_id: int, user_id: int, reaction_type: str, weight: float) -> dict[str, Any] | None:
        async with self._lock:
            self._reactions.add((message_id, user_id, reaction_type))
            return {
                "id": f"{message_id}:{user_id}:{reaction_type}",
                "message_id": message_id,
                "user_id": user_id,
                "reaction_type": reaction_type,
                "weight": float(weight),
                "created_at": _now_iso(),
            }

    async def update_message_reaction_score(self, message_id: int, reaction_score_delta: float) -> dict[str, Any] | None:
        async with self._lock:
            message = self._messages.get(message_id)
            if not message:
                return None
            message.reaction_score = float(message.reaction_score) + float(reaction_score_delta)
            # total_score = base_score * nlp_multiplier (fixed to 1.0) + reaction_score
            message.total_score = float(message.base_score) * float(message.nlp_score_multiplier) + float(message.reaction_score)
            return self._message_to_record(message)

    async def get_metadata(self, key: str) -> str | None:
        async with self._lock:
            return self._metadata.get(key)

    async def update_metadata(self, key: str, value: str) -> bool:
        async with self._lock:
            self._metadata[key] = value
            return True

    async def compare_and_set_metadata(self, key: str, expected_value: str | None, new_value: str) -> bool:
        async with self._lock:
            current = self._metadata.get(key)
            if current != expected_value:
                return False
            self._metadata[key] = new_value
            return True

    async def reset_weekly_scores(self) -> bool:
        async with self._lock:
            for user in self._users.values():
                user.weekly_score = 0.0
            return True

    def _user_to_record(self, user: _MemoryUser | None) -> dict[str, Any] | None:
        if user is None:
            return None
        return {
            "user_id": user.user_id,
            "username": user.username,
            "current_score": float(user.current_score),
            "weekly_score": float(user.weekly_score),
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }

    def _message_to_record(self, message: _MemoryMessage | None) -> dict[str, Any] | None:
        if message is None:
            return None
        return {
            "message_id": message.message_id,
            "user_id": message.user_id,
            "channel_id": message.channel_id,
            "guild_id": message.guild_id,
            "content": None,
            "nlp_score_multiplier": float(message.nlp_score_multiplier),
            "base_score": float(message.base_score),
            "reply_count": int(message.reply_count),
            "reaction_score": float(message.reaction_score),
            "total_score": float(message.total_score),
            "timestamp": message.timestamp,
            "created_at": message.created_at,
        }


def get_storage() -> Storage:
    backend = (config.storage_backend or "memory").strip().lower()
    if backend == "supabase":
        return SupabaseStorage()
    if backend == "memory":
        return MemoryStorage()
    raise DatabaseError(f"Unknown STORAGE_BACKEND: {backend}")


storage: Storage = get_storage()
