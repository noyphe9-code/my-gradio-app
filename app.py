# ============================================================
# AI MOVIE RECAP STUDIO PRO
# Full Single-File Version
# ============================================================

import os
import sys
import re
import time
import zipfile
import subprocess
import asyncio
import shutil
import tempfile
import uuid
import traceback
import mimetypes
import ctypes
from pathlib import Path

# ============================================================
# UTF-8 ENVIRONMENT
# ============================================================

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ============================================================
# IMPORTS
# ============================================================

import gradio as gr
import edge_tts
import yt_dlp

from google import genai


# ============================================================
# APP CONFIG
# ============================================================

APP_TITLE = "AI Movie Recap Studio Pro"

MAX_VIDEO_MINUTES = 10

SAVED_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# You can change the first model with:
# GEMINI_MODEL=your-model-name
DEFAULT_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

GEMINI_MODELS = [
    DEFAULT_MODEL,
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

# Remove duplicates while preserving order
GEMINI_MODELS = list(dict.fromkeys(
    [x for x in GEMINI_MODELS if x]
))


# ============================================================
# VOICES
# ============================================================

VOICE_MAP = {
    "Burmese Male": "my-MM-ThihaNeural",
    "Burmese Female": "my-MM-NilarNeural",

    "English Male": "en-US-GuyNeural",
    "English Female": "en-US-JennyNeural",

    "Thai Male": "th-TH-NiwatNeural",
    "Thai Female": "th-TH-PremwadeeNeural",

    "Chinese Male": "zh-CN-YunxiNeural",
    "Chinese Female": "zh-CN-XiaoxiaoNeural",
}

LANGUAGE_CHOICES = [
    "Burmese",
    "English",
    "Thai",
    "Chinese",
]


# ============================================================
# ASCII SAFE TEMP DIRECTORY
# ============================================================

def _is_ascii(value):
    try:
        str(value).encode("ascii")
        return True
    except Exception:
        return False


def _can_write_directory(directory):
    try:
        os.makedirs(directory, exist_ok=True)

        test_file = os.path.join(
            directory,
            "__ascii_test_" + uuid.uuid4().hex + ".tmp"
        )

        with open(test_file, "wb") as f:
            f.write(b"test")

        os.remove(test_file)

        return True

    except Exception:
        return False


def _windows_short_path(path):
    """
    Try converting a Windows Unicode path to an ASCII 8.3 path.
    Example:
        C:\\Users\\MyanmarName\\AppData\\Local\\Temp
    ->
        C:\\Users\\MYANMA~1\\AppData\\Local\\Temp
    """

    if not sys.platform.startswith("win"):
        return path

    try:
        path = os.path.abspath(path)

        if _is_ascii(path):
            return path

        kernel32 = ctypes.windll.kernel32

        buffer_size = 4096
        buffer = ctypes.create_unicode_buffer(buffer_size)

        result = kernel32.GetShortPathNameW(
            path,
            buffer,
            buffer_size
        )

        if result and result < buffer_size:
            short_path = buffer.value

            if short_path and _is_ascii(short_path):
                return short_path

    except Exception:
        pass

    return path


def get_ascii_temp_root():
    """
    Find a REAL ASCII-only writable temp directory.

    This is important because:
        tempfile.gettempdir()

    may itself contain Unicode:
        C:\\Users\\မြန်မာ\\AppData\\Local\\Temp

    Gemini upload can fail when the underlying HTTP multipart
    layer encounters such a path.
    """

    candidates = []

    # --------------------------------------------------------
    # Windows
    # --------------------------------------------------------

    if sys.platform.startswith("win"):

        normal_temp = tempfile.gettempdir()

        short_temp = _windows_short_path(normal_temp)

        if short_temp:
            candidates.append(short_temp)

        # Fixed ASCII locations
        system_drive = os.environ.get("SystemDrive", "C:")

        candidates.extend([
            os.path.join(system_drive, "ai_movie_recap_tmp"),
            os.path.join(system_drive, "Temp"),
            r"C:\ai_movie_recap_tmp",
            r"C:\Temp",
        ])

    # --------------------------------------------------------
    # Linux / macOS
    # --------------------------------------------------------

    else:
        candidates.extend([
            "/tmp/ai_movie_recap_tmp",
            "/var/tmp/ai_movie_recap_tmp",
            tempfile.gettempdir(),
        ])

    # Remove duplicates
    clean_candidates = []

    for candidate in candidates:
        if not candidate:
            continue

        candidate = os.path.abspath(candidate)

        if candidate not in clean_candidates:
            clean_candidates.append(candidate)

    # --------------------------------------------------------
    # Find working ASCII path
    # --------------------------------------------------------

    for candidate in clean_candidates:

        if not _is_ascii(candidate):
            continue

        if _can_write_directory(candidate):
            return candidate

    # --------------------------------------------------------
    # Last resort
    # --------------------------------------------------------

    normal_temp = tempfile.gettempdir()

    if _is_ascii(normal_temp):
        fallback = os.path.join(
            normal_temp,
            "ai_movie_recap_tmp"
        )

        if _can_write_directory(fallback):
            return fallback

    raise RuntimeError(
        "Could not create an ASCII-safe temporary directory. "
        "Please create C:\\ai_movie_recap_tmp manually on Windows "
        "or /tmp/ai_movie_recap_tmp on Linux."
    )


APP_TEMP_DIR = get_ascii_temp_root()

os.makedirs(APP_TEMP_DIR, exist_ok=True)


# ============================================================
# GENERAL HELPERS
# ============================================================

def safe_error(exc):
    try:
        text = str(exc)
    except Exception:
        text = repr(exc)

    text = text.replace("\x00", "")

    if len(text) > 4000:
        text = text[:4000] + "..."

    return text


def as_filepath(value):
    """
    Convert Gradio file/video values into a local filepath.
    """

    if value is None:
        return None

    if isinstance(value, str):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):

        for key in [
            "path",
            "filepath",
            "name"
        ]:
            if value.get(key):
                return str(value[key])

    return None


def create_job_dir(prefix="job"):
    job_dir = os.path.join(
        APP_TEMP_DIR,
        prefix + "_" + uuid.uuid4().hex
    )

    os.makedirs(job_dir, exist_ok=True)

    return job_dir


def cleanup_file(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def cleanup_dir(path):
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ============================================================
# API KEY
# ============================================================

def save_api_key(api_key):
    api_key = (api_key or "").strip()

    if not api_key:
        return "❌ Please enter Gemini API Key."

    try:
        os.environ["GEMINI_API_KEY"] = api_key

        return "✅ Gemini API Key saved for this session."

    except Exception as exc:
        return "❌ " + safe_error(exc)


def get_api_key(api_key=None):
    key = (api_key or "").strip()

    if key:
        return key

    key = os.environ.get("GEMINI_API_KEY", "").strip()

    if key:
        return key

    return SAVED_API_KEY


# ============================================================
# FFmpeg
# ============================================================

def command_exists(command):
    return shutil.which(command) is not None


def require_ffmpeg():
    if not command_exists("ffmpeg"):
        raise RuntimeError(
            "FFmpeg not found. Please install FFmpeg and add it to PATH."
        )


def run_command(
    command,
    timeout=None,
    capture_output=True
):

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            timeout=timeout,
            check=False,
        )

        stdout = ""
        stderr = ""

        if capture_output:

            stdout = (
                result.stdout.decode(
                    "utf-8",
                    errors="replace"
                )
                if result.stdout
                else ""
            )

            stderr = (
                result.stderr.decode(
                    "utf-8",
                    errors="replace"
                )
                if result.stderr
                else ""
            )

        return result.returncode, stdout, stderr

    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg process timed out.")


def get_video_duration(video_path):
    """
    Returns duration in seconds.
    """

    require_ffmpeg()

    video_path = as_filepath(video_path)

    if not video_path or not os.path.isfile(video_path):
        raise RuntimeError("Video file not found.")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    if not command_exists("ffprobe"):
        raise RuntimeError(
            "ffprobe not found. Please install FFmpeg correctly."
        )

    code, stdout, stderr = run_command(
        command,
        timeout=120
    )

    if code != 0:
        raise RuntimeError(
            "Unable to read video duration.\n" + stderr[-1500:]
        )

    try:
        return float(stdout.strip())
    except Exception:
        raise RuntimeError(
            "Could not parse video duration."
        )


def has_audio_stream(video_path):

    video_path = as_filepath(video_path)

    if not video_path:
        return False

    if not command_exists("ffprobe"):
        return True

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        video_path,
    ]

    code, stdout, _ = run_command(
        command,
        timeout=120
    )

    if code != 0:
        return False

    return "audio" in stdout.lower()


def validate_video_duration(video_path):

    duration = get_video_duration(video_path)

    max_seconds = MAX_VIDEO_MINUTES * 60

    if duration > max_seconds:
        raise RuntimeError(
            f"Video is {duration / 60:.2f} minutes long. "
            f"Maximum allowed is {MAX_VIDEO_MINUTES} minutes."
        )

    return duration


# ============================================================
# VIDEO SCENE / FREEZE DETECTION
# ============================================================

def detect_scene_boundaries(video_path):

    if not command_exists("ffmpeg"):
        return []

    video_path = as_filepath(video_path)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        video_path,
        "-vf",
        "select='gt(scene,0.35)',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]

    try:

        code, stdout, stderr = run_command(
            command,
            timeout=600
        )

        text = stdout + "\n" + stderr

        points = []

        for match in re.finditer(
            r"pts_time:([0-9.]+)",
            text
        ):
            try:
                points.append(float(match.group(1)))
            except Exception:
                pass

        return sorted(set(points))

    except Exception:
        return []


def detect_freeze_intervals(video_path):

    """
    Detect freeze frames.

    Returns:
        [(start, end), ...]
    """

    if not command_exists("ffmpeg"):
        return []

    video_path = as_filepath(video_path)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        video_path,
        "-vf",
        "freezedetect=n=-60dB:d=1.5",
        "-an",
        "-f",
        "null",
        "-",
    ]

    try:

        code, stdout, stderr = run_command(
            command,
            timeout=900
        )

        text = stdout + "\n" + stderr

        starts = []
        ends = []

        for match in re.finditer(
            r"freeze_start:\s*([0-9.]+)",
            text
        ):
            try:
                starts.append(float(match.group(1)))
            except Exception:
                pass

        for match in re.finditer(
            r"freeze_end:\s*([0-9.]+)",
            text
        ):
            try:
                ends.append(float(match.group(1)))
            except Exception:
                pass

        intervals = []

        for start, end in zip(starts, ends):
            if end > start:
                intervals.append((start, end))

        return intervals

    except Exception:
        return []


def build_keep_intervals(
    duration,
    remove_intervals
):

    if not remove_intervals:
        return [(0.0, duration)]

    remove_intervals = sorted(
        remove_intervals,
        key=lambda x: x[0]
    )

    merged = []

    for start, end in remove_intervals:

        start = max(0.0, start)
        end = min(duration, end)

        if end <= start:
            continue

        if not merged:
            merged.append([start, end])
        else:
            previous = merged[-1]

            if start <= previous[1] + 0.1:
                previous[1] = max(
                    previous[1],
                    end
                )
            else:
                merged.append([start, end])

    keep = []

    cursor = 0.0

    for start, end in merged:

        if start > cursor + 0.05:
            keep.append(
                (cursor, start)
            )

        cursor = max(cursor, end)

    if cursor < duration - 0.05:
        keep.append(
            (cursor, duration)
        )

    return keep


def prepare_scene_aware_video(
    video_path,
    output_path=None
):

    video_path = as_filepath(video_path)

    if not video_path:
        raise RuntimeError("Video not found.")

    duration = get_video_duration(video_path)

    # Freeze detection is optional and intentionally conservative.
    freeze_intervals = detect_freeze_intervals(video_path)

    keep_intervals = build_keep_intervals(
        duration,
        freeze_intervals
    )

    # Nothing to remove
    if len(keep_intervals) == 1:
        return video_path

    if output_path is None:

        job_dir = create_job_dir("scene")

        output_path = os.path.join(
            job_dir,
            "scene_cleaned.mp4"
        )

    # Build filter graph
    filters = []

    for index, (start, end) in enumerate(
        keep_intervals
    ):

        filters.append(
            f"[0:v]trim=start={start}:end={end},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )

    video_labels = "".join(
        f"[v{i}]"
        for i in range(len(keep_intervals))
    )

    concat_filter = (
        video_labels +
        f"concat=n={len(keep_intervals)}:v=1:a=0[outv]"
    )

    filter_complex = ";".join(
        filters + [concat_filter]
    )

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        video_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        output_path,
    ]

    code, _, stderr = run_command(
        command,
        timeout=1800
    )

    if code != 0:
        raise RuntimeError(
            "Scene-aware video processing failed.\n" +
            stderr[-3000:]
        )

    return output_path


# ============================================================
# YOUTUBE / URL DOWNLOAD
# ============================================================

def download_video_from_link(url):

    url = (url or "").strip()

    if not url:
        raise RuntimeError("Please enter a video URL.")

    job_dir = create_job_dir("download")

    output_template = os.path.join(
        job_dir,
        "downloaded_video.%(ext)s"
    )

    options = {
        "outtmpl": output_template,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,

        # IMPORTANT:
        # Prevent Unicode title from becoming filename.
        "windowsfilenames": True,

        # Avoid inherited proxy weirdness where possible.
        "retries": 3,
        "fragment_retries": 3,
    }

    try:

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

    except Exception as exc:
        cleanup_dir(job_dir)

        raise RuntimeError(
            "Video download failed:\n" +
            safe_error(exc)
        )

    files = []

    for name in os.listdir(job_dir):

        full = os.path.join(
            job_dir,
            name
        )

        if os.path.isfile(full):
            files.append(full)

    if not files:
        raise RuntimeError(
            "Downloaded video file was not found."
        )

    # Prefer mp4
    mp4_files = [
        f for f in files
        if f.lower().endswith(".mp4")
    ]

    return (
        mp4_files[0]
        if mp4_files
        else files[0]
    )


# ============================================================
# GEMINI VIDEO MIME / PATH
# ============================================================

VIDEO_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".3gp": "video/3gpp",
}


def get_safe_video_extension(video_path):

    ext = os.path.splitext(
        video_path
    )[1].lower()

    if ext in VIDEO_EXTENSIONS:
        return ext

    return ".mp4"


def detect_video_mime(video_path):

    ext = os.path.splitext(
        video_path
    )[1].lower()

    mime = VIDEO_EXTENSIONS.get(ext)

    if mime:
        return mime

    guessed, _ = mimetypes.guess_type(
        video_path
    )

    if guessed and guessed.startswith("video/"):
        return guessed

    return "video/mp4"


def make_ascii_gemini_directory():

    directory = os.path.join(
        APP_TEMP_DIR,
        "gemini_upload"
    )

    if not _is_ascii(directory):
        raise RuntimeError(
            "Gemini upload directory is not ASCII-safe."
        )

    os.makedirs(
        directory,
        exist_ok=True
    )

    return directory


def make_gemini_safe_video(video_path):

    """
    CRITICAL FIX

    Gemini SDK / multipart upload can fail with:

        'ascii' codec can't encode characters ...

    if the original path contains Unicode.

    We only copy the file when the FULL absolute path is not ASCII.

    This keeps normal ASCII-path uploads fast.
    """

    source = as_filepath(video_path)

    if not source:
        raise RuntimeError(
            "Video file path is empty."
        )

    source = os.path.abspath(source)

    if not os.path.isfile(source):
        raise RuntimeError(
            "Video file does not exist:\n" +
            source
        )

    # --------------------------------------------------------
    # FAST PATH
    # --------------------------------------------------------

    if _is_ascii(source):

        # Ensure extension is safe.
        ext = get_safe_video_extension(
            source
        )

        # If extension is already normal, use original file.
        if source.lower().endswith(ext):
            return source

        # Otherwise make a safe copy.
        pass

    # --------------------------------------------------------
    # SAFE COPY
    # --------------------------------------------------------

    safe_dir = make_ascii_gemini_directory()

    ext = get_safe_video_extension(
        source
    )

    safe_name = (
        "input_video_" +
        uuid.uuid4().hex +
        ext
    )

    destination = os.path.join(
        safe_dir,
        safe_name
    )

    if not _is_ascii(destination):
        raise RuntimeError(
            "Generated Gemini upload path is not ASCII."
        )

    try:

        shutil.copyfile(
            source,
            destination
        )

    except Exception as exc:

        raise RuntimeError(
            "Could not create Gemini-safe video copy:\n" +
            safe_error(exc)
        )

    if not os.path.isfile(destination):
        raise RuntimeError(
            "Gemini-safe video copy was not created."
        )

    return destination


def _get_gemini_state_name(uploaded):

    try:

        state = getattr(
            uploaded,
            "state",
            None
        )

        if state is None:
            return ""

        name = getattr(
            state,
            "name",
            None
        )

        if name:
            return str(name).upper()

        return str(state).upper()

    except Exception:
        return ""


def upload_video_to_gemini_safe(
    client,
    video_path
):

    safe_path = make_gemini_safe_video(
        video_path
    )

    mime_type = detect_video_mime(
        safe_path
    )

    # IMPORTANT:
    # display_name must also be ASCII-only.
    display_name = (
        "movie_recap_" +
        uuid.uuid4().hex +
        get_safe_video_extension(safe_path)
    )

    if not _is_ascii(safe_path):
        raise RuntimeError(
            "Internal error: Gemini upload path contains "
            "non-ASCII characters."
        )

    if not _is_ascii(display_name):
        raise RuntimeError(
            "Internal error: Gemini display name contains "
            "non-ASCII characters."
        )

    uploaded = None

    last_error = None

    # --------------------------------------------------------
    # RETRY UPLOAD
    # --------------------------------------------------------

    for attempt in range(1, 4):

        try:

            # New Google GenAI SDK
            try:

                uploaded = client.files.upload(
                    file=safe_path,
                    config={
                        "mime_type": mime_type,
                        "display_name": display_name,
                    }
                )

            except TypeError:

                # Older SDK compatibility
                try:

                    uploaded = client.files.upload(
                        file=safe_path,
                        config={
                            "mime_type": mime_type
                        }
                    )

                except TypeError:

                    uploaded = client.files.upload(
                        file=safe_path
                    )

            break

        except UnicodeEncodeError as exc:

            # Unicode error should not be retried blindly.
            raise RuntimeError(
                "Gemini upload failed because a non-ASCII path "
                "or multipart value reached the SDK.\n"
                f"Safe path: {safe_path}\n"
                f"Error: {safe_error(exc)}"
            )

        except Exception as exc:

            last_error = exc

            if attempt >= 3:
                break

            time.sleep(
                1.5 * attempt
            )

    if uploaded is None:

        raise RuntimeError(
            "Gemini Video Upload Error: " +
            safe_error(last_error)
        )

    return uploaded, safe_path


def wait_for_gemini_video(
    client,
    uploaded,
    timeout=900,
    poll_seconds=3
):

    """
    Wait until Gemini video is ACTIVE.
    """

    name = getattr(
        uploaded,
        "name",
        None
    )

    if not name:
        return uploaded

    start = time.time()

    current = uploaded

    while True:

        state_name = _get_gemini_state_name(
            current
        )

        if state_name in {
            "ACTIVE",
            "SUCCEEDED",
            "READY",
        }:

            return current

        if state_name in {
            "FAILED",
            "ERROR",
            "CANCELLED",
        }:

            raise RuntimeError(
                "Gemini video processing failed.\n"
                f"State: {state_name}"
            )

        if time.time() - start > timeout:

            raise RuntimeError(
                "Timed out waiting for Gemini video processing."
            )

        time.sleep(
            poll_seconds
        )

        try:

            current = client.files.get(
                name=name
            )

        except Exception as exc:

            # If get temporarily fails, retry.
            if time.time() - start > timeout:
                raise RuntimeError(
                    "Could not refresh Gemini file state:\n" +
                    safe_error(exc)
                )

            time.sleep(2)


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_script_for_display(text):

    if not text:
        return ""

    text = str(text)

    # Remove markdown code fences
    text = re.sub(
        r"```(?:text|markdown|plaintext)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = text.replace(
        "```",
        ""
    )

    # Remove common labels
    text = re.sub(
        r"^\s*(SCRIPT|RECAP|SUMMARY|NARRATION)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def clean_script_for_tts(text):

    text = clean_script_for_display(
        text
    )

    if not text:
        return ""

    # Remove URLs
    text = re.sub(
        r"https?://\S+",
        "",
        text
    )

    # Remove markdown formatting
    text = re.sub(
        r"[*_`#>]+",
        "",
        text
    )

    # Remove bracketed metadata
    text = re.sub(
        r"\[[^\]]*\]",
        "",
        text
    )

    # Remove excessive whitespace
    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def has_foreign_text(
    text,
    target_language
):

    if not text:
        return False

    if target_language == "Burmese":
        return bool(
            re.search(
                r"[\u1000-\u109F]",
                text
            )
        )

    if target_language == "Thai":
        return bool(
            re.search(
                r"[\u0E00-\u0E7F]",
                text
            )
        )

    if target_language == "Chinese":
        return bool(
            re.search(
                r"[\u4E00-\u9FFF]",
                text
            )
        )

    return True


# ============================================================
# SRT
# ============================================================

def split_into_two_lines(
    text,
    max_chars=48
):

    text = re.sub(
        r"\s+",
        " ",
        text.strip()
    )

    if len(text) <= max_chars:
        return text

    words = text.split()

    if len(words) <= 1:
        middle = len(text) // 2

        return (
            text[:middle].strip() +
            "\n" +
            text[middle:].strip()
        )

    best_index = 1
    best_score = 10**9

    current = ""

    for i, word in enumerate(words[:-1]):

        current += (
            (" " if current else "") +
            word
        )

        score = abs(
            len(current) -
            len(" ".join(words[i + 1:]))
        )

        if score < best_score:
            best_score = score
            best_index = i + 1

    line1 = " ".join(
        words[:best_index]
    )

    line2 = " ".join(
        words[best_index:]
    )

    return (
        line1 +
        "\n" +
        line2
    )


def seconds_to_srt_time(seconds):

    seconds = max(
        0,
        float(seconds)
    )

    milliseconds = int(
        round(seconds * 1000)
    )

    hours = milliseconds // 3600000

    milliseconds %= 3600000

    minutes = milliseconds // 60000

    milliseconds %= 60000

    secs = milliseconds // 1000

    milliseconds %= 1000

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


def generate_srt_and_zip(
    script,
    output_dir,
    total_duration=None
):

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    script = clean_script_for_tts(
        script
    )

    if not script:
        raise RuntimeError(
            "Script is empty."
        )

    paragraphs = [
        x.strip()
        for x in re.split(
            r"\n\s*\n",
            script
        )
        if x.strip()
    ]

    if not paragraphs:
        paragraphs = [
            x.strip()
            for x in script.splitlines()
            if x.strip()
        ]

    if not paragraphs:
        paragraphs = [script]

    # If no duration, estimate from text.
    if not total_duration:
        words = len(
            re.findall(
                r"\S+",
                script
            )
        )

        total_duration = max(
            10.0,
            words / 2.2
        )

    weights = []

    for paragraph in paragraphs:
        weights.append(
            max(
                1,
                len(
                    re.findall(
                        r"\S+",
                        paragraph
                    )
                )
            )
        )

    weight_total = sum(weights)

    srt_lines = []

    current_time = 0.0

    for index, (
        paragraph,
        weight
    ) in enumerate(
        zip(paragraphs, weights),
        start=1
    ):

        duration = (
            total_duration *
            weight /
            weight_total
        )

        start = current_time

        end = (
            current_time +
            duration
        )

        current_time = end

        subtitle = split_into_two_lines(
            paragraph
        )

        srt_lines.extend([
            str(index),
            (
                seconds_to_srt_time(start) +
                " --> " +
                seconds_to_srt_time(end)
            ),
            subtitle,
            "",
        ])

    srt_content = "\n".join(
        srt_lines
    )

    srt_path = os.path.join(
        output_dir,
        "subtitles.srt"
    )

    with open(
        srt_path,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:
        f.write(
            srt_content
        )

    zip_path = os.path.join(
        output_dir,
        "subtitles.zip"
    )

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as z:
        z.write(
            srt_path,
            arcname="subtitles.srt"
        )

    return srt_path, zip_path


# ============================================================
# LANGUAGE
# ============================================================

def get_target_language(
    language
):

    if language not in LANGUAGE_CHOICES:
        return "Burmese"

    return language


def build_recap_prompt(
    target_language,
    recap_length="Medium",
    style="Cinematic"
):

    language_instruction = {
        "Burmese":
            "Write the narration entirely in natural Burmese.",
        "English":
            "Write the narration entirely in natural English.",
        "Thai":
            "Write the narration entirely in natural Thai.",
        "Chinese":
            "Write the narration entirely in natural Simplified Chinese.",
    }.get(
        target_language,
        "Write the narration in Burmese."
    )

    length_instruction = {
        "Short":
            "Keep it concise, around 500-700 words.",
        "Medium":
            "Aim for around 900-1200 words.",
        "Long":
            "Aim for around 1400-1800 words.",
    }.get(
        recap_length,
        "Aim for around 900-1200 words."
    )

    style_instruction = {
        "Cinematic":
            "Use cinematic, engaging narration with suspense.",
        "Simple":
            "Use simple, clear narration that is easy to understand.",
        "Dramatic":
            "Use dramatic storytelling with emotional tension.",
        "YouTube":
            "Use an engaging YouTube movie-recap narration style.",
    }.get(
        style,
        "Use engaging narration."
    )

    return f"""
You are an expert movie recap narrator.

Analyze the uploaded movie/video carefully.

Create a chronological movie recap narration.

IMPORTANT:
- Do not invent scenes that are not present.
- Do not claim details that cannot be reasonably observed.
- Follow the actual sequence of events.
- Include important characters, conflicts, reveals and ending.
- Make the narration natural for voice-over.
- Avoid screenplay directions.
- Avoid timestamps.
- Avoid headings inside the final narration.
- Do not use Markdown.
- Do not output analysis or explanations.
- Output ONLY the narration script.

Target language:
{target_language}

{language_instruction}

Length:
{length_instruction}

Style:
{style_instruction}

The narration should be suitable for AI voice-over.
"""


# ============================================================
# GEMINI GENERATION
# ============================================================

def generate_with_retry(
    client,
    model,
    contents,
    attempts=3
):

    last_error = None

    for attempt in range(
        1,
        attempts + 1
    ):

        try:

            response = client.models.generate_content(
                model=model,
                contents=contents,
            )

            text = getattr(
                response,
                "text",
                None
            )

            if text and text.strip():
                return text.strip()

            # Some SDK versions may expose candidates.
            candidates = getattr(
                response,
                "candidates",
                None
            )

            if candidates:

                parts = []

                for candidate in candidates:

                    content = getattr(
                        candidate,
                        "content",
                        None
                    )

                    if not content:
                        continue

                    for part in getattr(
                        content,
                        "parts",
                        []
                    ):

                        part_text = getattr(
                            part,
                            "text",
                            None
                        )

                        if part_text:
                            parts.append(
                                part_text
                            )

                combined = "\n".join(
                    parts
                ).strip()

                if combined:
                    return combined

            raise RuntimeError(
                "Gemini returned an empty response."
            )

        except Exception as exc:

            last_error = exc

            if attempt < attempts:
                time.sleep(
                    2 * attempt
                )

    raise RuntimeError(
        "Gemini generation failed:\n" +
        safe_error(last_error)
    )


def run_gemini_video_analysis(
    video_path,
    api_key,
    target_language="Burmese",
    recap_length="Medium",
    style="Cinematic",
    model=None
):

    api_key = get_api_key(
        api_key
    )

    if not api_key:
        raise RuntimeError(
            "Gemini API Key is required."
        )

    video_path = as_filepath(
        video_path
    )

    if not video_path:
        raise RuntimeError(
            "Please upload a video."
        )

    if not os.path.isfile(video_path):
        raise RuntimeError(
            "Video file not found."
        )

    validate_video_duration(
        video_path
    )

    client = genai.Client(
        api_key=api_key
    )

    prompt = build_recap_prompt(
        target_language,
        recap_length,
        style
    )

    uploaded = None
    safe_path = None

    try:

        # ----------------------------------------------------
        # Upload
        # ----------------------------------------------------

        uploaded, safe_path = (
            upload_video_to_gemini_safe(
                client,
                video_path
            )
        )

        # ----------------------------------------------------
        # Wait for processing
        # ----------------------------------------------------

        uploaded = wait_for_gemini_video(
            client,
            uploaded
        )

        # ----------------------------------------------------
        # Generate
        # ----------------------------------------------------

        selected_model = model or DEFAULT_MODEL

        last_error = None

        for current_model in GEMINI_MODELS:

            if model:
                current_model = model

            try:

                result = generate_with_retry(
                    client,
                    current_model,
                    [
                        uploaded,
                        prompt,
                    ],
                    attempts=2
                )

                if result:
                    return clean_script_for_display(
                        result
                    )

            except Exception as exc:

                last_error = exc

                if model:
                    break

        raise RuntimeError(
            "Gemini analysis failed:\n" +
            safe_error(last_error)
        )

    finally:

        # Remove local Gemini-safe copy.
        if (
            safe_path and
            safe_path != video_path
        ):
            cleanup_file(
                safe_path
            )


# ============================================================
# TRANSLATION
# ============================================================

def translate_to_target_language(
    script,
    api_key,
    target_language,
    model=None
):

    script = clean_script_for_tts(
        script
    )

    if not script:
        raise RuntimeError(
            "Script is empty."
        )

    api_key = get_api_key(
        api_key
    )

    if not api_key:
        raise RuntimeError(
            "Gemini API Key is required."
        )

    client = genai.Client(
        api_key=api_key
    )

    language_names = {
        "Burmese": "Burmese",
        "English": "English",
        "Thai": "Thai",
        "Chinese": "Simplified Chinese",
    }

    target = language_names.get(
        target_language,
        target_language
    )

    prompt = f"""
Translate the following movie recap narration into {target}.

IMPORTANT:
- Preserve the meaning.
- Do not summarize.
- Do not add information.
- Keep the same paragraph structure where possible.
- Make it natural for voice-over.
- Do not output explanations.
- Output ONLY the translated narration.

TEXT:

{script}
"""

    selected_model = model or DEFAULT_MODEL

    return generate_with_retry(
        client,
        selected_model,
        [prompt],
        attempts=3
    )


# ============================================================
# TTS
# ============================================================

def generate_tts_file(
    text,
    voice,
    output_path,
    rate="+0%",
    pitch="+0Hz"
):

    text = clean_script_for_tts(
        text
    )

    if not text:
        raise RuntimeError(
            "Text is empty."
        )

    if not voice:
        raise RuntimeError(
            "Voice is required."
        )

    async def _generate():

        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=rate,
            pitch=pitch,
        )

        await communicate.save(
            output_path
        )

    asyncio.run(
        _generate()
    )

    if not os.path.isfile(output_path):
        raise RuntimeError(
            "TTS output was not created."
        )

    return output_path


def run_tts_sync(
    text,
    voice_name,
    rate="+0%",
    pitch="+0Hz"
):

    voice = VOICE_MAP.get(
        voice_name
    )

    if not voice:
        raise RuntimeError(
            "Unknown voice."
        )

    job_dir = create_job_dir(
        "tts"
    )

    output_path = os.path.join(
        job_dir,
        "narration.mp3"
    )

    generate_tts_file(
        text,
        voice,
        output_path,
        rate=rate,
        pitch=pitch
    )

    return output_path


# ============================================================
# TAB 1
# ============================================================

def tab1_preview_video(video):

    path = as_filepath(video)

    if not path:
        return None

    return path


def tab1_load_url(url):

    if not url or not url.strip():
        return None, "❌ Please enter a video URL."

    try:

        path = download_video_from_link(
            url
        )

        validate_video_duration(
            path
        )

        return (
            path,
            "✅ Video loaded. Preview is ready."
        )

    except Exception as exc:

        return (
            None,
            "❌ " + safe_error(exc)
        )


def tab1_analyze(
    video,
    url,
    api_key,
    language,
    recap_length,
    style,
    model
):

    status_messages = []

    try:

        path = as_filepath(
            video
        )

        # URL has priority if no uploaded video.
        if not path and url:

            status_messages.append(
                "⬇️ Downloading video..."
            )

            path = download_video_from_link(
                url
            )

        if not path:
            raise RuntimeError(
                "Please upload a video or enter a video URL."
            )

        status_messages.append(
            "🔎 Checking video..."
        )

        duration = validate_video_duration(
            path
        )

        status_messages.append(
            f"🎬 Video duration: {duration:.1f}s"
        )

        status_messages.append(
            "☁️ Uploading video to Gemini..."
        )

        script = run_gemini_video_analysis(
            path,
            api_key,
            get_target_language(language),
            recap_length,
            style,
            model
        )

        if not script:
            raise RuntimeError(
                "Gemini returned an empty script."
            )

        job_dir = create_job_dir(
            "subtitles"
        )

        srt_path, zip_path = (
            generate_srt_and_zip(
                script,
                job_dir,
                duration
            )
        )

        status_messages.append(
            "📝 Script generated."
        )

        status_messages.append(
            "📦 SRT package created."
        )

        return (
            script,
            srt_path,
            zip_path,
            "\n".join(status_messages) +
            "\n\n✅ Completed."
        )

    except Exception as exc:

        return (
            "",
            None,
            None,
            "❌ Error:\n" +
            safe_error(exc)
        )


# ============================================================
# TAB 2
# ============================================================

def handle_direct_translate(
    script,
    api_key,
    language,
    model
):

    try:

        translated = translate_to_target_language(
            script,
            api_key,
            language,
            model
        )

        return (
            clean_script_for_display(
                translated
            ),
            "✅ Translation completed."
        )

    except Exception as exc:

        return (
            script or "",
            "❌ " + safe_error(exc)
        )


def tab2_tts_with_auto_translate(
    script,
    api_key,
    language,
    voice_name,
    auto_translate,
    rate,
    pitch
):

    try:

        script = clean_script_for_tts(
            script
        )

        if not script:
            raise RuntimeError(
                "Please enter a script."
            )

        if auto_translate:

            script = translate_to_target_language(
                script,
                api_key,
                language
            )

        audio_path = run_tts_sync(
            script,
            voice_name,
            rate,
            pitch
        )

        return (
            script,
            audio_path,
            "✅ TTS generated successfully."
        )

    except Exception as exc:

        return (
            script or "",
            None,
            "❌ " + safe_error(exc)
        )


# ============================================================
# VOICE DROPDOWN
# ============================================================

def update_voice_choices(language):

    mapping = {
        "Burmese": [
            "Burmese Male",
            "Burmese Female",
        ],

        "English": [
            "English Male",
            "English Female",
        ],

        "Thai": [
            "Thai Male",
            "Thai Female",
        ],

        "Chinese": [
            "Chinese Male",
            "Chinese Female",
        ],
    }

    choices = mapping.get(
        language,
        [
            "Burmese Male",
            "Burmese Female",
        ]
    )

    return gr.update(
        choices=choices,
        value=choices[0]
    )


# ============================================================
# PREVIEW CSS
# ============================================================

def get_ratio_config(
    ratio
):

    configs = {

        "16:9": {
            "width": "100%",
            "aspect": "16 / 9",
        },

        "9:16": {
            "width": "100%",
            "aspect": "9 / 16",
        },

        "1:1": {
            "width": "100%",
            "aspect": "1 / 1",
        },

        "4:5": {
            "width": "100%",
            "aspect": "4 / 5",
        },

        "4:3": {
            "width": "100%",
            "aspect": "4 / 3",
        },
    }

    return configs.get(
        ratio,
        configs["16:9"]
    )


def get_ratio_css(
    ratio
):

    config = get_ratio_config(
        ratio
    )

    return f"""
    #tab3_preview_box video {{
        width: 100% !important;
        max-width: 100% !important;
        aspect-ratio: {config["aspect"]};
        object-fit: contain !important;
        background: #000 !important;
    }}
    """


# ============================================================
# COLOR HELPERS
# ============================================================

def hex_to_rgba(
    hex_color,
    opacity=1.0
):

    if not hex_color:
        hex_color = "#000000"

    value = hex_color.strip().lstrip("#")

    if len(value) == 3:
        value = "".join(
            c + c
            for c in value
        )

    if len(value) != 6:
        value = "000000"

    try:

        r = int(
            value[0:2],
            16
        )

        g = int(
            value[2:4],
            16
        )

        b = int(
            value[4:6],
            16
        )

    except Exception:

        r, g, b = 0, 0, 0

    opacity = max(
        0,
        min(
            1,
            float(opacity)
        )
    )

    return (
        r,
        g,
        b,
        opacity
    )


def hex_to_ass_color(
    hex_color
):

    if not hex_color:
        hex_color = "#FFFFFF"

    value = hex_color.strip().lstrip("#")

    if len(value) == 3:
        value = "".join(
            c + c
            for c in value
        )

    if len(value) != 6:
        value = "FFFFFF"

    try:

        r = int(
            value[0:2],
            16
        )

        g = int(
            value[2:4],
            16
        )

        b = int(
            value[4:6],
            16
        )

        # ASS = &HAABBGGRR
        return (
            f"&H00"
            f"{b:02X}"
            f"{g:02X}"
            f"{r:02X}"
        )

    except Exception:

        return "&H00FFFFFF"


# ============================================================
# TAB 3 LIVE PREVIEW
# ============================================================

def get_tab3_in_video_preview_html(
    ratio="16:9",
    overlay_text="",
    text_color="#FFFFFF",
    bg_color="#000000",
    bg_opacity=0.55,
    position="Bottom"
):

    config = get_ratio_config(
        ratio
    )

    if position == "Top":
        position_css = (
            "top: 20px;"
            "bottom: auto;"
        )

    elif position == "Center":
        position_css = (
            "top: 50%;"
            "bottom: auto;"
            "transform: translate(-50%, -50%);"
        )

    else:
        position_css = (
            "bottom: 20px;"
            "top: auto;"
        )

    r, g, b, a = hex_to_rgba(
        bg_color,
        bg_opacity
    )

    safe_text = (
        overlay_text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

    return f"""
    <div style="
        position:relative;
        width:100%;
        max-width:900px;
        margin:auto;
        aspect-ratio:{config['aspect']};
        pointer-events:none;
    ">

        <div style="
            position:absolute;
            left:50%;
            {position_css}
            max-width:90%;
            padding:10px 18px;
            border-radius:12px;
            background:rgba({r},{g},{b},{a});
            color:{text_color};
            font-size:22px;
            font-weight:600;
            text-align:center;
            line-height:1.4;
            z-index:20;
            white-space:pre-wrap;
            box-sizing:border-box;
        ">
            {safe_text}
        </div>

    </div>
    """


def tab3_preview_upload(
    video
):

    path = as_filepath(
        video
    )

    if not path:
        return None

    return path


# ============================================================
# FFMPEG FILTER HELPERS
# ============================================================

def get_font_file():

    candidates = []

    if sys.platform.startswith("win"):

        candidates.extend([
            r"C:\Windows\Fonts\NotoSansMyanmar-Regular.ttf",
            r"C:\Windows\Fonts\NotoSansThai-Regular.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ])

    else:

        candidates.extend([
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
        ])

    for path in candidates:

        if os.path.isfile(path):
            return path

    return None


def escape_filter_value(
    value
):

    value = str(value)

    value = value.replace(
        "\\",
        "\\\\"
    )

    value = value.replace(
        ":",
        "\\:"
    )

    value = value.replace(
        "'",
        "\\'"
    )

    value = value.replace(
        "[",
        "\\["
    )

    value = value.replace(
        "]",
        "\\]"
    )

    return value


# ============================================================
# FINAL VIDEO RENDER
# ============================================================

def render_advanced_clip(
    video_path,
    audio_path,
    srt_path=None,
    output_path=None,
    ratio="16:9",
    video_speed=1.0,
    subtitle_size=30,
    subtitle_color="#FFFFFF",
    subtitle_outline="#000000",
    subtitle_opacity=1.0,
    subtitle_position="Bottom",
    overlay_text="",
    overlay_text_color="#FFFFFF",
    overlay_bg_color="#000000",
    overlay_bg_opacity=0.55,
    overlay_position="Bottom",
    crop_enabled=False,
    crop_zoom=1.0,
):

    require_ffmpeg()

    video_path = as_filepath(
        video_path
    )

    audio_path = as_filepath(
        audio_path
    )

    srt_path = as_filepath(
        srt_path
    )

    if not video_path:
        raise RuntimeError(
            "Video file is required."
        )

    if not audio_path:
        raise RuntimeError(
            "Audio file is required."
        )

    if not os.path.isfile(video_path):
        raise RuntimeError(
            "Video file not found."
        )

    if not os.path.isfile(audio_path):
        raise RuntimeError(
            "Audio file not found."
        )

    if output_path is None:

        job_dir = create_job_dir(
            "render"
        )

        output_path = os.path.join(
            job_dir,
            "final_movie.mp4"
        )

    video_speed = max(
        0.5,
        min(
            2.0,
            float(video_speed)
        )
    )

    filters = []

    # --------------------------------------------------------
    # Aspect Ratio
    # --------------------------------------------------------

    if ratio == "16:9":
        target_w, target_h = 1280, 720

    elif ratio == "9:16":
        target_w, target_h = 720, 1280

    elif ratio == "1:1":
        target_w, target_h = 1080, 1080

    elif ratio == "4:5":
        target_w, target_h = 1080, 1350

    elif ratio == "4:3":
        target_w, target_h = 1024, 768

    else:
        target_w, target_h = 1280, 720

    # --------------------------------------------------------
    # Scale / Crop
    # --------------------------------------------------------

    if crop_enabled:

        zoom = max(
            1.0,
            min(
                2.0,
                float(crop_zoom)
            )
        )

        crop_w = int(
            target_w / zoom
        )

        crop_h = int(
            target_h / zoom
        )

        crop_w = max(
            2,
            crop_w
        )

        crop_h = max(
            2,
            crop_h
        )

        filters.append(
            f"scale={target_w}:{target_h}:"
            f"force_original_aspect_ratio=increase"
        )

        filters.append(
            f"crop={crop_w}:{crop_h}:"
            f"(iw-{crop_w})/2:"
            f"(ih-{crop_h})/2"
        )

        filters.append(
            f"scale={target_w}:{target_h}"
        )

    else:

        filters.append(
            f"scale={target_w}:{target_h}:"
            f"force_original_aspect_ratio=decrease"
        )

        filters.append(
            f"pad={target_w}:{target_h}:"
            f"(ow-iw)/2:(oh-ih)/2"
        )

    # --------------------------------------------------------
    # Video Speed
    # --------------------------------------------------------

    filters.append(
        f"setpts=PTS/{video_speed}"
    )

    # --------------------------------------------------------
    # Subtitles
    # --------------------------------------------------------

    if srt_path and os.path.isfile(srt_path):

        # FFmpeg subtitle filter can struggle with Windows paths.
        # Convert to forward slashes and escape colon.
        subtitle_file = os.path.abspath(
            srt_path
        ).replace(
            "\\",
            "/"
        )

        subtitle_file = subtitle_file.replace(
            ":",
            "\\:"
        )

        primary = hex_to_ass_color(
            subtitle_color
        )

        outline = hex_to_ass_color(
            subtitle_outline
        )

        alignment = {
            "Top": 8,
            "Center": 5,
            "Bottom": 2,
        }.get(
            subtitle_position,
            2
        )

        # Subtitle opacity
        alpha = int(
            (1.0 - max(
                0,
                min(
                    1,
                    subtitle_opacity
                )
            )) * 255
        )

        alpha_hex = f"{alpha:02X}"

        primary = (
            f"&H{alpha_hex}"
            f"{primary[4:]}"
        )

        subtitle_filter = (
            f"subtitles='{subtitle_file}':"
            f"force_style="
            f"'FontSize={int(subtitle_size)},"
            f"PrimaryColour={primary},"
            f"OutlineColour={outline},"
            f"BorderStyle=1,"
            f"Outline=2,"
            f"Shadow=0,"
            f"Alignment={alignment}'"
        )

        filters.append(
            subtitle_filter
        )

    filter_complex = ",".join(
        filters
    )

    # --------------------------------------------------------
    # Audio speed
    # --------------------------------------------------------

    # atempo accepts 0.5 - 2.0.
    atempo_filter = (
        f"atempo={video_speed}"
    )

    # --------------------------------------------------------
    # FFmpeg
    # --------------------------------------------------------

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",

        "-i",
        video_path,

        "-i",
        audio_path,

        "-filter_complex",
        f"[0:v]{filter_complex}[vout];"
        f"[1:a]{atempo_filter}[aout]",

        "-map",
        "[vout]",

        "-map",
        "[aout]",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "20",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-movflags",
        "+faststart",

        "-shortest",

        output_path,
    ]

    code, _, stderr = run_command(
        command,
        timeout=3600
    )

    if code != 0:
        raise RuntimeError(
            "Final rendering failed:\n" +
            stderr[-5000:]
        )

    if not os.path.isfile(output_path):
        raise RuntimeError(
            "Final video was not created."
        )

    return output_path


# ============================================================
# TAB 3 AUTO PIPELINE
# ============================================================

def tab3_auto_pipeline(
    video,
    api_key,
    language,
    voice_name,
    model,
    recap_length,
    style,
    video_speed,
    ratio,
    subtitle_size,
    subtitle_color,
    subtitle_outline,
    subtitle_opacity,
    subtitle_position,
    crop_enabled,
    crop_zoom,
    overlay_text,
    overlay_text_color,
    overlay_bg_color,
    overlay_bg_opacity,
    overlay_position,
):

    try:

        path = as_filepath(
            video
        )

        if not path:
            raise RuntimeError(
                "Please upload a video."
            )

        duration = validate_video_duration(
            path
        )

        # ----------------------------------------------------
        # 1. Gemini
        # ----------------------------------------------------

        script = run_gemini_video_analysis(
            path,
            api_key,
            language,
            recap_length,
            style,
            model
        )

        # ----------------------------------------------------
        # 2. TTS
        # ----------------------------------------------------

        tts_dir = create_job_dir(
            "auto_tts"
        )

        audio_path = os.path.join(
            tts_dir,
            "narration.mp3"
        )

        voice = VOICE_MAP.get(
            voice_name
        )

        if not voice:
            raise RuntimeError(
                "Invalid voice."
            )

        generate_tts_file(
            script,
            voice,
            audio_path
        )

        # ----------------------------------------------------
        # 3. SRT
        # ----------------------------------------------------

        subtitle_dir = create_job_dir(
            "auto_srt"
        )

        srt_path, zip_path = (
            generate_srt_and_zip(
                script,
                subtitle_dir,
                duration
            )
        )

        # ----------------------------------------------------
        # 4. Scene aware
        # ----------------------------------------------------

        scene_dir = create_job_dir(
            "auto_scene"
        )

        scene_video = os.path.join(
            scene_dir,
            "scene_cleaned.mp4"
        )

        try:

            processed_video = (
                prepare_scene_aware_video(
                    path,
                    scene_video
                )
            )

        except Exception:

            # If scene processing fails,
            # use original video instead.
            processed_video = path

        # ----------------------------------------------------
        # 5. Final render
        # ----------------------------------------------------

        render_dir = create_job_dir(
            "final"
        )

        output_path = os.path.join(
            render_dir,
            "AI_Movie_Recap_Final.mp4"
        )

        final_video = render_advanced_clip(
            video_path=processed_video,
            audio_path=audio_path,
            srt_path=srt_path,
            output_path=output_path,

            ratio=ratio,
            video_speed=video_speed,

            subtitle_size=subtitle_size,
            subtitle_color=subtitle_color,
            subtitle_outline=subtitle_outline,
            subtitle_opacity=subtitle_opacity,
            subtitle_position=subtitle_position,

            overlay_text=overlay_text,
            overlay_text_color=overlay_text_color,
            overlay_bg_color=overlay_bg_color,
            overlay_bg_opacity=overlay_bg_opacity,
            overlay_position=overlay_position,

            crop_enabled=crop_enabled,
            crop_zoom=crop_zoom,
        )

        return (
            script,
            audio_path,
            srt_path,
            zip_path,
            final_video,
            "✅ All-in-One pipeline completed successfully."
        )

    except Exception as exc:

        return (
            "",
            None,
            None,
            None,
            None,
            "❌ Error:\n" +
            safe_error(exc)
        )


# ============================================================
# CUSTOM CSS
# ============================================================

CUSTOM_CSS = """
.gradio-container {
    max-width: 1500px !important;
}

#tab3_preview_wrapper {
    position: relative;
}

#tab3_preview_box {
    width: 100%;
}

#tab3_preview_box video {
    width: 100% !important;
    max-width: 100% !important;
    background: #000 !important;
    border-radius: 12px !important;
}

textarea {
    font-size: 16px !important;
    line-height: 1.6 !important;
}

.status-box textarea {
    font-family: monospace !important;
}

.preview-note {
    font-size: 13px;
    opacity: 0.75;
}
"""


# ============================================================
# UI
# ============================================================

with gr.Blocks(
    title=APP_TITLE,
    css=CUSTOM_CSS
) as app:

    gr.Markdown(
        f"""
# 🎬 {APP_TITLE}

AI Movie Recap Studio with:

- Gemini video analysis
- Burmese / English / Thai / Chinese narration
- Edge TTS
- SRT subtitles
- One-click final video generation
- Immediate video preview
- Unicode-safe Gemini upload
"""
    )

    # ========================================================
    # API KEY
    # ========================================================

    with gr.Accordion(
        "🔑 Gemini API Key Setting",
        open=not bool(SAVED_API_KEY)
    ):

        api_key_input = gr.Textbox(
            label="Gemini API Key",
            value=SAVED_API_KEY,
            type="password",
            placeholder="AIza..."
        )

        save_key_btn = gr.Button(
            "💾 Save API Key"
        )

        api_key_status = gr.Markdown()

        save_key_btn.click(
            save_api_key,
            inputs=api_key_input,
            outputs=api_key_status
        )

    # ========================================================
    # TABS
    # ========================================================

    with gr.Tabs():

        # ====================================================
        # TAB 1
        # ====================================================

        with gr.Tab(
            "1️⃣ Video Analysis & Script"
        ):

            gr.Markdown(
                """
### Upload a video or use a video URL

**Preview appears immediately when the video is selected.**
"""
            )

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    v1_file = gr.Video(
                        label="Upload Movie / Video",
                        sources=["upload"],
                        type="filepath",
                        elem_id="tab1_video_input"
                    )

                    v1_url = gr.Textbox(
                        label="Video URL",
                        placeholder="https://..."
                    )

                    v1_load_btn = gr.Button(
                        "⬇️ Load Video from URL"
                    )

                    v1_preview = gr.Video(
                        label="🎥 Instant Preview",
                        interactive=False,
                        autoplay=True,
                        show_download_button=False,
                        elem_id="tab1_preview"
                    )

                    v1_preview_status = gr.Markdown(
                        elem_classes=["status-box"]
                    )

                with gr.Column(
                    scale=1
                ):

                    v1_language = gr.Dropdown(
                        choices=LANGUAGE_CHOICES,
                        value="Burmese",
                        label="Target Language"
                    )

                    v1_length = gr.Dropdown(
                        choices=[
                            "Short",
                            "Medium",
                            "Long",
                        ],
                        value="Medium",
                        label="Recap Length"
                    )

                    v1_style = gr.Dropdown(
                        choices=[
                            "Cinematic",
                            "Simple",
                            "Dramatic",
                            "YouTube",
                        ],
                        value="Cinematic",
                        label="Narration Style"
                    )

                    v1_model = gr.Dropdown(
                        choices=GEMINI_MODELS,
                        value=DEFAULT_MODEL,
                        label="Gemini Model"
                    )

                    v1_analyze_btn = gr.Button(
                        "🚀 Analyze Video & Generate Script",
                        variant="primary"
                    )

            v1_script = gr.Textbox(
                label="📝 Generated Recap Script",
                lines=18,
                show_copy_button=True
            )

            with gr.Row():

                v1_srt = gr.File(
                    label="SRT Subtitle"
                )

                v1_zip = gr.File(
                    label="SRT ZIP"
                )

            v1_status = gr.Textbox(
                label="Status",
                lines=6,
                interactive=False
            )

            # Immediate preview
            v1_file.change(
                tab1_preview_video,
                inputs=v1_file,
                outputs=v1_preview
            )

            # URL -> preview
            v1_load_btn.click(
                tab1_load_url,
                inputs=v1_url,
                outputs=[
                    v1_preview,
                    v1_preview_status
                ]
            )

            # Analyze
            v1_analyze_btn.click(
                tab1_analyze,
                inputs=[
                    v1_file,
                    v1_url,
                    api_key_input,
                    v1_language,
                    v1_length,
                    v1_style,
                    v1_model,
                ],
                outputs=[
                    v1_script,
                    v1_srt,
                    v1_zip,
                    v1_status,
                ]
            )

        # ====================================================
        # TAB 2
        # ====================================================

        with gr.Tab(
            "2️⃣ Text-to-Speech"
        ):

            with gr.Row():

                with gr.Column():

                    t2_language = gr.Dropdown(
                        choices=LANGUAGE_CHOICES,
                        value="Burmese",
                        label="Language"
                    )

                    t2_voice = gr.Dropdown(
                        choices=[
                            "Burmese Male",
                            "Burmese Female",
                        ],
                        value="Burmese Male",
                        label="Voice"
                    )

                    t2_auto_translate = gr.Checkbox(
                        value=False,
                        label="🌐 Auto Translate Before TTS"
                    )

                    t2_rate = gr.Dropdown(
                        choices=[
                            "-20%",
                            "-10%",
                            "+0%",
                            "+10%",
                            "+20%",
                        ],
                        value="+0%",
                        label="Speech Rate"
                    )

                    t2_pitch = gr.Dropdown(
                        choices=[
                            "-10Hz",
                            "-5Hz",
                            "+0Hz",
                            "+5Hz",
                            "+10Hz",
                        ],
                        value="+0Hz",
                        label="Pitch"
                    )

                with gr.Column():

                    t2_script = gr.Textbox(
                        label="Script",
                        lines=18,
                        placeholder="Paste narration script here..."
                    )

                    t2_translate_btn = gr.Button(
                        "🌐 Translate Script"
                    )

                    t2_translate_status = gr.Markdown()

                    t2_tts_btn = gr.Button(
                        "🔊 Generate Voice",
                        variant="primary"
                    )

                    t2_audio = gr.Audio(
                        label="Generated Voice",
                        type="filepath",
                        autoplay=False
                    )

                    t2_status = gr.Markdown()

            t2_language.change(
                update_voice_choices,
                inputs=t2_language,
                outputs=t2_voice
            )

            t2_translate_btn.click(
                handle_direct_translate,
                inputs=[
                    t2_script,
                    api_key_input,
                    t2_language,
                    v1_model,
                ],
                outputs=[
                    t2_script,
                    t2_translate_status
                ]
            )

            t2_tts_btn.click(
                tab2_tts_with_auto_translate,
                inputs=[
                    t2_script,
                    api_key_input,
                    t2_language,
                    t2_voice,
                    t2_auto_translate,
                    t2_rate,
                    t2_pitch,
                ],
                outputs=[
                    t2_script,
                    t2_audio,
                    t2_status,
                ]
            )

        # ====================================================
        # TAB 3
        # ====================================================

        with gr.Tab(
            "3️⃣ One-Click All-in-One Video Studio"
        ):

            gr.Markdown(
                """
### 🎬 One Click = Analyze → TTS → SRT → Render Final Video

Upload video first. **Preview appears immediately.**
"""
            )

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    t3_file = gr.Video(
                        label="Upload Video",
                        sources=["upload"],
                        type="filepath",
                        elem_id="tab3_input"
                    )

                    t3_live_video = gr.Video(
                        label="🎥 Instant Preview",
                        interactive=False,
                        autoplay=True,
                        show_download_button=False,
                        elem_id="tab3_preview_box"
                    )

                    t3_url = gr.Textbox(
                        label="Video URL",
                        placeholder="https://..."
                    )

                    t3_load_url_btn = gr.Button(
                        "⬇️ Load URL"
                    )

                    t3_url_status = gr.Markdown()

                with gr.Column(
                    scale=1
                ):

                    t3_language = gr.Dropdown(
                        choices=LANGUAGE_CHOICES,
                        value="Burmese",
                        label="Target Language"
                    )

                    t3_voice = gr.Dropdown(
                        choices=[
                            "Burmese Male",
                            "Burmese Female",
                        ],
                        value="Burmese Male",
                        label="Voice"
                    )

                    t3_model = gr.Dropdown(
                        choices=GEMINI_MODELS,
                        value=DEFAULT_MODEL,
                        label="Gemini Model"
                    )

                    t3_length = gr.Dropdown(
                        choices=[
                            "Short",
                            "Medium",
                            "Long",
                        ],
                        value="Medium",
                        label="Recap Length"
                    )

                    t3_style = gr.Dropdown(
                        choices=[
                            "Cinematic",
                            "Simple",
                            "Dramatic",
                            "YouTube",
                        ],
                        value="Cinematic",
                        label="Narration Style"
                    )

                    t3_speed = gr.Slider(
                        minimum=0.5,
                        maximum=2.0,
                        value=1.0,
                        step=0.05,
                        label="Video / Voice Speed"
                    )

            # =================================================
            # VIDEO FORMAT
            # =================================================

            with gr.Accordion(
                "📐 Video Format",
                open=True
            ):

                t3_ratio = gr.Dropdown(
                    choices=[
                        "16:9",
                        "9:16",
                        "1:1",
                        "4:5",
                        "4:3",
                    ],
                    value="16:9",
                    label="Aspect Ratio"
                )

                with gr.Row():

                    t3_crop_enabled = gr.Checkbox(
                        value=False,
                        label="Enable Center Crop"
                    )

                    t3_crop_zoom = gr.Slider(
                        minimum=1.0,
                        maximum=2.0,
                        value=1.0,
                        step=0.05,
                        label="Crop Zoom"
                    )

            # =================================================
            # SUBTITLE
            # =================================================

            with gr.Accordion(
                "💬 Subtitle Settings",
                open=True
            ):

                with gr.Row():

                    t3_subtitle_size = gr.Slider(
                        minimum=16,
                        maximum=80,
                        value=30,
                        step=1,
                        label="Subtitle Size"
                    )

                    t3_subtitle_opacity = gr.Slider(
                        minimum=0.0,
                        maximum=1.0,
                        value=1.0,
                        step=0.05,
                        label="Subtitle Opacity"
                    )

                with gr.Row():

                    t3_subtitle_color = gr.ColorPicker(
                        value="#FFFFFF",
                        label="Subtitle Color"
                    )

                    t3_subtitle_outline = gr.ColorPicker(
                        value="#000000",
                        label="Subtitle Outline"
                    )

                t3_subtitle_position = gr.Dropdown(
                    choices=[
                        "Top",
                        "Center",
                        "Bottom",
                    ],
                    value="Bottom",
                    label="Subtitle Position"
                )

            # =================================================
            # OVERLAY
            # =================================================

            with gr.Accordion(
                "🖼️ Overlay Text",
                open=False
            ):

                t3_overlay_text = gr.Textbox(
                    label="Overlay Text",
                    placeholder="Optional watermark / title..."
                )

                with gr.Row():

                    t3_overlay_text_color = gr.ColorPicker(
                        value="#FFFFFF",
                        label="Text Color"
                    )

                    t3_overlay_bg_color = gr.ColorPicker(
                        value="#000000",
                        label="Background Color"
                    )

                t3_overlay_bg_opacity = gr.Slider(
                    minimum=0.0,
                    maximum=1.0,
                    value=0.55,
                    step=0.05,
                    label="Background Opacity"
                )

                t3_overlay_position = gr.Dropdown(
                    choices=[
                        "Top",
                        "Center",
                        "Bottom",
                    ],
                    value="Bottom",
                    label="Overlay Position"
                )

            # =================================================
            # LIVE OVERLAY PREVIEW
            # =================================================

            t3_overlay_preview = gr.HTML(
                value=get_tab3_in_video_preview_html(
                    ratio="16:9"
                )
            )

            # =================================================
            # RUN
            # =================================================

            t3_run_btn = gr.Button(
                "🚀 RUN ALL-IN-ONE",
                variant="primary",
                size="lg"
            )

            t3_script_output = gr.Textbox(
                label="📝 Generated Script",
                lines=15,
                show_copy_button=True
            )

            with gr.Row():

                t3_audio_output = gr.Audio(
                    label="🔊 Narration"
                )

                t3_srt_output = gr.File(
                    label="📝 SRT"
                )

                t3_zip_output = gr.File(
                    label="📦 SRT ZIP"
                )

            t3_final_video = gr.Video(
                label="🎬 FINAL VIDEO",
                autoplay=False
            )

            t3_status = gr.Textbox(
                label="Status",
                lines=8,
                interactive=False
            )

            # =================================================
            # T3 IMMEDIATE PREVIEW
            # =================================================

            t3_file.change(
                tab3_preview_upload,
                inputs=t3_file,
                outputs=t3_live_video
            )

            # =================================================
            # T3 URL LOAD
            # =================================================

            def t3_load_url(
                url
            ):

                if not url or not url.strip():
                    return (
                        None,
                        "❌ Please enter URL."
                    )

                try:

                    path = download_video_from_link(
                        url
                    )

                    validate_video_duration(
                        path
                    )

                    return (
                        path,
                        "✅ Video loaded. Preview is ready."
                    )

                except Exception as exc:

                    return (
                        None,
                        "❌ " + safe_error(exc)
                    )

            t3_load_url_btn.click(
                t3_load_url,
                inputs=t3_url,
                outputs=[
                    t3_live_video,
                    t3_url_status
                ]
            )

            # =================================================
            # LIVE OVERLAY PREVIEW UPDATE
            # =================================================

            preview_inputs = [
                t3_ratio,
                t3_overlay_text,
                t3_overlay_text_color,
                t3_overlay_bg_color,
                t3_overlay_bg_opacity,
                t3_overlay_position,
            ]

            for component in preview_inputs:

                component.change(
                    get_tab3_in_video_preview_html,
                    inputs=preview_inputs,
                    outputs=t3_overlay_preview
                )

            # =================================================
            # RUN ALL
            # =================================================

            t3_run_btn.click(
                tab3_auto_pipeline,
                inputs=[
                    t3_file,
                    api_key_input,
                    t3_language,
                    t3_voice,
                    t3_model,
                    t3_length,
                    t3_style,
                    t3_speed,
                    t3_ratio,
                    t3_subtitle_size,
                    t3_subtitle_color,
                    t3_subtitle_outline,
                    t3_subtitle_opacity,
                    t3_subtitle_position,
                    t3_crop_enabled,
                    t3_crop_zoom,
                    t3_overlay_text,
                    t3_overlay_text_color,
                    t3_overlay_bg_color,
                    t3_overlay_bg_opacity,
                    t3_overlay_position,
                ],
                outputs=[
                    t3_script_output,
                    t3_audio_output,
                    t3_srt_output,
                    t3_zip_output,
                    t3_final_video,
                    t3_status,
                ]
            )


# ============================================================
# LAUNCH
# ============================================================

def main():

    port = int(
        os.environ.get(
            "PORT",
            "7860"
        )
    )

    print("=" * 70)
    print(APP_TITLE)
    print("=" * 70)
    print(
        "Temp directory:",
        APP_TEMP_DIR
    )
    print(
        "ASCII safe:",
        _is_ascii(APP_TEMP_DIR)
    )
    print(
        "FFmpeg:",
        command_exists("ffmpeg")
    )
    print(
        "FFprobe:",
        command_exists("ffprobe")
    )
    print(
        "Gemini models:",
        ", ".join(GEMINI_MODELS)
    )
    print("=" * 70)

    app.launch(
        server_name="0.0.0.0",
        server_port=port,
        show_error=True,
        share=False,
    )


if __name__ == "__main__":
    main()
