import os
import re
import json
import time
import uuid
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

import gradio as gr
import edge_tts
from google import genai
from google.genai import types


# ============================================================
# SMART RECAP STUDIO
# ALL-IN-ONE MOVIE RECAP + AUTO VIDEO EDITOR
# ============================================================

APP_NAME = "Smart Recap Studio"

BASE_DIR = Path("/tmp/smart_recap_studio")
BASE_DIR.mkdir(parents=True, exist_ok=True)

MAX_VIDEO_SECONDS = 300

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
ENV_API_KEY = os.getenv("GEMINI_API_KEY", "")

CURRENT_VIDEO = None
CURRENT_ANALYSIS = ""
CURRENT_SCRIPT = ""
CURRENT_TTS = None
CURRENT_SRT = None


# ============================================================
# HELPERS
# ============================================================

def new_job_dir():
    p = BASE_DIR / uuid.uuid4().hex
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(name: str):
    name = Path(name).name
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


def run_cmd(cmd, timeout=600):
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"


def ffprobe_json(path):
    code, out, err = run_cmd([
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path)
    ])

    if code != 0:
        return {}

    try:
        return json.loads(out)
    except:
        return {}


def video_duration(path):
    data = ffprobe_json(path)

    try:
        return float(data["format"]["duration"])
    except:
        return 0.0


def has_audio(path):
    data = ffprobe_json(path)

    for s in data.get("streams", []):
        if s.get("codec_type") == "audio":
            return True

    return False


def ensure_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg မတွေ့ပါ။ Render server မှာ FFmpeg install လုပ်ထားရန်လိုပါတယ်။")

    if shutil.which("ffprobe") is None:
        raise RuntimeError("FFprobe မတွေ့ပါ။")


def get_api_key(user_key):
    key = (user_key or "").strip()

    if key:
        return key

    if ENV_API_KEY:
        return ENV_API_KEY

    raise ValueError(
        "Gemini API Key မရှိပါ။ UI ထဲမှာ API Key ထည့်ပါ "
        "သို့မဟုတ် Render Environment Variable မှာ GEMINI_API_KEY ထည့်ပါ။"
    )


def get_client(api_key):
    return genai.Client(api_key=api_key)


# ============================================================
# DOWNLOAD VIDEO FROM URL
# ============================================================

def download_video(url, workdir):
    if not url or not url.strip():
        raise ValueError("Video URL မထည့်ထားပါ။")

    output = workdir / "downloaded_video.%(ext)s"

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format",
        "mp4",
        "-o",
        str(output),
        url.strip()
    ]

    code, out, err = run_cmd(cmd, timeout=900)

    if code != 0:
        raise RuntimeError(
            "Video Download မအောင်မြင်ပါ။\n\n" + err[-4000:]
        )

    files = list(workdir.glob("downloaded_video.*"))

    if not files:
        raise RuntimeError("Download ပြီးပေမယ့် video file မတွေ့ပါ။")

    return files[0]


def prepare_video(uploaded_video, video_url):
    workdir = new_job_dir()

    if video_url and video_url.strip():
        video_path = download_video(video_url, workdir)

    elif uploaded_video:
        source = Path(uploaded_video)
        video_path = workdir / safe_name(source.name)
        shutil.copy2(source, video_path)

    else:
        raise ValueError("Video Upload သို့မဟုတ် Video URL တစ်ခုခုထည့်ပါ။")

    duration = video_duration(video_path)

    if duration <= 0:
        raise ValueError("Video duration မဖတ်နိုင်ပါ။")

    if duration > MAX_VIDEO_SECONDS:
        raise ValueError(
            f"Video အများဆုံး {MAX_VIDEO_SECONDS} seconds "
            f"(5 minutes) အထိသာ ခွင့်ပြုထားပါတယ်။\n"
            f"သင့် video = {duration:.1f} seconds"
        )

    return str(video_path), workdir


# ============================================================
# GEMINI VIDEO ANALYSIS
# ============================================================

def upload_to_gemini(client, video_path):
    uploaded = client.files.upload(file=video_path)

    while True:
        current = client.files.get(name=uploaded.name)

        state = getattr(current.state, "name", "")

        if state == "ACTIVE":
            return current

        if state == "FAILED":
            raise RuntimeError("Gemini video processing FAILED.")

        time.sleep(3)


def analyze_video(
    api_key,
    model_name,
    uploaded_video,
    video_url,
    style
):
    global CURRENT_VIDEO
    global CURRENT_ANALYSIS
    global CURRENT_SCRIPT

    ensure_ffmpeg()

    video_path, workdir = prepare_video(uploaded_video, video_url)

    api_key = get_api_key(api_key)
    client = get_client(api_key)

    print("Uploading video to Gemini...")

    gemini_file = upload_to_gemini(client, video_path)

    style_instruction = {
        "TikTok": "Fast, exciting, hook-heavy TikTok recap style.",
        "Facebook": "Clear, emotional and easy-to-follow Facebook recap style.",
        "YouTube": "Detailed cinematic YouTube movie recap style.",
        "Short": "Very concise short-form recap style."
    }.get(style, "Engaging Burmese movie recap style.")

    prompt = f"""
You are an expert movie recap script writer.

Analyze the uploaded video carefully.

IMPORTANT:
- Use ONLY what actually happens in the video.
- Do NOT invent characters, events, locations, relationships or dialogue.
- Follow the real timeline from beginning to end.
- Pay attention to both visual actions and spoken audio.
- Identify important characters and their actions.
- Identify important scene changes.
- Mention timestamps for important events.
- If something is unclear, do not guess.
- Do not copy or directly translate the original dialogue.
- Rewrite it naturally in Burmese.
- Make it engaging and suitable for social media.
- Keep the story understandable.

Style:
{style_instruction}

FIRST provide a concise TIMELINE ANALYSIS.

Use this format:

[00:00-00:10]
What actually happens.

[00:10-00:20]
What actually happens.

Continue until the important events are covered.

THEN provide:

CHARACTERS:
- Character name/description
- Role if clearly known

IMPORTANT EVENTS:
- Event 1
- Event 2
- Event 3

Then provide:

BURMESE RECAP SCRIPT:

Write a natural Burmese recap.

Use narration for actions that are visible.

Use dialogue-style lines only when the meaning of the spoken conversation is clear.

Do not create fake dialogue.

Make the Burmese natural and easy to listen to.

Maximum approximately 5 minutes of narration.
"""

    response = client.models.generate_content(
        model=model_name,
        contents=[
            gemini_file,
            prompt
        ]
    )

    result = response.text or ""

    if not result.strip():
        raise RuntimeError("Gemini က script ပြန်မပေးပါ။")

    CURRENT_VIDEO = video_path
    CURRENT_ANALYSIS = result

    # Extract recap section if possible
    match = re.search(
        r"BURMESE RECAP SCRIPT\s*:?\s*(.*)",
        result,
        re.IGNORECASE | re.DOTALL
    )

    if match:
        script = match.group(1).strip()
    else:
        script = result.strip()

    CURRENT_SCRIPT = script

    return video_path, result, script


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_tts_text(text):
    text = text or ""

    text = re.sub(
        r"\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*[-–]\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]",
        "",
        text
    )

    text = re.sub(
        r"^\s*(Scene|Scene\s*\d+|Timeline|CHARACTERS|IMPORTANT EVENTS|BURMESE RECAP SCRIPT)\s*:?.*$",
        "",
        text,
        flags=re.MULTILINE | re.IGNORECASE
    )

    text = re.sub(r"^\s*[-•*]\s*", "", text, flags=re.MULTILINE)

    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# GEMINI SUBTITLE TRANSLATION
# ============================================================

def translate_script(api_key, model_name, text, language):
    if language == "Myanmar":
        return text

    api_key = get_api_key(api_key)
    client = get_client(api_key)

    target = {
        "English": "English",
        "Thai": "Thai"
    }.get(language, "Myanmar")

    prompt = f"""
Translate the following Burmese subtitle text into {target}.

Rules:
- Preserve meaning exactly.
- Do not add information.
- Do not remove information.
- Keep natural conversational language.
- Return ONLY the translated text.

TEXT:

{text}
"""

    response = client.models.generate_content(
        model=model_name,
        contents=prompt
    )

    return response.text.strip()


# ============================================================
# EDGE TTS
# ============================================================

VOICE_OPTIONS = {
    "မြန်မာ အမျိုးသား - Thiha": "my-MM-ThihaNeural",
    "မြန်မာ အမျိုးသမီး - Nilar": "my-MM-NilarNeural",
}


async def generate_tts_async(text, voice, speed, output):
    rate = int((speed - 1.0) * 100)

    if rate >= 0:
        rate_string = f"+{rate}%"
    else:
        rate_string = f"{rate}%"

    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate_string
    )

    await communicate.save(output)


def generate_tts(text, voice_label, speed):
    import asyncio

    if not text.strip():
        raise ValueError("TTS အတွက် စာသားမရှိပါ။")

    voice = VOICE_OPTIONS.get(
        voice_label,
        "my-MM-ThihaNeural"
    )

    workdir = new_job_dir()
    output = workdir / "burmese_voice.mp3"

    asyncio.run(
        generate_tts_async(
            text,
            voice,
            float(speed),
            str(output)
        )
    )

    if not output.exists():
        raise RuntimeError("TTS audio မထွက်ပါ။")

    return str(output)


# ============================================================
# SRT
# ============================================================

def format_srt_time(seconds):
    seconds = max(0, float(seconds))

    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)

    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_sentences(text):
    text = clean_tts_text(text)

    parts = re.split(
        r"(?<=[။!?])\s+|\n+",
        text
    )

    parts = [
        x.strip()
        for x in parts
        if x.strip()
    ]

    if not parts:
        return [text]

    return parts


def generate_srt_from_text(text, audio_path):
    duration = video_duration(audio_path)

    if duration <= 0:
        duration = max(5, len(text) / 8)

    sentences = split_sentences(text)

    weights = [
        max(1, len(s))
        for s in sentences
    ]

    total_weight = sum(weights)

    current = 0.0
    blocks = []

    for i, sentence in enumerate(sentences):
        portion = duration * weights[i] / total_weight

        start = current
        end = current + portion

        blocks.append(
            f"{i+1}\n"
            f"{format_srt_time(start)} --> {format_srt_time(end)}\n"
            f"{sentence}\n"
        )

        current = end

    return "\n".join(blocks)


def write_srt(srt_text):
    workdir = new_job_dir()
    path = workdir / "subtitle.srt"

    path.write_text(
        srt_text,
        encoding="utf-8"
    )

    return str(path)


# ============================================================
# ASS SUBTITLE
# ============================================================

def ass_color(hex_color):
    h = hex_color.strip().replace("#", "")

    if len(h) != 6:
        h = "FFFFFF"

    r = h[0:2]
    g = h[2:4]
    b = h[4:6]

    return f"&H00{b}{g}{r}"


def srt_to_ass(
    srt_path,
    font_size,
    text_color,
    outline_color,
    outline_width,
    x_position,
    y_position
):
    workdir = new_job_dir()
    ass_path = workdir / "subtitle.ass"

    text = Path(srt_path).read_text(
        encoding="utf-8"
    )

    lines = text.splitlines()

    entries = []
    current = []

    for line in lines:
        if line.strip() == "":
            if current:
                entries.append(current)
                current = []
        else:
            current.append(line)

    if current:
        entries.append(current)

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Noto Sans Myanmar,{font_size},{ass_color(text_color)},{ass_color(text_color)},{ass_color(outline_color)},&H80000000,0,0,0,0,100,100,0,0,1,{outline_width},0,2,20,20,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    for entry in entries:
        if len(entry) < 3:
            continue

        index = entry[0]
        timing = entry[1]

        match = re.match(
            r"(.+?)\s*-->\s*(.+)",
            timing
        )

        if not match:
            continue

        start = match.group(1).replace(",", ".")
        end = match.group(2).replace(",", ".")

        start = start.replace(":", ":", 1)
        end = end.replace(":", ":", 1)

        subtitle_text = "\\N".join(entry[2:])

        subtitle_text = (
            subtitle_text
            .replace("{", "\\{")
            .replace("}", "\\}")
        )

        if x_position is not None and y_position is not None:
            subtitle_text = (
                f"{{\\pos({int(x_position)},{int(y_position)})}}"
                + subtitle_text
            )

        ass_lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{subtitle_text}"
        )

    ass_path.write_text(
        "\n".join(ass_lines),
        encoding="utf-8"
    )

    return str(ass_path)


# ============================================================
# AUTO CUT
# ============================================================

def detect_silence_segments(video_path):
    """
    Detect long silent areas.

    This is a REAL applied auto-cut system.
    It does not pretend AI cut happened.
    """

    if not has_audio(video_path):
        return []

    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-af",
        "silencedetect=noise=-38dB:d=1.2",
        "-f",
        "null",
        "-"
    ]

    code, out, err = run_cmd(
        cmd,
        timeout=600
    )

    text = err

    starts = re.findall(
        r"silence_start:\s*([0-9.]+)",
        text
    )

    ends = re.findall(
        r"silence_end:\s*([0-9.]+)",
        text
    )

    result = []

    for s, e in zip(starts, ends):
        s = float(s)
        e = float(e)

        if e - s >= 1.2:
            result.append((s, e))

    return result


def build_keep_segments(duration, silences):
    if not silences:
        return [(0, duration)]

    segments = []

    current = 0.0

    for start, end in silences:

        # Keep tiny silences.
        if start - current > 0.15:
            segments.append(
                (current, start)
            )

        current = end

    if duration - current > 0.15:
        segments.append(
            (current, duration)
        )

    return segments


def auto_cut_video(video_path):
    duration = video_duration(video_path)

    if duration <= 0:
        return video_path

    silences = detect_silence_segments(video_path)

    if not silences:
        return video_path

    keep = build_keep_segments(
        duration,
        silences
    )

    # Do not destroy the whole video.
    if len(keep) <= 1:
        return video_path

    workdir = new_job_dir()

    parts = []

    for i, (start, end) in enumerate(keep):
        if end - start < 0.2:
            continue

        part = workdir / f"part_{i:03d}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", video_path,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            str(part)
        ]

        code, out, err = run_cmd(
            cmd,
            timeout=600
        )

        if code == 0 and part.exists():
            parts.append(part)

    if not parts:
        return video_path

    concat_file = workdir / "concat.txt"

    concat_file.write_text(
        "\n".join(
            f"file '{str(p).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'"
            for p in parts
        ),
        encoding="utf-8"
    )

    output = workdir / "auto_cut.mp4"

    code, out, err = run_cmd([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output)
    ], timeout=900)

    if code == 0 and output.exists():
        return str(output)

    return video_path


# ============================================================
# FILTER HELPERS
# ============================================================

def escape_filter_path(path):
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def build_video_filter(
    ratio,
    brightness,
    contrast,
    saturation,
    zoom,
    flip,
    blur_enabled,
    blur_x,
    blur_y,
    blur_w,
    blur_h,
    blur_strength,
    blur_color,
    subtitle_path,
    logo_path,
    logo_position,
    logo_size,
    logo_opacity,
    subtitle_font_size,
    subtitle_text_color,
    subtitle_outline_color,
    subtitle_outline_width,
    subtitle_x,
    subtitle_y
):
    filters = []

    # --------------------------------------------------------
    # SCALE / CROP RATIO
    # --------------------------------------------------------

    ratio_map = {
        "9:16": "ih*9/16:ih",
        "3:4": "ih*3/4:ih",
        "1:1": "ih:ih",
        "16:9": "iw:iw*9/16"
    }

    crop = ratio_map.get(
        ratio,
        "iw:ih"
    )

    filters.append(
        f"crop={crop}"
    )

    # --------------------------------------------------------
    # ZOOM
    # --------------------------------------------------------

    zoom_value = max(
        1.0,
        float(zoom)
    )

    if zoom_value > 1.001:
        filters.append(
            f"scale=ceil(iw*{zoom_value}/2)*2:"
            f"ceil(ih*{zoom_value}/2)*2"
        )

        filters.append(
            "crop=iw/1.0/1.0:ih/1.0/1.0"
        )

    # --------------------------------------------------------
    # FLIP
    # --------------------------------------------------------

    if flip:
        filters.append("hflip")

    # --------------------------------------------------------
    # COLOR
    # --------------------------------------------------------

    filters.append(
        f"eq=brightness={float(brightness)}:"
        f"contrast={float(contrast)}:"
        f"saturation={float(saturation)}"
    )

    chain = ",".join(filters)

    return chain


# ============================================================
# FINAL VIDEO RENDER
# ============================================================

def render_video(
    source_video,
    voice_audio,
    background_music,
    ratio,
    original_audio,
    original_volume,
    music_volume,
    brightness,
    contrast,
    saturation,
    zoom,
    flip,
    auto_cut,
    blur_enabled,
    blur_x,
    blur_y,
    blur_w,
    blur_h,
    blur_strength,
    blur_color,
    logo_file,
    logo_position,
    logo_size,
    logo_opacity,
    subtitle_file,
    subtitle_font_size,
    subtitle_text_color,
    subtitle_outline_color,
    subtitle_outline_width,
    subtitle_x,
    subtitle_y,
    preview_mode=False
):
    ensure_ffmpeg()

    if not source_video:
        raise ValueError("Video မရှိပါ။")

    source = source_video

    # --------------------------------------------------------
    # AUTO CUT
    # --------------------------------------------------------

    if auto_cut:
        source = auto_cut_video(source)

    workdir = new_job_dir()

    output = (
        workdir / "preview.mp4"
        if preview_mode
        else workdir / "Smart_Recap_Final.mp4"
    )

    # --------------------------------------------------------
    # INPUTS
    # --------------------------------------------------------

    cmd = [
        "ffmpeg",
        "-y",
        "-i", source
    ]

    source_has_audio = has_audio(source)

    voice_index = None
    music_index = None
    logo_index = None

    input_index = 1

    if voice_audio:
        cmd += [
            "-i",
            voice_audio
        ]

        voice_index = input_index
        input_index += 1

    if background_music:
        cmd += [
            "-stream_loop", "-1",
            "-i",
            background_music
        ]

        music_index = input_index
        input_index += 1

    if logo_file:
        cmd += [
            "-i",
            logo_file
        ]

        logo_index = input_index
        input_index += 1

    # --------------------------------------------------------
    # VIDEO FILTER
    # --------------------------------------------------------

    video_filter = build_video_filter(
        ratio,
        brightness,
        contrast,
        saturation,
        zoom,
        flip,
        blur_enabled,
        blur_x,
        blur_y,
        blur_w,
        blur_h,
        blur_strength,
        blur_color,
        subtitle_file,
        logo_file,
        logo_position,
        logo_size,
        logo_opacity,
        subtitle_font_size,
        subtitle_text_color,
        subtitle_outline_color,
        subtitle_outline_width,
        subtitle_x,
        subtitle_y
    )

    filter_parts = []

    filter_parts.append(
        f"[0:v]{video_filter}[base]"
    )

    current = "base"

    # --------------------------------------------------------
    # BLUR REGION
    # --------------------------------------------------------

    if blur_enabled:
        x = max(0, int(blur_x))
        y = max(0, int(blur_y))
        w = max(10, int(blur_w))
        h = max(10, int(blur_h))
        strength = max(1, int(blur_strength))

        blur_color_hex = (
            str(blur_color or "#000000")
            .replace("#", "")
        )

        if not re.match(
            r"^[0-9a-fA-F]{6}$",
            blur_color_hex
        ):
            blur_color_hex = "000000"

        filter_parts.append(
            f"[{current}]split=2[normal][blurinput]"
        )

        filter_parts.append(
            f"[blurinput]crop={w}:{h}:{x}:{y},"
            f"boxblur=luma_radius={strength}:"
            f"luma_power=1[blurred]"
        )

        # Slight color tint overlay.
        filter_parts.append(
            f"color=c=0x{blur_color_hex}@0.18:"
            f"s={w}x{h}:d=1[tint]"
        )

        filter_parts.append(
            "[blurred][tint]overlay=0:0[blurcolored]"
        )

        filter_parts.append(
            f"[normal][blurcolored]overlay={x}:{y}[blurout]"
        )

        current = "blurout"

    # --------------------------------------------------------
    # LOGO
    # --------------------------------------------------------

    if logo_index is not None:
        logo_x = "20"
        logo_y = "20"

        if logo_position == "Top Right":
            logo_x = "W-w-20"
            logo_y = "20"

        elif logo_position == "Bottom Left":
            logo_x = "20"
            logo_y = "H-h-20"

        elif logo_position == "Bottom Right":
            logo_x = "W-w-20"
            logo_y = "H-h-20"

        elif logo_position == "Center":
            logo_x = "(W-w)/2"
            logo_y = "(H-h)/2"

        logo_scale = max(
            5,
            min(100, float(logo_size))
        )

        opacity = max(
            0.0,
            min(1.0, float(logo_opacity))
        )

        filter_parts.append(
            f"[{logo_index}:v]format=rgba,"
            f"scale=iw*{logo_scale}/100:"
            f"ih*{logo_scale}/100,"
            f"colorchannelmixer=aa={opacity}[logo]"
        )

        filter_parts.append(
            f"[{current}][logo]overlay={logo_x}:{logo_y}[withlogo]"
        )

        current = "withlogo"

    # --------------------------------------------------------
    # SUBTITLE
    # --------------------------------------------------------

    if subtitle_file:
        escaped_sub = escape_filter_path(
            subtitle_file
        )

        filter_parts.append(
            f"[{current}]ass='{escaped_sub}'[subbed]"
        )

        current = "subbed"

    # --------------------------------------------------------
    # AUDIO
    # --------------------------------------------------------

    audio_parts = []

    if original_audio and source_has_audio:
        audio_parts.append(
            f"[0:a]volume={float(original_volume)}[orig]"
        )

    if voice_index is not None:
        audio_parts.append(
            f"[{voice_index}:a]volume=1[voice]"
        )

    if music_index is not None:
        audio_parts.append(
            f"[{music_index}:a]volume={float(music_volume)}[music]"
        )

    if len(audio_parts) > 0:
        filter_parts.extend(audio_parts)

    audio_labels = []

    if original_audio and source_has_audio:
        audio_labels.append("[orig]")

    if voice_index is not None:
        audio_labels.append("[voice]")

    if music_index is not None:
        audio_labels.append("[music]")

    if audio_labels:
        if len(audio_labels) == 1:
            audio_map = audio_labels[0]
        else:
            audio_map = "".join(audio_labels)

            filter_parts.append(
                f"{audio_map}"
                f"amix=inputs={len(audio_labels)}:"
                f"duration=longest:"
                f"dropout_transition=2,"
                f"aresample=async=1:first_pts=0[aout]"
            )

            audio_map = "[aout]"
    else:
        audio_map = None

    # --------------------------------------------------------
    # PREVIEW
    # --------------------------------------------------------

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{current}]"
    ]

    if audio_map:
        cmd += [
            "-map",
            audio_map
        ]
    else:
        cmd += [
            "-an"
        ]

    # --------------------------------------------------------
    # OUTPUT SETTINGS
    # --------------------------------------------------------

    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast" if preview_mode else "medium",
        "-crf",
        "26" if preview_mode else "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart"
    ]

    if preview_mode:
        cmd += [
            "-t",
            "15"
        ]

    cmd += [
        str(output)
    ]

    code, out, err = run_cmd(
        cmd,
        timeout=1800
    )

    if code != 0:
        raise RuntimeError(
            "FFmpeg Render Error:\n\n" +
            err[-7000:]
        )

    if not output.exists():
        raise RuntimeError("Output MP4 မထွက်ပါ။")

    return str(output)


# ============================================================
# PREVIEW
# ============================================================

def make_preview(
    source_video,
    voice_audio,
    background_music,
    ratio,
    original_audio,
    original_volume,
    music_volume,
    brightness,
    contrast,
    saturation,
    zoom,
    flip,
    auto_cut,
    blur_enabled,
    blur_x,
    blur_y,
    blur_w,
    blur_h,
    blur_strength,
    blur_color,
    logo_file,
    logo_position,
    logo_size,
    logo_opacity,
    subtitle_file,
    subtitle_font_size,
    subtitle_text_color,
    subtitle_outline_color,
    subtitle_outline_width,
    subtitle_x,
    subtitle_y
):
    return render_video(
        source_video,
        voice_audio,
        background_music,
        ratio,
        original_audio,
        original_volume,
        music_volume,
        brightness,
        contrast,
        saturation,
        zoom,
        flip,
        auto_cut,
        blur_enabled,
        blur_x,
        blur_y,
        blur_w,
        blur_h,
        blur_strength,
        blur_color,
        logo_file,
        logo_position,
        logo_size,
        logo_opacity,
        subtitle_file,
        subtitle_font_size,
        subtitle_text_color,
        subtitle_outline_color,
        subtitle_outline_width,
        subtitle_x,
        subtitle_y,
        preview_mode=True
    )


# ============================================================
# FULL PIPELINE
# ============================================================

def generate_voice_and_subtitle(
    script,
    api_key,
    model_name,
    voice,
    speed,
    subtitle_language
):
    global CURRENT_TTS
    global CURRENT_SRT

    if not script.strip():
        raise ValueError("Recap Script မရှိပါ။")

    clean = clean_tts_text(script)

    subtitle_text = clean

    if subtitle_language != "Myanmar":
        subtitle_text = translate_script(
            api_key,
            model_name,
            clean,
            subtitle_language
        )

    voice_path = generate_tts(
        clean,
        voice,
        speed
    )

    srt_text = generate_srt_from_text(
        subtitle_text,
        voice_path
    )

    srt_path = write_srt(
        srt_text
    )

    CURRENT_TTS = voice_path
    CURRENT_SRT = srt_path

    return (
        clean,
        voice_path,
        srt_text,
        srt_path
    )


# ============================================================
# UI
# ============================================================

CSS = """
body {
    background: #0f1117;
}

.gradio-container {
    max-width: 1450px !important;
}

#title {
    text-align: center;
}

textarea {
    font-size: 16px !important;
}

button {
    font-weight: 600 !important;
}
"""


with gr.Blocks(
    title=APP_NAME,
    css=CSS,
    theme=gr.themes.Soft()
) as app:

    gr.Markdown(
        """
        # 🎬 Smart Recap Studio
        ### Video → AI Recap → Burmese Voice → Subtitle → Auto Edit → Final MP4
        """,
        elem_id="title"
    )

    # ========================================================
    # API
    # ========================================================

    with gr.Row():

        api_key = gr.Textbox(
            label="Gemini API Key",
            type="password",
            placeholder="Render မှာ GEMINI_API_KEY ထည့်ထားရင် ဒီနေရာမလိုပါ",
            value=""
        )

        model_name = gr.Dropdown(
            label="Gemini Model",
            choices=[
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash"
            ],
            value=DEFAULT_MODEL
        )

    # ========================================================
    # TAB 1
    # ========================================================

    with gr.Tab("① Video Analysis + Recap"):

        with gr.Row():

            with gr.Column():

                video_upload = gr.Video(
                    label="Upload Video",
                    type="filepath"
                )

                video_url = gr.Textbox(
                    label="သို့မဟုတ် Video URL",
                    placeholder="YouTube / TikTok / Facebook / RedNote URL"
                )

                recap_style = gr.Dropdown(
                    label="Recap Style",
                    choices=[
                        "TikTok",
                        "Facebook",
                        "YouTube",
                        "Short"
                    ],
                    value="TikTok"
                )

                analyze_btn = gr.Button(
                    "🚀 Analyze Video + Generate Burmese Recap",
                    variant="primary"
                )

            with gr.Column():

                original_preview = gr.Video(
                    label="Original Video Preview"
                )

                analysis_output = gr.Textbox(
                    label="AI Video Analysis",
                    lines=20
                )

        recap_script = gr.Textbox(
            label="🎙 Burmese Recap Script",
            lines=18
        )

    # ========================================================
    # TAB 2
    # ========================================================

    with gr.Tab("② Burmese Voice + Subtitle"):

        with gr.Row():

            with gr.Column():

                voice_choice = gr.Dropdown(
                    label="Burmese Voice",
                    choices=list(VOICE_OPTIONS.keys()),
                    value="မြန်မာ အမျိုးသား - Thiha"
                )

                voice_speed = gr.Slider(
                    minimum=0.6,
                    maximum=1.5,
                    value=1.0,
                    step=0.05,
                    label="Voice Speed"
                )

                subtitle_language = gr.Dropdown(
                    label="Subtitle Language",
                    choices=[
                        "Myanmar",
                        "English",
                        "Thai"
                    ],
                    value="Myanmar"
                )

                generate_voice_btn = gr.Button(
                    "🔊 Generate Burmese Voice + Subtitle",
                    variant="primary"
                )

            with gr.Column():

                cleaned_script = gr.Textbox(
                    label="TTS Text",
                    lines=12
                )

                voice_preview = gr.Audio(
                    label="🎧 Voice Preview",
                    type="filepath"
                )

                srt_output = gr.Textbox(
                    label="Generated SRT",
                    lines=12
                )

                srt_file = gr.File(
                    label="Download SRT"
                )

    # ========================================================
    # TAB 3
    # ========================================================

    with gr.Tab("③ One Clip All-in-One Editor"):

        gr.Markdown(
            """
            ### 🎞️ Preview မှာ သတ်မှတ်ထားသမျှ Setting တွေကို အတူတကွ Render ပြထားပါမယ်။
            """

        )

        editor_video = gr.Video(
            label="Source Video",
            type="filepath"
        )

        with gr.Row():

            with gr.Column():

                ratio = gr.Dropdown(
                    label="Video Ratio",
                    choices=[
                        "9:16",
                        "3:4",
                        "1:1",
                        "16:9"
                    ],
                    value="9:16"
                )

                flip = gr.Checkbox(
                    label="↔ Flip Video",
                    value=False
                )

                zoom = gr.Slider(
                    label="🔍 Zoom",
                    minimum=1.0,
                    maximum=2.0,
                    value=1.0,
                    step=0.05
                )

                brightness = gr.Slider(
                    label="☀ Brightness",
                    minimum=-1,
                    maximum=1,
                    value=0,
                    step=0.05
                )

                contrast = gr.Slider(
                    label="Contrast",
                    minimum=0.5,
                    maximum=2,
                    value=1,
                    step=0.05
                )

                saturation = gr.Slider(
                    label="Saturation",
                    minimum=0,
                    maximum=2,
                    value=1,
                    step=0.05
                )

                auto_cut = gr.Checkbox(
                    label="✂ AI/Auto Cut — Long Silent Sections Remove",
                    value=False
                )

            with gr.Column():

                original_audio = gr.Checkbox(
                    label="Original Audio ON",
                    value=True
                )

                original_volume = gr.Slider(
                    label="Original Audio Volume",
                    minimum=0,
                    maximum=2,
                    value=0.25,
                    step=0.05
                )

                background_music = gr.Audio(
                    label="Background Music",
                    type="filepath"
                )

                music_volume = gr.Slider(
                    label="Background Music Volume",
                    minimum=0,
                    maximum=1,
                    value=0.08,
                    step=0.01
                )

        # ----------------------------------------------------
        # BLUR
        # ----------------------------------------------------

        gr.Markdown("## 🟦 Blur")

        with gr.Row():

            with gr.Column():

                blur_enabled = gr.Checkbox(
                    label="Enable Blur",
                    value=False
                )

                blur_x = gr.Number(
                    label="Blur X",
                    value=0
                )

                blur_y = gr.Number(
                    label="Blur Y",
                    value=0
                )

            with gr.Column():

                blur_w = gr.Number(
                    label="Blur Width",
                    value=250
                )

                blur_h = gr.Number(
                    label="Blur Height",
                    value=150
                )

                blur_strength = gr.Slider(
                    label="Blur Strength",
                    minimum=1,
                    maximum=30,
                    value=10,
                    step=1
                )

                blur_color = gr.ColorPicker(
                    label="Blur Tint Color",
                    value="#000000"
                )

        # ----------------------------------------------------
        # LOGO
        # ----------------------------------------------------

        gr.Markdown("## 🖼 Logo")

        with gr.Row():

            logo_file = gr.File(
                label="Upload Logo",
                type="filepath"
            )

            logo_position = gr.Dropdown(
                label="Logo Position",
                choices=[
                    "Top Left",
                    "Top Right",
                    "Bottom Left",
                    "Bottom Right",
                    "Center"
                ],
                value="Top Left"
            )

            logo_size = gr.Slider(
                label="Logo Size %",
                minimum=5,
                maximum=100,
                value=25,
                step=1
            )

            logo_opacity = gr.Slider(
                label="Logo Opacity",
                minimum=0,
                maximum=1,
                value=1,
                step=0.05
            )

        # ----------------------------------------------------
        # SUBTITLE
        # ----------------------------------------------------

        gr.Markdown("## 🔤 Subtitle")

        with gr.Row():

            subtitle_font_size = gr.Slider(
                label="Font Size",
                minimum=20,
                maximum=100,
                value=52,
                step=1
            )

            subtitle_text_color = gr.ColorPicker(
                label="Text Color",
                value="#FFFFFF"
            )

            subtitle_outline_color = gr.ColorPicker(
                label="Outline Color",
                value="#000000"
            )

            subtitle_outline_width = gr.Slider(
                label="Outline Thickness",
                minimum=0,
                maximum=10,
                value=3,
                step=1
            )

        with gr.Row():

            subtitle_x = gr.Slider(
                label="Subtitle X",
                minimum=0,
                maximum=1080,
                value=540,
                step=5
            )

            subtitle_y = gr.Slider(
                label="Subtitle Y",
                minimum=0,
                maximum=1920,
                value=1700,
                step=5
            )

        # ----------------------------------------------------
        # PREVIEW + FINAL
        # ----------------------------------------------------

        with gr.Row():

            preview_btn = gr.Button(
                "👀 Preview 15 Seconds",
                variant="secondary"
            )

            render_btn = gr.Button(
                "🎬 Render FINAL MP4",
                variant="primary"
            )

        preview_video = gr.Video(
            label="🎞️ Rendered Preview"
        )

        final_video = gr.Video(
            label="✅ FINAL VIDEO"
        )

        final_download = gr.File(
            label="⬇ Download Final MP4"
        )

        status = gr.Textbox(
            label="Status",
            interactive=False
        )

    # ========================================================
    # EVENTS
    # ========================================================

    analyze_btn.click(
        fn=analyze_video,
        inputs=[
            api_key,
            model_name,
            video_upload,
            video_url,
            recap_style
        ],
        outputs=[
            editor_video,
            analysis_output,
            recap_script
        ]
    )

    generate_voice_btn.click(
        fn=generate_voice_and_subtitle,
        inputs=[
            recap_script,
            api_key,
            model_name,
            voice_choice,
            voice_speed,
            subtitle_language
        ],
        outputs=[
            cleaned_script,
            voice_preview,
            srt_output,
            srt_file
        ]
    )

    # ========================================================
    # COMMON EDITOR INPUTS
    # ========================================================

    editor_inputs = [
        editor_video,
        voice_preview,
        background_music,
        ratio,
        original_audio,
        original_volume,
        music_volume,
        brightness,
        contrast,
        saturation,
        zoom,
        flip,
        auto_cut,
        blur_enabled,
        blur_x,
        blur_y,
        blur_w,
        blur_h,
        blur_strength,
        blur_color,
        logo_file,
        logo_position,
        logo_size,
        logo_opacity,
        srt_file,
        subtitle_font_size,
        subtitle_text_color,
        subtitle_outline_color,
        subtitle_outline_width,
        subtitle_x,
        subtitle_y
    ]

    preview_btn.click(
        fn=make_preview,
        inputs=editor_inputs,
        outputs=[
            preview_video
        ]
    )

    def final_render_wrapper(*args):
        result = render_video(
            *args,
            preview_mode=False
        )

        return result, result, "✅ Final MP4 Render ပြီးပါပြီ။"

    render_btn.click(
        fn=final_render_wrapper,
        inputs=editor_inputs,
        outputs=[
            final_video,
            final_download,
            status
        ]
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "7860")
    )

    app.launch(
        server_name="0.0.0.0",
        server_port=port
    )
