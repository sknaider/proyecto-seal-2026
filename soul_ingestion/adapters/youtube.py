"""Allowlisted YouTube transcript acquisition through a local executable."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from ..contracts import RawArtifact, Scope, Sensitivity, SourceDescriptor, SourceKind, TrustTier


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ALLOWED_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
PINNED_YTDLP_SHA256 = "3bda0968a01cde70d26720653003b28553c71be14dcb2e5f4c24e9921fdad745"


def parse_video_id(value: str) -> str:
    candidate = value.strip()
    if VIDEO_ID_RE.fullmatch(candidate):
        return candidate
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ValueError("only HTTPS YouTube video URLs or 11-character video IDs are allowed")
    if parsed.hostname == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    elif parsed.path.startswith(("/embed/", "/shorts/")):
        video_id = parsed.path.split("/")[2]
    else:
        raise ValueError("unsupported YouTube URL shape")
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("invalid YouTube video ID")
    return video_id


def _parse_json3(path: Path) -> tuple[str, list[dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rendered: list[str] = []
    timed: list[dict[str, object]] = []
    for event in payload.get("events", []):
        text = "".join(segment.get("utf8", "") for segment in event.get("segs", []))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        start_ms = int(event.get("tStartMs", 0))
        duration_ms = int(event.get("dDurationMs", 0))
        fragment = f"[{start_ms / 1000:.3f}s] {text}"
        start_char = sum(len(item) + 1 for item in rendered)
        rendered.append(fragment)
        timed.append(
            {
                "start_ms": start_ms,
                "duration_ms": duration_ms,
                "text": text,
                "start_char": start_char,
                "end_char": start_char + len(fragment),
            }
        )
    return "\n".join(rendered), timed


def _subtitle_candidates(languages: tuple[str, ...]) -> str:
    candidates: list[str] = []
    for language in languages:
        primary = language.split("-", 1)[0]
        for candidate in (language, f"{language}-orig", f"{language}-{primary}"):
            if candidate not in candidates:
                candidates.append(candidate)
    return ",".join(candidates)


class YouTubeAdapter:
    adapter_id = "youtube_v1"
    adapter_version = "1.0.0"
    timeout_seconds = 90
    max_output_bytes = 2 * 1024 * 1024

    async def acquire(
        self,
        value: str,
        *,
        tenant_id: UUID,
        owner_agent: str | None,
        scope: Scope,
        languages: tuple[str, ...] = ("es", "en"),
        observed_at: datetime | None = None,
    ) -> RawArtifact:
        video_id = parse_video_id(value)
        configured_path = os.environ.get("SUIE_YTDLP_PATH")
        executable = configured_path or shutil.which("yt-dlp")
        if not executable or not Path(executable).is_absolute() or not os.access(executable, os.X_OK):
            raise RuntimeError("local yt-dlp executable is unavailable")
        expected_hash = os.environ.get("SUIE_YTDLP_SHA256", PINNED_YTDLP_SHA256)
        actual_hash = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        if not hmac_compare(actual_hash, expected_hash):
            raise RuntimeError("local yt-dlp executable does not match the pinned SHA-256")
        if not languages or any(not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)?", item) for item in languages):
            raise ValueError("invalid subtitle language policy")
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        with tempfile.TemporaryDirectory(prefix="suie-youtube-") as temp_dir:
            output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")
            process = await asyncio.create_subprocess_exec(
                executable,
                "--no-config",
                "--no-update",
                "--skip-download",
                "--no-simulate",
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                _subtitle_candidates(languages),
                "--sub-format",
                "json3",
                "--print",
                '{"title":%(title)j,"channel":%(channel)j,"duration":%(duration)j,"channel_id":%(channel_id)j}',
                "--output",
                output_template,
                canonical_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("YouTube acquisition exceeded the 90 second limit") from None
            if len(stdout) > self.max_output_bytes or len(stderr) > self.max_output_bytes:
                raise RuntimeError("YouTube subprocess output exceeded the hard cap")
            if process.returncode != 0:
                errors = stderr.decode("utf-8", errors="replace").splitlines()
                raise RuntimeError(
                    f"YouTube transcript acquisition failed: {(errors[-1] if errors else 'unknown error')[:300]}"
                )
            metadata = json.loads(stdout.decode("utf-8").splitlines()[-1])
            subtitle_files = sorted(Path(temp_dir).glob(f"{video_id}.*.json3"))
            if not subtitle_files:
                raise RuntimeError("video has no subtitle track in the allowed languages")
            selected_file = subtitle_files[0]
            transcript, timed = _parse_json3(selected_file)
            if not transcript:
                raise RuntimeError("subtitle track was empty after deterministic parsing")
            raw_evidence = selected_file.read_bytes()
            selected_language = selected_file.name.split(".")[-2].split("-")[0]
        source = SourceDescriptor(
            source_kind=SourceKind.YOUTUBE,
            source_ref=video_id,
            source_uri=canonical_url,
            tenant_id=tenant_id,
            owner_agent=owner_agent,
            scope=scope,
            observed_at=observed_at or datetime.now(UTC),
            trust_tier=TrustTier.EXTERNAL_UNTRUSTED,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
        )
        return RawArtifact(
            source=source,
            media_type="application/vnd.youtube.transcript+json",
            content=raw_evidence,
            extracted_text=transcript,
            title=str(metadata.get("title") or video_id),
            authors=(str(metadata.get("channel") or metadata.get("uploader") or "unknown"),),
            language=selected_language,
            sensitivity=Sensitivity.PUBLIC,
            metadata={
                "video_id": video_id,
                "webpage_url": canonical_url,
                "duration": metadata.get("duration"),
                "channel_id": metadata.get("channel_id"),
                "timed_segments": timed,
                "acquirer": "local-yt-dlp",
            },
        )


def hmac_compare(left: str, right: str) -> bool:
    """Constant-time comparison without importing credential-bearing helpers."""

    import hmac

    return hmac.compare_digest(left, right)
