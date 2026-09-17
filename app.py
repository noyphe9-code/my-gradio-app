import os
import sys
import re
import time
import zipfile
import subprocess
import asyncio
import base64
import shutil
import tempfile
import uuid
import traceback
import mimetypes
from pathlib import Path

import gradio as gr
import edge_tts
import yt_dlp
from google import genai


# =========================================================
# UTF-8 ENVIRONMENT
# =========================================================

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")

try:
    sys.stdout.reconfigure(
        encoding="utf-8",
        errors="replace"
    )
    sys.stderr.reconfigure(
        encoding="utf-8",
        errors="replace"
    )
except Exception:
    pass


# =========================================================
# CONFIGURATION
# =========================================================

APP_TITLE = "AI Movie Recap Studio Pro"

MAX_VIDEO_MINUTES = 10

SAVED_API_KEY = ""

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]


# =========================================================
# VOICES
# =========================================================

VOICES_BY_LANG = {

    "မြန်မာ (Burmese Voice)": {
        "Thiha (အမျိုးသားအသံ) - Natural":
            "my-MM-ThihaNeural",

        "Nilar (အမျိုးသမီးအသံ) - Natural":
            "my-MM-NilarNeural",
    },

    "English Voice": {
        "Andrew (Male - Natural)":
            "en-US-AndrewNeural",

        "Ava (Female - Natural)":
            "en-US-AvaNeural",
    },

    "ไทย (Thai Voice)": {
        "Niwat (ผู้ชาย - ธรรมชาติ)":
            "th-TH-NiwatNeural",

        "Premwadee (ผู้หญิง - ธรรมชาติ)":
            "th-TH-PremwadeeNeural",
    },

    "中文 (Chinese Voice)": {
        "Yunxi (男声 - 自然)":
            "zh-CN-YunxiNeural",

        "Xiaoxiao (女声 - 自然)":
            "zh-CN-XiaoxiaoNeural",
    }
}


SUBTITLE_LANG_CHOICES = [
    "မြန်မာ (Burmese)",
    "English",
    "ไทย (Thai)",
    "中文 (Chinese)"
]


# =========================================================
# GLOBAL TEMP DIRECTORY
# =========================================================

APP_TEMP_DIR = os.path.join(
    tempfile.gettempdir(),
    "ai_movie_recap_studio"
)

os.makedirs(
    APP_TEMP_DIR,
    exist_ok=True
)


# =========================================================
# BASIC HELPERS
# =========================================================

def safe_error(exc):
    """
    Unicode-safe error string.
    """

    try:
        return (
            str(exc)
            .encode("utf-8", "replace")
            .decode("utf-8", "replace")
        )
    except Exception:
        try:
            return repr(exc)
        except Exception:
            return "Unknown error"


def as_filepath(value):
    """
    Normalize Gradio file/video/image input.
    """

    if not value:
        return None

    if isinstance(value, dict):

        value = (
            value.get("path")
            or value.get("name")
            or value.get("orig_name")
        )

    try:
        if isinstance(
            value,
            (str, bytes, os.PathLike)
        ):
            path = os.fspath(value)
        else:
            return None
    except Exception:
        return None

    if not path:
        return None

    if os.path.exists(path):
        return path

    return None


def create_job_dir():
    """
    Create an isolated directory for every operation.
    """

    job_id = uuid.uuid4().hex

    path = os.path.join(
        APP_TEMP_DIR,
        job_id
    )

    os.makedirs(
        path,
        exist_ok=True
    )

    return path


def cleanup_file(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


# =========================================================
# API KEY
# =========================================================

def save_api_key(api_key):

    global SAVED_API_KEY

    if not api_key or not api_key.strip():

        return (
            "⚠️ Gemini API Key ထည့်ပေးပါ။"
        )

    SAVED_API_KEY = api_key.strip()

    return (
        "✅ Gemini API Key ကို "
        "အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
    )


# =========================================================
# FFMPEG HELPERS
# =========================================================

def command_exists(command):

    return shutil.which(command) is not None


def get_video_duration(video_path):

    if not video_path:
        return None

    if not os.path.exists(video_path):
        return None

    if not command_exists("ffprobe"):
        return None

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30
        )

        if (
            result.returncode == 0
            and result.stdout.strip()
        ):
            return float(
                result.stdout.strip()
            )

    except Exception as e:

        print(
            "Duration Error:",
            safe_error(e)
        )

    return None


def has_audio_stream(video_path):

    if not video_path:
        return False

    if not os.path.exists(video_path):
        return False

    if not command_exists("ffprobe"):
        return False

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15
        )

        return (
            "audio"
            in result.stdout.lower()
        )

    except Exception:
        return False


def validate_video_duration(video_path):

    duration = get_video_duration(
        video_path
    )

    if duration is None:

        return (
            True,
            "ℹ️ Video duration ကို "
            "အတည်မပြုနိုင်သေးပါ။"
        )

    minutes = duration / 60.0

    if minutes > MAX_VIDEO_MINUTES:

        return (
            False,
            f"⚠️ Video သည် "
            f"{minutes:.1f} မိနစ်ရှိပါသည်။ "
            f"အများဆုံး "
            f"{MAX_VIDEO_MINUTES} မိနစ်အထိသာ "
            f"လက်ခံပါသည်။"
        )

    return (
        True,
        f"✅ Video Length: "
        f"{minutes:.1f} မိနစ်"
    )


# =========================================================
# SCENE DETECTION
# =========================================================

def detect_scene_boundaries(
    video_path,
    threshold=0.35
):

    if not video_path:
        return []

    if not os.path.exists(video_path):
        return []

    if not command_exists("ffmpeg"):
        return []

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        video_path,
        "-vf",
        (
            "select='gt(scene,"
            f"{threshold})',showinfo"
        ),
        "-an",
        "-f",
        "null",
        "-"
    ]

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300
        )

        values = re.findall(
            r"pts_time:([0-9]+(?:\.[0-9]+)?)",
            result.stderr
        )

        return sorted(
            {
                float(x)
                for x in values
            }
        )

    except Exception as e:

        print(
            "Scene detection skipped:",
            safe_error(e)
        )

        return []


def detect_freeze_intervals(
    video_path,
    min_duration=1.5
):

    if not video_path:
        return []

    if not os.path.exists(video_path):
        return []

    if not command_exists("ffmpeg"):
        return []

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        video_path,
        "-vf",
        (
            "freezedetect="
            f"n=-60dB:d={min_duration}"
        ),
        "-an",
        "-f",
        "null",
        "-"
    ]

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300
        )

        log = result.stderr

        starts = [
            float(x)
            for x in re.findall(
                r"freeze_start:([0-9]+(?:\.[0-9]+)?)",
                log
            )
        ]

        ends = [
            float(x)
            for x in re.findall(
                r"freeze_end:([0-9]+(?:\.[0-9]+)?)",
                log
            )
        ]

        intervals = []

        for i, start in enumerate(starts):

            if i >= len(ends):
                continue

            end = ends[i]

            if (
                end - start
                >= min_duration
            ):
                intervals.append(
                    (start, end)
                )

        return intervals

    except Exception as e:

        print(
            "Freeze detection skipped:",
            safe_error(e)
        )

        return []


def build_keep_intervals(
    duration,
    removed_intervals,
    min_clip=0.12
):

    if not duration:
        return []

    if duration <= 0:
        return []

    cursor = 0.0
    keep = []

    for start, end in sorted(
        removed_intervals
    ):

        start = max(
            0.0,
            float(start)
        )

        end = min(
            duration,
            float(end)
        )

        if start > cursor + min_clip:

            keep.append(
                (
                    cursor,
                    start
                )
            )

        cursor = max(
            cursor,
            end
        )

    if duration > cursor + min_clip:

        keep.append(
            (
                cursor,
                duration
            )
        )

    return keep or [
        (0.0, duration)
    ]


def prepare_scene_aware_video(
    source_video,
    output_filename=None
):

    """
    Optional scene/freeze processing.

    If anything fails, original video is returned.
    """

    if not source_video:
        return source_video

    duration = get_video_duration(
        source_video
    )

    if not duration or duration < 2:

        return source_video

    try:

        scene_points = (
            detect_scene_boundaries(
                source_video
            )
        )

        freeze_intervals = (
            detect_freeze_intervals(
                source_video
            )
        )

        print(
            "Scene changes:",
            len(scene_points)
        )

        print(
            "Freeze intervals:",
            len(freeze_intervals)
        )

        removed = [
            (a, b)
            for a, b in freeze_intervals
            if (
                b - a >= 1.5
                and
                b - a <= duration * 0.5
            )
        ]

        keep = build_keep_intervals(
            duration,
            removed
        )

        if len(keep) <= 1:

            return source_video

        if output_filename is None:

            output_filename = os.path.join(
                os.path.dirname(source_video),
                "scene_aware_source.mp4"
            )

        chains = []

        audio_exists = (
            has_audio_stream(
                source_video
            )
        )

        for i, (
            start,
            end
        ) in enumerate(keep):

            seg_dur = max(
                0.01,
                end - start
            )

            fade_d = min(
                0.08,
                seg_dur / 3.0
            )

            chains.append(
                f"[0:v]"
                f"trim=start={start:.3f}:end={end:.3f},"
                f"setpts=PTS-STARTPTS,"
                f"fade=t=in:st=0:d={fade_d:.3f},"
                f"fade=t=out:"
                f"st={max(0, seg_dur-fade_d):.3f}:"
                f"d={fade_d:.3f}"
                f"[v{i}]"
            )

            if audio_exists:

                chains.append(
                    f"[0:a]"
                    f"atrim=start={start:.3f}:end={end:.3f},"
                    f"asetpts=PTS-STARTPTS"
                    f"[a{i}]"
                )

        if audio_exists:

            inputs = "".join(
                f"[v{i}][a{i}]"
                for i in range(len(keep))
            )

            chains.append(
                f"{inputs}"
                f"concat=n={len(keep)}:v=1:a=1"
                f"[vout][aout]"
            )

        else:

            inputs = "".join(
                f"[v{i}]"
                for i in range(len(keep))
            )

            chains.append(
                f"{inputs}"
                f"concat=n={len(keep)}:v=1:a=0"
                f"[vout]"
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            source_video,
            "-filter_complex",
            ";".join(chains),
            "-map",
            "[vout]"
        ]

        if audio_exists:

            cmd += [
                "-map",
                "[aout]",
                "-c:a",
                "aac",
                "-b:a",
                "128k"
            ]

        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            output_filename
        ]

        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900
        )

        if os.path.exists(
            output_filename
        ):
            return output_filename

    except Exception as e:

        print(
            "Scene-aware processing skipped:",
            safe_error(e)
        )

    return source_video


# =========================================================
# VIDEO DOWNLOAD
# =========================================================

def download_video_from_link(
    link
):

    if not link:
        return None

    link = link.strip()

    if not link:
        return None

    job_dir = create_job_dir()

    output_template = os.path.join(
        job_dir,
        "downloaded_video.%(ext)s"
    )

    ydl_opts = {

        "format":
            "bestvideo[ext=mp4]+bestaudio/"
            "best[ext=mp4]/best",

        "outtmpl":
            output_template,

        "quiet":
            True,

        "no_warnings":
            True,

        "noplaylist":
            True,

        "overwrites":
            True,

        "windowsfilenames":
            True,

        "merge_output_format":
            "mp4",

        "user_agent":
            (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0.0.0 "
                "Safari/537.36"
            ),
    }

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                link,
                download=True
            )

            filename = (
                ydl.prepare_filename(
                    info
                )
            )

            if os.path.exists(
                filename
            ):
                return filename

            base = os.path.splitext(
                filename
            )[0]

            for ext in [
                ".mp4",
                ".mkv",
                ".webm",
                ".mov"
            ]:

                candidate = (
                    base + ext
                )

                if os.path.exists(
                    candidate
                ):
                    return candidate

    except Exception as e:

        print(
            "Download Error:",
            safe_error(e)
        )

    return None


# =========================================================
# UNICODE-SAFE GEMINI UPLOAD
# =========================================================

def get_safe_video_extension(
    video_path
):

    source = as_filepath(
        video_path
    )

    if not source:
        return ".mp4"

    ext = os.path.splitext(
        source
    )[1].lower()

    allowed = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v",
        ".mpeg",
        ".mpg"
    }

    if ext in allowed:
        return ext

    return ".mp4"


def detect_video_mime(
    video_path
):

    ext = os.path.splitext(
        video_path
    )[1].lower()

    mime_map = {

        ".mp4":
            "video/mp4",

        ".mov":
            "video/quicktime",

        ".mkv":
            "video/x-matroska",

        ".webm":
            "video/webm",

        ".avi":
            "video/x-msvideo",

        ".m4v":
            "video/x-m4v",

        ".mpeg":
            "video/mpeg",

        ".mpg":
            "video/mpeg",
    }

    return mime_map.get(
        ext,
        "video/mp4"
    )


def make_gemini_safe_video(
    video_path
):

    """
    VERY IMPORTANT:

    Original:
        မြန်မာဇာတ်ကား.mp4

    Gemini copy:
        input_video_a8f...mp4

    Directory and filename are ASCII-safe.
    """

    source = as_filepath(
        video_path
    )

    if not source:
        raise FileNotFoundError(
            "Video file မတွေ့ပါ။"
        )

    if not os.path.isfile(
        source
    ):
        raise FileNotFoundError(
            "Video file မရှိတော့ပါ။"
        )

    safe_dir = os.path.join(
        APP_TEMP_DIR,
        "gemini_upload"
    )

    os.makedirs(
        safe_dir,
        exist_ok=True
    )

    ext = get_safe_video_extension(
        source
    )

    safe_name = (
        "input_video_"
        + uuid.uuid4().hex
        + ext
    )

    destination = os.path.join(
        safe_dir,
        safe_name
    )

    shutil.copyfile(
        source,
        destination
    )

    if not os.path.isfile(
        destination
    ):
        raise IOError(
            "Gemini upload အတွက် "
            "temporary video မဖန်တီးနိုင်ပါ။"
        )

    return destination


def upload_video_to_gemini_safe(
    client,
    video_path
):

    safe_path = (
        make_gemini_safe_video(
            video_path
        )
    )

    mime_type = (
        detect_video_mime(
            safe_path
        )
    )

    print(
        "================================"
    )

    print(
        "GEMINI SAFE VIDEO UPLOAD"
    )

    print(
        "Original:",
        repr(video_path)
    )

    print(
        "Safe:",
        safe_path
    )

    print(
        "MIME:",
        mime_type
    )

    print(
        "================================"
    )

    try:

        # google-genai upload.
        #
        # ASCII-only path is the critical
        # part for the Unicode filename bug.

        try:

            uploaded = client.files.upload(
                file=safe_path,
                config={
                    "mime_type":
                        mime_type
                }
            )

        except TypeError:

            # Compatibility fallback
            # for SDK versions that do not
            # accept dict config.

            uploaded = client.files.upload(
                file=safe_path
            )

        if not uploaded:

            raise RuntimeError(
                "Gemini က upload result "
                "မပြန်ပါ။"
            )

        print(
            "Upload successful:",
            getattr(
                uploaded,
                "name",
                None
            )
        )

        return (
            uploaded,
            safe_path
        )

    except Exception as e:

        traceback.print_exc()

        cleanup_file(
            safe_path
        )

        raise RuntimeError(
            "Gemini Video Upload Error:\n"
            + safe_error(e)
        )


def wait_for_gemini_video(
    client,
    uploaded_file,
    timeout=900
):

    start = time.time()

    while True:

        state = getattr(
            uploaded_file,
            "state",
            None
        )

        state_name = (
            getattr(
                state,
                "name",
                ""
            )
            if state
            else ""
        )

        print(
            "Gemini video state:",
            state_name
        )

        if state_name == "ACTIVE":

            return uploaded_file

        if state_name == "FAILED":

            raise RuntimeError(
                "Gemini Video Processing FAILED."
            )

        if (
            time.time() - start
            > timeout
        ):

            raise TimeoutError(
                "Gemini Video Processing "
                "အချိန်ကြာလွန်းနေပါသည်။"
            )

        time.sleep(3)

        uploaded_file = (
            client.files.get(
                name=uploaded_file.name
            )
        )


# =========================================================
# SCRIPT CLEANING
# =========================================================

def clean_script_for_display(
    script_text
):

    if not script_text:
        return ""

    cleaned = []

    for line in script_text.splitlines():

        line = line.strip()

        if not line:
            continue

        line = (
            line
            .replace("**", "")
            .replace("__", "")
            .replace("`", "")
        )

        line = re.sub(
            r"^\s*"
            r"\[(?:Visual|Scene|Video|Audio|"
            r"Camera|Action|Narration|Narrator|"
            r"Dialogue|Intro)\]"
            r"\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        if line.lower() in [
            "movie recap",
            "recap script",
            "burmese recap script",
            "script"
        ]:
            continue

        if line.startswith("---"):
            continue

        cleaned.append(
            line.strip()
        )

    return "\n".join(
        cleaned
    )


def clean_script_for_tts(
    script_text
):

    text = clean_script_for_display(
        script_text
    )

    if not text:
        return ""

    cleaned = []

    for line in text.splitlines():

        line = re.sub(
            r"^\s*"
            r"(?:Narrator|Narration|"
            r"Voice[- ]?over|"
            r"ဇာတ်ညွှန်း|"
            r"ဇာတ်ကောင်\s*[^:：]{0,40}|"
            r"Character\s*[^:：]{0,40}|"
            r"[A-Za-z][A-Za-z0-9 _-]{0,30})"
            r"\s*[:：-]\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        line = line.strip()

        if line:
            cleaned.append(
                line
            )

    return "\n".join(
        cleaned
    )


def has_foreign_text(
    text
):

    if not text:
        return False

    return bool(
        re.search(
            r"[a-zA-Z]{3,}",
            text
        )
        or
        re.search(
            r"[\u0E00-\u0E7F]",
            text
        )
        or
        re.search(
            r"[\u4E00-\u9FFF]",
            text
        )
    )


# =========================================================
# SRT
# =========================================================

def split_into_two_lines(
    text,
    max_line_len=20
):

    text = text.strip()

    if len(text) <= max_line_len:
        return text

    words = text.split(" ")

    if len(words) >= 2:

        mid = len(words) // 2

        return (
            " ".join(words[:mid])
            + "\n"
            + " ".join(words[mid:])
        )

    mid = len(text) // 2

    return (
        text[:mid]
        + "\n"
        + text[mid:]
    )


def seconds_to_srt_time(
    seconds
):

    seconds = max(
        0,
        float(seconds)
    )

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = int(
        seconds % 60
    )

    millis = int(
        round(
            (seconds - int(seconds))
            * 1000
        )
    )

    if millis >= 1000:

        millis = 0
        secs += 1

    if secs >= 60:

        secs = 0
        minutes += 1

    if minutes >= 60:

        minutes = 0
        hours += 1

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{millis:03d}"
    )


def generate_srt_and_zip(
    script_text,
    total_target_duration=None,
    prefix="recap_sub",
    output_dir=None
):

    clean_text = clean_script_for_tts(
        script_text
    )

    if not clean_text:
        return None, None

    raw_lines = [
        x.strip()
        for x in clean_text.splitlines()
        if x.strip()
    ]

    if not raw_lines:
        return None, None

    formatted_chunks = []

    for line in raw_lines:

        if len(line) > 40:

            parts = re.split(
                r"(?<=[။၊.,!?])\s*",
                line
            )

            for p in parts:

                if p.strip():

                    formatted_chunks.append(
                        split_into_two_lines(
                            p.strip()
                        )
                    )

        else:

            formatted_chunks.append(
                split_into_two_lines(
                    line
                )
            )

    if output_dir is None:

        output_dir = create_job_dir()

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    srt_filename = os.path.join(
        output_dir,
        f"{prefix}_subtitle.srt"
    )

    zip_filename = os.path.join(
        output_dir,
        f"{prefix}_subtitle.zip"
    )

    entries = []

    if (
        total_target_duration
        and total_target_duration > 0
    ):

        total_chars = sum(
            len(
                x.replace(
                    "\n",
                    ""
                )
            )
            for x in formatted_chunks
        )

        current_time = 0.0

        for line in formatted_chunks:

            chars = len(
                line.replace(
                    "\n",
                    ""
                )
            )

            weight = (
                chars
                / max(
                    1,
                    total_chars
                )
            )

            dur = max(
                1.5,
                weight
                * total_target_duration
            )

            start = current_time

            end = min(
                total_target_duration,
                current_time + dur
            )

            if end <= start:

                continue

            entries.append(
                (
                    start,
                    end,
                    line
                )
            )

            current_time = end

            if current_time >= (
                total_target_duration
            ):
                break

    else:

        current_time = 0.0

        for line in formatted_chunks:

            dur = max(
                2.0,
                min(
                    6.0,
                    len(
                        line.replace(
                            "\n",
                            ""
                        )
                    ) / 10.0
                )
            )

            start = current_time

            end = current_time + dur

            entries.append(
                (
                    start,
                    end,
                    line
                )
            )

            current_time = end

    srt_parts = []

    for idx, (
        start,
        end,
        line
    ) in enumerate(
        entries,
        1
    ):

        srt_parts.append(
            f"{idx}\n"
            f"{seconds_to_srt_time(start)} "
            f"--> "
            f"{seconds_to_srt_time(end)}\n"
            f"{line}\n"
        )

    srt_content = "\n".join(
        srt_parts
    )

    with open(
        srt_filename,
        "w",
        encoding="utf-8-sig",
        newline="\n"
    ) as f:

        f.write(
            srt_content
        )

    with zipfile.ZipFile(
        zip_filename,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:

        zipf.write(
            srt_filename,
            arcname=os.path.basename(
                srt_filename
            )
        )

    return (
        srt_filename,
        zip_filename
    )


# =========================================================
# GEMINI PROMPT
# =========================================================

def get_target_language(
    voice_language
):

    if "English" in voice_language:
        return "English"

    if "Thai" in voice_language:
        return "Thai"

    if "Chinese" in voice_language:
        return "Chinese"

    return "Burmese"


def build_recap_prompt(
    selected_ratio,
    voice_language,
    video_duration=None
):

    target_language = (
        get_target_language(
            voice_language
        )
    )

    duration_text = ""

    if video_duration:

        duration_text = (
            f"\nVideo length: "
            f"{video_duration:.1f} seconds."
        )

    return f"""
You are an expert movie recap writer and scene-by-scene visual storyteller.

Target language: {target_language}
Target aspect ratio: {selected_ratio}
{duration_text}

Analyze ONLY the video that is provided.

Write a complete beginning-to-end movie recap.

Follow the exact chronological order of the video.

Do not skip:
- opening
- important visual actions
- transitions
- reactions
- major events
- turning points
- suspense
- climax
- ending

The narration must be natural for voice-over and TTS.

Describe visible actions, locations, facial expressions,
emotions, suspense and cause-and-effect clearly but concisely.

If dialogue is clearly audible:
include the meaning of the dialogue.

Do NOT invent dialogue.

Do NOT invent character names.

If a person's name is not clearly known:
use Character 1, Character 2, etc.

Use this format:

Narrator: narration

Character 1: dialogue

Character 2: dialogue

Rules:

1. One speaker per paragraph.
2. Natural spoken {target_language}.
3. Strong hook at the beginning.
4. Smooth transitions between scenes.
5. Exciting but accurate narration.
6. Preserve the meaning of audible dialogue.
7. Do not invent facts.
8. Do not invent events.
9. Do not invent motives.
10. Do not output timestamps.
11. Do not output markdown.
12. Do not output bullet points.
13. Do not output scene numbers.
14. Do not output camera instructions.
15. Do not output production instructions.
16. Output ONLY the final recap script.
"""


# =========================================================
# GEMINI GENERATION
# =========================================================

def generate_with_retry(
    client,
    uploaded_file,
    prompt
):

    retry_delays = [
        3,
        7
    ]

    last_error = None

    for model_name in GEMINI_MODELS:

        print(
            "Trying Gemini model:",
            model_name
        )

        for attempt in range(
            len(retry_delays) + 1
        ):

            try:

                response = (
                    client.models.generate_content(
                        model=model_name,
                        contents=[
                            uploaded_file,
                            prompt
                        ]
                    )
                )

                if (
                    response
                    and response.text
                ):

                    print(
                        "Gemini success:",
                        model_name
                    )

                    return (
                        response.text,
                        model_name
                    )

                last_error = RuntimeError(
                    f"{model_name} "
                    "returned empty response."
                )

            except Exception as e:

                last_error = e

                print(
                    "Gemini generation error:",
                    model_name,
                    safe_error(e)
                )

                if (
                    attempt
                    < len(retry_delays)
                ):

                    time.sleep(
                        retry_delays[
                            attempt
                        ]
                    )

    raise RuntimeError(
        "Gemini model အားလုံး "
        "မအောင်မြင်ပါ။\n"
        "Last Error: "
        + safe_error(
            last_error
        )
    )


def run_gemini_video_analysis(
    target_media,
    ratio_choice,
    voice_language="မြန်မာ (Burmese Voice)"
):

    global SAVED_API_KEY

    if not SAVED_API_KEY:

        raise ValueError(
            "Gemini API Key မရှိသေးပါ။ "
            "🔑 API Key Setting ထဲတွင် "
            "အရင်ထည့်သွင်းပေးပါ။"
        )

    target_media = as_filepath(
        target_media
    )

    if not target_media:

        raise FileNotFoundError(
            "Video file မတွေ့ပါ။"
        )

    if not os.path.isfile(
        target_media
    ):

        raise FileNotFoundError(
            "Video file မရှိတော့ပါ။"
        )

    valid, duration_msg = (
        validate_video_duration(
            target_media
        )
    )

    if not valid:
        raise ValueError(
            duration_msg
        )

    client = genai.Client(
        api_key=SAVED_API_KEY
    )

    safe_upload_path = None

    try:

        uploaded_file, safe_upload_path = (
            upload_video_to_gemini_safe(
                client,
                target_media
            )
        )

        uploaded_file = (
            wait_for_gemini_video(
                client,
                uploaded_file
            )
        )

        video_duration = (
            get_video_duration(
                target_media
            )
        )

        prompt = build_recap_prompt(
            ratio_choice,
            voice_language,
            video_duration
        )

        script_text, used_model = (
            generate_with_retry(
                client,
                uploaded_file,
                prompt
            )
        )

        script_text = (
            clean_script_for_display(
                script_text
            )
        )

        if not script_text.strip():

            raise RuntimeError(
                "Gemini က Script အလွတ် "
                "ပြန်ပေးပါသည်။"
            )

        return (
            script_text,
            used_model,
            duration_msg
        )

    except UnicodeEncodeError as e:

        traceback.print_exc()

        raise RuntimeError(
            "❌ Unicode / ASCII Encoding Error\n\n"
            "Video filename ကို ASCII-safe "
            "အဖြစ်ပြောင်းပြီး Gemini Upload "
            "ပြန်လုပ်ထားသော်လည်း Error ဖြစ်နေပါသည်။\n\n"
            f"Details: {safe_error(e)}"
        )

    except Exception as e:

        traceback.print_exc()

        raise RuntimeError(
            safe_error(e)
        )

    finally:

        cleanup_file(
            safe_upload_path
        )


# =========================================================
# TRANSLATION
# =========================================================

def translate_to_target_language(
    text,
    target_lang
):

    global SAVED_API_KEY

    if not text or not text.strip():
        return ""

    if not SAVED_API_KEY:

        raise ValueError(
            "Gemini API Key မရှိသေးပါ။ "
            "API Key Setting တွင် "
            "အရင်ထည့်ပါ။"
        )

    client = genai.Client(
        api_key=SAVED_API_KEY
    )

    prompt = f"""
You are a professional movie subtitle translator.

Translate the following text into:
{target_lang}

Requirements:

- Preserve the original meaning.
- Do not add information.
- Do not remove important information.
- Keep speaker labels if present.
- Make the language natural for spoken narration.
- Make subtitle lines concise.
- Do not output markdown.
- Output ONLY the translation.

Text:

{text}
"""

    last_error = None

    for model_name in GEMINI_MODELS:

        try:

            response = (
                client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
            )

            if (
                response
                and response.text
            ):

                return clean_script_for_tts(
                    response.text
                )

        except Exception as e:

            last_error = e

    if last_error:

        raise RuntimeError(
            safe_error(last_error)
        )

    return text


# =========================================================
# TTS
# =========================================================

async def generate_tts_file(
    text,
    voice_code,
    speed_percent,
    output_name
):

    clean_text = (
        clean_script_for_tts(
            text
        )
    )

    if not clean_text:
        return None

    rate_str = (
        f"{int(speed_percent):+d}%"
    )

    communicate = edge_tts.Communicate(
        clean_text,
        voice_code,
        rate=rate_str
    )

    await communicate.save(
        output_name
    )

    return output_name


def run_tts_sync(
    text,
    voice_code,
    speed_percent,
    output_name
):

    return asyncio.run(
        generate_tts_file(
            text,
            voice_code,
            speed_percent,
            output_name
        )
    )


# =========================================================
# TAB 1
# =========================================================

def tab1_analyze(
    v_file,
    v_url,
    ratio
):

    target = (
        as_filepath(v_file)
        or
        download_video_from_link(
            v_url
        )
    )

    if not target:

        return (
            "",
            "",
            "⚠️ Video ရှာမတွေ့ပါ။ "
            "Video File သို့မဟုတ် Link ထည့်ပါ။",
            None,
            None
        )

    try:

        script_text, model, duration_msg = (
            run_gemini_video_analysis(
                target,
                ratio,
                "မြန်မာ (Burmese Voice)"
            )
        )

        job_dir = create_job_dir()

        srt_file, zip_file = (
            generate_srt_and_zip(
                script_text,
                prefix="tab1_recap",
                output_dir=job_dir
            )
        )

        status = (
            "✅ Script ရေးသားပြီးပါပြီ!\n\n"
            f"🤖 Model: {model}\n"
            f"{duration_msg}"
        )

        return (
            script_text,
            script_text,
            status,
            srt_file,
            zip_file
        )

    except Exception as e:

        traceback.print_exc()

        return (
            "",
            "",
            "❌ Error:\n"
            + safe_error(e),
            None,
            None
        )


# =========================================================
# TAB 2 TRANSLATE
# =========================================================

def handle_direct_translate(
    text
):

    if not text or not text.strip():

        return (
            "",
            "⚠️ ဘာသာပြန်ရန် "
            "စာသားထည့်ပေးပါ။"
        )

    try:

        translated = (
            translate_to_target_language(
                text,
                "Burmese (မြန်မာ)"
            )
        )

        return (
            translated,
            "✅ မြန်မာဘာသာသို့ "
            "အောင်မြင်စွာ ပြန်ဆိုပြီးပါပြီ။"
        )

    except Exception as e:

        return (
            text,
            "❌ Translation Error:\n"
            + safe_error(e)
        )


def tab2_tts_with_auto_translate(
    text,
    voice_label,
    speed
):

    current_text = text
    trans_note = ""

    if has_foreign_text(
        current_text
    ):

        try:

            current_text = (
                translate_to_target_language(
                    current_text,
                    "Burmese (မြန်မာ)"
                )
            )

            trans_note = (
                " (အလိုအလျောက် "
                "မြန်မာပြန်ဆိုထားပါသည်)"
            )

        except Exception as e:

            print(
                "Auto translation failed:",
                safe_error(e)
            )

    voice_code = (
        VOICES_BY_LANG[
            "မြန်မာ (Burmese Voice)"
        ].get(
            voice_label,
            "my-MM-ThihaNeural"
        )
    )

    job_dir = create_job_dir()

    audio_path = os.path.join(
        job_dir,
        "tab2_voice.mp3"
    )

    try:

        audio_file = run_tts_sync(
            current_text,
            voice_code,
            speed,
            audio_path
        )

        if not audio_file:

            raise RuntimeError(
                "အသံဖိုင် မထုတ်နိုင်ပါ။"
            )

        audio_duration = (
            get_video_duration(
                audio_file
            )
            or
            10.0
        )

        srt_file, zip_file = (
            generate_srt_and_zip(
                current_text,
                total_target_duration=
                    audio_duration,
                prefix="tab2_output",
                output_dir=job_dir
            )
        )

        status = (
            "✅ အသံဖိုင် ဖန်တီးပြီးပါပြီ!"
            + trans_note
        )

        return (
            current_text,
            audio_file,
            audio_file,
            srt_file,
            zip_file,
            status
        )

    except Exception as e:

        traceback.print_exc()

        return (
            current_text,
            None,
            None,
            None,
            None,
            "❌ Error:\n"
            + safe_error(e)
        )


# =========================================================
# VOICE DROPDOWN
# =========================================================

def update_voice_choices(
    voice_lang
):

    voices = list(
        VOICES_BY_LANG[
            voice_lang
        ].keys()
    )

    return gr.Dropdown(
        choices=voices,
        value=voices[0]
    )


# =========================================================
# PREVIEW CSS
# =========================================================

def get_ratio_config(
    ratio
):

    configs = {

        "1:1": {
            "aspect": "1 / 1",
            "max_w": "450px"
        },

        "3:4": {
            "aspect": "3 / 4",
            "max_w": "380px"
        },

        "16:9": {
            "aspect": "16 / 9",
            "max_w": "640px"
        },

        "9:16": {
            "aspect": "9 / 16",
            "max_w": "340px"
        }
    }

    return configs.get(
        ratio,
        configs["9:16"]
    )


def get_ratio_css(
    ratio,
    container_id
):

    cfg = get_ratio_config(
        ratio
    )

    return f"""
<style>

#{container_id} {{
    width: 100% !important;
    max-width: {cfg["max_w"]} !important;
    margin: 0 auto !important;
}}

#{container_id} video {{
    width: 100% !important;
    aspect-ratio: {cfg["aspect"]} !important;
    object-fit: contain !important;
    background: #000 !important;
    border-radius: 12px !important;
}}

</style>
"""


# =========================================================
# COLOR HELPERS
# =========================================================

def hex_to_rgba(
    hex_code,
    opacity
):

    if not hex_code:
        hex_code = "#000000"

    hex_code = hex_code.lstrip("#")

    try:

        if len(hex_code) == 6:

            r = int(
                hex_code[0:2],
                16
            )

            g = int(
                hex_code[2:4],
                16
            )

            b = int(
                hex_code[4:6],
                16
            )

            opacity = max(
                0,
                min(
                    1,
                    float(opacity)
                )
            )

            return (
                f"rgba({r}, {g}, {b}, "
                f"{opacity})"
            )

    except Exception:
        pass

    return (
        "rgba(0,0,0,0.85)"
    )


def hex_to_ass_color(
    hex_str
):

    if not hex_str:
        return "&H00FFFFFF&"

    hex_str = (
        hex_str
        .lstrip("#")
    )

    if len(hex_str) == 6:

        r = hex_str[0:2]
        g = hex_str[2:4]
        b = hex_str[4:6]

        return (
            f"&H00{b}{g}{r}&"
        ).upper()

    return "&H00FFFFFF&"


# =========================================================
# TAB 3 LIVE PREVIEW
# =========================================================

def get_tab3_in_video_preview_html(
    sub_lang,
    ratio,
    video_speed,
    flip_h,
    scale_val,
    x_off,
    y_off,
    crop_w_pct,
    crop_h_pct,
    use_blur_bg,
    bg_color,
    bright_val,
    contrast_val,
    mask_enable,
    mask_type,
    mask_color,
    mask_opacity,
    mask_w,
    mask_h,
    mask_x,
    mask_y,
    logo_file,
    logo_size,
    logo_x,
    logo_y,
    font_family,
    font_size,
    font_color,
    outline_color,
    sub_x,
    sub_y
):

    cfg = get_ratio_config(
        ratio
    )

    try:
        video_speed = float(
            video_speed or 1.0
        )
    except Exception:
        video_speed = 1.0

    video_speed = max(
        0.5,
        min(
            2.0,
            video_speed
        )
    )

    try:
        scale_val = float(
            scale_val or 1.0
        )
    except Exception:
        scale_val = 1.0

    flip_x = (
        "-1"
        if flip_h
        else "1"
    )

    mask_top_pct = (
        50
        -
        (
            float(mask_y or 0)
            / 5.0
        )
    )

    sub_top_pct = (
        50
        -
        (
            float(sub_y or 0)
            / 5.0
        )
    )

    logo_top_pct = (
        50
        -
        (
            float(logo_y or 0)
            / 5.0
        )
    )

    # -----------------------------------------------------
    # MASK
    # -----------------------------------------------------

    mask_html = ""

    if mask_enable:

        mask_bg = hex_to_rgba(
            mask_color,
            mask_opacity
        )

        blur_style = ""

        if (
            mask_type
            ==
            "Blur (ဝေဝါးဖုံး)"
        ):

            blur_style = (
                "backdrop-filter:"
                "blur(14px);"
                "-webkit-backdrop-filter:"
                "blur(14px);"
            )

        mask_html = f"""
<div style="
    position:absolute;
    left:calc(50% + {mask_x}px);
    top:{mask_top_pct}%;
    transform:translate(-50%,-50%);
    width:{mask_w}%;
    height:{mask_h}px;
    background:{mask_bg};
    {blur_style}
    border-radius:6px;
    z-index:40;
    pointer-events:none;
"></div>
"""

    # -----------------------------------------------------
    # LOGO
    # -----------------------------------------------------

    logo_html = ""

    logo_file = as_filepath(
        logo_file
    )

    if logo_file:

        try:

            with open(
                logo_file,
                "rb"
            ) as f:

                encoded = (
                    base64.b64encode(
                        f.read()
                    ).decode(
                        "ascii"
                    )
                )

            ext = (
                os.path.splitext(
                    logo_file
                )[1]
                .lstrip(".")
                .lower()
            )

            if ext == "jpg":
                mime = "jpeg"
            else:
                mime = ext or "png"

            logo_src = (
                f"data:image/{mime};"
                f"base64,{encoded}"
            )

            logo_html = f"""
<img
    src="{logo_src}"
    style="
        position:absolute;
        left:calc(50% + {logo_x}px);
        top:{logo_top_pct}%;
        transform:translate(-50%,-50%);
        width:{logo_size}px;
        height:auto;
        z-index:50;
        pointer-events:none;
    "
/>
"""

        except Exception:
            logo_html = ""

    # -----------------------------------------------------
    # SAMPLE SUBTITLE
    # -----------------------------------------------------

    if sub_lang == "English":

        sample_l1 = (
            "A man standing on the mountain"
        )

        sample_l2 = (
            "( Sample English Subtitle )"
        )

    elif sub_lang == "ไทย (Thai)":

        sample_l1 = (
            "ชายคนหนึ่งยืนอยู่บนภูเขา"
        )

        sample_l2 = (
            "( ตัวอย่างซับไตเติลภาษาไทย )"
        )

    elif sub_lang == "中文 (Chinese)":

        sample_l1 = (
            "一个人站在高高的山顶上"
        )

        sample_l2 = (
            "( 示例中文字幕展示 )"
        )

    else:

        sample_l1 = (
            "လူတစ်ယောက် တောင်ပေါ်မှာ"
        )

        sample_l2 = (
            "( နမူနာ မြန်မာစာတန်းထိုး )"
        )

    # -----------------------------------------------------
    # SUBTITLE
    # -----------------------------------------------------

    sub_html = f"""
<div style="
    position:absolute;
    left:calc(50% + {sub_x}px);
    top:{sub_top_pct}%;
    transform:translate(-50%,-50%);
    width:92%;
    text-align:center;
    z-index:60;
    pointer-events:none;
">

<span style="
    display:block;
    font-family:'{font_family}',sans-serif;
    font-size:{font_size}px;
    line-height:1.35;
    color:{font_color};
    text-shadow:
        -2px -2px 0 {outline_color},
         2px -2px 0 {outline_color},
        -2px  2px 0 {outline_color},
         2px  2px 0 {outline_color},
         0 3px 6px rgba(0,0,0,.9);
    font-weight:800;
">
{sample_l1}
</span>

<span style="
    display:block;
    font-family:'{font_family}',sans-serif;
    font-size:{font_size}px;
    line-height:1.35;
    color:{font_color};
    text-shadow:
        -2px -2px 0 {outline_color},
         2px -2px 0 {outline_color},
        -2px  2px 0 {outline_color},
         2px  2px 0 {outline_color},
         0 3px 6px rgba(0,0,0,.9);
    font-weight:800;
">
{sample_l2}
</span>

</div>
"""

    # -----------------------------------------------------
    # CROP
    # -----------------------------------------------------

    crop_w_pct = max(
        30,
        min(
            100,
            float(crop_w_pct)
        )
    )

    crop_h_pct = max(
        30,
        min(
            100,
            float(crop_h_pct)
        )
    )

    inset_x = (
        100 - crop_w_pct
    ) / 2

    inset_y = (
        100 - crop_h_pct
    ) / 2

    clip_style = (
        f"clip-path:inset("
        f"{inset_y:.1f}% "
        f"{inset_x:.1f}% "
        f"{inset_y:.1f}% "
        f"{inset_x:.1f}%);"
    )

    # -----------------------------------------------------
    # HTML
    # -----------------------------------------------------

    return f"""
<style>

#tab3_preview_wrapper {{
    position:relative !important;
    width:100% !important;
    max-width:{cfg["max_w"]} !important;
    margin:0 auto !important;
}}

#tab3_preview_box {{
    width:100% !important;
    position:relative !important;
}}

#tab3_preview_box video {{
    width:100% !important;
    aspect-ratio:{cfg["aspect"]} !important;
    object-fit:contain !important;
    background:#000 !important;
    border-radius:14px !important;
    display:block !important;
}}

#tab3_inside_overlay {{
    position:absolute !important;
    inset:0 !important;
    width:100% !important;
    height:100% !important;
    pointer-events:none !important;
    overflow:hidden !important;
    border-radius:14px !important;
    z-index:30 !important;
}}

</style>

<div
    id="tab3_inside_overlay"
>
    {mask_html}
    {logo_html}
    {sub_html}
</div>
"""


# =========================================================
# TAB 3 FINAL RENDER
# =========================================================

def render_advanced_clip(
    source_video,
    tts_audio,
    srt_path,
    bgm_audio,
    enable_orig_audio,
    bgm_volume,
    ratio_choice,
    resolution_choice,
    quality_preset,
    video_speed,
    flip_h,
    scale_val,
    x_off,
    y_off,
    crop_w_pct,
    crop_h_pct,
    use_blur_bg,
    bg_color,
    bright_val,
    contrast_val,
    mask_enable,
    mask_type,
    mask_color,
    mask_opacity,
    mask_w,
    mask_h,
    mask_x,
    mask_y,
    logo_file,
    logo_size,
    logo_x,
    logo_y,
    font_family,
    font_size,
    font_color,
    outline_color,
    sub_x,
    sub_y,
    output_filename
):

    ratio_dims = {

        "1:1":
            (1080, 1080),

        "3:4":
            (810, 1080),

        "16:9":
            (1920, 1080),

        "9:16":
            (1080, 1920)
    }

    base_w, base_h = (
        ratio_dims.get(
            ratio_choice,
            (1080, 1920)
        )
    )

    resolution_map = {

        "480p":
            480,

        "720p":
            720,

        "1080p":
            1080,

        "1440p":
            1440,

        "Original Canvas":
            min(
                base_w,
                base_h
            )
    }

    short_edge = (
        resolution_map.get(
            resolution_choice,
            1080
        )
    )

    scale_ratio = (
        short_edge
        /
        min(
            base_w,
            base_h
        )
    )

    tw = max(
        2,
        int(
            round(
                base_w
                * scale_ratio
                / 2
            )
            * 2
        )
    )

    th = max(
        2,
        int(
            round(
                base_h
                * scale_ratio
                / 2
            )
            * 2
        )
    )

    quality_settings = {

        "Fast (သေးငယ်သောဖိုင်)": {
            "preset": "veryfast",
            "crf": "26"
        },

        "Balanced (အကြံပြု)": {
            "preset": "medium",
            "crf": "23"
        },

        "High Quality": {
            "preset": "slow",
            "crf": "20"
        },

        "Best Quality (ဖိုင်ကြီး)": {
            "preset": "slower",
            "crf": "18"
        }
    }

    quality = quality_settings.get(
        quality_preset,
        quality_settings[
            "Balanced (အကြံပြု)"
        ]
    )

    video_speed = max(
        0.5,
        min(
            2.0,
            float(video_speed or 1.0)
        )
    )

    scale_val = max(
        0.5,
        min(
            2.5,
            float(scale_val or 1.0)
        )
    )

    crop_w_pct = max(
        30,
        min(
            100,
            float(crop_w_pct)
        )
    )

    crop_h_pct = max(
        30,
        min(
            100,
            float(crop_h_pct)
        )
    )

    bright_val = float(
        bright_val or 1.0
    )

    contrast_val = float(
        contrast_val or 1.0
    )

    # -----------------------------------------------------
    # VIDEO SPEED
    # -----------------------------------------------------

    speed_filter = (
        f"setpts=PTS/{video_speed}"
    )

    flip_filter = (
        ",hflip"
        if flip_h
        else ""
    )

    # -----------------------------------------------------
    # CROP
    # -----------------------------------------------------

    crop_w = (
        f"iw*{crop_w_pct / 100:.4f}"
    )

    crop_h = (
        f"ih*{crop_h_pct / 100:.4f}"
    )

    crop_filter = (
        f"crop="
        f"{crop_w}:"
        f"{crop_h}:"
        f"(iw-{crop_w})/2:"
        f"(ih-{crop_h})/2"
    )

    # -----------------------------------------------------
    # COLOR
    # -----------------------------------------------------

    color_filter = (
        f"eq="
        f"brightness={bright_val - 1.0:.3f}:"
        f"contrast={contrast_val:.3f}"
    )

    # -----------------------------------------------------
    # VIDEO GRAPH
    # -----------------------------------------------------

    filters = []

    if use_blur_bg:

        filters.append(
            "[0:v]split=2"
            "[bg_src][fg_src]"
        )

        filters.append(
            f"[bg_src]"
            f"{speed_filter}"
            f"{flip_filter},"
            f"{color_filter},"
            f"scale={tw}:{th}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},"
            f"boxblur=20:8,"
            f"eq=brightness=-0.15"
            f"[bg]"
        )

        filters.append(
            f"[fg_src]"
            f"{speed_filter}"
            f"{flip_filter},"
            f"{crop_filter},"
            f"{color_filter},"
            f"scale="
            f"iw*{scale_val}:"
            f"ih*{scale_val}:"
            f"force_original_aspect_ratio=decrease"
            f"[fg]"
        )

        filters.append(
            f"[bg][fg]"
            f"overlay="
            f"(W-w)/2+({x_off}):"
            f"(H-h)/2+({y_off})"
            f"[vbase]"
        )

    else:

        bg_hex = (
            str(bg_color or "#000000")
            .lstrip("#")
        )

        filters.append(
            f"[0:v]"
            f"{speed_filter}"
            f"{flip_filter},"
            f"{crop_filter},"
            f"{color_filter},"
            f"scale="
            f"iw*{scale_val}:"
            f"ih*{scale_val}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad="
            f"{tw}:{th}:"
            f"(ow-iw)/2+({x_off}):"
            f"(oh-ih)/2+({y_off}):"
            f"color=0x{bg_hex}"
            f"[vbase]"
        )

    current_v = "vbase"

    # -----------------------------------------------------
    # MASK
    # -----------------------------------------------------

    if mask_enable:

        mw = max(
            2,
            int(
                tw
                * float(mask_w)
                / 100.0
            )
        )

        mh = max(
            2,
            int(
                float(mask_h)
                * th
                / 1920.0
                * 2.2
            )
        )

        mx = (
            f"(W-{mw})/2"
            f"+({mask_x})"
        )

        my = (
            f"(H*0.5)"
            f"-({mask_y}*(H/1000))"
            f"-({mh}/2)"
        )

        if (
            mask_type
            ==
            "Blur (ဝေဝါးဖုံး)"
        ):

            filters.append(
                f"[{current_v}]"
                f"split=2"
                "[mask_base]"
                "[mask_src]"
            )

            filters.append(
                f"[mask_src]"
                f"crop={mw}:{mh}:{mx}:{my},"
                f"boxblur=20:8"
                "[mask_blur]"
            )

            filters.append(
                f"[mask_base]"
                f"[mask_blur]"
                f"overlay={mx}:{my}"
                "[vmasked]"
            )

        else:

            mask_hex = (
                str(mask_color or "#000000")
                .lstrip("#")
            )

            filters.append(
                f"color="
                f"c=0x{mask_hex}"
                f"@{float(mask_opacity)}:"
                f"s={mw}x{mh}"
                "[mask_box]"
            )

            filters.append(
                f"[{current_v}]"
                f"[mask_box]"
                f"overlay={mx}:{my}"
                "[vmasked]"
            )

        current_v = "vmasked"

    # -----------------------------------------------------
    # INPUTS
    # -----------------------------------------------------

    inputs_cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        source_video,
        "-i",
        tts_audio
    ]

    next_input_idx = 2

    # -----------------------------------------------------
    # LOGO
    # -----------------------------------------------------

    logo_file = as_filepath(
        logo_file
    )

    if logo_file:

        inputs_cmd += [
            "-i",
            logo_file
        ]

        logo_idx = next_input_idx

        next_input_idx += 1

        lx = (
            f"(W-w)/2+({logo_x})"
        )

        ly = (
            f"(H*0.5)"
            f"-({logo_y}*(H/1000))"
            f"-(h/2)"
        )

        filters.append(
            f"[{logo_idx}:v]"
            f"scale={int(logo_size)}:-1"
            "[logo]"
        )

        filters.append(
            f"[{current_v}]"
            f"[logo]"
            f"overlay={lx}:{ly}"
            "[vlogo]"
        )

        current_v = "vlogo"

    # -----------------------------------------------------
    # SUBTITLE
    # -----------------------------------------------------

    primary_ass = (
        hex_to_ass_color(
            font_color
        )
    )

    outline_ass = (
        hex_to_ass_color(
            outline_color
        )
    )

    escaped_srt = (
        str(srt_path)
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )

    margin_v = max(
        10,
        int(
            (
                th / 2
            )
            -
            (
                float(sub_y)
                * (th / 1000)
            )
        )
    )

    subtitle_filter = (
        f"subtitles='{escaped_srt}':"
        f"force_style="
        f"'FontName={font_family},"
        f"FontSize={int(font_size)},"
        f"PrimaryColour={primary_ass},"
        f"OutlineColour={outline_ass},"
        f"BorderStyle=3,"
        f"Outline=3,"
        f"Shadow=2,"
        f"Alignment=2,"
        f"MarginV={margin_v}'"
    )

    filters.append(
        f"[{current_v}]"
        f"{subtitle_filter}"
        f"[vout]"
    )

    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    audio_filters = []

    tts_source_duration = (
        get_video_duration(
            tts_audio
        )
        or
        10.0
    )

    final_duration = (
        tts_source_duration
        /
        video_speed
    )

    if (
        enable_orig_audio
        and
        has_audio_stream(
            source_video
        )
    ):

        audio_filters.append(
            f"[0:a]"
            f"atempo={video_speed:.3f},"
            f"volume=0.20"
            f"[orig_a]"
        )

    else:

        audio_filters.append(
            f"aevalsrc=0:"
            f"d={final_duration:.3f}"
            f"[orig_a]"
        )

    audio_filters.append(
        f"[1:a]"
        f"atempo={video_speed:.3f},"
        f"volume=1.0"
        f"[tts_a]"
    )

    bgm_idx = None

    if (
        bgm_audio
        and
        os.path.exists(
            bgm_audio
        )
    ):

        inputs_cmd += [
            "-stream_loop",
            "-1",
            "-i",
            bgm_audio
        ]

        bgm_idx = next_input_idx

        next_input_idx += 1

        audio_filters.append(
            f"[{bgm_idx}:a]"
            f"volume={float(bgm_volume):.3f}"
            f"[bgm_a]"
        )

        audio_filters.append(
            "[orig_a]"
            "[tts_a]"
            "[bgm_a]"
            "amix="
            "inputs=3:"
            "duration=longest:"
            "dropout_transition=2"
            "[aout]"
        )

    else:

        audio_filters.append(
            "[orig_a]"
            "[tts_a]"
            "amix="
            "inputs=2:"
            "duration=longest:"
            "dropout_transition=2"
            "[aout]"
        )

    full_filter_complex = (
        ";".join(
            filters
        )
        + ";"
        + ";".join(
            audio_filters
        )
    )

    # -----------------------------------------------------
    # FINAL FFMPEG COMMAND
    # -----------------------------------------------------

    cmd = (
        inputs_cmd
        +
        [
            "-filter_complex",
            full_filter_complex,

            "-map",
            "[vout]",

            "-map",
            "[aout]",

            "-t",
            f"{final_duration:.3f}",

            "-threads",
            "0",

            "-filter_threads",
            "0",

            "-filter_complex_threads",
            "0",

            "-c:v",
            "libx264",

            "-preset",
            quality["preset"],

            "-crf",
            quality["crf"],

            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            "192k",

            "-movflags",
            "+faststart",

            "-map_metadata",
            "-1",

            output_filename
        ]
    )

    print(
        "Starting FFmpeg render..."
    )

    try:

        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800
        )

    except subprocess.CalledProcessError as e:

        print(
            "FFmpeg render failed:"
        )

        print(
            e.stderr
        )

        # -------------------------------------------------
        # FALLBACK
        # -------------------------------------------------

        fallback_vf = (
            f"scale={tw}:{th}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:"
            f"(ow-iw)/2:"
            f"(oh-ih)/2"
        )

        fallback_cmd = [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            source_video,
            "-i",
            tts_audio,
            "-vf",
            fallback_vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{final_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            output_filename
        ]

        subprocess.run(
            fallback_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800
        )

    if not os.path.exists(
        output_filename
    ):

        raise RuntimeError(
            "Final video file "
            "မထွက်လာပါ။"
        )

    return output_filename


# =========================================================
# TAB 3 AUTO PIPELINE
# =========================================================

def tab3_auto_pipeline(
    v_file,
    v_url,
    bgm_file,
    voice_lang,
    voice_label,
    speed,
    sub_lang,
    enable_orig_audio,
    bgm_vol,
    ratio,
    resolution_choice,
    quality_preset,
    video_speed,
    flip_h,
    scale_val,
    x_off,
    y_off,
    crop_w,
    crop_h,
    use_blur_bg,
    bg_color,
    bright_val,
    contrast_val,
    mask_enable,
    mask_type,
    mask_color,
    mask_opacity,
    mask_w,
    mask_h,
    mask_x,
    mask_y,
    logo_file,
    logo_size,
    logo_x,
    logo_y,
    font_family,
    font_size,
    font_color,
    outline_color,
    sub_x,
    sub_y
):

    target = (
        as_filepath(v_file)
        or
        download_video_from_link(
            v_url
        )
    )

    if not target:

        return (
            None,
            None,
            "",
            None,
            "⚠️ Video ရှာမတွေ့ပါ။"
        )

    job_dir = create_job_dir()

    try:

        # -------------------------------------------------
        # STEP 1 — GEMINI SCRIPT
        # -------------------------------------------------

        narration_script, model, duration_msg = (
            run_gemini_video_analysis(
                target,
                ratio,
                voice_lang
            )
        )

        # -------------------------------------------------
        # STEP 2 — SUBTITLE TRANSLATION
        # -------------------------------------------------

        source_language = (
            get_target_language(
                voice_lang
            )
        )

        subtitle_script = narration_script

        if (
            source_language == "Burmese"
            and
            sub_lang !=
            "မြန်မာ (Burmese)"
        ):

            subtitle_script = (
                translate_to_target_language(
                    narration_script,
                    sub_lang
                )
            )

        elif (
            source_language == "English"
            and
            sub_lang != "English"
        ):

            subtitle_script = (
                translate_to_target_language(
                    narration_script,
                    sub_lang
                )
            )

        elif (
            source_language == "Thai"
            and
            sub_lang != "ไทย (Thai)"
        ):

            subtitle_script = (
                translate_to_target_language(
                    narration_script,
                    sub_lang
                )
            )

        elif (
            source_language == "Chinese"
            and
            sub_lang != "中文 (Chinese)"
        ):

            subtitle_script = (
                translate_to_target_language(
                    narration_script,
                    sub_lang
                )
            )

        # -------------------------------------------------
        # STEP 3 — TTS
        # -------------------------------------------------

        voice_code = (
            VOICES_BY_LANG[
                voice_lang
            ].get(
                voice_label,
                list(
                    VOICES_BY_LANG[
                        voice_lang
                    ].values()
                )[0]
            )
        )

        audio_path = os.path.join(
            job_dir,
            "tab3_voice.mp3"
        )

        audio_file = run_tts_sync(
            narration_script,
            voice_code,
            speed,
            audio_path
        )

        if not audio_file:

            raise RuntimeError(
                "TTS အသံဖိုင် မထုတ်နိုင်ပါ။"
            )

        # -------------------------------------------------
        # STEP 4 — SRT
        # -------------------------------------------------

        audio_duration = (
            get_video_duration(
                audio_file
            )
            or
            10.0
        )

        final_timeline_duration = (
            audio_duration
            /
            max(
                0.5,
                min(
                    2.0,
                    float(
                        video_speed
                        or
                        1.0
                    )
                )
            )
        )

        srt_file, zip_file = (
            generate_srt_and_zip(
                subtitle_script,
                total_target_duration=
                    final_timeline_duration,
                prefix="tab3_sub",
                output_dir=job_dir
            )
        )

        # -------------------------------------------------
        # STEP 5 — SCENE AWARE VIDEO
        # -------------------------------------------------

        scene_video = os.path.join(
            job_dir,
            "scene_aware_source.mp4"
        )

        render_source = (
            prepare_scene_aware_video(
                target,
                scene_video
            )
        )

        # -------------------------------------------------
        # STEP 6 — FINAL OUTPUT
        # -------------------------------------------------

        final_video = os.path.join(
            job_dir,
            "final_movie_recap.mp4"
        )

        final_video = (
            render_advanced_clip(
                source_video=render_source,
                tts_audio=audio_file,
                srt_path=srt_file,
                bgm_audio=as_filepath(
                    bgm_file
                ),
                enable_orig_audio=
                    enable_orig_audio,
                bgm_volume=bgm_vol,
                ratio_choice=ratio,
                resolution_choice=
                    resolution_choice,
                quality_preset=
                    quality_preset,
                video_speed=
                    video_speed,
                flip_h=flip_h,
                scale_val=scale_val,
                x_off=x_off,
                y_off=y_off,
                crop_w_pct=crop_w,
                crop_h_pct=crop_h,
                use_blur_bg=
                    use_blur_bg,
                bg_color=bg_color,
                bright_val=
                    bright_val,
                contrast_val=
                    contrast_val,
                mask_enable=
                    mask_enable,
                mask_type=
                    mask_type,
                mask_color=
                    mask_color,
                mask_opacity=
                    mask_opacity,
                mask_w=mask_w,
                mask_h=mask_h,
                mask_x=mask_x,
                mask_y=mask_y,
                logo_file=
                    as_filepath(
                        logo_file
                    ),
                logo_size=
                    logo_size,
                logo_x=logo_x,
                logo_y=logo_y,
                font_family=
                    font_family,
                font_size=
                    font_size,
                font_color=
                    font_color,
                outline_color=
                    outline_color,
                sub_x=sub_x,
                sub_y=sub_y,
                output_filename=
                    final_video
            )
        )

        status = (
            "🎉 Video အပြီးစီး "
            "အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ!\n\n"
            f"🎤 Voice: {voice_lang}\n"
            f"📝 Subtitle: {sub_lang}\n"
            f"🤖 Model: {model}\n"
            f"{duration_msg}"
        )

        return (
            final_video,
            final_video,
            subtitle_script,
            srt_file,
            status
        )

    except Exception as e:

        traceback.print_exc()

        return (
            None,
            None,
            "",
            None,
            "❌ Error ဖြစ်ပေါ်ပါသည်:\n\n"
            + safe_error(e)
        )


# =========================================================
# GRADIO UI
# =========================================================

with gr.Blocks(
    title=APP_TITLE
) as demo:

    gr.Markdown(
        f"""
# 🎬 {APP_TITLE}

**All-in-One Professional Multilingual Movie Recap Studio**

Tab 1 → Script  
Tab 2 → Voice / TTS  
Tab 3 → One-Click Final Video
"""
    )

    with gr.Tabs() as main_tabs:

        # =================================================
        # API KEY
        # =================================================

        with gr.TabItem(
            "🔑 API Key Setting",
            id="tab_key"
        ):

            gr.Markdown(
                "### 🔐 Gemini API Key ထည့်သွင်းပါ"
            )

            api_key_input = gr.Textbox(
                label="Gemini API Key",
                type="password",
                placeholder="AIzaSy..."
            )

            save_key_btn = gr.Button(
                "💾 API Key သိမ်းမည်",
                variant="primary"
            )

            key_status = gr.Markdown("")

            save_key_btn.click(
                save_api_key,
                inputs=api_key_input,
                outputs=key_status
            )

        # =================================================
        # TAB 1
        # =================================================

        with gr.TabItem(
            "1️⃣ Video Analysis & Script",
            id="tab_script"
        ):

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    v1_file = gr.Video(
                        label=
                        "📹 Video File တင်ရန်"
                    )

                    v1_url = gr.Textbox(
                        label=
                        "🔗 Video URL Link",
                        placeholder=
                        "YouTube / TikTok / Facebook / RedNote..."
                    )

                    v1_load_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary"
                    )

                    v1_ratio = gr.Radio(
                        [
                            "1:1",
                            "3:4",
                            "16:9",
                            "9:16"
                        ],
                        value="1:1",
                        label=
                        "📐 Preview Screen Aspect Ratio"
                    )

                    v1_gen_btn = gr.Button(
                        "🚀 Recap Script စတင်ထုတ်မည်",
                        variant="primary",
                        size="lg"
                    )

                with gr.Column(
                    scale=1
                ):

                    v1_css = gr.HTML(
                        get_ratio_css(
                            "1:1",
                            "tab1_preview_container"
                        )
                    )

                    v1_preview = gr.Video(
                        label=
                        "📺 Video Preview",
                        elem_id=
                        "tab1_preview_container"
                    )

                    v1_status = gr.Markdown(
                        "ဗီဒီယိုထည့်သွင်းရန် "
                        "အဆင်သင့်ဖြစ်ပါသည်။"
                    )

                    v1_script_out = gr.Textbox(
                        label=
                        "🎬 ထွက်ရှိလာသော Script",
                        lines=14
                    )

                    go_to_tts_btn = gr.Button(
                        "🎙️ Tab 2 သို့ သွားပြီး "
                        "အသံထုတ်မည် ➡️",
                        variant="secondary"
                    )

            with gr.Row():

                v1_srt = gr.File(
                    label=
                    "📄 SRT စာတန်းထိုး"
                )

                v1_zip = gr.File(
                    label=
                    "📦 SRT ZIP"
                )

        # =================================================
        # TAB 2
        # =================================================

        with gr.TabItem(
            "2️⃣ Text-to-Speech",
            id="tab_tts"
        ):

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    v2_input_text = gr.Textbox(
                        label=
                        "🎙️ Script",
                        lines=14,
                        placeholder=
                        "မြန်မာ / English / ไทย / 中文 စာသား..."
                    )

                    v2_trans_btn = gr.Button(
                        "🌐 မြန်မာလို ပြန်ဆိုမည်",
                        variant="secondary"
                    )

                    v2_voice = gr.Dropdown(
                        choices=list(
                            VOICES_BY_LANG[
                                "မြန်မာ (Burmese Voice)"
                            ].keys()
                        ),
                        value=
                        "Thiha (အမျိုးသားအသံ) - Natural",
                        label=
                        "🎤 အသံ ရွေးချယ်ပါ"
                    )

                    v2_speed = gr.Slider(
                        -30,
                        50,
                        value=5,
                        step=1,
                        label=
                        "⚡ Voice Speed (%)"
                    )

                    v2_btn = gr.Button(
                        "⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်",
                        variant="primary",
                        size="lg"
                    )

                    v2_status = gr.Markdown("")

                with gr.Column(
                    scale=1
                ):

                    v2_audio = gr.Audio(
                        label=
                        "🔊 Voice Preview",
                        autoplay=True
                    )

                    v2_mp3 = gr.File(
                        label=
                        "🎵 MP3 ဖိုင်"
                    )

            with gr.Row():

                v2_srt = gr.File(
                    label="📄 SRT"
                )

                v2_zip = gr.File(
                    label="📦 SRT ZIP"
                )

        # =================================================
        # TAB 3
        # =================================================

        with gr.TabItem(
            "⚡ 3️⃣ One-Click All-in-One Video Studio",
            id="tab_auto"
        ):

            gr.Markdown(
                """
### 🎛️ Professional Video Crop / Mask / Subtitle / Voice Studio
"""
            )

            with gr.Row():

                # =========================================
                # LEFT
                # =========================================

                with gr.Column(
                    scale=1
                ):

                    t3_file = gr.Video(
                        label=
                        "📹 Video File တင်ရန်"
                    )

                    t3_url = gr.Textbox(
                        label=
                        "🔗 Video Link",
                        placeholder=
                        "YouTube / TikTok / Facebook / RedNote..."
                    )

                    t3_load_btn = gr.Button(
                        "🔍 Video ရယူ/စစ်ဆေးမည်",
                        variant="secondary"
                    )

                    # -------------------------------------
                    # VOICE
                    # -------------------------------------

                    with gr.Accordion(
                        "🎤 အသံပိုင်းဆိုင်ရာ & Voice ဘာသာစကား",
                        open=False
                    ):

                        t3_voice_lang = gr.Radio(
                            choices=list(
                                VOICES_BY_LANG.keys()
                            ),
                            value=
                            "မြန်မာ (Burmese Voice)",
                            label=
                            "🎙️ Voice Language"
                        )

                        t3_voice = gr.Dropdown(
                            choices=list(
                                VOICES_BY_LANG[
                                    "မြန်မာ (Burmese Voice)"
                                ].keys()
                            ),
                            value=
                            "Thiha (အမျိုးသားအသံ) - Natural",
                            label=
                            "🎤 Voice ရွေးချယ်ပါ"
                        )

                        t3_speed = gr.Slider(
                            -30,
                            50,
                            value=5,
                            step=1,
                            label=
                            "⚡ Voice Speed (%)"
                        )

                        t3_orig_audio = gr.Checkbox(
                            label=
                            "🔊 မူရင်း Video အသံ ဖွင့်မည်",
                            value=False
                        )

                        t3_bgm_file = gr.Audio(
                            label=
                            "🎵 Background Music",
                            type="filepath"
                        )

                        t3_bgm_vol = gr.Slider(
                            0.0,
                            1.0,
                            value=0.15,
                            step=0.05,
                            label=
                            "🎚️ BGM Volume"
                        )

                    # -------------------------------------
                    # SUBTITLE
                    # -------------------------------------

                    with gr.Accordion(
                        "✍️ စာတန်းထိုး",
                        open=True
                    ):

                        t3_sub_lang = gr.Radio(
                            choices=
                            SUBTITLE_LANG_CHOICES,
                            value=
                            "မြန်မာ (Burmese)",
                            label=
                            "📝 Subtitle Language"
                        )

                        t3_font = gr.Dropdown(
                            [
                                "Pyidaungsu",
                                "Padauk",
                                "Myanmar Text",
                                "Arial",
                                "sans-serif"
                            ],
                            value="Pyidaungsu",
                            label=
                            "🔤 Subtitle Font"
                        )

                        t3_fsize = gr.Slider(
                            10,
                            100,
                            value=22,
                            step=1,
                            label=
                            "📏 Font Size"
                        )

                        with gr.Row():

                            t3_fcolor = gr.ColorPicker(
                                label=
                                "🎨 Text Color",
                                value="#00E676"
                            )

                            t3_ocolor = gr.ColorPicker(
                                label=
                                "🖌️ Outline Color",
                                value="#000000"
                            )

                        with gr.Row():

                            t3_sub_x = gr.Slider(
                                -500,
                                500,
                                value=0,
                                step=5,
                                label=
                                "↔️ Subtitle X"
                            )

                            t3_sub_y = gr.Slider(
                                -450,
                                450,
                                value=-260,
                                step=5,
                                label=
                                "↕️ Subtitle Y"
                            )

                    # -------------------------------------
                    # VIDEO
                    # -------------------------------------

                    with gr.Accordion(
                        "✂️ Video Crop / Zoom / Position",
                        open=True
                    ):

                        t3_ratio = gr.Radio(
                            [
                                "1:1",
                                "3:4",
                                "16:9",
                                "9:16"
                            ],
                            value="9:16",
                            label=
                            "📐 Aspect Ratio"
                        )

                        with gr.Row():

                            t3_resolution = gr.Dropdown(
                                [
                                    "480p",
                                    "720p",
                                    "1080p",
                                    "1440p",
                                    "Original Canvas"
                                ],
                                value="1080p",
                                label=
                                "🖥️ Resolution"
                            )

                            t3_quality = gr.Dropdown(
                                [
                                    "Fast (သေးငယ်သောဖိုင်)",
                                    "Balanced (အကြံပြု)",
                                    "High Quality",
                                    "Best Quality (ဖိုင်ကြီး)"
                                ],
                                value=
                                "Balanced (အကြံပြု)",
                                label=
                                "🎞️ Quality"
                            )

                        t3_video_speed = gr.Slider(
                            0.5,
                            2.0,
                            value=1.0,
                            step=0.05,
                            label=
                            "⏩ Video Speed"
                        )

                        with gr.Row():

                            t3_crop_w = gr.Slider(
                                30,
                                100,
                                value=100,
                                step=1,
                                label=
                                "✂️ Crop Width (%)"
                            )

                            t3_crop_h = gr.Slider(
                                30,
                                100,
                                value=100,
                                step=1,
                                label=
                                "✂️ Crop Height (%)"
                            )

                        t3_scale = gr.Slider(
                            0.5,
                            2.5,
                            value=1.0,
                            step=0.05,
                            label=
                            "🔍 Video Zoom"
                        )

                        t3_flip = gr.Checkbox(
                            label=
                            "🔄 Horizontal Flip",
                            value=False
                        )

                        with gr.Row():

                            t3_x_off = gr.Slider(
                                -500,
                                500,
                                value=0,
                                step=5,
                                label=
                                "↔️ Video X"
                            )

                            t3_y_off = gr.Slider(
                                -500,
                                500,
                                value=0,
                                step=5,
                                label=
                                "↕️ Video Y"
                            )

                        t3_blur_bg = gr.Checkbox(
                            label=
                            "🌫️ Background Blur",
                            value=True
                        )

                        with gr.Row():

                            t3_bright = gr.Slider(
                                0.5,
                                1.5,
                                value=1.0,
                                step=0.05,
                                label=
                                "☀️ Brightness"
                            )

                            t3_contrast = gr.Slider(
                                0.5,
                                1.5,
                                value=1.0,
                                step=0.05,
                                label=
                                "🌓 Contrast"
                            )

                        t3_bgcolor = gr.ColorPicker(
                            label=
                            "🎨 Canvas Color",
                            value="#000000"
                        )

                    # -------------------------------------
                    # MASK
                    # -------------------------------------

                    with gr.Accordion(
                        "🛡️ Original Subtitle Mask",
                        open=True
                    ):

                        t3_mask_enable = gr.Checkbox(
                            label=
                            "✅ Mask ဖွင့်မည်",
                            value=True
                        )

                        t3_mask_type = gr.Radio(
                            [
                                "Blur (ဝေဝါးဖုံး)",
                                "Color Box (အရောင်အတုံးဖြင့်ဖုံး)"
                            ],
                            value=
                            "Blur (ဝေဝါးဖုံး)",
                            label=
                            "🎭 Mask Type"
                        )

                        with gr.Row():

                            t3_mask_color = gr.ColorPicker(
                                label=
                                "🎨 Mask Color",
                                value="#000000"
                            )

                            t3_mask_opacity = gr.Slider(
                                0.0,
                                1.0,
                                value=0.85,
                                step=0.05,
                                label=
                                "💧 Opacity"
                            )

                        with gr.Row():

                            t3_mask_w = gr.Slider(
                                10,
                                100,
                                value=85,
                                step=1,
                                label=
                                "↔️ Mask Width (%)"
                            )

                            t3_mask_h = gr.Slider(
                                10,
                                300,
                                value=85,
                                step=2,
                                label=
                                "↕️ Mask Height"
                            )

                        with gr.Row():

                            t3_mask_x = gr.Slider(
                                -500,
                                500,
                                value=0,
                                step=5,
                                label=
                                "↔️ Mask X"
                            )

                            t3_mask_y = gr.Slider(
                                -450,
                                450,
                                value=-260,
                                step=5,
                                label=
                                "↕️ Mask Y"
                            )

                    # -------------------------------------
                    # LOGO
                    # -------------------------------------

                    with gr.Accordion(
                        "🏷️ Logo",
                        open=False
                    ):

                        t3_logo_file = gr.Image(
                            label=
                            "🖼️ Logo PNG / JPG",
                            type="filepath"
                        )

                        t3_logo_size = gr.Slider(
                            30,
                            400,
                            value=100,
                            step=5,
                            label=
                            "📏 Logo Size"
                        )

                        with gr.Row():

                            t3_logo_x = gr.Slider(
                                -500,
                                500,
                                value=0,
                                step=5,
                                label=
                                "↔️ Logo X"
                            )

                            t3_logo_y = gr.Slider(
                                -450,
                                450,
                                value=250,
                                step=5,
                                label=
                                "↕️ Logo Y"
                            )

                    # -------------------------------------
                    # RUN
                    # -------------------------------------

                    t3_run_btn = gr.Button(
                        "✨ Video အပြီးစီး One-Click ထုတ်လုပ်မည်",
                        variant="primary",
                        size="lg"
                    )

                # =========================================
                # RIGHT
                # =========================================

                with gr.Column(
                    scale=1
                ):

                    with gr.Group(
                        elem_id=
                        "tab3_preview_wrapper"
                    ):

                        t3_live_video = gr.Video(
                            label=
                            "📺 Real-Time Preview",
                            elem_id=
                            "tab3_preview_box"
                        )

                        t3_preview_css = gr.HTML(
                            get_tab3_in_video_preview_html(
                                "မြန်မာ (Burmese)",
                                "9:16",
                                1.0,
                                False,
                                1.0,
                                0,
                                0,
                                100,
                                100,
                                True,
                                "#000000",
                                1.0,
                                1.0,
                                True,
                                "Blur (ဝေဝါးဖုံး)",
                                "#000000",
                                0.85,
                                85,
                                85,
                                0,
                                -260,
                                None,
                                100,
                                0,
                                250,
                                "Pyidaungsu",
                                22,
                                "#00E676",
                                "#000000",
                                0,
                                -260
                            )
                        )

                    t3_status = gr.Markdown(
                        """
စာတန်းထိုး၊ Mask၊ Logo နှင့် Video Position
တို့ကို Preview တွင် ချက်ချင်းကြည့်နိုင်ပါသည်။
"""
                    )

                    t3_final_video = gr.Video(
                        label=
                        "🎬 Final Video Output"
                    )

                    t3_script_view = gr.Textbox(
                        label=
                        "📝 Final Subtitle Script",
                        lines=8
                    )

                    with gr.Row():

                        t3_download = gr.File(
                            label=
                            "📥 Final Video"
                        )

                        t3_srt = gr.File(
                            label=
                            "📄 SRT"
                        )


    # =====================================================
    # EVENT BINDINGS
    # =====================================================

    # -----------------------------------------------------
    # TAB 1 VIDEO PREVIEW
    # -----------------------------------------------------

    v1_file.change(
        lambda f: as_filepath(f),
        inputs=v1_file,
        outputs=v1_preview
    )

    v1_load_btn.click(
        download_video_from_link,
        inputs=v1_url,
        outputs=v1_preview
    )

    v1_ratio.change(
        lambda r:
            get_ratio_css(
                r,
                "tab1_preview_container"
            ),
        inputs=v1_ratio,
        outputs=v1_css
    )

    v1_gen_btn.click(
        tab1_analyze,
        inputs=[
            v1_file,
            v1_url,
            v1_ratio
        ],
        outputs=[
            v1_script_out,
            v2_input_text,
            v1_status,
            v1_srt,
            v1_zip
        ]
    )

    go_to_tts_btn.click(
        lambda:
            gr.Tabs(
                selected="tab_tts"
            ),
        outputs=main_tabs
    )

    # -----------------------------------------------------
    # TAB 2
    # -----------------------------------------------------

    v2_trans_btn.click(
        handle_direct_translate,
        inputs=v2_input_text,
        outputs=[
            v2_input_text,
            v2_status
        ]
    )

    v2_btn.click(
        tab2_tts_with_auto_translate,
        inputs=[
            v2_input_text,
            v2_voice,
            v2_speed
        ],
        outputs=[
            v2_input_text,
            v2_audio,
            v2_mp3,
            v2_srt,
            v2_zip,
            v2_status
        ]
    )

    # -----------------------------------------------------
    # TAB 3 VOICE
    # -----------------------------------------------------

    t3_voice_lang.change(
        update_voice_choices,
        inputs=t3_voice_lang,
        outputs=t3_voice
    )

    # -----------------------------------------------------
    # TAB 3 VIDEO
    # -----------------------------------------------------

    t3_file.change(
        lambda f: as_filepath(f),
        inputs=t3_file,
        outputs=t3_live_video
    )

    t3_load_btn.click(
        download_video_from_link,
        inputs=t3_url,
        outputs=t3_live_video
    )

    # -----------------------------------------------------
    # TAB 3 LIVE PREVIEW
    # -----------------------------------------------------

    preview_all_inputs = [

        t3_sub_lang,

        t3_ratio,
        t3_video_speed,
        t3_flip,
        t3_scale,
        t3_x_off,
        t3_y_off,

        t3_crop_w,
        t3_crop_h,

        t3_blur_bg,
        t3_bgcolor,

        t3_bright,
        t3_contrast,

        t3_mask_enable,
        t3_mask_type,
        t3_mask_color,
        t3_mask_opacity,
        t3_mask_w,
        t3_mask_h,
        t3_mask_x,
        t3_mask_y,

        t3_logo_file,
        t3_logo_size,
        t3_logo_x,
        t3_logo_y,

        t3_font,
        t3_fsize,
        t3_fcolor,
        t3_ocolor,

        t3_sub_x,
        t3_sub_y
    ]

    for component in preview_all_inputs:

        component.change(
            get_tab3_in_video_preview_html,
            inputs=preview_all_inputs,
            outputs=t3_preview_css
        )

    # -----------------------------------------------------
    # TAB 3 FINAL
    # -----------------------------------------------------

    t3_run_btn.click(
        tab3_auto_pipeline,
        inputs=[

            t3_file,
            t3_url,
            t3_bgm_file,

            t3_voice_lang,
            t3_voice,
            t3_speed,

            t3_sub_lang,

            t3_orig_audio,
            t3_bgm_vol,

            t3_ratio,
            t3_resolution,
            t3_quality,

            t3_video_speed,
            t3_flip,
            t3_scale,
            t3_x_off,
            t3_y_off,

            t3_crop_w,
            t3_crop_h,

            t3_blur_bg,
            t3_bgcolor,

            t3_bright,
            t3_contrast,

            t3_mask_enable,
            t3_mask_type,
            t3_mask_color,
            t3_mask_opacity,
            t3_mask_w,
            t3_mask_h,
            t3_mask_x,
            t3_mask_y,

            t3_logo_file,
            t3_logo_size,
            t3_logo_x,
            t3_logo_y,

            t3_font,
            t3_fsize,
            t3_fcolor,
            t3_ocolor,
            t3_sub_x,
            t3_sub_y
        ],

        outputs=[
            t3_final_video,
            t3_download,
            t3_script_view,
            t3_srt,
            t3_status
        ]
    )


# =========================================================
# SERVER
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            7860
        )
    )

    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        theme=gr.themes.Soft()
    )
