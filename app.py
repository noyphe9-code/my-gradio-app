import os
import re
import time
import zipfile
import subprocess
import asyncio
import base64
import gradio as gr
import edge_tts
import yt_dlp
from google import genai

# =========================================================
# CONFIGURATION & SETTINGS
# =========================================================
APP_TITLE = "🎬 AI Movie Recap Studio Pro (All-in-One)"
MAX_VIDEO_MINUTES = 10
SAVED_API_KEY = ""
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

VOICES_BY_LANG = {
    "မြန်မာ (Burmese Voice)": {
        "Thiha (သီဟ - အမျိုးသားသဘာဝအသံ)": "my-MM-ThihaNeural",
        "Nilar (နီလာ - အမျိုးသမီးသဘာဝအသံ)": "my-MM-NilarNeural",
    },
    "English Voice": {
        "Andrew (Male - Natural)": "en-US-AndrewNeural",
        "Ava (Female - Natural)": "en-US-AvaNeural",
    },
    "ไทย (Thai Voice)": {
        "Niwat (ผู้ชาย - ธรรมชาติ)": "th-TH-NiwatNeural",
        "Premwadee (ผู้หญิง - ธรรมชาติ)": "th-TH-PremwadeeNeural",
    },
    "中文 (Chinese Voice)": {
        "Yunxi (男声 - 自然)": "zh-CN-YunxiNeural",
        "Xiaoxiao (女声 - 自然)": "zh-CN-XiaoxiaoNeural",
    }
}

SUBTITLE_LANG_CHOICES = ["မြန်မာ (Burmese)", "English", "ไทย (Thai)", "中文 (Chinese)"]

FONT_CHOICES = [
    "Pyidaungsu",
    "Padauk",
    "Myanmar Text",
    "Dancing Script",
    "Pacifico",
    "Caveat",
    "Arial",
    "sans-serif"
]

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
        return 0.0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print("Duration Error:", e)
    return 0.0

def has_audio_stream(video_path):
    if not video_path or not os.path.exists(video_path):
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )
        return "audio" in result.stdout.lower()
    except Exception:
        return False

def validate_video_duration(video_path):
    duration = get_video_duration(video_path)
    if duration <= 0:
        return True, "ℹ️ Video duration စစ်ဆေးပြီးပါပြီ။"
    minutes = duration / 60
    if minutes > MAX_VIDEO_MINUTES:
        return False, f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ ခွင့်ပြုထားပါသည်။"
    return True, f"✅ Video Length: {minutes:.1f} မိနစ်"

def clean_script_for_tts(script_text):
    if not script_text:
        return ""
    cleaned = []
    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue|Intro)\]\s*[:\-]?\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"^\s*Narrator\s*:\s*", "", line, flags=re.IGNORECASE)
        if line.lower() in ["movie recap", "recap script", "burmese recap script", "script"] or line.startswith("---"):
            continue
        cleaned.append(line.strip())
    return "\n".join(cleaned)

def split_into_two_lines(text, max_line_len=24):
    text = text.strip()
    if len(text) <= max_line_len:
        return text
    words = text.split(" ")
    if len(words) >= 2:
        mid = len(words) // 2
        return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
    mid_idx = len(text) // 2
    return text[:mid_idx] + "\n" + text[mid_idx:]

def seconds_to_srt_time(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def generate_srt_and_zip(script_text, total_target_duration=None, prefix="recap_sub"):
    clean_text = clean_script_for_tts(script_text)
    if not clean_text:
        return None, None
    raw_lines = [x.strip() for x in clean_text.splitlines() if x.strip()]
    if not raw_lines:
        return None, None

    formatted_chunks = []
    for rl in raw_lines:
        if len(rl) > 40:
            parts = re.split(r'(?<=[။၊.,!?])\s*', rl)
            for p in parts:
                if p.strip():
                    formatted_chunks.append(split_into_two_lines(p.strip()))
        else:
            formatted_chunks.append(split_into_two_lines(rl))

    srt_content = ""
    current_time = 0.0

    if total_target_duration and total_target_duration > 0:
        total_chars = sum(len(line.replace("\n", "")) for line in formatted_chunks)
        for idx, line in enumerate(formatted_chunks, 1):
            line_weight = len(line.replace("\n", "")) / max(1, total_chars)
            dur = max(1.2, line_weight * total_target_duration)
            start_time = current_time
            end_time = min(total_target_duration, current_time + dur)
            srt_content += f"{idx}\n{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n{line}\n\n"
            current_time = end_time
    else:
        for idx, line in enumerate(formatted_chunks, 1):
            dur = max(2.0, min(6.0, len(line.replace("\n", "")) / 10.0))
            start_time = current_time
            end_time = current_time + dur
            srt_content += f"{idx}\n{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n{line}\n\n"
            current_time = end_time

    srt_filename = f"{prefix}_subtitle.srt"
    zip_filename = f"{prefix}_subtitle.zip"

    with open(srt_filename, "w", encoding="utf-8-sig") as f:
        f.write(srt_content)

    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(srt_filename, arcname=srt_filename)

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
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link.strip(), download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
            base = os.path.splitext(filename)[0]
            for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                if os.path.exists(base + ext):
                    return base + ext
    except Exception as e:
        print("Download Error:", e)
    return None

# =========================================================
# TRANSLATION & SCRIPTING ENGINE
# =========================================================
def has_foreign_text(text):
    if not text:
        return False
    return bool(re.search(r"[a-zA-Z]{3,}", text) or re.search(r"[\u0E00-\u0E7F]", text) or re.search(r"[\u4E00-\u9FFF]", text))

def translate_to_target_language(text, target_lang):
    global SAVED_API_KEY
    if not text or not text.strip():
        return ""
    if not SAVED_API_KEY:
        raise ValueError("Gemini API Key မရှိသေးပါ။ 🔑 API Key Setting ထဲတွင် အရင်ထည့်သွင်းပေးပါ။")

    client = genai.Client(api_key=SAVED_API_KEY)
    prompt = f"""
Translate the following movie recap lines into {target_lang} for clean cinematic subtitles.
Keep each line concise, natural, and accurately mapped to storytelling tone.
Output ONLY the translated lines without any markdown formatting or commentary.

Text:
{text}
"""
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text:
                return clean_script_for_tts(response.text)
        except Exception:
            continue
    return text

def build_recap_prompt(selected_ratio, voice_language, video_duration=None):
    dur_guidance = f"\nVideo Duration: {video_duration:.1f} seconds. Balance narration timing accurately." if video_duration else ""

    return f"""
မင်းက Video Editing, AI Scripting နဲ့ Automation လုပ်ငန်းစဉ်တွေကို ကျွမ်းကျင်တဲ့ "Advanced AI Video Recap Generator" ဖြစ်သည်။
ရိုးရိုးပုံပြင်ပြောတဲ့ ပုံစံမျိုး လုံးဝမရေးသားရ။
ပရော်ဖက်ရှင်နယ် ရုပ်ရှင်ဇာတ်လမ်းပြော (Narrator) ပုံစံဖြင့် အလွန်ဆွဲဆောင်မှုရှိအောင် ရေးသားပေးပါ။
ဇာတ်လမ်းပြောနေစဉ်အတွင်း ဇာတ်ကောင်တွေရဲ့ ခံစားချက်ကို ပေါ်လွင်စေရန် အထဲက ဇာတ်ကောင်များ အပြန်အလှန်ပြောစကား (Dialogues) များကို သဘာဝကျကျ ထည့်သွင်းပေးပါ။

Target Aspect Ratio = {selected_ratio}{dur_guidance}
Target Narration Language = {voice_language}

စည်းကမ်းချက်များ-
၁။ [Visual], [Scene], [Narrator], [Dialogue], [Intro] စသည့် Tag များ လုံးဝမထည့်ရ။
၂။ TTS ဖြင့် အသံထွက်ရာတွင် ချောမွေ့ပြေပြစ်စေရန် စာကြောင်းများကို ကာရန်ကျပြီး စည်းဝါးကျအောင် ဖွဲ့စည်းပေးပါ။
"""

def run_gemini_video_analysis(target_media, ratio_choice, voice_language="မြန်မာ (Burmese Voice)"):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        raise ValueError("Gemini API Key မရှိသေးပါ။ 🔑 API Key Setting ထဲတွင် အရင်ထည့်သွင်းပေးပါ။")

    valid, msg = validate_video_duration(target_media)
    if not valid:
        raise ValueError(msg)

    client = genai.Client(api_key=SAVED_API_KEY)
    uploaded_file = client.files.upload(file=target_media)

    start_wait = time.time()
    while True:
        if uploaded_file.state and uploaded_file.state.name == "ACTIVE":
            break
        if uploaded_file.state and uploaded_file.state.name == "FAILED":
            raise RuntimeError("Gemini Video Processing မအောင်မြင်ပါ။ ဗီဒီယိုဖိုင်ကို စစ်ဆေးပါ။")
        if time.time() - start_wait > 600:
            raise TimeoutError("Gemini Video Processing ကြာမြင့်လွန်းနေပါသည်။")
        time.sleep(3)
        uploaded_file = client.files.get(name=uploaded_file.name)

    v_dur = get_video_duration(target_media)
    prompt = build_recap_prompt(ratio_choice, voice_language, v_dur)

    last_error = None
    for model_name in GEMINI_MODELS:
        for _ in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded_file, prompt],
                )
                if response and response.text:
                    clean_text = clean_script_for_tts(response.text)
                    return clean_text, model_name, msg
            except Exception as e:
                last_error = e
                time.sleep(2)

    raise RuntimeError(f"Gemini စာသားထုတ်လုပ်မှု မအောင်မြင်ပါ။ အသေးစိတ်: {last_error}")

# =========================================================
# TTS LOGIC
# =========================================================
async def generate_tts_file(text, voice_code, speed_percent, output_name="output_voice.mp3"):
    clean_text = clean_script_for_tts(text)
    if not clean_text:
        return None
    rate_str = f"{int(speed_percent):+d}%"
    communicate = edge_tts.Communicate(clean_text, voice_code, rate=rate_str)
    await communicate.save(output_name)
    return output_name

# =========================================================
# CSS & REAL-TIME PREVIEW ENGINE (INSTANT & DYNAMIC)
# =========================================================
def get_ratio_css(ratio, container_id="tab1_preview_container"):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "420px"},
        "3:4": {"aspect": "3 / 4", "max_w": "360px"},
        "16:9": {"aspect": "16 / 9", "max_w": "620px"},
        "9:16": {"aspect": "9 / 16", "max_w": "320px"},
    }
    cfg = configs.get(ratio, configs["1:1"])
    return f"""
    <style id="{container_id}-style">
    #{container_id} {{
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: 0 auto !important;
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
        aspect-ratio: {cfg["aspect"]} !important;
        object-fit: cover !important;
        display: block !important;
    }}
    </style>
    """

def hex_to_rgba(hex_code, opacity):
    hex_code = hex_code.lstrip("#")
    if len(hex_code) == 6:
        r = int(hex_code[0:2], 16)
        g = int(hex_code[2:4], 16)
        b = int(hex_code[4:6], 16)
        return f"rgba({r}, {g}, {b}, {opacity})"
    return f"rgba(0, 0, 0, {opacity})"

def get_tab3_full_preview_html(
    sub_lang, ratio, flip_h, scale_val, x_off, y_off, crop_w_pct, crop_h_pct,
    use_blur_bg, bg_color, bright_val, contrast_val, mask_enable, mask_type,
    mask_color, mask_opacity, mask_w, mask_h, mask_x, mask_y, logo_file,
    logo_size, logo_x, logo_y, font_family, font_size, font_color,
    outline_color, sub_x, sub_y
):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "420px"},
        "3:4": {"aspect": "3 / 4", "max_w": "360px"},
        "16:9": {"aspect": "16 / 9", "max_w": "580px"},
        "9:16": {"aspect": "9 / 16", "max_w": "320px"},
    }
    cfg = configs.get(ratio, configs["9:16"])
    flip_x = "-1" if flip_h else "1"

    mask_html = ""
    if mask_enable:
        mask_bg = hex_to_rgba(mask_color, mask_opacity)
        mask_backdrop = "backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);" if mask_type == "Blur (ဝေဝါးဖုံး)" else ""
        mask_html = f"""
        <div style="
            position: absolute;
            left: calc(50% + {mask_x}px);
            top: calc(50% - {mask_y}px);
            transform: translate(-50%, -50%);
            width: {mask_w}%;
            height: {mask_h}px;
            background: {mask_bg};
            {mask_backdrop}
            border-radius: 8px;
            z-index: 25;
            pointer-events: none;
            box-shadow: 0 0 10px rgba(0,0,0,0.3);
        "></div>
        """

    logo_html = ""
    if logo_file:
        try:
            with open(logo_file, "rb") as lf:
                encoded = base64.b64encode(lf.read()).decode()
            ext = os.path.splitext(logo_file)[1].lstrip(".").lower() or "png"
            logo_src = f"data:image/{ext};base64,{encoded}"
            logo_html = f"""
            <img src="{logo_src}" style="
                position: absolute;
                left: calc(50% + {logo_x}px);
                top: calc(50% - {logo_y}px);
                transform: translate(-50%, -50%);
                width: {logo_size}px;
                height: {logo_size}px;
                border-radius: 50%;
                object-fit: cover;
                border: 2px solid rgba(255,255,255,0.85);
                box-shadow: 0 2px 8px rgba(0,0,0,0.5);
                z-index: 30;
                pointer-events: none;
            "/>
            """
        except Exception:
            pass

    if sub_lang == "English":
        sample_l1 = "Cinematic Movie Recap Subtitle"
        sample_l2 = "( Live Screen Subtitle Preview )"
    elif sub_lang == "ไทย (Thai)":
        sample_l1 = "ตัวอย่างซับไตเติลภาพยนตร์"
        sample_l2 = "( แสดงผลสดบนหน้าจอ Preview )"
    elif sub_lang == "中文 (Chinese)":
        sample_l1 = "精彩电影解说实时字幕展示"
        sample_l2 = "( 屏幕预览对齐效果 )"
    else:
        sample_l1 = "ရုပ်ရှင်ဇာတ်လမ်းအညွှန်း စာတန်းထိုး"
        sample_l2 = "( Preview ဘောင်အတွင်း တိုက်ရိုက်ဖော်ပြမှု )"

    sub_html = f"""
    <div style="
        position: absolute;
        left: calc(50% + {sub_x}px);
        top: calc(50% - {sub_y}px);
        transform: translate(-50%, -50%);
        width: 95%;
        text-align: center;
        z-index: 35;
        pointer-events: none;
    ">
        <span style="
            display: block;
            font-family: '{font_family}', sans-serif;
            font-size: {font_size}px;
            line-height: 1.35;
            color: {font_color};
            text-shadow: -2px -2px 0 {outline_color}, 2px -2px 0 {outline_color}, -2px 2px 0 {outline_color}, 2px 2px 0 {outline_color}, 0px 3px 6px rgba(0,0,0,0.9);
            font-weight: 800;
        ">{sample_l1}</span>
        <span style="
            display: block;
            font-family: '{font_family}', sans-serif;
            font-size: {font_size}px;
            line-height: 1.35;
            color: {font_color};
            text-shadow: -2px -2px 0 {outline_color}, 2px -2px 0 {outline_color}, -2px 2px 0 {outline_color}, 2px 2px 0 {outline_color}, 0px 3px 6px rgba(0,0,0,0.9);
            font-weight: 800;
        ">{sample_l2}</span>
    </div>
    """

    inset_x = (100 - crop_w_pct) / 2
    inset_y = (100 - crop_h_pct) / 2
    clip_style = f"clip-path: inset({inset_y:.1f}% {inset_x:.1f}% {inset_y:.1f}% {inset_x:.1f}%);"

    return f"""
    <style id="tab3-live-preview-style">
    #tab3_preview_box {{
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: 0 auto !important;
        position: relative !important;
    }}
    #tab3_preview_box .video-container {{
        width: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        height: auto !important;
        background: {bg_color} !important;
        border-radius: 14px !important;
        overflow: hidden !important;
        position: relative !important;
        box-shadow: 0 8px 25px rgba(0,0,0,0.45) !important;
    }}
    #tab3_preview_box video {{
        width: 100% !important;
        height: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        object-fit: cover !important;
        transform: scale({scale_val}) scaleX({flip_x}) translate({x_off}px, {y_off}px) !important;
        filter: brightness({bright_val}) contrast({contrast_val}) !important;
        {clip_style}
        display: block !important;
        position: relative !important;
        z-index: 5 !important;
        transition: transform 0.15s ease-out;
    }}
    </style>
    <div id="tab3_interactive_overlay" style="
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        width: 100%; height: 100%;
        overflow: hidden;
        border-radius: 14px;
        pointer-events: none;
        z-index: 20;
    ">
        {mask_html}
        {logo_html}
        {sub_html}
    </div>
    """

# =========================================================
# FFMPEG COMPOSER
# =========================================================
def hex_to_ass_color(hex_str):
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"&H00{b}{g}{r}&".upper()
    return "&H00FFFFFF&"

def render_advanced_clip(
    source_video, tts_audio, srt_path, bgm_audio, enable_orig_audio, bgm_volume,
    ratio_choice, flip_h, scale_val, x_off, y_off, crop_w_pct, crop_h_pct,
    use_blur_bg, bg_color, bright_val, contrast_val, mask_enable, mask_type,
    mask_color, mask_opacity, mask_w, mask_h, mask_x, mask_y, logo_file,
    logo_size, logo_x, logo_y, font_family, font_size, font_color,
    outline_color, sub_x, sub_y, output_filename="final_movie_recap.mp4"
):
    ratio_dims = {
        "1:1": (1080, 1080),
        "3:4": (810, 1080),
        "16:9": (1920, 1080),
        "9:16": (1080, 1920)
    }
    tw, th = ratio_dims.get(ratio_choice, (1080, 1920))
    flip_filter = "hflip," if flip_h else ""
    crop_filter = f"crop=iw*{crop_w_pct/100.0:.2f}:ih*{crop_h_pct/100.0:.2f},"
    color_filter = f"eq=brightness={bright_val - 1.0:.2f}:contrast={contrast_val:.2f}"
    bg_clean = bg_color.lstrip("#")

    filter_chains = []
    if use_blur_bg:
        filter_chains.append(
            f"[0:v]{flip_filter}{crop_filter}{color_filter},split=2[fg_raw][bg_raw];"
            f"[bg_raw]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=25:10,eq=brightness=-0.15[bg_blurred];"
            f"[fg_raw]scale=iw*{scale_val}:ih*{scale_val}:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blurred][fg_scaled]overlay=(W-w)/2+({x_off}):(H-h)/2+({y_off})[v_base]"
        )
    else:
        filter_chains.append(
            f"[0:v]{flip_filter}{crop_filter}{color_filter},scale=iw*{scale_val}:ih*{scale_val}:force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:(ow-iw)/2+({x_off}):(oh-ih)/2+({y_off}):color=0x{bg_clean}[v_base]"
        )

    current_v = "v_base"

    if mask_enable:
        mw = int(tw * (mask_w / 100.0))
        mh = int(mask_h * (th / 1920.0 * 2.0))
        mx = f"(W-{mw})/2+({mask_x})"
        my = f"(H-{mh})/2-({mask_y})"

        if mask_type == "Blur (ဝေဝါးဖုံး)":
            filter_chains.append(
                f"[{current_v}]split=2[v_m1][v_m2];"
                f"[v_m2]crop={mw}:{mh}:{mx}:{my},boxblur=20:8[v_blurred_crop];"
                f"[v_m1][v_blurred_crop]overlay={mx}:{my}[v_masked]"
            )
        else:
            m_hex = mask_color.lstrip("#")
            filter_chains.append(
                f"color=c=0x{m_hex}@{mask_opacity}:s={mw}x{mh}[mask_box];"
                f"[{current_v}][mask_box]overlay={mx}:{my}[v_masked]"
            )
        current_v = "v_masked"

    inputs_cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", source_video, "-i", tts_audio]
    next_input_idx = 2

    if logo_file and os.path.exists(logo_file):
        inputs_cmd.extend(["-i", logo_file])
        logo_idx = next_input_idx
        next_input_idx += 1
        lx = f"(W-{logo_size})/2+({logo_x})"
        ly = f"(H-{logo_size})/2-({logo_y})"
        filter_chains.append(
            f"[{logo_idx}:v]scale={logo_size}:{logo_size},format=yuva420p,"
            f"geq=lum='p(X,Y)':a='if(lte((X-W/2)^2+(Y-H/2)^2,(min(W,H)/2)^2),255,0)'[circular_logo];"
            f"[{current_v}][circular_logo]overlay={lx}:{ly}[v_logoed]"
        )
        current_v = "v_logoed"

    primary_ass = hex_to_ass_color(font_color)
    outline_ass = hex_to_ass_color(outline_color)
    escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    calc_margin_v = max(10, int((th / 2) - sub_y))
    sub_style = (
        f"subtitles='{escaped_srt}':force_style="
        f"'FontName={font_family},FontSize={font_size},"
        f"PrimaryColour={primary_ass},OutlineColour={outline_ass},"
        f"BorderStyle=3,Outline=3,Shadow=2,Alignment=2,MarginV={calc_margin_v}'"
    )
    filter_chains.append(f"[{current_v}]{sub_style}[vout]")

    audio_inputs_count = 2
    if bgm_audio and os.path.exists(bgm_audio):
        inputs_cmd.extend(["-stream_loop", "-1", "-i", bgm_audio])
        bgm_idx = next_input_idx
        next_input_idx += 1
        audio_inputs_count += 1

    orig_has_audio = has_audio_stream(source_video)
    audio_filters = []
    if enable_orig_audio and orig_has_audio:
        audio_filters.append("[0:a]volume=0.2[orig_a];")
    else:
        audio_filters.append("aevalsrc=0:d=1[orig_a];")

    audio_filters.append("[1:a]volume=1.0[tts_a];")

    if audio_inputs_count == 3:
        audio_filters.append(f"[{bgm_idx}:a]volume={bgm_volume}[bgm_a];")
        audio_filters.append("[orig_a][tts_a][bgm_a]amix=inputs=3:duration=first:dropout_transition=2[aout]")
    else:
        audio_filters.append("[orig_a][tts_a]amix=inputs=2:duration=first:dropout_transition=2[aout]")

    full_filter_complex = ";".join(filter_chains) + ";" + "".join(audio_filters)
    tts_dur = get_video_duration(tts_audio)
    if tts_dur <= 0:
        tts_dur = 10.0

    cmd = inputs_cmd + [
        "-filter_complex", full_filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", str(tts_dur),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        output_filename
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print("FFmpeg Error:\n", e.stderr)
        fallback_vf = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2"
        fb_cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", source_video, "-i", tts_audio,
            "-vf", fallback_vf,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(tts_dur),
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            output_filename
        ]
        subprocess.run(fb_cmd, check=True)

    return output_filename

# =========================================================
# CONTROLLER LOGIC (WITH FAST SCRIPT GENERATION FOR TAB 2)
# =========================================================
def tab1_analyze(v_file, v_url, ratio):
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return "", "", "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ထည့်ပါ။", None, None
    try:
        clean_text, model, dur_msg = run_gemini_video_analysis(target, ratio, "မြန်မာ (Burmese Voice)")
        srt, zip_f = generate_srt_and_zip(clean_text)
        status = f"✅ Script ရေးသားပြီးပါပြီ! (Model: {model})\n{dur_msg}"
        return clean_text, clean_text, status, srt, zip_f
    except Exception as e:
        return "", "", f"❌ Error: {str(e)}", None, None

def fast_video_to_script_tab2(v_file, v_url):
    """Tab 2 တွင် ဗီဒီယိုဖိုင် သို့မဟုတ် Link မှတဆင့် Script ကို အမြန်ဆုံးထုတ်ယူပေးရန်"""
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return "", "⚠️ ဗီဒီယိုဖိုင် သို့မဟုတ် Link မရှိပါ။ ဖိုင် အရင်ထည့်ပါ။"
    try:
        # Fast & Optimized Prompt for Tab 2
        global SAVED_API_KEY
        if not SAVED_API_KEY:
            return "", "⚠️ Gemini API Key လိုအပ်ပါသည်။ API Key Setting တွင် အရင်ထည့်ပါ။"
        
        client = genai.Client(api_key=SAVED_API_KEY)
        uploaded_file = client.files.upload(file=target)
        
        # Wait for active
        while True:
            if uploaded_file.state and uploaded_file.state.name == "ACTIVE":
                break
            if uploaded_file.state and uploaded_file.state.name == "FAILED":
                return "", "❌ Video processing failed on Gemini server."
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)

        prompt = "Write a fast, engaging movie recap narration script in Burmese based on this video. No tags. Just natural conversational script."
        res = client.models.generate_content(model="gemini-2.5-flash", contents=[uploaded_file, prompt])
        if res and res.text:
            cleaned = clean_script_for_tts(res.text)
            return cleaned, "✅ ဗီဒီယိုမှ Script ကို အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ!"
    except Exception as e:
        return "", f"❌ Script Generation Error: {str(e)}"
    return "", "❌ Unknown Error"

def handle_direct_translate(text):
    if not text or not text.strip():
        return "", "⚠️ ဘာသာပြန်ရန် စာသားထည့်ပေးပါ။"
    try:
        translated = translate_to_target_language(text, "Burmese (မြန်မာ)")
        return translated, "✅ မြန်မာဘာသာသို့ အောင်မြင်စွာ ပြန်ဆိုပြီးပါပြီ။"
    except Exception as e:
        return text, f"❌ Translation Error: {str(e)}"

def tab2_tts_with_auto_translate(text, voice_label, speed):
    current_text = text
    trans_note = ""
    if has_foreign_text(current_text):
        try:
            current_text = translate_to_target_language(current_text, "Burmese (မြန်မာ)")
            trans_note = " (အလိုအလျောက် မြန်မာပြန်ဆိုထားပါသည်)"
        except Exception as e:
            print("Auto Translate Error:", e)

    voice_code = VOICES_BY_LANG["မြန်မာ (Burmese Voice)"].get(voice_label, "my-MM-ThihaNeural")
    try:
        audio_name = asyncio.run(generate_tts_file(current_text, voice_code, speed, "tab2_output.mp3"))
        audio_dur = get_video_duration(audio_name)
        srt_f, zip_f = generate_srt_and_zip(current_text, total_target_duration=audio_dur, prefix="tab2_output")
        status_msg = f"✅ အသံဖိုင် ဖန်တီးပြီးပါပြီ!{trans_note}"
        return current_text, audio_name, audio_name, srt_f, zip_f, status_msg
    except Exception as e:
        return current_text, None, None, None, None, f"❌ Error: {str(e)}"

def update_voice_choices(voice_lang):
    voices = list(VOICES_BY_LANG[voice_lang].keys())
    return gr.Dropdown(choices=voices, value=voices[0])

def tab3_auto_pipeline(
    v_file, v_url, bgm_file, voice_lang, voice_label, speed, sub_lang,
    enable_orig_audio, bgm_vol, ratio, flip_h, scale_val, x_off, y_off,
    crop_w, crop_h, use_blur_bg, bg_color, bright_val, contrast_val,
    mask_enable, mask_type, mask_color, mask_opacity, mask_w, mask_h,
    mask_x, mask_y, logo_file, logo_size, logo_x, logo_y, font_family,
    font_size, font_color, outline_color, sub_x, sub_y
):
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return None, None, "", None, "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ကို စစ်ဆေးပေးပါ။"

    try:
        narration_script, model, dur_msg = run_gemini_video_analysis(target, ratio, voice_lang)

        subtitle_script = narration_script
        need_sub_trans = False
        if "Burmese" in voice_lang and sub_lang != "မြန်မာ (Burmese)":
            need_sub_trans = True
        elif "English" in voice_lang and sub_lang != "English":
            need_sub_trans = True
        elif "Thai" in voice_lang and sub_lang != "ไทย (Thai)":
            need_sub_trans = True
        elif "Chinese" in voice_lang and sub_lang != "中文 (Chinese)":
            need_sub_trans = True

        if need_sub_trans:
            subtitle_script = translate_to_target_language(narration_script, sub_lang)

        voice_code = VOICES_BY_LANG[voice_lang].get(voice_label, list(VOICES_BY_LANG[voice_lang].values())[0])
        audio_file = asyncio.run(
            generate_tts_file(narration_script, voice_code, speed, "tab3_voice.mp3")
        )
        audio_dur = get_video_duration(audio_file)
        srt_file, _ = generate_srt_and_zip(subtitle_script, total_target_duration=audio_dur, prefix="tab3_sub")

        final_video = render_advanced_clip(
            source_video=target,
            tts_audio=audio_file,
            srt_path=srt_file,
            bgm_audio=bgm_file,
            enable_orig_audio=enable_orig_audio,
            bgm_volume=bgm_vol,
            ratio_choice=ratio,
            flip_h=flip_h,
            scale_val=scale_val,
            x_off=x_off,
            y_off=y_off,
            crop_w_pct=crop_w,
            crop_h_pct=crop_h,
            use_blur_bg=use_blur_bg,
            bg_color=bg_color,
            bright_val=bright_val,
            contrast_val=contrast_val,
            mask_enable=mask_enable,
            mask_type=mask_type,
            mask_color=mask_color,
            mask_opacity=mask_opacity,
            mask_w=mask_w,
            mask_h=mask_h,
            mask_x=mask_x,
            mask_y=mask_y,
            logo_file=logo_file,
            logo_size=logo_size,
            logo_x=logo_x,
            logo_y=logo_y,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            outline_color=outline_color,
            sub_x=sub_x,
            sub_y=sub_y,
            output_filename="final_movie_recap.mp4"
        )

        status_msg = f"🎉 Video အပြီးစီး အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ!\n🎤 အသံ: {voice_lang}\n📝 စာတန်းထိုး: {sub_lang}\n🎬 Model: {model}\n{dur_msg}"
        return final_video, final_video, subtitle_script, srt_file, status_msg
    except Exception as e:
        return None, None, "", None, f"❌ Error ဖြစ်ပေါ်ပါသည်: {str(e)}"

# =========================================================
# GRADIO UI
# =========================================================
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**All-in-One Professional Multilingual Movie Recap Studio**")

    with gr.Tabs() as main_tabs:

        # --- API KEY TAB ---
        with gr.TabItem("🔑 API Key Setting", id="tab_key"):
            gr.Markdown("### 🔐 Gemini API Key ထည့်သွင်းပါ")
            api_key_input = gr.Textbox(label="Gemini API Key", type="password", placeholder="AIzaSy...")
            save_key_btn = gr.Button("💾 API Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")
            save_key_btn.click(save_api_key, inputs=api_key_input, outputs=key_status)

        # --- TAB 1: SCRIPT ---
        with gr.TabItem("1️⃣ Video Analysis & Script", id="tab_script"):
            with gr.Row():
                with gr.Column(scale=1):
                    v1_file = gr.Video(label="📹 Video File တင်ရန်")
                    v1_url = gr.Textbox(label="🔗 Video URL Link (YouTube, TikTok, Facebook, RedNote စသည်)")
                    v1_load_btn = gr.Button("🔍 Link မှ Video ရယူမည်", variant="secondary")
                    v1_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="1:1", label="📐 Preview Screen Aspect Ratio")
                    v1_gen_btn = gr.Button("🚀 Recap Script စတင်ထုတ်မည်", variant="primary")
                with gr.Column(scale=1):
                    v1_css = gr.HTML(get_ratio_css("1:1", "tab1_preview_container"))
                    v1_preview = gr.Video(label="📺 Video Preview (ချက်ချင်းတန်းပေါ်)", elem_id="tab1_preview_container")
                    v1_status = gr.Markdown("ဗီဒီယိုထည့်သွင်းရန် အဆင်သင့်ဖြစ်ပါသည်။")
                    v1_script_out = gr.Textbox(label="🎬 ထွက်ရှိလာသော Script", lines=10)
                    go_to_tts_btn = gr.Button("🎙️ Tab 2 (TTS) သို့ သွားရောက် အသံထုတ်မည် ➡️", variant="secondary")
            with gr.Row():
                v1_srt = gr.File(label="📄 SRT စာတန်းထိုး ဖိုင်")
                v1_zip = gr.File(label="📦 SRT ZIP ဒေါင်းလုဒ်")

        # --- TAB 2: TTS & FAST SCRIPT ---
        with gr.TabItem("2️⃣ Text-to-Speech & Fast Script", id="tab_tts"):
            gr.Markdown("### ⚡ Video မှ Script ကို အမြန်ဆုံးထုတ်ယူပြီး အသံဖိုင်ပြောင်းလဲရန်")
            with gr.Row():
                with gr.Column(scale=1):
                    v2_vid_file = gr.Video(label="📹 Video File တင်ရန် (Tab 2 အတွက်)")
                    v2_vid_url = gr.Textbox(label="🔗 Video URL Link")
                    v2_fast_script_btn = gr.Button("🚀 ဗီဒီယိုမှ Script အမြန်ထုတ်မည်", variant="secondary")
                    
                    v2_input_text = gr.Textbox(
                        label="🎙️ Movie Script (အင်္ဂလိပ်၊ ထိုင်း၊ တရုတ် စာသားများ ထည့်ပါက အလိုအလျောက် မြန်မာပြန်ပေးပါမည်)",
                        lines=10,
                        placeholder="မြန်မာစာ သို့မဟုတ် အင်္ဂလိပ်၊ ထိုင်း၊ တရုတ် စာသားများ ထည့်သွင်းနိုင်ပါသည်..."
                    )
                    v2_trans_btn = gr.Button("🌐 မြန်မာလို ပြန်ဆိုမည် (Eng/Thai/中文 → မြန်မာ)", variant="secondary")
                    v2_voice = gr.Dropdown(list(VOICES_BY_LANG["မြန်မာ (Burmese Voice)"].keys()), value="Thiha (သီဟ - အမျိုးသားသဘာဝအသံ)", label="🎤 အသံ ရွေးချယ်ပါ")
                    v2_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Speed (%)")
                    v2_btn = gr.Button("⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်", variant="primary")
                    v2_status = gr.Markdown("")
                with gr.Column(scale=1):
                    v2_audio = gr.Audio(label="🔊 Voice Preview (အသံစမ်းနားထောင်ရန်)", autoplay=True)
                    v2_mp3 = gr.File(label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်")
            with gr.Row():
                v2_srt = gr.File(label="📄 SRT")
                v2_zip = gr.File(label="📦 SRT ZIP")

        # --- TAB 3: ONE-CLICK ADVANCED MULTILINGUAL STUDIO ---
        with gr.TabItem("⚡ 3️⃣ One-Click All-in-One Video Studio", id="tab_auto"):
            gr.Markdown("### 🎛️ Video Crop, Extended Range Mask & Positioning Studio")
            with gr.Row():
                with gr.Column(scale=1):
                    t3_file = gr.Video(label="📹 Video File တင်ရန်")
                    t3_url = gr.Textbox(label="🔗 Video Link (YouTube, TikTok, Facebook, RedNote စသည်)")
                    t3_load_btn = gr.Button("🔍 Video ရယူ/စစ်ဆေးမည်", variant="secondary")

                    with gr.Accordion("🎤 အသံပိုင်းဆိုင်ရာ & Voice ဘာသာစကား", open=False):
                        t3_voice_lang = gr.Radio(
                            list(VOICES_BY_LANG.keys()),
                            value="မြန်မာ (Burmese Voice)",
                            label="🎙️ ဇာတ်လမ်းပြောမည့် အသံ (Voice Language)"
                        )
                        t3_voice = gr.Dropdown(
                            list(VOICES_BY_LANG["မြန်မာ (Burmese Voice)"].keys()),
                            value=list(VOICES_BY_LANG["မြန်မာ (Burmese Voice)"].keys())[0],
                            label="🎤 Voice ရွေးချယ်ပါ (သီဟ / နီလာ စသည်)"
                        )
                        t3_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Voice Speed (%)")
                        t3_orig_audio = gr.Checkbox(label="🔊 မူရင်း Video အသံ ဖွင့်မည် (Background အနေဖြင့်)", value=False)
                        t3_bgm_file = gr.Audio(label="🎵 Background Music (BGM) ထည့်ရန်", type="filepath")
                        t3_bgm_vol = gr.Slider(0.0, 1.0, value=0.15, step=0.05, label="🎚️ BGM အသံအတိုး/အလျှော့ (Volume)")

                    with gr.Accordion("✍️ စာတန်းထိုး ဘာသာစကား & နေရာဆွဲရွှေ့ခြင်း (အပေါ်အောက် အဆုံးထိရွှေ့နိုင်သည်)", open=True):
                        t3_sub_lang = gr.Radio(
                            SUBTITLE_LANG_CHOICES,
                            value="မြန်မာ (Burmese)",
                            label="📝 စာတန်းထိုး ဘာသာစကား"
                        )
                        t3_font = gr.Dropdown(FONT_CHOICES, value="Pyidaungsu", label="🔤 Font ဒီဇိုင်း (Handwriting/Design Fonts)")
                        t3_fsize = gr.Slider(14, 48, value=22, step=1, label="📏 စာလုံး အရွယ်အစား")
                        with gr.Row():
                            t3_fcolor = gr.ColorPicker(label="🎨 စာလုံးအရောင်", value="#00E676")
                            t3_ocolor = gr.ColorPicker(label="🖌️ အနားသတ် အကြမ်းရောင် (Outline)", value="#000000")
                        with gr.Row():
                            t3_sub_x = gr.Slider(-500, 500, value=0, step=5, label="↔️ စာတန်းထိုး ဘယ်/ညာ ရွှေ့မည်")
                            t3_sub_y = gr.Slider(-750, 750, value=-260, step=5, label="↕️ စာတန်းထိုး အပေါ်/အောက် ရွှေ့မည် (အဆုံးထိရွှေ့နိုင်)")

                    with gr.Accordion("✂️ Video Crop (အတိုး/အလျော့ဖြတ်တောက်မှု) & ပုံစံ", open=True):
                        t3_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="9:16", label="📐 Aspect Ratio ရွေးပါ")
                        with gr.Row():
                            t3_crop_w = gr.Slider(30, 100, value=100, step=1, label="✂️ ဘယ်/ညာ Crop အကျယ် (%)")
                            t3_crop_h = gr.Slider(30, 100, value=100, step=1, label="✂️ အပေါ်/အောက် Crop အမြင့် (%)")
                        t3_scale = gr.Slider(0.5, 2.5, value=1.0, step=0.05, label="🔍 Video Zoom အကြီး/အသေး")
                        t3_flip = gr.Checkbox(label="🔄 ဗီဒီယို ဘယ်ညာလှန်မည် (Horizontal Flip)", value=False)
                        with gr.Row():
                            t3_x_off = gr.Slider(-500, 500, value=0, step=5, label="↔️ Video ဘယ်/ညာ ရွှေ့မည်")
                            t3_y_off = gr.Slider(-500, 500, value=0, step=5, label="↕️ Video အပေါ်/အောက် ရွှေ့မည်")
                        t3_blur_bg = gr.Checkbox(label="🌫️ Background Blur (ဝေဝါးသော နောက်ခံ) အသုံးပြုမည်", value=True)
                        with gr.Row():
                            t3_bright = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="☀️ Brightness (အလင်း/အမှောင်)")
                            t3_contrast = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="🌓 Contrast (အနု/အရင့်)")
                        t3_bgcolor = gr.ColorPicker(label="🎨 Canvas အရောင် (Blur ပိတ်ထားပါက)", value="#000000")

                    with gr.Accordion("🛡️ မူရင်းစာတန်းထိုး ဖုံးအုပ်မည့် Mask/Blur (အပေါ်အောက် အဆုံးထိရွှေ့နိုင်သည်)", open=True):
                        t3_mask_enable = gr.Checkbox(label="✅ စာတန်းထိုး ဖုံးအုပ်မည့် Mask ဖွင့်မည်", value=True)
                        t3_mask_type = gr.Radio(["Blur (ဝေဝါးဖုံး)", "Color Box (အရောင်အတုံးဖြင့်ဖုံး)"], value="Blur (ဝေဝါးဖုံး)", label="🎭 Mask ပုံစံ")
                        with gr.Row():
                            t3_mask_color = gr.ColorPicker(label="🎨 Mask အရောင် (အရောင်စုံရွေးနိုင်)", value="#000000")
                            t3_mask_opacity = gr.Slider(0.0, 1.0, value=0.85, step=0.05, label="💧 Opacity (အရောင် အတိုး/အလျှော့)")
                        with gr.Row():
                            t3_mask_w = gr.Slider(10, 100, value=85, step=1, label="↔️ Mask အကျယ် (%)")
                            t3_mask_h = gr.Slider(10, 300, value=85, step=2, label="↕️ Mask အမြင့် (px)")
                        with gr.Row():
                            t3_mask_x = gr.Slider(-500, 500, value=0, step=5, label="↔️ Mask ဘယ်/ညာ ရွှေ့မည်")
                            t3_mask_y = gr.Slider(-750, 750, value=-260, step=5, label="↕️ Mask အပေါ်/အောက် ရွှေ့မည် (အဆုံးထိရွှေ့နိုင်)")

                    with gr.Accordion("🏷️ Logo တံဆိပ် ထည့်သွင်းခြင်း (အဝိုင်းပုံစံ Circle Frame)", open=False):
                        t3_logo_file = gr.Image(label="🖼️ Logo ပုံတင်ရန် (PNG / JPG)", type="filepath")
                        t3_logo_size = gr.Slider(30, 400, value=100, step=5, label="📏 Logo အရွယ်အစား (px)")
                        with gr.Row():
                            t3_logo_x = gr.Slider(-500, 500, value=0, step=5, label="↔️ Logo ဘယ်/ညာ ရွှေ့မည်")
                            t3_logo_y = gr.Slider(-750, 750, value=300, step=5, label="↕️ Logo အပေါ်/အောက် ရွှေ့မည်")

                    t3_run_btn = gr.Button("✨ Video အပြီးစီး One-Click ထုတ်လုပ်မည်", variant="primary", size="lg")

                with gr.Column(scale=1):
                    t3_preview_css = gr.HTML(
                        get_tab3_full_preview_html(
                            "မြန်မာ (Burmese)", "9:16", False, 1.0, 0, 0, 100, 100, True,
                            "#000000", 1.0, 1.0, True, "Blur (ဝေဝါးဖုံး)", "#000000",
                            0.85, 85, 85, 0, -260, None, 100, 0, 300, "Pyidaungsu",
                            22, "#00E676", "#000000", 0, -260
                        )
                    )
                    t3_live_video = gr.Video(label="📺 Real-Time Preview (ဗီဒီယိုတင်လိုက်တာနဲ့ ချက်ချင်းတန်းပေါ်မည်)", elem_id="tab3_preview_box")
                    t3_status = gr.Markdown("စာတန်းထိုး၊ Blur Mask နှင့် Circle Logo များကို Preview Video ဘောင်အတွင်း အပေါ်အောက် အဆုံးထိ စိတ်ကြိုက်ဆွဲရွှေ့နိုင်ပါသည်။")
                    t3_final_video = gr.Video(label="🎬 အပြီးစီး Final Video Output (Playable)")
                    t3_script_view = gr.Textbox(label="📝 ထွက်ရှိလာသော စာတန်းထိုး Script", lines=5)
                    with gr.Row():
                        t3_download = gr.File(label="📥 Final Video ဒေါင်းလုဒ်")
                        t3_srt = gr.File(label="📄 SRT စာတန်းထိုး ဒေါင်းလုဒ်")

    # ================= EVENT BINDINGS (INSTANT PREVIEW & QUEUE SAFE) =================
    # Tab 1
    v1_file.change(lambda f: f, inputs=v1_file, outputs=v1_preview)
    v1_load_btn.click(download_video_from_link, inputs=v1_url, outputs=v1_preview)
    v1_ratio.change(lambda r: get_ratio_css(r, "tab1_preview_container"), inputs=v1_ratio, outputs=v1_css)
    v1_gen_btn.click(
        tab1_analyze,
        inputs=[v1_file, v1_url, v1_ratio],
        outputs=[v1_script_out, v2_input_text, v1_status, v1_srt, v1_zip]
    )
    go_to_tts_btn.click(lambda: gr.Tabs(selected="tab_tts"), outputs=main_tabs)

    # Tab 2 (Fast Script + TTS)
    v2_fast_script_btn.click(fast_video_to_script_tab2, inputs=[v2_vid_file, v2_vid_url], outputs=[v2_input_text, v2_status])
    v2_trans_btn.click(handle_direct_translate, inputs=v2_input_text, outputs=[v2_input_text, v2_status])
    v2_btn.click(
        tab2_tts_with_auto_translate,
        inputs=[v2_input_text, v2_voice, v2_speed],
        outputs=[v2_input_text, v2_audio, v2_mp3, v2_srt, v2_zip, v2_status]
    )

    # Tab 3 - Voice Selection
    t3_voice_lang.change(update_voice_choices, inputs=t3_voice_lang, outputs=t3_voice)

    # Tab 3: Video File ထည့်လိုက်တာနဲ့ Preview မှာ ချက်ချင်းတန်းပေါ်စေရန် ချိတ်ဆက်မှု
    t3_file.change(lambda f: f, inputs=t3_file, outputs=t3_live_video)
    t3_load_btn.click(download_video_from_link, inputs=t3_url, outputs=t3_live_video)

    # Tab 3 - Preview Updates
    preview_all_inputs = [
        t3_sub_lang, t3_ratio, t3_flip, t3_scale, t3_x_off, t3_y_off,
        t3_crop_w, t3_crop_h, t3_blur_bg, t3_bgcolor, t3_bright, t3_contrast,
        t3_mask_enable, t3_mask_type, t3_mask_color, t3_mask_opacity,
        t3_mask_w, t3_mask_h, t3_mask_x, t3_mask_y, t3_logo_file,
        t3_logo_size, t3_logo_x, t3_logo_y, t3_font, t3_fsize,
        t3_fcolor, t3_ocolor, t3_sub_x, t3_sub_y
    ]

    for comp in preview_all_inputs:
        comp.change(get_tab3_full_preview_html, inputs=preview_all_inputs, outputs=t3_preview_css)

    # Tab 3 - Final Execution
    t3_run_btn.click(
        tab3_auto_pipeline,
        inputs=[
            t3_file, t3_url, t3_bgm_file, t3_voice_lang, t3_voice, t3_speed,
            t3_sub_lang, t3_orig_audio, t3_bgm_vol, t3_ratio, t3_flip,
            t3_scale, t3_x_off, t3_y_off, t3_crop_w, t3_crop_h,
            t3_blur_bg, t3_bgcolor, t3_bright, t3_contrast,
            t3_mask_enable, t3_mask_type, t3_mask_color, t3_mask_opacity,
            t3_mask_w, t3_mask_h, t3_mask_x, t3_mask_y,
            t3_logo_file, t3_logo_size, t3_logo_x, t3_logo_y,
            t3_font, t3_fsize, t3_fcolor, t3_ocolor, t3_sub_x, t3_sub_y
        ],
        outputs=[t3_final_video, t3_download, t3_script_view, t3_srt, t3_status]
    )

# Server Launch with Queue enabled to completely prevent HTTP 502 Bad Gateway timeouts
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.queue(default_concurrency_limit=5).launch(
        server_name="0.0.0.0", 
        server_port=port,
        show_error=True
    )
