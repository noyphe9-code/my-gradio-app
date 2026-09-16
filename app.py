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

# API မှ တိုက်ရိုက်တောင်းဆိုထားသော နောက်ဆုံးထွက် Model များ
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
    lines = [x.strip() for x in clean_text.splitlines() if x.strip()]
    if not lines:
        return None, None

    srt_content = ""
    current_time = 0.0

    if total_target_duration and total_target_duration > 0:
        total_chars = sum(len(line) for line in lines)
        for idx, line in enumerate(lines, 1):
            line_weight = len(line) / max(1, total_chars)
            dur = max(1.5, line_weight * total_target_duration)
            start_time = current_time
            end_time = min(total_target_duration, current_time + dur)
            srt_content += f"{idx}\n{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n{line}\n\n"
            current_time = end_time
    else:
        for idx, line in enumerate(lines, 1):
            dur = max(2.0, min(8.0, len(line) / 10.0))
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
# TRANSLATION ENGINE (ENG, THAI, CHINESE -> BURMESE)
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
# DYNAMIC RATIO STYLING
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

# =========================================================
# GEMINI SCRIPT RECAP CORE
# =========================================================
def build_recap_prompt(selected_ratio, video_duration=None):
    dur_guidance = ""
    if video_duration:
        dur_guidance = f"\nVideo Length: {video_duration:.1f} seconds. Make sure the script length matches this video duration naturally."

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
# TAB 3: FFmpeg VIDEO COMPOSER & SYNC ENGINE
# =========================================================
def render_final_composite_video(source_video, audio_path, srt_path, ratio_choice, output_filename="final_movie_recap.mp4"):
    ratio_crops = {
        "1:1": "crop=min(iw\\,ih):min(iw\\,ih),scale=1080:1080",
        "3:4": "crop=min(iw\\,ih*3/4):min(ih\\,iw*4/3),scale=810:1080",
        "16:9": "crop=min(iw\\,ih*16/9):min(ih\\,iw*9/16),scale=1920:1080",
        "9:16": "crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),scale=1080:1920",
    }
    vf_crop = ratio_crops.get(ratio_choice, ratio_crops["9:16"])

    # Subtitle burn-in စာတန်းထိုး ချောမွေ့အောင် ထည့်သွင်းခြင်း
    escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    vf_filter = f"{vf_crop},subtitles='{escaped_srt}':force_style='FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=2,Alignment=2,MarginV=35'"

    audio_dur = get_video_duration(audio_path) or 10
    v_dur = get_video_duration(source_video) or 10

    # ဗီဒီယိုအရှည်နှင့် အသံအရှည်ကို Auto-Sync ညှိနှိုင်းခြင်း
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", source_video,
        "-i", audio_path,
        "-t", str(audio_dur),
        "-vf", vf_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        output_filename
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        # Subtitle font issue ဖြစ်ပါက subtitle မပါဘဲ clean render ပြန်လုပ်ခြင်း
        clean_cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", source_video,
            "-i", audio_path,
            "-t", str(audio_dur),
            "-vf", vf_crop,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            output_filename
        ]
        subprocess.run(clean_cmd, check=True)

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

def tab3_auto_pipeline(v_file, v_url, ratio, voice, speed):
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return None, None, None, None, "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ကို စစ်ဆေးပေးပါ။"

    try:
        # ၁။ Video Recap Script ထုတ်ယူခြင်း
        clean_text, model, dur_msg = run_gemini_video_analysis(target, ratio)
        
        # ၂။ စာတန်းထိုးနှင့် အသံဖိုင် ဖန်တီးခြင်း
        audio_file, srt_file, zip_file = asyncio.run(
            generate_myanmar_tts(clean_text, voice, speed, "tab3_voice.mp3")
        )

        # ၃။ Video + Audio + Subtitle + Crop Ratio ပေါင်းစပ်ပြီး Final Video ထုတ်ခြင်း
        final_video = render_final_composite_video(
            source_video=target,
            audio_path=audio_file,
            srt_path=srt_file,
            ratio_choice=ratio,
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
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**All-in-One AI Movie Recap Studio: Script, Dubbing & Auto Sync**")

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

        # --- TAB 3: ONE-CLICK ALL-IN-ONE VIDEO STUDIO ---
        with gr.TabItem("⚡ 3️⃣ One-Click All-in-One Video Studio", id="tab_auto"):
            gr.Markdown("### 🚀 Video ဖိုင် (သို့မဟုတ် Link) ထည့်လိုက်ရုံဖြင့် စာတန်းထိုး + အသံ + ဗီဒီယို အချိုးအစား ကွက်တိ အပြီးစီးထုတ်လုပ်ပေးမည့် Studio")
            with gr.Row():
                with gr.Column(scale=1):
                    v3_file = gr.Video(label="📹 Video File တင်ရန်")
                    v3_url = gr.Textbox(label="🔗 Video Link (YouTube, TikTok, Facebook, RedNote စသည်)")
                    v3_load_btn = gr.Button("🔍 Video Link စစ်ဆေးကြည့်မည်", variant="secondary")
                    v3_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="9:16", label="📐 Final Video Aspect Ratio")
                    v3_voice = gr.Dropdown(list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎤 Voice ရွေးချယ်ပါ")
                    v3_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Voice Speed (%)")
                    v3_btn = gr.Button("✨ Video အပြီးစီး One-Click ထုတ်လုပ်မည်", variant="primary")

                with gr.Column(scale=1):
                    v3_css = gr.HTML(get_ratio_css("9:16", "tab3_preview_container"))
                    v3_output_video = gr.Video(label="🎬 အပြီးစီး Final Video (Playable)", elem_id="tab3_preview_container")
                    v3_status = gr.Markdown("လုပ်ဆောင်ရန် အသင့်ဖြစ်ပါသည်။")
                    v3_script_view = gr.Textbox(label="📝 ထွက်ရှိလာသော စာတန်းထိုး Script", lines=6)
                    with gr.Row():
                        v3_download_file = gr.File(label="📥 Final Video Download")
                        v3_srt_file = gr.File(label="📄 SRT Download")

    # ================= EVENT BINDINGS =================
    # Tab 1 Events
    v1_file.change(lambda f: f, inputs=v1_file, outputs=v1_preview)
    v1_load_btn.click(download_video_from_link, inputs=v1_url, outputs=v1_preview)
    v1_ratio.change(lambda r: get_ratio_css(r, "tab1_preview_container"), inputs=v1_ratio, outputs=v1_css)
    v1_gen_btn.click(
        tab1_analyze, 
        inputs=[v1_file, v1_url, v1_ratio], 
        outputs=[v1_script_out, v2_input_text, v1_status, v1_srt, v1_zip]
    )
    go_to_tts_btn.click(lambda: gr.Tabs(selected="tab_tts"), outputs=main_tabs)

    # Tab 2 Events
    v2_trans_btn.click(handle_direct_translate, inputs=v2_input_text, outputs=[v2_input_text, v2_status])
    v2_btn.click(
        tab2_tts_with_auto_translate, 
        inputs=[v2_input_text, v2_voice, v2_speed], 
        outputs=[v2_input_text, v2_audio, v2_mp3, v2_srt, v2_zip, v2_status]
    )

    # Tab 3 Events
    v3_file.change(lambda f: f, inputs=v3_file, outputs=v3_output_video)
    v3_load_btn.click(download_video_from_link, inputs=v3_url, outputs=v3_output_video)
    v3_ratio.change(lambda r: get_ratio_css(r, "tab3_preview_container"), inputs=v3_ratio, outputs=v3_css)
    v3_btn.click(
        tab3_auto_pipeline,
        inputs=[v3_file, v3_url, v3_ratio, v3_voice, v3_speed],
        outputs=[v3_output_video, v3_download_file, v3_script_view, v3_srt_file, v3_status]
    )

# Render Server Port
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
