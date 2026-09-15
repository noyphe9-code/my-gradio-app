# ============================================================
# SMART RECAP STUDIO - FIXED & OPTIMIZED ALL-IN-ONE
# ============================================================

import gradio as gr
import edge_tts
import asyncio
import os
import re
import time
import json
import uuid
import shutil
import subprocess
import zipfile
import math
from pathlib import Path

import yt_dlp
from google import genai

# ============================================================
# SETTINGS
# ============================================================

APP_NAME = "Smart Recap Studio"
MAX_VIDEO_MINUTES = 5
MAX_VIDEO_SECONDS = MAX_VIDEO_MINUTES * 60
SAVED_API_KEY = ""

BASE_DIR = Path("smart_recap_workspace")
BASE_DIR.mkdir(exist_ok=True)

PREVIEW_DIR = BASE_DIR / "previews"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR = BASE_DIR / "uploads"

PREVIEW_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

RETRY_DELAYS = [5, 10, 20, 40, 60]

VOICES = {
    "Thiha — မြန်မာအမျိုးသားအသံ": "my-MM-ThihaNeural",
    "Nilar — မြန်မာအမျိုးသမီးအသံ": "my-MM-NilarNeural",
}

# ============================================================
# UTILITY
# ============================================================

def safe_name(name):
    name = str(name or "file")
    name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)
    return name[:100]

def run_cmd(cmd, timeout=None):
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)

def ffmpeg_available():
    return shutil.which("ffmpeg") is not None

def ffprobe_available():
    return shutil.which("ffprobe") is not None

def get_video_duration(video_path):
    if not video_path or not os.path.exists(video_path) or not ffprobe_available():
        return 0

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    code, out, err = run_cmd(cmd)
    if code != 0:
        return 0
    try:
        return float(out.strip())
    except:
        return 0

def validate_video_length(video_path):
    duration = get_video_duration(video_path)
    if duration <= 0:
        return True, ""
    if duration > MAX_VIDEO_SECONDS:
        return False, f"⚠️ Video အရှည် {duration/60:.1f} မိနစ်ရှိပါတယ်။ အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ ထည့်နိုင်ပါတယ်။"
    return True, f"✅ Video အရှည် {duration:.1f} sec"

def ensure_mp4(video_path):
    if not video_path:
        return None
    ext = Path(video_path).suffix.lower()
    if ext == ".mp4":
        return video_path

    output = UPLOAD_DIR / f"converted_{uuid.uuid4().hex}.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-movflags", "+faststart", str(output)
    ]
    code, out, err = run_cmd(cmd, timeout=900)
    if code == 0 and output.exists():
        return str(output)
    return video_path

def save_api_key(api_key):
    global SAVED_API_KEY
    if not api_key or not api_key.strip():
        return "⚠️ Gemini API Key ထည့်ပေးပါ။"
    SAVED_API_KEY = api_key.strip()
    return "✅ Gemini API Key သိမ်းပြီးပါပြီ။"

# ============================================================
# VIDEO DOWNLOAD
# ============================================================

def download_video_from_link(link):
    if not link or not link.strip():
        return None, "⚠️ Link မရှိပါ။"

    unique = uuid.uuid4().hex[:12]
    output_template = str(UPLOAD_DIR / f"download_{unique}.%(ext)s")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "overwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link.strip(), download=True)
            requested = ydl.prepare_filename(info)
            possible = [requested, os.path.splitext(requested)[0] + ".mp4"]
            for path in possible:
                if os.path.exists(path):
                    ok, msg = validate_video_length(path)
                    if not ok:
                        return None, msg
                    return path, "✅ Link Video ရယူပြီးပါပြီ။"
    except Exception as e:
        return None, f"❌ Video Download Error: {str(e)}"

    return None, "❌ Video file မတွေ့ပါ။"

def load_video_link(link):
    path, msg = download_video_from_link(link)
    if path:
        return path, path, msg
    return None, None, msg

# ============================================================
# SCRIPT CLEANING & SRT
# ============================================================

def clean_script_for_tts(script_text):
    if not script_text:
        return ""
    text = str(script_text)
    text = text.replace("```", "").replace("**", "").replace("__", "")

    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^\[(Visual|Scene|Video|Audio|Narration|Narrator|Action)\]", line, re.I):
            continue
        if re.match(r"^(Visual|Scene|Video|Audio|Narration|Narrator|Action)\s*:", line, re.I):
            line = re.sub(r"^(Visual|Scene|Video|Audio|Narration|Narrator|Action)\s*:\s*", "", line, flags=re.I)
        if line.lower().startswith("here is") or line.startswith("---"):
            continue
        if line.strip():
            lines.append(line)

    return "\n".join(lines)

def seconds_to_srt(sec):
    sec = max(0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def generate_srt(script_text, total_duration=None):
    clean = clean_script_for_tts(script_text)
    if not clean:
        return ""
    lines = [x.strip() for x in clean.splitlines() if x.strip()]
    if not lines:
        return ""

    blocks = []
    if total_duration and total_duration > 0:
        total_chars = sum(len(x) for x in lines)
        current = 0
        for i, line in enumerate(lines):
            weight = len(line) / max(total_chars, 1)
            duration = max(1.2, total_duration * weight)
            start = current
            end = current + duration
            blocks.append((i + 1, start, end, line))
            current = end
    else:
        current = 0
        for i, line in enumerate(lines):
            duration = max(2.0, len(line) / 10)
            start = current
            end = current + duration
            blocks.append((i + 1, start, end, line))
            current = end

    srt = ""
    for idx, start, end, line in blocks:
        srt += f"{idx}\n{seconds_to_srt(start)} --> {seconds_to_srt(end)}\n{line}\n\n"
    return srt

def save_srt_and_zip(script_text, duration=None):
    srt = generate_srt(script_text, duration)
    if not srt:
        return None, None

    uid = uuid.uuid4().hex[:10]
    srt_path = OUTPUT_DIR / f"burmese_subtitle_{uid}.srt"
    zip_path = OUTPUT_DIR / f"burmese_subtitle_{uid}.zip"

    with open(srt_path, "w", encoding="utf-8-sig") as f:
        f.write(srt)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(srt_path, arcname=srt_path.name)

    return str(srt_path), str(zip_path)

# ============================================================
# GEMINI ENGINE
# ============================================================

def wait_for_gemini_file(client, uploaded_file):
    for _ in range(120):
        try:
            current = client.files.get(name=uploaded_file.name)
            state = getattr(current.state, "name", str(current.state))
            if state == "ACTIVE":
                return current
            if state == "FAILED":
                return None
        except:
            pass
        time.sleep(3)
    return None

def generate_with_retry(client, uploaded_file, prompt):
    last_error = ""
    for model in GEMINI_MODELS:
        for attempt in range(4):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[uploaded_file, prompt]
                )
                if response and response.text:
                    return response.text, model
            except Exception as e:
                last_error = str(e)
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)])
    raise RuntimeError(last_error)

def build_recap_prompt():
    return r"""
You are an expert Burmese Movie Recap creator.
Watch the uploaded video carefully from beginning to end.
Create a natural, engaging Burmese Movie Recap script based ONLY on what is actually visible or clearly understandable from the video.

IMPORTANT RULES:
1. Do NOT invent scenes, characters, or backstory.
2. Follow chronological order.
3. Use natural spoken Burmese suitable for AI voice-over.
4. Do NOT use section labels like [Narrator], [Dialogue], [Scene].
5. Output ONLY the finished Burmese recap script.
"""

def analyze_and_generate_script(video_file, video_link, ratio_choice):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        return "", "⚠️ Gemini API Key မရှိသေးပါ။ API Key Setting မှာ ထည့်ပါ။", None, None, None

    if not ffmpeg_available():
        return "", "❌ FFmpeg မတွေ့ပါ။ FFmpeg install လုပ်ပါ။", None, None, None

    target = None
    if video_file:
        target = video_file.get("path") if isinstance(video_file, dict) else str(video_file)
    elif video_link:
        target, msg = download_video_from_link(video_link)
        if not target:
            return "", msg, None, None, None

    if not target or not os.path.exists(target):
        return "", "⚠️ Video File သို့မဟုတ် Link ထည့်ပါ။", None, None, None

    target = ensure_mp4(target)
    ok, length_message = validate_video_length(target)
    if not ok:
        return "", length_message, None, None, None

    try:
        client = genai.Client(api_key=SAVED_API_KEY)
        uploaded = client.files.upload(file=target)
        active = wait_for_gemini_file(client, uploaded)

        if not active:
            return "", "❌ Gemini က Video ကို process မလုပ်နိုင်ပါ။", None, None, None

        prompt = build_recap_prompt()
        script, used_model = generate_with_retry(client, active, prompt)
        clean_script = clean_script_for_tts(script)
        duration = get_video_duration(target)
        srt, zip_file = save_srt_and_zip(clean_script, duration)

        final_status = f"✅ Script အောင်မြင်စွာ ထုတ်ပြီးပါပြီ။\n\nVideo: {duration:.1f} sec | Model: {used_model}"
        return clean_script, final_status, target, srt, zip_file

    except Exception as e:
        return "", f"❌ Script Generate Error:\n{str(e)}", target, None, None

# ============================================================
# TTS ENGINE
# ============================================================

async def make_tts(text, voice_choice, speed):
    if not text or not text.strip():
        return None
    clean = clean_script_for_tts(text)
    if not clean:
        return None

    voice = VOICES.get(voice_choice, "my-MM-ThihaNeural")
    rate = f"{int(speed):+d}%"
    output = OUTPUT_DIR / f"voice_{uuid.uuid4().hex[:10]}.mp3"

    communicate = edge_tts.Communicate(clean, voice, rate=rate)
    await communicate.save(str(output))
    return str(output)

def tts_interface(text, voice_choice, speed):
    try:
        audio = asyncio.run(make_tts(text, voice_choice, speed))
        if not audio:
            return None, None, None, None
        srt, zip_file = save_srt_and_zip(text, None)
        return audio, audio, srt, zip_file
    except Exception as e:
        return None, None, None, None

# ============================================================
# VIDEO EDITING & FILTER ENGINE
# ============================================================

RATIO_SIZE = {
    "9:16": (1080, 1920),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}

def ratio_filter(ratio):
    w, h = RATIO_SIZE.get(ratio, (1080, 1920))
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"

def build_video_filter(ratio, zoom, brightness, contrast, saturation, flip):
    filters = [ratio_filter(ratio)]
    zoom_value = max(1.0, float(zoom))
    if zoom_value > 1.001:
        filters.append(f"scale=iw*{zoom_value}:ih*{zoom_value},crop=iw/{zoom_value}:ih/{zoom_value}")

    if flip == "ဘယ် ↔ ညာ Flip":
        filters.append("hflip")
    elif flip == "အပေါ် ↔ အောက် Flip":
        filters.append("vflip")

    filters.append(f"eq=brightness={float(brightness):.3f}:contrast={float(contrast):.3f}:saturation={float(saturation):.3f}")
    return ",".join(filters)

def overlay_position(position, margin):
    m = int(margin)
    positions = {
        "ဘယ်အပေါ်": f"x={m}:y={m}",
        "အလယ်အပေါ်": f"x=(W-w)/2:y={m}",
        "ညာအပေါ်": f"x=W-w-{m}:y={m}",
        "ဘယ်အလယ်": f"x={m}:y=(H-h)/2",
        "အလယ်": "x=(W-w)/2:y=(H-h)/2",
        "ညာအလယ်": f"x=W-w-{m}:y=(H-h)/2",
        "ဘယ်အောက်": f"x={m}:y=H-h-{m}",
        "အလယ်အောက်": f"x=(W-w)/2:y=H-h-{m}",
        "ညာအောက်": f"x=W-w-{m}:y=H-h-{m}",
    }
    return positions.get(position, f"x={m}:y={m}")

def create_ass_subtitle(script, language, font_name, font_size, text_color, outline_color, outline_width, position, total_duration):
    if not script:
        return None
    srt = generate_srt(script, total_duration)
    if not srt:
        return None

    uid = uuid.uuid4().hex[:10]
    ass_path = OUTPUT_DIR / f"subtitle_{uid}.ass"

    alignment_map = {
        "ဘယ်အပေါ်": 7, "အလယ်အပေါ်": 8, "ညာအပေါ်": 9,
        "ဘယ်အလယ်": 4, "အလယ်": 5, "ညာအလယ်": 6,
        "ဘယ်အောက်": 1, "အလယ်အောက်": 2, "ညာအောက်": 3,
    }
    alignment = alignment_map.get(position, 2)

    def ass_color(hex_color):
        h = str(hex_color).replace("#", "")
        if len(h) != 6: h = "FFFFFF"
        return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"

    primary = ass_color(text_color)
    outline = ass_color(outline_color)

    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{int(font_size)},{primary},&H00000000,{outline},&H80000000,0,0,0,0,100,100,0,0,1,{int(outline_width)},0,{alignment},50,50,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    for line in srt.split("\n\n"):
        parts = line.splitlines()
        if len(parts) < 3: continue
        timing = parts[1]
        if "-->" not in timing: continue
        start, end = timing.split("-->")
        start = start.strip().replace(",", ".")
        end = end.strip().replace(",", ".")
        txt = r"\N".join(parts[2:]).replace("{", r"\{").replace("}", r"\}")
        ass += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{txt}\n"

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write(ass)
    return str(ass_path)

# ============================================================
# FINAL RENDER ENGINE (FIXED AUDIO STREAM MAPPING)
# ============================================================

def create_final_video(
    video_path, voice_path, bg_music, ratio,
    original_audio, original_volume, bg_volume,
    flip, zoom, brightness, contrast, saturation,
    logo, logo_position, logo_size, logo_opacity, logo_margin,
    blur_enabled, blur_x, blur_y, blur_w, blur_h, blur_strength, blur_color,
    subtitle_text, subtitle_language, subtitle_font, subtitle_size,
    subtitle_color, subtitle_outline_color, subtitle_outline_width, subtitle_position,
    preview_mode=False
):
    if not video_path or not os.path.exists(video_path) or not ffmpeg_available():
        return None, "⚠️ Video သို့မဟုတ် FFmpeg အဆင်သင့်မဖြစ်ပါ။"

    duration = get_video_duration(video_path)
    if duration <= 0:
        return None, "❌ Video Duration မဖတ်နိုင်ပါ။"

    uid = uuid.uuid4().hex[:10]
    output = PREVIEW_DIR / f"preview_{uid}.mp4" if preview_mode else OUTPUT_DIR / f"Smart_Recap_{uid}.mp4"

    filter_parts = []
    base_filter = build_video_filter(ratio, zoom, brightness, contrast, saturation, flip)
    filter_parts.append(f"[0:v]{base_filter}[base]")
    current = "base"

    if blur_enabled:
        bx, by = int(blur_x), int(blur_y)
        bw, bh = max(20, int(blur_w)), max(20, int(blur_h))
        bs = max(1, int(blur_strength))
        blur_chain = (
            f"[{current}]split=2[orig][blur];"
            f"[blur]crop={bw}:{bh}:{bx}:{by},boxblur=luma_radius={bs}:luma_power=1[blurbox];"
            f"[orig][blurbox]overlay=x={bx}:y={by}[blurred]"
        )
        filter_parts.append(blur_chain)
        current = "blurred"

    # Logo Setup
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    input_idx = 1

    voice_idx = -1
    if voice_path and os.path.exists(voice_path):
        cmd += ["-i", str(voice_path)]
        voice_idx = input_idx
        input_idx += 1

    bgm_idx = -1
    if bg_music and os.path.exists(bg_music):
        cmd += ["-stream_loop", "-1", "-i", str(bg_music)]
        bgm_idx = input_idx
        input_idx += 1

    if logo and os.path.exists(logo):
        logo_filter = (
            f"movie='{str(logo).replace(chr(92), '/').replace(':', '\\:')}',format=rgba,"
            f"scale=iw*{float(logo_size)/100}:ih*{float(logo_size)/100},"
            f"colorchannelmixer=aa={float(logo_opacity):.2f}[logo]"
        )
        filter_parts.append(logo_filter)
        pos = overlay_position(logo_position, logo_margin)
        filter_parts.append(f"[{current}][logo]overlay={pos}[logoed]")
        current = "logoed"

    # Subtitles Setup
    ass_file = None
    if subtitle_text and subtitle_text.strip():
        ass_file = create_ass_subtitle(
            subtitle_text, subtitle_language, subtitle_font,
            subtitle_size, subtitle_color, subtitle_outline_color,
            subtitle_outline_width, subtitle_position, duration
        )

    if ass_file:
        ass_safe = str(ass_file).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        filter_parts.append(f"[{current}]subtitles='{ass_safe}'[subbed]")
        current = "subbed"

    filter_parts.append(f"[{current}]scale=720:-2[outv]" if preview_mode else f"[{current}]format=yuv420p[outv]")

    # Audio Mix Logic
    audio_inputs = []
    if original_audio:
        audio_inputs.append(f"[0:a]volume={float(original_volume)}[oa]")
    if voice_idx != -1:
        audio_inputs.append(f"[{voice_idx}:a]volume=1.0[va]")
    if bgm_idx != -1:
        audio_inputs.append(f"[{bgm_idx}:a]volume={float(bg_volume)}[ba]")

    if len(audio_inputs) > 0:
        mix_str = "".join(audio_inputs)
        labels = "".join(["[oa]" if original_audio else "", "[va]" if voice_idx != -1 else "", "[ba]" if bgm_idx != -1 else ""])
        if len(audio_inputs) > 1:
            filter_parts.append(f"{mix_str}{labels}amix=inputs={len(audio_inputs)}:duration=first:dropout_transition=2[aout]")
        else:
            filter_parts.append(f"{mix_str}{labels}aresample=async=1[aout]")

    filter_complex = ";".join(filter_parts)
    cmd += ["-filter_complex", filter_complex, "-map", "[outv]"]

    if len(audio_inputs) > 0:
        cmd += ["-map", "[aout]"]

    preset = "veryfast" if preview_mode else "medium"
    crf = "28" if preview_mode else "20"
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(output)]

    code, stdout, stderr = run_cmd(cmd, timeout=1800)
    if code != 0 or not output.exists():
        return None, f"❌ Video Create Error:\n{stderr[-1500:]}"

    return str(output), "✅ Video အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ။"

def generate_preview(*args):
    return create_final_video(*args, preview_mode=True)

def generate_final(*args):
    return create_final_video(*args, preview_mode=False)

# ============================================================
# GRADIO UI
# ============================================================

with gr.Blocks(title=APP_NAME, theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎬 Smart Recap Studio\n### Video → AI Recap → Burmese Voice → Subtitle → Auto Edit")

    with gr.Tab("🔑 API Key"):
        api_key_input = gr.Textbox(label="Gemini API Key", type="password", placeholder="AIza...")
        save_key_btn = gr.Button("💾 API Key သိမ်းမည်", variant="primary")
        key_status = gr.Markdown()
        save_key_btn.click(save_api_key, inputs=api_key_input, outputs=key_status)

    with gr.Tab("1️⃣ Video Analysis + Script"):
        with gr.Row():
            with gr.Column(scale=1):
                video_file = gr.Video(label="📹 Video File", sources=["upload"], type="filepath")
                video_link = gr.Textbox(label="🔗 Video Link", placeholder="https://...")
                load_link_btn = gr.Button("🔍 Link မှ Video ရယူမည်")
                ratio_choice = gr.Radio(choices=["9:16", "3:4", "1:1", "16:9"], value="9:16", label="📐 Aspect Ratio")
                gen_script_btn = gr.Button("🚀 AI Analyze + Script Generate", variant="primary")
                tab1_status = gr.Markdown()
            with gr.Column(scale=1):
                tab1_preview = gr.Video(label="📺 Video Preview", interactive=False)
                script_output = gr.Textbox(label="📝 Burmese Script", lines=15, show_copy_button=True)
                with gr.Row():
                    srt1 = gr.File(label="📄 SRT")
                    zip1 = gr.File(label="📦 ZIP")

    with gr.Tab("2️⃣ Burmese Voice"):
        with gr.Row():
            with gr.Column():
                voice_script = gr.Textbox(label="📝 Script", lines=12, show_copy_button=True)
                voice_dropdown = gr.Dropdown(choices=list(VOICES.keys()), value=list(VOICES.keys())[0], label="🎙️ Voice")
                voice_speed = gr.Slider(minimum=-30, maximum=50, value=5, step=1, label="⚡ Speed (%)")
                gen_voice_btn = gr.Button("🎙️ Generate Burmese Voice", variant="primary")
            with gr.Column():
                audio_output = gr.Audio(label="🔊 Audio Preview", type="filepath")
                mp3_download = gr.File(label="🎵 Download MP3")
                with gr.Row():
                    srt2 = gr.File(label="📄 SRT")
                    zip2 = gr.File(label="📦 ZIP")

    with gr.Tab("3️⃣ One Clip Auto Editor"):
        with gr.Row():
            with gr.Column(scale=1):
                editor_video = gr.Video(label="Video", sources=["upload"], type="filepath")
                editor_voice = gr.Audio(label="Voice MP3", type="filepath")
                bg_music = gr.Audio(label="🎵 BGM", type="filepath")
                editor_ratio = gr.Radio(choices=["9:16", "3:4", "1:1", "16:9"], value="9:16", label="Ratio")
                original_audio = gr.Checkbox(value=True, label="Original Audio ON")
                original_volume = gr.Slider(minimum=0, maximum=2, value=0.25, label="Original Vol")
                bg_volume = gr.Slider(minimum=0, maximum=1, value=0.12, label="BGM Vol")
                flip = gr.Radio(choices=["မပြောင်း", "ဘယ် ↔ ညာ Flip", "အပေါ် ↔ အောက် Flip"], value="မပြောင်း", label="Flip")
                zoom = gr.Slider(minimum=1.0, maximum=2.0, value=1.0, label="Zoom")
                brightness = gr.Slider(minimum=-1, maximum=1, value=0, label="Brightness")
                contrast = gr.Slider(minimum=0.5, maximum=2, value=1, label="Contrast")
                saturation = gr.Slider(minimum=0, maximum=2, value=1, label="Saturation")
                logo_file = gr.Image(label="Logo Upload", type="filepath")
                logo_position = gr.Dropdown(choices=["ဘယ်အပေါ်", "အလယ်အပေါ်", "ညာအပေါ်", "ဘယ်အလယ်", "အလယ်", "ညာအလယ်", "ဘယ်အောက်", "အလယ်အောက်", "ညာအောက်"], value="ညာအပေါ်", label="Logo Pos")
                logo_size = gr.Slider(minimum=10, maximum=100, value=35, label="Logo Size (%)")
                logo_opacity = gr.Slider(minimum=0, maximum=1, value=1, label="Logo Opacity")
                logo_margin = gr.Slider(minimum=0, maximum=200, value=30, label="Logo Margin")

            with gr.Column(scale=1):
                blur_enabled = gr.Checkbox(value=False, label="Blur ON")
                blur_x = gr.Slider(minimum=0, maximum=1080, value=100, label="Blur X")
                blur_y = gr.Slider(minimum=0, maximum=1920, value=200, label="Blur Y")
                blur_w = gr.Slider(minimum=20, maximum=1080, value=400, label="Blur Width")
                blur_h = gr.Slider(minimum=20, maximum=1000, value=150, label="Blur Height")
                blur_strength = gr.Slider(minimum=1, maximum=40, value=10, label="Blur Strength")
                blur_color = gr.ColorPicker(value="#000000", label="Blur Color")

                subtitle_script = gr.Textbox(label="Subtitle Text", lines=6)
                subtitle_language = gr.Radio(choices=["Myanmar", "English", "Thai"], value="Myanmar", label="Language")
                subtitle_font = gr.Dropdown(choices=["Noto Sans", "Arial", "DejaVu Sans", "Tahoma"], value="Noto Sans", label="Font")
                subtitle_size = gr.Slider(minimum=20, maximum=100, value=48, label="Font Size")
                subtitle_color = gr.ColorPicker(value="#FFFFFF", label="Text Color")
                subtitle_outline_color = gr.ColorPicker(value="#000000", label="Outline Color")
                subtitle_outline_width = gr.Slider(minimum=0, maximum=15, value=3, label="Outline Width")
                subtitle_position = gr.Dropdown(choices=["ဘယ်အပေါ်", "အလယ်အပေါ်", "ညာအပေါ်", "ဘယ်အလယ်", "အလယ်", "ညာအလယ်", "ဘယ်အောက်", "အလယ်အောက်", "ညာအောက်"], value="အလယ်အောက်", label="Subtitle Pos")

        with gr.Row():
            preview_button = gr.Button("👁️ Preview ပြမည်", variant="secondary")
            final_button = gr.Button("🎬 Create Final Video", variant="primary")

        preview_status = gr.Markdown()
        editor_preview = gr.Video(label="🎞️ Preview Video", autoplay=False)
        final_status = gr.Markdown()
        final_video = gr.Video(label="🎬 Final Video", autoplay=False)
        final_download = gr.File(label="⬇️ Download MP4")

    # Connections
    video_file.change(fn=lambda x: x, inputs=video_file, outputs=tab1_preview)
    load_link_btn.click(fn=load_video_link, inputs=video_link, outputs=[video_file, tab1_preview, tab1_status])
    gen_script_btn.click(fn=analyze_and_generate_script, inputs=[video_file, video_link, ratio_choice], outputs=[script_output, tab1_status, editor_video, srt1, zip1]).then(fn=lambda x: x, inputs=script_output, outputs=[voice_script, subtitle_script])
    gen_voice_btn.click(fn=tts_interface, inputs=[voice_script, voice_dropdown, voice_speed], outputs=[audio_output, mp3_download, srt2, zip2]).then(fn=lambda x: x, inputs=audio_output, outputs=editor_voice)
    voice_script.change(fn=lambda x: x, inputs=voice_script, outputs=subtitle_script)

    preview_inputs = [
        editor_video, editor_voice, bg_music, editor_ratio,
        original_audio, original_volume, bg_volume,
        flip, zoom, brightness, contrast, saturation,
        logo_file, logo_position, logo_size, logo_opacity, logo_margin,
        blur_enabled, blur_x, blur_y, blur_w, blur_h, blur_strength, blur_color,
        subtitle_script, subtitle_language, subtitle_font, subtitle_size,
        subtitle_color, subtitle_outline_color, subtitle_outline_width, subtitle_position
    ]

    preview_button.click(fn=generate_preview, inputs=preview_inputs, outputs=[editor_preview, preview_status])
    final_button.click(fn=generate_final, inputs=preview_inputs, outputs=[final_video, final_status]).then(fn=lambda x: x, inputs=final_video, outputs=final_download)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, show_error=True)
