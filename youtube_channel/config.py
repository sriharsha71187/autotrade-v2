"""Configuration loader for the YouTube channel manager.

Resolution order (later wins is *not* used — first found wins per key, then
process env overrides everything): process env  >  ~/.youtube_channel.env  >
./.env. Mirrors the pattern used by the trading system's secrets handling.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


def _load_env_files() -> None:
    """Load env files without clobbering variables already set in the process."""
    home_env = Path.home() / ".youtube_channel.env"
    local_env = Path(__file__).resolve().parent / ".env"
    for candidate in (home_env, local_env):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path))).resolve()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # Channel identity
    channel_theme: str = "fascinating science facts explained simply"
    channel_name: str = "Daily Curiosity"
    video_target_seconds: int = 60
    video_width: int = 1080
    video_height: int = 1920

    # Script generation
    anthropic_api_key: str = ""
    script_model: str = "claude-sonnet-4-6"

    # Voiceover
    tts_backend: str = "gtts"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    openai_api_key: str = ""
    openai_tts_voice: str = "alloy"

    # Visuals
    visuals_backend: str = "slides"

    # YouTube
    youtube_client_secrets: Path = field(default_factory=lambda: Path.home() / ".youtube_channel_client_secret.json")
    youtube_token_file: Path = field(default_factory=lambda: Path.home() / ".youtube_channel_token.json")
    youtube_privacy: str = "unlisted"
    youtube_upload: bool = False

    # Output
    output_dir: Path = field(default_factory=lambda: Path.home() / "youtube_channel_output")

    @classmethod
    def load(cls) -> "Config":
        _load_env_files()
        return cls(
            channel_theme=_get("CHANNEL_THEME", "fascinating science facts explained simply"),
            channel_name=_get("CHANNEL_NAME", "Daily Curiosity"),
            video_target_seconds=_get_int("VIDEO_TARGET_SECONDS", 60),
            video_width=_get_int("VIDEO_WIDTH", 1080),
            video_height=_get_int("VIDEO_HEIGHT", 1920),
            anthropic_api_key=_get("ANTHROPIC_API_KEY"),
            script_model=_get("SCRIPT_MODEL", "claude-sonnet-4-6"),
            tts_backend=_get("TTS_BACKEND", "gtts").lower(),
            elevenlabs_api_key=_get("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id=_get("ELEVENLABS_VOICE_ID"),
            openai_api_key=_get("OPENAI_API_KEY"),
            openai_tts_voice=_get("OPENAI_TTS_VOICE", "alloy"),
            visuals_backend=_get("VISUALS_BACKEND", "slides").lower(),
            youtube_client_secrets=_expand(_get("YOUTUBE_CLIENT_SECRETS", "~/.youtube_channel_client_secret.json")),
            youtube_token_file=_expand(_get("YOUTUBE_TOKEN_FILE", "~/.youtube_channel_token.json")),
            youtube_privacy=_get("YOUTUBE_PRIVACY", "unlisted").lower(),
            youtube_upload=_get_bool("YOUTUBE_UPLOAD", False),
            output_dir=_expand(_get("OUTPUT_DIR", "~/youtube_channel_output")),
        )

    def require_anthropic(self) -> str:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to ~/.youtube_channel.env "
                "(see .env.template)."
            )
        return self.anthropic_api_key
