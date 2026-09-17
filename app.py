import os
import sys
import locale
import re
import time
import zipfile
import subprocess
import asyncio
import base64
import gradio as gr

# Burmese/Thai/Chinese output must never fall back to ASCII.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
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

VOICES_BY_LANG = {
    "မြန်မာ (Burmese Voice)": {
        "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
        "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural",
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

# =========================================================
# SYSTEM HELPERS
# =========================================================
def as_filepath(value):
    """Normalize Gradio filepath/dict inputs without encoding surprises."""
    if not value:
        return None
    if isinstance(value, dict):
        value = value.get("path") or value.get("name")
    path = os.fspath(value) if isinstance(value, (str, bytes, os.PathLike)) else None
    return path if path and os.path.exists(path) else None

def safe_error(exc):
    """Return a Unicode-safe, user-readable error message."""
    try:
        return str(exc).encode("utf-8", "replace").decode("utf-8", "replace")
    except Exception:
        return repr(exc)

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
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print("Duration Error:", e)
    return None

def detect_scene_boundaries(video_path, threshold=0.35):
    """Return scene-change timestamps using FFmpeg's scene score detector."""
    if not video_path or not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-hide_banner", "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-"
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=300)
        return sorted({float(x) for x in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr)})
    except Exception as e:
        print("Scene detection skipped:", e)
        return []


def detect_freeze_intervals(video_path, min_duration=1.5):
    """Find long frozen/near-duplicate visual intervals."""
    if not video_path or not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-hide_banner", "-i", video_path,
        "-vf", f"freezedetect=n=-60dB:d={min_duration}",
        "-an", "-f", "null", "-"
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=300)
        log = result.stderr
        starts = [float(x) for x in re.findall(r"freeze_start:([0-9]+(?:\.[0-9]+)?)", log)]
        ends = [float(x) for x in re.findall(r"freeze_end:([0-9]+(?:\.[0-9]+)?)", log)]
        intervals = []
        for i, start in enumerate(starts):
            end = ends[i] if i < len(ends) else None
            if end is not None and end - start >= min_duration:
                intervals.append((start, end))
        return intervals
    except Exception as e:
        print("Freeze detection skipped:", e)
        return []


def build_keep_intervals(duration, removed_intervals, min_clip=0.12):
    """Subtract removed intervals from the source timeline."""
    if not duration or duration <= 0:
        return []
    cursor = 0.0
    keep = []
    for start, end in sorted(removed_intervals):
        start, end = max(0.0, start), min(duration, end)
        if start > cursor + min_clip:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + min_clip:
        keep.append((cursor, duration))
    return keep or [(0.0, duration)]


def prepare_scene_aware_video(source_video, output_filename="scene_aware_source.mp4"):
    """Remove long frozen/duplicate-looking intervals and keep A/V aligned.

    Scene timestamps are detected for diagnostics and cut planning. Long freeze
    intervals are removed; short scene segments are joined with tiny fades.
    On any failure, the original source is returned safely.
    """
    duration = get_video_duration(source_video)
    if not duration or duration < 2.0:
        return source_video
    scene_points = detect_scene_boundaries(source_video)
    freeze_intervals = detect_freeze_intervals(source_video)
    print(f"Scene-aware edit: {len(scene_points)} scene changes, {len(freeze_intervals)} freeze intervals")
    # Do not remove an entire short clip or more than half its content.
    removed = [(a, b) for a, b in freeze_intervals if b - a >= 1.5 and (b - a) <= duration * 0.5]
    keep = build_keep_intervals(duration, removed)
    if len(keep) <= 1:
        return source_video

    chains = []
    has_audio = has_audio_stream(source_video)
    for i, (start, end) in enumerate(keep):
        seg_dur = max(0.01, end - start)
        fade_d = min(0.08, seg_dur / 3.0)
        chains.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"fade=t=in:st=0:d={fade_d:.3f},fade=t=out:st={max(0.0, seg_dur-fade_d):.3f}:d={fade_d:.3f}[v{i}]"
        )
        if has_audio:
            chains.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    if has_audio:
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
        chains.append(f"{concat_inputs}concat=n={len(keep)}:v=1:a=1[vout][aout]")
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(len(keep)))
        chains.append(f"{concat_inputs}concat=n={len(keep)}:v=1:a=0[vout]")

    cmd = ["ffmpeg", "-y", "-i", source_video, "-filter_complex", ";".join(chains), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", output_filename]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=900)
        return output_filename if os.path.exists(output_filename) else source_video
    except Exception as e:
        print("Scene-aware render skipped:", e)
        return source_video


def has_audio_stream(video_path):
    if not video_path or not os.path.exists(video_path):
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=15
        )
        return "audio" in result.stdout.lower()
    except Exception:
        return False

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

def split_into_two_lines(text, max_line_len=20):
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
    seconds = max(0, float(seconds))
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
            dur = max(1.5, line_weight * total_target_duration)
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
    with open(srt_filename, "w", encoding="utf-8-sig", newline="\n") as f:
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
        "windowsfilenames": True,
        "consoletitle": False,
        "logger": None,
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
# TRANSLATION ENGINE
# =========================================================
def has_foreign_text(text):
    if not text:
        return False
    if re.search(r"[a-zA-Z]{3,}", text) or re.search(r"[\u0E00-\u0E7F]", text) or re.search(r"[\u4E00-\u9FFF]", text):
        return True
    return False

def translate_to_target_language(text, target_lang):
    global SAVED_API_KEY
    if not text or not text.strip():
        return ""
    if not SAVED_API_KEY:
        raise ValueError("Gemini API Key မရှိသေးပါ။ 🔑 API Key Setting ထဲတွင် အရင်ထည့်သွင်းပေးပါ။")

    client = genai.Client(api_key=SAVED_API_KEY)
    prompt = f"""
You are an expert movie subtitle translator.
Translate the following movie narration lines into {target_lang} for 2-line subtitles.
Keep each line concise, natural, and accurately mapped.
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

# =========================================================
# STABLE CSS & IN-CONTAINER REAL-TIME PREVIEW ENGINE
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

def get_tab3_in_video_preview_html(
    sub_lang,
    ratio, video_speed, flip_h, scale_val, x_off, y_off,
    crop_w_pct, crop_h_pct,
    use_blur_bg, bg_color,
    bright_val, contrast_val,
    mask_enable, mask_type, mask_color, mask_opacity, mask_w, mask_h, mask_x, mask_y,
    logo_file, logo_size, logo_x, logo_y,
    font_family, font_size, font_color, outline_color, sub_x, sub_y
):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "380px"},
        "3:4": {"aspect": "3 / 4", "max_w": "340px"},
        "16:9": {"aspect": "16 / 9", "max_w": "520px"},
        "9:16": {"aspect": "9 / 16", "max_w": "310px"},
    }
    cfg = configs.get(ratio, configs["9:16"])
    video_speed = max(0.5, min(2.0, float(video_speed or 1.0)))
    flip_x = "-1" if flip_h else "1"

    # Percentage offset calculation to stay strictly inside video box
    mask_top_pct = 50 - (mask_y / 5.0)
    sub_top_pct = 50 - (sub_y / 5.0)
    logo_top_pct = 50 - (logo_y / 5.0)

    # Mask Overlay
    mask_html = ""
    if mask_enable:
        mask_bg = hex_to_rgba(mask_color, mask_opacity)
        mask_backdrop = "backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);" if mask_type == "Blur (ဝေဝါးဖုံး)" else ""
        mask_html = f"""
        <div style="
            position: absolute;
            left: calc(50% + {mask_x}px);
            top: {mask_top_pct}%;
            transform: translate(-50%, -50%);
            width: {mask_w}%;
            height: {mask_h}px;
            background: {mask_bg};
            {mask_backdrop}
            border-radius: 6px;
            z-index: 40;
            pointer-events: none;
            box-shadow: 0 0 10px rgba(0,0,0,0.3);
        "></div>
        """

    # Logo Overlay
    logo_html = ""
    logo_file = as_filepath(logo_file)
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
                    top: {logo_top_pct}%;
                    transform: translate(-50%, -50%);
                    width: {logo_size}px;
                    height: auto;
                    z-index: 50;
                    pointer-events: none;
                "/>
                """
        except Exception:
            pass

    # Subtitle Text Preview
    if sub_lang == "English":
        sample_l1 = "A man standing on the mountain"
        sample_l2 = "( Sample English Subtitle )"
    elif sub_lang == "ไทย (Thai)":
        sample_l1 = "ชายคนหนึ่งยืนอยู่บนภูเขา"
        sample_l2 = "( ตัวอย่างซับไตเติลภาษาไทย )"
    elif sub_lang == "中文 (Chinese)":
        sample_l1 = "一个人站在高高的山顶上"
        sample_l2 = "( 示例中文字幕展示 )"
    else:
        sample_l1 = "လူတစ်ယောက် တောင်ပေါ်မှာ"
        sample_l2 = "( နမူနာ မြန်မာစာတန်းထိုး )"

    sub_html = f"""
    <div style="
        position: absolute;
        left: calc(50% + {sub_x}px);
        top: {sub_top_pct}%;
        transform: translate(-50%, -50%);
        width: 92%;
        text-align: center;
        z-index: 60;
        pointer-events: none;
    ">
        <span style="
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
        ">{sample_l1}</span>
        <span style="
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
        ">{sample_l2}</span>
    </div>
    """

    inset_x = (100 - crop_w_pct) / 2
    inset_y = (100 - crop_h_pct) / 2
    clip_style = f"clip-path: inset({inset_y:.1f}% {inset_x:.1f}% {inset_y:.1f}% {inset_x:.1f}%);"

    return f"""
    <style id="tab3-preview-master-style">
    /* Keep the HTML overlay component physically inside the preview wrapper. */
    #tab3_preview_wrapper {{
        position: relative !important;
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: 0 auto !important;
    }}
    #tab3_preview_wrapper > .gr-html,
    #tab3_preview_wrapper > .html-container,
    #tab3_preview_wrapper .gr-html-container {{
        position: absolute !important;
        inset: 0 !important;
        width: 100% !important;
        height: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        z-index: 30 !important;
        pointer-events: none !important;
    }}
    #tab3_preview_wrapper #tab3_preview_box {{
        position: relative !important;
        z-index: 5 !important;
    }}
    #tab3_preview_box {{
        width: 100% !important;
        margin: 0 auto !important;
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
        filter: brightness({bright_val}) contrast({contrast_val}) !important;
        {clip_style}
        display: block !important;
        position: relative !important;
        z-index: 5 !important;
    }}
    #tab3_inside_overlay {{
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        bottom: 0 !important;
        width: 100% !important;
        height: 100% !important;
        border-radius: 14px !important;
        overflow: hidden !important;
        pointer-events: none !important;
        z-index: 30 !important;
    }}
    </style>
    <div id="tab3_inside_overlay">
        {mask_html}
        {logo_html}
        {sub_html}
    </div>
    """

# =========================================================
# GEMINI GENERATION CORE
# =========================================================
def build_recap_prompt(selected_ratio, voice_language, video_duration=None):
    dur_guidance = ""
    if video_duration:
        dur_guidance = f"\nVideo Length: {video_duration:.1f} seconds. Keep the recap length strictly balanced."

    lang_instructions = {
        "မြန်မာ (Burmese Voice)": "မြန်မာ Movie Recap Script အဖြစ် မြန်မာစာလုံးပေါင်း သတ်ပုံတိကျစွာ သဘာဝကျကျ ရေးသားပေးပါ။",
        "English Voice": "Write an engaging, fluent English Movie Recap Script suitable for viral video narration.",
        "ไทย (Thai Voice)": "เขียนบทพากย์สรุปหนังภาษาไทย (Thai Movie Recap Script) ที่น่าสนใจ ถูกต้องตามไวยากรณ์ และกระชับชัดเจน",
        "中文 (Chinese Voice)": "写出地道、流畅的中文电影解说旁白文案，语句紧凑生动。"
    }

    instruction = lang_instructions.get(voice_language, lang_instructions["မြန်မာ (Burmese Voice)"])

    return f"""
You are an expert Professional Movie Recap Scriptwriter.
Target Aspect Ratio = {selected_ratio}{dur_guidance}
Target Narration Voice Language = {voice_language}

Instructions:
1. {instruction}
2. Accurately capture what happens in the video with fluent storytelling.
3. Do NOT include any technical tags like [Visual], [Scene], [Narrator], [Dialogue], or timestamps.
4. Keep sentences rhythmic and concise for smooth TTS synthesis.
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
        if time.time() - start_wait > 900:
            raise TimeoutError("Gemini Video Processing ကြာမြင့်လွန်းနေပါသည်။")
        time.sleep(3)
        uploaded_file = client.files.get(name=uploaded_file.name)

    v_dur = get_video_duration(target_media)
    prompt = build_recap_prompt(ratio_choice, voice_language, v_dur)
    script_text, used_model = generate_with_retry(client, uploaded_file, prompt)
    clean_text = clean_script_for_tts(script_text)
    return clean_text, used_model, msg

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
# ADVANCED FFMPEG COMPOSER (ROBUST & ERROR-FREE)
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
    ratio_choice, resolution_choice, quality_preset, video_speed, flip_h, scale_val, x_off, y_off,
    crop_w_pct, crop_h_pct, symmetric_crop,
    use_blur_bg, bg_color,
    bright_val, contrast_val,
    mask_enable, mask_type, mask_color, mask_opacity, mask_w, mask_h, mask_x, mask_y,
    logo_file, logo_size, logo_x, logo_y,
    font_family, font_size, font_color, outline_color, sub_x, sub_y,
    output_filename="final_movie_recap.mp4"
):
    ratio_dims = {
        "1:1": (1080, 1080),
        "3:4": (810, 1080),
        "16:9": (1920, 1080),
        "9:16": (1080, 1920)
    }
    base_w, base_h = ratio_dims.get(ratio_choice, (1080, 1920))
    resolution_short_edge = {
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
        "1440p": 1440,
        "Original Canvas": min(base_w, base_h),
    }.get(resolution_choice, min(base_w, base_h))
    # The selected p value is the short edge: 1080p becomes 1080x1920
    # for portrait and 1920x1080 for landscape, without distorting the ratio.
    scale_ratio = resolution_short_edge / min(base_w, base_h)
    tw = max(2, int(round(base_w * scale_ratio / 2) * 2))
    th = max(2, int(round(base_h * scale_ratio / 2) * 2))

    quality_settings = {
        "Fast (သေးငယ်သောဖိုင်)": {"preset": "veryfast", "crf": "26"},
        "Balanced (အကြံပြု)": {"preset": "medium", "crf": "23"},
        "High Quality": {"preset": "slow", "crf": "20"},
        "Best Quality (ဖိုင်ကြီး)": {"preset": "slower", "crf": "18"},
    }.get(quality_preset, {"preset": "medium", "crf": "23"})
    # Blur is one of the most expensive filters. Keep it visually smooth,
    # while using a lighter radius for the speed-oriented export preset.
    blur_radius = 14 if quality_preset == "Fast (သေးငယ်သောဖိုင်)" else 20
    blur_power = 6 if quality_preset == "Fast (သေးငယ်သောဖိုင်)" else 8

    video_speed = max(0.5, min(2.0, float(video_speed or 1.0)))
    source_tts_dur = get_video_duration(tts_audio) or 10.0
    # Speeding/slowing the video also changes narration duration so the
    # narration, subtitles, and motion remain aligned as one timeline.
    tts_dur = source_tts_dur / video_speed
    speed_filter = f"setpts=PTS/{video_speed},"
    flip_filter = "hflip," if flip_h else ""
    crop_w_expr = f"iw*{max(30, min(100, float(crop_w_pct)))/100.0:.4f}"
    crop_h_expr = f"ih*{max(30, min(100, float(crop_h_pct)))/100.0:.4f}"
    if symmetric_crop:
        crop_filter = f"crop={crop_w_expr}:{crop_h_expr}:(iw-{crop_w_expr})/2:(ih-{crop_h_expr})/2,"
    else:
        crop_filter = f"crop={crop_w_expr}:{crop_h_expr}:0:0,"
    color_filter = f"eq=brightness={bright_val - 1.0:.2f}:contrast={contrast_val:.2f}"
    bg_clean = bg_color.lstrip("#")

    filter_chains = []

    # Build the canvas background from the ORIGINAL frame, not the cropped
    # foreground. This prevents white/empty borders when crop_w/crop_h < 100.
    if use_blur_bg:
        filter_chains.append(
            f"[0:v]split=2[bg_src][fg_src];"
            f"[bg_src]{speed_filter}{flip_filter}{color_filter},scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},boxblur={blur_radius}:{blur_power},eq=brightness=-0.15[bg_blurred];"
            f"[fg_src]{speed_filter}{flip_filter}{crop_filter}{color_filter},"
            f"scale=iw*{scale_val}:ih*{scale_val}:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blurred][fg_scaled]overlay=(W-w)/2+({x_off}):(H-h)/2+({y_off})[v_base]"
        )
    else:
        filter_chains.append(
            f"[0:v]{speed_filter}{flip_filter}{crop_filter}{color_filter},"
            f"scale=iw*{scale_val}:ih*{scale_val}:force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:(ow-iw)/2+({x_off}):(oh-ih)/2+({y_off}):color=0x{bg_clean}[v_base]"
        )

    current_v = "v_base"

    # Mask to cover original subtitles (Percentage matching Preview calculation)
    if mask_enable:
        mw = int(tw * (mask_w / 100.0))
        mh = int(mask_h * (th / 1920.0 * 2.2))
        mx = f"(W-{mw})/2+({mask_x})"
        my = f"(H*0.5)-({mask_y}*(H/1000))-({mh}/2)"

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

    # Logo Overlay
    inputs_cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", source_video, "-i", tts_audio]
    next_input_idx = 2

    if logo_file and os.path.exists(logo_file):
        inputs_cmd.extend(["-i", logo_file])
        logo_idx = next_input_idx
        next_input_idx += 1
        lx = f"(W-w)/2+({logo_x})"
        ly = f"(H*0.5)-({logo_y}*(H/1000))-(h/2)"
        filter_chains.append(
            f"[{logo_idx}:v]scale={logo_size}:-1[scaled_logo];"
            f"[{current_v}][scaled_logo]overlay={lx}:{ly}[v_logoed]"
        )
        current_v = "v_logoed"

    # Subtitles Positioning
    primary_ass = hex_to_ass_color(font_color)
    outline_ass = hex_to_ass_color(outline_color)
    escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    calc_margin_v = max(10, int((th / 2) - (sub_y * (th / 1000))))
    sub_style = (
        f"subtitles='{escaped_srt}':force_style="
        f"'FontName={font_family},FontSize={font_size},"
        f"PrimaryColour={primary_ass},OutlineColour={outline_ass},"
        f"BorderStyle=3,Outline=3,Shadow=2,Alignment=2,MarginV={calc_margin_v}'"
    )
    filter_chains.append(f"[{current_v}]{sub_style}[vout]")

    # Audio Mixing Configuration
    audio_inputs_count = 2
    if bgm_audio and os.path.exists(bgm_audio):
        inputs_cmd.extend(["-stream_loop", "-1", "-i", bgm_audio])
        bgm_idx = next_input_idx
        next_input_idx += 1
        audio_inputs_count += 1

    orig_has_audio = has_audio_stream(source_video)

    audio_filters = []
    if enable_orig_audio and orig_has_audio:
        audio_filters.append(f"[0:a]atempo={video_speed:.3f},volume=0.2[orig_a];")
    else:
        # Keep the silent track alive for the complete narration duration;
        # otherwise amix(duration=first) can cut the output to one second.
        audio_filters.append(f"aevalsrc=0:d={tts_dur:.3f}[orig_a];")

    audio_filters.append(f"[1:a]atempo={video_speed:.3f},volume=1.0[tts_a];")

    if audio_inputs_count == 3:
        audio_filters.append(f"[{bgm_idx}:a]volume={bgm_volume}[bgm_a];")
        audio_filters.append("[orig_a][tts_a][bgm_a]amix=inputs=3:duration=longest:dropout_transition=2[aout]")
    else:
        audio_filters.append("[orig_a][tts_a]amix=inputs=2:duration=longest:dropout_transition=2[aout]")

    full_filter_complex = ";".join(filter_chains) + ";" + "".join(audio_filters)
    cmd = inputs_cmd + [
        "-filter_complex", full_filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(tts_dur),
        # Let FFmpeg use all available CPU cores and parallelize the filter graph.
        "-threads", "0", "-filter_threads", "0", "-filter_complex_threads", "0",
        "-c:v", "libx264", "-preset", quality_settings["preset"], "-crf", quality_settings["crf"], "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-threads", "0",
        # Makes the exported MP4 start playing while it is downloading.
        "-movflags", "+faststart", "-map_metadata", "-1",
        output_filename
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as e:
        print("FFmpeg Render Fallback:", e.stderr)
        fallback_vf = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2"
        fb_cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", source_video, "-i", tts_audio,
            "-vf", fallback_vf,
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
    target = as_filepath(v_file) or download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return "", "", "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ထည့်ပါ။", None, None
    try:
        clean_text, model, dur_msg = run_gemini_video_analysis(target, ratio, "မြန်မာ (Burmese Voice)")
        srt, zip_f = generate_srt_and_zip(clean_text)
        status = f"✅ Script ရေးသားပြီးပါပြီ! (Model: {model})\n{dur_msg}"
        return clean_text, clean_text, status, srt, zip_f
    except Exception as e:
        return "", "", f"❌ Error: {safe_error(e)}", None, None

def handle_direct_translate(text):
    if not text or not text.strip():
        return "", "⚠️ ဘာသာပြန်ရန် စာသားထည့်ပေးပါ။"
    try:
        translated = translate_to_target_language(text, "Burmese (မြန်မာ)")
        return translated, "✅ မြန်မာဘာသာသို့ အောင်မြင်စွာ ပြန်ဆိုပြီးပါပြီ။"
    except Exception as e:
        return text, f"❌ Translation Error: {safe_error(e)}"

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
        return current_text, None, None, None, None, f"❌ Error: {safe_error(e)}"

def update_voice_choices(voice_lang):
    voices = list(VOICES_BY_LANG[voice_lang].keys())
    return gr.Dropdown(choices=voices, value=voices[0])

def tab3_auto_pipeline(
    v_file, v_url, bgm_file,
    voice_lang, voice_label, speed,
    sub_lang,
    enable_orig_audio, bgm_vol,
    ratio, resolution_choice, quality_preset, video_speed, flip_h, scale_val, x_off, y_off,
    crop_w, crop_h,
    use_blur_bg, bg_color,
    bright_val, contrast_val,
    mask_enable, mask_type, mask_color, mask_opacity, mask_w, mask_h, mask_x, mask_y,
    logo_file, logo_size, logo_x, logo_y,
    font_family, font_size, font_color, outline_color, sub_x, sub_y
):
    target = as_filepath(v_file) or download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return None, None, "", None, "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ကို စစ်ဆေးပေးပါ။"

    try:
        # ၁။ Script ရေးသားခြင်း
        narration_script, model, dur_msg = run_gemini_video_analysis(target, ratio, voice_lang)
        
        # ၂။ စာတန်းထိုးအတွက် ဘာသာပြန်ခြင်း
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

        # ၃။ အသံဖိုင်နှင့် စာတန်းထိုး ဖန်တီးခြင်း
        voice_code = VOICES_BY_LANG[voice_lang].get(voice_label, list(VOICES_BY_LANG[voice_lang].values())[0])
        audio_file = asyncio.run(
            generate_tts_file(narration_script, voice_code, speed, "tab3_voice.mp3")
        )

        audio_dur = (get_video_duration(audio_file) or 10.0) / max(0.5, min(2.0, float(video_speed or 1.0)))
        srt_file, _ = generate_srt_and_zip(subtitle_script, total_target_duration=audio_dur, prefix="tab3_sub")

        # ၄။ Scene-aware preprocessing: remove long frozen/duplicate-looking
        # intervals and join the remaining A/V segments smoothly. If FFmpeg
        # detection fails, the helper safely returns the original source.
        render_source = prepare_scene_aware_video(target, "scene_aware_source.mp4")

        # ၅။ Video Render ပြုလုပ်ခြင်း
        final_video = render_advanced_clip(
            source_video=render_source,
            tts_audio=audio_file,
            srt_path=srt_file,
            bgm_audio=bgm_file,
            enable_orig_audio=enable_orig_audio,
            bgm_volume=bgm_vol,
            ratio_choice=ratio,
            resolution_choice=resolution_choice,
            quality_preset=quality_preset,
            video_speed=video_speed,
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
        return None, None, "", None, f"❌ Error ဖြစ်ပေါ်ပါသည်: {safe_error(e)}"

# =========================================================
# GRADIO UI
# =========================================================
with gr.Blocks(title=APP_TITLE) as demo:
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
                    v2_voice = gr.Dropdown(list(VOICES_BY_LANG["မြန်မာ (Burmese Voice)"].keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎤 အသံ ရွေးချယ်ပါ")
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
                # LEFT COLUMN: SETTINGS
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
                        t3_font = gr.Dropdown(["Pyidaungsu", "Padauk", "Myanmar Text", "Arial", "sans-serif"], value="Pyidaungsu", label="🔤 Subtitle Font ဒီဇိုင်း")
                        t3_fsize = gr.Slider(10, 100, value=22, step=1, label="📏 Subtitle Font အရွယ်အစား (Preview + Final)")
                        with gr.Row():
                            t3_fcolor = gr.ColorPicker(label="🎨 Subtitle စာလုံးအရောင်", value="#00E676")
                            t3_ocolor = gr.ColorPicker(label="🖌️ Subtitle Outline အရောင်", value="#000000")
                        with gr.Row():
                            t3_sub_x = gr.Slider(-500, 500, value=0, step=5, label="↔️ စာတန်းထိုး ဘယ်/ညာ ရွှေ့မည်")
                            t3_sub_y = gr.Slider(-450, 450, value=-260, step=5, label="↕️ စာတန်းထိုး အပေါ်/အောက် ရွှေ့မည် (အဆုံးထိရွှေ့နိုင်)")

                    with gr.Accordion("✂️ Video Crop (အတိုး/အလျော့ဖြတ်တောက်မှု) & ပုံစံ", open=True):
                        t3_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="9:16", label="📐 Aspect Ratio ရွေးပါ")
                        with gr.Row():
                            t3_resolution = gr.Dropdown(
                                ["480p", "720p", "1080p", "1440p", "Original Canvas"],
                                value="1080p", label="🖥️ Video Resolution"
                            )
                            t3_quality = gr.Dropdown(
                                ["Fast (သေးငယ်သောဖိုင်)", "Balanced (အကြံပြု)", "High Quality", "Best Quality (ဖိုင်ကြီး)"],
                                value="Balanced (အကြံပြု)", label="🎞️ Video Quality"
                            )
                        t3_video_speed = gr.Slider(
                            0.5, 2.0, value=1.0, step=0.05,
                            label="⏩ Video Speed (0.5× အနှေး — 2.0× အမြန်)"
                        )
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

                    with gr.Accordion("🛡️ မူရင်းစာတန်းထိုး ဖုံးအုပ်မည့် Mask (အပေါ်အောက် အဆုံးထိရွှေ့နိုင်သည်)", open=True):
                        t3_mask_enable = gr.Checkbox(label="✅ စာတန်းထိုး ဖုံးအုပ်မည့် Mask ဖွင့်မည်", value=True)
                        t3_mask_type = gr.Radio(["Blur (ဝေဝါးဖုံး)", "Color Box (အရောင်အတုံးဖြင့်ဖုံး)"], value="Blur (ဝေဝါးဖုံး)", label="🎭 Mask ပုံစံ")
                        with gr.Row():
                            t3_mask_color = gr.ColorPicker(label="🎨 Mask အရောင်", value="#000000")
                            t3_mask_opacity = gr.Slider(0.0, 1.0, value=0.85, step=0.05, label="💧 Opacity (အရောင် အတိုး/အလျှော့)")
                        with gr.Row():
                            t3_mask_w = gr.Slider(10, 100, value=85, step=1, label="↔️ Mask အကျယ် (%)")
                            t3_mask_h = gr.Slider(10, 300, value=85, step=2, label="↕️ Mask အမြင့် (px)")
                        with gr.Row():
                            t3_mask_x = gr.Slider(-500, 500, value=0, step=5, label="↔️ Mask ဘယ်/ညာ ရွှေ့မည်")
                            t3_mask_y = gr.Slider(-450, 450, value=-260, step=5, label="↕️ Mask အပေါ်/အောက် ရွှေ့မည် (အဆုံးထိရွှေ့နိုင်)")

                    with gr.Accordion("🏷️ Logo တံဆိပ် ထည့်သွင်းခြင်း", open=False):
                        t3_logo_file = gr.Image(label="🖼️ Logo ပုံတင်ရန် (PNG / JPG)", type="filepath")
                        t3_logo_size = gr.Slider(30, 400, value=100, step=5, label="📏 Logo အရွယ်အစား (px)")
                        with gr.Row():
                            t3_logo_x = gr.Slider(-500, 500, value=0, step=5, label="↔️ Logo ဘယ်/ညာ ရွှေ့မည်")
                            t3_logo_y = gr.Slider(-450, 450, value=250, step=5, label="↕️ Logo အပေါ်/အောက် ရွှေ့မည်")

                    t3_run_btn = gr.Button("✨ Video အပြီးစီး One-Click ထုတ်လုပ်မည်", variant="primary", size="lg")

                # RIGHT COLUMN: REAL-TIME IN-VIDEO PREVIEW & FINAL VIDEO
                with gr.Column(scale=1):
                    # Wrapper Div to perfectly lock overlay inside Video Preview
                    with gr.Group(elem_id="tab3_preview_wrapper"):
                        t3_live_video = gr.Video(label="📺 Real-Time Preview (Preview ဘောင်အတွင်း ကွက်တိပြသမှု)", elem_id="tab3_preview_box")
                        t3_preview_css = gr.HTML(
                            get_tab3_in_video_preview_html(
                                "မြန်မာ (Burmese)",
                                "9:16", 1.0, False, 1.0, 0, 0,
                                100, 100,
                                True, "#000000",
                                1.0, 1.0,
                                True, "Blur (ဝေဝါးဖုံး)", "#000000", 0.85, 85, 85, 0, -260,
                                None, 100, 0, 250,
                                "Pyidaungsu", 22, "#00E676", "#000000", 0, -260
                            )
                        )
                    t3_status = gr.Markdown("စာတန်းထိုးနှင့် Mask များကို Preview Video ဘောင်အတွင်း အပေါ်အောက် အဆုံးထိ စိတ်ကြိုက်ဆွဲရွှေ့နိုင်ပါသည်။")
                    t3_final_video = gr.Video(label="🎬 အပြီးစီး Final Video Output (Playable)")
                    t3_script_view = gr.Textbox(label="📝 ထွက်ရှိလာသော စာတန်းထိုး Script", lines=5)
                    with gr.Row():
                        t3_download = gr.File(label="📥 Final Video ဒေါင်းလုဒ်")
                        t3_srt = gr.File(label="📄 SRT စာတန်းထိုး ဒေါင်းလုဒ်")

    # ================= EVENT BINDINGS =================
    # Tab 1
    v1_file.change(lambda f: as_filepath(f), inputs=v1_file, outputs=v1_preview)
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

    # Tab 3 - Voice Selection
    t3_voice_lang.change(update_voice_choices, inputs=t3_voice_lang, outputs=t3_voice)

    # Tab 3 - Real-Time Preview Updates (Preserves Video, Never hides it)
    t3_file.change(lambda f: as_filepath(f), inputs=t3_file, outputs=t3_live_video)
    t3_load_btn.click(download_video_from_link, inputs=t3_url, outputs=t3_live_video)

    preview_all_inputs = [
        t3_sub_lang,
        t3_ratio, t3_video_speed, t3_flip, t3_scale, t3_x_off, t3_y_off,
        t3_crop_w, t3_crop_h,
        t3_blur_bg, t3_bgcolor,
        t3_bright, t3_contrast,
        t3_mask_enable, t3_mask_type, t3_mask_color, t3_mask_opacity, t3_mask_w, t3_mask_h, t3_mask_x, t3_mask_y,
        t3_logo_file, t3_logo_size, t3_logo_x, t3_logo_y,
        t3_font, t3_fsize, t3_fcolor, t3_ocolor, t3_sub_x, t3_sub_y
    ]
    for comp in preview_all_inputs:
        if comp is t3_video_speed:
            comp.change(
                get_tab3_in_video_preview_html,
                inputs=preview_all_inputs,
                outputs=t3_preview_css,
                js="(...args) => { const v = document.querySelector('#tab3_preview_box video'); if (v) v.playbackRate = Number(args[2] || 1); return args; }"
            )
        else:
            comp.change(get_tab3_in_video_preview_html, inputs=preview_all_inputs, outputs=t3_preview_css)

    # Tab 3 - Generate Final One-Clip Video
    t3_run_btn.click(
        tab3_auto_pipeline,
        inputs=[
            t3_file, t3_url, t3_bgm_file,
            t3_voice_lang, t3_voice, t3_speed,
            t3_sub_lang,
            t3_orig_audio, t3_bgm_vol,
            t3_ratio, t3_resolution, t3_quality, t3_video_speed, t3_flip, t3_scale, t3_x_off, t3_y_off,
            t3_crop_w, t3_crop_h,
            t3_blur_bg, t3_bgcolor,
            t3_bright, t3_contrast,
            t3_mask_enable, t3_mask_type, t3_mask_color, t3_mask_opacity, t3_mask_w, t3_mask_h, t3_mask_x, t3_mask_y,
            t3_logo_file, t3_logo_size, t3_logo_x, t3_logo_y,
            t3_font, t3_fsize, t3_fcolor, t3_ocolor, t3_sub_x, t3_sub_y
        ],
        outputs=[t3_final_video, t3_download, t3_script_view, t3_srt, t3_status]
    )

# Render Server Launch Port
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())
