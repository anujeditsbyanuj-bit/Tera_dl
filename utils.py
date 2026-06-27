import math
import time


def humanbytes(size: int) -> str:
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(max(size, 1), 1024)))
    return f"{size / math.pow(1024, i):.2f} {units[min(i, 4)]}"


def progress_bar(pct: float, length: int = 10) -> str:
    filled = int(length * pct / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


def time_fmt(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_duration(secs: int) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def file_icon(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "mp4": "🎬", "mkv": "🎬", "avi": "🎬", "mov": "🎬",
        "webm": "🎬", "flv": "🎬", "m4v": "🎬",
        "mp3": "🎵", "flac": "🎵", "wav": "🎵", "aac": "🎵",
        "m4a": "🎵", "ogg": "🎵",
        "jpg": "🖼️", "jpeg": "🖼️", "png": "🖼️", "gif": "🖼️", "webp": "🖼️",
        "pdf": "📄", "doc": "📝", "docx": "📝", "txt": "📃",
        "xls": "📊", "xlsx": "📊", "ppt": "📊", "pptx": "📊",
        "zip": "🗜️", "rar": "🗜️", "7z": "🗜️", "tar": "🗜️",
        "apk": "📱", "exe": "💻", "iso": "💿",
    }.get(ext, "📁")


def is_video(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in ("mp4", "mkv", "avi", "mov", "webm", "flv", "m4v")


def is_audio(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in ("mp3", "flac", "wav", "aac", "m4a", "ogg")


def is_image(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "png", "gif", "webp")

def fmt_quality_label(quality: str) -> str:
    """Convert quality code to human readable label"""
    return {
        "M3U8_AUTO_1080":   "1080p HD",
        "M3U8_FLV_264_720": "720p HD",
        "M3U8_FLV_264_480": "480p",
        "M3U8_FLV_264_360": "360p",
        "AUTO": "Auto (Best)",
        "DIRECT": "Original",
    }.get(quality, quality)
