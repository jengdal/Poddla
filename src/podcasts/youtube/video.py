from datetime import datetime, timezone
from pathlib import Path

import msgspec
import yt_dlp
from django.conf import settings


class DownloadInfo(msgspec.Struct):
    file_path: Path
    description: str | None = None
    published_at: datetime | None = None
    duration: int | None = None


def download_audio(url: str, base_path: Path, file_path: Path) -> DownloadInfo:
    """Download best audio for a YouTube URL. Returns (filepath, DownloadInfo)."""
    (base_path / file_path).parent.mkdir(parents=True, exist_ok=True)
    extractor_args: dict = {"youtube": {"player_client": ["mweb"]}}
    if bgutil_home := settings.BGUTIL_SERVER_HOME:
        extractor_args["youtubepot-bgutilscript"] = {"server_home": [bgutil_home]}
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(base_path / file_path) + ".%(ext)s",
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "extractor_args": extractor_args,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url=url, download=True) or {}

    ts = info.get("timestamp", None)
    published_at: datetime | None = None
    if ts:
        published_at = datetime.fromtimestamp(ts, tz=timezone.utc)

    return DownloadInfo(
        file_path=Path(info["requested_downloads"][0]["filepath"]),
        description=info.get("description"),
        duration=info.get("duration"),
        published_at=published_at,
    )
