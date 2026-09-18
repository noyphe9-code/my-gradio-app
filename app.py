# ============================================================
# AI Movie Recap Studio Pro
# TAB 1: Video Analysis & Script
# TAB 2: Myanmar / Multi-language Text-to-Speech
# TAB 3: One-Click Video Studio
#
# IMPORTANT COMPATIBILITY FIX:
# - Uploads use gr.File(type="filepath")
# - gr.Video() is used ONLY for preview/output
# - Do NOT use gr.Video(type="filepath")
#
# Features:
# - Fast local upload -> instant preview
# - YouTube/TikTok/etc URL download
# - Gemini video analysis with processing-state wait
# - Edge TTS
# - 1:1 / 3:4 / 16:9 / 9:16
# - Crop / zoom / X-Y movement / brightness / contrast
# - Blur or solid-color background
# - Original subtitle cover: blur or color
# - Cover opacity / blur strength / width / height
# - Interactive cover position preview with drag
# - Auto subtitles from generated/script text, max 2 lines
# - Subtitle color / size / position
# - Original audio / BGM / voice volume
# - Video loop/trim to voice duration
# - 480p / 720p / 1080p export
# ============================================================

import os
import re
import uuid
import shutil
import subprocess
import asyncio
import html
import time
from pathlib import Path
from urllib.parse import quote

import gradio as gr
import edge_tts
import yt_dlp

try:
    from google import genai
except Exception:
    genai = None


# ============================================================
# CONFIG
# ============================================================

APP_TITLE = "🎬 AI Movie Recap Studio Pro"

VIDEO_TYPES = [
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".m4v", ".mpeg", ".mpg"
]

AUDIO_TYPES = [
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"
]

OUTPUT_DIR = Path("outputs")
TEMP_DIR = Path("temp")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
ENV_API_KEY = os.getenv("GEMINI_API_KEY", "")

RATIO_DIMS = {
    "1:1": (1080, 1080),
    "3:4": (810, 1080),
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}

EXPORT_DIMS = {
    "480p": {
        "1:1": (480, 480),
        "3:4": (360, 480),
        "16:9": (854, 480),
        "9:16": (480, 854),
    },
    "720p": {
        "1:1": (720, 720),
        "3:4": (540, 720),
        "16:9": (1280, 720),
        "9:16": (720, 1280),
    },
    "1080p": {
        "1:1": (1080, 1080),
        "3:4": (810, 1080),
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
    },
}

VOICE_MAP = {
    "Myanmar Female — Nilar": "my-MM-NilarNeural",
    "Myanmar Male — Thiha": "my-MM-ThihaNeural",
    "English Female — Jenny": "en-US-JennyNeural",
    "English Male — Guy": "en-US-GuyNeural",
    "Japanese Female — Nanami": "ja-JP-NanamiNeural",
    "Japanese Male — Keita": "ja-JP-KeitaNeural",
    "Korean Female — SunHi": "ko-KR-SunHiNeural",
    "Korean Male — InJoon": "ko-KR-InJoonNeural",
    "Chinese Female — Xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "Chinese Male — Yunxi": "zh-CN-YunxiNeural",
}

COLOR_PRESETS = {
    "Black": "#000000",
    "White": "#FFFFFF",
    "Gray": "#808080",
    "Dark Gray": "#202020",
    "Red": "#FF0000",
    "Blue": "#0066FF",
    "Green": "#00AA44",
    "Yellow": "#FFD400",
    "Orange": "#FF7A00",
    "Purple": "#7A2CFF",
    "Pink": "#FF2D8D",
    "Cyan": "#00D9FF",
}


# ============================================================
# GENERIC HELPERS
# ============================================================

def safe_name(name: str) -> str:
    name = os.path.basename(str(name or ""))
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
    return name[:180] or "file"


def normalize_path(value):
    """Accept Gradio filepath values across common versions."""
    if value is None:
        return None

    if isinstance(value, (str, Path)):
        return str(value)

    if isinstance(value, list):
        if not value:
            return None
        return normalize_path(value[0])

    if isinstance(value, dict):
        for key in ("path", "name", "filepath"):
            if value.get(key):
                return str(value[key])

    return None


def run_cmd(cmd, timeout=None):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def ffmpeg_exists():
    return shutil.which("ffmpeg") is not None


def ffprobe_exists():
    return shutil.which("ffprobe") is not None


def get_duration(path):
    path = normalize_path(path)
    if not path or not os.path.exists(path) or not ffprobe_exists():
        return 0.0
    try:
        r = run_cmd([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ], timeout=30)
        if r.returncode == 0:
            return max(0.0, float(r.stdout.strip()))
    except Exception:
        pass
    return 0.0


def format_duration(seconds):
    try:
        seconds = int(float(seconds))
    except Exception:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def validate_video(path):
    path = normalize_path(path)
    if not path:
        return False, "❌ Video file မရွေးရသေးပါ။"
    if not os.path.exists(path):
        return False, "❌ Video file ကို မတွေ့ပါ။"
    if Path(path).suffix.lower() not in VIDEO_TYPES:
        return False, f"❌ Unsupported video format: {Path(path).suffix}"
    return True, ""


def clean_hex(value, fallback="000000"):
    value = str(value or "").strip().replace("#", "")
    return value.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", value) else fallback


def ffmpeg_filter_color(value):
    return "0x" + clean_hex(value)


def clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def even_int(v, minimum=2):
    n = max(minimum, int(round(float(v))))
    return n if n % 2 == 0 else n - 1


# ============================================================
# URL DOWNLOAD
# ============================================================

def download_video_from_link(link):
    if not link or not str(link).strip():
        return None, "❌ URL မရှိပါ။"

    link = str(link).strip()
    base = TEMP_DIR / f"download_{uuid.uuid4().hex}"
    template = str(base) + ".%(ext)s"

    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "overwrites": True,
        "merge_output_format": "mp4",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0 Safari/537.36"
        ),
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(link, download=True)
            prepared = ydl.prepare_filename(info)

        candidates = [
            Path(prepared),
            Path(str(base) + ".mp4"),
            Path(str(base) + ".mkv"),
            Path(str(base) + ".webm"),
            Path(str(base) + ".mov"),
        ]
        for p in candidates:
            if p.exists():
                return str(p), f"✅ URL download ပြီးပါပြီ — {p.name}"

        for p in TEMP_DIR.glob(f"download_{base.name.split('_', 1)[1]}.*"):
            if p.is_file():
                return str(p), f"✅ URL download ပြီးပါပြီ — {p.name}"

        return None, "❌ Download ပြီးပေမယ့် video file မတွေ့ပါ။"
    except Exception as e:
        return None, f"❌ URL download error: {e}"


# ============================================================
# FAST PREVIEW
# ============================================================

def instant_video_preview(value):
    path = normalize_path(value)
    if path and os.path.exists(path):
        return path
    return None


def url_preview(url):
    path, _ = download_video_from_link(url)
    return path


# ============================================================
# GEMINI
# ============================================================

def get_gemini_client(api_key=""):
    if genai is None:
        return None, "❌ google-genai package မရှိပါ။"

    key = (str(api_key or "").strip() or ENV_API_KEY.strip())
    if not key:
        return None, "❌ GEMINI API Key ထည့်ပါ။"

    try:
        return genai.Client(api_key=key), ""
    except Exception as e:
        return None, f"❌ Gemini client error: {e}"


def wait_for_gemini_file(client, uploaded, timeout_seconds=180):
    """Gemini video files can require server-side processing before use."""
    file_obj = uploaded
    started = time.time()

    while time.time() - started < timeout_seconds:
        state = getattr(file_obj, "state", None)
        state_name = str(getattr(state, "name", state or "")).upper()

        if "ACTIVE" in state_name:
            return file_obj, ""

        if "FAILED" in state_name:
            return None, f"❌ Gemini video processing failed: {state_name}"

        try:
            name = getattr(file_obj, "name", None)
            if name:
                file_obj = client.files.get(name=name)
        except Exception:
            pass

        time.sleep(2)

    return None, "❌ Gemini video processing timeout (180 sec)"


def upload_and_wait_gemini(client, video_path):
    try:
        uploaded = client.files.upload(file=video_path)
    except Exception as e:
        return None, f"❌ Gemini video upload error: {e}"

    return wait_for_gemini_file(client, uploaded)


def run_gemini_video_analysis(video_path, ratio, script_language, api_key):
    ok, err = validate_video(video_path)
    if not ok:
        return "", DEFAULT_MODEL, err

    client, client_err = get_gemini_client(api_key)
    if client is None:
        return "", DEFAULT_MODEL, client_err

    duration = get_duration(video_path)
    duration_msg = (
        f"🎞️ Duration: {format_duration(duration)}"
        if duration > 0 else "🎞️ Duration: unknown"
    )

    uploaded, upload_err = upload_and_wait_gemini(client, video_path)
    if uploaded is None:
        return "", DEFAULT_MODEL, upload_err

    prompt = f"""
You are an expert movie recap writer.
Analyze the uploaded video carefully and write a narration script based ONLY on events actually visible/audible in the video.

Output ratio: {ratio}
Requested narration language: {script_language}

Rules:
1. Follow the real sequence of events.
2. Include important characters, actions, conflicts and plot developments.
3. Do not invent scenes, dialogue, characters or an ending that is not present.
4. Make the narration understandable to someone who has not watched the video.
5. Use natural text-to-speech friendly sentences.
6. Avoid stage directions and excessive headings.
7. If Burmese is requested, use natural Myanmar Burmese.
8. Keep important character names where useful.
9. Return ONLY the finished narration script.
"""

    try:
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=[uploaded, prompt],
        )
        text = getattr(response, "text", "") or ""
        if not text.strip():
            return "", DEFAULT_MODEL, "❌ Gemini က script မပြန်ပေးပါ။"
        return text.strip(), DEFAULT_MODEL, duration_msg
    except Exception as e:
        return "", DEFAULT_MODEL, f"❌ Gemini analysis error: {e}"


def tab1_analyze(video_file, url, ratio, language, api_key):
    target = normalize_path(video_file)
    status = ""

    if not target and url and str(url).strip():
        target, status = download_video_from_link(url)

    if not target:
        return "", "❌ Video file သို့ URL ထည့်ပါ။", ""

    script, model, analysis_status = run_gemini_video_analysis(
        target, ratio, language, api_key
    )
    if not script:
        return "", analysis_status, model
    return script, f"✅ Analysis ပြီးပါပြီ — {analysis_status}", model


def save_script(text):
    if not text or not str(text).strip():
        return None
    p = OUTPUT_DIR / f"movie_script_{uuid.uuid4().hex[:8]}.txt"
    p.write_text(str(text), encoding="utf-8")
    return str(p)


# ============================================================
# TTS
# ============================================================

async def _tts_async(text, voice, output_path, rate, volume):
    communicator = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
    )
    await communicator.save(output_path)


def edge_rate(value):
    try:
        return f"{int(float(value)):+d}%"
    except Exception:
        return "+0%"


def edge_volume(value):
    try:
        return f"{int(float(value)):+d}%"
    except Exception:
        return "+0%"


def make_tts(text, voice_name, rate, volume):
    if not text or not str(text).strip():
        return None, "❌ TTS text မရှိပါ။"

    voice = VOICE_MAP.get(voice_name)
    if not voice:
        return None, f"❌ Voice မတွေ့ပါ: {voice_name}"

    out = OUTPUT_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
    try:
        asyncio.run(_tts_async(
            str(text).strip(),
            voice,
            str(out),
            edge_rate(rate),
            edge_volume(volume),
        ))
        return str(out), f"✅ TTS ပြီးပါပြီ — {voice_name}"
    except RuntimeError as e:
        # Fallback for environments that already have an event loop.
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_tts_async(
                str(text).strip(), voice, str(out),
                edge_rate(rate), edge_volume(volume)
            ))
            loop.close()
            return str(out), f"✅ TTS ပြီးပါပြီ — {voice_name}"
        except Exception:
            return None, f"❌ TTS Error: {e}"
    except Exception as e:
        return None, f"❌ TTS Error: {e}"


# ============================================================
# SUBTITLE HELPERS
# ============================================================

def wrap_two_lines(text, max_chars=42):
    words = str(text).strip().split()
    if not words:
        return ""
    if len(words) == 1:
        return words[0]

    line1 = ""
    line2 = ""
    for word in words:
        candidate = (line1 + " " + word).strip()
        if len(candidate) <= max_chars or not line1:
            line1 = candidate
        else:
            line2 = (line2 + " " + word).strip()
    return line1 if not line2 else line1 + "\n" + line2


def script_chunks(text):
    # Split by punctuation first, then keep chunks readable.
    raw = re.split(r"(?<=[.!?။！？])\s+|\n+", str(text).strip())
    chunks = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        if len(item) <= 120:
            chunks.append(item)
        else:
            words = item.split()
            cur = ""
            for w in words:
                if len(cur) + len(w) + 1 > 110 and cur:
                    chunks.append(cur)
                    cur = w
                else:
                    cur = (cur + " " + w).strip()
            if cur:
                chunks.append(cur)
    return chunks


def srt_time(seconds):
    ms = int(round((float(seconds) - int(float(seconds))) * 1000))
    total = int(float(seconds))
    if ms >= 1000:
        total += 1
        ms = 0
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def create_srt(text, audio_duration, max_lines=2):
    chunks = script_chunks(text)
    if not chunks:
        return None

    if audio_duration <= 0:
        audio_duration = max(4.0, len(chunks) * 4.0)

    weights = [max(1, len(x)) for x in chunks]
    total_weight = sum(weights)
    cursor = 0.0
    blocks = []

    for i, chunk in enumerate(chunks, start=1):
        duration = audio_duration * weights[i - 1] / total_weight
        # Avoid ultra-short captions.
        duration = max(1.6, duration)
        start = cursor
        end = min(audio_duration, cursor + duration)
        if i == len(chunks):
            end = audio_duration
        caption = wrap_two_lines(chunk)
        blocks.append(
            f"{i}\n{srt_time(start)} --> {srt_time(end)}\n{caption}\n"
        )
        cursor = end

    p = TEMP_DIR / f"subtitles_{uuid.uuid4().hex[:8]}.srt"
    p.write_text("\n".join(blocks), encoding="utf-8")
    return str(p)


def ass_escape(text):
    return str(text).replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def create_ass_from_text(text, duration, font_size, color, position):
    """Creates simple ASS subtitles; max two lines per caption."""
    chunks = script_chunks(text)
    if not chunks:
        return None

    duration = max(1.0, float(duration or 1.0))
    weights = [max(1, len(x)) for x in chunks]
    total = sum(weights)
    cursor = 0.0

    # ASS alignment: 2 bottom-center, 8 top-center, 5 middle-center.
    align = {"Lower": 2, "Upper": 8, "Center": 5}.get(position, 2)
    margin_v = 60 if position == "Lower" else 55

    primary = clean_hex(color)
    # ASS uses BGR hex: BBGGRR.
    bgr = primary[4:6] + primary[2:4] + primary[0:2]

    ass = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{int(font_size)},&H00{bgr},&H00000000,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,3,1,{align},40,40,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for i, chunk in enumerate(chunks, start=1):
        part = duration * weights[i - 1] / total
        start = cursor
        end = duration if i == len(chunks) else min(duration, cursor + max(1.6, part))
        caption = ass_escape(wrap_two_lines(chunk))
        caption = caption.replace("\n", r"\N")
        ass.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{caption}"
        )
        cursor = end

    p = TEMP_DIR / f"subtitles_{uuid.uuid4().hex[:8]}.ass"
    p.write_text("\n".join(ass), encoding="utf-8")
    return str(p)


def ass_time(sec):
    cs = int(round(float(sec) * 100))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


# ============================================================
# INTERACTIVE COVER PREVIEW
# ============================================================

def cover_preview_html(video_path, ratio, cover_type, color, opacity,
                       blur, x, y, width, height, enabled,
                       crop_percent=100, zoom=1.0, video_x=0, video_y=0,
                       brightness=0, contrast=1.0, fill_mode="Blur (ဝေဝါးဖြည့်)",
                       fill_color="#000000"):
    video_path = normalize_path(video_path)
    if not video_path or not os.path.exists(video_path):
        return """
        <div class="empty-preview">
          🎬 Video upload လုပ်ပြီးရင် မူရင်းစာတန်း Cover Preview ဒီနေရာမှာပေါ်ပါမယ်။
        </div>
        """

    ratio_css = {
        "1:1": "1 / 1",
        "3:4": "3 / 4",
        "16:9": "16 / 9",
        "9:16": "9 / 16",
    }.get(ratio, "9 / 16")

    x = clamp(x, 0, 100)
    y = clamp(y, 0, 100)
    w = clamp(width, 5, 100)
    h = clamp(height, 2, 50)
    x = min(x, 100 - w)
    y = min(y, 100 - h)
    op = clamp(opacity, 0, 100) / 100.0
    blur_px = clamp(blur, 0, 50)
    color = clean_hex(color)

    if cover_type == "Blur (ဝေဝါး)":
        cover_style = (
            f"background:rgba(0,0,0,{op * 0.18:.3f});"
            f"backdrop-filter:blur({blur_px:.1f}px);"
            f"-webkit-backdrop-filter:blur({blur_px:.1f}px);"
        )
    else:
        cover_style = f"background:#{color};opacity:{op:.3f};"

    disabled_style = "opacity:0;pointer-events:none;" if not enabled else ""
    # Gradio 6.x serves local files from the /gradio_api/file= endpoint.
    # Use the real VIDEO file as the preview source so the cover is rendered
    # ON TOP OF THE VIDEO itself, not as a separate cover image.
    src = "/gradio_api/file=" + quote(video_path, safe="/")
    crop_percent = clamp(crop_percent, 30, 100)
    zoom = clamp(zoom, 0.5, 2.5)
    video_x = clamp(video_x, -300, 300)
    video_y = clamp(video_y, -300, 300)
    brightness = clamp(brightness, -1, 1)
    contrast = clamp(contrast, 0.5, 2.5)
    fill_color_clean = clean_hex(fill_color)
    if fill_mode == "Color (အရောင်ဖြည့်)":
        bg_style = f"background:#{fill_color_clean};"
    else:
        bg_style = "background:#111;"
    crop_scale = 100.0 / crop_percent
    total_scale = max(1.0, crop_scale * zoom)
    bright_css = max(0.0, 1.0 + brightness)

    return f"""
    <div class="cover-editor" id="cover-editor-inner" style="aspect-ratio:{ratio_css};{bg_style}">
      <video id="cover-editor-video" src="{src}" controls playsinline preload="metadata" style="transform:translate({video_x:.1f}px,{video_y:.1f}px) scale({total_scale:.4f});filter:brightness({bright_css:.3f}) contrast({contrast:.3f});"></video>
      <div id="cover-box" class="cover-box" style="left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%;{cover_style}{disabled_style}">
        <span>↕↔ ဆွဲရွှေ့ပါ</span>
      </div>
      <div class="cover-label">Original Video Subtitle Blur / Cover · {ratio}</div>
    </div>
    """


COVER_JS = r"""
const root = element;

function findInput(id) {
  const r = document.querySelector('#' + id);
  return r ? r.querySelector('input, textarea') : null;
}

function setNumber(id, value) {
  const input = findInput(id);
  if (!input) return;
  const v = String(Math.round(value * 100) / 100);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (setter) setter.call(input, v); else input.value = v;
  input.dispatchEvent(new Event('input', {bubbles:true}));
  input.dispatchEvent(new Event('change', {bubbles:true}));
}

function attachDrag() {
  const editor = root.querySelector('#cover-editor-inner');
  const box = root.querySelector('#cover-box');
  if (!editor || !box || box.dataset.bound === '1') return;
  box.dataset.bound = '1';

  let dragging = false;
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;

  box.addEventListener('pointerdown', (e) => {
    if (box.style.pointerEvents === 'none') return;
    e.preventDefault();
    dragging = true;
    box.setPointerCapture?.(e.pointerId);
    startX = e.clientX;
    startY = e.clientY;
    startLeft = parseFloat(box.style.left) || 0;
    startTop = parseFloat(box.style.top) || 0;
    box.classList.add('dragging');
  });

  box.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const rect = editor.getBoundingClientRect();
    const dx = ((e.clientX - startX) / rect.width) * 100;
    const dy = ((e.clientY - startY) / rect.height) * 100;
    const w = parseFloat(box.style.width) || 10;
    const h = parseFloat(box.style.height) || 10;
    const x = Math.max(0, Math.min(100 - w, startLeft + dx));
    const y = Math.max(0, Math.min(100 - h, startTop + dy));
    box.style.left = x + '%';
    box.style.top = y + '%';
    setNumber('cover-x-number', x);
    setNumber('cover-y-number', y);
  });

  const stop = () => {
    dragging = false;
    box.classList.remove('dragging');
  };
  box.addEventListener('pointerup', stop);
  box.addEventListener('pointercancel', stop);
}

attachDrag();
new MutationObserver(() => setTimeout(attachDrag, 20)).observe(root, {childList:true, subtree:true});
"""


def build_cover_preview(*args):
    return cover_preview_html(*args)


# ============================================================
# FFMPEG RENDER HELPERS
# ============================================================

def escape_filter_path(path):
    # For subtitles/ASS paths used in filter arguments.
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def build_video_filter(
    source_index,
    tw,
    th,
    crop_percent,
    fill_mode,
    fill_color,
    zoom,
    x_offset,
    y_offset,
    brightness,
    contrast,
    cover_enable,
    cover_type,
    cover_color,
    cover_opacity,
    cover_blur,
    cover_x,
    cover_y,
    cover_width,
    cover_height,
    subtitle_file=None,
):
    crop = clamp(crop_percent, 30, 100) / 100.0
    zoom = clamp(zoom, 0.5, 3.0)
    x_offset = clamp(x_offset, -100, 100)
    y_offset = clamp(y_offset, -100, 100)
    brightness = clamp(brightness, -1, 1)
    contrast = clamp(contrast, 0.5, 2.5)

    # Crop center, then fill the selected output frame.
    crop_filter = (
        f"crop=iw*{crop:.6f}:ih*{crop:.6f}:"
        f"(iw-iw*{crop:.6f})/2:(ih-ih*{crop:.6f})/2"
    )

    # Main foreground. Zoom is applied after the crop-to-output operation.
    fg = (
        f"[{source_index}:v]"
        f"{crop_filter},"
        f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},"
        f"eq=brightness={brightness:.4f}:contrast={contrast:.4f},"
        f"scale={even_int(tw * zoom)}:{even_int(th * zoom)}"
        "[fg]"
    )

    if fill_mode == "Color (အရောင်ဖြည့်)":
        bg = (
            f"color=c={ffmpeg_filter_color(fill_color)}:s={tw}x{th}:d=36000,"
            "format=yuv420p[bg]"
        )
    else:
        bg = (
            f"[{source_index}:v]"
            f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},"
            "boxblur=30:15[bg]"
        )

    # Zoomed foreground may exceed output frame; overlay crops it naturally.
    overlay = (
        f"[bg][fg]overlay="
        f"(W-w)/2+{x_offset:.2f}:"
        f"(H-h)/2+{y_offset:.2f}:"
        "eof_action=repeat[v1]"
    )

    parts = [bg, fg, overlay]
    current = "[v1]"

    # --------------------------------------------------------
    # Original subtitle cover
    # Coordinates are percentages of the final output frame.
    # --------------------------------------------------------
    if cover_enable:
        cx = clamp(cover_x, 0, 100)
        cy = clamp(cover_y, 0, 100)
        cw = clamp(cover_width, 5, 100)
        ch = clamp(cover_height, 2, 50)
        cx = min(cx, 100 - cw)
        cy = min(cy, 100 - ch)
        opacity = clamp(cover_opacity, 0, 100) / 100.0
        blur_strength = clamp(cover_blur, 0, 50)

        px = f"{tw}*{cw/100:.6f}"
        py = f"{th}*{ch/100:.6f}"
        pxx = f"{tw}*{cx/100:.6f}"
        pyy = f"{th}*{cy/100:.6f}"

        if cover_type == "Blur (ဝေဝါး)":
            # Blur only the selected rectangle from the already-composed video.
            parts.append(
                f"[v1]split=2[cover_base][cover_src]"
            )
            parts.append(
                f"[cover_src]crop={px}:{py}:{pxx}:{pyy},"
                f"boxblur={max(1, int(round(blur_strength)))}:1[cover_blur]"
            )
            parts.append(
                f"[cover_base][cover_blur]overlay={pxx}:{pyy}:eof_action=repeat[v2]"
            )
            current = "[v2]"
        else:
            c = ffmpeg_filter_color(cover_color)
            parts.append(
                f"[v1]drawbox=x={pxx}:y={pyy}:w={px}:h={py}:"
                f"color={c}@{opacity:.4f}:t=fill[v2]"
            )
            current = "[v2]"

    # --------------------------------------------------------
    # AI subtitles
    # --------------------------------------------------------
    if subtitle_file and os.path.exists(subtitle_file):
        sp = escape_filter_path(subtitle_file)
        parts.append(
            f"{current}subtitles='{sp}':"
            "force_style='FontName=Arial,FontSize=22,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,Outline=2,Shadow=1,"
            "Alignment=2,MarginV=50'[vout]"
        )
        current = "[vout]"
    else:
        parts.append(f"{current}null[vout]")
        current = "[vout]"

    return parts, current


def render_video(
    source_video,
    output_ratio,
    export_quality,
    crop_percent,
    fill_mode,
    fill_color,
    zoom,
    x_offset,
    y_offset,
    brightness,
    contrast,
    cover_enable,
    cover_type,
    cover_color,
    cover_opacity,
    cover_blur,
    cover_x,
    cover_y,
    cover_width,
    cover_height,
    tts_audio=None,
    original_audio=True,
    original_audio_volume=1.0,
    bgm_audio=None,
    bgm_volume=0.12,
    subtitle_text=None,
    subtitle_color="#FFFFFF",
    subtitle_size=22,
    subtitle_position="Lower",
):
    if not ffmpeg_exists():
        return None, "❌ FFmpeg မတွေ့ပါ။ Render server မှာ FFmpeg install လုပ်ပါ။"

    source_video = normalize_path(source_video)
    tts_audio = normalize_path(tts_audio)
    bgm_audio = normalize_path(bgm_audio)

    ok, err = validate_video(source_video)
    if not ok:
        return None, err

    if output_ratio not in EXPORT_DIMS:
        return None, "❌ Output ratio မမှန်ပါ။"
    if export_quality not in EXPORT_DIMS:
        return None, "❌ Export quality မမှန်ပါ။"

    tw, th = EXPORT_DIMS[export_quality][output_ratio]
    tw, th = even_int(tw), even_int(th)

    tts_duration = get_duration(tts_audio) if tts_audio else 0.0
    source_duration = get_duration(source_video)
    bgm_duration = get_duration(bgm_audio) if bgm_audio else 0.0

    # Voice duration controls final duration. Otherwise use source duration.
    final_duration = tts_duration if tts_duration > 0 else source_duration
    if final_duration <= 0:
        final_duration = bgm_duration if bgm_duration > 0 else 10.0
    final_duration = max(0.5, final_duration)

    subtitle_file = None
    if subtitle_text and str(subtitle_text).strip() and tts_duration > 0:
        # Use ASS so Myanmar/Unicode is rendered by libass when available.
        subtitle_file = create_ass_from_text(
            subtitle_text,
            tts_duration,
            int(clamp(subtitle_size, 12, 80)),
            subtitle_color,
            subtitle_position,
        )

    # Input 0 loops so video continues until voice ends.
    inputs = []
    inputs += ["-stream_loop", "-1", "-i", source_video]
    input_count = 1

    tts_idx = None
    bgm_idx = None

    if tts_audio and os.path.exists(tts_audio):
        inputs += ["-i", tts_audio]
        tts_idx = input_count
        input_count += 1

    if bgm_audio and os.path.exists(bgm_audio):
        inputs += ["-stream_loop", "-1", "-i", bgm_audio]
        bgm_idx = input_count
        input_count += 1

    parts, video_label = build_video_filter(
        0, tw, th,
        crop_percent, fill_mode, fill_color,
        zoom, x_offset, y_offset,
        brightness, contrast,
        bool(cover_enable), cover_type, cover_color,
        cover_opacity, cover_blur,
        cover_x, cover_y, cover_width, cover_height,
        subtitle_file,
    )

    audio_parts = []
    current_audio = None

    if original_audio:
        orig_vol = clamp(original_audio_volume, 0, 2)
        audio_parts.append(
            f"[0:a]aresample=48000,volume={orig_vol:.4f}[orig]"
        )
        current_audio = "[orig]"

    if tts_idx is not None:
        audio_parts.append(
            f"[{tts_idx}:a]aresample=48000,asetpts=N/SR/TB[tts]"
        )
        if current_audio:
            audio_parts.append(
                f"{current_audio}[tts]"
                "amix=inputs=2:duration=longest:dropout_transition=2[a1]"
            )
            current_audio = "[a1]"
        else:
            current_audio = "[tts]"

    if bgm_idx is not None:
        bv = clamp(bgm_volume, 0, 1)
        audio_parts.append(
            f"[{bgm_idx}:a]aresample=48000,volume={bv:.4f}[bgm]"
        )
        if current_audio:
            audio_parts.append(
                f"{current_audio}[bgm]"
                "amix=inputs=2:duration=longest:dropout_transition=2[aout]"
            )
            current_audio = "[aout]"
        else:
            current_audio = "[bgm]"

    filter_complex = ";".join(parts + audio_parts)

    out = OUTPUT_DIR / f"final_{uuid.uuid4().hex[:10]}.mp4"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", video_label,
    ]

    if current_audio:
        cmd += ["-map", current_audio, "-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21" if export_quality != "1080p" else "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-t", f"{final_duration:.3f}",
        str(out),
    ]

    try:
        r = run_cmd(cmd, timeout=None)
        if r.returncode != 0:
            return None, "❌ FFmpeg render error:\n\n" + r.stderr[-5000:]
        if not out.exists():
            return None, "❌ Output video မထွက်ပါ။"

        return str(out), (
            "✅ Render ပြီးပါပြီ\n"
            f"📐 {output_ratio} · {export_quality} ({tw}×{th})\n"
            f"⏱️ Duration: {format_duration(final_duration)}\n"
            f"✂️ Crop: {float(crop_percent):.0f}% · Zoom: {float(zoom):.2f}x\n"
            f"🧱 Original subtitle cover: {'ON' if cover_enable else 'OFF'}"
        )
    except Exception as e:
        return None, f"❌ Render exception: {e}"


# ============================================================
# TAB 3 PREVIEW / PIPELINE
# ============================================================

def tab3_preview(
    video_path, ratio,
    crop, fill_mode, fill_color, zoom, x, y,
    brightness, contrast,
    cover_enable, cover_type, cover_color, cover_opacity,
    cover_blur, cover_x, cover_y, cover_width, cover_height,
):
    path = normalize_path(video_path)
    return cover_preview_html(
        path, ratio, cover_type, cover_color, cover_opacity,
        cover_blur, cover_x, cover_y, cover_width, cover_height,
        cover_enable, crop, zoom, x, y, brightness, contrast, fill_mode, fill_color
    )


def tab3_generate(
    video_file, video_url, api_key,
    ratio, language, voice_name, tts_rate, tts_volume,
    crop, fill_mode, fill_color, zoom, x, y,
    brightness, contrast,
    cover_enable, cover_type, cover_color, cover_opacity,
    cover_blur, cover_x, cover_y, cover_width, cover_height,
    subtitle_enable, subtitle_color, subtitle_size, subtitle_position,
    original_audio, original_audio_volume,
    bgm_audio, bgm_volume,
    export_quality,
):
    target = normalize_path(video_file)
    status_lines = []

    if not target and video_url and str(video_url).strip():
        target, msg = download_video_from_link(video_url)
        status_lines.append(msg)

    if not target:
        return None, "", "❌ Video file/URL မရှိပါ။", ""

    ok, err = validate_video(target)
    if not ok:
        return None, "", err, ""

    # 1) Gemini
    script, model, analysis_status = run_gemini_video_analysis(
        target, ratio, language, api_key
    )
    if not script:
        return None, "", analysis_status, model
    status_lines.append("✅ Gemini Analysis: " + analysis_status)

    # 2) TTS
    tts_path, tts_status = make_tts(
        script, voice_name, tts_rate, tts_volume
    )
    if not tts_path:
        return None, script, tts_status, model
    status_lines.append(tts_status)

    # 3) Render
    subtitle_text = script if subtitle_enable else None
    output, render_status = render_video(
        source_video=target,
        output_ratio=ratio,
        export_quality=export_quality,
        crop_percent=crop,
        fill_mode=fill_mode,
        fill_color=fill_color,
        zoom=zoom,
        x_offset=x,
        y_offset=y,
        brightness=brightness,
        contrast=contrast,
        cover_enable=cover_enable,
        cover_type=cover_type,
        cover_color=cover_color,
        cover_opacity=cover_opacity,
        cover_blur=cover_blur,
        cover_x=cover_x,
        cover_y=cover_y,
        cover_width=cover_width,
        cover_height=cover_height,
        tts_audio=tts_path,
        original_audio=original_audio,
        original_audio_volume=original_audio_volume,
        bgm_audio=bgm_audio,
        bgm_volume=bgm_volume,
        subtitle_text=subtitle_text,
        subtitle_color=subtitle_color,
        subtitle_size=subtitle_size,
        subtitle_position=subtitle_position,
    )
    if not output:
        return None, script, "\n".join(status_lines + [render_status]), model

    status_lines.append(render_status)
    return output, script, "\n".join(status_lines), model


# ============================================================
# CSS
# ============================================================

CSS = r"""
.gradio-container { max-width: 1500px !important; }
video { border-radius: 12px !important; }
.empty-preview {
  min-height: 260px; display:flex; align-items:center; justify-content:center;
  border:1px dashed #777; border-radius:14px; padding:20px; text-align:center;
}
.cover-editor {
  position:relative; width:100%; max-width:720px; margin:auto; overflow:hidden;
  border-radius:14px; background:#111; box-shadow:0 8px 28px rgba(0,0,0,.25);
  touch-action:none;
}
.cover-editor video {
  width:100%; height:100%; display:block; object-fit:cover;
  background:#111; border-radius:0 !important;
}
.cover-box {
  position:absolute; z-index:10; box-sizing:border-box;
  border:2px solid rgba(255,255,255,.95);
  cursor:move; touch-action:none; display:flex; align-items:center; justify-content:center;
  color:#fff; font-size:12px; font-weight:700; text-shadow:0 1px 3px #000;
  user-select:none;
}
.cover-box span { background:rgba(0,0,0,.45); padding:3px 7px; border-radius:7px; }
.cover-box.dragging { border-style:dashed; }
.cover-label {
  position:absolute; z-index:20; left:8px; top:8px; color:#fff;
  background:rgba(0,0,0,.55); padding:5px 9px; border-radius:8px; font-size:12px;
  pointer-events:none;
}
.small-note { padding:10px 12px; border-radius:10px; background:rgba(127,127,127,.10); }
"""


# ============================================================
# UI
# ============================================================

with gr.Blocks(title=APP_TITLE) as demo:
    gr.Markdown(
        """
# 🎬 AI Movie Recap Studio Pro

**Fast Upload → Gemini Recap → Myanmar TTS → Interactive Subtitle Cover → Final Video**

⚠️ **Render fix:** `gr.Video(type="filepath")` မသုံးထားပါ။ Upload အတွက် `gr.File(type="filepath")` ကိုပဲ သုံးထားပါတယ်။
"""
    )

    with gr.Row():
        api_key = gr.Textbox(
            label="🔑 Gemini API Key",
            value=ENV_API_KEY,
            type="password",
            placeholder="AIza...",
            scale=3,
        )
        api_status = gr.Markdown(
            "ENV GEMINI_API_KEY ရှိရင် အလိုအလျောက်သုံးနိုင်ပါတယ်။",
        )

    # ========================================================
    # TAB 1
    # ========================================================
    with gr.Tab("1️⃣ Video Analysis & Script"):
        gr.Markdown(
            """
### 🎥 Video Analysis
Upload ပြီးတာနဲ့ preview တန်းပေါ်မယ်။ FFmpeg / Gemini analysis ကို upload event မှာ မလုပ်ပါ။
`Analyze Video` နှိပ်မှ Gemini video upload + processing + analysis စပါမယ်။
"""
        )

        with gr.Row():
            with gr.Column():
                v1_file = gr.File(
                    label="📤 Upload Video",
                    type="filepath",
                    file_types=VIDEO_TYPES,
                )
                v1_url = gr.Textbox(
                    label="🔗 Or YouTube / TikTok / Video URL",
                    placeholder="https://...",
                )
                v1_ratio = gr.Dropdown(
                    choices=list(RATIO_DIMS.keys()),
                    value="9:16",
                    label="📐 Output Ratio",
                )
                v1_language = gr.Dropdown(
                    choices=["မြန်မာ (Burmese)", "English", "Japanese", "Korean", "Chinese"],
                    value="မြန်မာ (Burmese)",
                    label="🌐 Script Language",
                )
                v1_analyze = gr.Button("🚀 Analyze Video", variant="primary")

            with gr.Column():
                v1_preview = gr.Video(
                    label="⚡ Instant Video Preview",
                    autoplay=True,
                    interactive=False,
                )
                v1_status = gr.Textbox(label="Status", interactive=False, lines=4)

        v1_script = gr.Textbox(
            label="📝 AI Movie Recap Script",
            lines=18,
            buttons=["copy"],
        )
        with gr.Row():
            v1_model = gr.Textbox(label="Gemini Model", interactive=False)
            v1_save = gr.Button("💾 Save Script")
        v1_download = gr.File(label="Saved Script", interactive=False)

        v1_file.change(
            instant_video_preview,
            inputs=v1_file,
            outputs=v1_preview,
            show_progress="hidden",
        )
        v1_url.change(
            url_preview,
            inputs=v1_url,
            outputs=v1_preview,
            show_progress="minimal",
        )
        v1_analyze.click(
            tab1_analyze,
            inputs=[v1_file, v1_url, v1_ratio, v1_language, api_key],
            outputs=[v1_script, v1_status, v1_model],
            show_progress="full",
        )
        v1_save.click(save_script, inputs=v1_script, outputs=v1_download)

    # ========================================================
    # TAB 2
    # ========================================================
    with gr.Tab("2️⃣ Text-to-Speech"):
        with gr.Row():
            with gr.Column():
                t2_text = gr.Textbox(
                    label="📝 Text / Script",
                    lines=18,
                    placeholder="Tab 1 က script ကို ဒီမှာထည့်ပါ...",
                )
                t2_voice = gr.Dropdown(
                    choices=list(VOICE_MAP.keys()),
                    value="Myanmar Female — Nilar",
                    label="🎙️ Voice",
                )
                with gr.Row():
                    t2_rate = gr.Slider(-50, 50, value=0, step=1, label="Speaking Speed (%)")
                    t2_volume = gr.Slider(-50, 50, value=0, step=1, label="Voice Volume (%)")
                t2_generate = gr.Button("🔊 Generate TTS", variant="primary")
            with gr.Column():
                t2_audio = gr.Audio(
                    label="🎧 Generated Voice",
                    type="filepath",
                    interactive=False,
                )
                t2_status = gr.Textbox(label="TTS Status", interactive=False, lines=5)

        t2_generate.click(
            make_tts,
            inputs=[t2_text, t2_voice, t2_rate, t2_volume],
            outputs=[t2_audio, t2_status],
            show_progress="minimal",
        )

    # ========================================================
    # TAB 3
    # ========================================================
    with gr.Tab("3️⃣ One-Click All-in-One Video Studio"):
        gr.Markdown(
            """
## ⚡ One-Click Movie Studio

**Upload → Preview → Gemini → TTS → Crop → Background → Original Subtitle Cover → AI Subtitle → Export**

### 🖱️ Original Subtitle Cover ကို Video Preview ပေါ်မှာ တိုက်ရိုက်ဆွဲရွှေ့နိုင်ပါတယ်
Cover box ကို **အပေါ် / အောက် / ဘယ် / ညာ** ဆွဲပြီး position ပြောင်းပါ။
"""
        )

        with gr.Row():
            with gr.Column(scale=1):
                t3_file = gr.File(
                    label="📤 Upload Video — Fast Mode",
                    type="filepath",
                    file_types=VIDEO_TYPES,
                )
                t3_url = gr.Textbox(
                    label="🔗 Or Video URL",
                    placeholder="YouTube / TikTok / video URL...",
                )
                t3_ratio = gr.Dropdown(
                    choices=list(RATIO_DIMS.keys()),
                    value="9:16",
                    label="📐 Output Ratio",
                )
                t3_language = gr.Dropdown(
                    choices=["မြန်မာ (Burmese)", "English", "Japanese", "Korean", "Chinese"],
                    value="မြန်မာ (Burmese)",
                    label="🌐 Script Language",
                )
                t3_voice = gr.Dropdown(
                    choices=list(VOICE_MAP.keys()),
                    value="Myanmar Female — Nilar",
                    label="🎙️ TTS Voice",
                )
                with gr.Row():
                    t3_tts_rate = gr.Slider(-50, 50, value=0, step=1, label="Voice Speed (%)")
                    t3_tts_volume = gr.Slider(-50, 50, value=0, step=1, label="Voice Volume (%)")

            with gr.Column(scale=1):
                t3_live_video = gr.Video(
                    label="⚡ Instant Video Preview",
                    autoplay=True,
                    interactive=False,
                )
                t3_preview_status = gr.Markdown("Upload video လုပ်ပြီးရင် preview ပေါ်ပါမယ်။")

        gr.Markdown("## ✂️ Crop / Zoom / Position / Color")
        with gr.Row():
            t3_crop = gr.Slider(30, 100, value=100, step=1, label="✂️ Crop Size (%) — Center Crop")
            t3_zoom = gr.Slider(0.5, 2.5, value=1.0, step=0.05, label="🔎 Zoom")
        with gr.Row():
            t3_x = gr.Slider(-300, 300, value=0, step=1, label="↔️ Video X Position")
            t3_y = gr.Slider(-300, 300, value=0, step=1, label="↕️ Video Y Position")
        with gr.Row():
            t3_fill_mode = gr.Dropdown(
                ["Blur (ဝေဝါးဖြည့်)", "Color (အရောင်ဖြည့်)"],
                value="Blur (ဝေဝါးဖြည့်)",
                label="🎨 Background Fill",
            )
            t3_fill_color = gr.ColorPicker(value="#000000", label="🎨 Background Color")
            t3_brightness = gr.Slider(-1, 1, value=0, step=0.05, label="☀️ Brightness")
            t3_contrast = gr.Slider(0.5, 2.5, value=1, step=0.05, label="◐ Contrast")

        gr.Markdown("## 🛡️ Original Video Subtitle Cover")
        with gr.Row():
            t3_cover_enable = gr.Checkbox(True, label="မူရင်း Video စာတန်းကို ဖုံးမည်")
            t3_cover_type = gr.Radio(
                ["Blur (ဝေဝါး)", "Color (အရောင်)"],
                value="Blur (ဝေဝါး)",
                label="ဖုံးတဲ့ပုံစံ",
            )
            t3_cover_preset = gr.Dropdown(
                choices=list(COLOR_PRESETS.keys()),
                value="Black",
                label="🎨 Color Preset",
            )
            t3_cover_color = gr.ColorPicker(value="#000000", label="🎨 Custom Color")

        with gr.Row():
            t3_cover_opacity = gr.Slider(0, 100, value=75, step=1, label="🌫️ Color Opacity (%)")
            t3_cover_blur = gr.Slider(0, 50, value=18, step=1, label="🌁 Blur Strength")
            t3_cover_width = gr.Slider(5, 100, value=80, step=1, label="↔️ Cover Width (%)")
            t3_cover_height = gr.Slider(2, 50, value=12, step=1, label="↕️ Cover Height (%)")

        with gr.Row():
            t3_cover_x = gr.Number(10, label="Cover X (%)", elem_id="cover-x-number")
            t3_cover_y = gr.Number(78, label="Cover Y (%)", elem_id="cover-y-number")

        t3_cover_preview = gr.HTML(
            label="🖱️ Drag Cover Preview",
            elem_id="cover-preview-html",
            js_on_load=COVER_JS,
        )

        gr.Markdown(
            """
**Cover Position:** Preview ထဲက အဖြူဘောင်ပါတဲ့ Cover box ကို လက်နဲ့/Mouse နဲ့ ဆွဲရွှေ့ပါ။
X/Y က အလိုအလျောက် update ဖြစ်ပြီး Final Render မှာလည်း အဲဒီ position ကို သုံးပါမယ်။
"""
        )

        gr.Markdown("## 📝 Auto Subtitle")
        with gr.Row():
            t3_subtitle_enable = gr.Checkbox(True, label="AI Voice Script ကို Subtitle အဖြစ်ထည့်မည်")
            t3_subtitle_color = gr.ColorPicker(value="#FFFFFF", label="Subtitle Color")
            t3_subtitle_size = gr.Slider(12, 80, value=28, step=1, label="Subtitle Size")
            t3_subtitle_position = gr.Dropdown(
                ["Lower", "Upper", "Center"],
                value="Lower",
                label="Subtitle Position",
            )

        gr.Markdown("## 🔊 Audio")
        with gr.Row():
            t3_original_audio = gr.Checkbox(True, label="🎵 Original Audio")
            t3_original_audio_volume = gr.Slider(0, 2, value=1.0, step=0.05, label="Original Audio Volume")
            t3_bgm = gr.Audio(label="🎼 Optional BGM", type="filepath")
            t3_bgm_volume = gr.Slider(0, 1, value=0.12, step=0.01, label="BGM Volume")

        gr.Markdown("## 🎬 Export")
        t3_quality = gr.Radio(
            ["480p", "720p", "1080p"],
            value="720p",
            label="Export Quality",
        )

        t3_generate = gr.Button(
            "🚀 GENERATE ALL-IN-ONE VIDEO",
            variant="primary",
            size="lg",
        )

        with gr.Row():
            with gr.Column():
                t3_output = gr.Video(
                    label="🎬 Final Output",
                    autoplay=False,
                    interactive=False,
                    buttons=["download"],
                )
            with gr.Column():
                t3_script = gr.Textbox(
                    label="📝 Generated Script",
                    lines=14,
                    buttons=["copy"],
                )
                t3_status = gr.Textbox(
                    label="Status",
                    lines=10,
                    interactive=False,
                )
                t3_model = gr.Textbox(label="Gemini Model", interactive=False)

        # Fast upload -> native Gradio video preview. No processing.
        t3_file.change(
            instant_video_preview,
            inputs=t3_file,
            outputs=t3_live_video,
            show_progress="hidden",
        )

        # URL -> download only when URL changes.
        t3_url.change(
            url_preview,
            inputs=t3_url,
            outputs=t3_live_video,
            show_progress="minimal",
        )

        # Use the already downloaded/previewed video as the HTML preview source.
        # This prevents every slider change from downloading the URL again.
        preview_inputs = [
            t3_live_video, t3_ratio,
            t3_crop, t3_fill_mode, t3_fill_color, t3_zoom, t3_x, t3_y,
            t3_brightness, t3_contrast,
            t3_cover_enable, t3_cover_type, t3_cover_color, t3_cover_opacity,
            t3_cover_blur, t3_cover_x, t3_cover_y, t3_cover_width, t3_cover_height,
        ]

        # Upload/URL changes update both native preview and drag-cover preview.
        t3_file.change(
            tab3_preview, inputs=preview_inputs, outputs=t3_cover_preview,
            show_progress="hidden"
        )
        t3_url.change(
            tab3_preview, inputs=preview_inputs, outputs=t3_cover_preview,
            show_progress="minimal"
        )

        # Cover/crop settings update preview without downloading again.
        for component in [
            t3_ratio, t3_crop, t3_fill_mode, t3_fill_color, t3_zoom, t3_x, t3_y,
            t3_brightness, t3_contrast, t3_cover_enable, t3_cover_type,
            t3_cover_color, t3_cover_opacity, t3_cover_blur, t3_cover_x,
            t3_cover_y, t3_cover_width, t3_cover_height,
        ]:
            component.change(
                tab3_preview, inputs=preview_inputs, outputs=t3_cover_preview,
                show_progress="hidden"
            )

        def apply_cover_preset(name):
            return COLOR_PRESETS.get(name, "#000000")

        t3_cover_preset.change(
            apply_cover_preset,
            inputs=t3_cover_preset,
            outputs=t3_cover_color,
            show_progress="hidden",
        )

        t3_generate.click(
            tab3_generate,
            inputs=[
                t3_file, t3_url, api_key,
                t3_ratio, t3_language, t3_voice, t3_tts_rate, t3_tts_volume,
                t3_crop, t3_fill_mode, t3_fill_color, t3_zoom, t3_x, t3_y,
                t3_brightness, t3_contrast,
                t3_cover_enable, t3_cover_type, t3_cover_color, t3_cover_opacity,
                t3_cover_blur, t3_cover_x, t3_cover_y, t3_cover_width, t3_cover_height,
                t3_subtitle_enable, t3_subtitle_color, t3_subtitle_size, t3_subtitle_position,
                t3_original_audio, t3_original_audio_volume,
                t3_bgm, t3_bgm_volume,
                t3_quality,
            ],
            outputs=[t3_output, t3_script, t3_status, t3_model],
            show_progress="full",
        )


# ============================================================
# LAUNCH
# ============================================================

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860")),
        show_error=True,
        css=CSS,
    )
