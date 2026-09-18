import os
import re
import time
import zipfile
import subprocess
import asyncio
import json
import math
import shutil
import uuid
from pathlib import Path

import gradio as gr
import edge_tts
import yt_dlp
from google import genai


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

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural",
}

RATIOS = ["9:16", "3:4", "16:9", "1:1"]

WORK_DIR = os.path.abspath("one_clip_workspace")
os.makedirs(WORK_DIR, exist_ok=True)


# =========================================================
# GENERAL HELPERS
# =========================================================

def safe_filename(name):
    name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", str(name))
    return name[:100]


def run_cmd(cmd, timeout=600):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])

    return result


def save_api_key(api_key):
    global SAVED_API_KEY

    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"

    return "⚠️ Gemini API Key ထည့်ပေးပါ။"


# =========================================================
# VIDEO HELPERS
# =========================================================

def get_video_duration(video_path):
    if not video_path or not os.path.exists(video_path):
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
            timeout=30
        )

        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())

    except Exception as e:
        print("Duration Error:", e)

    return None


def get_video_info(video_path):
    duration = get_video_duration(video_path)

    width = 0
    height = 0
    fps = 30

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate",
                "-of",
                "json",
                video_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )

        data = json.loads(result.stdout)

        stream = data["streams"][0]

        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))

        rate = stream.get("r_frame_rate", "30/1")

        if "/" in rate:
            a, b = rate.split("/")
            if float(b) != 0:
                fps = float(a) / float(b)

    except Exception as e:
        print("Video info error:", e)

    return {
        "duration": duration or 0,
        "width": width,
        "height": height,
        "fps": fps
    }


def validate_video_duration(video_path):
    duration = get_video_duration(video_path)

    if duration is None:
        return True, "ℹ️ Video duration ကို စစ်ဆေးနေပါသည်..."

    minutes = duration / 60

    if minutes > MAX_VIDEO_MINUTES:
        return (
            False,
            f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ "
            f"အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ လက်ခံပါသည်။"
        )

    return True, f"✅ Video Length: {minutes:.1f} မိနစ်"


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_video_from_link(link):
    if not link or not link.strip():
        return None

    unique = uuid.uuid4().hex[:8]

    output_template = os.path.join(
        WORK_DIR,
        f"download_{unique}.%(ext)s"
    )

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "overwrites": True,
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                link.strip(),
                download=True
            )

            filename = ydl.prepare_filename(info)

            if os.path.exists(filename):
                return filename

            base = os.path.splitext(filename)[0]

            for ext in [
                ".mp4",
                ".mkv",
                ".webm",
                ".mov"
            ]:
                candidate = base + ext

                if os.path.exists(candidate):
                    return candidate

    except Exception as e:
        print("Download Error:", e)

    return None


# =========================================================
# SCRIPT CLEANING
# =========================================================

def clean_script_for_tts(script_text):
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
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue|Intro)\]"
            r"\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        line = re.sub(
            r"^\s*Narrator\s*:\s*",
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

        cleaned.append(line.strip())

    return "\n".join(cleaned)


# =========================================================
# SRT
# =========================================================

def seconds_to_srt_time(seconds):
    milliseconds = int(round((seconds - int(seconds)) * 1000))

    total = int(seconds)

    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


def generate_srt_and_zip(
    script_text,
    prefix="myanmar_recap"
):
    clean_text = clean_script_for_tts(script_text)

    if not clean_text:
        return None, None

    lines = [
        x.strip()
        for x in clean_text.splitlines()
        if x.strip()
    ]

    srt_content = ""

    current_time = 0.0

    for idx, line in enumerate(lines, 1):

        duration = max(
            2,
            min(
                8,
                round(len(line) / 11)
            )
        )

        start_time = current_time
        end_time = current_time + duration

        srt_content += (
            f"{idx}\n"
            f"{seconds_to_srt_time(start_time)} --> "
            f"{seconds_to_srt_time(end_time)}\n"
            f"{line}\n\n"
        )

        current_time = end_time

    srt_filename = os.path.join(
        WORK_DIR,
        f"{safe_filename(prefix)}_subtitle.srt"
    )

    zip_filename = os.path.join(
        WORK_DIR,
        f"{safe_filename(prefix)}_subtitle.zip"
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
            arcname=os.path.basename(srt_filename)
        )

    return srt_filename, zip_filename


# =========================================================
# RATIO
# =========================================================

def ratio_dimensions(ratio):
    if ratio == "9:16":
        return 1080, 1920

    if ratio == "3:4":
        return 1080, 1440

    if ratio == "16:9":
        return 1920, 1080

    return 1080, 1080


def get_ratio_css(
    ratio,
    container_id="tab1_preview_container"
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
            "max_w": "320px"
        },
    }

    cfg = configs.get(
        ratio,
        configs["1:1"]
    )

    return f"""
    <style>
    #{container_id} {{
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: auto !important;
    }}

    #{container_id} .video-container {{
        width: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        height: auto !important;
        background: #000 !important;
        border-radius: 12px !important;
        overflow: hidden !important;
    }}

    #{container_id} video {{
        width: 100% !important;
        height: 100% !important;
        object-fit: cover !important;
    }}
    </style>
    """


# =========================================================
# GEMINI PROMPT
# =========================================================

def build_recap_prompt(selected_ratio):

    return f"""
သင်သည် Professional Movie Recap Scriptwriter ဖြစ်သည်။

Target Video Frame Ratio: {selected_ratio}

ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ သေချာကြည့်ရှု
နားထောင်ပြီး မြန်မာ Movie Recap Script ရေးပါ။

စည်းမျဉ်းများ

၁။ Video ထဲမှာ တကယ်ဖြစ်ပျက်သော အဖြစ်အပျက်များကိုသာ
အချိန်အစဉ်လိုက်ရေးပါ။

၂။ Character dialogue များကို သဘာဝကျသော
မြန်မာစကားပြောပုံစံဖြင့် ပြန်ဆိုပါ။

၃။ Visual, Scene, Narrator, Dialogue, Intro
စသည့် technical labels မထည့်ပါနှင့်။

၄။ TTS ဖတ်ရန် လွယ်ကူအောင် စာကြောင်းတိုတို
ရှင်းရှင်းရေးပါ။

၅။ အပိုရှင်းပြချက်၊ title၊ heading မထည့်ပါနှင့်။
"""


# =========================================================
# GEMINI GENERATION
# =========================================================

def generate_with_retry(
    client,
    uploaded_file,
    prompt
):

    retry_delays = [3, 7]

    last_error = None

    for model_name in GEMINI_MODELS:

        for attempt in range(
            len(retry_delays) + 1
        ):

            try:

                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        uploaded_file,
                        prompt
                    ],
                )

                if response and response.text:
                    return (
                        response.text,
                        model_name
                    )

            except Exception as e:

                last_error = e

                if attempt < len(retry_delays):
                    time.sleep(
                        retry_delays[attempt]
                    )

    raise RuntimeError(
        f"All Gemini models failed. "
        f"Last error: {last_error}"
    )


def run_gemini_video_analysis(
    target_media,
    ratio_choice
):

    global SAVED_API_KEY

    if not SAVED_API_KEY:
        raise ValueError(
            "Gemini API Key မရှိသေးပါ။ "
            "API Key Setting ထဲတွင် ထည့်ပေးပါ။"
        )

    valid, msg = validate_video_duration(
        target_media
    )

    if not valid:
        raise ValueError(msg)

    client = genai.Client(
        api_key=SAVED_API_KEY
    )

    uploaded_file = client.files.upload(
        file=target_media
    )

    start_wait = time.time()

    while True:

        if (
            uploaded_file.state
            and uploaded_file.state.name == "ACTIVE"
        ):
            break

        if (
            uploaded_file.state
            and uploaded_file.state.name == "FAILED"
        ):
            raise RuntimeError(
                "Gemini Video Processing မအောင်မြင်ပါ။"
            )

        if time.time() - start_wait > 900:
            raise TimeoutError(
                "Gemini Video Processing ကြာမြင့်လွန်းနေပါသည်။"
            )

        time.sleep(3)

        uploaded_file = client.files.get(
            name=uploaded_file.name
        )

    prompt = build_recap_prompt(
        ratio_choice
    )

    script_text, used_model = generate_with_retry(
        client,
        uploaded_file,
        prompt
    )

    clean_text = clean_script_for_tts(
        script_text
    )

    return (
        clean_text,
        used_model,
        msg
    )


# =========================================================
# TTS
# =========================================================

async def generate_myanmar_tts(
    text,
    voice_choice,
    speed_percent,
    output_name="tab2_output.mp3"
):

    clean_text = clean_script_for_tts(text)

    if not clean_text:
        return None, None, None

    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )

    rate_str = f"{int(speed_percent):+d}%"

    communicate = edge_tts.Communicate(
        clean_text,
        selected_voice,
        rate=rate_str
    )

    output_path = os.path.join(
        WORK_DIR,
        output_name
    )

    await communicate.save(
        output_path
    )

    srt_file, zip_file = (
        generate_srt_and_zip(
            clean_text,
            prefix=output_name.replace(
                ".mp3",
                ""
            )
        )
    )

    return (
        output_path,
        srt_file,
        zip_file
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
        v_file
        if v_file
        else download_video_from_link(v_url)
    )

    if (
        not target
        or not os.path.exists(target)
    ):
        return (
            "",
            "",
            "⚠️ Video ရှာမတွေ့ပါ။ "
            "ဖိုင် သို့မဟုတ် Link ထည့်ပါ။",
            None,
            None,
            None
        )

    try:

        clean_text, model, dur_msg = (
            run_gemini_video_analysis(
                target,
                ratio
            )
        )

        srt, zip_f = (
            generate_srt_and_zip(
                clean_text
            )
        )

        status = (
            f"✅ Script ရေးသားပြီးပါပြီ!\n"
            f"Model: {model}\n"
            f"{dur_msg}"
        )

        return (
            clean_text,
            clean_text,
            status,
            srt,
            zip_f,
            target
        )

    except Exception as e:

        return (
            "",
            "",
            f"❌ Error: {str(e)}",
            None,
            None,
            target
        )


# =========================================================
# TAB 2
# =========================================================

def tab2_tts(
    text,
    voice,
    speed
):

    try:

        mp3, srt, zip_f = asyncio.run(
            generate_myanmar_tts(
                text,
                voice,
                speed
            )
        )

        return (
            mp3,
            mp3,
            srt,
            zip_f
        )

    except Exception as e:

        print(
            "Tab 2 Error:",
            e
        )

        return (
            None,
            None,
            None,
            None
        )


# =========================================================
# TAB 3 - PREVIEW CSS
# =========================================================

def tab3_preview_css(
    ratio,
    zoom,
    x_position,
    y_position,
    brightness,
    subtitle_color,
    subtitle_size,
    subtitle_x,
    subtitle_y,
    blur_strength,
    blur_color,
    subtitle_bg,
    subtitle_bg_opacity
):

    width, height = ratio_dimensions(
        ratio
    )

    # Normalize position
    x = float(x_position)
    y = float(y_position)

    scale = float(zoom) / 100.0

    brightness_value = (
        float(brightness) / 100.0
    )

    # CSS brightness
    css_brightness = max(
        0.1,
        1.0 + brightness_value
    )

    bg_opacity = (
        float(subtitle_bg_opacity)
        / 100.0
    )

    return f"""
    <style>

    #oneclip-preview {{
        position: relative;
        width: min(100%, 600px);
        margin: auto;
        background: #000;
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 8px 30px rgba(0,0,0,.35);
    }}

    #oneclip-stage {{
        position: relative;
        width: 100%;
        aspect-ratio: {width} / {height};
        overflow: hidden;
        background: #000;
    }}

    #oneclip-stage video {{
        position: absolute;
        left: {50 + x / 2}%;
        top: {50 + y / 2}%;
        width: 100%;
        height: 100%;
        transform:
            translate(-50%, -50%)
            scale({scale});
        object-fit: cover;
        filter: brightness({css_brightness});
    }}

    #oneclip-blur {{
        position: absolute;
        left: 5%;
        right: 5%;
        bottom: 17%;
        height: 12%;
        background: {blur_color};
        opacity: {min(1, max(0, float(blur_strength) / 100))};
        filter: blur(10px);
        border-radius: 10px;
        pointer-events: none;
    }}

    #oneclip-subtitle {{
        position: absolute;
        left: {50 + float(subtitle_x) / 2}%;
        top: {50 + float(subtitle_y) / 2}%;
        transform: translate(-50%, -50%);
        width: 88%;
        text-align: center;
        color: {subtitle_color};
        font-size: {int(subtitle_size)}px;
        font-weight: 800;
        line-height: 1.25;
        text-shadow:
            2px 2px 3px #000,
            -1px -1px 2px #000;
        background: {subtitle_bg};
        opacity: {bg_opacity};
        padding: 8px 12px;
        border-radius: 8px;
        z-index: 10;
        box-sizing: border-box;
    }}

    #oneclip-safe-area {{
        position: absolute;
        left: 5%;
        right: 5%;
        top: 8%;
        bottom: 8%;
        border: 1px dashed rgba(255,255,255,.35);
        pointer-events: none;
        z-index: 20;
    }}

    </style>

    <div id="oneclip-preview">

        <div id="oneclip-stage">

            <div id="oneclip-safe-area"></div>

            <video
                id="oneclip-preview-video"
                controls
                playsinline>
            </video>

            <div id="oneclip-blur"></div>

            <div id="oneclip-subtitle">
                အရင်းဆုံး နမူနာစာ<br>
                မြန်မာ Movie Recap Subtitle
            </div>

        </div>

    </div>

    <script>

    const stage =
        document.getElementById(
            "oneclip-stage"
        );

    </script>
    """


# =========================================================
# TAB 3 - CREATE SRT FROM SCRIPT
# =========================================================

def make_two_line_subtitle(text):
    words = text.strip().split()

    if len(words) < 4:
        return text.strip()

    mid = len(words) // 2

    return (
        " ".join(words[:mid])
        + "\\N"
        + " ".join(words[mid:])
    )


def create_ass_subtitle(
    script,
    output_file,
    video_duration,
    font_size=52,
    color="&H00FFFFFF",
    margin_v=100
):

    lines = [
        x.strip()
        for x in clean_script_for_tts(
            script
        ).splitlines()
        if x.strip()
    ]

    if not lines:
        return None

    each = (
        video_duration / len(lines)
        if video_duration > 0
        else 4
    )

    def ass_time(seconds):
        seconds = max(
            0,
            float(seconds)
        )

        h = int(seconds // 3600)
        m = int(
            (seconds % 3600) // 60
        )
        s = int(seconds % 60)
        cs = int(
            (seconds - int(seconds))
            * 100
        )

        return (
            f"{h}:{m:02d}:"
            f"{s:02d}.{cs:02d}"
        )

    content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font},{primary},&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,2,2,60,60,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".format(
        font=int(font_size),
        primary=color,
        margin=int(margin_v)
    )

    for i, line in enumerate(lines):

        start = i * each
        end = (
            (i + 1) * each
        )

        subtitle = (
            make_two_line_subtitle(
                line
            )
            .replace(
                "\n",
                r"\N"
            )
        )

        content += (
            f"Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            f"Default,,0,0,0,,"
            f"{subtitle}\n"
        )

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(content)

    return output_file


# =========================================================
# TAB 3 - FFMPEG FILTER
# =========================================================

def escape_filter_path(path):
    return (
        str(path)
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def build_video_filter(
    ratio,
    zoom,
    x_pos,
    y_pos,
    brightness,
    crop_top,
    crop_bottom,
    crop_left,
    crop_right
):

    target_w, target_h = ratio_dimensions(
        ratio
    )

    zoom = max(
        1.0,
        float(zoom) / 100.0
    )

    brightness = (
        float(brightness) / 100.0
    )

    crop_top = float(crop_top)
    crop_bottom = float(crop_bottom)
    crop_left = float(crop_left)
    crop_right = float(crop_right)

    filters = []

    # -----------------------------------------------------
    # Crop
    # -----------------------------------------------------

    crop_w = (
        f"iw*(1-{crop_left + crop_right:.4f})"
    )

    crop_h = (
        f"ih*(1-{crop_top + crop_bottom:.4f})"
    )

    crop_x = (
        f"iw*{crop_left:.4f}"
    )

    crop_y = (
        f"ih*{crop_top:.4f}"
    )

    filters.append(
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
    )

    # -----------------------------------------------------
    # Zoom
    # -----------------------------------------------------

    if zoom != 1:

        filters.append(
            f"scale="
            f"iw*{zoom}:"
            f"ih*{zoom}"
        )

    # -----------------------------------------------------
    # Position
    # -----------------------------------------------------

    # Keep output center adjustable.
    # FFmpeg crop after zoom provides stable positioning.

    if abs(float(x_pos)) > 0.1 or abs(float(y_pos)) > 0.1:

        px = float(x_pos) / 100.0
        py = float(y_pos) / 100.0

        crop_expression = (
            f"crop="
            f"min(iw,{target_w}):"
            f"min(ih,{target_h}):"
            f"(iw-min(iw,{target_w}))/2+({px}*iw):"
            f"(ih-min(ih,{target_h}))/2+({py}*ih)"
        )

        filters.append(
            crop_expression
        )

    # -----------------------------------------------------
    # Scale / Pad
    # -----------------------------------------------------

    filters.append(
        f"scale={target_w}:{target_h}:"
        f"force_original_aspect_ratio=increase"
    )

    filters.append(
        f"crop={target_w}:{target_h}"
    )

    # -----------------------------------------------------
    # Brightness
    # -----------------------------------------------------

    if abs(brightness) > 0.001:

        filters.append(
            f"eq=brightness={brightness:.3f}"
        )

    return ",".join(filters)


# =========================================================
# TAB 3 - RENDER
# =========================================================

def render_one_clip(
    video_file,
    tts_audio,
    bg_music,
    ratio,
    zoom,
    x_position,
    y_position,
    brightness,
    crop_top,
    crop_bottom,
    crop_left,
    crop_right,
    original_audio,
    original_volume,
    voice_volume,
    bgm_volume,
    subtitle_text,
    subtitle_size,
    subtitle_color,
    subtitle_position,
    blur_original,
    blur_strength,
    blur_color,
    resolution
):

    if not video_file:
        raise ValueError(
            "Video file မရှိပါ။"
        )

    if not os.path.exists(video_file):
        raise ValueError(
            "Video file ရှာမတွေ့ပါ။"
        )

    job_id = uuid.uuid4().hex[:10]

    output_file = os.path.join(
        WORK_DIR,
        f"one_clip_{job_id}.mp4"
    )

    info = get_video_info(
        video_file
    )

    duration = info["duration"]

    # -----------------------------------------------------
    # Resolution
    # -----------------------------------------------------

    if resolution == "480p":
        target_w, target_h = ratio_dimensions(
            ratio
        )

        target_h = 480
        target_w = int(
            target_h *
            target_w /
            ratio_dimensions(ratio)[1]
        )

    elif resolution == "720p":
        target_w, target_h = ratio_dimensions(
            ratio
        )

        target_h = 720
        target_w = int(
            target_h *
            target_w /
            ratio_dimensions(ratio)[1]
        )

    else:
        target_w, target_h = ratio_dimensions(
            ratio
        )

    # force even
    target_w -= target_w % 2
    target_h -= target_h % 2

    # -----------------------------------------------------
    # Video filter
    # -----------------------------------------------------

    vf = build_video_filter(
        ratio,
        zoom,
        x_position,
        y_position,
        brightness,
        crop_top,
        crop_bottom,
        crop_left,
        crop_right
    )

    # Replace final output scale with selected resolution
    vf += (
        f",scale={target_w}:{target_h}"
    )

    # -----------------------------------------------------
    # Subtitle
    # -----------------------------------------------------

    subtitle_file = None

    if subtitle_text:

        subtitle_file = os.path.join(
            WORK_DIR,
            f"subtitle_{job_id}.ass"
        )

        ass_color = "&H00FFFFFF"

        color_map = {
            "White": "&H00FFFFFF",
            "Yellow": "&H0000FFFF",
            "Green": "&H0000FF00",
            "Red": "&H000000FF",
            "Blue": "&H00FF0000",
            "Pink": "&H00FF00FF",
            "Orange": "&H0000A5FF",
        }

        ass_color = color_map.get(
            subtitle_color,
            "&H00FFFFFF"
        )

        position_map = {
            "အပေါ်": 180,
            "အလယ်": 960,
            "အောက်": 1680,
        }

        margin_v = position_map.get(
            subtitle_position,
            1680
        )

        create_ass_subtitle(
            subtitle_text,
            subtitle_file,
            duration,
            font_size=subtitle_size,
            color=ass_color,
            margin_v=margin_v
        )

        vf += (
            f",ass='{escape_filter_path(subtitle_file)}'"
        )

    # -----------------------------------------------------
    # Original audio
    # -----------------------------------------------------

    has_voice = (
        tts_audio
        and os.path.exists(tts_audio)
    )

    has_bgm = (
        bg_music
        and os.path.exists(bg_music)
    )

    audio_inputs = 1

    if has_voice:
        audio_inputs += 1

    if has_bgm:
        audio_inputs += 1

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_file
    ]

    # Voice
    if has_voice:
        cmd += [
            "-i",
            tts_audio
        ]

    # BGM
    if has_bgm:
        cmd += [
            "-i",
            bg_music
        ]

    # -----------------------------------------------------
    # Audio filter
    # -----------------------------------------------------

    audio_filters = []

    if original_audio:

        audio_filters.append(
            f"[0:a]"
            f"volume={float(original_volume)/100:.3f}"
            f"[orig]"
        )

    if has_voice:

        voice_index = 1

        audio_filters.append(
            f"[{voice_index}:a]"
            f"volume={float(voice_volume)/100:.3f}"
            f"[voice]"
        )

    if has_bgm:

        bg_index = 2 if has_voice else 1

        audio_filters.append(
            f"[{bg_index}:a]"
            f"volume={float(bgm_volume)/100:.3f},"
            f"aloop=loop=-1:size=2e+09"
            f"[bgm]"
        )

    mix_inputs = []

    if original_audio:
        mix_inputs.append("[orig]")

    if has_voice:
        mix_inputs.append("[voice]")

    if has_bgm:
        mix_inputs.append("[bgm]")

    if len(mix_inputs) == 0:

        audio_filter_complex = None

    elif len(mix_inputs) == 1:

        audio_filter_complex = (
            "".join(audio_filters)
            + mix_inputs[0]
            + "anull[aout]"
        )

    else:

        audio_filter_complex = (
            "".join(audio_filters)
            + "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:"
            f"duration=longest:"
            f"dropout_transition=2"
            f"[aout]"
        )

    # -----------------------------------------------------
    # Final command
    # -----------------------------------------------------

    cmd += [
        "-vf",
        vf
    ]

    if audio_filter_complex:

        cmd += [
            "-filter_complex",
            audio_filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]"
        ]

    else:

        cmd += [
            "-map",
            "0:v:0",
            "-an"
        ]

    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_file
    ]

    run_cmd(
        cmd,
        timeout=1800
    )

    if not os.path.exists(output_file):
        raise RuntimeError(
            "Final video မထွက်လာပါ။"
        )

    return output_file


# =========================================================
# TAB 3 - PREVIEW EVENT
# =========================================================

def make_tab3_preview(
    video_file,
    ratio,
    zoom,
    x_position,
    y_position,
    brightness,
    subtitle_color,
    subtitle_size,
    subtitle_x,
    subtitle_y,
    blur_strength,
    blur_color,
    subtitle_bg,
    subtitle_bg_opacity
):

    if not video_file:
        return (
            tab3_preview_css(
                ratio,
                zoom,
                x_position,
                y_position,
                brightness,
                subtitle_color,
                subtitle_size,
                subtitle_x,
                subtitle_y,
                blur_strength,
                blur_color,
                subtitle_bg,
                subtitle_bg_opacity
            )
        )

    return tab3_preview_css(
        ratio,
        zoom,
        x_position,
        y_position,
        brightness,
        subtitle_color,
        subtitle_size,
        subtitle_x,
        subtitle_y,
        blur_strength,
        blur_color,
        subtitle_bg,
        subtitle_bg_opacity
    )


# =========================================================
# TAB 3 LINK LOAD
# =========================================================

def tab3_load_link(link):

    if not link:
        return None, "⚠️ Link ထည့်ပါ။"

    video = download_video_from_link(
        link
    )

    if not video:
        return (
            None,
            "❌ Video Download မအောင်မြင်ပါ။"
        )

    return (
        video,
        "✅ Video ရရှိပါပြီ။ Preview ပြနေပါပြီ။"
    )


# =========================================================
# TAB 3 AUTO LOAD FROM TAB 1
# =========================================================

def tab3_use_tab1_video(video):
    if video and os.path.exists(video):
        return (
            video,
            "✅ Tab 1 Video ကို Tab 3 သို့ ရောက်ရှိပါပြီ။"
        )

    return (
        None,
        "ℹ️ Tab 1 Video မရှိသေးပါ။"
    )


# =========================================================
# TAB 3 FINAL
# =========================================================

def tab3_generate(
    video_file,
    tts_audio,
    bg_music,
    ratio,
    zoom,
    x_position,
    y_position,
    brightness,
    crop_top,
    crop_bottom,
    crop_left,
    crop_right,
    original_audio,
    original_volume,
    voice_volume,
    bgm_volume,
    subtitle_text,
    subtitle_size,
    subtitle_color,
    subtitle_position,
    blur_original,
    blur_strength,
    blur_color,
    resolution
):

    try:

        if not video_file:
            return (
                None,
                "❌ Video မရှိပါ။"
            )

        output = render_one_clip(
            video_file,
            tts_audio,
            bg_music,
            ratio,
            zoom,
            x_position,
            y_position,
            brightness,
            crop_top,
            crop_bottom,
            crop_left,
            crop_right,
            original_audio,
            original_volume,
            voice_volume,
            bgm_volume,
            subtitle_text,
            subtitle_size,
            subtitle_color,
            subtitle_position,
            blur_original,
            blur_strength,
            blur_color,
            resolution
        )

        return (
            output,
            "✅ One Clip Video အပြီးသတ် Generate ပြီးပါပြီ။"
        )

    except Exception as e:

        print(
            "One Clip Error:",
            e
        )

        return (
            None,
            f"❌ Generate Error: {str(e)}"
        )


# =========================================================
# UI
# =========================================================

with gr.Blocks(
    title=APP_TITLE,
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown(
        f"""
# 🎬 {APP_TITLE}

**AI Video Recap Script + Myanmar Voice-Over + One Clip Video Editor**
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
                        label="📹 Video File တင်ရန်"
                    )

                    v1_url = gr.Textbox(
                        label="🔗 Video URL Link "
                               "(YouTube, TikTok, Facebook စသည်)"
                    )

                    v1_load_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary"
                    )

                    v1_ratio = gr.Radio(
                        RATIOS,
                        value="1:1",
                        label="📐 Preview Screen Aspect Ratio"
                    )

                    v1_gen_btn = gr.Button(
                        "🚀 Recap Script စတင်ထုတ်မည်",
                        variant="primary"
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
                        label="📺 Video Preview",
                        elem_id="tab1_preview_container"
                    )

                    v1_status = gr.Markdown(
                        "ဗီဒီယိုထည့်သွင်းရန် အဆင်သင့်ဖြစ်ပါသည်။"
                    )

                    v1_script_out = gr.Textbox(
                        label="🎬 ထွက်ရှိလာသော Script",
                        lines=10
                    )

                    go_to_tts_btn = gr.Button(
                        "🎙️ Tab 2 (TTS) သို့ သွားရောက် အသံထုတ်မည် ➡️",
                        variant="secondary"
                    )

            with gr.Row():

                v1_srt = gr.File(
                    label="📄 SRT စာတန်းထိုး ဖိုင်"
                )

                v1_zip = gr.File(
                    label="📦 SRT ZIP ဒေါင်းလုဒ်"
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
                        label=(
                            "🎙️ Burmese Script "
                            "(Tab 1 မှ အလိုအလျောက် ရောက်ရှိပါမည်)"
                        ),
                        lines=12
                    )

                    v2_voice = gr.Dropdown(
                        list(VOICES.keys()),
                        value=(
                            "Thiha "
                            "(အမျိုးသားအသံ) - Natural"
                        ),
                        label="🎤 အသံ ရွေးချယ်ပါ"
                    )

                    v2_speed = gr.Slider(
                        -30,
                        50,
                        value=5,
                        step=1,
                        label="⚡ Speed (%)"
                    )

                    v2_btn = gr.Button(
                        "⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်",
                        variant="primary"
                    )

                with gr.Column(
                    scale=1
                ):

                    v2_audio = gr.Audio(
                        label="🔊 Voice Preview",
                        autoplay=True
                    )

                    v2_mp3 = gr.File(
                        label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်"
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
            "3️⃣ One Clip Video Studio",
            id="tab_oneclip"
        ):

            gr.Markdown(
                """
## 🎬 One Clip Video Studio

**Tab 1 Video + Tab 2 Voice + Subtitle + Crop + Zoom + Audio + BGM
အားလုံးကို တစ်နေရာတည်းတွင် Final Video ပြုလုပ်နိုင်ပါသည်။**

Preview ထဲတွင် ရွေးချယ်ထားသော Video / Ratio / Zoom /
Brightness / Subtitle style များကို တစ်ပြိုင်နက်မြင်နိုင်ပါသည်။
"""
            )

            # ---------------------------------------------
            # SOURCE
            # ---------------------------------------------

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    tab3_video = gr.Video(
                        label="📹 Video File / Tab 1 Video"
                    )

                    tab3_url = gr.Textbox(
                        label="🔗 YouTube / TikTok Link"
                    )

                    tab3_load_btn = gr.Button(
                        "⬇️ Link မှ Video ရယူမည်"
                    )

                    tab3_from_tab1_btn = gr.Button(
                        "📥 Tab 1 Video ကို အသုံးပြုမည်"
                    )

                    tab3_source_status = gr.Markdown(
                        ""
                    )

                with gr.Column(
                    scale=1
                ):

                    tab3_preview = gr.HTML(
                        tab3_preview_css(
                            "9:16",
                            100,
                            0,
                            0,
                            0,
                            "White",
                            52,
                            0,
                            35,
                            40,
                            "#000000",
                            "#000000",
                            65
                        )
                    )

            # ---------------------------------------------
            # RATIO / VIDEO
            # ---------------------------------------------

            gr.Markdown(
                "### 📐 Video Frame / Movement"
            )

            with gr.Row():

                tab3_ratio = gr.Radio(
                    RATIOS,
                    value="9:16",
                    label="Aspect Ratio"
                )

                tab3_zoom = gr.Slider(
                    50,
                    200,
                    value=100,
                    step=1,
                    label="🔍 Zoom (%)"
                )

            with gr.Row():

                tab3_x = gr.Slider(
                    -100,
                    100,
                    value=0,
                    step=1,
                    label="↔️ Video Left / Right"
                )

                tab3_y = gr.Slider(
                    -100,
                    100,
                    value=0,
                    step=1,
                    label="↕️ Video Up / Down"
                )

                tab3_brightness = gr.Slider(
                    -100,
                    100,
                    value=0,
                    step=1,
                    label="☀️ Brightness"
                )

            # ---------------------------------------------
            # CROP
            # ---------------------------------------------

            gr.Markdown(
                "### ✂️ Crop"
            )

            with gr.Row():

                tab3_crop_top = gr.Slider(
                    0,
                    40,
                    value=0,
                    step=1,
                    label="⬆️ Crop Top (%)"
                )

                tab3_crop_bottom = gr.Slider(
                    0,
                    40,
                    value=0,
                    step=1,
                    label="⬇️ Crop Bottom (%)"
                )

            with gr.Row():

                tab3_crop_left = gr.Slider(
                    0,
                    40,
                    value=0,
                    step=1,
                    label="⬅️ Crop Left (%)"
                )

                tab3_crop_right = gr.Slider(
                    0,
                    40,
                    value=0,
                    step=1,
                    label="➡️ Crop Right (%)"
                )

            # ---------------------------------------------
            # ORIGINAL SUBTITLE BLUR
            # ---------------------------------------------

            gr.Markdown(
                "### 🫥 မူရင်း Subtitle Blur"
            )

            with gr.Row():

                tab3_blur_original = gr.Checkbox(
                    value=True,
                    label="မူရင်း Video စာတန်းကို Blur လုပ်မည်"
                )

                tab3_blur_strength = gr.Slider(
                    0,
                    100,
                    value=40,
                    step=1,
                    label="Blur Strength"
                )

                tab3_blur_color = gr.ColorPicker(
                    value="#000000",
                    label="Blur Color"
                )

            # ---------------------------------------------
            # MYANMAR SUBTITLE
            # ---------------------------------------------

            gr.Markdown(
                "### 📝 Myanmar Subtitle"
            )

            with gr.Row():

                tab3_subtitle_text = gr.Textbox(
                    label="Subtitle / Tab 1 Script",
                    lines=5
                )

                tab3_subtitle_color = gr.Dropdown(
                    [
                        "White",
                        "Yellow",
                        "Green",
                        "Red",
                        "Blue",
                        "Pink",
                        "Orange"
                    ],
                    value="White",
                    label="စာလုံးအရောင်"
                )

            with gr.Row():

                tab3_subtitle_size = gr.Slider(
                    24,
                    100,
                    value=52,
                    step=1,
                    label="🔠 စာလုံးအရွယ်အစား"
                )

                tab3_subtitle_position = gr.Radio(
                    [
                        "အပေါ်",
                        "အလယ်",
                        "အောက်"
                    ],
                    value="အောက်",
                    label="↕️ Subtitle Position"
                )

            with gr.Row():

                tab3_subtitle_x = gr.Slider(
                    -100,
                    100,
                    value=0,
                    step=1,
                    label="↔️ Subtitle Left / Right"
                )

                tab3_subtitle_y = gr.Slider(
                    -100,
                    100,
                    value=35,
                    step=1,
                    label="↕️ Subtitle Up / Down"
                )

            with gr.Row():

                tab3_subtitle_bg = gr.ColorPicker(
                    value="#000000",
                    label="Subtitle Background"
                )

                tab3_subtitle_bg_opacity = gr.Slider(
                    0,
                    100,
                    value=65,
                    step=1,
                    label="Background Opacity (%)"
                )

            gr.Markdown(
                """
**Subtitle Preview:**  
စာတစ်ကြောင်းကို Auto split လုပ်ပြီး **အပေါ်တစ်ကြောင်း + အောက်တစ်ကြောင်း**
၂ ကြောင်းပုံစံနဲ့ ပြပေးမည်။
"""
            )

            # ---------------------------------------------
            # VOICE
            # ---------------------------------------------

            gr.Markdown(
                "### 🎙️ Voice Over"
            )

            with gr.Row():

                tab3_voice = gr.Audio(
                    label="🎤 Tab 2 Voice / MP3",
                    type="filepath"
                )

                tab3_voice_volume = gr.Slider(
                    0,
                    200,
                    value=100,
                    step=1,
                    label="Voice Volume (%)"
                )

            # ---------------------------------------------
            # ORIGINAL AUDIO
            # ---------------------------------------------

            gr.Markdown(
                "### 🔊 Original Video Audio"
            )

            with gr.Row():

                tab3_original_audio = gr.Checkbox(
                    value=False,
                    label="Original Audio ဖွင့်မည်"
                )

                tab3_original_volume = gr.Slider(
                    0,
                    200,
                    value=30,
                    step=1,
                    label="Original Audio Volume (%)"
                )

            # ---------------------------------------------
            # BGM
            # ---------------------------------------------

            gr.Markdown(
                "### 🎵 Background Music"
            )

            with gr.Row():

                tab3_bgm = gr.Audio(
                    label="🎵 BGM File",
                    type="filepath"
                )

                tab3_bgm_volume = gr.Slider(
                    0,
                    200,
                    value=20,
                    step=1,
                    label="BGM Volume (%)"
                )

            # ---------------------------------------------
            # OUTPUT
            # ---------------------------------------------

            gr.Markdown(
                "### 🎞️ Final Output"
            )

            with gr.Row():

                tab3_resolution = gr.Radio(
                    [
                        "480p",
                        "720p",
                        "1080p"
                    ],
                    value="1080p",
                    label="Quality"
                )

                tab3_generate_btn = gr.Button(
                    "🚀 ONE CLIP VIDEO GENERATE",
                    variant="primary",
                    size="lg"
                )

            tab3_status = gr.Markdown(
                ""
            )

            tab3_output = gr.Video(
                label="🎬 Final One Clip Video",
                autoplay=False
            )

            tab3_download = gr.File(
                label="⬇️ Final MP4 Download"
            )

    # =====================================================
    # EVENT BINDINGS
    # =====================================================

    # TAB 1 preview
    v1_file.change(
        lambda f: f,
        inputs=v1_file,
        outputs=v1_preview
    )

    v1_load_btn.click(
        download_video_from_link,
        inputs=v1_url,
        outputs=v1_preview
    )

    v1_ratio.change(
        lambda r: get_ratio_css(
            r,
            "tab1_preview_container"
        ),
        inputs=v1_ratio,
        outputs=v1_css
    )

    # TAB 1 generate
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
            v1_zip,
            tab3_video
        ]
    )

    # Go Tab 2
    go_to_tts_btn.click(
        lambda: gr.Tabs(
            selected="tab_tts"
        ),
        outputs=main_tabs
    )

    # TAB 2
    v2_btn.click(
        tab2_tts,
        inputs=[
            v2_input_text,
            v2_voice,
            v2_speed
        ],
        outputs=[
            v2_audio,
            v2_mp3,
            v2_srt,
            v2_zip
        ]
    )

    # =====================================================
    # TAB 3 SOURCE
    # =====================================================

    tab3_load_btn.click(
        tab3_load_link,
        inputs=tab3_url,
        outputs=[
            tab3_video,
            tab3_source_status
        ]
    )

    tab3_from_tab1_btn.click(
        tab3_use_tab1_video,
        inputs=tab3_video,
        outputs=[
            tab3_video,
            tab3_source_status
        ]
    )

    # Tab 2 voice -> Tab 3 voice
    v2_audio.change(
        lambda x: x,
        inputs=v2_audio,
        outputs=tab3_voice
    )

    # Tab 1 script -> Tab 3 subtitle
    v1_script_out.change(
        lambda x: x,
        inputs=v1_script_out,
        outputs=tab3_subtitle_text
    )

    # =====================================================
    # TAB 3 LIVE PREVIEW
    # =====================================================

    preview_inputs = [
        tab3_video,
        tab3_ratio,
        tab3_zoom,
        tab3_x,
        tab3_y,
        tab3_brightness,
        tab3_subtitle_color,
        tab3_subtitle_size,
        tab3_subtitle_x,
        tab3_subtitle_y,
        tab3_blur_strength,
        tab3_blur_color,
        tab3_subtitle_bg,
        tab3_subtitle_bg_opacity
    ]

    preview_controls = [
        tab3_ratio,
        tab3_zoom,
        tab3_x,
        tab3_y,
        tab3_brightness,
        tab3_subtitle_color,
        tab3_subtitle_size,
        tab3_subtitle_x,
        tab3_subtitle_y,
        tab3_blur_strength,
        tab3_blur_color,
        tab3_subtitle_bg,
        tab3_subtitle_bg_opacity
    ]

    for control in preview_controls:

        control.change(
            make_tab3_preview,
            inputs=preview_inputs,
            outputs=tab3_preview
        )

    tab3_video.change(
        make_tab3_preview,
        inputs=preview_inputs,
        outputs=tab3_preview
    )

    # =====================================================
    # TAB 3 FINAL GENERATE
    # =====================================================

    tab3_generate_btn.click(
        tab3_generate,
        inputs=[
            tab3_video,
            tab3_voice,
            tab3_bgm,
            tab3_ratio,
            tab3_zoom,
            tab3_x,
            tab3_y,
            tab3_brightness,
            tab3_crop_top,
            tab3_crop_bottom,
            tab3_crop_left,
            tab3_crop_right,
            tab3_original_audio,
            tab3_original_volume,
            tab3_voice_volume,
            tab3_bgm_volume,
            tab3_subtitle_text,
            tab3_subtitle_size,
            tab3_subtitle_color,
            tab3_subtitle_position,
            tab3_blur_original,
            tab3_blur_strength,
            tab3_blur_color,
            tab3_resolution
        ],
        outputs=[
            tab3_output,
            tab3_status
        ]
    )

    # Final output -> download
    tab3_output.change(
        lambda x: x,
        inputs=tab3_output,
        outputs=tab3_download
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
        server_port=port
    )
