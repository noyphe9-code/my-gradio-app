import os
import re
import time
import zipfile
import subprocess
import asyncio
import html
import json
import gradio as gr
import edge_tts
import yt_dlp
from google import genai
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps, ImageFilter, ImageStat

# =========================================================
# CONFIGURATION & SETTINGS
# =========================================================
APP_TITLE = "AI Movie Recap Studio Pro"
MAX_VIDEO_MINUTES = 10
MAX_AI_UPLOAD_MB = 80
SAVED_API_KEY = ""

GEMINI_MODELS = [
    # The API error says 2.5-flash-lite is unavailable to new users.
    # Use the current model recommended by the API response first.
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
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

def has_audio_stream(media_path):
    """Video ထဲတွင် မူရင်း audio stream ရှိ/မရှိ စစ်သည်။"""
    if not media_path or not os.path.exists(media_path):
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", media_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception as exc:
        print("Audio Stream Error:", exc)
        return False

def validate_video_duration(video_path):
    duration = get_video_duration(video_path)
    if duration is None:
        return True, "ℹ️ Video duration ကို စစ်ဆေးနေပါသည်..."
    minutes = duration / 60
    if minutes > MAX_VIDEO_MINUTES:
        return False, f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ လက်ခံပါသည်။"
    return True, f"✅ Video Length: {minutes:.1f} မိနစ်"

def prepare_video_for_ai(video_path):
    """AI upload အတွက် ဖိုင်ကြီးများကို 720p proxy အဖြစ် ချုံ့ပေးသည်။"""
    if not video_path or not os.path.exists(video_path):
        return video_path
    try:
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if size_mb <= MAX_AI_UPLOAD_MB:
            return video_path
        proxy_path = os.path.abspath(
            f"ai_proxy_{os.path.splitext(os.path.basename(video_path))[0]}.mp4"
        )
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vf", "scale='min(1280,iw)':-2:force_original_aspect_ratio=decrease",
             "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", proxy_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=900
        )
        if result.returncode == 0 and os.path.exists(proxy_path):
            return proxy_path
    except Exception as exc:
        print("AI Proxy Error:", exc)
    return video_path

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
        line = re.sub(
            r"^\s*(?:ဇာတ်ကြောင်း|ဇာတ်ကောင်စကားပြော|တုံ့ပြန်မှု|နိဒါန်း|အဆုံးသတ်|ဇာတ်လမ်း|Narration|Dialogue|Reaction)\s*[:：\-]?\s*",
            "", line, flags=re.IGNORECASE
        )
        # Remove a leading speaker label such as “Name:” while keeping the
        # spoken sentence itself. This is intentionally limited to short
        # labels so normal Burmese punctuation is not disturbed.
        line = re.sub(r"^\s*[A-Za-z0-9\u1000-\u109F][A-Za-z0-9\u1000-\u109F .'-]{0,28}\s*[:：]\s*", "", line)
        if line.lower() in ["movie recap", "recap script", "burmese recap script", "script"] or line.startswith("---"):
            continue
        if line.strip():
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

def generate_synced_srt_and_zip(script_text, media_path, prefix="tab3_synced"):
    """စာကြောင်းများကို Narrator/Audio duration အလိုက် proportionally ခွဲပြီး SRT ထုတ်သည်။"""
    clean_text = clean_script_for_tts(script_text)
    duration = get_video_duration(media_path) if media_path else None
    if not clean_text or not duration or duration <= 0:
        return generate_srt_and_zip(clean_text, prefix=prefix)
    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    weights = [max(1, len(line)) for line in lines]
    total_weight = sum(weights) or 1
    current = 0.0
    srt_content = []
    for index, (line, weight) in enumerate(zip(lines, weights), 1):
        start = current
        end = duration if index == len(lines) else current + (duration * weight / total_weight)
        srt_content.append(
            f"{index}\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n{line}\n"
        )
        current = end
    srt_filename = f"{prefix}_subtitle.srt"
    zip_filename = f"{prefix}_subtitle.zip"
    with open(srt_filename, "w", encoding="utf-8-sig") as file:
        file.write("\n".join(srt_content))
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(srt_filename, arcname=srt_filename)
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

def thumbnail_metadata(text):
    """Thumbnail title/copy နှင့် social metadata ကို Gemini ဖြင့် ထုတ်သည်။"""
    if not SAVED_API_KEY:
        return {
            "thumb_text": "မယုံနိုင်စရာ ဇာတ်လမ်း",
            "title": "မယုံနိုင်စရာ ဇာတ်လမ်းတစ်ပုဒ်",
            "description": "ဒီဗီဒီယိုထဲက စိတ်ဝင်စားစရာအဖြစ်အပျက်ကို အဆုံးထိကြည့်လိုက်ပါ။",
            "hashtags": ["#MovieRecap", "#မြန်မာ", "#ဇာတ်လမ်း", "#TikTokMyanmar", "#Viral"],
            "tags": ["movie recap", "မြန်မာဇာတ်လမ်း", "movie summary", "ဗီဒီယို", "ဇာတ်ကား", "အံ့သြဖွယ်", "suspense", "viral video", "tiktok", "youtube"],
        }
    prompt = f"""
Analyze this Burmese movie recap script and return ONLY valid JSON with exactly these keys:
thumb_text, title, description, hashtags, tags.
Write natural, catchy Burmese for TikTok, YouTube, and Facebook.
thumb_text: 3-8 Burmese words, highly clickable but truthful.
title: one compelling Burmese title.
description: 2-3 short sentences.
hashtags: exactly 5 hashtag strings.
tags: exactly 10 short tag strings.
Do not include markdown or explanations.

SCRIPT:
{text[:12000]}
"""
    client = genai.Client(api_key=SAVED_API_KEY)
    last_error = None
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            raw = (response.text or "").strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
            data["hashtags"] = list(data.get("hashtags", []))[:5]
            data["tags"] = list(data.get("tags", []))[:10]
            return data
        except Exception as exc:
            last_error = exc
    print("Thumbnail metadata error:", last_error)
    return {
        "thumb_text": "စိတ်ဝင်စားဖွယ် ဇာတ်လမ်း",
        "title": "စိတ်ဝင်စားဖွယ် ဇာတ်လမ်းတစ်ပုဒ်",
        "description": "ဒီဇာတ်လမ်းထဲက အဖြစ်အပျက်ကို အဆုံးထိ ကြည့်ရှုလိုက်ပါ။",
        "hashtags": ["#MovieRecap", "#မြန်မာ", "#ဇာတ်လမ်း", "#TikTokMyanmar", "#Viral"],
        "tags": ["movie recap", "မြန်မာဇာတ်လမ်း", "movie summary", "ဇာတ်ကား", "suspense", "viral", "tiktok", "youtube", "facebook", "မြန်မာ"],
    }

def _font_path():
    try:
        path = subprocess.check_output(
            ["fc-match", "-f", "%{file}", "Noto Sans Myanmar"], text=True
        ).strip()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

def _wrap_thumbnail_text(draw, text, font, max_width):
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [str(text or "").strip()]

def create_thumbnail_from_video(video_path, script_text, ratio, resolution):
    if not video_path or not os.path.exists(video_path):
        raise ValueError("Thumbnail ထုတ်ရန် Video File တင်ပေးပါ။")
    sizes = {
        "1:1": (1, 1), "3:4": (3, 4), "16:9": (16, 9), "9:16": (9, 16)
    }
    scale_map = {"1080p": 1080, "2K": 1440, "4K": 2160, "8K": 4320}
    rw, rh = sizes.get(ratio, sizes["16:9"])
    short_side = scale_map.get(resolution, 1080)
    if ratio == "16:9": width, height = round(short_side * 16 / 9), short_side
    elif ratio == "9:16": width, height = short_side, round(short_side * 16 / 9)
    elif ratio == "3:4": width, height = round(short_side * 3 / 4), short_side
    else: width, height = short_side, short_side
    duration = get_video_duration(video_path) or 1
    temp_frames = []
    best_frame, best_score = None, -1
    for index, fraction in enumerate((0.18, 0.38, 0.58, 0.78, 0.90)):
        frame_path = os.path.abspath(f"thumb_frame_{int(time.time())}_{index}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(duration * fraction), "-i", video_path,
             "-frames:v", "1", "-vf", "scale=1280:-2", frame_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120
        )
        if os.path.exists(frame_path):
            temp_frames.append(frame_path)
            image = Image.open(frame_path).convert("RGB")
            edge_score = ImageStat.Stat(image.filter(ImageFilter.FIND_EDGES).convert("L")).var[0]
            color_score = ImageStat.Stat(ImageEnhance.Color(image).enhance(1.5)).mean
            score = edge_score + sum(color_score) * 0.15
            if score > best_score:
                best_score, best_frame = score, image.copy()
    for frame_path in temp_frames:
        try: os.remove(frame_path)
        except OSError: pass
    if best_frame is None:
        raise RuntimeError("Video ထဲမှ thumbnail frame မထုတ်နိုင်ပါ။")

    metadata = thumbnail_metadata(script_text or "")
    canvas = ImageOps.fit(best_frame, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
    canvas = ImageEnhance.Color(canvas).enhance(1.35)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.12)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for y in range(height):
        alpha = int(170 * (y / max(1, height)))
        odraw.line((0, y, width, y), fill=(5, 8, 25, alpha))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)
    font_path = _font_path()
    title_font = ImageFont.truetype(font_path, max(32, int(min(width, height) * 0.085)))
    small_font = ImageFont.truetype(font_path, max(22, int(min(width, height) * 0.035)))
    lines = _wrap_thumbnail_text(draw, metadata.get("thumb_text", "မယုံနိုင်စရာ ဇာတ်လမ်း"), title_font, int(width * 0.86))
    line_heights = [draw.textbbox((0, 0), line, font=title_font)[3] for line in lines]
    total_h = sum(line_heights) + (len(lines) - 1) * 12
    y = int(height * 0.72 - total_h / 2)
    for line, line_h in zip(lines, line_heights):
        box = draw.textbbox((0, 0), line, font=title_font, stroke_width=4)
        x = (width - (box[2] - box[0])) // 2
        draw.text((x + 4, y + 6), line, font=title_font, fill=(0, 0, 0, 210), stroke_width=6, stroke_fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=title_font, fill=(255, 239, 65, 255), stroke_width=2, stroke_fill=(225, 35, 65, 255))
        y += line_h + 12
    badge = "AI MOVIE RECAP"
    draw.rounded_rectangle((int(width * .04), int(height * .04), int(width * .42), int(height * .11)), radius=18, fill=(235, 42, 80, 235))
    draw.text((int(width * .06), int(height * .055)), badge, font=small_font, fill=(255, 255, 255, 255))
    out_path = os.path.abspath(f"thumbnail_{ratio.replace(':','x')}_{resolution}_{int(time.time())}.jpg")
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0, optimize=True)
    return out_path, metadata

def tab4_thumbnail(video_file, script_text, ratio, resolution):
    try:
        path, metadata = create_thumbnail_from_video(video_file, script_text, ratio, resolution)
        hashtags = " ".join(metadata.get("hashtags", [])[:5])
        tags = ", ".join(metadata.get("tags", [])[:10])
        info = f"**Title:** {metadata.get('title','')}\n\n**Description:** {metadata.get('description','')}\n\n**Hashtags (5):** {hashtags}\n\n**Tags (10):** {tags}"
        return path, info
    except Exception as exc:
        return None, f"❌ Thumbnail Error: {exc}"

def render_tab3_video(video_path, fallback_video_path, audio_mode, audio_file,
                      original_volume, audio_volume, zoom, brightness, contrast,
                      tts_text, tts_voice, tts_speed, output_resolution, ratio):
    """Tab 3 အတွက် အသံနှင့် video effect များကို ffmpeg ဖြင့် output video အဖြစ်ထုတ်ပေးသည်။"""
    video_path = video_path or fallback_video_path
    if not video_path or not os.path.exists(video_path):
        return None, "⚠️ အရင်ဆုံး Tab 3 တွင် Video File တင်ပါ။", None, None
    try:
        original_volume = max(0.0, min(2.0, float(original_volume)))
        audio_volume = max(0.0, min(2.0, float(audio_volume)))
        zoom = max(1.0, min(2.0, float(zoom)))
        brightness = max(-1.0, min(1.0, float(brightness)))
        contrast = max(0.0, min(3.0, float(contrast)))
    except (TypeError, ValueError):
        return None, "❌ Audio/Video setting တန်ဖိုး မမှန်ပါ။", None, None

    auto_narration = audio_mode in ("🎙️ Tab 3 Thiha/Nilar Voice", "🎙️ Narrator + Original Video")
    if auto_narration and (not tts_text or tts_text.strip() in ("(နမူနာစာ)", "နမူနာစာ")):
        try:
            tts_text, _, _ = run_gemini_video_analysis(video_path, ratio)
        except Exception as exc:
            return None, f"❌ Video ဇာတ်လမ်းကို AI နားလည်ပြီး Script မထုတ်နိုင်ပါ: {exc}", None, None
    selected_audio = audio_file
    # Both narrator modes must generate the Myanmar TTS track automatically.
    if audio_mode in ("🎙️ Tab 3 Thiha/Nilar Voice", "🎙️ Narrator + Original Video"):
        tts_text = clean_script_for_tts(tts_text) or "(နမူနာစာ)"
        tts_output = os.path.abspath(f"tab3_tts_{int(time.time())}.mp3")
        try:
            selected_audio, _, _ = asyncio.run(
                generate_myanmar_tts(tts_text, tts_voice, tts_speed, tts_output)
            )
        except Exception as exc:
            return None, f"❌ Thiha/Nilar Voice မထုတ်နိုင်ပါ: {exc}", None, None
    if audio_mode == "🎙️ Tab 2 Voice (tab2_output.mp3)" and not selected_audio:
        selected_audio = "tab2_output.mp3"
    if audio_mode == "🎵 MP3 BGM" and not selected_audio:
        return None, "⚠️ MP3 BGM ဖိုင်တင်ပေးပါ။", None, None
    if selected_audio and not os.path.exists(selected_audio):
        return None, "⚠️ ရွေးထားသော Audio ဖိုင်ကို မတွေ့ပါ။", None, None

    subtitle_file = None
    if auto_narration and tts_text:
        subtitle_file, _ = generate_synced_srt_and_zip(
            tts_text, selected_audio, prefix=f"tab3_burnin_{int(time.time())}"
        )

    # Encode the selected ratio into the final file, not only into the preview.
    base_heights = {"480p": 480, "720p": 720, "1080p": 1080}
    base_height = base_heights.get(output_resolution, 720)
    ratio_sizes = {
        "16:9": (round(base_height * 16 / 9), base_height),
        "9:16": (base_height, round(base_height * 16 / 9)),
        "3:4": (round(base_height * 3 / 4), base_height),
        "1:1": (base_height, base_height),
    }
    width, height = ratio_sizes.get(ratio, ratio_sizes["16:9"])
    width, height = int(width // 2 * 2), int(height // 2 * 2)
    output_path = os.path.abspath(f"tab3_rendered_{output_resolution or '720p'}_{int(time.time())}.mp4")
    video_filter = (
        # Scale-to-cover and centre-crop preserve content without stretching.
        f"scale=ceil({width}*{zoom}/2)*2:ceil({height}*{zoom}/2)*2:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(in_w-out_w)/2:(in_h-out_h)/2,"
        f"eq=brightness={brightness}:contrast={contrast}"
    )
    if subtitle_file and os.path.exists(subtitle_file):
        subtitle_path = subtitle_file.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        video_filter += f",subtitles='{subtitle_path}'"
    cmd = ["ffmpeg", "-y", "-i", video_path]
    original_has_audio = has_audio_stream(video_path)
    mix_with_original = audio_mode in (
        "🎵 Original 
