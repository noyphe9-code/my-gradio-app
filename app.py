import gradio as gr
import edge_tts
import asyncio
import os
import re
import time
import zipfile
import subprocess
import yt_dlp
import random
import uuid
import shutil
import json
from pathlib import Path

from google import genai


# =========================================================
# AI MOVIE RECAP STUDIO PRO
# FULL VERSION
# 1. VIDEO ANALYSIS
# 2. BURMESE TTS
# 3. VIDEO EDIT
# =========================================================

APP_TITLE = "AI Movie Recap Studio Pro"

MAX_VIDEO_MINUTES = 5

# Render port
PORT = int(os.environ.get("PORT", "7860"))

# Gemini API Key
SAVED_API_KEY = os.environ.get("GEMINI_API_KEY", "")


# =========================================================
# GEMINI MODELS
# =========================================================

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]


# =========================================================
# BURMESE TTS
# =========================================================

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural":
        "my-MM-ThihaNeural",

    "Nilar (အမျိုးသမီးအသံ) - Natural":
        "my-MM-NilarNeural",
}


# =========================================================
# WORK DIRECTORY
# =========================================================

BASE_DIR = Path("studio_workspace")
BASE_DIR.mkdir(exist_ok=True)


def unique_file(prefix, ext):
    return str(
        BASE_DIR /
        f"{prefix}_{uuid.uuid4().hex[:10]}{ext}"
    )


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

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(
            result.stderr[-5000:]
        )

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

        return (
            "✅ Gemini API Key သိမ်းပြီးပါပြီ။"
        )

    return (
        "⚠️ Gemini API Key ထည့်ပေးပါ။"
    )


# =========================================================
# VIDEO DURATION
# =========================================================

def get_video_duration(video_path):

    if not video_path:
        return None

    if not os.path.exists(video_path):
        return None

    if not ffprobe_exists():
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
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            value = result.stdout.strip()

            if value:
                return float(value)

    except Exception as e:
        print("Duration Error:", e)

    return None


def validate_video_duration(video_path):

    duration = get_video_duration(video_path)

    if duration is None:
        return True, (
            "ℹ️ Video duration ကို "
            "စစ်ဆေး၍မရသေးပါ။"
        )

    minutes = duration / 60

    if minutes > MAX_VIDEO_MINUTES:
        return (
            False,
            (
                f"⚠️ Video သည် "
                f"{minutes:.1f} မိနစ်ရှိပါတယ်။\n\n"
                f"အများဆုံး "
                f"{MAX_VIDEO_MINUTES} မိနစ်အထိသာ "
                f"အသုံးပြုနိုင်ပါတယ်။"
            ),
        )

    return (
        True,
        f"✅ Video Length: {minutes:.1f} မိနစ်"
    )


# =========================================================
# CLEAN SCRIPT
# =========================================================

def clean_script_for_tts(script_text):

    if not script_text:
        return ""

    lines = script_text.splitlines()
    cleaned = []

    for line in lines:

        line = line.strip()

        if not line:
            continue

        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("`", "")

        line = re.sub(
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue)\]\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )

        line = re.sub(
            r"^\s*Narrator\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )

        if line.lower() in [
            "movie recap",
            "recap script",
            "burmese recap script",
            "script",
        ]:
            continue

        if line.startswith("---"):
            continue

        cleaned.append(line)

    return "\n".join(cleaned)


# =========================================================
# SRT
# =========================================================

def seconds_to_srt_time(seconds):

    seconds = max(0, int(seconds))

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    secs = seconds % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},000"
    )


def generate_srt_and_zip(script_text):

    clean_text = clean_script_for_tts(
        script_text
    )

    if not clean_text:
        return None, None

    lines = [
        x.strip()
        for x in clean_text.splitlines()
        if x.strip()
    ]

    srt_content = ""

    current_time = 0
    subtitle_index = 1

    for line in lines:

        char_count = len(line)

        duration = max(
            2,
            min(
                8,
                round(char_count / 11)
            )
        )

        start_time = current_time
        end_time = current_time + duration

        srt_content += (
            f"{subtitle_index}\n"
            f"{seconds_to_srt_time(start_time)} "
            f"--> "
            f"{seconds_to_srt_time(end_time)}\n"
            f"{line}\n\n"
        )

        current_time = end_time
        subtitle_index += 1

    srt_filename = unique_file(
        "subtitle",
        ".srt"
    )

    zip_filename = unique_file(
        "subtitle",
        ".zip"
    )

    with open(
        srt_filename,
        "w",
        encoding="utf-8-sig"
    ) as f:
        f.write(srt_content)

    with zipfile.ZipFile(
        zip_filename,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:

        zipf.write(
            srt_filename,
            arcname="myanmar_recap_subtitle.srt"
        )

    return (
        srt_filename,
        zip_filename
    )


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_video_from_link(link):

    if not link:
        return None

    link = link.strip()

    if not link:
        return None

    output_template = str(
        BASE_DIR /
        f"download_{uuid.uuid4().hex[:10]}.%(ext)s"
    )

    ydl_opts = {
        "format":
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]/best",

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

        "merge_output_format":
            "mp4",
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

            if os.path.exists(filename):
                return filename

            base = os.path.splitext(
                filename
            )[0]

            possible_files = [
                base + ".mp4",
                base + ".mkv",
                base + ".webm",
                base + ".mov",
            ]

            for file_path in possible_files:

                if os.path.exists(file_path):
                    return file_path

    except Exception as e:

        print(
            "Download Error:",
            e
        )

    return None


# =========================================================
# GET VIDEO INPUT
# =========================================================

def resolve_video(video_file, video_link):

    if video_file:
        return video_file

    if video_link:
        return download_video_from_link(
            video_link
        )

    return None


# =========================================================
# RATIO
# =========================================================

def ratio_values(ratio):

    values = {
        "9:16": (9, 16),
        "3:4": (3, 4),
        "1:1": (1, 1),
        "16:9": (16, 9),
    }

    return values.get(
        ratio,
        (9, 16)
    )


def get_ratio_css(ratio):

    configs = {

        "9:16": {
            "aspect": "9 / 16",
            "max_width": "360px",
        },

        "3:4": {
            "aspect": "3 / 4",
            "max_width": "420px",
        },

        "1:1": {
            "aspect": "1 / 1",
            "max_width": "500px",
        },

        "16:9": {
            "aspect": "16 / 9",
            "max_width": "650px",
        },
    }

    cfg = configs.get(
        ratio,
        configs["9:16"]
    )

    return f"""
<style>
#edit_preview_container {{
    width:100%!important;
    max-width:{cfg["max_width"]}!important;
    margin:auto!important;
}}

#edit_preview_container video {{
    width:100%!important;
    aspect-ratio:{cfg["aspect"]}!important;
    object-fit:contain!important;
    background:#000!important;
}}
</style>
"""


# =========================================================
# PREVIEW UPLOAD
# =========================================================

def preview_uploaded_video(video_file):

    if not video_file:
        return None

    return video_file


def load_link_preview(link):

    if not link:
        return None

    return download_video_from_link(
        link
    )


# =========================================================
# RECAP PROMPT
# =========================================================

def build_recap_prompt(
    selected_ratio
):

    return f"""
You are a Professional Movie Recap Creator.

Analyze the supplied video from beginning
to end.

Target aspect ratio:
{selected_ratio}

Write a natural Burmese Movie Recap Script.

IMPORTANT:

Do NOT invent events.

Do NOT invent character names.

Do NOT invent relationships.

Do NOT invent dialogue.

Do NOT invent locations.

Only describe things that can actually be
seen or heard in the video.

The style must NOT be narrator-only.

Use a mixture of:

VISUAL ACTION
+
NARRATOR DESCRIPTION
+
ACTUAL CHARACTER DIALOGUE
+
CHARACTER REACTION
+
NEXT ACTION

Example style:

ကောင်လေးက အခန်းထဲကို ဖြည်းဖြည်းဝင်လာပြီး
မိန်းကလေးရှေ့မှာ ရပ်လိုက်ပါတယ်။

နင် ဒီကို ဘာလာလုပ်တာလဲ?

ငါ မင်းကို ပြောစရာရှိလို့။

သူတို့နှစ်ယောက် စကားပြောနေတုန်းမှာပဲ
အပြင်ဘက်ကနေ အသံတစ်ခု ထွက်လာပါတယ်။

Dialogue must be translated naturally
into Burmese when the original dialogue
is English, Chinese, Japanese, Korean,
Thai or another language.

Do not create dialogue if it is unclear.

Describe visible action instead.

Use natural spoken Burmese.

Avoid generic phrases such as:

တစ်နေ့မှာ...
ဒီဇာတ်ကားမှာတော့...
နောက်ဆုံးမှာ...
အဓိကဇာတ်ကောင်က...

Keep the story engaging.

Include the complete ending.

Make the script TTS friendly.

Do not output technical labels.

Return only the final Burmese recap script.
"""


# =========================================================
# ERROR DETECTION
# =========================================================

def is_retryable_error(error):

    text = str(error).lower()

    words = [
        "503",
        "unavailable",
        "high demand",
        "service unavailable",
        "429",
        "resource_exhausted",
        "500",
        "internal server error",
        "504",
        "deadline_exceeded",
        "timeout",
        "temporarily",
    ]

    return any(
        x in text
        for x in words
    )


# =========================================================
# GEMINI GENERATE
# =========================================================

def generate_with_retry(
    client,
    uploaded_file,
    prompt
):

    retry_delays = [
        5,
        10,
        20,
        40,
    ]

    last_error = None

    for model_index, model_name in enumerate(
        GEMINI_MODELS
    ):

        print(
            f"Trying model: {model_name}"
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
                        ],
                    )
                )

                if response and response.text:
                    return (
                        response.text,
                        model_name
                    )

                raise RuntimeError(
                    "Empty Gemini response"
                )

            except Exception as e:

                last_error = e

                if not is_retryable_error(e):
                    break

                if attempt < len(retry_delays):

                    delay = (
                        retry_delays[attempt]
                    )

                    delay += random.uniform(
                        0,
                        2
                    )

                    print(
                        f"Retrying in {delay:.1f}s"
                    )

                    time.sleep(delay)

        if model_index < len(
            GEMINI_MODELS
        ) - 1:

            time.sleep(2)

    raise RuntimeError(
        f"All Gemini models failed.\n{last_error}"
    )


# =========================================================
# MAIN ANALYSIS
# =========================================================

def analyze_and_generate_script(
    video_file,
    video_link,
    ratio_choice
):

    global SAVED_API_KEY

    if not SAVED_API_KEY:

        return (
            "",
            "⚠️ Gemini API Key မရှိသေးပါ။",
            None,
            None,
        )

    target_media = resolve_video(
        video_file,
        video_link
    )

    if not target_media:

        return (
            "",
            "⚠️ Video File သို့မဟုတ် URL ထည့်ပါ။",
            None,
            None,
        )

    if not os.path.exists(
        target_media
    ):

        return (
            "",
            "⚠️ Video File ရှာမတွေ့ပါ။",
            None,
            None,
        )

    valid, duration_message = (
        validate_video_duration(
            target_media
        )
    )

    if not valid:

        return (
            "",
            duration_message,
            None,
            None,
        )

    try:

        client = genai.Client(
            api_key=SAVED_API_KEY
        )

    except Exception as e:

        return (
            "",
            f"⚠️ Gemini Client Error:\n{e}",
            None,
            None,
        )

    try:

        uploaded_file = client.files.upload(
            file=target_media
        )

    except Exception as e:

        return (
            "",
            f"⚠️ Video Upload Error:\n{e}",
            None,
            None,
        )

    try:

        start = time.time()

        while True:

            state_name = (
                uploaded_file.state.name
                if uploaded_file.state
                else ""
            )

            print(
                "Gemini State:",
                state_name
            )

            if state_name == "ACTIVE":
                break

            if state_name == "FAILED":

                return (
                    "",
                    "⚠️ Gemini Video Processing Failed.",
                    None,
                    None,
                )

            if time.time() - start > 900:

                return (
                    "",
                    "⚠️ Video Processing Timeout.",
                    None,
                    None,
                )

            time.sleep(5)

            uploaded_file = client.files.get(
                name=uploaded_file.name
            )

    except Exception as e:

        return (
            "",
            f"⚠️ Processing Error:\n{e}",
            None,
            None,
        )

    try:

        prompt = build_recap_prompt(
            ratio_choice
        )

        script_text, used_model = (
            generate_with_retry(
                client,
                uploaded_file,
                prompt
            )
        )

        clean_text = (
            clean_script_for_tts(
                script_text
            )
        )

        if not clean_text:

            return (
                "",
                "⚠️ Script မရပါ။",
                None,
                None,
            )

        srt_file, zip_file = (
            generate_srt_and_zip(
                clean_text
            )
        )

        status = (
            "## ✅ Script ပြီးပါပြီ\n\n"
            f"{duration_message}\n\n"
            f"🤖 Model: `{used_model}`\n\n"
            "🎙️ Narrator + Dialogue + "
            "Visual Storytelling"
        )

        return (
            clean_text,
            status,
            srt_file,
            zip_file
        )

    except Exception as e:

        return (
            "",
            f"⚠️ Script Error:\n{e}",
            None,
            None
        )


# =========================================================
# TTS
# =========================================================

async def generate_myanmar_tts(
    text,
    voice_choice,
    speed_percent
):

    if not text or not text.strip():

        return (
            None,
            None,
            None,
            None
        )

    clean_text = (
        clean_script_for_tts(
            text
        )
    )

    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )

    rate_str = (
        f"{int(speed_percent):+d}%"
    )

    output_filename = unique_file(
        "recap_voice",
        ".mp3"
    )

    try:

        communicate = edge_tts.Communicate(
            clean_text,
            selected_voice,
            rate=rate_str
        )

        await communicate.save(
            output_filename
        )

        srt_file, zip_file = (
            generate_srt_and_zip(
                clean_text
            )
        )

        return (
            output_filename,
            output_filename,
            srt_file,
            zip_file
        )

    except Exception as e:

        print("TTS Error:", e)

        return (
            None,
            None,
            None,
            None
        )


def tts_interface(
    text,
    voice_choice,
    speed
):

    try:

        return asyncio.run(
            generate_myanmar_tts(
                text,
                voice_choice,
                speed
            )
        )

    except Exception as e:

        print(
            "TTS Interface Error:",
            e
        )

        return (
            None,
            None,
            None,
            None
        )


# =========================================================
# SUBTITLE TRANSLATION
# =========================================================

def translate_script(
    text,
    language
):

    if not text:
        return ""

    if language == "Myanmar":
        return text

    global SAVED_API_KEY

    if not SAVED_API_KEY:
        return text

    language_name = {
        "English": "English",
        "Thai": "Thai"
    }.get(
        language,
        "Myanmar"
    )

    try:

        client = genai.Client(
            api_key=SAVED_API_KEY
        )

        prompt = f"""
Translate the following movie recap script
into natural {language_name}.

Keep the same meaning.
Do not add information.
Keep dialogue natural.
Return only translated text.

SCRIPT:

{text}
"""

        response = client.models.generate_content(
            model=GEMINI_MODELS[0],
            contents=prompt
        )

        if response and response.text:
            return response.text

    except Exception as e:

        print(
            "Subtitle Translation Error:",
            e
        )

    return text


# =========================================================
# FONT / COLOR HELPERS
# =========================================================

COLOR_MAP = {

    "White": "white",
    "Black": "black",
    "Red": "red",
    "Yellow": "yellow",
    "Green": "green",
    "Blue": "blue",
    "Orange": "orange",
    "Pink": "pink",
    "Purple": "purple",
    "Cyan": "cyan",
}


def escape_drawtext(text):

    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def find_font():

    candidates = [

        "/usr/share/fonts/truetype/noto/"
        "NotoSansMyanmar-Regular.ttf",

        "/usr/share/fonts/truetype/"
        "noto/NotoSansMyanmar-Regular.ttf",

        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans.ttf",

        "/usr/share/fonts/truetype/liberation2/"
        "LiberationSans-Regular.ttf",
    ]

    for path in candidates:

        if os.path.exists(path):
            return path

    return None


# =========================================================
# CREATE SUBTITLE FILE
# =========================================================

def make_subtitle_file(
    script,
    language
):

    translated = translate_script(
        script,
        language
    )

    srt_file, zip_file = (
        generate_srt_and_zip(
            translated
        )
    )

    return srt_file


# =========================================================
# BUILD VIDEO FILTER
# =========================================================

def build_video_filter(
    ratio,
    zoom,
    brightness,
    flip,
    blur_enabled,
    blur_x,
    blur_y,
    blur_w,
    blur_h,
    blur_strength,
    logo_enabled,
    logo_x,
    logo_y,
    logo_scale
):

    filters = []

    # -----------------------------------------
    # SCALE / CROP
    # -----------------------------------------

    rw, rh = ratio_values(
        ratio
    )

    filters.append(
        f"scale=iw*{zoom}:ih*{zoom}:"
        "force_original_aspect_ratio=increase"
    )

    filters.append(
        f"crop="
        f"min(iw,iw):min(ih,ih)"
    )

    filters.append(
        f"scale={rw*720//rh if rw < rh else 720}:"
        f"{720 if rw < rh else rh*720//rw}"
    )

    # -----------------------------------------
    # FLIP
    # -----------------------------------------

    if flip:
        filters.append(
            "hflip"
        )

    # -----------------------------------------
    # BRIGHTNESS
    # -----------------------------------------

    if abs(brightness) > 0.01:

        filters.append(
            f"eq=brightness={brightness:.3f}"
        )

    return filters


# =========================================================
# FINAL VIDEO RENDER
# =========================================================

def render_video(
    video_file,
    video_url,
    recap_script,
    ratio,
    trim_start,
    trim_end,
    zoom,
    brightness,
    flip,
    original_audio,
    original_volume,
    bg_music,
    music_volume,
    logo_file,
    logo_enabled,
    logo_x,
    logo_y,
    logo_scale,
    blur_enabled,
    blur_x,
    blur_y,
    blur_w,
    blur_h,
    blur_strength,
    subtitle_enabled,
    subtitle_language,
    subtitle_size,
    subtitle_text_color,
    subtitle_outline_color,
    subtitle_outline,
    subtitle_x,
    subtitle_y,
    subtitle_font
):

    source = resolve_video(
        video_file,
        video_url
    )

    if not source:
        return (
            None,
            "⚠️ Video မရှိပါ။"
        )

    if not os.path.exists(source):
        return (
            None,
            "⚠️ Video File မတွေ့ပါ။"
        )

    valid, duration_message = (
        validate_video_duration(
            source
        )
    )

    if not valid:
        return (
            None,
            duration_message
        )

    duration = get_video_duration(
        source
    )

    start = max(
        0,
        safe_float(trim_start)
    )

    end = safe_float(
        trim_end,
        0
    )

    if end <= 0:
        end = duration or 999999

    if duration:
        end = min(
            end,
            duration
        )

    if end <= start:
        return (
            None,
            "⚠️ Trim Start/End မမှန်ပါ။"
        )

    output = unique_file(
        "final_video",
        ".mp4"
    )

    try:

        # =====================================
        # Prepare subtitle
        # =====================================

        subtitle_file = None

        if (
            subtitle_enabled
            and recap_script
        ):

            subtitle_file = (
                make_subtitle_file(
                    recap_script,
                    subtitle_language
                )
            )

        # =====================================
        # Build filter graph
        # =====================================

        rw, rh = ratio_values(
            ratio
        )

        target_h = 720
        target_w = int(
            target_h * rw / rh
        )

        if target_w % 2:
            target_w += 1

        filter_parts = []

        base_filters = [
            f"scale="
            f"{target_w}:"
            f"{target_h}:"
            "force_original_aspect_ratio=increase",

            f"crop={target_w}:{target_h}",

            f"setsar=1",
        ]

        if zoom > 1:
            base_filters.append(
                f"scale="
                f"{int(target_w * zoom)}:"
                f"{int(target_h * zoom)}"
            )

            base_filters.append(
                f"crop={target_w}:{target_h}"
            )

        if flip:
            base_filters.append(
                "hflip"
            )

        if abs(brightness) > 0.001:
            base_filters.append(
                f"eq=brightness={brightness:.3f}"
            )

        filter_parts.append(
            "[0:v]"
            +
            ",".join(base_filters)
            +
            "[base]"
        )

        current_video = "base"

        # =====================================
        # Blur
        # =====================================

        if blur_enabled:

            bx = int(
                target_w *
                safe_float(blur_x) /
                100
            )

            by = int(
                target_h *
                safe_float(blur_y) /
                100
            )

            bw = int(
                target_w *
                safe_float(blur_w) /
                100
            )

            bh = int(
                target_h *
                safe_float(blur_h) /
                100
            )

            bw = max(
                10,
                min(
                    bw,
                    target_w
                )
            )

            bh = max(
                10,
                min(
                    bh,
                    target_h
                )
            )

            blur_strength = max(
                1,
                int(
                    safe_float(
                        blur_strength,
                        10
                    )
                )
            )

            filter_parts.append(
                f"[{current_video}]"
                f"split=2[blurbase][blursource]"
            )

            filter_parts.append(
                "[blurbase]"
                f"boxblur={blur_strength}:"
                f"{blur_strength}[blurred]"
            )

            filter_parts.append(
                f"[blursource]"
                f"crop={bw}:{bh}:{bx}:{by},"
                f"boxblur={blur_strength}:"
                f"{blur_strength},"
                f"pad={target_w}:{target_h}:"
                f"{bx}:{by}:color=black@0"
                f"[blurpatch]"
            )

            filter_parts.append(
                "[blurbase][blurpatch]"
                "overlay=0:0[blurout]"
            )

            current_video = "blurout"

        # =====================================
        # Logo
        # =====================================

        if (
            logo_enabled
            and logo_file
            and os.path.exists(logo_file)
        ):

            logo_size = max(
                5,
                min(
                    100,
                    safe_float(
                        logo_scale,
                        20
                    )
                )
            )

            lx = int(
                target_w *
                safe_float(logo_x) /
                100
            )

            ly = int(
                target_h *
                safe_float(logo_y) /
                100
            )

            filter_parts.append(
                f"[1:v]"
                f"scale="
                f"-1:{int(target_h * logo_size / 100)}"
                "[logo]"
            )

            filter_parts.append(
                f"[{current_video}][logo]"
                f"overlay={lx}:{ly}"
                "[logoout]"
            )

            current_video = "logoout"

        # =====================================
        # Subtitle
        # =====================================

        if (
            subtitle_enabled
            and subtitle_file
            and os.path.exists(
                subtitle_file
            )
        ):

            font_size = max(
                12,
                int(
                    safe_float(
                        subtitle_size,
                        32
                    )
                )
            )

            tx = int(
                target_w *
                safe_float(
                    subtitle_x,
                    50
                ) /
                100
            )

            ty = int(
                target_h *
                safe_float(
                    subtitle_y,
                    85
                ) /
                100
            )

            text_color = COLOR_MAP.get(
                subtitle_text_color,
                "white"
            )

            outline_color = COLOR_MAP.get(
                subtitle_outline_color,
                "black"
            )

            # ffmpeg subtitles filter
            subtitle_filter = (
                f"subtitles="
                f"'{subtitle_file}':"
                f"force_style="
                f"'FontName={subtitle_font},"
                f"FontSize={font_size},"
                f"PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,"
                f"Outline={int(subtitle_outline)},"
                f"Alignment=2,"
                f"MarginV={max(5, target_h-ty)}'"
            )

            filter_parts.append(
                f"[{current_video}]"
                f"{subtitle_filter}"
                "[subout]"
            )

            current_video = "subout"

        # =====================================
        # Audio
        # =====================================

        audio_parts = []

        input_count = 1

        # Logo is input 1
        has_logo = (
            logo_enabled
            and logo_file
            and os.path.exists(logo_file)
        )

        if has_logo:
            input_count = 2

        has_music = (
            bg_music
            and os.path.exists(bg_music)
        )

        music_index = None

        if has_music:
            music_index = input_count
            input_count += 1

        has_voice = False

        # =====================================
        # We create voice from script
        # =====================================

        voice_file = None

        if recap_script:

            try:

                voice_file, _, _, _ = (
                    tts_interface(
                        recap_script,
                        "Thiha (အမျိုးသားအသံ) - Natural",
                        5
                    )
                )

                if voice_file:
                    has_voice = True

            except Exception as e:

                print(
                    "Voice generation error:",
                    e
                )

        voice_index = None

        if has_voice:

            voice_index = input_count
            input_count += 1

        # =====================================
        # Original audio
        # =====================================

        audio_labels = []

        if original_audio:

            audio_parts.append(
                f"[0:a]"
                f"volume="
                f"{max(0, original_volume)/100:.3f}"
                "[orig]"
            )

            audio_labels.append(
                "[orig]"
            )

        # =====================================
        # Music
        # =====================================

        if has_music:

            filter_parts.append(
                f"[{music_index}:a]"
                f"volume="
                f"{max(0, music_volume)/100:.3f}"
                "[music]"
            )

            audio_labels.append(
                "[music]"
            )

        # =====================================
        # Voice
        # =====================================

        if has_voice:

            filter_parts.append(
                f"[{voice_index}:a]"
                f"volume=1.0"
                "[voice]"
            )

            audio_labels.append(
                "[voice]"
            )

        if len(audio_labels) == 1:

            audio_map = audio_labels[0]

        elif len(audio_labels) > 1:

            filter_parts.append(
                "".join(audio_labels)
                +
                f"amix=inputs="
                f"{len(audio_labels)}:"
                "duration=longest:"
                "dropout_transition=2"
                "[mixed]"
            )

            audio_map = "[mixed]"

        else:

            audio_map = "0:a?"

        # =====================================
        # Command
        # =====================================

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            source,
        ]

        if has_logo:

            cmd += [
                "-i",
                logo_file
            ]

        if has_music:

            cmd += [
                "-stream_loop",
                "-1",
                "-i",
                bg_music
            ]

        if has_voice:

            cmd += [
                "-i",
                voice_file
            ]

        filter_complex = ";".join(
            filter_parts
        )

        cmd += [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{current_video}]",
            "-map",
            audio_map,
            "-t",
            str(max(1, end-start)),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
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
            output
        ]

        run_cmd(
            cmd,
            timeout=1800
        )

        return (
            output,
            (
                "## ✅ Video Render ပြီးပါပြီ\n\n"
                f"{duration_message}\n\n"
                f"📐 Ratio: {ratio}\n\n"
                "🎙️ Voice + 🎵 Music + "
                "📝 Subtitle + 🎬 Effects "
                "ထည့်သွင်းပြီးပါပြီ။"
            )
        )

    except Exception as e:

        print(
            "Render Error:",
            e
        )

        return (
            None,
            (
                "❌ Render Error\n\n"
                f"{str(e)}"
            )
        )


# =========================================================
# PREVIEW RENDER
# =========================================================

def preview_render(
    video_file,
    video_url,
    ratio,
    zoom,
    brightness,
    flip,
    original_audio,
    original_volume,
    bg_music,
    music_volume,
    logo_file,
    logo_enabled,
    logo_x,
    logo_y,
    logo_scale,
    blur_enabled,
    blur_x,
    blur_y,
    blur_w,
    blur_h,
    blur_strength,
    subtitle_enabled,
    subtitle_language,
    subtitle_size,
    subtitle_text_color,
    subtitle_outline_color,
    subtitle_outline,
    subtitle_x,
    subtitle_y,
    subtitle_font,
    trim_start,
    trim_end
):

    # Preview intentionally uses
    # a blank recap script.
    # This allows fast visual preview.

    result, status = render_video(
        video_file,
        video_url,
        "",
        ratio,
        trim_start,
        trim_end,
        zoom,
        brightness,
        flip,
        original_audio,
        original_volume,
        bg_music,
        music_volume,
        logo_file,
        logo_enabled,
        logo_x,
        logo_y,
        logo_scale,
        blur_enabled,
        blur_x,
        blur_y,
        blur_w,
        blur_h,
        blur_strength,
        False,
        subtitle_language,
        subtitle_size,
        subtitle_text_color,
        subtitle_outline_color,
        subtitle_outline,
        subtitle_x,
        subtitle_y,
        subtitle_font
    )

    return (
        result,
        status
    )


# =========================================================
# AUTO SEND SCRIPT TO TTS
# =========================================================

def send_script_to_editor(
    script
):

    return script or ""


# =========================================================
# FULL UI
# =========================================================

with gr.Blocks(
    title=APP_TITLE,
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown(
        """
# 🎬 AI Movie Recap Studio Pro

### Video → AI Recap → Burmese Voice → Video Edit → Final MP4

**Narrator + Character Dialogue + Visual Storytelling**
"""
    )

    # =====================================================
    # API TAB
    # =====================================================

    with gr.Tab(
        "🔑 API Key"
    ):

        gr.Markdown(
            """
### Gemini API Key

Render Environment Variables ထဲမှာ
`GEMINI_API_KEY` ထည့်ထားရင် အလိုအလျောက်သုံးနိုင်ပါတယ်။
မထည့်ထားရင် အောက်မှာထည့်ပြီး Save လုပ်နိုင်ပါတယ်။
"""
        )

        api_key_input = gr.Textbox(
            label="Gemini API Key",
            type="password",
            placeholder="AIzaSy..."
        )

        save_key_btn = gr.Button(
            "💾 Save API Key",
            variant="primary"
        )

        key_status = gr.Markdown("")

    # =====================================================
    # TAB 1
    # =====================================================

    with gr.Tab(
        "1️⃣ Video Analysis & Script"
    ):

        with gr.Row():

            with gr.Column():

                video_file = gr.Video(
                    label="📹 Video File"
                )

                video_url = gr.Textbox(
                    label="🔗 Video URL",
                    placeholder=(
                        "YouTube / Facebook / TikTok / "
                        "RedNote / Other URL"
                    )
                )

                load_link_btn = gr.Button(
                    "🔍 Link Video ရယူမည်"
                )

                ratio_picker = gr.Radio(
                    choices=[
                        "9:16",
                        "3:4",
                        "1:1",
                        "16:9"
                    ],
                    value="9:16",
                    label="📐 Aspect Ratio"
                )

                gen_script_btn = gr.Button(
                    "🚀 Movie Recap Script ထုတ်မည်",
                    variant="primary"
                )

            with gr.Column():

                ratio_css = gr.HTML(
                    get_ratio_css("9:16")
                )

                tab1_preview = gr.Video(
                    label="📺 Video Preview"
                )

                script_status = gr.Markdown(
                    "Video တင်ပြီး Script ထုတ်နိုင်ပါပြီ။"
                )

                script_display = gr.Textbox(
                    label="🎬 Generated Burmese Recap Script",
                    lines=18
                )

                with gr.Row():

                    srt_download_tab1 = gr.File(
                        label="📄 SRT"
                    )

                    zip_download_tab1 = gr.File(
                        label="📦 SRT ZIP"
                    )

    # =====================================================
    # TAB 2
    # =====================================================

    with gr.Tab(
        "2️⃣ Text-to-Speech"
    ):

        with gr.Row():

            with gr.Column():

                input_text = gr.Textbox(
                    label="🎙️ Burmese Recap Script",
                    lines=18,
                    placeholder=(
                        "Tab 1 က Script "
                        "ဒီနေရာကို အလိုအလျောက်ရောက်လာပါမယ်။"
                    )
                )

                voice_dropdown = gr.Dropdown(
                    choices=list(
                        VOICES.keys()
                    ),
                    value=(
                        "Thiha "
                        "(အမျိုးသားအသံ) - Natural"
                    ),
                    label="🎤 Burmese Voice"
                )

                speed_slider = gr.Slider(
                    minimum=-30,
                    maximum=50,
                    value=5,
                    step=1,
                    label="⚡ Voice Speed (%)"
                )

                gen_voice_btn = gr.Button(
                    "⚡ Voice ထုတ်မည်",
                    variant="primary"
                )

            with gr.Column():

                audio_output = gr.Audio(
                    type="filepath",
                    autoplay=True,
                    label="🔊 Voice Preview"
                )

                mp3_download = gr.File(
                    label="🎵 MP3 Download"
                )

                with gr.Row():

                    srt_download_tab2 = gr.File(
                        label="📄 SRT"
                    )

                    zip_download_tab2 = gr.File(
                        label="📦 SRT ZIP"
                    )

    # =====================================================
    # TAB 3
    # =====================================================

    with gr.Tab(
        "3️⃣ Video Edit"
    ):

        gr.Markdown(
            """
# 🎬 Video Edit

Video ကို upload လုပ်ပါ သို့မဟုတ် Link ထည့်ပါ။
အောက်က Setting တွေပြောင်းပြီး **Preview Render**
လုပ်ကြည့်ပြီးမှ Final Video ဖန်တီးနိုင်ပါတယ်။
"""
        )

        # -------------------------------------------------
        # INPUT
        # -------------------------------------------------

        with gr.Row():

            with gr.Column():

                edit_video_file = gr.Video(
                    label="📹 Video File"
                )

                edit_video_url = gr.Textbox(
                    label="🔗 YouTube / Facebook / TikTok / RedNote / Other URL"
                )

                edit_load_btn = gr.Button(
                    "🔍 Link Video ရယူမည်"
                )

                edit_ratio = gr.Radio(
                    choices=[
                        "9:16",
                        "3:4",
                        "1:1",
                        "16:9"
                    ],
                    value="9:16",
                    label="📐 Video Ratio"
                )

            with gr.Column():

                edit_preview = gr.Video(
                    label="👀 Live Preview / Render Preview",
                    elem_id="edit_preview_container"
                )

                edit_status = gr.Markdown(
                    "Video Edit Preview အဆင်သင့်ပါပြီ။"
                )

        # -------------------------------------------------
        # TRIM
        # -------------------------------------------------

        with gr.Accordion(
            "✂️ Trim / Cut",
            open=True
        ):

            with gr.Row():

                trim_start = gr.Number(
                    value=0,
                    label="Start Time (seconds)"
                )

                trim_end = gr.Number(
                    value=0,
                    label=(
                        "End Time (0 = Video အဆုံး)"
                    )
                )

        # -------------------------------------------------
        # VIDEO EFFECTS
        # -------------------------------------------------

        with gr.Accordion(
            "🎬 Video Effects",
            open=True
        ):

            with gr.Row():

                zoom_slider = gr.Slider(
                    minimum=1,
                    maximum=2,
                    value=1,
                    step=0.01,
                    label="🔍 Zoom"
                )

                brightness_slider = gr.Slider(
                    minimum=-1,
                    maximum=1,
                    value=0,
                    step=0.05,
                    label="☀️ Brightness"
                )

                flip_checkbox = gr.Checkbox(
                    label="↔️ Flip Left / Right",
                    value=False
                )

        # -------------------------------------------------
        # AUDIO
        # -------------------------------------------------

        with gr.Accordion(
            "🔊 Audio",
            open=True
        ):

            with gr.Row():

                original_audio = gr.Checkbox(
                    label="🎙️ Original Audio ON/OFF",
                    value=False
                )

                original_volume = gr.Slider(
                    minimum=0,
                    maximum=200,
                    value=30,
                    step=1,
                    label="Original Audio Volume %"
                )

            bg_music = gr.Audio(
                type="filepath",
                label="🎵 Background Music"
            )

            music_volume = gr.Slider(
                minimum=0,
                maximum=200,
                value=15,
                step=1,
                label="Background Music Volume %"
            )

        # -------------------------------------------------
        # LOGO
        # -------------------------------------------------

        with gr.Accordion(
            "🏷️ Logo",
            open=False
        ):

            with gr.Row():

                logo_file = gr.Image(
                    type="filepath",
                    label="Logo PNG/JPG"
                )

                logo_enabled = gr.Checkbox(
                    label="Logo ON",
                    value=False
                )

            with gr.Row():

                logo_x = gr.Slider(
                    minimum=0,
                    maximum=100,
                    value=85,
                    step=1,
                    label="Logo Left / Right"
                )

                logo_y = gr.Slider(
                    minimum=0,
                    maximum=100,
                    value=10,
                    step=1,
                    label="Logo Top / Bottom"
                )

                logo_scale = gr.Slider(
                    minimum=5,
                    maximum=50,
                    value=20,
                    step=1,
                    label="Logo Size %"
                )

        # -------------------------------------------------
        # BLUR
        # -------------------------------------------------

        with gr.Accordion(
            "🌫️ Blur Area",
            open=False
        ):

            blur_enabled = gr.Checkbox(
                label="Blur ON",
                value=False
            )

            with gr.Row():

                blur_x = gr.Slider(
                    minimum=0,
                    maximum=100,
                    value=10,
                    step=1,
                    label="Blur X / Left"
                )

                blur_y = gr.Slider(
                    minimum=0,
                    maximum=100,
                    value=10,
                    step=1,
                    label="Blur Y / Top"
                )

            with gr.Row():

                blur_w = gr.Slider(
                    minimum=1,
                    maximum=100,
                    value=30,
                    step=1,
                    label="Blur Width"
                )

                blur_h = gr.Slider(
                    minimum=1,
                    maximum=100,
                    value=20,
                    step=1,
                    label="Blur Height"
                )

                blur_strength = gr.Slider(
                    minimum=1,
                    maximum=40,
                    value=10,
                    step=1,
                    label="Blur Strength"
                )

        # -------------------------------------------------
        # SUBTITLE
        # -------------------------------------------------

        with gr.Accordion(
            "📝 Subtitle",
            open=True
        ):

            subtitle_enabled = gr.Checkbox(
                label="Subtitle ON",
                value=True
            )

            with gr.Row():

                subtitle_language = gr.Dropdown(
                    choices=[
                        "Myanmar",
                        "English",
                        "Thai"
                    ],
                    value="Myanmar",
                    label="Subtitle Language"
                )

                subtitle_font = gr.Dropdown(
                    choices=[
                        "Noto Sans Myanmar",
                        "Noto Sans",
                        "Arial",
                        "DejaVu Sans",
                        "Liberation Sans"
                    ],
                    value="Noto Sans Myanmar",
                    label="Font"
                )

            with gr.Row():

                subtitle_size = gr.Slider(
                    minimum=12,
                    maximum=80,
                    value=32,
                    step=1,
                    label="Font Size"
                )

                subtitle_outline = gr.Slider(
                    minimum=0,
                    maximum=10,
                    value=3,
                    step=1,
                    label="Outline Size"
                )

            with gr.Row():

                subtitle_text_color = gr.Dropdown(
                    choices=list(
                        COLOR_MAP.keys()
                    ),
                    value="White",
                    label="Text Color"
                )

                subtitle_outline_color = gr.Dropdown(
                    choices=list(
                        COLOR_MAP.keys()
                    ),
                    value="Black",
                    label="Outline Color"
                )

            with gr.Row():

                subtitle_x = gr.Slider(
                    minimum=0,
                    maximum=100,
                    value=50,
                    step=1,
                    label="Subtitle Left / Right"
                )

                subtitle_y = gr.Slider(
                    minimum=0,
                    maximum=100,
                    value=85,
                    step=1,
                    label="Subtitle Top / Bottom"
                )

        # -------------------------------------------------
        # SCRIPT
        # -------------------------------------------------

        with gr.Accordion(
            "🎙️ Burmese Voice Script",
            open=True
        ):

            edit_script = gr.Textbox(
                label="Recap Script",
                lines=12,
                placeholder=(
                    "Tab 1 မှ Script အလိုအလျောက် "
                    "ဒီနေရာကို ရောက်လာပါမည်။"
                )
            )

            edit_voice = gr.Dropdown(
                choices=list(
                    VOICES.keys()
                ),
                value=(
                    "Thiha "
                    "(အမျိုးသားအသံ) - Natural"
                ),
                label="Video Voice"
            )

            edit_voice_speed = gr.Slider(
                minimum=-30,
                maximum=50,
                value=5,
                step=1,
                label="Voice Speed"
            )

            edit_voice_preview = gr.Audio(
                type="filepath",
                autoplay=True,
                label="🎧 Voice Preview"
            )

            edit_voice_download = gr.File(
                label="🎵 MP3 Download"
            )

            generate_edit_voice_btn = gr.Button(
                "🎙️ Voice Generate",
                variant="secondary"
            )

        # -------------------------------------------------
        # PREVIEW BUTTON
        # -------------------------------------------------

        preview_button = gr.Button(
            "👀 PREVIEW VIDEO",
            variant="secondary",
            size="lg"
        )

        # -------------------------------------------------
        # FINAL
        # -------------------------------------------------

        gr.Markdown(
            """
## 🚀 Final Video

အပေါ်က Preview ကို စစ်ပြီး အဆင်ပြေမှ
**CREATE FINAL VIDEO** ကိုနှိပ်ပါ။
"""
        )

        final_button = gr.Button(
            "🎬 CREATE FINAL VIDEO",
            variant="primary",
            size="lg"
        )

        final_video = gr.Video(
            label="🎥 Final Video Preview"
        )

        final_status = gr.Markdown("")

        final_download = gr.File(
            label="⬇️ DOWNLOAD FINAL MP4"
        )

    # =====================================================
    # EVENTS
    # =====================================================

    # API
    save_key_btn.click(
        fn=save_api_key,
        inputs=api_key_input,
        outputs=key_status
    )

    # TAB 1
    ratio_picker.change(
        fn=get_ratio_css,
        inputs=ratio_picker,
        outputs=ratio_css
    )

    video_file.change(
        fn=preview_uploaded_video,
        inputs=video_file,
        outputs=tab1_preview
    )

    load_link_btn.click(
        fn=load_link_preview,
        inputs=video_url,
        outputs=tab1_preview
    )

    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[
            video_file,
            video_url,
            ratio_picker
        ],
        outputs=[
            script_display,
            script_status,
            srt_download_tab1,
            zip_download_tab1
        ]
    )

    # Script automatically to TTS
    gen_script_btn.click(
        fn=send_script_to_editor,
        inputs=script_display,
        outputs=input_text
    )

    # Script automatically to Video Edit
    gen_script_btn.click(
        fn=send_script_to_editor,
        inputs=script_display,
        outputs=edit_script
    )

    # TTS
    gen_voice_btn.click(
        fn=tts_interface,
        inputs=[
            input_text,
            voice_dropdown,
            speed_slider
        ],
        outputs=[
            audio_output,
            mp3_download,
            srt_download_tab2,
            zip_download_tab2
        ]
    )

    # TAB 3 URL
    edit_load_btn.click(
        fn=load_link_preview,
        inputs=edit_video_url,
        outputs=edit_preview
    )

    edit_video_file.change(
        fn=preview_uploaded_video,
        inputs=edit_video_file,
        outputs=edit_preview
    )

    # Auto script
    gen_script_btn.click(
        fn=send_script_to_editor,
        inputs=script_display,
        outputs=edit_script
    )

    # Edit voice
    generate_edit_voice_btn.click(
        fn=tts_interface,
        inputs=[
            edit_script,
            edit_voice,
            edit_voice_speed
        ],
        outputs=[
            edit_voice_preview,
            edit_voice_download,
            srt_download_tab2,
            zip_download_tab2
        ]
    )

    # Preview
    preview_button.click(
        fn=preview_render,
        inputs=[
            edit_video_file,
            edit_video_url,
            edit_ratio,
            zoom_slider,
            brightness_slider,
            flip_checkbox,
            original_audio,
            original_volume,
            bg_music,
            music_volume,
            logo_file,
            logo_enabled,
            logo_x,
            logo_y,
            logo_scale,
            blur_enabled,
            blur_x,
            blur_y,
            blur_w,
            blur_h,
            blur_strength,
            subtitle_enabled,
            subtitle_language,
            subtitle_size,
            subtitle_text_color,
            subtitle_outline_color,
            subtitle_outline,
            subtitle_x,
            subtitle_y,
            subtitle_font,
            trim_start,
            trim_end
        ],
        outputs=[
            edit_preview,
            edit_status
        ]
    )

    # Final Render
    final_button.click(
        fn=render_video,
        inputs=[
            edit_video_file,
            edit_video_url,
            edit_script,
            edit_ratio,
            trim_start,
            trim_end,
            zoom_slider,
            brightness_slider,
            flip_checkbox,
            original_audio,
            original_volume,
            bg_music,
            music_volume,
            logo_file,
            logo_enabled,
            logo_x,
            logo_y,
            logo_scale,
            blur_enabled,
            blur_x,
            blur_y,
            blur_w,
            blur_h,
            blur_strength,
            subtitle_enabled,
            subtitle_language,
            subtitle_size,
            subtitle_text_color,
            subtitle_outline_color,
            subtitle_outline,
            subtitle_x,
            subtitle_y,
            subtitle_font
        ],
        outputs=[
            final_video,
            final_status
        ]
    )

    final_video.change(
        fn=lambda x: x,
        inputs=final_video,
        outputs=final_download
    )


# =========================================================
# LAUNCH
# =========================================================

if __name__ == "__main__":

    if not ffmpeg_exists():
        print(
            "WARNING: FFmpeg မတွေ့ပါ။ "
            "Render Environment မှာ FFmpeg install လုပ်ပါ။"
        )

    demo.launch(
        server_name="0.0.0.0",
        server_port=PORT,
        show_error=True
    )
