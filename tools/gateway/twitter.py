"""Twitter/X channel adapter — native v2 REST API via stdlib.

No tweepy. Uses Bearer-token (App auth) for read-only mention polling and
OAuth 2.0 user-context bearer for posting tweets via POST /2/tweets.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage

API_BASE = "https://api.x.com/2"


class TwitterChannel(GatewayChannel):
    """Native Twitter/X adapter using API v2."""

    def __init__(
        self,
        handler: InboundHandler,
        bearer_token: Optional[str] = None,
        user_id: Optional[str] = None,
        user_oauth_token: Optional[str] = None,
        poll_interval_s: float = 60.0,
    ) -> None:
        super().__init__("twitter", handler)
        self._bearer_token = bearer_token or os.environ.get("TWITTER_BEARER_TOKEN", "")
        self._user_id = user_id or os.environ.get("TWITTER_USER_ID", "")
        # Per-user OAuth 2.0 token used to POST tweets on behalf of the account.
        self._user_oauth_token = user_oauth_token or os.environ.get("TWITTER_USER_OAUTH_TOKEN", "")
        self._poll_interval_s = poll_interval_s
        self._since_id: Optional[str] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._bearer_token or not self._user_id:
            raise GatewayError("TWITTER_BEARER_TOKEN and TWITTER_USER_ID required")
        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> str:
        if not self._user_oauth_token:
            raise GatewayError("TWITTER_USER_OAUTH_TOKEN required to post tweets")

        payload: dict = {"text": message.text}
        if message.reply_to_message_id:
            payload["reply"] = {"in_reply_to_tweet_id": message.reply_to_message_id}

        def _post():
            req = urllib.request.Request(
                f"{API_BASE}/tweets",
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._user_oauth_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"twitter post failed: {e.code} {e.reason}")

        return str((data.get("data") or {}).get("id", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = await asyncio.to_thread(self._fetch_mentions)
                msgs = self.parse_mentions(payload)
                for inbound in msgs:
                    await self._dispatch(inbound)
                # Track newest id for since_id cursor.
                meta = (payload or {}).get("meta") or {}
                if meta.get("newest_id"):
                    self._since_id = str(meta["newest_id"])
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def _fetch_mentions(self) -> dict:
        """GET /2/users/:id/mentions with optional since_id."""
        params: dict = {
            "tweet.fields": "created_at,author_id,conversation_id,in_reply_to_user_id",
            "expansions": "author_id",
            "user.fields": "username,name",
            "max_results": "20",
        }
        if self._since_id:
            params["since_id"] = self._since_id
        url = f"{API_BASE}/users/{self._user_id}/mentions?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._bearer_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError:
            return {}
        except urllib.error.URLError:
            return {}

    @staticmethod
    def parse_mentions(payload: dict) -> list[InboundMessage]:
        """Parse a v2 mentions response into a list of InboundMessages."""
        if not payload:
            return []
        users_by_id: dict = {}
        for u in (payload.get("includes") or {}).get("users") or []:
            uid = u.get("id")
            if uid:
                users_by_id[uid] = u

        out: list[InboundMessage] = []
        for tweet in payload.get("data") or []:
            tid = str(tweet.get("id", ""))
            text = str(tweet.get("text", ""))
            author_id = str(tweet.get("author_id", ""))
            author = users_by_id.get(author_id) or {}
            created = tweet.get("created_at")
            try:
                received_at = (
                    datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if created
                    else datetime.now(timezone.utc)
                )
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=timezone.utc)
            except Exception:
                received_at = datetime.now(timezone.utc)

            out.append(
                InboundMessage(
                    channel="twitter",
                    platform_user_id=author_id,
                    platform_chat_id=str(tweet.get("conversation_id") or tid),
                    text=text,
                    received_at=received_at,
                    platform_message_id=tid or None,
                    user_display_name=author.get("name") or author.get("username"),
                    metadata={
                        "username": author.get("username"),
                        "in_reply_to_user_id": tweet.get("in_reply_to_user_id"),
                        "raw": tweet,
                    },
                )
            )
        return out
