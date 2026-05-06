"""Tests for SpotifyClient — native Spotify Web API adapter."""

import base64
import json
import sys
import pathlib
import time
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.spotify import (
    SpotifyClient, SpotifyTrack, SpotifyDevice, PlaybackState,
    SpotifyError, SpotifyAuthError, _Token, _TokenManager,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_resp(body: dict, status: int = 200):
    raw = json.dumps(body).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_err(code: int, body: dict = {}):
    raw = json.dumps(body).encode()
    return urllib.error.HTTPError("http://x", code, "err", {}, BytesIO(raw))


def _client(token: str = "test-token") -> SpotifyClient:
    return SpotifyClient(access_token=token)


def _track_payload(tid="t1", name="Song", artist="Artist",
                   album="Album", uri="spotify:track:t1") -> dict:
    return {
        "id": tid, "name": name,
        "artists": [{"name": artist}],
        "album": {"name": album},
        "uri": uri, "duration_ms": 200000,
    }


# ── Token ─────────────────────────────────────────────────────────────────────

def test_token_valid_before_expiry():
    t = _Token(access_token="x", expires_at=time.monotonic() + 100)
    assert t.valid is True


def test_token_invalid_near_expiry():
    t = _Token(access_token="x", expires_at=time.monotonic() + 10)
    assert t.valid is False


def test_token_manager_fetches_token():
    resp_body = {"access_token": "fresh-token", "token_type": "Bearer",
                 "expires_in": 3600}
    mgr = _TokenManager("client-id", "client-secret")
    with patch("urllib.request.urlopen", return_value=_mock_resp(resp_body)):
        token = mgr.get()
    assert token == "fresh-token"


def test_token_manager_sends_basic_auth():
    resp_body = {"access_token": "t", "token_type": "Bearer", "expires_in": 3600}
    captured = {}

    def fake_open(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        return _mock_resp(resp_body)

    mgr = _TokenManager("myid", "mysecret")
    with patch("urllib.request.urlopen", side_effect=fake_open):
        mgr.get()

    expected = "Basic " + base64.b64encode(b"myid:mysecret").decode()
    assert captured["auth"] == expected


def test_token_manager_caches_valid_token():
    resp_body = {"access_token": "cached", "token_type": "Bearer", "expires_in": 3600}
    mgr = _TokenManager("id", "secret")
    with patch("urllib.request.urlopen", return_value=_mock_resp(resp_body)) as mock:
        mgr.get()
        mgr.get()
    assert mock.call_count == 1


# ── SpotifyClient.available ───────────────────────────────────────────────────

def test_available_with_static_token():
    assert SpotifyClient(access_token="tok").available() is True


def test_available_with_credentials():
    assert SpotifyClient(client_id="cid", client_secret="csec").available() is True


def test_not_available_without_any_creds():
    assert SpotifyClient().available() is False


# ── SpotifyClient.search ──────────────────────────────────────────────────────

def test_search_returns_tracks():
    sp = _client()
    payload = {"tracks": {"items": [_track_payload()]}}
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        results = sp.search("Daft Punk", kind="track")
    assert len(results) == 1
    assert isinstance(results[0], SpotifyTrack)
    assert results[0].name == "Song"
    assert results[0].artist == "Artist"


def test_search_sends_query_params():
    sp = _client()
    captured = {}

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp({"tracks": {"items": []}})

    with patch("urllib.request.urlopen", side_effect=fake_open):
        sp.search("Radiohead", kind="track", limit=5)

    assert "q=Radiohead" in captured["url"]
    assert "type=track" in captured["url"]
    assert "limit=5" in captured["url"]


def test_search_empty_results():
    sp = _client()
    payload = {"tracks": {"items": []}}
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        results = sp.search("zzznoresult")
    assert results == []


def test_search_multiple_artists():
    sp = _client()
    track = {"id": "x", "name": "Collab",
              "artists": [{"name": "A"}, {"name": "B"}],
              "album": {"name": "ALB"}, "uri": "u", "duration_ms": 0}
    payload = {"tracks": {"items": [track]}}
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        results = sp.search("collab")
    assert results[0].artist == "A, B"


# ── SpotifyClient playback ─────────────────────────────────────────────────────

def test_play_sends_put():
    sp = _client()
    captured = {}

    def fake_open(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        return _mock_resp({})

    with patch("urllib.request.urlopen", side_effect=fake_open):
        sp.play(track_uri="spotify:track:abc")

    assert captured["method"] == "PUT"
    assert "/me/player/play" in captured["url"]


def test_pause_sends_put():
    sp = _client()
    with patch("urllib.request.urlopen", return_value=_mock_resp({})) as mock:
        sp.pause()
    req = mock.call_args[0][0]
    assert req.get_method() == "PUT"
    assert "/me/player/pause" in req.full_url


def test_next_track_sends_post():
    sp = _client()
    with patch("urllib.request.urlopen", return_value=_mock_resp({})) as mock:
        sp.next_track()
    req = mock.call_args[0][0]
    assert req.get_method() == "POST"
    assert "/me/player/next" in req.full_url


def test_set_volume_clamps_to_100():
    sp = _client()
    captured = {}

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp({})

    with patch("urllib.request.urlopen", side_effect=fake_open):
        sp.set_volume(150)

    assert "volume_percent=100" in captured["url"]


def test_set_volume_clamps_to_0():
    sp = _client()
    captured = {}

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp({})

    with patch("urllib.request.urlopen", side_effect=fake_open):
        sp.set_volume(-10)

    assert "volume_percent=0" in captured["url"]


def test_current_playback_returns_state():
    sp = _client()
    payload = {
        "is_playing": True,
        "item": _track_payload(tid="t1", name="Now Playing"),
        "device": {"id": "d1", "name": "Laptop", "type": "Computer",
                   "is_active": True, "volume_percent": 80},
        "progress_ms": 45000,
        "shuffle_state": False,
        "repeat_state": "off",
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        state = sp.current_playback()
    assert state is not None
    assert state.is_playing is True
    assert state.track.name == "Now Playing"
    assert state.device.name == "Laptop"
    assert state.progress_ms == 45000


def test_current_playback_returns_none_on_error():
    sp = _client()
    with patch("urllib.request.urlopen", side_effect=_http_err(204)):
        state = sp.current_playback()
    assert state is None


# ── Queue ─────────────────────────────────────────────────────────────────────

def test_queue_sends_post_with_uri():
    sp = _client()
    captured = {}

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _mock_resp({})

    with patch("urllib.request.urlopen", side_effect=fake_open):
        sp.queue("spotify:track:abc123")

    assert "POST" == captured["method"]
    assert "spotify%3Atrack%3Aabc123" in captured["url"] or "abc123" in captured["url"]


# ── Devices ───────────────────────────────────────────────────────────────────

def test_devices_returns_list():
    sp = _client()
    payload = {"devices": [
        {"id": "d1", "name": "Phone", "type": "Smartphone",
         "is_active": False, "volume_percent": 60},
        {"id": "d2", "name": "PC", "type": "Computer",
         "is_active": True, "volume_percent": 100},
    ]}
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        devs = sp.devices()
    assert len(devs) == 2
    assert devs[0].name == "Phone"
    assert devs[1].is_active is True


def test_devices_empty():
    sp = _client()
    with patch("urllib.request.urlopen", return_value=_mock_resp({"devices": []})):
        assert sp.devices() == []


# ── Playlists ─────────────────────────────────────────────────────────────────

def test_my_playlists_returns_list():
    sp = _client()
    payload = {"items": [
        {"id": "pl1", "name": "Chill", "uri": "spotify:playlist:pl1",
         "tracks": {"total": 20}},
    ]}
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        pls = sp.my_playlists()
    assert len(pls) == 1
    assert pls[0]["name"] == "Chill"
    assert pls[0]["tracks"] == 20


def test_playlist_tracks_returns_tracks():
    sp = _client()
    payload = {"items": [
        {"track": _track_payload(name="Track A")},
        {"track": _track_payload(name="Track B")},
    ]}
    with patch("urllib.request.urlopen", return_value=_mock_resp(payload)):
        tracks = sp.playlist_tracks("pl1")
    assert len(tracks) == 2
    assert tracks[0].name == "Track A"


# ── from_env ──────────────────────────────────────────────────────────────────

def test_from_env_reads_token():
    import os
    os.environ["SPOTIFY_ACCESS_TOKEN"] = "env-token"
    try:
        sp = SpotifyClient.from_env()
        assert sp._static_token == "env-token"
    finally:
        del os.environ["SPOTIFY_ACCESS_TOKEN"]


def test_from_env_reads_credentials():
    import os
    os.environ["SPOTIFY_CLIENT_ID"] = "cid"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "csec"
    try:
        sp = SpotifyClient.from_env()
        assert sp._token_mgr is not None
        assert sp._client_id == "cid"
    finally:
        del os.environ["SPOTIFY_CLIENT_ID"]
        del os.environ["SPOTIFY_CLIENT_SECRET"]


# ── Error handling ────────────────────────────────────────────────────────────

def test_401_raises_auth_error():
    sp = _client()
    with patch("urllib.request.urlopen", side_effect=_http_err(401)):
        raised = False
        try:
            sp.search("test")
        except SpotifyAuthError:
            raised = True
        assert raised


def test_500_raises_spotify_error():
    sp = _client()
    with patch("urllib.request.urlopen", side_effect=_http_err(500)):
        raised = False
        try:
            sp.search("test")
        except SpotifyError:
            raised = True
        assert raised


def test_no_creds_raises_auth_error():
    sp = SpotifyClient()
    raised = False
    try:
        sp.search("test")
    except SpotifyAuthError:
        raised = True
    assert raised


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_token_valid_before_expiry,
        test_token_invalid_near_expiry,
        test_token_manager_fetches_token,
        test_token_manager_sends_basic_auth,
        test_token_manager_caches_valid_token,
        test_available_with_static_token,
        test_available_with_credentials,
        test_not_available_without_any_creds,
        test_search_returns_tracks,
        test_search_sends_query_params,
        test_search_empty_results,
        test_search_multiple_artists,
        test_play_sends_put,
        test_pause_sends_put,
        test_next_track_sends_post,
        test_set_volume_clamps_to_100,
        test_set_volume_clamps_to_0,
        test_current_playback_returns_state,
        test_current_playback_returns_none_on_error,
        test_queue_sends_post_with_uri,
        test_devices_returns_list,
        test_devices_empty,
        test_my_playlists_returns_list,
        test_playlist_tracks_returns_tracks,
        test_from_env_reads_token,
        test_from_env_reads_credentials,
        test_401_raises_auth_error,
        test_500_raises_spotify_error,
        test_no_creds_raises_auth_error,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
