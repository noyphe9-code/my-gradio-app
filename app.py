import gradio as gr
import edge_tts
import asyncio
import os
import re
import time
import zipfile
import subprocess
import yt_dlp
import random

from google import genai


# =========================================================
# AI MOVIE RECAP STUDIO PRO
# =========================================================

APP_TITLE = "AI Movie Recap Studio Pro"

# Maximum video length
MAX_VIDEO_MINUTES = 10

# Gemini API Key
SAVED_API_KEY = ""


# =========================================================
# GEMINI MODELS
# =========================================================
#
# Primary:
#   gemini-3.6-flash
#
# Fallback:
#   gemini-3.5-flash
#   gemini-3.1-flash-lite
#
# If one model is temporarily overloaded,
# the program automatically retries and then
# switches to the next model.
# =========================================================

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]


# =========================================================
# BURMESE TTS VOICES
# =========================================================

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural":
        "my-MM-ThihaNeural",

    "Nilar (အမျိုးသမီးအသံ) - Natural":
        "my-MM-NilarNeural",
}


# =========================================================
# API KEY
# =========================================================

def save_api_key(api_key):

    global SAVED_API_KEY

    if api_key and api_key.strip():

        SAVED_API_KEY = api_key.strip()

        return (
            "✅ Gemini API Key ကို "
            "အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
        )

    return (
        "⚠️ Gemini API Key ထည့်ပေးပါ။"
    )


# =========================================================
# VIDEO DURATION
# =========================================================

def get_video_duration(video_path):

    if not video_path:
        return None

    if not os.path.exists(video_path):
        return None

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:

            value = result.stdout.strip()

            if value:
                return float(value)

    except Exception as e:

        print(
            "Duration Error:",
            e
        )

    return None


def validate_video_duration(video_path):

    duration = get_video_duration(
        video_path
    )

    if duration is None:

        return True, (
            "ℹ️ Video duration ကို "
            "အတည်ပြု၍ မရသေးပါ။"
        )

    minutes = duration / 60

    if minutes > MAX_VIDEO_MINUTES:

        return (
            False,
            (
                f"⚠️ Video သည် "
                f"{minutes:.1f} မိနစ်ရှိပါသည်။\n\n"
                f"အများဆုံး "
                f"{MAX_VIDEO_MINUTES} မိနစ်အထိသာ "
                f"အသုံးပြုနိုင်ပါသည်။"
            ),
        )

    return (
        True,
        (
            f"✅ Video Length: "
            f"{minutes:.1f} မိနစ်"
        ),
    )


# =========================================================
# CLEAN SCRIPT FOR TTS
# =========================================================

def clean_script_for_tts(script_text):

    if not script_text:
        return ""

    lines = script_text.splitlines()

    cleaned = []

    for line in lines:

        line = line.strip()

        if not line:
            continue

        # Markdown cleanup
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("`", "")

        # Remove technical labels only
        line = re.sub(
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue)\]\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )

        # Remove explicit narrator label only
        line = re.sub(
            r"^\s*Narrator\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )

        # DO NOT remove normal dialogue.
        #
        # Example:
        #
        # "နင် ဒီကို ဘာလာလုပ်တာလဲ?"
        #
        # must remain.

        if line.lower() in [
            "movie recap",
            "recap script",
            "burmese recap script",
            "script",
        ]:
            continue

        if line.startswith("---"):
            continue

        if line.strip():
            cleaned.append(
                line.strip()
            )

    return "\n".join(cleaned)


# =========================================================
# SRT
# =========================================================

def seconds_to_srt_time(seconds):

    seconds = max(
        0,
        int(seconds)
    )

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    secs = seconds % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},000"
    )


def generate_srt_and_zip(script_text):

    clean_text = clean_script_for_tts(
        script_text
    )

    if not clean_text:
        return None, None

    lines = [
        x.strip()
        for x in clean_text.splitlines()
        if x.strip()
    ]

    srt_content = ""

    current_time = 0

    subtitle_index = 1

    for line in lines:

        char_count = len(line)

        duration = max(
            2,
            min(
                8,
                round(
                    char_count / 11
                )
            ),
        )

        start_time = current_time

        end_time = (
            current_time +
            duration
        )

        srt_content += (
            f"{subtitle_index}\n"
            f"{seconds_to_srt_time(start_time)} "
            f"--> "
            f"{seconds_to_srt_time(end_time)}\n"
            f"{line}\n\n"
        )

        current_time = end_time

        subtitle_index += 1

    srt_filename = (
        "myanmar_recap_subtitle.srt"
    )

    zip_filename = (
        "myanmar_recap_subtitle.zip"
    )

    with open(
        srt_filename,
        "w",
        encoding="utf-8-sig",
    ) as f:

        f.write(
            srt_content
        )

    with zipfile.ZipFile(
        zip_filename,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as zipf:

        zipf.write(
            srt_filename,
            arcname=srt_filename,
        )

    return (
        srt_filename,
        zip_filename,
    )


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_video_from_link(link):

    if not link:
        return None

    link = link.strip()

    if not link:
        return None

    output_template = (
        "temp_movie_recap.%(ext)s"
    )

    ydl_opts = {

        "format":
            "best[ext=mp4]/best",

        "outtmpl":
            output_template,

        "quiet":
            True,

        "no_warnings":
            True,

        "noplaylist":
            True,

        "overwrites":
            True,

        "merge_output_format":
            "mp4",
    }

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                link,
                download=True
            )

            filename = (
                ydl.prepare_filename(
                    info
                )
            )

            if os.path.exists(
                filename
            ):
                return filename

            base = os.path.splitext(
                filename
            )[0]

            possible_files = [
                base + ".mp4",
                base + ".mkv",
                base + ".webm",
                base + ".mov",
            ]

            for file_path in possible_files:

                if os.path.exists(
                    file_path
                ):
                    return file_path

    except Exception as e:

        print(
            "Download Error:",
            e
        )

    return None


# =========================================================
# RATIO CSS
# =========================================================

def get_ratio_css(ratio):

    configs = {

        "9:16": {
            "aspect": "9 / 16",
            "max_width": "360px",
        },

        "3:4": {
            "aspect": "3 / 4",
            "max_width": "420px",
        },

        "1:1": {
            "aspect": "1 / 1",
            "max_width": "500px",
        },

        "16:9": {
            "aspect": "16 / 9",
            "max_width": "650px",
        },
    }

    cfg = configs.get(
        ratio,
        configs["9:16"]
    )

    return f"""
<style id="tab1-ratio-style">

#tab1_preview_container {{
    width: 100% !important;
    max-width: {cfg["max_width"]} !important;
    margin-left: auto !important;
    margin-right: auto !important;
}}

#tab1_preview_container .wrap {{
    width: 100% !important;
}}

#tab1_preview_container .video-container {{
    width: 100% !important;
    aspect-ratio: {cfg["aspect"]} !important;
    height: auto !important;
    min-height: 0 !important;
    max-height: none !important;
    overflow: hidden !important;
    background: #000 !important;
    border-radius: 10px !important;
}}

#tab1_preview_container video {{
    width: 100% !important;
    height: 100% !important;
    aspect-ratio: {cfg["aspect"]} !important;
    object-fit: contain !important;
    background: #000 !important;
    display: block !important;
}}

</style>

<script>

(function() {{

    const ratio =
        "{cfg["aspect"]}";

    function applyRatio() {{

        const root =
            document.querySelector(
                "#tab1_preview_container"
            );

        if (!root) return;

        const containers =
            root.querySelectorAll(
                ".video-container, video"
            );

        containers.forEach(
            function(el) {{

                el.style.aspectRatio =
                    ratio;

                el.style.width =
                    "100%";

                if (
                    el.tagName ===
                    "VIDEO"
                ) {{

                    el.style.height =
                        "100%";

                    el.style.objectFit =
                        "contain";
                }}

            }}
        );
    }}

    applyRatio();

    setTimeout(
        applyRatio,
        100
    );

    setTimeout(
        applyRatio,
        300
    );

    setTimeout(
        applyRatio,
        700
    );

    setTimeout(
        applyRatio,
        1200
    );

    setTimeout(
        applyRatio,
        2000
    );

    const observer =
        new MutationObserver(
            function() {{
                applyRatio();
            }}
        );

    const root =
        document.querySelector(
            "#tab1_preview_container"
        );

    if (root) {{

        observer.observe(
            root,
            {{
                childList: true,
                subtree: true
            }}
        );

    }}

}})();

</script>
"""


# =========================================================
# PREVIEW
# =========================================================

def preview_uploaded_video(
    video_file
):

    if not video_file:
        return None

    return video_file


def load_tab1_link(link):

    if not link:
        return None

    video = (
        download_video_from_link(
            link
        )
    )

    return video


# =========================================================
# MOVIE RECAP PROMPT
# =========================================================

def build_recap_prompt(
    selected_ratio
):

    return f"""

သင်သည် Professional Movie Recap Creator
တစ်ယောက်ဖြစ်သည်။

ပေးထားသော Video ကို အစမှအဆုံးအထိ
သေချာကြည့်ရှု၊ နားထောင်ပြီး Video ထဲတွင်
တကယ်ဖြစ်ပျက်နေသော အဖြစ်အပျက်များကို
အခြေခံ၍ သဘာဝကျသော မြန်မာ Movie Recap
Script တစ်ခုရေးပါ။

Target Aspect Ratio = {selected_ratio}

==================================================
အရေးကြီးဆုံး STYLE
==================================================

Narrator တစ်ယောက်တည်းက ပုံပြင်ပြောသလို
အစအဆုံး မရေးရ။

အောက်ပါပုံစံကို ဦးစားပေးရမည် -

VISUAL ACTION
+
NARRATOR DESCRIPTION
+
ACTUAL CHARACTER DIALOGUE
+
CHARACTER REACTION
+
NEXT ACTION
+
NEXT DIALOGUE

ဥပမာ -

ကောင်လေးက အိမ်ထဲကို ဝင်လာပြီး
မိန်းကလေးရှေ့မှာ ရပ်လိုက်ပါတယ်။
သူ့မျက်နှာကိုကြည့်ရတာ တစ်ခုခုအရေးကြီးတဲ့
ကိစ္စရှိနေသလိုပါပဲ။

နင် ဒီကို ဘာလာလုပ်တာလဲ?

ငါ မင်းကို ပြောစရာရှိလို့။

သူတို့နှစ်ယောက် စကားပြောနေတုန်းမှာပဲ
အပြင်ဘက်ကနေ အသံတစ်ခု ထွက်လာပါတယ်။
အဲ့ဒီအသံကြောင့် နှစ်ယောက်စလုံး
တံခါးဘက်ကို လှည့်ကြည့်လိုက်ကြပါတယ်။

ဒီလိုပုံစံကို Video တစ်ခုလုံးအတွက်
အသုံးပြုပါ။

==================================================
1. VIDEO ကို အစမှအဆုံးကြည့်ပါ
==================================================

Video အစပိုင်းကိုပဲ ကြည့်ပြီး
ဇာတ်လမ်းကို မခန့်မှန်းရ။

Video တစ်ခုလုံးကို Analyze လုပ်ပါ။

Beginning
Development
Conflict
Important Events
Character Reactions
Climax
Ending

အားလုံးကို ထည့်ပါ။

==================================================
2. VISUAL STORYTELLING
==================================================

Video ထဲမှာ မြင်ရတဲ့ Action ကို
သဘာဝကျကျ Narrator နဲ့ ရှင်းပြပါ။

ဥပမာ -

ကောင်လေးက တံခါးကို ဖြည်းဖြည်းဖွင့်ပြီး
အခန်းထဲကို ဝင်လာပါတယ်။

မိန်းကလေးက သူ့ကို တိတ်တိတ်လေး
ကြည့်နေပါတယ်။

ကောင်လေးက စားပွဲပေါ်က ဖုန်းကို
ကောက်ယူလိုက်ပါတယ်။

သူမက ချက်ချင်း တံခါးဘက်ကို
လှည့်ကြည့်လိုက်ပါတယ်။

Video ထဲမှာ မမြင်ရတဲ့ Action ကို
မဖန်တီးရ။

==================================================
3. CHARACTER DIALOGUE
==================================================

Character တစ်ယောက်က Video ထဲမှာ
တကယ်စကားပြောနေပါက Dialogue ကို
ထည့်ပါ။

ဥပမာ -

နင် ဒီကို ဘာလာလုပ်တာလဲ?

ငါ မင်းကို ပြောစရာရှိလို့။

မင်း အခုချက်ချင်း ဒီကနေ ထွက်သွား!

Dialogue ကို Narrator စကားနဲ့
မရောပါနှင့်။

==================================================
4. FOREIGN LANGUAGE DIALOGUE
==================================================

English
Chinese
Japanese
Korean
Thai
သို့မဟုတ် အခြားဘာသာစကားဖြင့်
Character က ပြောပါက

အဓိပ္ပာယ်မပျက်စေဘဲ
သဘာဝကျသော မြန်မာစကားပြောပုံစံဖြင့်
ဘာသာပြန်ပါ။

စာအုပ်ထဲက ဘာသာပြန်သလို မရေးရ။

==================================================
5. DIALOGUE မကြားရပါက
==================================================

အသံမရှင်းလင်းပါက
ကိုယ်တိုင် Dialogue မဖန်တီးရ။

ခန့်မှန်းပြီး ဇာတ်လမ်းအသစ် မထည့်ရ။

အဲ့ဒီနေရာမှာ မြင်ရတဲ့ Action ကိုသာ
Narrator နဲ့ ပြောပါ။

==================================================
6. CHARACTER REACTION
==================================================

Video ထဲမှာ တကယ်မြင်ရတဲ့
မျက်နှာအမူအရာ၊ ကိုယ်ဟန်၊ လှုပ်ရှားမှုကို
သဘာဝကျကျ ဖော်ပြပါ။

ဥပမာ -

အဲ့ဒီစကားကို ကြားလိုက်တာနဲ့
မိန်းကလေးရဲ့မျက်နှာက ချက်ချင်း
တင်းမာသွားပါတယ်။

ကောင်လေးက ခဏတိတ်သွားပြီး
သူမကို စိုက်ကြည့်နေပါတယ်။

သူမက ဘာပြန်ပြောရမလဲ မသိသလို
ခဏငြိမ်သွားပါတယ်။

ဒါပေမယ့် Video ထဲမှာ မမြင်ရတဲ့
အတွေးတွေကို မဖန်တီးရ။

==================================================
7. SCENE TRANSITION
==================================================

Scene တစ်ခုကနေ နောက်တစ်ခုကို
ပုံပြင်စာအုပ်လို မပြောင်းရ။

Video ထဲက Action အတိုင်း
သဘာဝကျကျ ဆက်သွားပါ။

ဥပမာ -

သူတို့နှစ်ယောက် စကားပြောနေတုန်းမှာပဲ
အပြင်ဘက်ကနေ အသံတစ်ခု ထွက်လာပါတယ်။

အသံကြားလိုက်တာနဲ့ နှစ်ယောက်စလုံး
တံခါးဘက်ကို လှည့်ကြည့်လိုက်ကြပါတယ်။

==================================================
8. NO INVENTED INFORMATION
==================================================

Video ထဲမှာ မပါတဲ့ -

Character Name
Relationship
Background
Secret
Motivation
Location
Emotion
Dialogue
Event
Ending

တို့ကို ကိုယ်တိုင် မဖန်တီးရ။

Video ထဲမှာ မြင်ရ၊ ကြားရ၊
သိသာစွာ ခွဲခြားနိုင်တဲ့ အရာများကိုသာ
အသုံးပြုပါ။

==================================================
9. NO GENERIC STORYBOOK
==================================================

အောက်ပါပုံစံကို တတ်နိုင်သမျှ မသုံးရ -

တစ်နေ့မှာ...
အဲ့ဒီနောက်...
ဒီဇာတ်ကားမှာတော့...
အဓိကဇာတ်ကောင်က...
နောက်ဆုံးမှာ အရာအားလုံးက...

အစား Video ထဲက Action ကို
တိုက်ရိုက် ပြန်လည်အသက်ဝင်အောင်
ပြောပြပါ။

==================================================
10. NATURAL BURMESE
==================================================

မြန်မာလူတစ်ယောက်က Movie ကို
ကြည့်ပြီး သူငယ်ချင်းတစ်ယောက်ကို
ပြန်ရှင်းပြနေသလို ရေးပါ။

စာအုပ်ပုံစံ မရေးရ။

Machine Translation ပုံစံ မဖြစ်ရ။

==================================================
11. ENGAGING
==================================================

TikTok
Facebook Reels
YouTube Shorts

ကြည့်သူတွေ ဆက်ကြည့်ချင်အောင်
ဇာတ်လမ်း Rhythm ကို ထိန်းပါ။

အရေးကြီးတဲ့ Action နဲ့ Dialogue ကို
ဦးစားပေးပါ။

Suspense ရှိရင် suspense ကို ထိန်းပါ။

Surprise ရှိရင် surprise ကို မဖျက်ပါနှင့်။

==================================================
12. TTS FRIENDLY
==================================================

AI Voice နဲ့ ဖတ်မည်ဖြစ်သောကြောင့် -

စာကြောင်းများ အလွန်ရှည်မနေစေရ။

Comma နှင့် Full Stop ကို သဘာဝကျကျ
အသုံးပြုပါ။

Dialogue ကို သီးခြားစာကြောင်းများအဖြစ်
ရေးပါ။

ဖတ်ရခက်တဲ့ စာကြောင်းများ မရေးရ။

==================================================
13. NO TECHNICAL LABELS
==================================================

အောက်ပါ Label များ မထည့်ရ -

[Visual]
[Scene]
[Action]
[Narrator]
[Dialogue]
[Camera]
[Audio]
[Video]

Script ကို Natural Burmese
စာသားပုံစံနဲ့သာ ထုတ်ပါ။

==================================================
14. FULL ENDING
==================================================

Video အဆုံးထိ ရောက်အောင် ရေးပါ။

ဇာတ်လမ်းကို အလယ်မှာ မရပ်ပါနှင့်။

==================================================
15. FINAL CHECK
==================================================

Final Script မထုတ်ခင် စစ်ဆေးပါ -

✓ Video အစမှအဆုံး ကြည့်ပြီးပြီလား?
✓ Actual Action ပါလား?
✓ Actual Dialogue ပါလား?
✓ Dialogue ကို သဘာဝကျကျ ဘာသာပြန်ထားလား?
✓ Narrator-only မဖြစ်ဘူးလား?
✓ Dialogue ကို ကိုယ်တိုင်ဖန်တီးထားတာ မရှိဘူးလား?
✓ Video ထဲမပါတဲ့ Event မထည့်ထားဘူးလား?
✓ Character Reaction က Video နဲ့ကိုက်ညီလား?
✓ Ending ပါလား?
✓ TTS ဖတ်လို့ကောင်းလား?
✓ Natural Burmese ဖြစ်လား?

အားလုံးကို စစ်ပြီးနောက်
Final Natural Burmese Movie Recap Script
ကိုသာ ပြန်ပေးပါ။
"""


# =========================================================
# 503 / 429 / 500 / 504 DETECTION
# =========================================================

def get_error_text(error):

    return str(error).lower()


def is_retryable_error(error):

    text = get_error_text(
        error
    )

    retry_words = [
        "503",
        "unavailable",
        "high demand",
        "service unavailable",
        "429",
        "resource_exhausted",
        "500",
        "internal server error",
        "504",
        "deadline_exceeded",
        "timeout",
        "temporarily",
    ]

    return any(
        word in text
        for word in retry_words
    )


# =========================================================
# GEMINI GENERATION WITH RETRY + FALLBACK
# =========================================================

def generate_with_retry(
    client,
    uploaded_file,
    prompt,
):

    # Retry delays
    retry_delays = [
        5,
        10,
        20,
        40,
        60,
    ]

    last_error = None

    # -----------------------------------------
    # Try each model
    # -----------------------------------------

    for model_index, model_name in enumerate(
        GEMINI_MODELS
    ):

        print(
            "\n================================"
        )

        print(
            f"Trying Gemini model: "
            f"{model_name}"
        )

        print(
            "================================"
        )

        # -------------------------------------
        # Each model gets retries
        # -------------------------------------

        for attempt in range(
            len(retry_delays) + 1
        ):

            try:

                print(
                    f"Attempt "
                    f"{attempt + 1} "
                    f"for {model_name}"
                )

                response = (
                    client.models.generate_content(
                        model=model_name,
                        contents=[
                            uploaded_file,
                            prompt,
                        ],
                    )
                )

                if response and response.text:

                    print(
                        f"✅ Success: "
                        f"{model_name}"
                    )

                    return (
                        response.text,
                        model_name,
                    )

                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            except Exception as e:

                last_error = e

                print(
                    f"❌ {model_name} "
                    f"attempt "
                    f"{attempt + 1} failed:"
                )

                print(e)

                # ---------------------------------
                # If not retryable, stop immediately
                # ---------------------------------

                if not is_retryable_error(e):

                    print(
                        "Non-retryable error."
                    )

                    break

                # ---------------------------------
                # Retry current model
                # ---------------------------------

                if attempt < len(
                    retry_delays
                ):

                    delay = (
                        retry_delays[
                            attempt
                        ]
                    )

                    # Small random jitter
                    jitter = random.uniform(
                        0,
                        2
                    )

                    total_wait = (
                        delay +
                        jitter
                    )

                    print(
                        f"⏳ Waiting "
                        f"{total_wait:.1f}s "
                        f"before retry..."
                    )

                    time.sleep(
                        total_wait
                    )

                else:

                    print(
                        f"⚠️ "
                        f"{model_name} "
                        f"still unavailable."
                    )

        # -------------------------------------
        # Move to next model
        # -------------------------------------

        if model_index < len(
            GEMINI_MODELS
        ) - 1:

            print(
                "\n🔄 Switching to "
                f"fallback model..."
            )

            time.sleep(2)

    # =====================================================
    # ALL MODELS FAILED
    # =====================================================

    raise RuntimeError(
        "All Gemini models failed.\n\n"
        f"Last error:\n{last_error}"
    )


# =========================================================
# MAIN VIDEO ANALYSIS
# =========================================================

def analyze_and_generate_script(
    video_file,
    video_link,
    ratio_choice,
):

    global SAVED_API_KEY

    # -----------------------------------------
    # API KEY
    # -----------------------------------------

    if not SAVED_API_KEY:

        return (
            "",
            "⚠️ Gemini API Key မရှိသေးပါ။ "
            "API Key Setting မှာ အရင်ထည့်ပါ။",
            None,
            None,
        )

    # -----------------------------------------
    # VIDEO
    # -----------------------------------------

    target_media = None

    if video_file:

        target_media = video_file

    elif video_link:

        target_media = (
            download_video_from_link(
                video_link
            )
        )

    if not target_media:

        return (
            "",
            "⚠️ Video File တင်ပါ "
            "သို့မဟုတ် Video URL ထည့်ပါ။",
            None,
            None,
        )

    if not os.path.exists(
        target_media
    ):

        return (
            "",
            "⚠️ Video File ကို ရှာမတွေ့ပါ။",
            None,
            None,
        )

    # -----------------------------------------
    # DURATION
    # -----------------------------------------

    valid, duration_message = (
        validate_video_duration(
            target_media
        )
    )

    if not valid:

        return (
            "",
            duration_message,
            None,
            None,
        )

    # -----------------------------------------
    # GEMINI CLIENT
    # -----------------------------------------

    try:

        client = genai.Client(
            api_key=SAVED_API_KEY
        )

    except Exception as e:

        return (
            "",
            (
                "⚠️ Gemini Client Error:\n\n"
                f"{str(e)}"
            ),
            None,
            None,
        )

    # -----------------------------------------
    # UPLOAD
    # -----------------------------------------

    try:

        print(
            "\n🎬 Uploading video to Gemini..."
        )

        uploaded_file = (
            client.files.upload(
                file=target_media
            )
        )

        print(
            "📤 Upload complete."
        )

    except Exception as e:

        return (
            "",
            (
                "⚠️ Video Upload Error:\n\n"
                f"{str(e)}"
            ),
            None,
            None,
        )

    # -----------------------------------------
    # WAIT FOR VIDEO PROCESSING
    # -----------------------------------------

    try:

        processing_start = time.time()

        while True:

            state_name = (
                uploaded_file.state.name
                if uploaded_file.state
                else ""
            )

            print(
                "Gemini File State:",
                state_name
            )

            if state_name == "ACTIVE":

                break

            if state_name == "FAILED":

                return (
                    "",
                    (
                        "⚠️ Gemini Video Processing "
                        "မအောင်မြင်ပါ။"
                    ),
                    None,
                    None,
                )

            elapsed = (
                time.time() -
                processing_start
            )

            # Safety timeout:
            # 15 minutes
            if elapsed > 900:

                return (
                    "",
                    (
                        "⚠️ Gemini Video Processing "
                        "အချိန်အလွန်ကြာနေပါသည်။"
                    ),
                    None,
                    None,
                )

            time.sleep(5)

            uploaded_file = (
                client.files.get(
                    name=uploaded_file.name
                )
            )

    except Exception as e:

        return (
            "",
            (
                "⚠️ Video Processing Error:\n\n"
                f"{str(e)}"
            ),
            None,
            None,
        )

    # -----------------------------------------
    # PROMPT
    # -----------------------------------------

    prompt = build_recap_prompt(
        ratio_choice
    )

    # -----------------------------------------
    # GENERATE
    # -----------------------------------------

    try:

        script_text, used_model = (
            generate_with_retry(
                client,
                uploaded_file,
                prompt,
            )
        )

        # -------------------------------------
        # CLEAN
        # -------------------------------------

        clean_text = (
            clean_script_for_tts(
                script_text
            )
        )

        if not clean_text:

            return (
                "",
                (
                    "⚠️ Gemini က Script "
                    "ပြန်မပေးပါ။"
                ),
                None,
                None,
            )

        # -------------------------------------
        # SRT
        # -------------------------------------

        srt_file, zip_file = (
            generate_srt_and_zip(
                clean_text
            )
        )

        # -------------------------------------
        # STATUS
        # -------------------------------------

        status = (
            "## ✅ Script Generate ပြီးပါပြီ\n\n"
            f"{duration_message}\n\n"
            f"🤖 **Model:** `{used_model}`\n\n"
            "🎬 **Style:** Narrator + "
            "Character Dialogue + "
            "Visual Storytelling\n\n"
            "✅ Video အစမှအဆုံးအထိ "
            "Analyze လုပ်ထားပါသည်။"
        )

        return (
            clean_text,
            status,
            srt_file,
            zip_file,
        )

    except Exception as e:

        error_text = str(e)

        # -------------------------------------
        # Friendly 503 message
        # -------------------------------------

        if (
            "503" in error_text
            or
            "UNAVAILABLE" in error_text
            or
            "high demand" in error_text.lower()
        ):

            friendly_message = (
                "⚠️ Gemini Server မှာ "
                "လက်ရှိ Demand များနေပါသည်။\n\n"
                "🔄 Primary Model နဲ့ Retry လုပ်ပြီး "
                "Fallback Models တွေကိုပါ "
                "စမ်းပြီးပါပြီ။\n\n"
                "ခဏနားပြီး ထပ် Generate လုပ်ပါ။"
            )

        else:

            friendly_message = (
                "⚠️ Script Generate Error:\n\n"
                f"{error_text}"
            )

        return (
            "",
            friendly_message,
            None,
            None,
        )


# =========================================================
# TTS
# =========================================================

async def generate_myanmar_tts(
    text,
    voice_choice,
    speed_percent,
):

    if not text or not text.strip():

        return (
            None,
            None,
            None,
            None,
        )

    clean_text = (
        clean_script_for_tts(
            text
        )
    )

    if not clean_text.strip():

        return (
            None,
            None,
            None,
            None,
        )

    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )

    rate_str = (
        f"{int(speed_percent):+d}%"
    )

    output_filename = (
        "recap_voice_over.mp3"
    )

    try:

        communicate = (
            edge_tts.Communicate(
                clean_text,
                selected_voice,
                rate=rate_str,
            )
        )

        await communicate.save(
            output_filename
        )

        srt_file, zip_file = (
            generate_srt_and_zip(
                clean_text
            )
        )

        return (
            output_filename,
            output_filename,
            srt_file,
            zip_file,
        )

    except Exception as e:

        print(
            "TTS Error:",
            e
        )

        return (
            None,
            None,
            None,
            None,
        )


def tts_interface(
    text,
    voice_choice,
    speed,
):

    try:

        return asyncio.run(
            generate_myanmar_tts(
                text,
                voice_choice,
                speed,
            )
        )

    except Exception as e:

        print(
            "TTS Interface Error:",
            e
        )

        return (
            None,
            None,
            None,
            None,
        )


# =========================================================
# UI
# =========================================================

with gr.Blocks(
    title=APP_TITLE,
    theme=gr.themes.Soft(),
) as demo:

    gr.Markdown(
        """
# 🎬 AI Movie Recap Studio Pro

### Video → AI Analysis → Natural Burmese Movie Recap → Voice

**Narrator + Character Dialogue + Visual Storytelling**
"""
    )

    # =====================================================
    # API KEY TAB
    # =====================================================

    with gr.Tabs():

        with gr.TabItem(
            "🔑 API Key Setting"
        ):

            gr.Markdown(
                """
### 🔐 Gemini API Key

Video Analysis နှင့် Movie Recap Script
ထုတ်ရန် Gemini API Key လိုအပ်ပါသည်။
"""
            )

            api_key_input = gr.Textbox(
                label="Gemini API Key",
                type="password",
                placeholder="AIzaSy..."
            )

            save_key_btn = gr.Button(
                "💾 API Key သိမ်းမည်",
                variant="primary",
            )

            key_status = gr.Markdown("")

        # =================================================
        # VIDEO ANALYSIS
        # =================================================

        with gr.TabItem(
            "1️⃣ Video Analysis & Script"
        ):

            with gr.Row():

                # -----------------------------------------
                # LEFT
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    video_file = gr.Video(
                        label="📹 Video File တင်ရန်"
                    )

                    video_url = gr.Textbox(
                        label="🔗 Video URL",
                        placeholder=(
                            "YouTube / TikTok / Facebook "
                            "Video URL"
                        ),
                    )

                    load_link_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary",
                    )

                    ratio_picker = gr.Radio(
                        choices=[
                            "9:16",
                            "3:4",
                            "1:1",
                            "16:9",
                        ],
                        value="9:16",
                        label=(
                            "📐 Preview Aspect Ratio"
                        ),
                    )

                    gr.Markdown(
                        """
📱 **9:16** — TikTok / Reels / Shorts

📱 **3:4** — Facebook Vertical

⬜ **1:1** — Square

🖥️ **16:9** — YouTube Landscape
"""
                    )

                    gen_script_btn = gr.Button(
                        "🚀 Movie Recap Script ထုတ်မည်",
                        variant="primary",
                    )

                # -----------------------------------------
                # RIGHT
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    ratio_css_injection = gr.HTML(
                        get_ratio_css(
                            "9:16"
                        )
                    )

                    tab1_preview = gr.Video(
                        label="📺 Video Preview",
                        elem_id=(
                            "tab1_preview_container"
                        ),
                    )

                    script_status = gr.Markdown(
                        "Video တင်ပြီး Script ထုတ်နိုင်ပါပြီ။"
                    )

                    script_display = gr.Markdown(
                        label=(
                            "🎬 Generated Recap Script"
                        )
                    )

                    gr.Markdown(
                        """
### 📝 Script Style

AI က Video ထဲက -

🎙️ Narrator  
🗣️ Actual Character Dialogue  
👀 Visual Action  
😮 Character Reaction  
🎬 Scene Progression  

တို့ကို ပေါင်းစပ်ပြီး Natural Burmese Movie Recap
ပုံစံနဲ့ ထုတ်ပေးပါမယ်။
"""
                    )

                    with gr.Row():

                        srt_download_tab1 = gr.File(
                            label="📄 SRT"
                        )

                        zip_download_tab1 = gr.File(
                            label="📦 SRT ZIP"
                        )

        # =================================================
        # TTS TAB
        # =================================================

        with gr.TabItem(
            "2️⃣ Text-to-Speech"
        ):

            with gr.Row():

                # -----------------------------------------
                # LEFT
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    input_text = gr.Textbox(
                        label=(
                            "🎙️ Burmese Recap Script"
                        ),
                        lines=15,
                        placeholder=(
                            "Tab 1 မှ Script "
                            "အလိုအလျောက် ရောက်လာပါမည်..."
                        ),
                    )

                    voice_dropdown = gr.Dropdown(
                        choices=list(
                            VOICES.keys()
                        ),
                        value=(
                            "Thiha "
                            "(အမျိုးသားအသံ) - Natural"
                        ),
                        label="🎤 Voice ရွေးချယ်ပါ",
                    )

                    speed_slider = gr.Slider(
                        minimum=-30,
                        maximum=50,
                        value=5,
                        step=1,
                        label=(
                            "⚡ Voice Speed (%)"
                        ),
                    )

                    gen_voice_btn = gr.Button(
                        "⚡ Burmese Voice ထုတ်မည်",
                        variant="primary",
                    )

                # -----------------------------------------
                # RIGHT
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    audio_output = gr.Audio(
                        type="filepath",
                        autoplay=True,
                        label=(
                            "🔊 Burmese Voice Preview"
                        ),
                    )

                    mp3_download = gr.File(
                        label="🎵 MP3 Download"
                    )

                    with gr.Row():

                        srt_download_tab2 = gr.File(
                            label="📄 SRT"
                        )

                        zip_download_tab2 = gr.File(
                            label="📦 SRT ZIP"
                        )

    # =====================================================
    # EVENTS
    # =====================================================

    # API Key
    save_key_btn.click(
        fn=save_api_key,
        inputs=api_key_input,
        outputs=key_status,
    )

    # Ratio change
    ratio_picker.change(
        fn=get_ratio_css,
        inputs=ratio_picker,
        outputs=ratio_css_injection,
    )

    # Video upload → preview
    video_file.change(
        fn=preview_uploaded_video,
        inputs=video_file,
        outputs=tab1_preview,
    )

    # URL → download → preview
    load_link_btn.click(
        fn=load_tab1_link,
        inputs=video_url,
        outputs=tab1_preview,
    )

    # Generate Movie Recap
    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[
            video_file,
            video_url,
            ratio_picker,
        ],
        outputs=[
            input_text,
            script_status,
            srt_download_tab1,
            zip_download_tab1,
        ],
    )

    # Generate Voice
    gen_voice_btn.click(
        fn=tts_interface,
        inputs=[
            input_text,
            voice_dropdown,
            speed_slider,
        ],
        outputs=[
            audio_output,
            mp3_download,
            srt_download_tab2,
            zip_download_tab2,
        ],
    )


# =========================================================
# LAUNCH
# =========================================================

if __name__ == "__main__":

    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
    ) 
