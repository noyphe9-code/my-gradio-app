import os
import re
import time
import zipfile
import subprocess
import asyncio
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

# Gemini API Models
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
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30
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
        return False, f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ လက်ခံပါသည်။"
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

def split_into_two_lines(text, max_line_len=18):
    """စာတန်းထိုး စာကြောင်းရှည်များကို ဓာတ်ပုံထဲကအတိုင်း အပေါ်အောက် ၂ ကြောင်း ခွဲပေးခြင်း"""
    text = text.strip()
    if len(text) <= max_line_len:
        return text
    
    words = text.split(" ")
    if len(words) >= 2:
        mid = len(words) // 2
        return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
    
    # Space မပါပါက စာလုံးရေအလယ်မှ ဖြတ်ခြင်း
    mid_idx = len(text) // 2
    return text[:mid_idx] + "\n" + text[mid_idx:]

def seconds_to_srt_time(seconds):
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def generate_srt_and_zip(script_text, total_target_duration=None, prefix="myanmar_recap"):
    clean_text = clean_script_for_tts(script_text)
    if not clean_text:
        return None, None
    raw_lines = [x.strip() for x in clean_text.splitlines() if x.strip()]
    if not raw_lines:
        return None, None

    # အပေါ်အောက် ၂ ကြောင်းစနစ်ဖြင့် စာတန်းထိုးများကို ဖွဲ့စည်းခြင်း
    formatted_chunks = []
    for rl in raw_lines:
        # စာကြောင်းရှည်လွန်းပါက အပိုင်းခွဲထုတ်ခြင်း
        if len(rl) > 36:
            parts = re.split(r'(?<=[။၊])\s*', rl)
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
            dur = max(1.5, line_weight * total_target_duration)
            start_time = current_time
            end_time = min(total_target_duration, current_time + dur)
            srt_content += f"{idx}\n{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n{line}\n\n"
            current_time = end_time
    else:
        for idx, line in enumerate(formatted_chunks, 1):
            dur = max(2.0, min(6.0, len(line.replace("\n", "")) / 9.0))
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
# TRANSLATION ENGINE
# =========================================================
def has_foreign_text(text):
    if not text:
        return False
    if re.search(r"[a-zA-Z]{3,}", text) or re.search(r"[\u0E00-\u0E7F]", text) or re.search(r"[\u4E00-\u9FFF]", text):
        return True
    return False

def translate_to_burmese(text):
    global SAVED_API_KEY
    if not text or not text.strip():
        return ""
    if not SAVED_API_KEY:
        raise ValueError("Gemini API Key မရှိသေးပါ။ 🔑 API Key Setting ထဲတွင် အရင်ထည့်သွင်းပေးပါ။")

    client = genai.Client(api_key=SAVED_API_KEY)
    prompt = f"""
You are an expert translator and movie storyteller.
Translate the following text into natural, engaging Burmese spoken narration for Movie Recap & Voice-Over.
[Rules]
1. Translate into fluent spoken Burmese narration.
2. Remove any timestamps, camera notes, or brackets.
3. Output ONLY the translated Burmese text without greetings or markdown wrappers.

Text:
{text}
"""
    last_error = None
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text:
                return clean_script_for_tts(response.text)
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"Translation failed: {last_error}")

# =========================================================
# DYNAMIC PREVIEW & CSS STYLING
# =========================================================
def get_ratio_css(ratio, container_id="tab1_preview_container"):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "450px"},
        "3:4": {"aspect": "3 / 4", "max_w": "380px"},
        "16:9": {"aspect": "16 / 9", "max_w": "640px"},
        "9:16": {"aspect": "9 / 16", "max_w": "320px"},
    }
    cfg = configs.get(ratio, configs["1:1"])
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
    }}
    </style>
    """

def get_tab3_preview_css(ratio, flip_h, scale_val, x_off, y_off, use_blur, bg_color, font_family, font_size, font_color, outline_color, sub_pos):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "420px"},
        "3:4": {"aspect": "3 / 4", "max_w": "360px"},
        "16:9": {"aspect": "16 / 9", "max_w": "580px"},
        "9:16": {"aspect": "9 / 16", "max_w": "320px"},
    }
    cfg = configs.get(ratio, configs["9:16"])
    flip_x = "-1" if flip_h else "1"
    sub_pos_css = "top: 15px;" if sub_pos == "အပေါ်ဘက်" else "bottom: 25px;"

    # Blur background effect in CSS preview
    blur_bg_style = "filter: blur(12px) brightness(0.65); transform: scale(1.15);" if use_blur else ""

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
        box-shadow: 0 8px 25px rgba(0,0,0,0.4) !important;
    }}
    #tab3_preview_box video {{
        width: 100% !important;
        height: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        object-fit: contain !important;
        transform: scale({scale_val}) scaleX({flip_x}) translate({x_off}px, {y_off}px) !important;
        display: block !important;
        position: relative !important;
        z-index: 2 !important;
        transition: transform 0.2s ease-out;
    }}
    #tab3_preview_sub_overlay {{
        position: absolute;
        {sub_pos_css}
        left: 0;
        right: 0;
        text-align: center;
        z-index: 99;
        pointer-events: none;
        padding: 0 10px;
    }}
    #tab3_preview_sub_overlay .sub-line {{
        display: block;
        font-family: '{font_family}', sans-serif;
        font-size: {font_size}px;
        line-height: 1.35;
        color: {font_color};
        text-shadow: 
            -2px -2px 0 {outline_color},  
             2px -2px 0 {outline_color},
            -2px  2px 0 {outline_color},
             2px  2px 0 {outline_color},
             0px 3px 6px rgba(0,0,0,0.9);
        font-weight: 800;
        letter-spacing: 0.5px;
    }}
    </style>
    <div id="tab3_preview_sub_overlay">
        <span class="sub-line">လူတစ်ယောက် တောင်ပေါ်မှာ</span>
        <span class="sub-line">( နမူနာစာတန်းထိုး ၂ ကြောင်း )</span>
    </div>
    """

# =========================================================
# GEMINI SCRIPT RECAP CORE
# =========================================================
def build_recap_prompt(selected_ratio, video_duration=None):
    dur_guidance = ""
    if video_duration:
        dur_guidance = f"\nVideo Length: {video_duration:.1f} seconds. Keep the script length and pacing naturally matched."

    return f"""
သင်သည် ထိပ်တန်း Professional Movie Recap Scriptwriter ဖြစ်သည်။ 
Target Aspect Ratio = {selected_ratio}{dur_guidance}

ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ တိကျသေချာစွာ ကြည့်ရှုနားထောင်ပြီး အောက်ပါစည်းမျဉ်းများအတိုင်း "မြန်မာ Movie Recap Script" ကို ရေးသားပေးပါ-

[စည်းမျဉ်းများ]
၁။ ဗီဒီယိုထဲတွင် တကယ်ဖြစ်ပျက်နေသော အဖြစ်အပျက် အမှန်များကိုသာ အချိန်အစဉ်လိုက် အတိုချုံး၍ တိကျမှန်ကန်စွာ ရေးပါ။
၂။ ဇာတ်ကောင်များ၏ ပြောစကားများကို သဘာဝကျကျ ဆွဲဆောင်မှုရှိသော မြန်မာစကားပြောအဖြစ် တိုက်ရိုက်ပြန်ဆိုပါ။
၃။ [Visual], [Scene], [Narrator], [Dialogue], [Intro] စသည့် Technical Label များနှင့် စကားအပိုများ လုံးဝမထည့်ပါနှင့်။
၄။ TTS အသံထွက်ဖတ်ရာတွင် ချောမွေ့စေရန် စာကြောင်းတစ်ကြောင်းချင်းစီကို တိုတိုရှင်းရှင်းနှင့် အဓိပ္ပာယ်ပြည့်စုံစွာ ရေးပေးပါ။
"""

def generate_with_retry(client, uploaded_file, prompt):
    retry_delays = [3, 7]
    last_error = None
    for model_name in GEMINI_MODELS:
        for attempt in range(len(retry_delays) + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded_file, prompt],
                )
                if response and response.text:
                    return response.text, model_name
            except Exception as e:
                last_error = e
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                else:
                    break
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")

def run_gemini_video_analysis(target_media, ratio_choice):
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
        if time.time() - start_wait > 900:
            raise TimeoutError("Gemini Video Processing ကြာမြင့်လွန်းနေပါသည်။")
        time.sleep(3)
        uploaded_file = client.files.get(name=uploaded_file.name)

    v_dur = get_video_duration(target_media)
    prompt = build_recap_prompt(ratio_choice, v_dur)
    script_text, used_model = generate_with_retry(client, uploaded_file, prompt)
    clean_text = clean_script_for_tts(script_text)
    return clean_text, used_model, msg

# =========================================================
# TTS LOGIC
# =========================================================
async def generate_myanmar_tts(text, voice_choice, speed_percent, output_name="output_voice.mp3"):
    clean_text = clean_script_for_tts(text)
    if not clean_text:
        return None, None, None
    selected_voice = VOICES.get(voice_choice, "my-MM-ThihaNeural")
    rate_str = f"{int(speed_percent):+d}%"
    communicate = edge_tts.Communicate(clean_text, selected_voice, rate=rate_str)
    await communicate.save(output_name)
    audio_dur = get_video_duration(output_name)
    srt_file, zip_file = generate_srt_and_zip(clean_text, total_target_duration=audio_dur, prefix=output_name.replace(".mp3", ""))
    return output_name, srt_file, zip_file

# =========================================================
# ADVANCED FFMPEG COMPOSER (BLUR BG & 2-LINE SUBTITLE)
# =========================================================
def hex_to_ass_color(hex_str):
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"&H00{b}{g}{r}&".upper()
    return "&H00FFFFFF&"

def render_advanced_clip(
    source_video, tts_audio, srt_path, bgm_audio,
    enable_orig_audio, bgm_volume,
    ratio_choice, flip_h, scale_val, x_off, y_off,
    use_blur, bg_color,
    font_family, font_size, font_color, outline_color, sub_pos,
    output_filename="final_movie_recap.mp4"
):
    ratio_dims = {
        "1:1": (1080, 1080),
        "3:4": (810, 1080),
        "16:9": (1920, 1080),
        "9:16": (1080, 1920)
    }
    tw, th = ratio_dims.get(ratio_choice, (1080, 1920))

    primary_ass = hex_to_ass_color(font_color)
    outline_ass = hex_to_ass_color(outline_color)
    alignment = "6" if sub_pos == "အပေါ်ဘက်" else "2"
    margin_v = "55"

    escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    sub_style = (
        f"subtitles='{escaped_srt}':force_style="
        f"'FontName={font_family},FontSize={font_size},"
        f"PrimaryColour={primary_ass},OutlineColour={outline_ass},"
        f"BorderStyle=3,Outline=3,Shadow=2,Alignment={alignment},MarginV={margin_v}'"
    )

    flip_filter = "hflip," if flip_h else ""
    bg_clean = bg_color.lstrip("#")

    # ဝေဝါးသော နောက်ခံ (Blurred Background) Filter Complex ဖန်တီးခြင်း
    if use_blur:
        filter_v = (
            f"[0:v]{flip_filter}split=2[fg_raw][bg_raw];"
            f"[bg_raw]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=25:10,eq=brightness=-0.1[bg_blurred];"
            f"[fg_raw]scale=iw*{scale_val}:ih*{scale_val}:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blurred][fg_scaled]overlay=(W-w)/2+({x_off}):(H-h)/2+({y_off})[v_comp];"
            f"[v_comp]{sub_style}[vout]"
        )
    else:
        filter_v = (
            f"[0:v]{flip_filter}scale=iw*{scale_val}:ih*{scale_val},"
            f"pad={tw}:{th}:({tw}-iw)/2+({x_off}):({th}-ih)/2+({y_off}):color=0x{bg_clean},"
            f"{sub_style}[vout]"
        )

    tts_dur = get_video_duration(tts_audio) or 10.0

    # Audio Mixing Configuration
    cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", source_video, "-i", tts_audio]
    audio_inputs_count = 2

    if bgm_audio and os.path.exists(bgm_audio):
        cmd.extend(["-stream_loop", "-1", "-i", bgm_audio])
        audio_inputs_count += 1

    audio_filters = []
    if enable_orig_audio:
        audio_filters.append("[0:a]volume=0.2[orig_a];")
    else:
        audio_filters.append("aevalsrc=0:d=1[orig_a];")

    audio_filters.append("[1:a]volume=1.0[tts_a];")

    if audio_inputs_count == 3:
        audio_filters.append(f"[2:a]volume={bgm_volume}[bgm_a];")
        audio_filters.append("[orig_a][tts_a][bgm_a]amix=inputs=3:duration=first:dropout_transition=2[aout]")
    else:
        audio_filters.append("[orig_a][tts_a]amix=inputs=2:duration=first:dropout_transition=2[aout]")

    filter_complex = f"{filter_v};" + "".join(audio_filters)

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(tts_dur),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        output_filename
    ])

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        print("Complex Render Failed, falling back to simple render:", e)
        clean_vf = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(OW-IW)/2:(OH-IH)/2"
        fb_cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", source_video, "-i", tts_audio,
            "-vf", clean_vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", str(tts_dur),
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
            output_filename
        ]
        subprocess.run(fb_cmd, check=True)

    return output_filename

# =========================================================
# TAB CONTROLLERS
# =========================================================
def tab1_analyze(v_file, v_url, ratio):
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return "", "", "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ထည့်ပါ။", None, None
    try:
        clean_text, model, dur_msg = run_gemini_video_analysis(target, ratio)
        srt, zip_f = generate_srt_and_zip(clean_text)
        status = f"✅ Script ရေးသားပြီးပါပြီ! (Model: {model})\n{dur_msg}"
        return clean_text, clean_text, status, srt, zip_f
    except Exception as e:
        return "", "", f"❌ Error: {str(e)}", None, None

def handle_direct_translate(text):
    if not text or not text.strip():
        return "", "⚠️ ဘာသာပြန်ရန် စာသားထည့်ပေးပါ။"
    try:
        translated = translate_to_burmese(text)
        return translated, "✅ မြန်မာဘာသာသို့ အောင်မြင်စွာ ပြန်ဆိုပြီးပါပြီ။"
    except Exception as e:
        return text, f"❌ Translation Error: {str(e)}"

def tab2_tts_with_auto_translate(text, voice, speed):
    current_text = text
    trans_note = ""
    if has_foreign_text(current_text):
        try:
            current_text = translate_to_burmese(current_text)
            trans_note = " (အလိုအလျောက် မြန်မာပြန်ဆိုထားပါသည်)"
        except Exception as e:
            print("Auto Translate Error:", e)

    try:
        mp3, srt, zip_f = asyncio.run(generate_myanmar_tts(current_text, voice, speed, "tab2_output.mp3"))
        status_msg = f"✅ အသံဖိုင် ဖန်တီးပြီးပါပြီ!{trans_note}"
        return current_text, mp3, mp3, srt, zip_f, status_msg
    except Exception as e:
        return current_text, None, None, None, None, f"❌ Error: {str(e)}"

def tab3_auto_pipeline(
    v_file, v_url, bgm_file,
    enable_orig_audio, bgm_vol,
    ratio, flip_h, scale_val, x_off, y_off,
    use_blur, bg_color,
    font_family, font_size, font_color, outline_color, sub_pos,
    voice, speed
):
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return None, None, "", None, "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ကို စစ်ဆေးပေးပါ။"

    try:
        # ၁။ Video Recap Script ထုတ်ယူခြင်း (AI Video Analysis)
        clean_text, model, dur_msg = run_gemini_video_analysis(target, ratio)
        
        # ၂။ စာတန်းထိုး (၂ ကြောင်းခွဲစနစ်) နှင့် မြန်မာအသံဖိုင် ဖန်တီးခြင်း
        audio_file, srt_file, _ = asyncio.run(
            generate_myanmar_tts(clean_text, voice, speed, "tab3_voice.mp3")
        )

        # ၃။ Video + Blurred Background + 2-Line Subtitle + Audio ပေါင်းစပ်ထုတ်လုပ်ခြင်း
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
            use_blur=use_blur,
            bg_color=bg_color,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            outline_color=outline_color,
            sub_pos=sub_pos,
            output_filename="final_movie_recap.mp4"
        )

        status_msg = f"🎉 Video အပြီးစီး အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ!\n🎬 Model: {model}\n{dur_msg}"
        return final_video, final_video, clean_text, srt_file, status_msg
    except Exception as e:
        return None, None, "", None, f"❌ Error ဖြစ်ပေါ်ပါသည်: {str(e)}"

# =========================================================
# GRADIO UI
# =========================================================
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**All-in-One AI Movie Recap Studio: Script, Dubbing, Subtitles & Real-Time Sync**")

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
                    v1_preview = gr.Video(label="📺 Video Preview", elem_id="tab1_preview_container")
                    v1_status = gr.Markdown("ဗီဒီယိုထည့်သွင်းရန် အဆင်သင့်ဖြစ်ပါသည်။")
                    v1_script_out = gr.Textbox(label="🎬 ထွက်ရှိလာသော Script", lines=10)
                    go_to_tts_btn = gr.Button("🎙️ Tab 2 (TTS) သို့ သွားရောက် အသံထုတ်မည် ➡️", variant="secondary")

            with gr.Row():
                v1_srt = gr.File(label="📄 SRT စာတန်းထိုး ဖိုင်")
                v1_zip = gr.File(label="📦 SRT ZIP ဒေါင်းလုဒ်")

        # --- TAB 2: TTS ---
        with gr.TabItem("2️⃣ Text-to-Speech", id="tab_tts"):
            with gr.Row():
                with gr.Column(scale=1):
                    v2_input_text = gr.Textbox(
                        label="🎙️ Burmese Script (English, Thai, 中文 စာသားများ ထည့်ပါက အလိုအလျောက် မြန်မာပြန်ပေးပါမည်)", 
                        lines=12,
                        placeholder="မြန်မာစာ သို့မဟုတ် အင်္ဂလိပ်၊ ထိုင်း၊ တရုတ် စာသားများ ထည့်သွင်းနိုင်ပါသည်..."
                    )
                    v2_trans_btn = gr.Button("🌐 မြန်မာလို ပြန်ဆိုမည် (Eng/Thai/中文 → မြန်မာ)", variant="secondary")
                    v2_voice = gr.Dropdown(list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎤 အသံ ရွေးချယ်ပါ")
                    v2_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Speed (%)")
                    v2_btn = gr.Button("⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်", variant="primary")
                    v2_status = gr.Markdown("")

                with gr.Column(scale=1):
                    v2_audio = gr.Audio(label="🔊 Voice Preview (အသံစမ်းနားထောင်ရန်)", autoplay=True)
                    v2_mp3 = gr.File(label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်")
            with gr.Row():
                v2_srt = gr.File(label="📄 SRT")
                v2_zip = gr.File(label="📦 SRT ZIP")

        # --- TAB 3: ONE-CLICK ADVANCED STUDIO ---
        with gr.TabItem("⚡ 3️⃣ One-Click All-in-One Video Studio", id="tab_auto"):
            gr.Markdown("### 🎛️ Blurred Background & စာတန်းထိုး ၂ ကြောင်းစနစ်ပါဝင်သော One-Click Studio")
            
            with gr.Row():
                # LEFT COLUMN: SETTINGS
                with gr.Column(scale=1):
                    t3_file = gr.Video(label="📹 Video File တင်ရန်")
                    t3_url = gr.Textbox(label="🔗 Video Link (YouTube, TikTok, Facebook, RedNote စသည်)")
                    t3_load_btn = gr.Button("🔍 Video ရယူ/စစ်ဆေးမည်", variant="secondary")

                    with gr.Accordion("🔊 အသံပိုင်းဆိုင်ရာ ထိန်းချုပ်မှုများ", open=True):
                        t3_orig_audio = gr.Checkbox(label="🔊 မူရင်း Video အသံ ဖွင့်မည် (Background အနေဖြင့်)", value=False)
                        t3_bgm_file = gr.Audio(label="🎵 Background Music (BGM) ထည့်ရန်", type="filepath")
                        t3_bgm_vol = gr.Slider(0.0, 1.0, value=0.15, step=0.05, label="🎚️ BGM အသံအတိုး/အလျှော့ (Volume)")
                        t3_voice = gr.Dropdown(list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎤 Voice ရွေးချယ်ပါ")
                        t3_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Voice Speed (%)")

                    with gr.Accordion("📐 Video ပုံစံ၊ အရွယ်အစားနှင့် နောက်ခံ ဝေဝါးမှု", open=True):
                        t3_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="9:16", label="📐 Aspect Ratio ရွေးပါ")
                        t3_blur = gr.Checkbox(label="🌫️ Background Blur (ဝေဝါးသော နောက်ခံ) အသုံးပြုမည်", value=True)
                        t3_flip = gr.Checkbox(label="🔄 ဗီဒီယို ဘယ်ညာလှန်မည် (Horizontal Flip)", value=False)
                        t3_scale = gr.Slider(0.5, 2.0, value=1.0, step=0.05, label="🔍 Video အကြီး/အသေး (Scale/Zoom)")
                        with gr.Row():
                            t3_x_off = gr.Slider(-200, 200, value=0, step=5, label="↔️ ဘယ်/ညာ ရွှေ့မည် (X-Offset)")
                            t3_y_off = gr.Slider(-200, 200, value=0, step=5, label="↕️ အပေါ်/အောက် ရွှေ့မည် (Y-Offset)")
                        t3_bgcolor = gr.ColorPicker(label="🎨 Canvas/Background အရောင် (Blur ပိတ်ထားပါက အသုံးပြုရန်)", value="#000000")

                    with gr.Accordion("✍️ စာတန်းထိုး ၂ ကြောင်း Fonts & ဒီဇိုင်း ချိန်ညှိမှု", open=True):
                        t3_font = gr.Dropdown(["Pyidaungsu", "Padauk", "Myanmar Text", "Arial", "sans-serif"], value="Pyidaungsu", label="🔤 Font ဒီဇိုင်း")
                        t3_sub_pos = gr.Radio(["အောက်ဘက်", "အပေါ်ဘက်"], value="အောက်ဘက်", label="📍 စာတန်းထိုး နေရာသတ်မှတ်ချက်")
                        t3_fsize = gr.Slider(14, 38, value=22, step=1, label="📏 စာလုံး အရွယ်အစား")
                        with gr.Row():
                            t3_fcolor = gr.ColorPicker(label="🎨 စာလုံးအရောင်", value="#FFF066") # ဓာတ်ပုံထဲကအတိုင်း ဝါနုရောင် default
                            t3_ocolor = gr.ColorPicker(label="🖌️ အနားသတ် အကြမ်းရောင် (Outline)", value="#000000")

                    t3_run_btn = gr.Button("✨ Video အပြီးစီး One-Click ထုတ်လုပ်မည်", variant="primary", size="lg")

                # RIGHT COLUMN: REAL-TIME PREVIEW & FINAL VIDEO
                with gr.Column(scale=1):
                    t3_preview_css = gr.HTML(
                        get_tab3_preview_css("9:16", False, 1.0, 0, 0, True, "#000000", "Pyidaungsu", 22, "#FFF066", "#000000", "အောက်ဘက်")
                    )
                    t3_live_video = gr.Video(label="📺 Real-Time Preview (နမူနာစာတန်းထိုး ၂ ကြောင်း)", elem_id="tab3_preview_box")
                    t3_status = gr.Markdown("ဗီဒီယိုနှင့် စတိုင်လ်များကို စိတ်ကြိုက်ချိန်ညှိပြီး Preview တွင် ကြည့်ရှုနိုင်ပါသည်။")
                    t3_final_video = gr.Video(label="🎬 အပြီးစီး Final Video Output (Playable)")
                    t3_script_view = gr.Textbox(label="📝 ထွက်ရှိလာသော ဘာသာပြန် စာတန်းထိုး Script", lines=5)
                    with gr.Row():
                        t3_download = gr.File(label="📥 Final Video ဒေါင်းလုဒ်")
                        t3_srt = gr.File(label="📄 SRT စာတန်းထိုး ဒေါင်းလုဒ်")

    # ================= EVENT BINDINGS =================
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

    # Tab 2
    v2_trans_btn.click(handle_direct_translate, inputs=v2_input_text, outputs=[v2_input_text, v2_status])
    v2_btn.click(
        tab2_tts_with_auto_translate, 
        inputs=[v2_input_text, v2_voice, v2_speed], 
        outputs=[v2_input_text, v2_audio, v2_mp3, v2_srt, v2_zip, v2_status]
    )

    # Tab 3 - Real-Time Preview Updates
    t3_file.change(lambda f: f, inputs=t3_file, outputs=t3_live_video)
    t3_load_btn.click(download_video_from_link, inputs=t3_url, outputs=t3_live_video)

    preview_inputs = [
        t3_ratio, t3_flip, t3_scale, t3_x_off, t3_y_off,
        t3_blur, t3_bgcolor,
        t3_font, t3_fsize, t3_fcolor, t3_ocolor, t3_sub_pos
    ]
    for comp in preview_inputs:
        comp.change(get_tab3_preview_css, inputs=preview_inputs, outputs=t3_preview_css)

    # Tab 3 - Generate Final One-Clip Video
    t3_run_btn.click(
        tab3_auto_pipeline,
        inputs=[
            t3_file, t3_url, t3_bgm_file,
            t3_orig_audio, t3_bgm_vol,
            t3_ratio, t3_flip, t3_scale, t3_x_off, t3_y_off,
            t3_blur, t3_bgcolor,
            t3_font, t3_fsize, t3_fcolor, t3_ocolor, t3_sub_pos,
            t3_voice, t3_speed
        ],
        outputs=[t3_final_video, t3_download, t3_script_view, t3_srt, t3_status]
    )

# Render Server Port
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
