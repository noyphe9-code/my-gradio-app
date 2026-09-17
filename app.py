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
# CONFIGURATION & CONSTANTS
# =========================================================
APP_TITLE = "Advanced AI Video Recap Studio"
SAVED_API_KEY = ""
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]

VOICE_OPTIONS = {
    "သီဟ (Thiha - Male)": "my-MM-ThihaNeural",
    "နီလာ (Nilar - Female)": "my-MM-NilarNeural"
}

SUBTITLE_LANGUAGES = ["မြန်မာ (Burmese)", "English", "ไทย (Thai)"]

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
# CORE SYSTEM HELPERS
# =========================================================
def save_api_key(key):
    global SAVED_API_KEY
    if key and key.strip():
        SAVED_API_KEY = key.strip()
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
    return "⚠️ Gemini API Key ထည့်ပေးပါ။"

def get_media_duration(file_path):
    if not file_path or not os.path.exists(file_path):
        return 0.0
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        return float(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else 0.0
    except Exception:
        return 0.0

def has_audio(file_path):
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        return "audio" in res.stdout.lower()
    except Exception:
        return False

def download_link(url):
    if not url or not url.strip():
        return None
    out = "downloaded_input.%(ext)s"
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": out,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "merge_output_format": "mp4",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=True)
            fn = ydl.prepare_filename(info)
            if os.path.exists(fn):
                return fn
            base = os.path.splitext(fn)[0]
            for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                if os.path.exists(base + ext):
                    return base + ext
    except Exception as e:
        print("Download error:", e)
    return None

def clean_script(text):
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"\*\*|__|`", "", line)
        line = re.sub(r"^\s*\[(?:Visual|Scene|Audio|Video|Camera|Action)\]\s*[:\-]?\s*", "", line, flags=re.IGNORECASE)
        lines.append(line.strip())
    return "\n".join(lines)

def split_lines(text, max_len=24):
    text = text.strip()
    if len(text) <= max_len:
        return text
    words = text.split(" ")
    if len(words) >= 2:
        mid = len(words) // 2
        return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
    mid_idx = len(text) // 2
    return text[:mid_idx] + "\n" + text[mid_idx:]

def format_timestamp(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def make_srt(script_text, total_duration):
    raw_lines = [l.strip() for l in clean_script(script_text).splitlines() if l.strip()]
    if not raw_lines:
        return None
    chunks = []
    for rl in raw_lines:
        if len(rl) > 40:
            for p in re.split(r'(?<=[။၊.,!?])\s*', rl):
                if p.strip():
                    chunks.append(split_lines(p.strip()))
        else:
            chunks.append(split_lines(rl))
    
    srt_str = ""
    curr = 0.0
    total_chars = sum(len(c.replace("\n", "")) for c in chunks)
    for i, line in enumerate(chunks, 1):
        weight = len(line.replace("\n", "")) / max(1, total_chars)
        dur = max(1.2, weight * total_duration)
        start = curr
        end = min(total_duration, curr + dur)
        srt_str += f"{i}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{line}\n\n"
        curr = end
        
    srt_path = "subtitles.srt"
    with open(srt_path, "w", encoding="utf-8-sig") as f:
        f.write(srt_str)
    return srt_path

# =========================================================
# AI SCRIPTING & TRANSLATION ENGINE
# =========================================================
def generate_recap_script(video_path, ratio):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        raise ValueError("Gemini API Key ထည့်သွင်းပေးရန် လိုအပ်ပါသည်။")
    
    client = genai.Client(api_key=SAVED_API_KEY)
    video_upload = client.files.upload(file=video_path)
    
    start_time = time.time()
    while True:
        if video_upload.state and video_upload.state.name == "ACTIVE":
            break
        if video_upload.state and video_upload.state.name == "FAILED":
            raise RuntimeError("Video processing failed on Gemini server.")
        if time.time() - start_time > 600:
            raise TimeoutError("Video processing timed out.")
        time.sleep(3)
        video_upload = client.files.get(name=video_upload.name)
        
    prompt = f"""
မင်းက ကျွမ်းကျင်သော Professional Movie Recap Narrator ဖြစ်သည်။
ရုပ်ရှင်ကို ပုံမှန်ပုံပြင်ပြောသလို လုံးဝမပြောပါနှင့်။
ဆွဲဆောင်မှုအပြည့်ရှိသော ရုပ်ရှင်ဇာတ်လမ်းပြော Narrator စတိုင်ဖြင့် ရေးသားပါ။
ဇာတ်လမ်းပြောနေစဉ်အတွင်း ဇာတ်ကောင်များ၏ အပြန်အလှန်ပြောစကားများ (Dialogues) ကို သဘာဝကျကျ ထည့်သွင်းပေးပါ။
Aspect Ratio: {ratio}
မှတ်ချက်- [Visual], [Scene], [Narrator] စသည့် tag များမထည့်ပါနှင့်။
"""
    for model in GEMINI_MODELS:
        try:
            res = client.models.generate_content(model=model, contents=[video_upload, prompt])
            if res and res.text:
                return clean_script(res.text)
        except Exception:
            continue
    raise RuntimeError("AI Script ရေးသားမှု မအောင်မြင်ပါ။")

def translate_script(script, target_lang):
    global SAVED_API_KEY
    if target_lang == "မြန်မာ (Burmese)" or not SAVED_API_KEY:
        return script
    client = genai.Client(api_key=SAVED_API_KEY)
    prompt = f"Translate the following movie recap script into {target_lang} for subtitles. Keep it cinematic and concise:\n\n{script}"
    for model in GEMINI_MODELS:
        try:
            res = client.models.generate_content(model=model, contents=prompt)
            if res and res.text:
                return clean_script(res.text)
        except Exception:
            continue
    return script

# =========================================================
# TTS LOGIC
# =========================================================
async def run_edge_tts(text, voice, out_path="tts_voice.mp3"):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)
    return out_path

# =========================================================
# REAL-TIME PREVIEW GENERATOR (BLUR & SUBTITLE OVERLAY)
# =========================================================
def hex_to_rgba(h, alpha):
    h = h.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"
    return f"rgba(0, 0, 0, {alpha})"

def build_preview_overlay(
    ratio, brightness, contrast, zoom, flip_h,
    blur_color, blur_opacity, blur_w, blur_h, blur_x, blur_y,
    logo_file, logo_size, logo_x, logo_y,
    sub_lang, font_name, font_size, text_color, stroke_color, sub_x, sub_y
):
    aspect_map = {"1:1": "1 / 1", "3:4": "3 / 4", "16:9": "16 / 9", "9:16": "9 / 16"}
    max_w_map = {"1:1": "420px", "3:4": "360px", "16:9": "580px", "9:16": "320px"}
    
    aspect = aspect_map.get(ratio, "9:16")
    max_w = max_w_map.get(ratio, "320px")
    flip_factor = "-1" if flip_h else "1"
    
    # Blur Layer (Preview ပေါ်တွင်သာ တိုက်ရိုက်ပြသခြင်း)
    blur_bg = hex_to_rgba(blur_color, blur_opacity)
    blur_html = f"""
    <div style="
        position: absolute;
        left: calc(50% + {blur_x}px);
        top: calc(50% - {blur_y}px);
        transform: translate(-50%, -50%);
        width: {blur_w}%;
        height: {blur_h}px;
        background: {blur_bg};
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border-radius: 8px;
        z-index: 25;
        pointer-events: none;
        box-shadow: 0 0 12px rgba(0,0,0,0.3);
    "></div>
    """

    # Logo Layer (Circle Frame)
    logo_html = ""
    if logo_file and os.path.exists(logo_file):
        try:
            with open(logo_file, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            logo_html = f"""
            <img src="data:image/png;base64,{b64}" style="
                position: absolute;
                left: calc(50% + {logo_x}px);
                top: calc(50% - {logo_y}px);
                transform: translate(-50%, -50%);
                width: {logo_size}px;
                height: {logo_size}px;
                border-radius: 50%;
                object-fit: cover;
                z-index: 30;
                pointer-events: none;
                border: 2px solid rgba(255,255,255,0.8);
                box-shadow: 0 2px 8px rgba(0,0,0,0.4);
            "/>
            """
        except Exception:
            pass

    # Subtitle Layer (Preview Video ပေါ်တွင်သာ တိုက်ရိုက်ပြသခြင်း)
    sample_sub = "ရုပ်ရှင်ဇာတ်လမ်းအညွှန်း စာတန်းထိုး နမူနာ"
    if "English" in sub_lang:
        sample_sub = "Cinematic Movie Recap Subtitle Preview"
    elif "Thai" in sub_lang:
        sample_sub = "ตัวอย่างคำบรรยายภาพยนตร์ตัวอย่าง"

    sub_html = f"""
    <div style="
        position: absolute;
        left: calc(50% + {sub_x}px);
        top: calc(50% - {sub_y}px);
        transform: translate(-50%, -50%);
        width: 90%;
        text-align: center;
        z-index: 35;
        pointer-events: none;
    ">
        <span style="
            display: inline-block;
            font-family: '{font_name}', sans-serif;
            font-size: {font_size}px;
            line-height: 1.4;
            color: {text_color};
            text-shadow: -2px -2px 0 {stroke_color}, 2px -2px 0 {stroke_color}, -2px 2px 0 {stroke_color}, 2px 2px 0 {stroke_color}, 0px 3px 6px rgba(0,0,0,0.9);
            font-weight: bold;
        ">{sample_sub}</span>
    </div>
    """

    return f"""
    <style id="preview-stage-style">
    #preview_stage {{
        width: 100% !important;
        max-width: {max_w} !important;
        margin: 0 auto !important;
        position: relative !important;
    }}
    #preview_stage .video-container {{
        width: 100% !important;
        aspect-ratio: {aspect} !important;
        background: #000 !important;
        border-radius: 12px !important;
        overflow: hidden !important;
        position: relative !important;
        box-shadow: 0 8px 24px rgba(0,0,0,0.5) !important;
    }}
    #preview_stage video {{
        width: 100% !important;
        height: 100% !important;
        aspect-ratio: {aspect} !important;
        object-fit: cover !important;
        transform: scale({zoom}) scaleX({flip_factor}) !important;
        filter: brightness({brightness}) contrast({contrast}) !important;
        display: block !important;
        position: relative !important;
        z-index: 5 !important;
    }}
    </style>
    <div id="preview_stage_overlay" style="
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        width: 100%; height: 100%;
        overflow: hidden;
        border-radius: 12px;
        pointer-events: none;
        z-index: 20;
    ">
        {blur_html}
        {logo_html}
        {sub_html}
    </div>
    """

# =========================================================
# FFMPEG AUTO-SYNC, CUT & EXPORT COMPOSER
# =========================================================
def hex_to_ass(hex_str):
    h = hex_str.lstrip("#")
    if len(h) == 6:
        return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()
    return "&H00FFFFFF&"

def compose_final_recap(
    video_source, tts_audio, srt_file, bgm_file,
    enable_orig_audio, bgm_volume,
    ratio, brightness, contrast, zoom, flip_h,
    blur_color, blur_opacity, blur_w, blur_h, blur_x, blur_y,
    logo_file, logo_size, logo_x, logo_y,
    font_name, font_size, text_color, stroke_color, sub_y,
    output_path="final_recap_clip.mp4"
):
    dims = {"1:1": (1080, 1080), "3:4": (810, 1080), "16:9": (1920, 1080), "9:16": (1080, 1920)}
    tw, th = dims.get(ratio, (1080, 1920))
    tts_dur = get_media_duration(tts_audio)
    
    flip = "hflip," if flip_h else ""
    eq_b = brightness - 1.0
    vf_base = f"[0:v]{flip}scale=iw*{zoom}:ih*{zoom},eq=brightness={eq_b:.2f}:contrast={contrast:.2f},crop={tw}:{th}:(iw-{tw})/2:(ih-{th})/2[v_base]"
    
    filter_chain = [vf_base]
    curr_v = "v_base"
    
    # 1. Blur Box Addition
    bw = int(tw * (blur_w / 100.0))
    bh = int(blur_h * (th / 1920.0 * 2.0))
    bx = f"(W-{bw})/2+({blur_x})"
    by = f"(H-{bh})/2-({blur_y})"
    m_hex = blur_color.lstrip("#")
    
    filter_chain.append(f"color=c=0x{m_hex}@{blur_opacity}:s={bw}x{bh}[b_box];[{curr_v}][b_box]overlay={bx}:{by}[v_blurred]")
    curr_v = "v_blurred"
    
    # 2. Circular Logo Addition
    inputs = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", video_source, "-i", tts_audio]
    next_idx = 2
    
    if logo_file and os.path.exists(logo_file):
        inputs.extend(["-i", logo_file])
        lx = f"(W-{logo_size})/2+({logo_x})"
        ly = f"(H-{logo_size})/2-({logo_y})"
        filter_chain.append(
            f"[{next_idx}:v]scale={logo_size}:{logo_size},format=yuva420p,"
            f"geq=lum='p(X,Y)':a='if(lte((X-W/2)^2+(Y-H/2)^2,(min(W,H)/2)^2),255,0)'[circular_logo];"
            f"[{curr_v}][circular_logo]overlay={lx}:{ly}[v_logo]"
        )
        curr_v = "v_logo"
        next_idx += 1
        
    # 3. Subtitles Overlay
    ass_primary = hex_to_ass(text_color)
    ass_outline = hex_to_ass(stroke_color)
    escaped_srt = srt_file.replace("\\", "/").replace(":", "\\:")
    margin_v = max(10, int((th / 2) - sub_y))
    
    sub_filter = (
        f"subtitles='{escaped_srt}':force_style="
        f"'FontName={font_name},FontSize={font_size},"
        f"PrimaryColour={ass_primary},OutlineColour={ass_outline},"
        f"BorderStyle=3,Outline=3,Shadow=2,Alignment=2,MarginV={margin_v}'"
    )
    filter_chain.append(f"[{curr_v}]{sub_filter}[vout]")
    
    # 4. Audio Mixing (Auto-Sync & Balance)
    audio_chains = []
    audio_inputs = ["[1:a]volume=1.0[tts_a]"]
    mix_sources = ["[tts_a]"]
    
    if enable_orig_audio and has_audio(video_source):
        audio_chains.append("[0:a]volume=0.2[orig_a]")
        mix_sources.append("[orig_a]")
        
    if bgm_file and os.path.exists(bgm_file):
        inputs.extend(["-stream_loop", "-1", "-i", bgm_file])
        audio_chains.append(f"[{next_idx}:a]volume={bgm_volume}[bgm_a]")
        mix_sources.append("[bgm_a]")
        
    audio_chains.extend(audio_inputs)
    mix_count = len(mix_sources)
    audio_chains.append(f"{''.join(mix_sources)}amix=inputs={mix_count}:duration=first:dropout_transition=2[aout]")
    
    fc = ";".join(filter_chain) + ";" + ";".join(audio_chains)
    
    cmd = inputs + [
        "-filter_complex", fc,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", str(tts_dur),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]
    subprocess.run(cmd, check=True)
    return output_path

# =========================================================
# MAIN GENERATION PIPELINE
# =========================================================
def run_pipeline(
    v_file, v_link, ratio, brightness, contrast, zoom, flip_h,
    blur_color, blur_opacity, blur_w, blur_h, blur_x, blur_y,
    logo_file, logo_size, logo_x, logo_y,
    voice_choice, mute_orig, bgm_file, bgm_vol,
    sub_lang, font_name, font_size, text_color, stroke_color, sub_x, sub_y
):
    target_video = v_file if v_file else download_link(v_link)
    if not target_video or not os.path.exists(target_video):
        return None, None, "⚠️ ဗီဒီယိုဖိုင် သို့မဟုတ် Link ကို စစ်ဆေးပေးပါ။"

    try:
        # Step 1: Scripting
        script = generate_recap_script(target_video, ratio)
        sub_script = translate_script(script, sub_lang)
        
        # Step 2: TTS Voiceover
        voice_code = VOICE_OPTIONS.get(voice_choice, "my-MM-ThihaNeural")
        audio_out = asyncio.run(run_edge_tts(script, voice_code))
        
        # Step 3: Subtitles Creation
        total_dur = get_media_duration(audio_out)
        srt_file = make_srt(sub_script, total_dur)
        
        # Step 4: Video Composition (Sync & Auto-cut)
        final_video = compose_final_recap(
            video_source=target_video,
            tts_audio=audio_out,
            srt_file=srt_file,
            bgm_file=bgm_file,
            enable_orig_audio=(not mute_orig),
            bgm_volume=bgm_vol,
            ratio=ratio,
            brightness=brightness,
            contrast=contrast,
            zoom=zoom,
            flip_h=flip_h,
            blur_color=blur_color,
            blur_opacity=blur_opacity,
            blur_w=blur_w,
            blur_h=blur_h,
            blur_x=blur_x,
            blur_y=blur_y,
            logo_file=logo_file,
            logo_size=logo_size,
            logo_x=logo_x,
            logo_y=logo_y,
            font_name=font_name,
            font_size=font_size,
            text_color=text_color,
            stroke_color=stroke_color,
            sub_y=sub_y,
            output_path="final_recap_clip.mp4"
        )
        return final_video, final_video, "🎉 Movie Recap One Clip ဖန်တီးမှု အောင်မြင်ပါပြီ!"
    except Exception as e:
        return None, None, f"❌ Error: {str(e)}"

# =========================================================
# GRADIO INTERFACE
# =========================================================
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**Professional Automated Movie Recap Studio**")
    
    with gr.Accordion("🔑 Gemini API Settings", open=False):
        api_input = gr.Textbox(label="Gemini API Key", type="password", placeholder="AIzaSy...")
        api_btn = gr.Button("API Key သိမ်းဆည်းရန်", variant="primary")
        api_status = gr.Markdown("")
        api_btn.click(save_api_key, inputs=api_input, outputs=api_status)

    with gr.Row():
        # LEFT: CONTROLS & SETTINGS
        with gr.Column(scale=1):
            with gr.Accordion("၁။ Input စနစ် (Video Source)", open=True):
                ui_file = gr.Video(label="ဗီဒီယို ဖိုင်တင်ရန်")
                ui_link = gr.Textbox(label="Video Link (YouTube, TikTok, Facebook, Rednote)")
                ui_load_link = gr.Button("🔗 Link မှ Video ဆွဲယူမည်", variant="secondary")
                ui_ratio = gr.Radio(["9:16", "16:9", "1:1", "3:4"], value="9:16", label="Aspect Ratio")

            with gr.Accordion("၂။ Video ချိန်ညှိမှုများ & Blur & Logo", open=False):
                with gr.Row():
                    ui_bright = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="Brightness")
                    ui_contrast = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="Contrast")
                with gr.Row():
                    ui_zoom = gr.Slider(0.8, 2.0, value=1.0, step=0.05, label="Zoom")
                    ui_flip = gr.Checkbox(label="ဘယ်ညာလှန်မည် (Flip Horizontal)", value=False)
                
                gr.Markdown("#### 🌫️ Blur Box ချိန်ညှိချက်များ")
                with gr.Row():
                    ui_blur_color = gr.ColorPicker(label="Blur Box အရောင်", value="#000000")
                    ui_blur_opacity = gr.Slider(0.0, 1.0, value=0.7, step=0.05, label="Blur Opacity")
                with gr.Row():
                    ui_blur_w = gr.Slider(10, 100, value=90, step=1, label="Blur အကျယ် (%)")
                    ui_blur_h = gr.Slider(10, 300, value=90, step=5, label="Blur အမြင့် (px)")
                with gr.Row():
                    ui_blur_x = gr.Slider(-400, 400, value=0, step=5, label="Blur ↔️ ဘယ်/ညာ")
                    ui_blur_y = gr.Slider(-700, 700, value=-260, step=5, label="Blur ↕️ အပေါ်/အောက်")

                gr.Markdown("#### 🔘 Logo Watermark")
                ui_logo = gr.Image(label="Logo ပုံတင်ရန်", type="filepath")
                ui_logo_size = gr.Slider(30, 250, value=90, step=5, label="Logo အရွယ်အစား (px)")
                with gr.Row():
                    ui_logo_x = gr.Slider(-400, 400, value=0, step=5, label="Logo ↔️ ဘယ်/ညာ")
                    ui_logo_y = gr.Slider(-700, 700, value=300, step=5, label="Logo ↕️ အပေါ်/အောက်")

            with gr.Accordion("၃။ အသံပိုင်းဆိုင်ရာ (Voiceover, BGM & Audio)", open=False):
                ui_voice = gr.Radio(list(VOICE_OPTIONS.keys()), value="သီဟ (Thiha - Male)", label="🎙️ TTS Voice ရွေးချယ်ပါ")
                ui_mute_orig = gr.Checkbox(label="🔇 မူရင်း Video အသံကို ပိတ်မည် (Mute)", value=False)
                ui_bgm = gr.Audio(label="Background Music (BGM)", type="filepath")
                ui_bgm_vol = gr.Slider(0.0, 1.0, value=0.15, step=0.05, label="BGM Volume")

            with gr.Accordion("၄။ စာတန်းထိုး (Subtitles & Fonts)", open=False):
                ui_sub_lang = gr.Radio(SUBTITLE_LANGUAGES, value="မြန်မာ (Burmese)", label="စာတန်းထိုး ဘာသာစကား")
                ui_font = gr.Dropdown(FONT_CHOICES, value="Pyidaungsu", label="Font ရွေးချယ်ပါ")
                ui_fsize = gr.Slider(14, 50, value=24, step=1, label="စာလုံး အရွယ်အစား")
                with gr.Row():
                    ui_fcolor = gr.ColorPicker(label="စာလုံး အရောင်", value="#00FF66")
                    ui_scolor = gr.ColorPicker(label="အနားသတ် Border အရောင်", value="#000000")
                with gr.Row():
                    ui_sub_x = gr.Slider(-400, 400, value=0, step=5, label="စာတန်းထိုး ↔️ ဘယ်/ညာ")
                    ui_sub_y = gr.Slider(-700, 700, value=-260, step=5, label="စာတန်းထိုး ↕️ အပေါ်/အောက်")

            ui_gen_btn = gr.Button("🚀 Generate Movie Recap One Clip", variant="primary", size="lg")

        # RIGHT: LIVE PREVIEW & FINAL EXPORT
        with gr.Column(scale=1):
            gr.Markdown("### 📺 Live Video Stage Preview")
            gr.Markdown("> *စာတန်းထိုးနှင့် Blur Box များကို Preview ဘောင်အတွင်း တိုက်ရိုက် ကြည့်ရှုနိုင်ပါသည်။*")
            
            ui_preview_css = gr.HTML(build_preview_overlay(
                "9:16", 1.0, 1.0, 1.0, False,
                "#000000", 0.7, 90, 90, 0, -260,
                None, 90, 0, 300,
                "မြန်မာ (Burmese)", "Pyidaungsu", 24, "#00FF66", "#000000", 0, -260
            ))
            ui_preview_vid = gr.Video(label="Preview Screen", elem_id="preview_stage")
            
            ui_status = gr.Markdown("ဗီဒီယို ရွေးချယ်ရန် အဆင်သင့်ဖြစ်ပါသည်။")
            ui_final_vid = gr.Video(label="🎬 အပြီးစီး ထွက်ရှိလာသော One Clip Video")
            ui_download_btn = gr.File(label="📥 Download Recap Video")

    # ================= EVENT BINDINGS =================
    ui_file.change(lambda f: f, inputs=ui_file, outputs=ui_preview_vid)
    ui_load_link.click(download_link, inputs=ui_link, outputs=ui_preview_vid)
    
    live_preview_inputs = [
        ui_ratio, ui_bright, ui_contrast, ui_zoom, ui_flip,
        ui_blur_color, ui_blur_opacity, ui_blur_w, ui_blur_h, ui_blur_x, ui_blur_y,
        ui_logo, ui_logo_size, ui_logo_x, ui_logo_y,
        ui_sub_lang, ui_font, ui_fsize, ui_fcolor, ui_scolor, ui_sub_x, ui_sub_y
    ]
    for element in live_preview_inputs:
        element.change(build_preview_overlay, inputs=live_preview_inputs, outputs=ui_preview_css)

    ui_gen_btn.click(
        run_pipeline,
        inputs=[
            ui_file, ui_link, ui_ratio, ui_bright, ui_contrast, ui_zoom, ui_flip,
            ui_blur_color, ui_blur_opacity, ui_blur_w, ui_blur_h, ui_blur_x, ui_blur_y,
            ui_logo, ui_logo_size, ui_logo_x, ui_logo_y,
            ui_voice, ui_mute_orig, ui_bgm, ui_bgm_vol,
            ui_sub_lang, ui_font, ui_fsize, ui_fcolor, ui_scolor, ui_sub_x, ui_sub_y
        ],
        outputs=[ui_final_vid, ui_download_btn, ui_status]
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
