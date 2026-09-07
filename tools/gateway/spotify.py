"""Spotify Web API adapter — native stdlib, no spotipy or third-party SDKs.

Implements playback control, search, queue, playlists, and device management
via Spotify's REST API.  Authentication uses the Client Credentials flow
(server-side, no user redirect needed) for search/catalog calls, and
Authorization Code + PKCE for user-scoped playback endpoints.

Environment variables:
    SPOTIFY_CLIENT_ID      — app client ID from Spotify Developer Dashboard
    SPOTIFY_CLIENT_SECRET  — app client secret (Client Credentials only)
    SPOTIFY_ACCESS_TOKEN   — pre-issued access token (optional shortcut)

Usage:
    sp = SpotifyClient.from_env()
    results = sp.search("Daft Punk", kind="track", limit=5)
    sp.play(track_uri="spotify:track:4PTG3Z6ehGkBFwjybzWkR8")
    sp.pause()
    sp.next_track()
    sp.queue(track_uri="spotify:track:...")
    devices = sp.devices()
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Exceptions ────────────────────────────────────────────────────────────────

class SpotifyError(Exception):
    """Raised on Spotify API errors."""

class SpotifyAuthError(SpotifyError):
    """Raised when authentication fails."""


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class SpotifyTrack:
    id:       str
    name:     str
    artist:   str
    album:    str
    uri:      str
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "artist": self.artist,
            "album": self.album, "uri": self.uri, "duration_ms": self.duration_ms,
        }


@dataclass
class SpotifyDevice:
    id:        str
    name:      str
    type:      str
    is_active: bool
    volume:    int = 0


@dataclass
class PlaybackState:
    is_playing:   bool
    track:        Optional[SpotifyTrack]
    device:       Optional[SpotifyDevice]
    progress_ms:  int = 0
    shuffle:      bool = False
    repeat:       str  = "off"


# ── Token manager ─────────────────────────────────────────────────────────────

@dataclass
class _Token:
    access_token: str
    expires_at:   float
    token_type:   str = "Bearer"

    @property
    def valid(self) -> bool:
        return time.monotonic() < self.expires_at - 30


class _TokenManager:
    """Manages Client Credentials token with auto-refresh."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id     = client_id
        self._client_secret = client_secret
        self._token:        Optional[_Token] = None

    def get(self) -> str:
        if self._token and self._token.valid:
            return self._token.access_token
        self._token = self._fetch()
        return self._token.access_token

    def _fetch(self) -> _Token:
        creds = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        data  = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req   = urllib.request.Request(
            self.TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise SpotifyAuthError(f"Token fetch failed ({e.code}): {raw}") from e
        except (urllib.error.URLError, OSError) as e:
            raise SpotifyAuthError(f"Token fetch connection error: {e}") from e

        return _Token(
            access_token = body["access_token"],
            expires_at   = time.monotonic() + int(body.get("expires_in", 3600)),
            token_type   = body.get("token_type", "Bearer"),
        )


# ── Main client ───────────────────────────────────────────────────────────────

class SpotifyClient:
    """Spotify Web API client — pure stdlib.

    For server-side catalog/search: uses Client Credentials (no user login).
    For user playback control: requires a pre-issued user access token via
    SPOTIFY_ACCESS_TOKEN env var (Authorization Code flow handled externally).
    """

    API_BASE = "https://api.spotify.com/v1"

    def __init__(
        self,
        client_id:     Optional[str] = None,
        client_secret: Optional[str] = None,
        access_token:  Optional[str] = None,
    ) -> None:
        self._client_id     = client_id     or ""
        self._client_secret = client_secret or ""
        self._static_token  = access_token  or ""
        self._token_mgr: Optional[_TokenManager] = None
        if self._client_id and self._client_secret:
            self._token_mgr = _TokenManager(self._client_id, self._client_secret)

    def available(self) -> bool:
        return bool(self._static_token or (self._client_id and self._client_secret))

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        kind:  str = "track",
        limit: int = 10,
        market: Optional[str] = None,
    ) -> List[SpotifyTrack]:
        """Search Spotify catalog. kind: track | album | artist | playlist."""
        params: Dict[str, Any] = {
            "q":    query,
            "type": kind,
            "limit": min(limit, 50),
        }
        if market:
            params["market"] = market
        data = self._get("/search", params=params)
        items = data.get(f"{kind}s", {}).get("items", [])
        if kind == "track":
            return [self._parse_track(t) for t in items if t]
        return []

    # ── Playback ──────────────────────────────────────────────────────────────

    def play(
        self,
        track_uri:  Optional[str]      = None,
        uris:       Optional[List[str]] = None,
        context_uri: Optional[str]     = None,
        device_id:  Optional[str]      = None,
    ) -> None:
        """Start or resume playback."""
        body: dict = {}
        if uris:
            body["uris"] = uris
        elif track_uri:
            body["uris"] = [track_uri]
        elif context_uri:
            body["context_uri"] = context_uri
        params = {"device_id": device_id} if device_id else None
        self._put("/me/player/play", body=body or None, params=params)

    def pause(self, device_id: Optional[str] = None) -> None:
        """Pause playback."""
        params = {"device_id": device_id} if device_id else None
        self._put("/me/player/pause", params=params)

    def next_track(self, device_id: Optional[str] = None) -> None:
        """Skip to next track."""
        params = {"device_id": device_id} if device_id else None
        self._post("/me/player/next", params=params)

    def previous_track(self, device_id: Optional[str] = None) -> None:
        """Skip to previous track."""
        params = {"device_id": device_id} if device_id else None
        self._post("/me/player/previous", params=params)

    def set_volume(self, volume_percent: int, device_id: Optional[str] = None) -> None:
        """Set playback volume (0–100)."""
        params: dict = {"volume_percent": max(0, min(100, volume_percent))}
        if device_id:
            params["device_id"] = device_id
        self._put("/me/player/volume", params=params)

    def seek(self, position_ms: int, device_id: Optional[str] = None) -> None:
        """Seek to position in current track."""
        params: dict = {"position_ms": position_ms}
        if device_id:
            params["device_id"] = device_id
        self._put("/me/player/seek", params=params)

    def shuffle(self, state: bool, device_id: Optional[str] = None) -> None:
        """Toggle shuffle."""
        params: dict = {"state": str(state).lower()}
        if device_id:
            params["device_id"] = device_id
        self._put("/me/player/shuffle", params=params)

    def repeat(self, state: str, device_id: Optional[str] = None) -> None:
        """Set repeat mode: track | context | off."""
        params: dict = {"state": state}
        if device_id:
            params["device_id"] = device_id
        self._put("/me/player/repeat", params=params)

    def current_playback(self) -> Optional[PlaybackState]:
        """Get current playback state."""
        try:
            data = self._get("/me/player")
        except SpotifyError:
            return None
        if not data:
            return None
        item = data.get("item")
        track = self._parse_track(item) if item else None
        dev   = data.get("device")
        device = SpotifyDevice(
            id        = dev.get("id", ""),
            name      = dev.get("name", ""),
            type      = dev.get("type", ""),
            is_active = dev.get("is_active", False),
            volume    = dev.get("volume_percent", 0),
        ) if dev else None
        return PlaybackState(
            is_playing  = data.get("is_playing", False),
            track       = track,
            device      = device,
            progress_ms = data.get("progress_ms", 0),
            shuffle     = data.get("shuffle_state", False),
            repeat      = data.get("repeat_state", "off"),
        )

    # ── Queue ─────────────────────────────────────────────────────────────────

    def queue(self, track_uri: str, device_id: Optional[str] = None) -> None:
        """Add track to queue."""
        params: dict = {"uri": track_uri}
        if device_id:
            params["device_id"] = device_id
        self._post("/me/player/queue", params=params)

    # ── Devices ───────────────────────────────────────────────────────────────

    def devices(self) -> List[SpotifyDevice]:
        """List available Spotify Connect devices."""
        data = self._get("/me/player/devices")
        return [
            SpotifyDevice(
                id        = d.get("id", ""),
                name      = d.get("name", ""),
                type      = d.get("type", ""),
                is_active = d.get("is_active", False),
                volume    = d.get("volume_percent", 0),
            )
            for d in data.get("devices", [])
        ]

    # ── Playlists ─────────────────────────────────────────────────────────────

    def my_playlists(self, limit: int = 20) -> List[dict]:
        """Get current user's playlists."""
        data = self._get("/me/playlists", params={"limit": min(limit, 50)})
        return [
            {"id": p["id"], "name": p["name"], "uri": p["uri"],
             "tracks": p.get("tracks", {}).get("total", 0)}
            for p in data.get("items", []) if p
        ]

    def playlist_tracks(self, playlist_id: str, limit: int = 50) -> List[SpotifyTrack]:
        """Get tracks from a playlist."""
        data = self._get(
            f"/playlists/{playlist_id}/tracks",
            params={"limit": min(limit, 100), "fields": "items(track)"},
        )
        return [
            self._parse_track(item["track"])
            for item in data.get("items", [])
            if item and item.get("track")
        ]

    # ── Library ───────────────────────────────────────────────────────────────

    def saved_tracks(self, limit: int = 20) -> List[SpotifyTrack]:
        """Get user's saved (liked) tracks."""
        data = self._get("/me/tracks", params={"limit": min(limit, 50)})
        return [
            self._parse_track(item["track"])
            for item in data.get("items", [])
            if item and item.get("track")
        ]

    def save_track(self, track_id: str) -> None:
        """Save (like) a track."""
        self._put("/me/tracks", body={"ids": [track_id]})

    def remove_track(self, track_id: str) -> None:
        """Remove (unlike) a track."""
        self._delete(f"/me/tracks", params={"ids": track_id})

    # ── Classmethod ───────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "SpotifyClient":
        return cls(
            client_id     = os.environ.get("SPOTIFY_CLIENT_ID"),
            client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET"),
            access_token  = os.environ.get("SPOTIFY_ACCESS_TOKEN"),
        )

    # ── Internal HTTP ─────────────────────────────────────────────────────────

    def _token(self) -> str:
        if self._static_token:
            return self._static_token
        if self._token_mgr:
            return self._token_mgr.get()
        raise SpotifyAuthError("No Spotify credentials configured")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type":  "application/json",
        }

    def _url(self, path: str, params: Optional[dict] = None) -> str:
        url = self.API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        return url

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        req = urllib.request.Request(self._url(path, params), headers=self._headers())
        return self._call(req)

    def _put(self, path: str, body: Optional[dict] = None,
             params: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body else b""
        req  = urllib.request.Request(
            self._url(path, params), data=data,
            headers=self._headers(), method="PUT",
        )
        return self._call(req)

    def _post(self, path: str, body: Optional[dict] = None,
              params: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body else b""
        req  = urllib.request.Request(
            self._url(path, params), data=data,
            headers=self._headers(), method="POST",
        )
        return self._call(req)

    def _delete(self, path: str, params: Optional[dict] = None) -> dict:
        req = urllib.request.Request(
            self._url(path, params), headers=self._headers(), method="DELETE",
        )
        return self._call(req)

    def _call(self, req: urllib.request.Request, timeout: int = 10) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            if e.code == 401:
                raise SpotifyAuthError(f"Unauthorized (401): {raw}") from e
            if e.code == 204:
                return {}
            raise SpotifyError(f"Spotify API error {e.code}: {raw}") from e
        except (urllib.error.URLError, OSError) as e:
            raise SpotifyError(f"Connection failed: {e}") from e

    @staticmethod
    def _parse_track(data: dict) -> SpotifyTrack:
        artists = data.get("artists", [{}])
        artist  = ", ".join(a.get("name", "") for a in artists)
        album   = data.get("album", {}).get("name", "") if data.get("album") else ""
        return SpotifyTrack(
            id          = data.get("id", ""),
            name        = data.get("name", ""),
            artist      = artist,
            album       = album,
            uri         = data.get("uri", ""),
            duration_ms = data.get("duration_ms", 0),
        )
