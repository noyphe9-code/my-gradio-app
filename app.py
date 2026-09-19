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
# SUBTITLE COVER CONFIG
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

PREVIEW_CACHE = {}

# =========================================================
# SYSTEM HELPERS
# =========================================================

def save_api_key(api_key):
    global SAVED_API_KEY

    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"

    return "⚠️ Gemini API Key ထည့်ပေးပါ။"


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
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())

    except Exception as e:
        print("Duration Error:", e)

    return None


def validate_video_duration(video_path):
    duration = get_video_duration(video_path)

    if duration is None:
        return True, "ℹ️ Video duration ကို စစ်ဆေးနေပါသည်..."

    minutes = duration / 60

    if minutes > MAX_VIDEO_MINUTES:
        return (
            False,
            f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ "
            f"အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ လက်ခံပါသည်။",
        )

    return True, f"✅ Video Length: {minutes:.1f} မိနစ်"


def clean_script_for_tts(script_text):
    if not script_text:
        return ""

    cleaned = []

    for line in script_text.splitlines():
        line = line.strip()

        if not line:
            continue

        line = (
            line.replace("**", "")
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

        if line.lower() in [
            "movie recap",
            "recap script",
            "burmese recap script",
            "script",
        ] or line.startswith("---"):
            continue

        cleaned.append(line.strip())

    return "\n".join(cleaned)


def seconds_to_srt_time(seconds):
    seconds = max(0, int(seconds))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:02d},000"


def generate_srt_and_zip(script_text, prefix="myanmar_recap"):
    clean_text = clean_script_for_tts(script_text)

    if not clean_text:
        return None, None

    lines = [
        x.strip()
        for x in clean_text.splitlines()
        if x.strip()
    ]

    srt_content = ""
    current_time = 0

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

    srt_filename = f"{prefix}_subtitle.srt"
    zip_filename = f"{prefix}_subtitle.zip"

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
            arcname=srt_filename
        )

    return srt_filename, zip_filename


def download_video_from_link(link):
    if not link or not link.strip():
        return None

    output_template = "temp_downloaded_video.%(ext)s"

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
                ".mov",
            ]:

                if os.path.exists(base + ext):
                    return base + ext

    except Exception as e:
        print("Download Error:", e)

    return None


# =========================================================
# DYNAMIC RATIO & FLIP STYLING
# =========================================================

def get_ratio_css(
    ratio,
    container_id="tab1_preview_container",
    flip_horizontal=False
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

    transform_rule = (
        "scaleX(-1)"
        if flip_horizontal
        else "scaleX(1)"
    )

    return f"""
    <style id="{container_id}-style">

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
        box-shadow: 0 4px 15px rgba(0,0,0,0.3) !important;
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
# SUBTITLE COVER ENGINE
# =========================================================

def color_to_ffmpeg(color_name):
    """
    FFmpeg အတွက် color mapping
    """

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
    Video ထဲမှာ Subtitle Cover ကို တကယ် render လုပ်ပြီး
    Preview Video ပြန်ထုတ်ပေးသည်။

    pos_x / pos_y / width / height = percentage
    """

    if not video_path:
        return None, "⚠️ Preview Video ထည့်ပေးပါ။"

    if not os.path.exists(video_path):
        return None, "⚠️ Video file မတွေ့ပါ။"

    try:

        # -------------------------------------------------
        # Cache
        # -------------------------------------------------

        cache_key = hashlib.md5(
            (
                f"{video_path}|"
                f"{cover_color}|"
                f"{opacity}|"
                f"{blur_amount}|"
                f"{pos_x}|"
                f"{pos_y}|"
                f"{cover_width}|"
                f"{cover_height}|"
                f"{flip_horizontal}"
            ).encode()
        ).hexdigest()

        output_file = os.path.abspath(
            f"subtitle_preview_{cache_key}.mp4"
        )

        if os.path.exists(output_file):
            return (
                output_file,
                "✅ Preview Ready"
            )

        # -------------------------------------------------
        # FFmpeg values
        # -------------------------------------------------

        color = color_to_ffmpeg(
            cover_color
        )

        opacity_value = max(
            0,
            min(
                100,
                float(opacity)
            )
        ) / 100.0

        blur_value = max(
            0,
            min(
                30,
                int(blur_amount)
            )
        )

        # Percentage → FFmpeg expression
        #
        # width  = iw * percentage
        # height = ih * percentage
        # x      = iw * percentage
        # y      = ih * percentage

        crop_w = f"iw*{float(cover_width)/100:.4f}"
        crop_h = f"ih*{float(cover_height)/100:.4f}"

        crop_x = f"iw*{float(pos_x)/100:.4f}"
        crop_y = f"ih*{float(pos_y)/100:.4f}"

        # -------------------------------------------------
        # Filter
        # -------------------------------------------------

        if blur_value > 0:

            filter_complex = (
                f"[0:v]split=2[base][blur_src];"
                f"[blur_src]"
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"boxblur={blur_value}:1"
                f"[blurred];"
                f"[base][blurred]"
                f"overlay={crop_x}:{crop_y}:shortest=1"
                f"[blur_video];"
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

            filter_complex = (
                f"[0:v]"
                f"drawbox="
                f"x={crop_x}:"
                f"y={crop_y}:"
                f"w={crop_w}:"
                f"h={crop_h}:"
                f"color={color}@{opacity_value}:"
                f"t=fill"
            )

        # -------------------------------------------------
        # Horizontal Flip
        # -------------------------------------------------

        if flip_horizontal:

            if blur_value > 0:

                filter_complex += (
                    ",hflip"
                )

            else:

                filter_complex += (
                    ",hflip"
                )

        # -------------------------------------------------
        # FFmpeg command
        # -------------------------------------------------

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
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-t",
            "60",
            output_file,
        ]

        print("Running FFmpeg:")
        print(" ".join(cmd))

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:

            print(
                "FFmpeg ERROR:",
                result.stderr[-5000:]
            )

            return (
                None,
                "❌ Subtitle Cover Preview ပြုလုပ်၍ မရပါ။ FFmpeg ကို စစ်ဆေးပါ။"
            )

        if not os.path.exists(output_file):

            return (
                None,
                "❌ Preview file မထွက်လာပါ။"
            )

        PREVIEW_CACHE[cache_key] = output_file

        return (
            output_file,
            "✅ Subtitle Cover Preview ပြီးပါပြီ။"
        )

    except subprocess.TimeoutExpired:

        return (
            None,
            "⏱️ Preview ပြုလုပ်ချိန် ကြာမြင့်နေပါသည်။"
        )

    except Exception as e:

        print(
            "Subtitle Cover Error:",
            e
        )

        return (
            None,
            f"❌ Error: {str(e)}"
        )


# =========================================================
# SHARED COVER CONTROLLER
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
    """
    Slider / Color ပြောင်းတိုင်း Preview Video update
    """

    if not video:
        return None, "📹 Video ထည့်ပြီးနောက် Cover Setting ကို စမ်းနိုင်ပါသည်။"

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
# GEMINI GENERATION
# =========================================================

def build_recap_prompt(selected_ratio):

    return f"""
သင်သည် ထိပ်တန်း Professional Movie Recap Scriptwriter ဖြစ်သည်။

Target Video Frame Ratio: {selected_ratio}

ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ တိကျသေချာစွာ ကြည့်ရှုနားထောင်ပြီး
အောက်ပါစည်းမျဉ်းများအတိုင်း "မြန်မာ Movie Recap Script" ကို ရေးသားပေးပါ-

[စည်းမျဉ်းများ]

၁။ ဗီဒီယိုထဲတွင် တကယ်ဖြစ်ပျက်နေသော အဖြစ်အပျက် အမှန်များကိုသာ
အချိန်အစဉ်လိုက် အတိုချုံး၍ တိကျမှန်ကန်စွာ ရေးပါ။

၂။ ဇာတ်ကောင်များ၏ ပြောစကားများကို သဘာဝကျကျ
ဆွဲဆောင်မှုရှိသော မြန်မာစကားပြောအဖြစ် တိုက်ရိုက်ပြန်ဆိုပါ။

၃။ [Visual], [Scene], [Narrator], [Dialogue], [Intro]
စသည့် Technical Label များနှင့် စကားအပိုများ လုံးဝမထည့်ပါနှင့်။

၄။ TTS အသံထွက်ဖတ်ရာတွင် ချောမွေ့စေရန်
စာကြောင်းတစ်ကြောင်းချင်းစီကို တိုတိုရှင်းရှင်းနှင့်
အဓိပ္ပာယ်ပြည့်စုံစွာ ရေးပေးပါ။
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

                if attempt < len(
                    retry_delays
                ):

                    time.sleep(
                        retry_delays[attempt]
                    )

                else:
                    break

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
            "🔑 API Key Setting ထဲတွင် "
            "အရင်ထည့်သွင်းပေးပါ။"
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
            and uploaded_file.state.name
            == "ACTIVE"
        ):
            break

        if (
            uploaded_file.state
            and uploaded_file.state.name
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

        uploaded_file = client.files.get(
            name=uploaded_file.name
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

    clean_text = clean_script_for_tts(
        script_text
    )

    return (
        clean_text,
        used_model,
        msg
    )


# =========================================================
# TTS LOGIC
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

    communicate = edge_tts.Communicate(
        clean_text,
        selected_voice,
        rate=rate_str
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
            )
        )
    )

    return (
        output_name,
        srt_file,
        zip_file
    )


# =========================================================
# TAB CONTROLLERS
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

    if not target or not os.path.exists(
        target
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
            f"✅ Script ရေးသားပြီးပါပြီ! "
            f"(Model: {model})\n"
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
# UI HELPER
# =========================================================

def subtitle_cover_panel(prefix):
    """
    Tab 1 / Tab 2 / Tab 3 တွင် ပြန်သုံးနိုင်သော
    Subtitle Cover UI
    """

    gr.Markdown(
        """
### 🎨 Original Subtitle ဖုံးရန် Cover Setting

**Video ထဲက မူရင်းစာတန်းထိုးကို ဖုံးနိုင်ရန်**
Blur + Color Overlay ကို ချိန်ညှိနိုင်ပါသည်။
"""
    )

    with gr.Row():

        with gr.Column():

            color = gr.Dropdown(
                list(COVER_COLORS.keys()),
                value="⬛ Black",
                label="🎨 Cover Color",
            )

            opacity = gr.Slider(
                0,
                100,
                value=75,
                step=1,
                label="💧 Color Intensity / Opacity (%)",
            )

            blur = gr.Slider(
                0,
                30,
                value=12,
                step=1,
                label="🌫️ Blur အား",
            )

        with gr.Column():

            pos_x = gr.Slider(
                0,
                90,
                value=5,
                step=1,
                label="↔️ ဘယ် / ညာ ရွှေ့ရန် (X %)",
            )

            pos_y = gr.Slider(
                0,
                90,
                value=75,
                step=1,
                label="↕️ အပေါ် / အောက် ရွှေ့ရန် (Y %)",
            )

            width = gr.Slider(
                5,
                100,
                value=90,
                step=1,
                label="↔️ Cover Width (%)",
            )

            height = gr.Slider(
                5,
                40,
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
                        label="🔗 Video URL Link"
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
                        label="📐 Preview Screen Aspect Ratio"
                    )

                    v1_gen_btn = gr.Button(
                        "🚀 Recap Script စတင်ထုတ်မည်",
                        variant="primary"
                    )

                    # ------------------------------
                    # TAB 1 COVER
                    # ------------------------------

                    t1_cover = subtitle_cover_panel(
                        "tab1"
                    )

                with gr.Column(
                    scale=1
                ):

                    v1_css = gr.HTML(
                        get_ratio_css(
                            "1:1",
                            "tab1_preview_container",
                            False
                        )
                    )

                    v1_preview = gr.Video(
                        label="📺 Video Preview",
                        elem_id="tab1_preview_container"
                    )

                    v1_cover_status = gr.Markdown(
                        "Cover Preview အဆင်သင့်ဖြစ်ပါသည်။"
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
                        label="🎙️ Burmese Script",
                        lines=12
                    )

                    v2_voice = gr.Dropdown(
                        list(VOICES.keys()),
                        value="Thiha (အမျိုးသားအသံ) - Natural",
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

                    # ------------------------------
                    # TAB 2 COVER
                    # ------------------------------

                    t2_cover = subtitle_cover_panel(
                        "tab2"
                    )

                with gr.Column(
                    scale=1
                ):

                    v2_video = gr.Video(
                        label="📺 Video + Subtitle Cover Preview"
                    )

                    v2_audio = gr.Audio(
                        label="🔊 Voice Preview",
                        autoplay=True
                    )

                    v2_mp3 = gr.File(
                        label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်"
                    )

                    v2_cover_status = gr.Markdown(
                        "Tab 1 မှ Video ရောက်လာပါက "
                        "Cover Preview ကို ဒီနေရာမှာ စမ်းနိုင်ပါသည်။"
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
                        label="🔗 Video URL Link"
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
                            "1:1"
                        ],
                        value="9:16",
                        label="📐 Aspect Ratio ရွေးချယ်ရန်"
                    )

                    t3_flip = gr.Checkbox(
                        label="↔️ Horizontal Flip Preview",
                        value=False
                    )

                    # ------------------------------
                    # TAB 3 COVER
                    # ------------------------------

                    t3_cover = subtitle_cover_panel(
                        "tab3"
                    )

                with gr.Column(
                    scale=1
                ):

                    t3_css = gr.HTML(
                        get_ratio_css(
                            "9:16",
                            "tab3_preview_container",
                            False
                        )
                    )

                    t3_preview = gr.Video(
                        label="📺 Original Video Preview",
                        elem_id="tab3_preview_container"
                    )

                    t3_cover_status = gr.Markdown(
                        "Subtitle Cover Preview အဆင်သင့်ဖြစ်ပါသည်။"
                    )

        # =================================================
        # EVENT BINDINGS
        # =================================================

        # -------------------------------------------------
        # TAB 1 VIDEO
        # -------------------------------------------------

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
                "tab1_preview_container",
                False
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
                v1_zip,
                v2_video,
            ]
        )

        go_to_tts_btn.click(
            lambda: gr.Tabs(
                selected="tab_tts"
            ),
            outputs=main_tabs
        )

        # -------------------------------------------------
        # TAB 1 COVER PREVIEW
        # -------------------------------------------------

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
            ]
        )

        # -------------------------------------------------
        # TAB 2 TTS
        # -------------------------------------------------

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

        # -------------------------------------------------
        # TAB 2 COVER PREVIEW
        # -------------------------------------------------

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
            ]
        )

        # -------------------------------------------------
        # TAB 3 VIDEO
        # -------------------------------------------------

        t3_file.change(
            lambda f: f,
            inputs=t3_file,
            outputs=t3_preview
        )

        t3_load_btn.click(
            download_video_from_link,
            inputs=t3_url,
            outputs=t3_preview
        )

        def update_tab3_css(
            ratio,
            is_flipped
        ):
            return get_ratio_css(
                ratio,
                "tab3_preview_container",
                is_flipped
            )

        t3_ratio.change(
            update_tab3_css,
            inputs=[
                t3_ratio,
                t3_flip
            ],
            outputs=t3_css
        )

        t3_flip.change(
            update_tab3_css,
            inputs=[
                t3_ratio,
                t3_flip
            ],
            outputs=t3_css
        )

        # -------------------------------------------------
        # TAB 3 COVER PREVIEW
        # -------------------------------------------------

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
        server_port=port
    )
