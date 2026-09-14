import gradio as gr
import edge_tts
import asyncio
import os
import re
import time
import zipfile
import subprocess
import yt_dlp
from google import genai

# =========================================================
# AI MOVIE RECAP STUDIO PRO
# Video → AI Analysis → Natural Burmese Recap
# Narrator + Actual Character Dialogue + Visual Storytelling
# =========================================================

APP_TITLE = "AI Movie Recap Studio Pro"
MAX_VIDEO_MINUTES = 10

SAVED_API_KEY = ""

# =========================================================
# BURMESE VOICES
# =========================================================

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural",
}

# =========================================================
# API KEY
# =========================================================

def save_api_key(api_key):
    global SAVED_API_KEY

    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"

    return "⚠️ Gemini API Key ထည့်ပေးပါ။"


# =========================================================
# VIDEO DURATION
# =========================================================

def get_video_duration(video_path):

    if not video_path or not os.path.exists(video_path):
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
            return float(result.stdout.strip())

    except Exception as e:
        print("Duration Error:", e)

    return None


def validate_video_duration(video_path):

    duration = get_video_duration(video_path)

    if duration is None:
        return True, ""

    minutes = duration / 60

    if minutes > MAX_VIDEO_MINUTES:
        return (
            False,
            f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ "
            f"အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ အသုံးပြုနိုင်ပါသည်။"
        )

    return True, f"✅ Video Length: {minutes:.1f} မိနစ်"


# =========================================================
# TTS CLEANER
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

        # Remove markdown
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = line.replace("`", "")

        # Remove technical labels only
        # IMPORTANT:
        # Do NOT remove normal dialogue such as
        # "နင် ဒီကို ဘာလာလုပ်တာလဲ?"
        line = re.sub(
            r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator)\]\s*[:\-]?\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        # Remove obvious title/header lines
        if line.lower() in [
            "movie recap",
            "recap script",
            "burmese recap script",
            "script",
        ]:
            continue

        if line.startswith("---"):
            continue

        # Remove only explicit speaker labels,
        # not every colon-containing sentence.
        line = re.sub(
            r"^\s*(?:🎙️\s*)?Narrator\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE
        )

        if line.strip():
            cleaned.append(line.strip())

    return "\n".join(cleaned)


# =========================================================
# SRT TIME
# =========================================================

def seconds_to_srt_time(seconds):

    seconds = max(0, int(seconds))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:02d},000"


def generate_srt_and_zip(script_text):

    clean_text = clean_script_for_tts(script_text)

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

        # Estimate reading duration
        # Burmese spoken speed approximation
        char_count = len(line)

        duration = max(
            2,
            min(
                8,
                round(char_count / 11)
            )
        )

        start_time = current_time
        end_time = current_time + duration

        srt_content += (
            f"{subtitle_index}\n"
            f"{seconds_to_srt_time(start_time)} --> "
            f"{seconds_to_srt_time(end_time)}\n"
            f"{line}\n\n"
        )

        current_time = end_time
        subtitle_index += 1

    srt_filename = "myanmar_recap_subtitle.srt"
    zip_filename = "myanmar_recap_subtitle.zip"

    with open(
        srt_filename,
        "w",
        encoding="utf-8-sig"
    ) as f:
        f.write(srt_content)

    with zipfile.ZipFile(
        zip_filename,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:
        zipf.write(
            srt_filename,
            arcname=srt_filename
        )

    return srt_filename, zip_filename


# =========================================================
# DOWNLOAD VIDEO FROM URL
# =========================================================

def download_video_from_link(link):

    if not link or not link.strip():
        return None

    output_template = "temp_movie_recap.%(ext)s"

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

            info = ydl.extract_info(
                link.strip(),
                download=True
            )

            filename = ydl.prepare_filename(info)

            # Sometimes yt-dlp changes extension after merge
            if os.path.exists(filename):
                return filename

            base = os.path.splitext(filename)[0]

            possible_files = [
                base + ".mp4",
                base + ".mkv",
                base + ".webm",
                base + ".mov",
            ]

            for f in possible_files:
                if os.path.exists(f):
                    return f

    except Exception as e:

        print("Download Error:", e)

    return None


# =========================================================
# RATIO CSS
# =========================================================

def get_ratio_css(ratio):

    configs = {

        "9:16": {
            "aspect": "9 / 16",
            "max_width": "360px"
        },

        "3:4": {
            "aspect": "3 / 4",
            "max_width": "420px"
        },

        "1:1": {
            "aspect": "1 / 1",
            "max_width": "500px"
        },

        "16:9": {
            "aspect": "16 / 9",
            "max_width": "650px"
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

#tab1_preview_container > div {{
    width: 100% !important;
}}

</style>

<script>

(function() {{

    const ratio = "{cfg["aspect"]}";

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

        containers.forEach(function(el) {{

            el.style.aspectRatio = ratio;
            el.style.width = "100%";

            if (el.tagName === "VIDEO") {{
                el.style.height = "100%";
                el.style.objectFit = "contain";
            }}

        }});
    }}

    applyRatio();

    setTimeout(applyRatio, 100);
    setTimeout(applyRatio, 300);
    setTimeout(applyRatio, 700);
    setTimeout(applyRatio, 1200);
    setTimeout(applyRatio, 2000);

    const observer =
        new MutationObserver(function() {{
            applyRatio();
        }});

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
# VIDEO PREVIEW
# =========================================================

def preview_uploaded_video(video_file):

    if not video_file:
        return None

    return video_file


def load_tab1_link(link):

    if not link or not link.strip():
        return None

    video = download_video_from_link(link)

    if not video:
        return None

    return video


# =========================================================
# MAIN GEMINI RECAP PROMPT
# =========================================================

def build_recap_prompt(selected_ratio):

    return f"""

သင်သည် Professional Movie Recap Creator တစ်ယောက်ဖြစ်သည်။

ပေးထားသော Video ကို အစမှအဆုံးအထိ သေချာကြည့်ရှု၊ နားထောင်ပြီး
Video ထဲတွင် တကယ်ဖြစ်ပျက်နေသော ဇာတ်လမ်းကို အခြေခံ၍
မြန်မာဘာသာဖြင့် အလွန်သဘာဝကျပြီး နားထောင်လို့ကောင်းသော
Movie Recap Script တစ်ခုရေးပါ။

Target Aspect Ratio = {selected_ratio}

==================================================
အရေးကြီးဆုံး STYLE
==================================================

Narrator တစ်ယောက်တည်းက ပုံပြင်တစ်ပုဒ်လို
အစအဆုံး ရှင်းပြနေတဲ့ပုံစံ မရေးရ။

ဥပမာ -

"တစ်နေ့မှာ ကောင်လေးတစ်ယောက်ဟာ အိမ်တစ်အိမ်ကို
သွားခဲ့ပါတယ်..."

လိုမျိုး Narrator-only storytelling မလုပ်ရ။

အစား -

"ကောင်လေးက အိမ်ထဲကို ဝင်လာပြီး
မိန်းကလေးရှေ့မှာ ရပ်လိုက်ပါတယ်။
သူ့မျက်နှာကိုကြည့်ရတာ တစ်ခုခုအရေးကြီးတဲ့
ကိစ္စရှိနေသလိုပါပဲ။

နင် ဒီကို ဘာလာလုပ်တာလဲ?

ငါ မင်းကို ပြောစရာရှိလို့...

သူတို့နှစ်ယောက် စကားပြောနေတုန်းမှာပဲ
အပြင်ဘက်ကနေ အသံတစ်ခု ထွက်လာပါတယ်။
အဲ့ဒီအသံကြောင့် နှစ်ယောက်စလုံး
တံခါးဘက်ကို လှည့်ကြည့်လိုက်ကြပါတယ်..."

ဆိုတဲ့ပုံစံကို အသုံးပြုရမည်။

==================================================
1. VISUAL STORYTELLING
==================================================

Video ထဲမှာ မြင်ရတဲ့ Action ကို
Narrator အဖြစ် သဘာဝကျကျ ပြောပြပါ။

ဥပမာ -

"ကောင်လေးက တံခါးကို ဖြည်းဖြည်းဖွင့်ပြီး
အခန်းထဲကို ဝင်လာပါတယ်။"

"မိန်းကလေးက သူ့ကို မယုံသင်္ကာနဲ့
ကြည့်နေပါတယ်။"

"ကောင်လေးက စားပွဲပေါ်မှာရှိတဲ့ ဖုန်းကို
ကောက်ယူလိုက်ပါတယ်။"

"သူမက ခဏတိတ်သွားပြီး
တံခါးဘက်ကို လှည့်ကြည့်လိုက်ပါတယ်။"

Video ထဲမှာ တကယ်မြင်ရတဲ့ Action ကို
အသေးစိတ်နဲ့ သဘာဝကျကျ ရေးပါ။

==================================================
2. CHARACTER DIALOGUE
==================================================

Video ထဲမှာ Character တစ်ယောက်က
တကယ်စကားပြောနေပါက Dialogue ကို
Narrator စကားနဲ့ မရောဘဲ ထည့်ပါ။

ဥပမာ -

"နင် ဒီကို ဘာလာလုပ်တာလဲ?"

"ငါ မင်းကို ပြောစရာရှိလို့။"

"မင်း အခုချက်ချင်း ဒီကနေ ထွက်သွား!"

Dialogue ကို Video ထဲက အဓိပ္ပာယ်အတိုင်း
သဘာဝကျသော မြန်မာစကားပြောပုံစံဖြင့် ပြန်ဆိုပါ။

==================================================
3. ORIGINAL LANGUAGE DIALOGUE
==================================================

Video ထဲမှာ English / Chinese / Japanese /
Korean / Thai / အခြားဘာသာစကားဖြင့်
စကားပြောနေပါက -

မူရင်းဘာသာစကားကို မရေးပါနှင့်။

အဓိပ္ပာယ်မပျက်စေဘဲ
မြန်မာလူတစ်ယောက် တကယ်စကားပြောသလို
သဘာဝကျကျ ဘာသာပြန်ပါ။

စာအုပ်ထဲက ဘာသာပြန်သလို မရေးရ။

==================================================
4. DIALOGUE မရှင်းလင်းလျှင်
==================================================

Video ထဲက Dialogue ကို မကြားရ၊
မသေချာရင် ကိုယ်တိုင် Dialogue မဖန်တီးရ။

အဓိပ္ပာယ်ကို ခန့်မှန်းပြီး ဇာတ်လမ်းအသစ်
မထည့်ရ။

အဲ့ဒီအခြေအနေမှာ မြင်ရတဲ့ Action ကိုသာ
Narrator နဲ့ ရှင်းပြပါ။

==================================================
5. NARRATOR + DIALOGUE + ACTION
==================================================

ဇာတ်လမ်းတစ်လျှောက် အောက်ပါပုံစံကို
သဘာဝကျကျ အသုံးပြုပါ -

Action
→ Narrator
→ Character Dialogue
→ Character Reaction
→ Narrator
→ Next Action
→ Dialogue
→ Reaction
→ Next Scene

အမြဲတမ်း Narrator တစ်ယောက်တည်း မပြောရ။

အမြဲတမ်း Dialogue တစ်ခုချင်းစီပဲ မရေးရ။

Video ထဲမှာ ဖြစ်ပျက်နေတဲ့အရာအလိုက်
Narrator နဲ့ Dialogue ကို ရောစပ်ပါ။

==================================================
6. CHARACTER REACTION
==================================================

Video ထဲမှာ Character ရဲ့မျက်နှာ၊
ကိုယ်ဟန်အမူအရာ၊ လှုပ်ရှားမှုတွေ မြင်ရရင်
သဘာဝကျကျ ထည့်ရေးပါ။

ဥပမာ -

"အဲ့ဒီစကားကို ကြားလိုက်တာနဲ့
မိန်းကလေးရဲ့မျက်နှာက ချက်ချင်းတင်းမာသွားပါတယ်။"

"ကောင်လေးက ခဏတိတ်သွားပြီး
သူမကို စိုက်ကြည့်နေပါတယ်။"

"သူမက ဘာပြန်ပြောရမလဲ မသိသလို
ခဏငြိမ်သွားပါတယ်။"

ဒါပေမယ့် Video ထဲမှာ မမြင်ရတဲ့
အတွေးတွေကို မဖန်တီးရ။

==================================================
7. SCENE TRANSITION
==================================================

Scene တစ်ခုကနေ တစ်ခုကို ပြောင်းတဲ့အခါ
ပုံပြင်စာအုပ်လို မရေးရ။

Video ထဲက အဖြစ်အပျက်အတိုင်း
သဘာဝကျကျ ဆက်သွားရမည်။

ဥပမာ -

"သူတို့နှစ်ယောက် စကားပြောနေတုန်းမှာပဲ
အပြင်ဘက်ကနေ အသံတစ်ခု ထွက်လာပါတယ်။

အသံကြားလိုက်တာနဲ့ နှစ်ယောက်စလုံး
တံခါးဘက်ကို လှည့်ကြည့်လိုက်ကြပါတယ်။"

==================================================
8. NO INVENTED STORY
==================================================

အလွန်အရေးကြီးသည်။

Video ထဲမှာ မပါသော -

- Character Name
- Relationship
- Background
- Location
- Secret
- Motivation
- Emotion
- Dialogue
- Event
- Ending

တို့ကို ကိုယ်တိုင် မဖန်တီးရ။

Video ထဲမှာ မြင်ရ၊ ကြားရ၊
သိသာစွာ ခွဲခြားနိုင်သောအရာများကိုသာ
အသုံးပြုရမည်။

==================================================
9. NO GENERIC SUMMARY
==================================================

"ဒီဇာတ်ကားမှာတော့..."
"အဓိကဇာတ်ကောင်က..."
"ဒီနေ့မှာတော့..."
"တစ်နေ့မှာ..."
"အဲ့ဒီနောက် သူတို့ဘဝက..."
"နောက်ဆုံးမှာ အရာအားလုံးက..."
လိုမျိုး Generic Movie Summary ပုံစံကို
တတ်နိုင်သမျှ မသုံးရ။

Scene ထဲက ဖြစ်ရပ်ကို တိုက်ရိုက်
ပြန်လည်အသက်ဝင်အောင် ပြောပြပါ။

==================================================
10. ENGAGING MOVIE RECAP STYLE
==================================================

TikTok / Facebook Reels / YouTube Shorts
ကြည့်သူတွေ ဆက်ကြည့်ချင်အောင်
စိတ်ဝင်စားစရာကောင်းသော Rhythm ဖြင့် ရေးပါ။

Scene တစ်ခုမှာ ဖြစ်ပျက်နေတာကို
အလွန်ရှည်ရှည် မဆွဲပါနှင့်။

အရေးကြီးတဲ့ Action၊ Reaction၊ Dialogue
တွေကို ဦးစားပေးပါ။

Suspense ဖြစ်ရင် Suspense ကို ထိန်းပါ။

Surprise ဖြစ်ရင် Surprise ကို မဖျက်ပါနှင့်။

==================================================
11. FULL VIDEO
==================================================

Video ကို အစပိုင်းပဲ မကြည့်ဘဲ
အစမှ အဆုံးအထိ Analyze လုပ်ပါ။

Beginning
→ Development
→ Conflict
→ Important Events
→ Reactions
→ Climax
→ Ending

အားလုံးပါအောင်ရေးပါ။

Video ရဲ့ အဆုံးမရောက်ခင်
ဇာတ်လမ်းကို မရပ်ပါနှင့်။

==================================================
12. BURMESE LANGUAGE
==================================================

မြန်မာလူတွေ နားထောင်တဲ့အခါ
သဘာဝကျမယ့် စကားလုံးတွေကို အသုံးပြုပါ။

စာရေးဆရာပုံစံ မဟုတ်ရ။

Movie Recap Creator တစ်ယောက်က
Video ကြည့်ပြီး သူငယ်ချင်းကို
ပြန်ပြောပြနေသလို ဖြစ်ရမည်။

ဥပမာ -

"သူက အခန်းထဲကို ဝင်လာပြီး
သူမရှေ့မှာ တန်းရပ်လိုက်ပါတယ်။"

ဒီလိုပုံစံကို ဦးစားပေးပါ။

==================================================
13. TTS FRIENDLY
==================================================

Script ကို AI Voice ဖြင့် ဖတ်မည်ဖြစ်သောကြောင့် -

- ရှည်လွန်းသော စာကြောင်းများ မရေးရ
- Comma / Full stop ကို သဘာဝကျကျ သုံးပါ
- Dialogue ကို ရှင်းလင်းစွာ ခွဲပါ
- ဖတ်ရခက်တဲ့ စာလုံးပေါင်းများ မသုံးရ
- အင်္ဂလိပ်စကားလုံးများကို မလိုအပ်ဘဲ မထည့်ရ

==================================================
14. OUTPUT FORMAT
==================================================

အောက်ပါလို ရိုးရှင်းသော Script တစ်ခုတည်းကိုသာ ထုတ်ပါ။

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

ထို့နောက်...

==================================================
15. DO NOT ADD TECHNICAL LABELS
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

Script ကို ဖတ်လို့ရတဲ့ Natural Burmese
စာသားပုံစံနဲ့သာ ထုတ်ပါ။

==================================================
16. FINAL QUALITY CHECK
==================================================

Script မထုတ်ခင် ကိုယ်တိုင်စစ်ဆေးပါ -

✓ Video အစမှအဆုံး ကြည့်ပြီးပြီလား?
✓ တကယ်ဖြစ်တဲ့ Action တွေပါလား?
✓ တကယ်ကြားရတဲ့ Dialogue တွေပါလား?
✓ Dialogue ကို သဘာဝကျကျ ဘာသာပြန်ထားလား?
✓ Narrator-only မဖြစ်ဘူးလား?
✓ Character Dialogue ကို မဖန်တီးထားဘူးလား?
✓ Video ထဲမပါတဲ့ ဇာတ်လမ်း မထည့်ထားဘူးလား?
✓ Character Reaction တွေ Video နဲ့ကိုက်ညီလား?
✓ ဇာတ်လမ်းအဆုံးထိ ပါလား?
✓ TTS ဖတ်လို့ သဘာဝကျလား?
✓ TikTok / Facebook / YouTube ကြည့်သူတွေ
  ဆက်ကြည့်ချင်အောင် Rhythm ကောင်းလား?

အားလုံးကို စစ်ပြီးနောက်
Final Natural Burmese Movie Recap Script
ကိုသာ ပြန်ပေးပါ။
"""


# =========================================================
# ANALYZE VIDEO + GENERATE SCRIPT
# =========================================================

def analyze_and_generate_script(
    video_file,
    video_link,
    ratio_choice
):

    global SAVED_API_KEY

    # -----------------------------------------
    # API KEY CHECK
    # -----------------------------------------

    if not SAVED_API_KEY:

        return (
            "",
            "⚠️ API Key မရှိသေးပါ။ "
            "အရင်ဆုံး API Key Setting မှာ Gemini API Key ထည့်ပါ။",
            None,
            None,
        )

    # -----------------------------------------
    # SELECT VIDEO
    # -----------------------------------------

    target_media = None

    if video_file:
        target_media = video_file

    elif video_link:
        target_media = download_video_from_link(
            video_link
        )

    if not target_media:

        return (
            "",
            "⚠️ Video File တင်ပါ သို့မဟုတ် "
            "မှန်ကန်သော Video URL ထည့်ပါ။",
            None,
            None,
        )

    if not os.path.exists(target_media):

        return (
            "",
            "⚠️ Video File ကို ရှာမတွေ့ပါ။",
            None,
            None,
        )

    # -----------------------------------------
    # 10 MINUTE LIMIT
    # -----------------------------------------

    valid, duration_message = validate_video_duration(
        target_media
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
            f"⚠️ Gemini Client Error:\n{str(e)}",
            None,
            None,
        )

    # -----------------------------------------
    # RATIO
    # -----------------------------------------

    selected_ratio = ratio_choice

    # -----------------------------------------
    # UPLOAD VIDEO
    # -----------------------------------------

    try:

        uploaded_file = client.files.upload(
            file=target_media
        )

        # Wait until Gemini finishes processing
        while uploaded_file.state.name == "PROCESSING":

            time.sleep(3)

            uploaded_file = client.files.get(
                name=uploaded_file.name
            )

        if uploaded_file.state.name == "FAILED":

            return (
                "",
                "⚠️ Gemini ဘက်မှ Video Processing "
                "မအောင်မြင်ပါ။",
                None,
                None,
            )

        # -----------------------------------------
        # PROMPT
        # -----------------------------------------

        prompt = build_recap_prompt(
            selected_ratio
        )

        # -----------------------------------------
        # GENERATE
        # -----------------------------------------

        response = client.models.generate_content(

            # Keep your current model here.
            # If your API account does not support this
            # model, replace it with a supported Gemini
            # video-capable model.
            model="gemini-3.6-flash",

            contents=[
                uploaded_file,
                prompt
            ],
        )

        script_text = response.text or ""

        if not script_text.strip():

            return (
                "",
                "⚠️ Gemini က Script ပြန်မပေးပါ။",
                None,
                None,
            )

        # -----------------------------------------
        # CLEAN SCRIPT
        # -----------------------------------------

        clean_text = clean_script_for_tts(
            script_text
        )

        # -----------------------------------------
        # SRT
        # -----------------------------------------

        srt_file, zip_file = generate_srt_and_zip(
            clean_text
        )

        # -----------------------------------------
        # OUTPUT
        # -----------------------------------------

        status = (
            "✅ Video Analysis ပြီးပါပြီ။\n\n"
            f"{duration_message}\n\n"
            "🎬 Narrator + Character Dialogue + "
            "Visual Storytelling ပုံစံဖြင့် "
            "Recap Script ထုတ်ပြီးပါပြီ။"
        )

        return (
            clean_text,
            status,
            srt_file,
            zip_file,
        )

    except Exception as e:

        return (
            "",
            f"⚠️ Script Generate Error:\n{str(e)}",
            None,
            None,
        )


# =========================================================
# TTS
# =========================================================

async def generate_myanmar_tts(
    text,
    voice_choice,
    speed_percent
):

    if not text or not text.strip():

        return (
            None,
            None,
            None,
            None,
        )

    clean_text = clean_script_for_tts(
        text
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

    rate_str = f"{int(speed_percent):+d}%"

    output_filename = (
        "recap_voice_over.mp3"
    )

    try:

        communicate = edge_tts.Communicate(
            clean_text,
            selected_voice,
            rate=rate_str,
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

        print("TTS Error:", e)

        return (
            None,
            None,
            None,
            None,
        )


def tts_interface(
    text,
    voice_choice,
    speed
):

    try:

        return asyncio.run(
            generate_myanmar_tts(
                text,
                voice_choice,
                speed
            )
        )

    except Exception as e:

        print("TTS Interface Error:", e)

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
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown(
        """
# 🎬 AI Movie Recap Studio Pro

### Video → AI Analysis → Natural Burmese Movie Recap → Voice

**Narrator + Character Dialogue + Visual Storytelling**
"""
    )

    # =====================================================
    # TABS
    # =====================================================

    with gr.Tabs():

        # =================================================
        # API KEY
        # =================================================

        with gr.TabItem(
            "🔑 API Key Setting"
        ):

            gr.Markdown(
                """
### 🔐 Gemini API Key

Video Analysis နဲ့ AI Movie Recap Script
ထုတ်ဖို့ Gemini API Key လိုအပ်ပါတယ်။
"""
            )

            api_key_input = gr.Textbox(
                label="Gemini API Key",
                type="password",
                placeholder="AIzaSy..."
            )

            save_key_btn = gr.Button(
                "💾 API Key သိမ်းမည်",
                variant="primary"
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
                            "Video URL ထည့်ပါ"
                        )
                    )

                    load_link_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary"
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
                        )
                    )

                    gr.Markdown(
                        """
**Supported Preview**

📱 9:16 — TikTok / Reels / Shorts  
📱 3:4 — Facebook / Vertical  
⬜ 1:1 — Square  
🖥️ 16:9 — YouTube / Landscape
"""
                    )

                    gen_script_btn = gr.Button(
                        "🚀 Movie Recap Script ထုတ်မည်",
                        variant="primary"
                    )

                # -----------------------------------------
                # RIGHT
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    ratio_css_injection = gr.HTML(
                        get_ratio_css("9:16")
                    )

                    tab1_preview = gr.Video(
                        label=(
                            "📺 Video Preview"
                        ),
                        elem_id=(
                            "tab1_preview_container"
                        )
                    )

                    script_status = gr.Markdown(
                        "Video တင်ပြီး Script ထုတ်နိုင်ပါပြီ။"
                    )

                    script_display = gr.Markdown(
                        label="🎬 Generated Recap Script"
                    )

                    gr.Markdown(
                        """
### 📝 Script Output

Script ထဲမှာ -

- 🎙️ Narrator
- 🗣️ Character Dialogue
- 👀 Visual Action
- 😮 Character Reaction
- 🎬 Scene Progression

တွေကို Video ထဲက အဖြစ်အပျက်အတိုင်း
သဘာဝကျကျ ပေါင်းစပ်ထားပါမယ်။
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
        # TEXT TO SPEECH
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
                        )
                    )

                    voice_dropdown = gr.Dropdown(
                        choices=list(
                            VOICES.keys()
                        ),
                        value=(
                            "Thiha "
                            "(အမျိုးသားအသံ) - Natural"
                        ),
                        label="🎤 Voice ရွေးချယ်ပါ"
                    )

                    speed_slider = gr.Slider(
                        minimum=-30,
                        maximum=50,
                        value=5,
                        step=1,
                        label=(
                            "⚡ Voice Speed (%)"
                        )
                    )

                    gen_voice_btn = gr.Button(
                        "⚡ Burmese Voice ထုတ်မည်",
                        variant="primary"
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
                        label="🔊 Burmese Voice Preview"
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

    # API KEY
    save_key_btn.click(
        fn=save_api_key,
        inputs=api_key_input,
        outputs=key_status
    )

    # Ratio
    ratio_picker.change(
        fn=get_ratio_css,
        inputs=ratio_picker,
        outputs=ratio_css_injection
    )

    # Video upload → Preview immediately
    video_file.change(
        fn=preview_uploaded_video,
        inputs=video_file,
        outputs=tab1_preview
    )

    # URL → Download → Preview
    load_link_btn.click(
        fn=load_tab1_link,
        inputs=video_url,
        outputs=tab1_preview
    )

    # Generate Script
    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[
            video_file,
            video_url,
            ratio_picker
        ],
        outputs=[
            input_text,
            script_status,
            srt_download_tab1,
            zip_download_tab1
        ]
    )

    # Generate Voice
    gen_voice_btn.click(
        fn=tts_interface,
        inputs=[
            input_text,
            voice_dropdown,
            speed_slider
        ],
        outputs=[
            audio_output,
            mp3_download,
            srt_download_tab2,
            zip_download_tab2
        ]
    )


# =========================================================
# LAUNCH
# =========================================================

if __name__ == "__main__":

    demo.launch(
        server_name="0.0.0.0",
        server_port=7860
    )
