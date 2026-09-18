import os
import re
import time
import zipfile
import subprocess
import asyncio
import shutil
import uuid
import math
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

WORK_DIR = os.path.abspath("one_clip_workspace")
os.makedirs(WORK_DIR, exist_ok=True)

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural",
}

RATIO_MAP = {
    "9:16": (9, 16),
    "3:4": (3, 4),
    "16:9": (16, 9),
    "1:1": (1, 1),
}


# =========================================================
# GENERAL HELPERS
# =========================================================

def safe_filename(name):
    name = str(name or "output")
    name = re.sub(r"[^a-zA-Z0-9_\-.]+", "_", name)
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
        raise RuntimeError(
            result.stderr[-5000:] if result.stderr else "FFmpeg command failed"
        )

    return result


def ffmpeg_exists():
    return shutil.which("ffmpeg") is not None


def ffprobe_exists():
    return shutil.which("ffprobe") is not None


def save_api_key(api_key):
    global SAVED_API_KEY

    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"

    return "⚠️ Gemini API Key ထည့်ပေးပါ။"


# =========================================================
# VIDEO INFORMATION
# =========================================================

def get_video_duration(video_path):
    if not video_path or not os.path.exists(video_path):
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


def get_video_resolution(video_path):
    if not video_path or not os.path.exists(video_path):
        return 1920, 1080

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                video_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            text = result.stdout.strip()

            if "x" in text:
                w, h = text.split("x")
                return int(w), int(h)

    except Exception as e:
        print("Resolution Error:", e)

    return 1920, 1080


def validate_video_duration(video_path):
    duration = get_video_duration(video_path)

    if duration is None:
        return True, "ℹ️ Video duration ကို စစ်ဆေးမရပါ။"

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

    unique_id = uuid.uuid4().hex[:10]

    output_template = os.path.join(
        WORK_DIR,
        f"download_{unique_id}.%(ext)s"
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
            info = ydl.extract_info(link.strip(), download=True)

            filename = ydl.prepare_filename(info)

            if os.path.exists(filename):
                return filename

            base = os.path.splitext(filename)[0]

            for ext in [
                ".mp4",
                ".mkv",
                ".webm",
                ".mov",
                ".avi"
            ]:
                candidate = base + ext

                if os.path.exists(candidate):
                    return candidate

    except Exception as e:
        print("Download Error:", e)

    return None


def load_url_for_tab1(link):
    path = download_video_from_link(link)

    if path:
        return path, "✅ Video ကို Download လုပ်ပြီးပါပြီ။"

    return None, "❌ Link မှ Video ရယူ၍ မရပါ။"


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
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue|Intro)\]\s*[:\-]?\s*",
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

        cleaned.append(line)

    return "\n".join(cleaned)


# =========================================================
# SRT
# =========================================================

def seconds_to_srt_time(seconds):
    seconds = max(0, float(seconds))

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    milliseconds = int((seconds - int(seconds)) * 1000)

    return (
        f"{hours:02d}:{minutes:02d}:{secs:02d},"
        f"{milliseconds:03d}"
    )


def split_caption_two_lines(text, max_chars=34):
    text = text.strip()

    if len(text) <= max_chars:
        return text

    words = text.split()

    if len(words) <= 1:
        middle = len(text) // 2
        return text[:middle] + "\n" + text[middle:]

    best_index = 1
    best_diff = 999999

    for i in range(1, len(words)):
        line1 = " ".join(words[:i])
        line2 = " ".join(words[i:])

        diff = abs(len(line1) - len(line2))

        if diff < best_diff:
            best_diff = diff
            best_index = i

    return (
        " ".join(words[:best_index])
        + "\n"
        + " ".join(words[best_index:])
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
            2.0,
            min(
                8.0,
                len(line) / 11.0
            )
        )

        start_time = current_time
        end_time = current_time + duration

        caption = split_caption_two_lines(line)

        srt_content += (
            f"{idx}\n"
            f"{seconds_to_srt_time(start_time)} --> "
            f"{seconds_to_srt_time(end_time)}\n"
            f"{caption}\n\n"
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
# GEMINI
# =========================================================

def build_recap_prompt(selected_ratio):

    return f"""
သင်သည် Professional Movie Recap Scriptwriter ဖြစ်သည်။

Target Video Frame Ratio: {selected_ratio}

ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ ကြည့်ရှုနားထောင်ပြီး
မြန်မာ Movie Recap Script ရေးပါ။

စည်းမျဉ်းများ -

၁။ Video ထဲတွင် တကယ်ဖြစ်ပျက်သောအရာကိုသာ ရေးပါ။
၂။ အဖြစ်အပျက်များကို အချိန်အစဉ်လိုက်ရေးပါ။
၃။ Dialogue များကို သဘာဝကျသော မြန်မာစကားပြောပုံစံဖြင့် ပြန်ရေးပါ။
၄။ Technical labels မထည့်ပါနှင့်။
၅။ [Visual], [Scene], [Narrator], [Dialogue] မထည့်ပါနှင့်။
၆။ TTS ဖတ်ရန် လွယ်ကူသော စာကြောင်းတိုများအသုံးပြုပါ။
၇။ Caption အဖြစ် ပြရန်လွယ်ကူအောင် စာကြောင်းများကို အလွန်ရှည်မရေးပါနှင့်။
၈။ အပိုရှင်းပြချက် မထည့်ပါနှင့်။
"""


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
            "API Key Setting ထဲတွင် ထည့်ပါ။"
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
# TAB 1
# =========================================================

def tab1_analyze(
    v_file,
    v_url,
    ratio
):

    target = v_file

    if not target and v_url:
        target = download_video_from_link(
            v_url
        )

    if not target or not os.path.exists(target):

        return (
            "",
            "",
            "⚠️ Video ရှာမတွေ့ပါ။",
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

        srt, zip_f = generate_srt_and_zip(
            clean_text
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
            zip_f
        )

    except Exception as e:

        return (
            "",
            "",
            f"❌ Error: {str(e)}",
            None,
            None
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

    clean_text = clean_script_for_tts(
        text
    )

    if not clean_text:
        return None, None, None

    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )

    rate_str = f"{int(speed_percent):+d}%"

    output_path = os.path.join(
        WORK_DIR,
        safe_filename(output_name)
    )

    communicate = edge_tts.Communicate(
        clean_text,
        selected_voice,
        rate=rate_str
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
                speed,
                "tab2_output.mp3"
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
# ASS SUBTITLE
# =========================================================

def hex_to_ass(hex_color):
    """
    #RRGGBB -> &H00BBGGRR
    """

    hex_color = (
        hex_color
        .replace("#", "")
        .strip()
    )

    if len(hex_color) != 6:
        hex_color = "FFFFFF"

    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]

    return f"&H00{b}{g}{r}"


def make_ass_file(
    script_text,
    output_path,
    video_width,
    video_height,
    font_size=48,
    color="#FFFFFF",
    outline_color="#000000",
    position="အောက်",
    margin_x=40,
    margin_y=80
):

    text = clean_script_for_tts(
        script_text
    )

    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    total_duration = max(
        1.0,
        len(lines) * 4.0
    )

    primary = hex_to_ass(color)
    outline = hex_to_ass(outline_color)

    if position == "အပေါ်":
        alignment = 8
        margin_v = int(margin_y)

    elif position == "အလယ်":
        alignment = 5
        margin_v = int(margin_y)

    else:
        alignment = 2
        margin_v = int(margin_y)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans Myanmar,{font_size},{primary},&H00000000,{outline},&H80000000,-1,0,0,0,100,100,0,0,1,3,1,{alignment},{margin_x},{margin_x},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    current = 0.0

    events = []

    for line in lines:

        duration = max(
            2.0,
            min(
                8.0,
                len(line) / 10.5
            )
        )

        start = current
        end = current + duration

        caption = split_caption_two_lines(
            line,
            max_chars=32
        )

        caption = caption.replace(
            "\n",
            r"\N"
        )

        events.append(
            "Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            f"Default,,0,0,0,,{caption}"
        )

        current = end

    with open(
        output_path,
        "w",
        encoding="utf-8-sig"
    ) as f:

        f.write(
            header
            + "\n".join(events)
            + "\n"
        )

    return output_path


def ass_time(seconds):
    seconds = max(
        0.0,
        float(seconds)
    )

    h = int(seconds // 3600)

    m = int(
        (seconds % 3600) // 60
    )

    s = int(
        seconds % 60
    )

    cs = int(
        (seconds - int(seconds)) * 100
    )

    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# =========================================================
# VIDEO FILTER HELPERS
# =========================================================

def get_output_size(ratio, quality):
    sizes = {
        "480p": {
            "9:16": (480, 854),
            "3:4": (480, 640),
            "16:9": (854, 480),
            "1:1": (480, 480),
        },

        "720p": {
            "9:16": (720, 1280),
            "3:4": (720, 960),
            "16:9": (1280, 720),
            "1:1": (720, 720),
        },

        "1080p": {
            "9:16": (1080, 1920),
            "3:4": (1080, 1440),
            "16:9": (1920, 1080),
            "1:1": (1080, 1080),
        }
    }

    return sizes.get(
        quality,
        sizes["720p"]
    ).get(
        ratio,
        sizes["720p"]["9:16"]
    )


def color_to_ffmpeg(color):
    color = str(color or "#000000")

    if color.startswith("#"):
        return color

    return "#000000"


# =========================================================
# ONE CLIP PREVIEW
# =========================================================

def create_one_clip_preview(
    video_path,
    script_text,
    ratio,
    background_type,
    background_color,
    blur_strength,
    zoom,
    pos_x,
    pos_y,
    brightness,
    contrast,
    crop_top,
    crop_bottom,
    crop_left,
    crop_right,
    subtitle_color,
    subtitle_size,
    subtitle_position,
    voice,
    voice_speed,
    voice_volume,
    original_audio,
    original_volume,
    bgm_file,
    bgm_volume
):

    if not video_path or not os.path.exists(video_path):
        raise ValueError(
            "📹 Video File သို့မဟုတ် Link ထည့်ပါ။"
        )

    if not ffmpeg_exists():
        raise RuntimeError(
            "FFmpeg မတွေ့ပါ။ Server တွင် FFmpeg install လုပ်ပါ။"
        )

    if not script_text.strip():
        raise ValueError(
            "📝 Script မရှိသေးပါ။ Tab 1 မှ Script ရယူပါ။"
        )

    job_id = uuid.uuid4().hex[:12]

    preview_video = os.path.join(
        WORK_DIR,
        f"preview_{job_id}.mp4"
    )

    voice_path = os.path.join(
        WORK_DIR,
        f"voice_{job_id}.mp3"
    )

    ass_path = os.path.join(
        WORK_DIR,
        f"caption_{job_id}.ass"
    )

    # -----------------------------------------------------
    # Generate voice
    # -----------------------------------------------------

    asyncio.run(
        generate_myanmar_tts(
            script_text,
            voice,
            voice_speed,
            f"voice_{job_id}.mp3"
        )
    )

    if not os.path.exists(voice_path):
        raise RuntimeError(
            "Voice ဖန်တီး၍ မရပါ။"
        )

    voice_duration = (
        get_video_duration(
            voice_path
        )
        or 1.0
    )

    # -----------------------------------------------------
    # Output resolution for preview
    # -----------------------------------------------------

    out_w, out_h = get_output_size(
        ratio,
        "480p"
    )

    src_w, src_h = get_video_resolution(
        video_path
    )

    # -----------------------------------------------------
    # Crop percentages
    # -----------------------------------------------------

    ct = max(
        0.0,
        min(45.0, float(crop_top))
    ) / 100.0

    cb = max(
        0.0,
        min(45.0, float(crop_bottom))
    ) / 100.0

    cl = max(
        0.0,
        min(45.0, float(crop_left))
    ) / 100.0

    cr = max(
        0.0,
        min(45.0, float(crop_right))
    ) / 100.0

    crop_w = max(
        2,
        int(src_w * (1 - cl - cr))
    )

    crop_h = max(
        2,
        int(src_h * (1 - ct - cb))
    )

    crop_x = int(
        src_w * cl
    )

    crop_y = int(
        src_h * ct
    )

    # -----------------------------------------------------
    # Foreground video
    # -----------------------------------------------------

    zoom_value = max(
        1.0,
        min(3.0, float(zoom))
    )

    brightness_value = (
        float(brightness) / 100.0
    )

    contrast_value = (
        float(contrast) / 100.0
    )

    # Scale before crop
    fg_filter = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale="
        f"{int(crop_w * zoom_value)}:"
        f"{int(crop_h * zoom_value)}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={int(crop_w * zoom_value)}:"
        f"{int(crop_h * zoom_value)}:"
        f"(iw-{int(crop_w * zoom_value)})/2:"
        f"(ih-{int(crop_h * zoom_value)})/2,"
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
        f"eq=brightness={brightness_value}:contrast={contrast_value}"
    )

    # -----------------------------------------------------
    # Background
    # -----------------------------------------------------

    if background_type == "Blur":

        bg_filter = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},"
            f"boxblur={max(1, int(blur_strength))}:2"
        )

    else:

        bg_color = color_to_ffmpeg(
            background_color
        )

        bg_filter = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:"
            f"(ow-iw)/2:(oh-ih)/2:"
            f"color={bg_color}"
        )

    # -----------------------------------------------------
    # Position
    # -----------------------------------------------------

    px = float(pos_x)
    py = float(pos_y)

    x_offset = int(
        (out_w - out_w) * px / 100
    )

    y_offset = int(
        (out_h - out_h) * py / 100
    )

    # -----------------------------------------------------
    # ASS Subtitle
    # -----------------------------------------------------

    make_ass_file(
        script_text,
        ass_path,
        out_w,
        out_h,
        font_size=int(subtitle_size),
        color=subtitle_color,
        position=subtitle_position
    )

    # -----------------------------------------------------
    # Video duration matching
    # -----------------------------------------------------

    # Auto trim / loop
    #
    # Short video:
    # loop it.
    #
    # Long video:
    # trim it.
    #
    # This keeps the video smooth and prevents
    # black frames when voice is longer.
    # -----------------------------------------------------

    filter_complex = (
        f"[0:v]{bg_filter}[bg];"
        f"[0:v]{fg_filter}[fg];"
        f"[bg][fg]overlay="
        f"shortest=1,"
        f"setpts=PTS-STARTPTS,"
        f"subtitles='{ass_path.replace(chr(92), '/')}'"
        f"[vout]"
    )

    # -----------------------------------------------------
    # Audio
    # -----------------------------------------------------

    audio_inputs = [
        "-i",
        video_path
    ]

    audio_index = 1

    # Voice
    audio_inputs += [
        "-i",
        voice_path
    ]

    voice_input_index = audio_index
    audio_index += 1

    bgm_input_index = None

    if bgm_file and os.path.exists(
        bgm_file
    ):

        audio_inputs += [
            "-stream_loop",
            "-1",
            "-i",
            bgm_file
        ]

        bgm_input_index = audio_index
        audio_index += 1

    # -----------------------------------------------------
    # Audio filter
    # -----------------------------------------------------

    audio_filters = []

    # Voice
    audio_filters.append(
        f"[{voice_input_index}:a]"
        f"volume={max(0,float(voice_volume))}"
        f"[voice]"
    )

    # Original audio
    if original_audio:

        audio_filters.append(
            f"[0:a]"
            f"volume={max(0,float(original_volume))}"
            f"[orig]"
        )

    # BGM
    if bgm_input_index is not None:

        audio_filters.append(
            f"[{bgm_input_index}:a]"
            f"volume={max(0,float(bgm_volume))}"
            f"[bgm]"
        )

    if original_audio and bgm_input_index is not None:

        audio_filters.append(
            "[voice][orig][bgm]"
            "amix=inputs=3:"
            "duration=first:"
            "dropout_transition=2"
            "[aout]"
        )

    elif original_audio:

        audio_filters.append(
            "[voice][orig]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=2"
            "[aout]"
        )

    elif bgm_input_index is not None:

        audio_filters.append(
            "[voice][bgm]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=2"
            "[aout]"
        )

    else:

        audio_filters.append(
            "[voice]anull[aout]"
        )

    # -----------------------------------------------------
    # Final FFmpeg
    # -----------------------------------------------------

    cmd = [
        "ffmpeg",
        "-y"
    ]

    cmd += audio_inputs

    cmd += [
        "-filter_complex",
        filter_complex + ";" + ";".join(
            audio_filters
        ),

        "-map",
        "[vout]",

        "-map",
        "[aout]",

        "-t",
        str(voice_duration),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "27",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        preview_video
    ]

    try:

        run_cmd(
            cmd,
            timeout=900
        )

    except Exception as e:

        print(
            "Preview FFmpeg Error:",
            e
        )

        raise

    return (
        preview_video,
        f"✅ Preview ပြီးပါပြီ။ Voice Duration: {voice_duration:.1f}s"
    )


# =========================================================
# FINAL ONE CLIP GENERATOR
# =========================================================

def generate_final_one_clip(
    video_path,
    script_text,
    ratio,
    background_type,
    background_color,
    blur_strength,
    zoom,
    pos_x,
    pos_y,
    brightness,
    contrast,
    crop_top,
    crop_bottom,
    crop_left,
    crop_right,
    subtitle_color,
    subtitle_size,
    subtitle_position,
    voice,
    voice_speed,
    voice_volume,
    original_audio,
    original_volume,
    bgm_file,
    bgm_volume,
    quality
):

    if not video_path or not os.path.exists(
        video_path
    ):
        raise ValueError(
            "Video မရှိပါ။"
        )

    if not script_text.strip():
        raise ValueError(
            "Script မရှိပါ။"
        )

    if not ffmpeg_exists():
        raise RuntimeError(
            "FFmpeg မရှိပါ။"
        )

    job_id = uuid.uuid4().hex[:12]

    final_path = os.path.join(
        WORK_DIR,
        f"ONE_CLIP_{quality}_{ratio.replace(':','x')}_{job_id}.mp4"
    )

    voice_path = os.path.join(
        WORK_DIR,
        f"final_voice_{job_id}.mp3"
    )

    ass_path = os.path.join(
        WORK_DIR,
        f"final_caption_{job_id}.ass"
    )

    # -----------------------------------------------------
    # Generate TTS
    # -----------------------------------------------------

    asyncio.run(
        generate_myanmar_tts(
            script_text,
            voice,
            voice_speed,
            f"final_voice_{job_id}.mp3"
        )
    )

    if not os.path.exists(
        voice_path
    ):
        raise RuntimeError(
            "Voice ဖန်တီး၍ မရပါ။"
        )

    voice_duration = (
        get_video_duration(
            voice_path
        )
        or 1.0
    )

    out_w, out_h = get_output_size(
        ratio,
        quality
    )

    src_w, src_h = get_video_resolution(
        video_path
    )

    # -----------------------------------------------------
    # Crop
    # -----------------------------------------------------

    ct = max(
        0,
        min(45, float(crop_top))
    ) / 100

    cb = max(
        0,
        min(45, float(crop_bottom))
    ) / 100

    cl = max(
        0,
        min(45, float(crop_left))
    ) / 100

    cr = max(
        0,
        min(45, float(crop_right))
    ) / 100

    crop_w = max(
        2,
        int(src_w * (1 - cl - cr))
    )

    crop_h = max(
        2,
        int(src_h * (1 - ct - cb))
    )

    crop_x = int(
        src_w * cl
    )

    crop_y = int(
        src_h * ct
    )

    # -----------------------------------------------------
    # Zoom
    # -----------------------------------------------------

    zoom_value = max(
        1.0,
        min(3.0, float(zoom))
    )

    zoom_w = int(
        crop_w * zoom_value
    )

    zoom_h = int(
        crop_h * zoom_value
    )

    # -----------------------------------------------------
    # Color
    # -----------------------------------------------------

    brightness_value = (
        float(brightness) / 100
    )

    contrast_value = (
        float(contrast) / 100
    )

    # -----------------------------------------------------
    # Foreground
    # -----------------------------------------------------

    fg_filter = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={zoom_w}:{zoom_h}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={zoom_w}:{zoom_h}:"
        f"(iw-{zoom_w})/2:"
        f"(ih-{zoom_h})/2,"
        f"scale={out_w}:{out_h}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:"
        f"(ow-iw)/2:(oh-ih)/2,"
        f"eq=brightness={brightness_value}:"
        f"contrast={contrast_value}"
    )

    # -----------------------------------------------------
    # Background
    # -----------------------------------------------------

    if background_type == "Blur":

        bg_filter = (
            f"scale={out_w}:{out_h}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},"
            f"boxblur="
            f"{max(1,int(blur_strength))}:2"
        )

    else:

        bg_filter = (
            f"scale={out_w}:{out_h}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:"
            f"(ow-iw)/2:(oh-ih)/2:"
            f"color={color_to_ffmpeg(background_color)}"
        )

    # -----------------------------------------------------
    # Subtitle
    # -----------------------------------------------------

    make_ass_file(
        script_text,
        ass_path,
        out_w,
        out_h,
        font_size=int(subtitle_size),
        color=subtitle_color,
        position=subtitle_position
    )

    ass_filter_path = ass_path.replace(
        "\\",
        "/"
    )

    # -----------------------------------------------------
    # Complex video
    # -----------------------------------------------------

    filter_complex = (
        f"[0:v]{bg_filter}[bg];"
        f"[0:v]{fg_filter}[fg];"
        f"[bg][fg]overlay="
        f"shortest=1,"
        f"setpts=PTS-STARTPTS,"
        f"subtitles='{ass_filter_path}'"
        f"[vout]"
    )

    # -----------------------------------------------------
    # Audio inputs
    # -----------------------------------------------------

    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        video_path,
        "-i",
        voice_path
    ]

    # NOTE:
    # input #0 is looped video.
    # input #1 is voice.
    #
    # BGM becomes input #2.

    bgm_index = None

    if bgm_file and os.path.exists(
        bgm_file
    ):

        cmd += [
            "-stream_loop",
            "-1",
            "-i",
            bgm_file
        ]

        bgm_index = 2

    # -----------------------------------------------------
    # Audio filters
    # -----------------------------------------------------

    audio_filters = [
        f"[1:a]volume={max(0,float(voice_volume))}[voice]"
    ]

    if original_audio:

        audio_filters.append(
            f"[0:a]"
            f"volume={max(0,float(original_volume))}"
            f"[orig]"
        )

    if bgm_index is not None:

        audio_filters.append(
            f"[{bgm_index}:a]"
            f"volume={max(0,float(bgm_volume))}"
            f"[bgm]"
        )

    if original_audio and bgm_index is not None:

        audio_filters.append(
            "[voice][orig][bgm]"
            "amix=inputs=3:"
            "duration=first:"
            "dropout_transition=2"
            "[aout]"
        )

    elif original_audio:

        audio_filters.append(
            "[voice][orig]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=2"
            "[aout]"
        )

    elif bgm_index is not None:

        audio_filters.append(
            "[voice][bgm]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=2"
            "[aout]"
        )

    else:

        audio_filters.append(
            "[voice]anull[aout]"
        )

    # -----------------------------------------------------
    # Final command
    # -----------------------------------------------------

    cmd += [
        "-filter_complex",
        filter_complex + ";" + ";".join(
            audio_filters
        ),

        "-map",
        "[vout]",

        "-map",
        "[aout]",

        # Voice duration controls final video.
        "-t",
        str(voice_duration),

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "18" if quality == "1080p"
        else "20" if quality == "720p"
        else "23",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-movflags",
        "+faststart",

        final_path
    ]

    run_cmd(
        cmd,
        timeout=1800
    )

    if not os.path.exists(
        final_path
    ):
        raise RuntimeError(
            "Final Video မထွက်လာပါ။"
        )

    return (
        final_path,
        f"""
## ✅ One Clip Video အပြီးစီးပါပြီ

- Ratio: **{ratio}**
- Quality: **{quality}**
- Voice Duration: **{voice_duration:.1f}s**
- Subtitle: **ON**
- Auto Trim/Loop: **ON**
- Audio Mixing: **ပြီးပါပြီ**
- Video Processing: **ပြီးပါပြီ**
"""
    )


# =========================================================
# PREVIEW CSS
# =========================================================

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
    width:100% !important;
    max-width:{cfg["max_w"]} !important;
    margin:auto !important;
}}

#{container_id} video {{
    width:100% !important;
    aspect-ratio:{cfg["aspect"]} !important;
    object-fit:cover !important;
    border-radius:12px !important;
}}
</style>
"""


# =========================================================
# TAB 3 SOURCE LOADING
# =========================================================

def tab3_load_url(link):

    if not link or not link.strip():
        return (
            None,
            "⚠️ YouTube/TikTok Link ထည့်ပါ။"
        )

    video = download_video_from_link(
        link
    )

    if not video:
        return (
            None,
            "❌ Link မှ Video ရယူ၍ မရပါ။"
        )

    return (
        video,
        "✅ Video ရပါပြီ။ Preview ပြင်ဆင်နိုင်ပါပြီ။"
    )


def tab3_use_uploaded_file(
    video
):
    if video:
        return (
            video,
            "✅ Video File ရပါပြီ။"
        )

    return (
        None,
        ""
    )


# =========================================================
# SAMPLE CAPTION
# =========================================================

def sample_caption_html(
    color,
    size,
    position
):

    pos = {
        "အပေါ်": "top:20px;",
        "အလယ်": "top:50%;transform:translateY(-50%);",
        "အောက်": "bottom:25px;"
    }.get(
        position,
        "bottom:25px;"
    )

    return f"""
<div style="
    width:100%;
    max-width:420px;
    aspect-ratio:9/16;
    background:linear-gradient(135deg,#222,#555);
    margin:auto;
    border-radius:12px;
    position:relative;
    overflow:hidden;
">
    <div style="
        position:absolute;
        left:10%;
        right:10%;
        {pos}
        text-align:center;
        font-size:{int(size)}px;
        font-weight:800;
        color:{color};
        text-shadow:
            3px 3px 0 #000,
            -2px -2px 0 #000,
            2px -2px 0 #000,
            -2px 2px 0 #000;
        line-height:1.2;
    ">
        အရင်းဆုံးနမူနာစာ<br>
        ဒုတိယစာကြောင်း
    </div>
</div>
"""


# =========================================================
# GRADIO UI
# =========================================================

with gr.Blocks(
    title=APP_TITLE,
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown(
        f"""
# 🎬 {APP_TITLE}

**AI Movie Recap Script → Myanmar Voice → Subtitle → One Clip Video**
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
                        label="📹 Video File တင်ရန်",
                        type="filepath"
                    )

                    v1_url = gr.Textbox(
                        label="🔗 YouTube / TikTok / Facebook URL",
                        placeholder="https://..."
                    )

                    v1_load_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary"
                    )

                    v1_ratio = gr.Radio(
                        [
                            "9:16",
                            "3:4",
                            "16:9",
                            "1:1"
                        ],
                        value="1:1",
                        label="📐 Preview Ratio"
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
                        lines=12
                    )

                    go_to_tts_btn = gr.Button(
                        "🎙️ Tab 2 (TTS) သို့ သွားမည် ➡️",
                        variant="secondary"
                    )

            with gr.Row():

                v1_srt = gr.File(
                    label="📄 SRT"
                )

                v1_zip = gr.File(
                    label="📦 SRT ZIP"
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
                        label="🎙️ Burmese Script",
                        lines=12
                    )

                    v2_voice = gr.Dropdown(
                        list(VOICES.keys()),
                        value=list(VOICES.keys())[0],
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
                        label="🎵 MP3 Download"
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
            "3️⃣ One Clip Video",
            id="tab_one_clip"
        ):

            gr.Markdown(
                """
## 🎬 One Clip Video Editor

Video + Voice + Subtitle + Background + Crop + Zoom + Audio
ကို Preview အရင်ကြည့်ပြီးမှ Final Generate လုပ်နိုင်ပါတယ်။
"""
            )

            with gr.Row():

                # -----------------------------------------
                # LEFT CONTROL PANEL
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    gr.Markdown(
                        "### 📹 1. Video Source"
                    )

                    t3_file = gr.Video(
                        label="Video File",
                        type="filepath"
                    )

                    t3_url = gr.Textbox(
                        label="YouTube / TikTok Link",
                        placeholder="https://..."
                    )

                    t3_load = gr.Button(
                        "⬇️ Link မှ Video ရယူမည်"
                    )

                    t3_status = gr.Markdown("")

                    gr.Markdown(
                        "### 📐 2. Video Ratio"
                    )

                    t3_ratio = gr.Radio(
                        [
                            "9:16",
                            "3:4",
                            "16:9",
                            "1:1"
                        ],
                        value="9:16",
                        label="Output Ratio"
                    )

                    gr.Markdown(
                        "### 🖼️ 3. Background"
                    )

                    t3_bg_type = gr.Radio(
                        [
                            "Blur",
                            "Solid Color"
                        ],
                        value="Blur",
                        label="Background Type"
                    )

                    t3_bg_color = gr.ColorPicker(
                        value="#151515",
                        label="Background Color"
                    )

                    t3_blur = gr.Slider(
                        1,
                        40,
                        value=18,
                        step=1,
                        label="Blur Strength"
                    )

                    gr.Markdown(
                        "### 🔍 4. Video Zoom / Position"
                    )

                    t3_zoom = gr.Slider(
                        1,
                        3,
                        value=1,
                        step=0.05,
                        label="Zoom"
                    )

                    t3_pos_x = gr.Slider(
                        -100,
                        100,
                        value=0,
                        step=1,
                        label="↔️ Video ဘယ် / ညာ"
                    )

                    t3_pos_y = gr.Slider(
                        -100,
                        100,
                        value=0,
                        step=1,
                        label="↕️ Video အပေါ် / အောက်"
                    )

                    gr.Markdown(
                        "### ✂️ 5. Crop"
                    )

                    t3_crop_top = gr.Slider(
                        0,
                        45,
                        value=0,
                        step=1,
                        label="Crop Top (%)"
                    )

                    t3_crop_bottom = gr.Slider(
                        0,
                        45,
                        value=0,
                        step=1,
                        label="Crop Bottom (%)"
                    )

                    t3_crop_left = gr.Slider(
                        0,
                        45,
                        value=0,
                        step=1,
                        label="Crop Left (%)"
                    )

                    t3_crop_right = gr.Slider(
                        0,
                        45,
                        value=0,
                        step=1,
                        label="Crop Right (%)"
                    )

                    gr.Markdown(
                        "### ☀️ 6. Light / Color"
                    )

                    t3_brightness = gr.Slider(
                        -100,
                        100,
                        value=0,
                        step=1,
                        label="Brightness"
                    )

                    t3_contrast = gr.Slider(
                        -100,
                        100,
                        value=0,
                        step=1,
                        label="Contrast"
                    )

                # -----------------------------------------
                # RIGHT CONTROL PANEL
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    gr.Markdown(
                        "### 📝 7. Auto Subtitle"
                    )

                    t3_script = gr.Textbox(
                        label="Voice Script",
                        lines=10,
                        placeholder="Tab 1 မှ Script အလိုအလျောက်ရောက်ပါမည်..."
                    )

                    t3_sub_color = gr.ColorPicker(
                        value="#FFFFFF",
                        label="စာလုံးအရောင်"
                    )

                    t3_sub_size = gr.Slider(
                        20,
                        100,
                        value=48,
                        step=1,
                        label="စာလုံးအရွယ်"
                    )

                    t3_sub_position = gr.Radio(
                        [
                            "အပေါ်",
                            "အလယ်",
                            "အောက်"
                        ],
                        value="အောက်",
                        label="စာတန်း Position"
                    )

                    t3_sample = gr.HTML(
                        sample_caption_html(
                            "#FFFFFF",
                            48,
                            "အောက်"
                        )
                    )

                    gr.Markdown(
                        "### 🎤 8. Voice"
                    )

                    t3_voice = gr.Dropdown(
                        list(VOICES.keys()),
                        value=list(VOICES.keys())[0],
                        label="Voice"
                    )

                    t3_voice_speed = gr.Slider(
                        -30,
                        50,
                        value=5,
                        step=1,
                        label="Voice Speed (%)"
                    )

                    t3_voice_volume = gr.Slider(
                        0,
                        2,
                        value=1,
                        step=0.05,
                        label="Voice Volume"
                    )

                    gr.Markdown(
                        "### 🔊 9. Original Audio"
                    )

                    t3_original_audio = gr.Checkbox(
                        value=False,
                        label="မူရင်းအသံ ဖွင့်မည်"
                    )

                    t3_original_volume = gr.Slider(
                        0,
                        2,
                        value=0.35,
                        step=0.05,
                        label="Original Audio Volume"
                    )

                    gr.Markdown(
                        "### 🎵 10. Background Music"
                    )

                    t3_bgm = gr.Audio(
                        label="BK Song ထည့်ရန်",
                        type="filepath"
                    )

                    t3_bgm_volume = gr.Slider(
                        0,
                        2,
                        value=0.15,
                        step=0.05,
                        label="BK Song Volume"
                    )

                    gr.Markdown(
                        "### 🎞️ 11. Preview"
                    )

                    t3_preview_btn = gr.Button(
                        "👀 Preview Generate",
                        variant="secondary"
                    )

                    t3_preview = gr.Video(
                        label="📺 One Clip Preview"
                    )

                    t3_preview_status = gr.Markdown(
                        ""
                    )

            # =================================================
            # FINAL OUTPUT
            # =================================================

            gr.Markdown(
                "---"
            )

            gr.Markdown(
                "## 🚀 12. Final Generate & Download"
            )

            with gr.Row():

                t3_quality = gr.Radio(
                    [
                        "480p",
                        "720p",
                        "1080p"
                    ],
                    value="720p",
                    label="🎯 Output Quality"
                )

                t3_generate = gr.Button(
                    "🔥 FINAL ONE CLIP GENERATE",
                    variant="primary"
                )

            t3_final_status = gr.Markdown(
                ""
            )

            t3_final_video = gr.Video(
                label="🎬 Final One Clip Video"
            )

            t3_download = gr.File(
                label="⬇️ Download Final Video"
            )


    # =====================================================
    # EVENT BINDINGS
    # =====================================================

    # -----------------------------------------------------
    # TAB 1
    # -----------------------------------------------------

    v1_file.change(
        lambda f: f,
        inputs=v1_file,
        outputs=v1_preview
    )

    v1_load_btn.click(
        load_url_for_tab1,
        inputs=v1_url,
        outputs=[
            v1_preview,
            v1_status
        ]
    )

    v1_ratio.change(
        lambda r: get_ratio_css(
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
        lambda: gr.Tabs(
            selected="tab_tts"
        ),
        outputs=main_tabs
    )

    # -----------------------------------------------------
    # TAB 2
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # TAB 1 -> TAB 3
    # -----------------------------------------------------

    v1_script_out.change(
        lambda x: x,
        inputs=v1_script_out,
        outputs=t3_script
    )

    v1_file.change(
        lambda x: x,
        inputs=v1_file,
        outputs=t3_file
    )

    # -----------------------------------------------------
    # TAB 3 URL
    # -----------------------------------------------------

    t3_load.click(
        tab3_load_url,
        inputs=t3_url,
        outputs=[
            t3_file,
            t3_status
        ]
    )

    # -----------------------------------------------------
    # TAB 3 upload
    # -----------------------------------------------------

    t3_file.change(
        tab3_use_uploaded_file,
        inputs=t3_file,
        outputs=[
            t3_file,
            t3_status
        ]
    )

    # -----------------------------------------------------
    # Subtitle sample
    # -----------------------------------------------------

    def update_sample(
        color,
        size,
        position
    ):
        return sample_caption_html(
            color,
            size,
            position
        )

    for component in [
        t3_sub_color,
        t3_sub_size,
        t3_sub_position
    ]:

        component.change(
            update_sample,
            inputs=[
                t3_sub_color,
                t3_sub_size,
                t3_sub_position
            ],
            outputs=t3_sample
        )

    # -----------------------------------------------------
    # Ratio preview CSS
    # -----------------------------------------------------

    t3_ratio.change(
        lambda r: get_ratio_css(
            r,
            "tab1_preview_container"
        ),
        inputs=t3_ratio,
        outputs=v1_css
    )

    # -----------------------------------------------------
    # One Clip Preview
    # -----------------------------------------------------

    t3_preview_btn.click(
        create_one_clip_preview,
        inputs=[
            t3_file,
            t3_script,
            t3_ratio,
            t3_bg_type,
            t3_bg_color,
            t3_blur,
            t3_zoom,
            t3_pos_x,
            t3_pos_y,
            t3_brightness,
            t3_contrast,
            t3_crop_top,
            t3_crop_bottom,
            t3_crop_left,
            t3_crop_right,
            t3_sub_color,
            t3_sub_size,
            t3_sub_position,
            t3_voice,
            t3_voice_speed,
            t3_voice_volume,
            t3_original_audio,
            t3_original_volume,
            t3_bgm,
            t3_bgm_volume
        ],
        outputs=[
            t3_preview,
            t3_preview_status
        ]
    )

    # -----------------------------------------------------
    # FINAL GENERATE
    # -----------------------------------------------------

    t3_generate.click(
        generate_final_one_clip,
        inputs=[
            t3_file,
            t3_script,
            t3_ratio,
            t3_bg_type,
            t3_bg_color,
            t3_blur,
            t3_zoom,
            t3_pos_x,
            t3_pos_y,
            t3_brightness,
            t3_contrast,
            t3_crop_top,
            t3_crop_bottom,
            t3_crop_left,
            t3_crop_right,
            t3_sub_color,
            t3_sub_size,
            t3_sub_position,
            t3_voice,
            t3_voice_speed,
            t3_voice_volume,
            t3_original_audio,
            t3_original_volume,
            t3_bgm,
            t3_bgm_volume,
            t3_quality
        ],
        outputs=[
            t3_final_video,
            t3_final_status
        ]
    )

    # Final video -> Download
    t3_final_video.change(
        lambda x: x,
        inputs=t3_final_video,
        outputs=t3_download
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
