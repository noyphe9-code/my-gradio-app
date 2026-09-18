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
APP_TITLE = "AI Movie Recap Studio Pro + Advanced Live Preview One Clip Studio"
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
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ。"
    return "⚠️ Gemini API Key ထည့်ပေးပါ။"

def get_video_duration(video_path):
    if not video_path or not os.path.exists(video_path):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore", timeout=30
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
# LIVE PREVIEW CSS & HTML GENERATOR (TAB 3)
# =========================================================
def update_live_preview(
    ratio, sub_text, font_color, stroke_color, font_size,
    sub_blur_bg, pos_x, pos_y, logo_file, logo_pos, logo_size
):
    configs = {
        "16:9": {"aspect": "16 / 9", "max_w": "640px"},
        "9:16": {"aspect": "9 / 16", "max_w": "300px"},
        "1:1": {"aspect": "1 / 1", "max_w": "400px"},
        "3:4": {"aspect": "3 / 4", "max_w": "340px"},
    }
    cfg = configs.get(ratio, configs["16:9"])

    # Logo Position CSS
    logo_css_rules = "display: none;"
    if logo_file:
        # logo_file might be a file path or uploaded object
        logo_path = logo_file if isinstance(logo_file, str) else getattr(logo_file, "name", "")
        if logo_path and os.path.exists(logo_path):
            if "Top-Left" in logo_pos or "ဘယ်ဘက်" in logo_pos:
                l_pos = "top: 15px; left: 15px;"
            elif "Top-Right" in logo_pos:
                l_pos = "top: 15px; right: 15px;"
            elif "Bottom-Left" in logo_pos:
                l_pos = "bottom: 60px; left: 15px;"
            else:
                l_pos = "bottom: 60px; right: 15px;"
            logo_css_rules = f"position: absolute; {l_pos} width: {logo_size}px; height: auto; z-index: 10; border-radius: 4px;"

    # Subtitle Styling & Positioning
    sub_bg_style = ""
    if sub_blur_bg:
        sub_bg_style = "background: rgba(0, 0, 0, 0.6); backdrop-filter: blur(4px); padding: 6px 12px; border-radius: 6px;"
    else:
        sub_bg_style = "background: rgba(0, 0, 0, 0.2); padding: 4px 8px;"

    stroke_style = f"-webkit-text-stroke: 1px {stroke_color}; text-shadow: 2px 2px 2px {stroke_color};" if stroke_color else ""

    preview_html = f"""
    <style>
        .live-preview-wrapper {{
            width: 100%;
            max-width: {cfg["max_w"]};
            margin: 0 auto;
            position: relative;
            background: #000;
            border-radius: 12px;
            overflow: hidden;
            aspect-ratio: {cfg["aspect"]};
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 8px 20px rgba(0,0,0,0.3);
        }}
        .live-preview-video {{
            width: 100%;
            height: 100%;
            object-fit: contain;
        }}
        .live-preview-logo {{
            {logo_css_rules}
        }}
        .live-preview-subtitle {{
            position: absolute;
            bottom: {max(15, 30 + pos_y)}px;
            left: calc(50% + {pos_x}px);
            transform: translateX(-50%);
            color: {font_color};
            font-size: {font_size}px;
            font-weight: bold;
            text-align: center;
            width: 90%;
            z-index: 15;
            {stroke_style}
            {sub_bg_style}
            pointer-events: none;
            word-break: break-word;
        }}
    </style>
    <div class="live-preview-wrapper" id="live_preview_box">
        <div class="live-preview-logo"><img src="{logo_path if logo_file else ''}" style="width:100%; height:auto;" /></div>
        <div class="live-preview-subtitle">{sub_text if sub_text else "စာတန်းထိုး နမူနာပြသရန် ဤနေရာတွင် စာသားထည့်ပါ..."}</div>
    </div>
    """
    return preview_html

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
    #{container_id} .video-container, #{container_id} video {{
        width: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        height: auto !important;
        object-fit: contain !important;
        background-color: #000000 !important;
        border-radius: 12px !important;
        display: block !important;
    }}
    </style>
    """

def build_recap_prompt(selected_ratio):
    return f"""
သင်သည် ထိပ်တန်း Professional Movie Recap Scriptwriter ဖြစ်သည်။ 
Target Video Frame Ratio: {selected_ratio}
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

    prompt = build_recap_prompt(ratio_choice)
    script_text, used_model = generate_with_retry(client, uploaded_file, prompt)
    clean_text = clean_script_for_tts(script_text)
    return clean_text, used_model, msg

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
# OPTIMIZED & FAST ADVANCED ONE CLIP STUDIO RENDERER
# =========================================================
def generate_advanced_one_clip(
    video_file, audio_file, resolution, ratio,
    brightness, contrast_val, zoom,
    orig_vol, bgm_file, bgm_vol,
    sub_text, font_color, stroke_color, font_size,
    sub_blur_bg, sub_pos_x, sub_pos_y,
    logo_file, logo_pos, logo_size
):
    if not video_file or not os.path.exists(video_file):
        return None, "⚠️ ဗီဒီယိုဖိုင် မရှိပါ။"
    if not audio_file or not os.path.exists(audio_file):
        return None, "⚠️ အသံဖိုင် (TTS MP3) မရှိပါ။"

    output_filename = "final_advanced_clip.mp4"
    
    if ratio == "9:16":
        base_w, base_h = "720", "1280"
    elif ratio == "1:1":
        base_w, base_h = "720", "720"
    elif ratio == "3:4":
        base_w, base_h = "720", "960"
    else:
        base_w, base_h = "1280", "720"

    eq_filter = f"eq=brightness={brightness}:contrast={contrast_val}"
    # Optimized Fast Video Filter Chain
    video_filter = f"[0:v]{eq_filter},scale={base_w}:{base_h}:force_original_aspect_ratio=decrease,pad={base_w}:{base_h}:(ow-iw)/2:(oh-ih)/2,fps=30[v_base]"

    current_v = "v_base"
    filter_chains = [video_filter]

    has_logo = bool(logo_file and os.path.exists(logo_file))
    if has_logo:
        if "Top-Left" in logo_pos or "ဘယ်ဘက်" in logo_pos:
            logo_x, logo_y = "15", "15"
        elif "Top-Right" in logo_pos:
            logo_x, logo_y = f"W-w-15", "15"
        elif "Bottom-Left" in logo_pos:
            logo_x, logo_y = "15", f"H-h-60"
        else:
            logo_x, logo_y = f"W-w-15", f"H-h-60"

        filter_chains.append(f"[1:v]scale={logo_size}:-1[logo];[{current_v}][logo]overlay={logo_x}:{logo_y}[v_logo]")
        current_v = "v_logo"
        audio_input_idx = 2
    else:
        audio_input_idx = 1

    if sub_text and sub_text.strip():
        fc = font_color.replace("#", "0x") if font_color else "0xFFFF00"
        sc = stroke_color.replace("#", "0x") if stroke_color else "0x000000"
        
        x_expr = f"(w-text_w)/2+({sub_pos_x})"
        y_expr = f"h-text_h-{max(20, 40 + sub_pos_y)}"

        if sub_blur_bg:
            filter_chains.append(f"[{current_v}]drawtext=text='{sub_text}':fontcolor={fc}:fontsize={font_size}:borderw=2:bordercolor={sc}:box=1:boxcolor=black@0.6:boxborderw=8:x={x_expr}:y={y_expr}[v_sub]")
        else:
            filter_chains.append(f"[{current_v}]drawtext=text='{sub_text}':fontcolor={fc}:fontsize={font_size}:borderw=2:bordercolor={sc}:x={x_expr}:y={y_expr}[v_sub]")
        current_v = "v_sub"

    has_bgm = bool(bgm_file and os.path.exists(bgm_file))
    if has_logo and has_bgm:
        audio_mix = f"[{audio_input_idx}:a]volume=1.0[voice];[0:a]volume={orig_vol}[orig];[3:a]volume={bgm_vol},aloop=loop=-1:size=2e9[bgm];[voice][orig][bgm]amix=inputs=3:duration=first:dropout_transition=2[a]"
    elif has_logo and not has_bgm:
        audio_mix = f"[{audio_input_idx}:a]volume=1.0[voice];[0:a]volume={orig_vol}[orig];[voice][orig]amix=inputs=2:duration=first:dropout_transition=2[a]"
    elif not has_logo and has_bgm:
        audio_mix = f"[1:a]volume=1.0[voice];[0:a]volume={orig_vol}[orig];[2:a]volume={bgm_vol},aloop=loop=-1:size=2e9[bgm];[voice][orig][bgm]amix=inputs=3:duration=first:dropout_transition=2[a]"
    else:
        audio_mix = f"[1:a]volume=1.0[voice];[0:a]volume={orig_vol}[orig];[voice][orig]amix=inputs=2:duration=first:dropout_transition=2[a]"

    filter_chains.append(audio_mix)
    final_filter_complex = ";".join(filter_chains)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_file,
    ]
    if has_logo:
        cmd.extend(["-i", logo_file])
    cmd.extend(["-i", audio_file])
    if has_bgm:
        cmd.extend(["-i", bgm_file])

    # Optimized Speed with preset veryfast & CRF 24
    cmd.extend([
        "-filter_complex", final_filter_complex,
        "-map", f"[{current_v}]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_filename
    ])

    try:
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore", timeout=400)
        if process.returncode != 0:
            err_msg = process.stderr[-300:] if process.stderr else "Unknown FFmpeg error"
            return None, f"❌ FFmpeg Error: {err_msg}"
        if os.path.exists(output_filename):
            return output_filename, "✅ Advanced One Clip အမြန်ဆုံးနှင့် အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ။"
    except Exception as e:
        return None, f"❌ Error: {str(e)}"
    
    return None, "❌ Render လုပ်ဆောင်ရာတွင် အမှားအယွင်းရှိခဲ့ပါသည်။"

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

def tab3_render_advanced(
    v_file, a_file, res, ratio,
    bright, contrast, zoom,
    orig_v, bgm_f, bgm_v,
    script_t, f_color, s_color, f_size,
    s_blur, pos_x, pos_y,
    logo_f, logo_p, logo_s
):
    mp4_out, status = asyncio.run(asyncio.to_thread(
        generate_advanced_one_clip,
        v_file, a_file, res, ratio,
        bright, contrast, zoom,
        orig_v, bgm_f, bgm_v,
        script_t, f_color, s_color, f_size,
        s_blur, pos_x, pos_y,
        logo_f, logo_p, logo_s
    ))
    return mp4_out, mp4_out, status

# =========================================================
# GRADIO UI
# =========================================================
with gr.Blocks(title=APP_TITLE) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**AI Video Recap Studio Pro + Live Preview & Optimized Fast Rendering**")

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
                    v1_css = gr.HTML(get_ratio_css("1:1", "tab1_preview_container"))
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
                    go_to_tab3_btn = gr.Button("🎬 Tab 3 (Advanced One Clip) သို့ သွားမည် ➡️", variant="secondary")
                with gr.Column(scale=1):
                    v2_audio = gr.Audio(label="🔊 Voice Preview (အသံစမ်းနားထောင်ရန်)", autoplay=True)
                    v2_mp3 = gr.File(label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်")
            with gr.Row():
                v2_srt = gr.File(label="📄 SRT")
                v2_zip = gr.File(label="📦 SRT ZIP")

            v2_btn.click(tab2_tts, inputs=[v2_input_text, v2_voice, v2_speed], outputs=[v2_audio, v2_mp3, v2_srt, v2_zip])

        # --- TAB 3: ADVANCED ONE CLIP STUDIO WITH LIVE PREVIEW ---
        with gr.TabItem("3️⃣ Advanced One Clip Studio", id="tab_clip"):
            gr.Markdown("### 🎛️ Live Preview ဖြင့် တိုက်ရိုက်ချိန်ညှိကာ အမြန်ဆုံး Render လုပ်ရန်")
            with gr.Row():
                with gr.Column(scale=1):
                    v3_video_input = gr.Video(label="📹 မူရင်းဗီဒီယိုဖိုင် တင်ရန်")
                    v3_audio_input = gr.Audio(label="🎵 Tab 2 မှ ထွက်လာသော MP3 အသံဖိုင် တင်ရန်", type="filepath")
                    
                    with gr.Accordion("⚙️ ဗီဒီယို အရွယ်အစားနှင့် အသွင်အပြင် (Ratio & Effects)", open=True):
                        v3_resolution = gr.Radio(["480p", "720p", "1080p"], value="720p", label="🖥️ Video Resolution")
                        v3_ratio = gr.Radio(["16:9", "9:16", "1:1", "3:4"], value="16:9", label="📐 Aspect Ratio")
                        
                        v3_brightness = gr.Slider(-1.0, 1.0, value=0.0, step=0.05, label="☀️ အလင်းအမှောင် (Brightness)")
                        v3_contrast = gr.Slider(0.0, 2.0, value=1.0, step=0.05, label="🌓 Contrast (အရောင်စုံ ချိန်ရန်)")
                        v3_zoom = gr.Slider(0.5, 2.0, value=1.0, step=0.1, label="🔍 Zoom ဆွဲရန်")

                    with gr.Accordion("🎵 အသံနှင့် နောက်ခံတေးဂီတ (Audio & BGM)", open=False):
                        v3_orig_vol = gr.Slider(0.0, 1.0, value=0.2, step=0.05, label="🔊 မူရင်းဗီဒီယိုအသံ အတိုးအလျော့ (Original Vol)")
                        v3_bgm_file = gr.Audio(label="🎶 နောက်ခံတေးဂီတ (BGM MP3) ထည့်ရန် (Optional)", type="filepath")
                        v3_bgm_vol = gr.Slider(0.0, 1.0, value=0.15, step=0.05, label="🎵 BGM အသံ အတိုးအလျော့")

                    with gr.Accordion("💬 စာတန်းထိုး ဒီဇိုင်းနှင့် နေရာရွှေ့ခြင်း (Subtitles, Blur & Position)", open=True):
                        v3_sub_text = gr.Textbox(label="📝 စာတန်းထိုးစာသား", lines=3, value="ဤနေရာတွင် စာတန်းထိုးပေါ်လာပါမည်...", placeholder="စာတန်းထိုးထည့်ပါ...")
                        v3_font_color = gr.ColorPicker(value="#FFFF00", label="🎨 စာလုံးအရောင် ရွေးချယ်ရန်")
                        v3_stroke_color = gr.ColorPicker(value="#000000", label="🖍️ စာလုံးအနားသတ် အရောင်")
                        v3_font_size = gr.Slider(16, 72, value=28, step=2, label="🔤 စာလုံးအရွယ်အစား (Font Size)")
                        v3_sub_blur = gr.Checkbox(label="🌫️ စာတန်းထိုး နောက်ခံ Blur (ဝေဝါးမှု) ထည့်မည်", value=True)
                        v3_pos_x = gr.Slider(-200, 200, value=0, step=10, label="↔️ ဘယ်/ညာ ရွှေ့ရန် (Position X)")
                        v3_pos_y = gr.Slider(-400, 400, value=0, step=10, label="↕️ အပေါ်/အောက် ရွှေ့ရန် (Position Y)")

                    with gr.Accordion("🏷️ Logo Watermark ထည့်သွင်းရန်", open=False):
                        v3_logo_file = gr.Image(label="🖼️ Logo ပုံတင်ရန်", type="filepath")
                        v3_logo_pos = gr.Dropdown(["ထိပ်ဆုံး ဘယ်ဘက် (Top-Left)", "ထိပ်ဆုံး ညာဘက် (Top-Right)", "အောက်ဆုံး ဘယ်ဘက် (Bottom-Left)", "အောက်ဆုံး ညာဘက် (Bottom-Right)"], value="ထိပ်ဆုံး ညာဘက် (Top-Right)", label="📍 Logo တည်နေရာ")
                        v3_logo_size = gr.Slider(40, 250, value=100, step=10, label="📐 Logo အရွယ်အစား (Size)")

                    v3_render_btn = gr.Button("🚀 Advanced One Clip အမြန်ဆုံး ဖန်တီးမည်", variant="primary")
                
                with gr.Column(scale=1):
                    v3_status = gr.Markdown("Live Preview တွင် အပြောင်းအလဲများကို တိုက်ရိုက်ကြည့်ရှုနိုင်ပါသည်။")
                    
                    gr.Markdown("### 📺 Live Visual Preview (Blur, Logo & Subtitle)")
                    live_preview_box = gr.HTML(
                        update_live_preview("16:9", "ဤနေရာတွင် စာတန်းထိုးပေါ်လာပါမည်...", "#FFFF00", "#000000", 28, True, 0, 0, None, "ထိပ်ဆုံး ညာဘက် (Top-Right)", 100)
                    )

                    gr.Markdown("### 📥 Final Rendered Video")
                    v3_final_mp4 = gr.File(label="📥 Final MP4 Video ဒေါင်းလုဒ်ဆွဲရန်")

            # Real-time Live Preview Inputs Bindings
            preview_inputs = [
                v3_ratio, v3_sub_text, v3_font_color, v3_stroke_color, v3_font_size,
                v3_sub_blur, v3_pos_x, v3_pos_y, v3_logo_file, v3_logo_pos, v3_logo_size
            ]
            for inp in preview_inputs:
                inp.change(update_live_preview, inputs=preview_inputs, outputs=live_preview_box)

            v3_render_btn.click(
                tab3_render_advanced,
                inputs=[
                    v3_video_input, v3_audio_input, v3_resolution, v3_ratio,
                    v3_brightness, v3_contrast, v3_zoom,
                    v3_orig_vol, v3_bgm_file, v3_bgm_vol,
                    v3_sub_text, v3_font_color, v3_stroke_color, v3_font_size,
                    v3_sub_blur, v3_pos_x, v3_pos_y,
                    v3_logo_file, v3_logo_pos, v3_logo_size
                ],
                outputs=[v3_final_mp4, v3_final_mp4, v3_status]
            )

    # ================= EVENT BINDINGS =================
    v1_file.change(lambda f: f, inputs=v1_file, outputs=v1_preview)
    v1_load_btn.click(download_video_from_link, inputs=v1_url, outputs=v1_preview)
    v1_ratio.change(lambda r: get_ratio_css(r, "tab1_preview_container"), inputs=v1_ratio, outputs=v1_css)
    
    v1_gen_btn.click(
        tab1_analyze, 
        inputs=[v1_file, v1_url, v1_ratio], 
        outputs=[v1_script_out, v2_input_text, v1_status, v1_srt, v1_zip]
    )

    go_to_tts_btn.click(lambda: gr.Tabs(selected="tab_tts"), outputs=main_tabs)
    go_to_tab3_btn.click(lambda: gr.Tabs(selected="tab_clip"), outputs=main_tabs)

# Server Port Configuration for Render & Railway
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())
