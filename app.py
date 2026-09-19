import os
import re
import time
import zipfile
import subprocess
import asyncio
import html
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
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},000"

def generate_srt_and_zip(script_text, prefix="myanmar_recap"):
    clean_text = clean_script_for_tts(script_text)
    if not clean_text:
        return None, None
    lines = [x.strip() for x in clean_text.splitlines() if x.strip()]
    srt_content = ""
    current_time = 0
    for idx, line in enumerate(lines, 1):
        duration = max(2, min(8, round(len(line) / 11)))
        start_time = current_time
        end_time = current_time + duration
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
# DYNAMIC RATIO & STYLING (TAB 1 & TAB 3)
# =========================================================
def get_ratio_css(ratio, container_id="tab1_preview_container", flip_horizontal=False):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "450px"},
        "3:4": {"aspect": "3 / 4", "max_w": "380px"},
        "16:9": {"aspect": "16 / 9", "max_w": "640px"},
        "9:16": {"aspect": "9 / 16", "max_w": "320px"},
    }
    cfg = configs.get(ratio, configs["1:1"])
    transform_rule = "scaleX(-1)" if flip_horizontal else "scaleX(1)"
    return f"""
    <style id="{container_id}-style">
    #{container_id} {{
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: 0 auto !important;
        transition: all 0.3s ease-in-out !important;
    }}
    #{container_id} .video-container, #{container_id} video, #{container_id} .wrap {{
        width: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        height: auto !important;
        max-height: none !important;
    }}
    #{container_id} video {{
        object-fit: cover !important;
        transform: {transform_rule} !important;
    }}
    </style>
    """

def hex_to_rgba(hex_color, opacity):
    hex_color = (hex_color or '#000000').strip().lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    except (TypeError, ValueError):
        r, g, b = 0, 0, 0
    try:
        opacity = max(0.0, min(1.0, float(opacity)))
    except (TypeError, ValueError):
        opacity = 0.6
    return f"rgba({r}, {g}, {b}, {opacity:.3f})"

def get_tab3_mask_css(mask_enabled, mask_type, color, opacity, blur_amount, pos_y, pos_x, height_pct,
                      subtitle_color="#FFFFFF", subtitle_size=28, subtitle_y=82, subtitle_x=50):
    mask_display = "block" if mask_enabled else "none"
    mask_type = mask_type or ""
    rgba_bg = hex_to_rgba(color, opacity)
    background_style = f"background-color: {rgba_bg};"
    try:
        blur_amount = max(0, min(30, float(blur_amount)))
        pos_y = max(0, min(100, float(pos_y)))
        pos_x = max(0, min(100, float(pos_x)))
        height_pct = max(5, min(50, float(height_pct)))
        subtitle_size = max(10, min(96, float(subtitle_size)))
        subtitle_y = max(0, min(100, float(subtitle_y)))
        subtitle_x = max(0, min(100, float(subtitle_x)))
    except (TypeError, ValueError):
        blur_amount, pos_y, pos_x, height_pct = 10, 85, 50, 12
        subtitle_size, subtitle_y, subtitle_x = 28, 82, 50
    subtitle_rgba = hex_to_rgba(subtitle_color, 1.0)
    backdrop_filter_style = (
        f"backdrop-filter: blur({blur_amount}px); "
        f"-webkit-backdrop-filter: blur({blur_amount}px);"
        if "Blur" in mask_type else ""
    )

    return f"""
    <style>
    #tab3_stage {{
        position: relative !important;
        overflow: hidden !important;
    }}
    #tab3_stage #tab3_preview_container {{
        position: relative !important;
        z-index: 1 !important;
    }}
    #tab3_stage #tab3_mask_dom {{
        position: absolute !important;
        inset: 0 !important;
        width: 100% !important;
        height: 100% !important;
        z-index: 20 !important;
        pointer-events: none !important;
    }}
    #tab3_stage #tab3_custom_mask {{
        display: {mask_display} !important;
        position: absolute !important;
        left: {pos_x}% !important;
        top: {pos_y}% !important;
        width: 90% !important;
        height: {height_pct}% !important;
        transform: translateX(-50%) !important;
        border-radius: 8px !important;
        {background_style}
        {backdrop_filter_style}
        z-index: 21 !important;
        pointer-events: none !important;
        border: 1px dashed rgba(255, 255, 255, 0.4);
    }}
    #tab3_stage #tab3_custom_subtitle {{
        display: block !important;
        position: absolute !important;
        left: {subtitle_x}% !important;
        top: {subtitle_y}% !important;
        transform: translate(-50%, -50%) !important;
        max-width: 90% !important;
        color: {subtitle_rgba} !important;
        font-size: {subtitle_size}px !important;
        line-height: 1.25 !important;
        font-weight: 700 !important;
        text-align: center !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        z-index: 22 !important;
        pointer-events: none !important;
        text-shadow: 2px 2px 3px #000, -1px -1px 2px #000 !important;
    }}
    </style>
    """

def get_tab3_overlay_html(sample_text="(နမူနာစာ)"):
    safe_text = html.escape(str(sample_text or "(နမူနာစာ)"), quote=True)
    return f'<div id="tab3_custom_mask"></div><div id="tab3_custom_subtitle">{safe_text}</div>'

# =========================================================
# GEMINI GENERATION
# =========================================================
def build_recap_prompt(selected_ratio):
    return f""" သင်သည် ထိပ်တန်း Professional Movie Recap Scriptwriter ဖြစ်သည်။ Target Video Frame Ratio: {selected_ratio} ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ တိကျသေချာစွာ ကြည့်ရှုနားထောင်ပြီး အောက်ပါစည်းမျဉ်းများအတိုင်း "မြန်မာ Movie Recap Script" ကို ရေးသားပေးပါ-
[စည်းမျဉ်းများ]
၁။ ဗီဒီယိုထဲတွင် တကယ်ဖြစ်ပျက်နေသော အဖြစ်အပျက် အမှန်များကိုသာ အချိန်အစဉ်လိုက် အတိုချုံး၍ တိကျမှန်ကန်စွာ ရေးပါ။
၂။ ဇာတ်ကောင်များ၏ ပြောစကားများကို သဘာဝကျကျ ဆွဲဆောင်မှုရှိသော မြန်မာစကားပြောအဖြစ် တိုက်ရိုက်ပြန်ဆိုပါ။
၃။ [Visual], [Scene], [Narrator], [Dialogue], [Intro] စသည့် Technical Label များနှင့် စကားအပိုများ လုံးဝမထည့်ပါနှင့်။
၄။ TTS အသံထွက်ဖတ်ရာတွင် ချောမွေ့စေရန် စာကြောင်းတစ်ကြောင်းချင်းစီကို တိုတိုရှင်းရှင်းနှင့် အဓိပ္ပာယ်ပြည့်စုံစွာ ရေးပေးပါ။ """

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
        
    prompt = build_recap_prompt(ratio_choice)
    script_text, used_model = generate_with_retry(client, uploaded_file, prompt)
    clean_text = clean_script_for_tts(script_text)
    return clean_text, used_model, msg

# =========================================================
# TTS LOGIC
# =========================================================
async def generate_myanmar_tts(text, voice_choice, speed_percent, output_name="tab2_output.mp3"):
    clean_text = clean_script_for_tts(text)
    if not clean_text:
        return None, None, None
    selected_voice = VOICES.get(voice_choice, "my-MM-ThihaNeural")
    rate_str = f"{int(speed_percent):+d}%"
    communicate = edge_tts.Communicate(clean_text, selected_voice, rate=rate_str)
    await communicate.save(output_name)
    srt_file, zip_file = generate_srt_and_zip(clean_text, prefix=output_name.replace(".mp3", ""))
    return output_name, srt_file, zip_file

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

def tab2_tts(text, voice, speed):
    try:
        mp3, srt, zip_f = asyncio.run(generate_myanmar_tts(text, voice, speed, "tab2_output.mp3"))
        return mp3, mp3, srt, zip_f
    except Exception as e:
        print("Tab 2 Error:", e)
        return None, None, None, None

# =========================================================
# GRADIO UI
# =========================================================
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**AI Video Recap Script & Myanmar Voice-Over**")
    
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
                    v1_url = gr.Textbox(label="🔗 Video URL Link (YouTube, TikTok, Facebook စသည်)")
                    v1_load_btn = gr.Button("🔍 Link မှ Video ရယူမည်", variant="secondary")
                    v1_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="1:1", label="📐 Preview Screen Aspect Ratio")
                    v1_gen_btn = gr.Button("🚀 Recap Script စတင်ထုတ်မည်", variant="primary")
                with gr.Column(scale=1):
                    v1_css = gr.HTML(get_ratio_css("1:1", "tab1_preview_container", False))
                    v1_preview = gr.Video(label="📺 Video Preview (Selected Ratio View)", elem_id="tab1_preview_container")
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
                    v2_input_text = gr.Textbox(label="🎙️ Burmese Script (Tab 1 မှ အလိုအလျောက် ရောက်ရှိပါမည်)", lines=12)
                    v2_voice = gr.Dropdown(list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎤 အသံ ရွေးချယ်ပါ")
                    v2_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Speed (%)")
                    v2_btn = gr.Button("⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်", variant="primary")
                with gr.Column(scale=1):
                    v2_audio = gr.Audio(label="🔊 Voice Preview (အသံစမ်းနားထောင်ရန်)", autoplay=True)
                    v2_mp3 = gr.File(label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်")
            with gr.Row():
                v2_srt = gr.File(label="📄 SRT")
                v2_zip = gr.File(label="📦 SRT ZIP")
            v2_btn.click(tab2_tts, inputs=[v2_input_text, v2_voice, v2_speed], outputs=[v2_audio, v2_mp3, v2_srt, v2_zip])

        # --- TAB 3: ONE CLIP VIDEO (Using gr.Video with absolute mask overlay) ---
        with gr.TabItem("3️⃣ One Clip Video", id="tab_one_clip"):
            with gr.Row():
                with gr.Column(scale=1):
                    t3_file = gr.Video(label="📹 Video File ထည့်ရန်")
                    t3_url = gr.Textbox(label="🔗 Video URL Link (YouTube, TikTok စသည်)")
                    t3_load_btn = gr.Button("🔍 Link မှ Video ရယူမည်", variant="secondary")
                    t3_ratio = gr.Radio(["9:16", "3:4", "16:9", "1:1"], value="9:16", label="📐 Aspect Ratio ရွေးချယ်ရန်")
                    t3_flip = gr.Checkbox(label="↔️ ဗီဒီယိုကို ဘယ်ညာလှန်မည် (Horizontal Flip Preview)", value=False)
                    
                    gr.Markdown("### 🔲 မူရင်းစာတန်းထိုးဖုံးရန် (Blur / Color Cover Mask)")
                    t3_mask_toggle = gr.Checkbox(label="✨ စာတန်းထိုးဖုံးရန် Mask အသုံးပြုမည်", value=True)
                    t3_mask_type = gr.Radio(["Blur (နောက်ခံဝဲဝါးရန်)", "Solid/Custom Color (အရောင်သီးသန့်)"], value="Blur (နောက်ခံဝဲဝါးရန်)", label="🎨 Mask အမျိုးအစား")
                    with gr.Row():
                        t3_color = gr.ColorPicker(value="#000000", label="🎨 အရောင်ရွေးချယ်ရန်")
                        t3_opacity = gr.Slider(0.0, 1.0, value=0.6, step=0.05, label="💧 အရောင်အတိုးအလျော့ (Opacity)")
                        t3_blur_amt = gr.Slider(0, 30, value=10, step=1, label="🌫️ Blur ဝေဝါးမှု ပမာဏ (Blur Strength)")
                    
                    gr.Markdown("### 📍 အပေါ်အောက် / ဘယ်ညာ ရွေ့လျားမှု နှင့် အထူအပါး")
                    t3_pos_y = gr.Slider(0, 100, value=85, step=1, label="↕️ အပေါ်အောက် နေရာရွေ့ရန် (Top Position %)")
                    t3_pos_x = gr.Slider(0, 100, value=50, step=1, label="↔️ ဘယ်ညာ နေရာရွေ့ရန် (Left Position %)")
                    t3_height = gr.Slider(5, 50, value=12, step=1, label="📏 အထူအပါး အမြင့် (Height Size %)")
                    gr.Markdown("### 🔤 Preview စာတန်းထိုး အလှဆင်ခြင်း")
                    t3_subtitle_text = gr.Textbox(value="(နမူနာစာ)", label="📝 Preview မှာပြမည့် စာတန်းထိုးစာ", lines=2)
                    with gr.Row():
                        t3_subtitle_color = gr.ColorPicker(value="#FFFFFF", label="🎨 စာလုံးအရောင်")
                        t3_subtitle_size = gr.Slider(10, 96, value=28, step=1, label="🔠 စာလုံးအကြီးအသေး")
                    with gr.Row():
                        t3_subtitle_y = gr.Slider(0, 100, value=82, step=1, label="↕️ စာတန်း အပေါ်/အောက်")
                        t3_subtitle_x = gr.Slider(0, 100, value=50, step=1, label="↔️ စာတန်း ဘယ်/ညာ")

                with gr.Column(scale=1):
                    with gr.Group(elem_id="tab3_stage"):
                        t3_css = gr.HTML(get_ratio_css("9:16", "tab3_preview_container", False) + get_tab3_mask_css(True, "Blur (နောက်ခံဝဲဝါးရန်)", "#000000", 0.6, 10, 85, 50, 12, "#FFFFFF", 28, 82, 50))
                        t3_preview = gr.Video(label="📺 Video Preview (With Subtitle Mask)", elem_id="tab3_preview_container")
                        t3_mask_dom = gr.HTML(get_tab3_overlay_html(), elem_id="tab3_mask_dom")
                    gr.Markdown("💡 *အထက်ပါ Preview ပေါ်တွင် မူရင်းစာတန်းထိုးများကို ဖုံးကွယ်ရန် ချိန်ကိုက်ထားသော Mask ကို ဗီဒီယိုပေါ်တွင် တိုက်ရိုက်ထပ်နေအောင် စီစဉ်ပေးထားပါသည်။*")

    # ================= EVENT BINDINGS =================
    # --- Tab 1 Bindings ---
    v1_file.change(lambda f: f, inputs=v1_file, outputs=v1_preview)
    v1_load_btn.click(download_video_from_link, inputs=v1_url, outputs=v1_preview)
    v1_ratio.change(lambda r: get_ratio_css(r, "tab1_preview_container", False), inputs=v1_ratio, outputs=v1_css)
    v1_gen_btn.click(
        tab1_analyze,
        inputs=[v1_file, v1_url, v1_ratio],
        outputs=[v1_script_out, v2_input_text, v1_status, v1_srt, v1_zip]
    )
    go_to_tts_btn.click(lambda: gr.Tabs(selected="tab_tts"), outputs=main_tabs)

    # --- Tab 3 Bindings ---
    t3_file.change(lambda f: f, inputs=t3_file, outputs=t3_preview)
    t3_load_btn.click(download_video_from_link, inputs=t3_url, outputs=t3_preview)
    
    def update_tab3_styling(ratio, flip, mask_en, mask_t, col, op, blur, py, px, h,
                            subtitle_text, subtitle_color, subtitle_size, subtitle_y, subtitle_x):
        return (
            get_ratio_css(ratio, "tab3_preview_container", flip) + get_tab3_mask_css(
                mask_en, mask_t, col, op, blur, py, px, h,
                subtitle_color, subtitle_size, subtitle_y, subtitle_x
            ),
            get_tab3_overlay_html(subtitle_text)
        )

    tab3_style_inputs = [
        t3_ratio, t3_flip, t3_mask_toggle, t3_mask_type, t3_color, t3_opacity,
        t3_blur_amt, t3_pos_y, t3_pos_x, t3_height, t3_subtitle_text,
        t3_subtitle_color, t3_subtitle_size, t3_subtitle_y, t3_subtitle_x,
    ]
    for inp in tab3_style_inputs:
        inp.change(
            update_tab3_styling,
            inputs=tab3_style_inputs,
            outputs=[t3_css, t3_mask_dom]
        )

# Server Port Configuration for Render & Railway
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
