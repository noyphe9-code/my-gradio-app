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
from google.genai import types


# =========================================================
# AI MOVIE RECAP STUDIO PRO
# OPTIMIZED ALL-IN-ONE VERSION
#
# VIDEO
#   ↓
# GEMINI VIDEO ANALYSIS
#   ↓
# BURMESE RECAP SCRIPT
#   ↓
# BURMESE TTS
#   ↓
# VIDEO EDIT
#   ↓
# SUBTITLE / LOGO / BLUR / AUDIO
#   ↓
# EXACT RATIO PREVIEW
#   ↓
# FINAL MP4
# =========================================================


APP_TITLE = "AI Movie Recap Studio Pro"

MAX_VIDEO_MINUTES = 5

PORT = int(
    os.environ.get(
        "PORT",
        "7860"
    )
)

# Render Environment Variable
SAVED_API_KEY = os.environ.get(
    "GEMINI_API_KEY",
    ""
)


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

    "Thiha (အမျိုးသားအသံ) - Natural":
        "my-MM-ThihaNeural",

    "Nilar (အမျိုးသမီးအသံ) - Natural":
        "my-MM-NilarNeural",
}


# =========================================================
# WORKSPACE
# =========================================================

BASE_DIR = Path(
    "studio_workspace"
)

BASE_DIR.mkdir(
    exist_ok=True
)


def unique_file(
    prefix,
    ext
):

    return str(
        BASE_DIR /
        f"{prefix}_{uuid.uuid4().hex[:10]}{ext}"
    )


# =========================================================
# BASIC HELPERS
# =========================================================

def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)

    except Exception:
        return default


def run_cmd(
    cmd,
    timeout=1800
):

    print(
        "\nRUNNING:"
    )

    print(
        " ".join(
            str(x)
            for x in cmd
        )
    )

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:

        raise RuntimeError(
            "FFmpeg process timeout."
        )

    if result.returncode != 0:

        print(
            result.stderr
        )

        raise RuntimeError(
            result.stderr[-7000:]
        )

    return result


def ffmpeg_exists():

    return (
        shutil.which(
            "ffmpeg"
        )
        is not None
    )


def ffprobe_exists():

    return (
        shutil.which(
            "ffprobe"
        )
        is not None
    )


# =========================================================
# API KEY
# =========================================================

def save_api_key(
    api_key
):

    global SAVED_API_KEY

    if (
        api_key
        and
        api_key.strip()
    ):

        SAVED_API_KEY = (
            api_key.strip()
        )

        return (
            "✅ Gemini API Key သိမ်းပြီးပါပြီ။"
        )

    return (
        "⚠️ Gemini API Key ထည့်ပါ။"
    )


# =========================================================
# VIDEO INFO
# =========================================================

def get_video_info(
    video_path
):

    if not video_path:
        return {}

    if not os.path.exists(
        video_path
    ):
        return {}

    if not ffprobe_exists():
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

        return json.loads(
            result.stdout
        )

    except Exception:
        return {}


def get_video_duration(
    video_path
):

    info = get_video_info(
        video_path
    )

    try:

        return float(
            info[
                "format"
            ][
                "duration"
            ]
        )

    except Exception:
        return None


def has_audio_stream(
    video_path
):

    info = get_video_info(
        video_path
    )

    for stream in info.get(
        "streams",
        []
    ):

        if (
            stream.get(
                "codec_type"
            )
            ==
            "audio"
        ):

            return True

    return False


def validate_video_duration(
    video_path
):

    duration = get_video_duration(
        video_path
    )

    if duration is None:

        return (
            True,
            "ℹ️ Video duration စစ်ဆေး၍မရပါ။"
        )

    minutes = (
        duration / 60
    )

    if (
        minutes
        >
        MAX_VIDEO_MINUTES
    ):

        return (
            False,
            (
                f"⚠️ Video {minutes:.1f} minutes ရှိပါတယ်။\n"
                f"အများဆုံး {MAX_VIDEO_MINUTES} minutes ပါ။"
            )
        )

    return (
        True,
        f"✅ Video Length: {minutes:.1f} minutes"
    )


# =========================================================
# CLEAN SCRIPT
# =========================================================

def clean_script_for_tts(
    script_text
):

    if not script_text:
        return ""

    lines = []

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
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue)\]\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        line = re.sub(
            r"^\s*(Narrator|Narration)\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        if line.startswith(
            "---"
        ):
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

        lines.append(
            line
        )

    return "\n".join(
        lines
    ).strip()


# =========================================================
# SRT
# =========================================================

def seconds_to_srt_time(
    seconds
):

    seconds = max(
        0,
        float(seconds)
    )

    total_ms = int(
        seconds * 1000
    )

    hours = (
        total_ms
        //
        3600000
    )

    minutes = (
        total_ms
        %
        3600000
    ) // 60000

    secs = (
        total_ms
        %
        60000
    ) // 1000

    ms = (
        total_ms
        %
        1000
    )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{ms:03d}"
    )


def split_script_sentences(
    text
):

    text = clean_script_for_tts(
        text
    )

    if not text:
        return []

    # Keep Burmese sentences reasonably small
    parts = re.split(
        r"(?<=[။!?])\s+|\n+",
        text
    )

    result = []

    for part in parts:

        part = part.strip()

        if part:
            result.append(
                part
            )

    return result


def generate_srt_content(
    script_text,
    audio_duration=None
):

    clean_text = clean_script_for_tts(
        script_text
    )

    if not clean_text:
        return ""

    sentences = (
        split_script_sentences(
            clean_text
        )
    )

    if not sentences:
        return ""

    if not audio_duration:
        audio_duration = max(
            5,
            len(clean_text) / 10
        )

    weights = [
        max(
            1,
            len(sentence)
        )
        for sentence in sentences
    ]

    total_weight = sum(
        weights
    )

    current = 0.0

    output = []

    for index, sentence in enumerate(
        sentences,
        start=1
    ):

        duration = (
            audio_duration
            *
            weights[index - 1]
            /
            total_weight
        )

        start = current

        end = (
            current
            +
            duration
        )

        output.append(
            f"{index}\n"
            f"{seconds_to_srt_time(start)} --> "
            f"{seconds_to_srt_time(end)}\n"
            f"{sentence}\n"
        )

        current = end

    return "\n".join(
        output
    )


def generate_srt_and_zip(
    script_text,
    audio_duration=None
):

    srt_content = (
        generate_srt_content(
            script_text,
            audio_duration
        )
    )

    if not srt_content:
        return (
            None,
            None
        )

    srt_filename = unique_file(
        "subtitle",
        ".srt"
    )

    zip_filename = unique_file(
        "subtitle",
        ".zip"
    )

    Path(
        srt_filename
    ).write_text(
        srt_content,
        encoding="utf-8-sig"
    )

    with zipfile.ZipFile(
        zip_filename,
        "w",
        zipfile.ZIP_DEFLATED
    ) as z:

        z.write(
            srt_filename,
            "myanmar_recap_subtitle.srt"
        )

    return (
        srt_filename,
        zip_filename
    )


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
            e
        )

    return None


def load_link_preview(link):
    if not link:
        return None
    return download_video_from_link(link)


def resolve_video(
    video_file,
    video_link
):

    if video_file:

        if isinstance(
            video_file,
            dict
        ):

            video_file = (
                video_file.get(
                    "path"
                )
                or
                video_file.get(
                    "name"
                )
            )

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

RATIO_SIZES = {

    "9:16":
        (1080, 1920),

    "3:4":
        (1080, 1440),

    "1:1":
        (1080, 1080),

    "16:9":
        (1920, 1080),
}


def ratio_size(
    ratio
):

    return RATIO_SIZES.get(
        ratio,
        RATIO_SIZES["9:16"]
    )


def get_ratio_css(
    ratio
):

    width, height = ratio_size(
        ratio
    )

    return f"""
    <div style="
        width:100%;
        display:flex;
        justify-content:center;
        align-items:center;
        background:#111;
        padding:15px;
        box-sizing:border-box;
    ">
        <div style="
            aspect-ratio:{width}/{height};
            width:min(100%,700px);
            max-height:70vh;
            background:#000;
            overflow:hidden;
        ">
            <div style="
                width:100%;
                height:100%;
                display:flex;
                align-items:center;
                justify-content:center;
                color:#aaa;
                font-size:14px;
            ">
                {ratio} — {width} × {height}
            </div>
        </div>
    </div>
    """


# =========================================================
# GEMINI PROMPT
# =========================================================

def build_fast_recap_prompt(
    ratio
):

    return f"""
You are a professional movie recap writer.

Analyze this video from beginning to end.

Target output:
Burmese social-media movie recap.

Target ratio:
{ratio}

IMPORTANT:
1. Use only events actually visible or audible.
2. Never invent story details.
3. Never invent character names.
4. Never invent relationships.
5. Never invent dialogue.
6. If dialogue is clearly understandable, naturally translate its meaning into Burmese.
7. If dialogue is unclear, describe the visible action instead.
8. Follow the actual timeline.
9. Include important beginning, middle and ending events.
10. Make the script natural for Burmese TTS.
11. Do not output analysis.
12. Do not output headings.
13. Do not output timestamps.
14. Return ONLY the final Burmese recap script.

STYLE:

Start directly with an interesting event.

Use a mixture of:
- visible action
- short narration
- natural character dialogue
- character reaction
- next action

Do not make every sentence narrator-only.

Keep sentences short and TTS friendly.

Avoid:
"တစ်နေ့မှာ..."
"ဒီဇာတ်ကားမှာတော့..."
"နောက်ဆုံးမှာတော့..."

Write a continuous story.

Do not explain what you are doing.

Return ONLY Burmese recap text.
"""


# =========================================================
# GEMINI RETRY
# =========================================================

def retryable_error(
    error
):

    text = str(
        error
    ).lower()

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
            "temporarily"
        ]
    )


def generate_script_fast(
    client,
    uploaded_file,
    prompt
):

    last_error = None

    for model in GEMINI_MODELS:

        for attempt in range(3):

            try:

                # Low thinking = faster recap generation
                config = (
                    types.GenerateContentConfig(
                        thinking_config=(
                            types.ThinkingConfig(
                                thinking_level="low"
                            )
                        ),
                        temperature=0.4,
                    )
                )

                response = (
                    client.models.generate_content(
                        model=model,
                        contents=[
                            uploaded_file,
                            prompt
                        ],
                        config=config
                    )
                )

                if (
                    response
                    and
                    response.text
                    and
                    response.text.strip()
                ):

                    return (
                        response.text.strip(),
                        model
                    )

                raise RuntimeError(
                    "Gemini Empty Response"
                )

            except Exception as e:

                last_error = e

                print(
                    f"{model} attempt {attempt+1}:",
                    e
                )

                if not retryable_error(
                    e
                ):

                    break

                time.sleep(
                    3
                    +
                    attempt * 4
                )

    raise RuntimeError(
        f"Gemini failed: {last_error}"
    )


# =========================================================
# GEMINI VIDEO ANALYSIS
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
            "⚠️ Gemini API Key မရှိပါ။",
            None,
            None,
            None
        )

    source = resolve_video(
        video_file,
        video_link
    )

    if not source:

        return (
            "",
            "⚠️ Video Upload သို့မဟုတ် URL ထည့်ပါ။",
            None,
            None,
            None
        )

    if not os.path.exists(
        source
    ):

        return (
            "",
            "⚠️ Video file မတွေ့ပါ။",
            None,
            None,
            None
        )

    valid, message = (
        validate_video_duration(
            source
        )
    )

    if not valid:

        return (
            "",
            message,
            None,
            None,
            source
        )

    try:

        client = genai.Client(
            api_key=SAVED_API_KEY
        )

        print(
            "Uploading video to Gemini..."
        )

        uploaded = (
            client.files.upload(
                file=source
            )
        )

        start = time.time()

        while True:

            state = ""

            if uploaded.state:

                state = (
                    uploaded.state.name
                )

            print(
                "Gemini:",
                state
            )

            if state == "ACTIVE":
                break

            if state == "FAILED":

                raise RuntimeError(
                    "Gemini Video Processing Failed."
                )

            if (
                time.time() - start
                >
                600
            ):

                raise RuntimeError(
                    "Gemini Video Processing Timeout."
                )

            time.sleep(
                3
            )

            uploaded = (
                client.files.get(
                    name=uploaded.name
                )
            )

        prompt = (
            build_fast_recap_prompt(
                ratio_choice
            )
        )

        print(
            "Generating recap..."
        )

        script, used_model = (
            generate_script_fast(
                client,
                uploaded,
                prompt
            )
        )

        script = clean_script_for_tts(
            script
        )

        if not script:

            raise RuntimeError(
                "Gemini Script Empty."
            )

        srt_file, zip_file = (
            generate_srt_and_zip(
                script
            )
        )

        status = (
            "## ✅ Script Ready\n\n"
            f"{message}\n\n"
            f"🤖 Model: `{used_model}`\n\n"
            "⚡ Fast Video Recap Mode"
        )

        return (
            script,
            status,
            srt_file,
            zip_file,
            source
        )

    except Exception as e:

        print(
            "Analysis Error:",
            e
        )

        return (
            "",
            f"❌ Script Error:\n\n{e}",
            None,
            None,
            source
        )


# =========================================================
# TTS
# =========================================================

async def make_tts(
    text,
    voice_name,
    speed
):

    if not text:
        return None

    text = clean_script_for_tts(
        text
    )

    voice = VOICES.get(
        voice_name,
        "my-MM-ThihaNeural"
    )

    output = unique_file(
        "voice",
        ".mp3"
    )

    rate = (
        f"{int(speed):+d}%"
    )

    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate
    )

    await communicate.save(
        output
    )

    if not os.path.exists(
        output
    ):
        return None

    return output


def tts_interface(
    text,
    voice,
    speed
):

    try:

        audio = asyncio.run(
            make_tts(
                text,
                voice,
                speed
            )
        )

        if not audio:

            return (
                None,
                None,
                None,
                None
            )

        duration = (
            get_audio_duration(
                audio
            )
        )

        srt, zip_file = (
            generate_srt_and_zip(
                text,
                duration
            )
        )

        return (
            audio,
            audio,
            srt,
            zip_file
        )

    except Exception as e:

        print(
            "TTS Error:",
            e
        )

        return (
            None,
            None,
            None,
            None
        )


def get_audio_duration(
    audio
):

    if not audio:
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
                audio
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode == 0:

            return float(
                result.stdout.strip()
            )

    except Exception:
        pass

    return None


# =========================================================
# TRANSLATE SUBTITLE
# =========================================================

def translate_script(
    text,
    language
):

    if (
        not text
        or
        language == "Myanmar"
    ):

        return text

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
Translate this movie recap into natural {language_name}.

Rules:
- Preserve meaning.
- Do not add information.
- Do not remove information.
- Keep dialogue natural.
- Return only translated text.

{text}
"""

        response = (
            client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_level="low"
                    )
                )
            )
        )

        if (
            response
            and
            response.text
        ):

            return response.text.strip()

    except Exception as e:

        print(
            "Translation error:",
            e
        )

    return text


# =========================================================
# SUBTITLE
# =========================================================

COLOR_MAP = {

    "White": "&H00FFFFFF",
    "Black": "&H00000000",
    "Red": "&H000000FF",
    "Yellow": "&H0000FFFF",
    "Green": "&H0000FF00",
    "Blue": "&H00FF0000",
    "Orange": "&H000080FF",
    "Pink": "&H00CBC0FF",
    "Purple": "&H00800080",
    "Cyan": "&H00FFFF00",
}


def srt_to_ass(
    srt_file,
    font_name,
    font_size,
    text_color,
    outline_color,
    outline_width,
    x_percent,
    y_percent,
    width,
    height
):

    if not srt_file:
        return None

    content = Path(
        srt_file
    ).read_text(
        encoding="utf-8-sig"
    )

    # Convert percentage to actual output coordinates
    x = int(
        width
        *
        safe_float(
            x_percent,
            50
        )
        /
        100
    )

    y = int(
        height
        *
        safe_float(
            y_percent,
            85
        )
        /
        100
    )

    ass_file = unique_file(
        "subtitle",
        ".ass"
    )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            "Style: Default,"
            f"{font_name},"
            f"{int(font_size)},"
            f"{COLOR_MAP.get(text_color, COLOR_MAP['White'])},"
            f"{COLOR_MAP.get(text_color, COLOR_MAP['White'])},"
            f"{COLOR_MAP.get(outline_color, COLOR_MAP['Black'])},"
            "&H80000000,"
            "0,0,0,0,100,100,0,0,1,"
            f"{int(outline_width)},"
            "0,2,20,20,20,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    blocks = re.split(
        r"\n\s*\n",
        content.strip()
    )

    for block in blocks:

        rows = block.splitlines()

        if len(rows) < 3:
            continue

        timing = rows[1]

        match = re.match(
            r"(.+?)\s*-->\s*(.+)",
            timing
        )

        if not match:
            continue

        start = (
            match.group(1)
            .replace(",", ".")
        )

        end = (
            match.group(2)
            .replace(",", ".")
        )

        text = (
            "\\N".join(
                rows[2:]
            )
        )

        text = (
            text
            .replace(
                "{",
                "\\{"
            )
            .replace(
                "}",
                "\\}"
            )
        )

        # EXACT X/Y position
        text = (
            f"{{\\pos({x},{y})}}"
            +
            text
        )

        lines.append(
            "Dialogue: 0,"
            f"{start},"
            f"{end},"
            "Default,,0,0,0,,"
            f"{text}"
        )

    Path(
        ass_file
    ).write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    return ass_file


# =========================================================
# FFMPEG PATH ESCAPE
# =========================================================

def escape_filter_path(
    path
):

    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


# =========================================================
# BUILD VIDEO FILTER
# =========================================================

def build_exact_video_filter(
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
    logo_index,
    logo_enabled,
    logo_x,
    logo_y,
    logo_scale,
    subtitle_ass,
    width,
    height
):

    filters = []

    # =====================================================
    # EXACT RATIO
    # =====================================================

    # Scale so the frame completely covers target.
    filters.append(
        f"scale="
        f"{width}:"
        f"{height}:"
        "force_original_aspect_ratio=increase"
    )

    # Exact target crop.
    filters.append(
        f"crop="
        f"{width}:"
        f"{height}"
    )

    filters.append(
        "setsar=1"
    )

    # =====================================================
    # ZOOM
    # =====================================================

    z = max(
        1.0,
        safe_float(
            zoom,
            1
        )
    )

    if z > 1.001:

        zoom_w = int(
            width * z
        )

        zoom_h = int(
            height * z
        )

        filters.append(
            f"scale="
            f"{zoom_w}:"
            f"{zoom_h}"
        )

        filters.append(
            f"crop="
            f"{width}:"
            f"{height}"
        )

    # =====================================================
    # FLIP
    # =====================================================

    if flip:

        filters.append(
            "hflip"
        )

    # =====================================================
    # BRIGHTNESS
    # =====================================================

    b = safe_float(
        brightness,
        0
    )

    if abs(b) > 0.001:

        filters.append(
            f"eq=brightness={b:.3f}"
        )

    return filters


# =========================================================
# FINAL RENDER
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
    subtitle_font,
    voice_file=None,
    preview_seconds=None
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

    if not os.path.exists(
        source
    ):

        return (
            None,
            "⚠️ Video file မတွေ့ပါ။"
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
        safe_float(
            trim_start,
            0
        )
    )

    end = safe_float(
        trim_end,
        0
    )

    if end <= 0:

        end = (
            duration
            or
            999999
        )

    if duration:

        end = min(
            end,
            duration
        )

    if (
        end
        <=
        start
    ):

        return (
            None,
            "⚠️ Trim Start / End မမှန်ပါ။"
        )

    # =====================================================
    # EXACT OUTPUT SIZE
    # =====================================================

    width, height = (
        ratio_size(
            ratio
        )
    )

    # =====================================================
    # PREPARE VOICE
    # =====================================================

    generated_voice = voice_file

    if (
        not generated_voice
        and
        recap_script
        and
        recap_script.strip()
    ):

        try:

            generated_voice = asyncio.run(
                make_tts(
                    recap_script,
                    "Thiha (အမျိုးသားအသံ) - Natural",
                    5
                )
            )

        except Exception as e:

            print(
                "Auto TTS Error:",
                e
            )

    # =====================================================
    # SUBTITLE
    # =====================================================

    subtitle_ass = None

    if (
        subtitle_enabled
        and
        recap_script
        and
        recap_script.strip()
    ):

        subtitle_text = (
            translate_script(
                recap_script,
                subtitle_language
            )
        )

        audio_duration = None

        if generated_voice:

            audio_duration = (
                get_audio_duration(
                    generated_voice
                )
            )

        srt_content = (
            generate_srt_content(
                subtitle_text,
                audio_duration
            )
        )

        if srt_content:

            srt_file = unique_file(
                "render_subtitle",
                ".srt"
            )

            Path(
                srt_file
            ).write_text(
                srt_content,
                encoding="utf-8-sig"
            )

            subtitle_ass = srt_to_ass(
                srt_file,
                subtitle_font,
                subtitle_size,
                subtitle_text_color,
                subtitle_outline_color,
                subtitle_outline,
                subtitle_x,
                subtitle_y,
                width,
                height
            )

    # =====================================================
    # INPUT INDEX
    # =====================================================

    # 0 = source
    next_index = 1

    logo_index = None
    music_index = None
    voice_index = None

    has_logo = (
        logo_enabled
        and
        logo_file
        and
        os.path.exists(
            logo_file
        )
    )

    has_music = (
        bg_music
        and
        os.path.exists(
            bg_music
        )
    )

    has_voice = (
        generated_voice
        and
        os.path.exists(
            generated_voice
        )
    )

    if has_logo:

        logo_index = next_index

        next_index += 1

    if has_music:

        music_index = next_index

        next_index += 1

    if has_voice:

        voice_index = next_index

        next_index += 1

    # =====================================================
    # VIDEO FILTER
    # =====================================================

    filter_parts = []

    base_filters = (
        build_exact_video_filter(
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
            logo_index,
            logo_enabled,
            logo_x,
            logo_y,
            logo_scale,
            subtitle_ass,
            width,
            height
        )
    )

    filter_parts.append(
        "[0:v]"
        +
        ",".join(
            base_filters
        )
        +
        "[base]"
    )

    current_video = "base"

    # =====================================================
    # BLUR REGION
    # =====================================================

    if blur_enabled:

        x = int(
            width
            *
            safe_float(
                blur_x,
                10
            )
            /
            100
        )

        y = int(
            height
            *
            safe_float(
                blur_y,
                10
            )
            /
            100
        )

        w = int(
            width
            *
            safe_float(
                blur_w,
                30
            )
            /
            100
        )

        h = int(
            height
            *
            safe_float(
                blur_h,
                20
            )
            /
            100
        )

        x = max(
            0,
            min(
                x,
                width - 10
            )
        )

        y = max(
            0,
            min(
                y,
                height - 10
            )
        )

        w = max(
            10,
            min(
                w,
                width - x
            )
        )

        h = max(
            10,
            min(
                h,
                height - y
            )
        )

        strength = max(
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
            f"split=2[normal][blurbase]"
        )

        filter_parts.append(
            f"[blurbase]"
            f"crop={w}:{h}:{x}:{y},"
            f"boxblur="
            f"luma_radius={strength}:"
            f"luma_power=1[blurpatch]"
        )

        filter_parts.append(
            f"[normal][blurpatch]"
            f"overlay={x}:{y}"
            "[blurout]"
        )

        current_video = "blurout"

    # =====================================================
    # LOGO
    # =====================================================

    if has_logo:

        logo_h = max(
            20,
            int(
                height
                *
                safe_float(
                    logo_scale,
                    20
                )
                /
                100
            )
        )

        # Position percentage
        lx = int(
            (
                width
                -
                logo_h
            )
            *
            safe_float(
                logo_x,
                85
            )
            /
            100
        )

        ly = int(
            (
                height
                -
                logo_h
            )
            *
            safe_float(
                logo_y,
                10
            )
            /
            100
        )

        lx = max(
            0,
            lx
        )

        ly = max(
            0,
            ly
        )

        filter_parts.append(
            f"[{logo_index}:v]"
            f"format=rgba,"
            f"scale=-1:{logo_h}"
            "[logo]"
        )

        filter_parts.append(
            f"[{current_video}]"
            "[logo]"
            f"overlay={lx}:{ly}"
            "[logoout]"
        )

        current_video = "logoout"

    # =====================================================
    # SUBTITLE
    # =====================================================

    if subtitle_ass:

        escaped_ass = (
            escape_filter_path(
                subtitle_ass
            )
        )

        filter_parts.append(
            f"[{current_video}]"
            f"ass='{escaped_ass}'"
            "[subout]"
        )

        current_video = "subout"

    # =====================================================
    # AUDIO FILTER
    # =====================================================

    audio_labels = []

    # ORIGINAL AUDIO
    if (
        original_audio
        and
        has_audio_stream(
            source
        )
    ):

        volume = max(
            0,
            min(
                200,
                safe_float(
                    original_volume,
                    30
                )
            )
        ) / 100

        filter_parts.append(
            f"[0:a]"
            f"volume={volume:.3f}"
            "[orig]"
        )

        audio_labels.append(
            "[orig]"
        )

    # BACKGROUND MUSIC
    if has_music:

        volume = max(
            0,
            min(
                200,
                safe_float(
                    music_volume,
                    15
                )
            )
        ) / 100

        filter_parts.append(
            f"[{music_index}:a]"
            f"volume={volume:.3f},"
            "aresample=async=1"
            "[music]"
        )

        audio_labels.append(
            "[music]"
        )

    # BURMESE VOICE
    if has_voice:

        filter_parts.append(
            f"[{voice_index}:a]"
            "volume=1,"
            "aresample=async=1"
            "[voice]"
        )

        audio_labels.append(
            "[voice]"
        )

    # =====================================================
    # MIX AUDIO
    # =====================================================

    if len(
        audio_labels
    ) == 0:

        audio_map = None

    elif len(
        audio_labels
    ) == 1:

        audio_map = audio_labels[0]

    else:

        filter_parts.append(
            "".join(
                audio_labels
            )
            +
            f"amix="
            f"inputs={len(audio_labels)}:"
            "duration=longest:"
            "dropout_transition=2:"
            "normalize=0"
            "[finalaudio]"
        )

        audio_map = (
            "[finalaudio]"
        )

    # =====================================================
    # COMMAND
    # =====================================================

    cmd = [
        "ffmpeg",
        "-y",

        "-ss",
        str(start),

        "-i",
        source
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
            generated_voice
        ]

    filter_complex = ";".join(
        filter_parts
    )

    cmd += [
        "-filter_complex",
        filter_complex,

        "-map",
        f"[{current_video}]"
    ]

    if audio_map:

        cmd += [
            "-map",
            audio_map
        ]

    else:

        cmd += [
            "-an"
        ]

    # Preview = first 15 seconds
    if preview_seconds:

        render_duration = min(
            float(preview_seconds),
            max(
                1,
                end - start
            )
        )

    else:

        render_duration = max(
            1,
            end - start
        )

    cmd += [
        "-t",
        str(render_duration),

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

        unique_file(
            "preview"
            if preview_seconds
            else "final",
            ".mp4"
        )
    ]

    output = cmd[-1]

    try:

        run_cmd(
            cmd,
            timeout=1800
        )

        if not os.path.exists(
            output
        ):

            raise RuntimeError(
                "FFmpeg output မတွေ့ပါ။"
            )

        if preview_seconds:

            status = (
                f"## 👀 Preview Ready\n\n"
                f"📐 **{ratio}**\n\n"
                f"📏 **{width} × {height}**\n\n"
                "ဒီ Preview က Final Video မှာ "
                "သုံးမယ့် ratio နဲ့ filter အတိုင်း "
                "render လုပ်ထားတာပါ။"
            )

        else:

            status = (
                "## ✅ FINAL VIDEO READY\n\n"
                f"📐 Ratio: **{ratio}**\n\n"
                f"📏 Resolution: **{width} × {height}**\n\n"
                "🎙️ Burmese Voice\n\n"
                "🎵 Audio Mix\n\n"
                "📝 Subtitle\n\n"
                "🏷️ Logo\n\n"
                "🌫️ Blur\n\n"
                "🎬 Video Effects"
            )

        return (
            output,
            status
        )

    except Exception as e:

        print(
            "Render Error:",
            e
        )

        return (
            None,
            f"❌ Render Error\n\n{e}"
        )


# =========================================================
# PREVIEW
# =========================================================

def preview_render(
    video_file,
    video_url,
    edit_script,
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
    subtitle_font,
    voice_file
):

    return render_video(
        video_file,
        video_url,
        edit_script,
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
        subtitle_font,
        voice_file=voice_file,
        preview_seconds=15
    )


# =========================================================
# UI CSS
# =========================================================

CSS = """

#ratio_preview {
    width:100%;
}

#ratio_preview video {
    background:#000 !important;
    object-fit:contain !important;
}

"""


# =========================================================
# UI
# =========================================================

with gr.Blocks(
    title=APP_TITLE,
    css=CSS,
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown(
        """
# 🎬 AI Movie Recap Studio Pro

### Video → Gemini Recap → Burmese Voice → Subtitle → Edit → Final MP4
"""
    )

    # =====================================================
    # API
    # =====================================================

    with gr.Tab(
        "🔑 API Key"
    ):

        api_key_input = gr.Textbox(
            label="Gemini API Key",
            type="password",
            placeholder="AIza..."
        )

        save_key_btn = gr.Button(
            "💾 Save API Key",
            variant="primary"
        )

        key_status = gr.Markdown()

    # =====================================================
    # TAB 1
    # =====================================================

    with gr.Tab(
        "1️⃣ Video Analysis & Script"
    ):

        with gr.Row():

            with gr.Column():

                video_file = gr.Video(
                    label="📹 Upload Video"
                )

                video_url = gr.Textbox(
                    label="🔗 Video URL",
                    placeholder=(
                        "YouTube / TikTok / Facebook / RedNote"
                    )
                )

                load_link_btn = gr.Button(
                    "🔍 Load URL Video"
                )

                ratio_picker = gr.Radio(
                    choices=[
                        "9:16",
                        "3:4",
                        "1:1",
                        "16:9"
                    ],
                    value="9:16",
                    label="📐 Target Ratio"
                )

                gen_script_btn = gr.Button(
                    "🚀 Generate Burmese Recap Script",
                    variant="primary"
                )

            with gr.Column():

                ratio_info = gr.Markdown(
                    get_ratio_css(
                        "9:16"
                    )
                )

                tab1_preview = gr.Video(
                    label="📺 Video Preview"
                )

                script_status = gr.Markdown(
                    "Video ထည့်ပြီး Script Generate လုပ်နိုင်ပါပြီ။"
                )

                script_display = gr.Textbox(
                    label="🎬 Burmese Recap Script",
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
        "2️⃣ Burmese Text-to-Speech"
    ):

        with gr.Row():

            with gr.Column():

                input_text = gr.Textbox(
                    label="🎙️ Burmese Script",
                    lines=18
                )

                voice_dropdown = gr.Dropdown(
                    choices=list(
                        VOICES.keys()
                    ),
                    value=(
                        "Thiha "
                        "(အမျိုးသားအသံ) - Natural"
                    ),
                    label="🎤 Voice"
                )

                speed_slider = gr.Slider(
                    minimum=-30,
                    maximum=50,
                    value=5,
                    step=1,
                    label="⚡ Voice Speed %"
                )

                gen_voice_btn = gr.Button(
                    "🔊 Generate Voice",
                    variant="primary"
                )

            with gr.Column():

                audio_output = gr.Audio(
                    label="🎧 Voice Preview",
                    type="filepath",
                    autoplay=True
                )

                mp3_download = gr.File(
                    label="🎵 Download MP3"
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
        "3️⃣ Video Edit - All In One"
    ):

        with gr.Row():

            with gr.Column():

                edit_video_file = gr.Video(
                    label="📹 Edit Video"
                )

                edit_video_url = gr.Textbox(
                    label="🔗 Video URL"
                )

                edit_load_btn = gr.Button(
                    "🔍 Load URL"
                )

            with gr.Column():

                edit_preview = gr.Video(
                    label="👀 Rendered Preview",
                    elem_id="ratio_preview"
                )

                edit_status = gr.Markdown()

        # =================================================
        # RATIO
        # =================================================

        edit_ratio = gr.Radio(
            choices=[
                "9:16",
                "3:4",
                "1:1",
                "16:9"
            ],
            value="9:16",
            label="📐 FINAL VIDEO RATIO"
        )

        edit_ratio_info = gr.Markdown(
            get_ratio_css(
                "9:16"
            )
        )

        # =================================================
        # SCRIPT / VOICE
        # =================================================

        with gr.Accordion(
            "🎙️ Burmese Voice",
            open=True
        ):

            edit_script = gr.Textbox(
                label="Recap Script",
                lines=12
            )

            edit_voice = gr.Dropdown(
                choices=list(
                    VOICES.keys()
                ),
                value=(
                    "Thiha "
                    "(အမျိုးသားအသံ) - Natural"
                ),
                label="Voice"
            )

            edit_voice_speed = gr.Slider(
                minimum=-30,
                maximum=50,
                value=5,
                step=1,
                label="Voice Speed"
            )

            generate_edit_voice_btn = gr.Button(
                "🎙️ Generate Voice"
            )

            edit_voice_preview = gr.Audio(
                label="🎧 Voice Preview",
                type="filepath",
                autoplay=True
            )

            edit_voice_download = gr.File(
                label="🎵 MP3 Download"
            )

        # =================================================
        # TRIM
        # =================================================

        with gr.Accordion(
            "✂️ Trim",
            open=True
        ):

            with gr.Row():

                trim_start = gr.Number(
                    value=0,
                    label="Start Seconds"
                )

                trim_end = gr.Number(
                    value=0,
                    label="End Seconds (0 = End)"
                )

        # =================================================
        # VIDEO
        # =================================================

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
                    label="↔️ Flip",
                    value=False
                )

        # =================================================
        # AUDIO
        # =================================================

        with gr.Accordion(
            "🔊 Audio",
            open=True
        ):

            with gr.Row():

                original_audio = gr.Checkbox(
                    label="Original Audio ON",
                    value=False
                )

                original_volume = gr.Slider(
                    minimum=0,
                    maximum=200,
                    value=30,
                    step=1,
                    label="Original Volume %"
                )

            bg_music = gr.Audio(
                label="🎵 Background Music",
                type="filepath"
            )

            music_volume = gr.Slider(
                minimum=0,
                maximum=200,
                value=15,
                step=1,
                label="Music Volume %"
            )

        # =================================================
        # LOGO
        # =================================================

        with gr.Accordion(
            "🏷️ Logo",
            open=False
        ):

            logo_file = gr.Image(
                label="Logo",
                type="filepath"
            )

            logo_enabled = gr.Checkbox(
                label="Logo ON",
                value=False
            )

            with gr.Row():

                logo_x = gr.Slider(
                    0,
                    100,
                    value=85,
                    step=1,
                    label="Logo X %"
                )

                logo_y = gr.Slider(
                    0,
                    100,
                    value=10,
                    step=1,
                    label="Logo Y %"
                )

                logo_scale = gr.Slider(
                    5,
                    50,
                    value=20,
                    step=1,
                    label="Logo Size %"
                )

        # =================================================
        # BLUR
        # =================================================

        with gr.Accordion(
            "🌫️ Blur",
            open=False
        ):

            blur_enabled = gr.Checkbox(
                label="Blur ON",
                value=False
            )

            with gr.Row():

                blur_x = gr.Slider(
                    0,
                    100,
                    value=10,
                    step=1,
                    label="Blur X %"
                )

                blur_y = gr.Slider(
                    0,
                    100,
                    value=10,
                    step=1,
                    label="Blur Y %"
                )

            with gr.Row():

                blur_w = gr.Slider(
                    1,
                    100,
                    value=30,
                    step=1,
                    label="Blur Width %"
                )

                blur_h = gr.Slider(
                    1,
                    100,
                    value=20,
                    step=1,
                    label="Blur Height %"
                )

                blur_strength = gr.Slider(
                    1,
                    40,
                    value=10,
                    step=1,
                    label="Blur Strength"
                )

        # =================================================
        # SUBTITLE
        # =================================================

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
                    label="Language"
                )

                subtitle_font = gr.Dropdown(
                    choices=[
                        "Noto Sans Myanmar",
                        "Noto Sans",
                        "DejaVu Sans",
                        "Liberation Sans"
                    ],
                    value="Noto Sans Myanmar",
                    label="Font"
                )

            with gr.Row():

                subtitle_size = gr.Slider(
                    12,
                    80,
                    value=32,
                    step=1,
                    label="Font Size"
                )

                subtitle_outline = gr.Slider(
                    0,
                    10,
                    value=3,
                    step=1,
                    label="Outline"
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
                    0,
                    100,
                    value=50,
                    step=1,
                    label="Subtitle X %"
                )

                subtitle_y = gr.Slider(
                    0,
                    100,
                    value=85,
                    step=1,
                    label="Subtitle Y %"
                )

        # =================================================
        # BUTTONS
        # =================================================

        with gr.Row():

            preview_button = gr.Button(
                "👀 PREVIEW 15 SEC",
                variant="secondary",
                size="lg"
            )

            final_button = gr.Button(
                "🎬 CREATE FINAL VIDEO",
                variant="primary",
                size="lg"
            )

        # =================================================
        # FINAL
        # =================================================

        final_video = gr.Video(
            label="🎥 FINAL VIDEO"
        )

        final_status = gr.Markdown()

        final_download = gr.File(
            label="⬇️ DOWNLOAD FINAL MP4"
        )

    # =====================================================
    # EVENTS
    # =====================================================

    # -----------------------------------------------------
    # API
    # -----------------------------------------------------

    save_key_btn.click(
        fn=save_api_key,
        inputs=[
            api_key_input
        ],
        outputs=[
            key_status
        ]
    )

    # -----------------------------------------------------
    # RATIO INFO
    # -----------------------------------------------------

    ratio_picker.change(
        fn=get_ratio_css,
        inputs=[
            ratio_picker
        ],
        outputs=[
            ratio_info
        ]
    )

    edit_ratio.change(
        fn=get_ratio_css,
        inputs=[
            edit_ratio
        ],
        outputs=[
            edit_ratio_info
        ]
    )

    # -----------------------------------------------------
    # TAB 1 VIDEO
    # -----------------------------------------------------

    video_file.change(
        fn=lambda x: x,
        inputs=[
            video_file
        ],
        outputs=[
            tab1_preview
        ]
    )

    load_link_btn.click(
        fn=load_link_preview,
        inputs=[
            video_url
        ],
        outputs=[
            tab1_preview
        ]
    )

    # -----------------------------------------------------
    # GENERATE SCRIPT
    # -----------------------------------------------------

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
            zip_download_tab1,
            edit_video_file
        ]
    )

    # Script → TTS
    gen_script_btn.click(
        fn=lambda x: x,
        inputs=[
            script_display
        ],
        outputs=[
            input_text
        ]
    )

    # Script → Editor
    gen_script_btn.click(
        fn=lambda x: x,
        inputs=[
            script_display
        ],
        outputs=[
            edit_script
        ]
    )

    # Video → Editor
    gen_script_btn.click(
        fn=lambda x: x,
        inputs=[
            video_file
        ],
        outputs=[
            edit_video_file
        ]
    )

    # -----------------------------------------------------
    # TAB 2 TTS
    # -----------------------------------------------------

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

    # TTS → Editor voice
    gen_voice_btn.click(
        fn=lambda x: x,
        inputs=[
            audio_output
        ],
        outputs=[
            edit_voice_preview
        ]
    )

    # -----------------------------------------------------
    # EDIT URL
    # -----------------------------------------------------

    edit_load_btn.click(
        fn=load_link_preview,
        inputs=[
            edit_video_url
        ],
        outputs=[
            edit_video_file
        ]
    )

    # -----------------------------------------------------
    # EDIT VIDEO
    # -----------------------------------------------------

    edit_video_file.change(
        fn=lambda x: x,
        inputs=[
            edit_video_file
        ],
        outputs=[
            edit_preview
        ]
    )

    # -----------------------------------------------------
    # EDIT VOICE
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # PREVIEW
    # -----------------------------------------------------

    preview_button.click(
        fn=preview_render,
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
            subtitle_font,

            edit_voice_preview
        ],
        outputs=[
            edit_preview,
            edit_status
        ]
    )

    # -----------------------------------------------------
    # FINAL
    # -----------------------------------------------------

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
            subtitle_font,

            edit_voice_preview
        ],
        outputs=[
            final_video,
            final_status
        ]
    )

    # Final video → Download
    final_video.change(
        fn=lambda x: x,
        inputs=[
            final_video
        ],
        outputs=[
            final_download
        ]
    )


# =========================================================
# LAUNCH
# =========================================================

if __name__ == "__main__":

    if not ffmpeg_exists():

        print(
            "WARNING: FFmpeg မတွေ့ပါ။"
        )

    demo.launch(
        server_name="0.0.0.0",
        server_port=PORT,
        show_error=True
    )
