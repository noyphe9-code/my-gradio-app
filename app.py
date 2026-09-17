import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

import edge_ts
import gradio as gr
from google import genai
from google.genai import types

# =========================================================
# AI MOVIE RECAP STUDIO PRO
# OPTIMIZED ALL-IN-ONE VERSION
# =========================================================

APP_TITLE = "AI Movie Recap Studio Pro"
MAX_VIDEO_MINUTES = 5

PORT = int(os.environ.get("PORT", "7860"))

# Render Environment Variable
SAVED_API_KEY = os.environ.get("GEMINI_API_KEY", "")


# =========================================================
# CURRENT GEMINI MODELS
# =========================================================

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


# =========================================================
# BURMESE VOICES
# =========================================================

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural",
}


# =========================================================
# WORKSPACE
# =========================================================

BASE_DIR = Path("studio_workspace")
BASE_DIR.mkdir(exist_ok=True)


def unique_file(prefix, ext):
    return str(BASE_DIR / f"{prefix}_{uuid.uuid4().hex[:10]}{ext}")


# =========================================================
# BASIC HELPERS
# =========================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def run_cmd(cmd, timeout=1800):
    print("\nRUNNING:")
    print(" ".join(str(x) for x in cmd))

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg process timeout.")

    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(result.stderr[-7000:])

    return result


def ffmpeg_exists():
    return shutil.which("ffmpeg") is not None


def ffprobe_exists():
    return shutil.which("ffprobe") is not None


# =========================================================
# API KEY
# =========================================================

def save_api_key(api_key):
    global SAVED_API_KEY
    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ Gemini API Key သိမ်းပြီးပါပြီ။"
    return "⚠️ Gemini API Key ထည့်ပါ။"


# =========================================================
# VIDEO INFO
# =========================================================

def get_video_info(video_path):
    if not video_path or not os.path.exists(video_path) or not ffprobe_exists():
        return {}

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout)
    except Exception:
        return {}


def get_video_duration(video_path):
    info = get_video_info(video_path)
    try:
        return float(info["format"]["duration"])
    except Exception:
        return None


def validate_video_duration(video_path):
    duration = get_video_duration(video_path)
    if duration is None:
        return True, "ℹ️ Video duration စစ်ဆေး၍မရပါ။"

    minutes = duration / 60
    if minutes > MAX_VIDEO_MINUTES:
        return False, (
            f"⚠️ Video {minutes:.1f} minutes ရှိပါတယ်။\n"
            f"အများဆုံး {MAX_VIDEO_MINUTES} minutes ပါ။"
        )
    return True, f"✅ Video Length: {minutes:.1f} minutes"


# =========================================================
# CLEAN SCRIPT
# =========================================================

def clean_script_for_tts(script_text):
    if not script_text:
        return ""

    lines = []
    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue

        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue)\]\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        line = re.sub(r"^\s*(Narrator|Narration)\s*:\s*", "", line, flags=re.IGNORECASE)

        if line.startswith("---"):
            continue

        if line.lower() in [
            "movie recap",
            "recap script",
            "burmese recap script",
            "script",
            "characters",
            "important events",
        ]:
            continue

        lines.append(line)

    return "\n".join(lines).strip()


# =========================================================
# SRT
# =========================================================

def seconds_to_srt_time(seconds):
    seconds = max(0, float(seconds))
    total_ms = int(seconds * 1000)
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def split_script_sentences(text):
    text = clean_script_for_tts(text)
    if not text:
        return []

    parts = re.split(r"(?<=[။!?])\s+|\n+", text)
    result = []
    for part in parts:
        part = part.strip()
        if part:
            result.append(part)
    return result


def generate_srt_content(script_text, audio_duration=None):
    clean_text = clean_script_for_tts(script_text)
    if not clean_text:
        return ""

    sentences = split_script_sentences(clean_text)
    if not sentences:
        return ""

    if not audio_duration:
        audio_duration = max(5, len(clean_text) / 10)

    weights = [max(1, len(sentence)) for sentence in sentences]
    total_weight = sum(weights)
    current = 0.0
    output = []

    for index, sentence in enumerate(sentences, start=1):
        duration = audio_duration * weights[index - 1] / total_weight
        start = current
        end = current + duration

        output.append(
            f"{index}\n"
            f"{seconds_to_srt_time(start)} --> "
            f"{seconds_to_srt_time(end)}\n"
            f"{sentence}\n"
        )
        current = end

    return "\n".join(output)


def generate_srt_and_zip(script_text, audio_duration=None):
    srt_content = generate_srt_content(script_text, audio_duration)
    if not srt_content:
        return None, None

    srt_filename = unique_file("subtitle", ".srt")
    zip_filename = unique_file("subtitle", ".zip")

    Path(srt_filename).write_text(srt_content, encoding="utf-8-sig")

    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(srt_filename, "myanmar_recap_subtitle.srt")

    return srt_filename, zip_filename


# =========================================================
# VIDEO DOWNLOAD & SANITIZE
# =========================================================

def sanitize_video_path(input_path):
    """Burmese သို့မဟုတ် ASCII မဟုတ်သော နာမည်များကြောင့် Error မတက်စေရန် Safe ဖြစ်သောနာမည်သို့ ပြောင်းပေးခြင်း"""
    if not input_path or not os.path.exists(input_path):
        return None
    
    ext = os.path.splitext(input_path)[1]
    if not ext:
        ext = ".mp4"
        
    safe_path = unique_file("input_video", ext)
    shutil.copy(input_path, safe_path)
    return safe_path


def download_video_from_link(link):
    if not link or not link.strip():
        return None

    output_template = str(BASE_DIR / f"download_{uuid.uuid4().hex[:10]}.%(ext)s")
    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "overwrites": True,
        "merge_output_format": "mp4",
    }

    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link.strip(), download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return sanitize_video_path(filename)

            base = os.path.splitext(filename)[0]
            for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                candidate = base + ext
                if os.path.exists(candidate):
                    return sanitize_video_path(candidate)
    except Exception as e:
        print("Download Error:", e)

    return None


def resolve_video(video_file, video_link):
    if video_file:
        if isinstance(video_file, dict):
            video_file = video_file.get("path") or video_file.get("name")
        if video_file:
            return sanitize_video_path(video_file)

    if video_link:
        return download_video_from_link(video_link)

    return None


# =========================================================
# RATIO
# =========================================================

RATIO_SIZES = {
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


def ratio_size(ratio):
    return RATIO_SIZES.get(ratio, RATIO_SIZES["9:16"])


# =========================================================
# GEMINI PROMPT & GENERATION
# =========================================================

def build_fast_recap_prompt(ratio):
    return f"""
You are a professional movie recap writer.
Analyze this video from beginning to end.
Target output: Burmese social-media movie recap.
Target ratio: {ratio}

IMPORTANT:
1. Use only events actually visible or audible.
2. Never invent story details, character names, or relationships.
3. If dialogue is clear, translate its meaning into Burmese naturally.
4. Return ONLY the final Burmese recap script without headings or timestamps.
"""


def retryable_error(error):
    text = str(error).lower()
    return any(
        x in text
        for x in [
            "429",
            "500",
            "503",
            "504",
            "unavailable",
            "resource_exhausted",
            "timeout",
            "deadline",
            "temporarily",
        ]
    )


def generate_script_fast(client, uploaded_file, prompt):
    last_error = None

    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                config = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                    temperature=0.4,
                )

                response = client.models.generate_content(
                    model=model, contents=[uploaded_file, prompt], config=config
                )

                if response and response.text and response.text.strip():
                    return response.text.strip(), model

                raise RuntimeError("Gemini Empty Response")

            except Exception as e:
                last_error = e
                print(f"{model} attempt {attempt+1}:", e)
                if not retryable_error(e):
                    break
                time.sleep(3 + attempt * 4)

    raise RuntimeError(f"Gemini failed: {last_error}")


# =========================================================
# MAIN VIDEO ANALYSIS FUNCTION
# =========================================================

def run_gemini_video_analysis(video_file, video_link, ratio_choice):
    global SAVED_API_KEY

    if not SAVED_API_KEY:
        return "", "⚠️ Gemini API Key မရှိပါ။", None, None, None

    source = resolve_video(video_file, video_link)
    if not source or not os.path.exists(source):
        return "", "⚠️ Video Upload သို့မဟုတ် URL ထည့်ပါ။", None, None, None

    valid, message = validate_video_duration(source)
    if not valid:
        return "", message, None, None, source

    try:
        client = genai.Client(api_key=SAVED_API_KEY)
        print("Uploading video to Gemini...")
        uploaded = client.files.upload(file=source)

        start = time.time()
        while True:
            state = uploaded.state.name if uploaded.state else ""
            print("Gemini:", state)

            if state == "ACTIVE":
                break
            if state == "FAILED":
                raise RuntimeError("Gemini Video Processing Failed.")
            if time.time() - start > 600:
                raise RuntimeError("Gemini Video Processing Timeout.")

            time.sleep(3)
            uploaded = client.files.get(name=uploaded.name)

        prompt = build_fast_recap_prompt(ratio_choice)
        print("Generating recap...")

        script, used_model = generate_script_fast(client, uploaded, prompt)
        script = clean_script_for_tts(script)

        if not script:
            raise RuntimeError("Gemini Script Empty.")

        duration = get_video_duration(source)
        srt_file, zip_file = generate_srt_and_zip(script, duration)

        return (
            script,
            "✅ ဇာတ်ညွှန်း အောင်မြင်စွာ ထွက်ရှိပါပြီ။",
            srt_file,
            zip_file,
            source,
        )

    except Exception as e:
        print("Error:", e)
        return "", f"⚠️ Error: {str(e)}", None, None, source
