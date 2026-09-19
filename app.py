import os
import re
import time
import zipfile
import subprocess
import asyncio
import hashlib

import gradio as gr
import edge_tts
import yt_dlp
from google import genai


# =========================================================
# CONFIGURATION & SETTINGS
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


# =========================================================
# SUBTITLE COVER COLORS
# =========================================================

COVER_COLORS = {
    "⬛ Black": "black",
    "⬜ White": "white",
    "🟥 Red": "red",
    "🟧 Orange": "orange",
    "🟨 Yellow": "yellow",
    "🟩 Green": "green",
    "🟦 Blue": "blue",
    "🟪 Purple": "purple",
    "🩷 Pink": "pink",
    "🟫 Brown": "brown",
    "🌫️ Gray": "gray",
}


# =========================================================
# SYSTEM HELPERS
# =========================================================

def save_api_key(api_key):
    global SAVED_API_KEY

    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()

        return (
            "✅ Gemini API Key ကို "
            "အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
        )

    return "⚠️ Gemini API Key ထည့်ပေးပါ။"


# =========================================================
# FFMPEG CHECK
# =========================================================

def check_ffmpeg():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:

            first_line = (
                result.stdout.splitlines()[0]
                if result.stdout
                else "FFmpeg"
            )

            print("====================================")
            print("✅ FFmpeg FOUND")
            print(first_line)
            print("====================================")

            return True

    except FileNotFoundError:

        print("====================================")
        print("❌ FFmpeg NOT FOUND")
        print("====================================")

    except Exception as e:

        print(
            "❌ FFmpeg Check Error:",
            e
        )

    return False


# =========================================================
# VIDEO DURATION
# =========================================================

def get_video_duration(video_path):

    if not video_path:
        return None

    if not os.path.exists(video_path):
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
            e
        )

    return None


def validate_video_duration(video_path):

    duration = get_video_duration(
        video_path
    )

    if duration is None:

        return (
            True,
            "ℹ️ Video duration ကို စစ်ဆေး၍ မရသေးပါ။"
        )

    minutes = duration / 60

    if minutes > MAX_VIDEO_MINUTES:

        return (
            False,
            f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ "
            f"အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ "
            f"လက်ခံပါသည်။"
        )

    return (
        True,
        f"✅ Video Length: {minutes:.1f} မိနစ်"
    )


# =========================================================
# SCRIPT CLEANER
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
            flags=re.IGNORECASE,
        )

        line = re.sub(
            r"^\s*Narrator\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )

        if (
            line.lower()
            in [
                "movie recap",
                "recap script",
                "burmese recap script",
                "script",
            ]
            or line.startswith("---")
        ):
            continue

        cleaned.append(
            line.strip()
        )

    return "\n".join(cleaned)


# =========================================================
# SRT HELPERS
# =========================================================

def seconds_to_srt_time(seconds):

    seconds = max(
        0,
        int(seconds)
    )

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


def generate_srt_and_zip(
    script_text,
    prefix="myanmar_recap"
):

    clean_text = (
        clean_script_for_tts(
            script_text
        )
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

    for idx, line in enumerate(
        lines,
        1
    ):

        duration = max(
            2,
            min(
                8,
                round(
                    len(line) / 11
                )
            )
        )

        start_time = current_time

        end_time = (
            current_time
            + duration
        )

        srt_content += (
            f"{idx}\n"
            f"{seconds_to_srt_time(start_time)} "
            f"--> "
            f"{seconds_to_srt_time(end_time)}\n"
            f"{line}\n\n"
        )

        current_time = end_time

    srt_filename = (
        f"{prefix}_subtitle.srt"
    )

    zip_filename = (
        f"{prefix}_subtitle.zip"
    )

    with open(
        srt_filename,
        "w",
        encoding="utf-8-sig",
    ) as f:

        f.write(
            srt_content
        )

    with zipfile.ZipFile(
        zip_filename,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as zipf:

        zipf.write(
            srt_filename,
            arcname=srt_filename
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

    output_template = (
        "temp_downloaded_video.%(ext)s"
    )

    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "overwrites": True,
        "merge_output_format": "mp4",
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
                ".mov",
            ]:

                possible = (
                    base + ext
                )

                if os.path.exists(
                    possible
                ):

                    return possible

    except Exception as e:

        print(
            "Download Error:",
            e
        )

    return None


# =========================================================
# RATIO CSS
# =========================================================

def get_ratio_css(
    ratio,
    container_id="tab1_preview_container",
    flip_horizontal=False,
):

    configs = {

        "1:1": {
            "aspect": "1 / 1",
            "max_w": "450px",
        },

        "3:4": {
            "aspect": "3 / 4",
            "max_w": "380px",
        },

        "16:9": {
            "aspect": "16 / 9",
            "max_w": "640px",
        },

        "9:16": {
            "aspect": "9 / 16",
            "max_w": "320px",
        },
    }

    cfg = configs.get(
        ratio,
        configs["1:1"]
    )

    transform_rule = (
        "scaleX(-1)"
        if flip_horizontal
        else "scaleX(1)"
    )

    return f"""
    <style>

    #{container_id} {{
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: 0 auto !important;
        transition: all 0.3s ease-in-out !important;
    }}

    #{container_id} .video-container {{
        width: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        height: auto !important;
        background: #000 !important;
        border-radius: 12px !important;
        overflow: hidden !important;
        box-shadow:
            0 4px 15px rgba(0,0,0,0.3) !important;
    }}

    #{container_id} video {{
        width: 100% !important;
        height: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        object-fit: cover !important;
        display: block !important;
        transform: {transform_rule} !important;
    }}

    </style>
    """


# =========================================================
# COLOR → FFMPEG
# =========================================================

def color_to_ffmpeg(
    color_name
):

    mapping = {

        "black": "black",
        "white": "white",
        "red": "red",
        "orange": "orange",
        "yellow": "yellow",
        "green": "green",
        "blue": "blue",
        "purple": "purple",
        "pink": "pink",
        "brown": "brown",
        "gray": "gray",
    }

    return mapping.get(
        color_name,
        "black"
    )


# =========================================================
# SUBTITLE COVER PREVIEW ENGINE
# =========================================================

def make_cover_preview(
    video_path,
    cover_color,
    opacity,
    blur_amount,
    pos_x,
    pos_y,
    cover_width,
    cover_height,
    ratio="1:1",
    flip_horizontal=False,
):
    """
    Subtitle Cover Preview

    Features:
        - Blur
        - Color
        - Opacity
        - X Position
        - Y Position
        - Width
        - Height
        - Horizontal Flip

    Preview output:
        Maximum 60 seconds
    """

    # -----------------------------------------------------
    # Video validation
    # -----------------------------------------------------

    if not video_path:

        return (
            None,
            "⚠️ Preview Video ထည့်ပေးပါ။"
        )

    if not os.path.exists(
        video_path
    ):

        return (
            None,
            "⚠️ Video file မတွေ့ပါ။"
        )

    # -----------------------------------------------------
    # FFmpeg validation
    # -----------------------------------------------------

    if not check_ffmpeg():

        return (
            None,
            "❌ FFmpeg မရှိပါ။ "
            "Render Build Command မှာ "
            "FFmpeg install လုပ်ပါ။"
        )

    # -----------------------------------------------------
    # Numeric values
    # -----------------------------------------------------

    try:

        opacity = float(
            opacity
        )

        blur_amount = int(
            blur_amount
        )

        pos_x = float(
            pos_x
        )

        pos_y = float(
            pos_y
        )

        cover_width = float(
            cover_width
        )

        cover_height = float(
            cover_height
        )

    except Exception:

        return (
            None,
            "❌ Cover Setting value မမှန်ပါ။"
        )

    # -----------------------------------------------------
    # Clamp values
    # -----------------------------------------------------

    opacity = max(
        0,
        min(
            100,
            opacity
        )
    )

    blur_amount = max(
        0,
        min(
            40,
            blur_amount
        )
    )

    cover_width = max(
        5,
        min(
            100,
            cover_width
        )
    )

    cover_height = max(
        5,
        min(
            80,
            cover_height
        )
    )

    # X/Y ကို box frame အပြင် မထွက်အောင်
    pos_x = max(
        0,
        min(
            100 - cover_width,
            pos_x
        )
    )

    pos_y = max(
        0,
        min(
            100 - cover_height,
            pos_y
        )
    )

    # -----------------------------------------------------
    # Color
    # -----------------------------------------------------

    color = color_to_ffmpeg(
        cover_color
    )

    opacity_value = (
        opacity / 100.0
    )

    # -----------------------------------------------------
    # Convert percentage to FFmpeg expression
    # -----------------------------------------------------

    crop_w = (
        f"trunc("
        f"iw*{cover_width / 100:.5f}"
        f"/2)*2"
    )

    crop_h = (
        f"trunc("
        f"ih*{cover_height / 100:.5f}"
        f"/2)*2"
    )

    crop_x = (
        f"trunc("
        f"iw*{pos_x / 100:.5f}"
        f"/2)*2"
    )

    crop_y = (
        f"trunc("
        f"ih*{pos_y / 100:.5f}"
        f"/2)*2"
    )

    # -----------------------------------------------------
    # Hash / Cache
    # -----------------------------------------------------

    settings = (
        f"{video_path}|"
        f"{cover_color}|"
        f"{opacity}|"
        f"{blur_amount}|"
        f"{pos_x}|"
        f"{pos_y}|"
        f"{cover_width}|"
        f"{cover_height}|"
        f"{ratio}|"
        f"{flip_horizontal}"
    )

    file_hash = hashlib.md5(
        settings.encode(
            "utf-8"
        )
    ).hexdigest()

    output_file = os.path.abspath(
        f"subtitle_cover_{file_hash}.mp4"
    )

    # -----------------------------------------------------
    # Existing preview
    # -----------------------------------------------------

    if os.path.exists(
        output_file
    ):

        return (
            output_file,
            "✅ Blur + Color Cover Preview အဆင်သင့်ဖြစ်ပါပြီ။"
        )

    # -----------------------------------------------------
    # FILTER
    # -----------------------------------------------------

    if blur_amount > 0:

        # -----------------------------------------------
        # Blur region
        # -----------------------------------------------

        filter_complex = (

            # Video ကို 2 stream ခွဲ
            "[0:v]split=2[main][blur_src];"

            # Subtitle area ကို crop
            f"[blur_src]"
            f"crop="
            f"w={crop_w}:"
            f"h={crop_h}:"
            f"x={crop_x}:"
            f"y={crop_y},"

            # Strong Blur
            f"boxblur="
            f"luma_radius={blur_amount}:"
            f"luma_power=2:"
            f"chroma_radius={blur_amount}:"
            f"chroma_power=2"
            "[blurred];"

            # Blur area ကို original video ပေါ်တင်
            f"[main][blurred]"
            f"overlay="
            f"x={crop_x}:"
            f"y={crop_y}:"
            f"shortest=1"
            "[blur_video];"

            # Color transparent box
            f"[blur_video]"
            f"drawbox="
            f"x={crop_x}:"
            f"y={crop_y}:"
            f"w={crop_w}:"
            f"h={crop_h}:"
            f"color={color}@{opacity_value}:"
            f"t=fill"
        )

    else:

        # -------------------------------------------------
        # Blur = 0
        # Color Cover only
        # -------------------------------------------------

        filter_complex = (

            "[0:v]"
            f"drawbox="
            f"x={crop_x}:"
            f"y={crop_y}:"
            f"w={crop_w}:"
            f"h={crop_h}:"
            f"color={color}@{opacity_value}:"
            f"t=fill"
        )

    # -----------------------------------------------------
    # Horizontal flip
    # -----------------------------------------------------

    if flip_horizontal:

        filter_complex += ",hflip"

    # -----------------------------------------------------
    # FFmpeg command
    # -----------------------------------------------------

    cmd = [

        "ffmpeg",

        "-y",

        "-i",
        video_path,

        "-filter_complex",
        filter_complex,

        "-map",
        "0:v:0",

        "-map",
        "0:a?",

        "-c:v",
        "libx264",

        "-preset",
        "ultrafast",

        "-crf",
        "23",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        # Preview = first 60 seconds
        "-t",
        "60",

        output_file,
    ]

    print("")
    print(
        "===================================="
    )
    print(
        "🎬 GENERATING SUBTITLE COVER PREVIEW"
    )
    print(
        "===================================="
    )

    print(
        "Color:",
        color
    )

    print(
        "Opacity:",
        opacity
    )

    print(
        "Blur:",
        blur_amount
    )

    print(
        "X:",
        pos_x
    )

    print(
        "Y:",
        pos_y
    )

    print(
        "Width:",
        cover_width
    )

    print(
        "Height:",
        cover_height
    )

    print(
        "===================================="
    )

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )

        # -------------------------------------------------
        # Error
        # -------------------------------------------------

        if result.returncode != 0:

            error_text = (
                result.stderr[-8000:]
                if result.stderr
                else "Unknown FFmpeg error"
            )

            print("")
            print(
                "❌ FFMPEG ERROR"
            )
            print(
                error_text
            )

            return (
                None,
                "❌ FFmpeg Error ဖြစ်နေပါသည်။ "
                "Console Log ကို စစ်ပါ။"
            )

        # -------------------------------------------------
        # Check output
        # -------------------------------------------------

        if not os.path.exists(
            output_file
        ):

            return (
                None,
                "❌ Preview Video မထွက်လာပါ။"
            )

        file_size = os.path.getsize(
            output_file
        )

        if file_size < 1000:

            return (
                None,
                "❌ Preview Video file ပျက်နေပါသည်။"
            )

        print("")
        print(
            "✅ PREVIEW CREATED:"
        )

        print(
            output_file
        )

        print(
            "Size:",
            file_size,
            "bytes"
        )

        print(
            "===================================="
        )

        return (
            output_file,
            "✅ 🌫️ Blur + 🎨 Color Cover Preview ပေါ်ပါပြီ။"
        )

    except subprocess.TimeoutExpired:

        return (
            None,
            "⏱️ Preview ပြုလုပ်ချိန် ကြာမြင့်နေပါသည်။"
        )

    except Exception as e:

        print(
            "Preview Error:",
            e
        )

        return (
            None,
            f"❌ Preview Error: {str(e)}"
        )


# =========================================================
# UPDATE COVER PREVIEW
# =========================================================

def update_cover_preview(
    video,
    color_choice,
    opacity,
    blur,
    x,
    y,
    width,
    height,
    ratio,
    flip,
):

    if not video:

        return (
            None,
            "📹 Video ထည့်ပြီးနောက် "
            "Cover Preview စမ်းပါ။"
        )

    color = COVER_COLORS.get(
        color_choice,
        "black"
    )

    return make_cover_preview(
        video_path=video,
        cover_color=color,
        opacity=opacity,
        blur_amount=blur,
        pos_x=x,
        pos_y=y,
        cover_width=width,
        cover_height=height,
        ratio=ratio,
        flip_horizontal=flip,
    )


# =========================================================
# GEMINI PROMPT
# =========================================================

def build_recap_prompt(
    selected_ratio
):

    return f"""

သင်သည် ထိပ်တန်း Professional Movie Recap Scriptwriter ဖြစ်သည်။

Target Video Frame Ratio: {selected_ratio}

ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ
တိကျသေချာစွာ ကြည့်ရှုနားထောင်ပြီး
အောက်ပါစည်းမျဉ်းများအတိုင်း
"မြန်မာ Movie Recap Script" ကို
ရေးသားပေးပါ။

[စည်းမျဉ်းများ]

၁။ ဗီဒီယိုထဲတွင် တကယ်ဖြစ်ပျက်နေသော
အဖြစ်အပျက် အမှန်များကိုသာ
အချိန်အစဉ်လိုက် အတိုချုံး၍
တိကျမှန်ကန်စွာ ရေးပါ။

၂။ ဇာတ်ကောင်များ၏ ပြောစကားများကို
သဘာဝကျကျ ဆွဲဆောင်မှုရှိသော
မြန်မာစကားပြောအဖြစ် တိုက်ရိုက်ပြန်ဆိုပါ။

၃။ [Visual], [Scene], [Narrator],
[Dialogue], [Intro] စသည့်
Technical Label များနှင့်
စကားအပိုများ လုံးဝမထည့်ပါနှင့်။

၄။ TTS အသံထွက်ဖတ်ရာတွင်
ချောမွေ့စေရန် စာကြောင်းတစ်ကြောင်းချင်းစီကို
တိုတိုရှင်းရှင်းနှင့် အဓိပ္ပာယ်ပြည့်စုံစွာ
ရေးပေးပါ။
"""


# =========================================================
# GEMINI RETRY
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

                if (
                    response
                    and response.text
                ):

                    return (
                        response.text,
                        model_name
                    )

            except Exception as e:

                last_error = e

                print(
                    f"Gemini Error "
                    f"{model_name}:",
                    e
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

                else:

                    break

    raise RuntimeError(
        "All Gemini models failed. "
        f"Last error: {last_error}"
    )


# =========================================================
# GEMINI VIDEO ANALYSIS
# =========================================================

def run_gemini_video_analysis(
    target_media,
    ratio_choice
):

    global SAVED_API_KEY

    if not SAVED_API_KEY:

        raise ValueError(
            "Gemini API Key မရှိသေးပါ။ "
            "🔑 API Key Setting ထဲတွင် "
            "အရင်ထည့်သွင်းပေးပါ။"
        )

    valid, msg = (
        validate_video_duration(
            target_media
        )
    )

    if not valid:
        raise ValueError(msg)

    client = genai.Client(
        api_key=SAVED_API_KEY
    )

    uploaded_file = (
        client.files.upload(
            file=target_media
        )
    )

    start_wait = time.time()

    while True:

        if (
            uploaded_file.state
            and
            uploaded_file.state.name
            == "ACTIVE"
        ):

            break

        if (
            uploaded_file.state
            and
            uploaded_file.state.name
            == "FAILED"
        ):

            raise RuntimeError(
                "Gemini Video Processing "
                "မအောင်မြင်ပါ။ "
                "ဗီဒီယိုဖိုင်ကို စစ်ဆေးပါ။"
            )

        if (
            time.time() - start_wait
            > 900
        ):

            raise TimeoutError(
                "Gemini Video Processing "
                "ကြာမြင့်လွန်းနေပါသည်။"
            )

        time.sleep(3)

        uploaded_file = (
            client.files.get(
                name=uploaded_file.name
            )
        )

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
    output_name="tab2_output.mp3",
):

    clean_text = (
        clean_script_for_tts(
            text
        )
    )

    if not clean_text:

        return (
            None,
            None,
            None
        )

    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )

    rate_str = (
        f"{int(speed_percent):+d}%"
    )

    communicate = (
        edge_tts.Communicate(
            clean_text,
            selected_voice,
            rate=rate_str,
        )
    )

    await communicate.save(
        output_name
    )

    srt_file, zip_file = (
        generate_srt_and_zip(
            clean_text,
            prefix=output_name.replace(
                ".mp3",
                ""
            ),
        )
    )

    return (
        output_name,
        srt_file,
        zip_file
    )


# =========================================================
# TAB 1 ANALYSIS
# =========================================================

def tab1_analyze(
    v_file,
    v_url,
    ratio
):

    target = (
        v_file
        if v_file
        else download_video_from_link(
            v_url
        )
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
            None,
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
            target,
        )

    except Exception as e:

        return (
            "",
            "",
            f"❌ Error: {str(e)}",
            None,
            None,
            target,
        )


# =========================================================
# TAB 2 TTS
# =========================================================

def tab2_tts(
    text,
    voice,
    speed
):

    try:

        mp3, srt, zip_f = (
            asyncio.run(
                generate_myanmar_tts(
                    text,
                    voice,
                    speed,
                    "tab2_output.mp3"
                )
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
# SUBTITLE COVER UI
# =========================================================

def subtitle_cover_panel():

    gr.Markdown(
        """
### 🎨 Original Subtitle ဖုံးရန် Cover Setting

မူရင်း Video ထဲက Subtitle ကို
**Blur + Color** နဲ့ ဖုံးနိုင်ပါတယ်။

`Preview` ခလုတ်နှိပ်ပြီး Video ပေါ်မှာ
တကယ်ဘယ်လိုပေါ်မလဲ စမ်းကြည့်နိုင်ပါတယ်။
"""
    )

    with gr.Row():

        with gr.Column():

            color = gr.Dropdown(
                choices=list(
                    COVER_COLORS.keys()
                ),
                value="⬛ Black",
                label="🎨 Cover Color",
            )

            opacity = gr.Slider(
                minimum=0,
                maximum=100,
                value=75,
                step=1,
                label="💧 Color Intensity / Opacity (%)",
            )

            blur = gr.Slider(
                minimum=0,
                maximum=40,
                value=15,
                step=1,
                label="🌫️ Blur အား",
            )

        with gr.Column():

            pos_x = gr.Slider(
                minimum=0,
                maximum=95,
                value=5,
                step=1,
                label="↔️ ဘယ် / ညာ (X %)",
            )

            pos_y = gr.Slider(
                minimum=0,
                maximum=95,
                value=75,
                step=1,
                label="↕️ အပေါ် / အောက် (Y %)",
            )

            width = gr.Slider(
                minimum=5,
                maximum=100,
                value=90,
                step=1,
                label="↔️ Cover Width (%)",
            )

            height = gr.Slider(
                minimum=5,
                maximum=80,
                value=15,
                step=1,
                label="↕️ Cover Height (%)",
            )

    preview_btn = gr.Button(
        "👁️ Preview Video ပေါ်တွင် Cover စမ်းမည်",
        variant="secondary",
    )

    return {
        "color": color,
        "opacity": opacity,
        "blur": blur,
        "x": pos_x,
        "y": pos_y,
        "width": width,
        "height": height,
        "preview_btn": preview_btn,
    }


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

**AI Video Recap Script & Myanmar Voice-Over**
"""
    )

    # =====================================================
    # TABS
    # =====================================================

    with gr.Tabs() as main_tabs:

        # =================================================
        # API KEY TAB
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
                outputs=key_status,
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
                        label=(
                            "🔗 Video URL Link "
                            "(YouTube, TikTok, Facebook စသည်)"
                        )
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
                            "9:16",
                        ],
                        value="1:1",
                        label=(
                            "📐 Preview Screen "
                            "Aspect Ratio"
                        ),
                    )

                    v1_gen_btn = gr.Button(
                        "🚀 Recap Script စတင်ထုတ်မည်",
                        variant="primary"
                    )

                    # -------------------------------------
                    # TAB 1 COVER
                    # -------------------------------------

                    t1_cover = (
                        subtitle_cover_panel()
                    )

                with gr.Column(
                    scale=1
                ):

                    v1_css = gr.HTML(
                        get_ratio_css(
                            "1:1",
                            "tab1_preview_container",
                            False,
                        )
                    )

                    v1_preview = gr.Video(
                        label=(
                            "📺 Video Preview "
                            "(Selected Ratio View)"
                        ),
                        elem_id=(
                            "tab1_preview_container"
                        ),
                    )

                    v1_cover_status = gr.Markdown(
                        "Cover Preview အဆင်သင့်ဖြစ်ပါသည်။"
                    )

                    v1_status = gr.Markdown(
                        "ဗီဒီယိုထည့်သွင်းရန် "
                        "အဆင်သင့်ဖြစ်ပါသည်။"
                    )

                    v1_script_out = gr.Textbox(
                        label="🎬 ထွက်ရှိလာသော Script",
                        lines=10,
                    )

                    go_to_tts_btn = gr.Button(
                        "🎙️ Tab 2 (TTS) သို့ "
                        "သွားရောက် အသံထုတ်မည် ➡️",
                        variant="secondary",
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
                        lines=12,
                    )

                    v2_voice = gr.Dropdown(
                        choices=list(
                            VOICES.keys()
                        ),
                        value=(
                            "Thiha "
                            "(အမျိုးသားအသံ) - Natural"
                        ),
                        label="🎤 အသံ ရွေးချယ်ပါ",
                    )

                    v2_speed = gr.Slider(
                        minimum=-30,
                        maximum=50,
                        value=5,
                        step=1,
                        label="⚡ Speed (%)",
                    )

                    v2_btn = gr.Button(
                        "⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်",
                        variant="primary",
                    )

                    # -------------------------------------
                    # TAB 2 COVER
                    # -------------------------------------

                    t2_cover = (
                        subtitle_cover_panel()
                    )

                with gr.Column(
                    scale=1
                ):

                    v2_video = gr.Video(
                        label=(
                            "📺 Video + "
                            "Subtitle Cover Preview"
                        )
                    )

                    v2_cover_status = gr.Markdown(
                        "Tab 1 မှ Video ရောက်လာပါက "
                        "Cover Preview စမ်းနိုင်ပါသည်။"
                    )

                    v2_audio = gr.Audio(
                        label=(
                            "🔊 Voice Preview "
                            "(အသံစမ်းနားထောင်ရန်)"
                        ),
                        autoplay=True,
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
            "3️⃣ One Clip Video",
            id="tab_one_clip"
        ):

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    t3_file = gr.Video(
                        label="📹 Video File ထည့်ရန်"
                    )

                    t3_url = gr.Textbox(
                        label=(
                            "🔗 Video URL Link "
                            "(YouTube, TikTok စသည်)"
                        )
                    )

                    t3_load_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary"
                    )

                    t3_ratio = gr.Radio(
                        [
                            "9:16",
                            "3:4",
                            "16:9",
                            "1:1",
                        ],
                        value="9:16",
                        label="📐 Aspect Ratio ရွေးချယ်ရန်",
                    )

                    t3_flip = gr.Checkbox(
                        label=(
                            "↔️ ဗီဒီယိုကို ဘယ်ညာလှန်မည် "
                            "(Horizontal Flip)"
                        ),
                        value=False,
                    )

                    # -------------------------------------
                    # TAB 3 COVER
                    # -------------------------------------

                    t3_cover = (
                        subtitle_cover_panel()
                    )

                with gr.Column(
                    scale=1
                ):

                    t3_css = gr.HTML(
                        get_ratio_css(
                            "9:16",
                            "tab3_preview_container",
                            False,
                        )
                    )

                    t3_preview = gr.Video(
                        label=(
                            "📺 Original Video Preview"
                        ),
                        elem_id=(
                            "tab3_preview_container"
                        ),
                    )

                    t3_cover_status = gr.Markdown(
                        "Subtitle Cover Preview "
                        "အဆင်သင့်ဖြစ်ပါသည်။"
                    )

    # =====================================================
    # EVENT BINDINGS
    # =====================================================

    # =====================================================
    # TAB 1 VIDEO
    # =====================================================

    v1_file.change(
        lambda f: f,
        inputs=v1_file,
        outputs=v1_preview,
    )

    v1_load_btn.click(
        download_video_from_link,
        inputs=v1_url,
        outputs=v1_preview,
    )

    v1_ratio.change(
        lambda r: get_ratio_css(
            r,
            "tab1_preview_container",
            False,
        ),
        inputs=v1_ratio,
        outputs=v1_css,
    )

    # =====================================================
    # TAB 1 ANALYZE
    # =====================================================

    v1_gen_btn.click(
        tab1_analyze,
        inputs=[
            v1_file,
            v1_url,
            v1_ratio,
        ],
        outputs=[
            v1_script_out,
            v2_input_text,
            v1_status,
            v1_srt,
            v1_zip,
            v2_video,
        ],
    )

    # =====================================================
    # TAB 1 COVER PREVIEW
    # =====================================================

    t1_cover["preview_btn"].click(
        update_cover_preview,
        inputs=[
            v1_preview,
            t1_cover["color"],
            t1_cover["opacity"],
            t1_cover["blur"],
            t1_cover["x"],
            t1_cover["y"],
            t1_cover["width"],
            t1_cover["height"],
            v1_ratio,
            gr.State(False),
        ],
        outputs=[
            v1_preview,
            v1_cover_status,
        ],
    )

    # =====================================================
    # GO TAB 2
    # =====================================================

    go_to_tts_btn.click(
        lambda: gr.Tabs(
            selected="tab_tts"
        ),
        outputs=main_tabs,
    )

    # =====================================================
    # TAB 2 TTS
    # =====================================================

    v2_btn.click(
        tab2_tts,
        inputs=[
            v2_input_text,
            v2_voice,
            v2_speed,
        ],
        outputs=[
            v2_audio,
            v2_mp3,
            v2_srt,
            v2_zip,
        ],
    )

    # =====================================================
    # TAB 2 COVER PREVIEW
    # =====================================================

    t2_cover["preview_btn"].click(
        update_cover_preview,
        inputs=[
            v2_video,
            t2_cover["color"],
            t2_cover["opacity"],
            t2_cover["blur"],
            t2_cover["x"],
            t2_cover["y"],
            t2_cover["width"],
            t2_cover["height"],
            gr.State("9:16"),
            gr.State(False),
        ],
        outputs=[
            v2_video,
            v2_cover_status,
        ],
    )

    # =====================================================
    # TAB 3 VIDEO
    # =====================================================

    t3_file.change(
        lambda f: f,
        inputs=t3_file,
        outputs=t3_preview,
    )

    t3_load_btn.click(
        download_video_from_link,
        inputs=t3_url,
        outputs=t3_preview,
    )

    # =====================================================
    # TAB 3 CSS
    # =====================================================

    def update_tab3_css(
        ratio,
        is_flipped
    ):

        return get_ratio_css(
            ratio,
            "tab3_preview_container",
            is_flipped,
        )

    t3_ratio.change(
        update_tab3_css,
        inputs=[
            t3_ratio,
            t3_flip,
        ],
        outputs=t3_css,
    )

    t3_flip.change(
        update_tab3_css,
        inputs=[
            t3_ratio,
            t3_flip,
        ],
        outputs=t3_css,
    )

    # =====================================================
    # TAB 3 COVER PREVIEW
    # =====================================================

    t3_cover["preview_btn"].click(
        update_cover_preview,
        inputs=[
            t3_preview,
            t3_cover["color"],
            t3_cover["opacity"],
            t3_cover["blur"],
            t3_cover["x"],
            t3_cover["y"],
            t3_cover["width"],
            t3_cover["height"],
            t3_ratio,
            t3_flip,
        ],
        outputs=[
            t3_preview,
            t3_cover_status,
        ],
    )


# =========================================================
# SERVER
# =========================================================

if __name__ == "__main__":

    print("")
    print(
        "===================================="
    )
    print(
        "🎬 AI MOVIE RECAP STUDIO PRO"
    )
    print(
        "===================================="
    )

    check_ffmpeg()

    port = int(
        os.environ.get(
            "PORT",
            7860
        )
    )

    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
    )
