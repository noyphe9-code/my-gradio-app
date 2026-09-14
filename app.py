import gradio as gr
import edge_tts
import asyncio
import os
import re
import time
import zipfile
import yt_dlp
from google import genai


# =========================================================
# SETTINGS
# =========================================================

SAVED_API_KEY = ""

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural"
}


# =========================================================
# API KEY
# =========================================================

def save_api_key(api_key):
    global SAVED_API_KEY

    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()

        return (
            "✅ Gemini API Key ကို အောင်မြင်စွာ "
            "သိမ်းဆည်းပြီးပါပြီ။"
        )

    return (
        "⚠️ ကျေးဇူးပြု၍ Gemini API Key ထည့်ပါ။"
    )


# =========================================================
# SCRIPT CLEANER
# =========================================================

def clean_script_for_tts(script_text):

    if not script_text:
        return ""

    lines = script_text.split("\n")
    cleaned_lines = []

    for line in lines:

        line_str = line.strip()

        if not line_str:
            continue

        # Remove visual / scene instructions
        if re.match(
            r"^\[(Visual|Scene|Video|Audio|Camera|Shot|Action).*?\]",
            line_str,
            re.IGNORECASE
        ):
            continue

        # Remove markdown headings
        if line_str.startswith("###"):
            continue

        if line_str.startswith("---"):
            continue

        # Remove common AI intro/outro
        if "recap script" in line_str.lower():
            continue

        if line_str in [
            "ဒီမှာ",
            "အောက်တွင်",
            "ဇာတ်လမ်းအကျဉ်း",
            "Summary"
        ]:
            continue

        # Remove markdown
        line_str = (
            line_str
            .replace("**", "")
            .replace("__", "")
            .replace("`", "")
        )

        # Remove explicit technical labels only
        line_str = re.sub(
            r"^\s*\[(Visual|Scene|Video|Audio|Camera|Shot|Action).*?\]\s*",
            "",
            line_str,
            flags=re.IGNORECASE
        )

        # IMPORTANT:
        # Do NOT remove Character dialogue.
        #
        # Example:
        # မင်း ဘာလုပ်နေတာလဲ?
        #
        # will remain unchanged.

        if line_str.strip():
            cleaned_lines.append(
                line_str.strip()
            )

    return "\n".join(cleaned_lines)


# =========================================================
# SRT TIME
# =========================================================

def seconds_to_srt_time(seconds):

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{millis:03d}"
    )


# =========================================================
# GENERATE SRT + ZIP
# =========================================================

def generate_srt_and_zip(script_text):

    clean_text = clean_script_for_tts(
        script_text
    )

    lines = clean_text.split("\n")

    srt_content = ""

    start_sec = 0
    subtitle_index = 1

    for line in lines:

        line = line.strip()

        if not line:
            continue

        # Estimate subtitle duration
        # Burmese text needs slightly more time
        char_count = len(line)

        duration = max(
            2.5,
            min(
                8,
                char_count / 9
            )
        )

        end_sec = start_sec + duration

        start_time = seconds_to_srt_time(
            start_sec
        )

        end_time = seconds_to_srt_time(
            end_sec
        )

        srt_content += (
            f"{subtitle_index}\n"
            f"{start_time} --> {end_time}\n"
            f"{line}\n\n"
        )

        subtitle_index += 1
        start_sec = end_sec

    srt_filename = (
        "myanmar_subtitle.srt"
    )

    zip_filename = (
        "myanmar_subtitle.zip"
    )

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

    return (
        srt_filename,
        zip_filename
    )


# =========================================================
# DOWNLOAD VIDEO FROM URL
# =========================================================

def download_video_from_link(link):

    if not link or not link.strip():
        return None

    output_template = (
        "temp_downloaded_video.%(ext)s"
    )

    ydl_opts = {
        "format":
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/best",

        "outtmpl": output_template,

        "quiet": True,

        "no_warnings": True,

        "overwrites": True,

        "merge_output_format": "mp4"
    }

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                link.strip(),
                download=True
            )

            filename = ydl.prepare_filename(
                info
            )

            # Sometimes merged file becomes .mp4
            if os.path.exists(filename):
                return filename

            base = os.path.splitext(
                filename
            )[0]

            mp4_file = base + ".mp4"

            if os.path.exists(mp4_file):
                return mp4_file

    except Exception as e:

        print(
            "Video Download Error:",
            e
        )

    return None


# =========================================================
# PREVIEW RATIO CSS
# =========================================================

RATIO_CSS = """
<style>

#tab1_preview_container {
    width: 100% !important;
    max-width: 650px !important;
    margin-left: auto !important;
    margin-right: auto !important;
}

#tab1_preview_container .video-container {
    width: 100% !important;
    background: #000 !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    aspect-ratio: 9 / 16 !important;
    position: relative !important;
}

#tab1_preview_container video {
    width: 100% !important;
    height: 100% !important;
    object-fit: contain !important;
    background: #000 !important;
    display: block !important;
}

</style>
"""


# =========================================================
# RATIO JAVASCRIPT
# =========================================================

RATIO_JS = """
<script>

window.currentVideoRatio = "9:16";


function getRatioSettings(ratio) {

    if (ratio === "9:16") {

        return {
            aspect: "9 / 16",
            maxWidth: "360px"
        };

    }

    if (ratio === "3:4") {

        return {
            aspect: "3 / 4",
            maxWidth: "420px"
        };

    }

    if (ratio === "1:1") {

        return {
            aspect: "1 / 1",
            maxWidth: "500px"
        };

    }

    if (ratio === "16:9") {

        return {
            aspect: "16 / 9",
            maxWidth: "650px"
        };

    }

    return {
        aspect: "9 / 16",
        maxWidth: "360px"
    };
}


function applySelectedRatio() {

    const root =
        document.querySelector(
            "#tab1_preview_container"
        );

    if (!root) {
        return;
    }

    const container =
        root.querySelector(
            ".video-container"
        );

    if (!container) {
        return;
    }

    const video =
        container.querySelector(
            "video"
        );

    const settings =
        getRatioSettings(
            window.currentVideoRatio
        );

    container.style.aspectRatio =
        settings.aspect;

    container.style.width =
        "100%";

    container.style.maxWidth =
        settings.maxWidth;

    container.style.height =
        "auto";

    if (video) {

        video.style.width =
            "100%";

        video.style.height =
            "100%";

        video.style.objectFit =
            "contain";
    }
}


function watchPreviewChanges() {

    const root =
        document.querySelector(
            "#tab1_preview_container"
        );

    if (!root) {
        return;
    }

    const observer =
        new MutationObserver(
            function() {

                setTimeout(
                    applySelectedRatio,
                    100
                );

            }
        );

    observer.observe(
        root,
        {
            childList: true,
            subtree: true
        }
    );

    setInterval(
        applySelectedRatio,
        500
    );
}


setTimeout(
    watchPreviewChanges,
    500
);

</script>
"""


# =========================================================
# CHANGE RATIO
# =========================================================

def change_ratio(ratio):

    ratio = ratio or "9:16"

    if ratio == "9:16":

        aspect = "9 / 16"
        max_width = "360px"

    elif ratio == "3:4":

        aspect = "3 / 4"
        max_width = "420px"

    elif ratio == "1:1":

        aspect = "1 / 1"
        max_width = "500px"

    elif ratio == "16:9":

        aspect = "16 / 9"
        max_width = "650px"

    else:

        aspect = "9 / 16"
        max_width = "360px"

    html = f"""
    <div style="display:none">
        Ratio: {ratio}
    </div>

    <script>

    window.currentVideoRatio =
        "{ratio}";

    function updateVideoRatio() {{

        const root =
            document.querySelector(
                "#tab1_preview_container"
            );

        if (!root) return;

        const container =
            root.querySelector(
                ".video-container"
            );

        if (!container) return;

        const video =
            container.querySelector(
                "video"
            );

        container.style.aspectRatio =
            "{aspect}";

        container.style.width =
            "100%";

        container.style.maxWidth =
            "{max_width}";

        container.style.height =
            "auto";

        if (video) {{

            video.style.width =
                "100%";

            video.style.height =
                "100%";

            video.style.objectFit =
                "contain";

        }}

    }}

    let ratioAttempts = 0;

    const ratioTimer =
        setInterval(
            function() {{

                updateVideoRatio();

                ratioAttempts++;

                if (
                    ratioAttempts > 40
                ) {{
                    clearInterval(
                        ratioTimer
                    );
                }}

            }},
            150
        );

    </script>
    """

    return html


# =========================================================
# VIDEO FILE → PREVIEW
# =========================================================

def preview_uploaded_video(video):

    if not video:
        return None

    return video


# =========================================================
# LINK → PREVIEW
# =========================================================

def load_video_link(link):

    if not link or not link.strip():
        return None

    return download_video_from_link(
        link
    )


# =========================================================
# VIDEO → MOVIE RECAP SCRIPT
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
            "⚠️ Gemini API Key မရှိသေးပါ။ "
            "API Key Setting မှာ အရင်ထည့်ပါ။",
            None,
            None
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


    if (
        not target_media
        or not os.path.exists(
            target_media
        )
    ):

        return (
            "",
            "⚠️ Video ဖိုင်တင်ပါ "
            "သို့မဟုတ် Video Link မှန်ကန်စွာထည့်ပါ။",
            None,
            None
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
            f"⚠️ Gemini Client Error: {str(e)}",
            None,
            None
        )


    selected_ratio = (
        ratio_choice
        or "9:16"
    )


    # -----------------------------------------
    # UPLOAD VIDEO
    # -----------------------------------------

    try:

        uploaded_file = (
            client.files.upload(
                file=target_media
            )
        )


        while (
            uploaded_file.state.name
            == "PROCESSING"
        ):

            time.sleep(3)

            uploaded_file = (
                client.files.get(
                    name=uploaded_file.name
                )
            )


        if (
            uploaded_file.state.name
            == "FAILED"
        ):

            return (
                "",
                "⚠️ Gemini သို့ Video ပို့ပြီး "
                "Analyze လုပ်ရာတွင် မအောင်မြင်ပါ။",
                None,
                None
            )


        # =================================================
        # PROFESSIONAL MOVIE RECAP PROMPT
        # =================================================

        prompt = f"""
သင်သည် TikTok, Facebook Reels နှင့်
YouTube Shorts အတွက်
Professional Myanmar Movie Recap Writer တစ်ဦးဖြစ်သည်။

ပေးထားသော VIDEO ကို အစမှအဆုံး သေချာကြည့်ရှုပါ။

Target Aspect Ratio:
{selected_ratio}


==================================================
အဓိကတာဝန်
==================================================

Video ထဲမှာ တကယ်ဖြစ်ပျက်တဲ့ ဇာတ်လမ်းကို
အစကနေ အဆုံးအထိ အစဉ်လိုက် နားလည်ပြီး
မြန်မာပရိသတ်တွေ နားထောင်ရင်း
စိတ်ဝင်စားသွားအောင် Movie Recap Script ရေးပါ။

ရိုးရိုး Summary မရေးပါနှင့်။

Video ကို ကိုယ်တိုင်ကြည့်နေသလို ခံစားရအောင်—

Visual ဖြစ်ရပ်
+
Narrator ပြောစကား
+
ဇာတ်ကောင်များ၏ အပြန်အလှန်ပြောစကား
+
စိတ်ခံစားချက်
+
Suspense
+
Story Flow

တို့ကို သဘာဝကျကျ ပေါင်းစပ်ရေးပါ။


==================================================
1. VIDEO ကို အရင်ဆုံး နားလည်ပါ
==================================================

Video ထဲက—

• ဘယ်သူတွေပါလဲ
• ဘယ်သူက ဘယ်သူနဲ့ဆက်စပ်လဲ
• ဘယ်နေရာမှာဖြစ်လဲ
• ဘယ်သူဘာလုပ်လဲ
• ဘာကြောင့်လုပ်လဲ
• ဘာတွေဖြစ်လာလဲ
• ဇာတ်ကောင်တွေရဲ့ စိတ်ခံစားချက်
• ပြဿနာ
• အန္တရာယ်
• ပဋိပက္ခ
• အရေးကြီးတဲ့အပြောင်းအလဲ
• Climax
• Ending

တို့ကို Video ထဲက အချက်အလက်အတိုင်း
နားလည်ပါ။


==================================================
2. VISUAL STORYTELLING
==================================================

Video ထဲမှာ တကယ်မြင်ရတဲ့ Action တွေကို
ကြည့်ရှုသူက မျက်စိရှေ့မှာ မြင်နေရသလို
သဘာဝကျကျ ဖော်ပြပါ။

ဥပမာ—

"ကောင်မလေးက တံခါးကို ဖြည်းဖြည်းဖွင့်လိုက်ပါတယ်။
အခန်းထဲကို ဝင်ကြည့်လိုက်တဲ့အချိန်
တစ်ခန်းလုံး တိတ်ဆိတ်နေပါတယ်။"

"သူမ နောက်ကိုလှည့်ကြည့်လိုက်တဲ့အခါ
အနောက်မှာ လူတစ်ယောက် ရပ်နေတာကို
ရုတ်တရက်တွေ့လိုက်ရပါတယ်။"

ထိုကဲ့သို့ Video ထဲက
မြင်ရသောအရာကို Story အဖြစ် ပြန်ဖော်ပြပါ။

သို့သော် မမြင်ရသောအရာကို မတီထွင်ပါနှင့်။


==================================================
3. NARRATOR
==================================================

Narrator သည် Story ကို ချိတ်ဆက်ပေးရမည်။

Narrator က—

• အခြေအနေရှင်းပြခြင်း
• ဇာတ်ကောင်လုပ်ရပ်ရှင်းပြခြင်း
• Scene တစ်ခုမှတစ်ခုသို့ ချိတ်ဆက်ခြင်း
• Suspense ဖန်တီးခြင်း
• အရေးကြီးတဲ့အချက်ကို Highlight လုပ်ခြင်း
• ကြည့်ရှုသူ ဆက်ကြည့်ချင်အောင် Curiosity ဖန်တီးခြင်း

တို့ကို သဘာဝကျကျ ပြုလုပ်ရမည်။

Narrator စကားကို
စာအုပ်ဖတ်နေသလို မရေးပါနှင့်။

မြန်မာလူတစ်ယောက် Movie Recap ပြောနေသလို
စကားပြောပုံစံဖြင့် ရေးပါ။


==================================================
4. CHARACTER DIALOGUE
==================================================

Video ထဲမှာ ဇာတ်ကောင်တွေ
အပြန်အလှန်ပြောဆိုနေပါက
Dialogue ကို ထည့်ပါ။

Narrator တစ်ယောက်တည်းက
ဇာတ်ကောင်အားလုံးရဲ့ စကားကို
အကုန်ပြောနေသလို မရေးပါနှင့်။

ဥပမာ—

ကောင်မလေးက သူ့ကိုကြည့်ပြီး
"နင် ဒီကို ဘာလာလုပ်တာလဲ?"

ကောင်လေးက ခဏငြိမ်ပြီး
"ငါ မင်းကိုရှာနေတာ။"

သူမက မယုံသလိုကြည့်ပြီး
"တကယ်လား?"

ဒီလို Dialogue Flow ရှိနိုင်သည်။

သို့သော်—

IMPORTANT:

Video ထဲတွင် မကြားရသော
သို့မဟုတ် Video မှ အဓိပ္ပာယ်မသေချာသော
Dialogue ကို ကိုယ်တိုင်မတီထွင်ပါနှင့်။

Video ထဲမှာ Dialogue မရှိရင်
Dialogue အတင်းထည့်ရန် မလိုပါ။


==================================================
5. DIALOGUE + NARRATOR FLOW
==================================================

Script တစ်ခုလုံးကို Narrator စကားချည်း
ဆက်တိုက်မရေးပါနှင့်။

လိုအပ်သလို—

Narrator
→ Character A
→ Character B
→ Narrator
→ Character A
→ Character C
→ Narrator

ကဲ့သို့ Flow ဖြစ်အောင်ရေးပါ။

ဒါပေမယ့် Video ထဲက အခြေအနေကို
မဖျက်မိအောင် ဦးစားပေးပါ။


==================================================
6. TIKTOK / FACEBOOK / YOUTUBE HOOK
==================================================

Script အစမှာ ပရိသတ်ကို ဆွဲဆောင်နိုင်တဲ့
Hook တစ်ခု ထည့်ပါ။

Hook သည် Video ထဲမှာ တကယ်ဖြစ်လာမည့်
အရေးကြီးသောအဖြစ်အပျက်ကို အခြေခံရမည်။

ဥပမာ—

"ဒီကောင်လေး အဲ့ဒီအခန်းထဲကို ဝင်သွားတဲ့အချိန်မှာ
သူ့ဘဝတစ်ခုလုံး ပြောင်းလဲသွားမယ့်အကြောင်း
လုံးဝမသိခဲ့ပါဘူး။"

သို့မဟုတ် Video ထဲက ဖြစ်ရပ်နဲ့
ပိုကိုက်ညီတဲ့ Hook ကို ဖန်တီးပါ။

Clickbait လွန်လွန်ကဲကဲ မလုပ်ပါနှင့်။


==================================================
7. SUSPENSE
==================================================

Video ထဲမှာ Suspense ရှိပါက
ကြည့်ရှုသူ ဆက်ကြည့်ချင်အောင်
သဘာဝကျကျရေးပါ။

ဥပမာ—

"ဒါပေမယ့် သူမ မသိသေးတာက
အခန်းထဲမှာ သူမထင်ထားတာထက်
ပိုပြီး အန္တရာယ်ရှိတဲ့အရာတစ်ခု
စောင့်နေခဲ့တာပါ။"

သို့သော် Video ထဲမှာ
တကယ်မရှိတဲ့အရာကို မထည့်ပါနှင့်။


==================================================
8. STORY ORDER
==================================================

ဇာတ်လမ်းကို Video Timeline အတိုင်းရေးပါ။

အောက်ပါ Flow ကို လိုက်နာပါ—

HOOK
↓
ဇာတ်ကောင် / အခြေအနေ
↓
ပြဿနာစတင်ခြင်း
↓
ပဋိပက္ခ
↓
အရေးကြီးဖြစ်ရပ်များ
↓
ဇာတ်ကောင်များ၏ တုံ့ပြန်မှု
↓
ပြောင်းလဲမှု
↓
Climax
↓
ဖြေရှင်းမှု
↓
Ending

Video ရဲ့ အစိတ်အပိုင်းတွေကို
အကြောင်းမဲ့ မကျော်ပါနှင့်။


==================================================
9. NO INVENTED INFORMATION
==================================================

အလွန်အရေးကြီးသည်။

Video ထဲတွင် မမြင်ရ၊ မကြားရ၊
သေချာမသိရသော—

• Character Name
• Relationship
• Location
• Motivation
• Dialogue
• Backstory
• Event
• Ending

တို့ကို မိမိစိတ်ကူးဖြင့် မထည့်ပါနှင့်။

မသေချာလျှင် အတည်ပြုထားသကဲ့သို့
မရေးပါနှင့်။


==================================================
10. NATURAL MYANMAR
==================================================

မြန်မာပရိသတ် နားထောင်ရလွယ်အောင်
နေ့စဉ်စကားပြောသလို သဘာဝကျတဲ့
မြန်မာစကားကို အသုံးပြုပါ။

ဘာသာပြန်စက်လို မရေးပါနှင့်။

အလွန်စာအုပ်ဆန်သော စကားအသုံးအနှုန်း
မသုံးပါနှင့်။

ဝါကျများကို အလွန်ရှည်မထားပါနှင့်။


==================================================
11. TTS FRIENDLY
==================================================

ဒီ Script ကို AI Voice ဖြင့် ဖတ်မည်ဖြစ်သောကြောင့်—

• ဝါကျတိုတို
• သဘာဝကျသော punctuation
• အသံထွက်ဖတ်ရလွယ်
• စကားပြောသလိုရေး
• အဓိပ္ပာယ်ရှင်းလင်း

ဖြစ်ရမည်။


==================================================
12. OUTPUT FORMAT
==================================================

အောက်ပါ Technical Label များကို မထည့်ပါနှင့်—

[Visual]
[Scene]
[Camera]
[Shot]
[Action]
[Audio]
[Video]

ထို့အပြင်—

"Recap Script"
"Summary"
"Visual Cue"
"Scene 1"
"Scene 2"

ကဲ့သို့သော Technical Heading များလည်း
မလိုအပ်ပါ။

Narrator နှင့် Character Dialogue ကို
သဘာဝကျတဲ့ Paragraph Flow ဖြင့်ရေးပါ။

ဥပမာ—

"ညဘက်မှာ ကောင်လေးတစ်ယောက်
အိမ်တစ်အိမ်ရှေ့ကို ရောက်လာပါတယ်။
သူက တံခါးကို ကြည့်ပြီး ခဏရပ်နေပါတယ်။

တံခါးဖွင့်ပြီး အထဲဝင်သွားတဲ့အချိန်မှာ
အိမ်ထဲက မိန်းကလေးက သူ့ကိုမြင်ပြီး
'နင် ဘာလာလုပ်တာလဲ' လို့ မေးလိုက်ပါတယ်။

ကောင်လေးကတော့
'ငါ မင်းကို ပြောစရာရှိလို့' လို့ ပြန်ပြောပါတယ်။

ဒါပေမယ့် သူတို့နှစ်ယောက် စကားပြောနေတဲ့အချိန်မှာ
အပြင်ဘက်ကနေ ထူးဆန်းတဲ့အသံတစ်ခု
ကြားလိုက်ရပါတယ်။"

အထက်ပါပုံစံအတိုင်း
Visual + Narrator + Dialogue ကို
သဘာဝကျကျ ဆက်စပ်ရေးပါ။

FINAL OUTPUT သည်
TTS ထုတ်ရန်အတွက် သန့်ရှင်းသော
မြန်မာ Movie Recap Script သာ ဖြစ်ရမည်။
"""


        # =================================================
        # GENERATE
        # =================================================

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[
                uploaded_file,
                prompt
            ]
        )


        script_text = (
            response.text
            if response.text
            else ""
        )


        if not script_text.strip():

            return (
                "",
                "⚠️ Gemini က Script မထုတ်ပေးနိုင်ပါ။",
                None,
                None
            )


        # =================================================
        # CLEAN FOR TTS
        # =================================================

        clean_text = (
            clean_script_for_tts(
                script_text
            )
        )


        # =================================================
        # SRT
        # =================================================

        srt_file, zip_file = (
            generate_srt_and_zip(
                clean_text
            )
        )


        # =================================================
        # RETURN
        # =================================================

        return (
            clean_text,
            script_text,
            srt_file,
            zip_file
        )


    except Exception as e:

        print(
            "Gemini Error:",
            e
        )

        return (
            "",
            f"⚠️ Error ဖြစ်ပွားပါသည်:\n\n{str(e)}",
            None,
            None
        )


# =========================================================
# TEXT TO SPEECH
# =========================================================

async def generate_myanmar_tts(
    text,
    voice_choice,
    speed_percent
):

    if (
        not text
        or not text.strip()
    ):

        return (
            None,
            None,
            None,
            None
        )


    clean_text = (
        clean_script_for_tts(
            text
        )
    )


    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )


    try:

        speed_percent = int(
            speed_percent
        )

    except:

        speed_percent = 5


    rate_str = (
        f"{speed_percent:+d}%"
    )


    output_filename = (
        "recap_voice_over.mp3"
    )


    communicate = edge_tts.Communicate(
        clean_text,
        selected_voice,
        rate=rate_str
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
        zip_file
    )


# =========================================================
# TTS WRAPPER
# =========================================================

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

        print(
            "TTS Error:",
            e
        )

        return (
            None,
            None,
            None,
            None
        )


# =========================================================
# GRADIO UI
# =========================================================

with gr.Blocks(
    title="AI Movie Recap Studio Pro",
    theme=gr.themes.Soft()
) as demo:


    # =====================================================
    # GLOBAL PREVIEW CSS + JS
    # =====================================================

    gr.HTML(
        RATIO_CSS
        + RATIO_JS
    )


    # =====================================================
    # TITLE
    # =====================================================

    gr.Markdown(
        "# 🎬 AI Movie Recap Studio Pro"
    )

    gr.Markdown(
        "Video → AI Movie Recap → Burmese Voice → SRT"
    )


    # =====================================================
    # TABS
    # =====================================================

    with gr.Tabs():


        # =================================================
        # API KEY TAB
        # =================================================

        with gr.TabItem(
            "🔑 API Key Setting"
        ):

            gr.Markdown(
                "### 🔑 Gemini API Key"
            )

            api_key_input = gr.Textbox(
                label="Gemini API Key",
                type="password",
                placeholder="AIzaSy..."
            )

            save_key_btn = gr.Button(
                "💾 Key သိမ်းမည်",
                variant="primary"
            )

            key_status = gr.Markdown(
                ""
            )


        # =================================================
        # TAB 1
        # =================================================

        with gr.TabItem(
            "1️⃣ Video Analysis & Script"
        ):

            with gr.Row():


                # -----------------------------------------
                # LEFT SIDE
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    video_file = gr.Video(
                        label="📹 Video File တင်ရန်",
                        sources=["upload"],
                        type="filepath"
                    )


                    video_url = gr.Textbox(
                        label="🔗 Video URL Link",
                        placeholder=(
                            "https://youtube.com/..."
                        )
                    )


                    load_link_btn = gr.Button(
                        "🔍 Link မှ Video ရယူမည်",
                        variant="secondary"
                    )


                    gr.Markdown(
                        "### 📐 Preview Aspect Ratio"
                    )


                    ratio_picker = gr.Radio(
                        choices=[
                            "9:16",
                            "3:4",
                            "1:1",
                            "16:9"
                        ],
                        value="9:16",
                        label="Ratio ရွေးချယ်ပါ"
                    )


                    ratio_status = gr.HTML(
                        change_ratio(
                            "9:16"
                        )
                    )


                    gen_script_btn = gr.Button(
                        "🚀 Video မှ Script ထုတ်မည်",
                        variant="primary"
                    )


                # -----------------------------------------
                # RIGHT SIDE
                # -----------------------------------------

                with gr.Column(
                    scale=1
                ):

                    gr.Markdown(
                        "### 📺 Video Preview"
                    )


                    tab1_preview = gr.Video(
                        label="Video Preview",
                        show_label=False,
                        elem_id="tab1_preview_container",
                        interactive=False
                    )


                    gr.Markdown(
                        "### 📝 Generated Movie Recap"
                    )


                    script_display = gr.Markdown(
                        ""
                    )


                    with gr.Row():

                        srt_download_tab1 = gr.File(
                            label="📄 SRT Download"
                        )

                        zip_download_tab1 = gr.File(
                            label="📦 ZIP Download"
                        )


        # =================================================
        # TAB 2
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
                            "🎙️ မြန်မာ Movie Recap Script"
                        ),
                        lines=15,
                        placeholder=(
                            "Tab 1 မှ Script အလိုအလျောက် "
                            "ရောက်ရှိပါမည်..."
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
                        label="🎙️ အသံရွေးချယ်ပါ"
                    )


                    speed_slider = gr.Slider(
                        minimum=-30,
                        maximum=50,
                        value=5,
                        step=1,
                        label=(
                            "⚡ အသံအမြန်နှုန်း "
                            "Speed (%)"
                        )
                    )


                    gen_voice_btn = gr.Button(
                        "⚡ မြန်မာအသံထုတ်မည်",
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
                        label="🔊 အသံနားဆင်ရန်"
                    )


                    mp3_download = gr.File(
                        label="🎵 MP3 Download"
                    )


                    with gr.Row():

                        srt_download_tab2 = gr.File(
                            label="📄 SRT Download"
                        )

                        zip_download_tab2 = gr.File(
                            label="📦 ZIP Download"
                        )


    # =====================================================
    # EVENTS
    # =====================================================


    # -----------------------------------------
    # SAVE API KEY
    # -----------------------------------------

    save_key_btn.click(
        fn=save_api_key,
        inputs=api_key_input,
        outputs=key_status
    )


    # -----------------------------------------
    # VIDEO FILE → PREVIEW
    # -----------------------------------------

    video_file.change(
        fn=preview_uploaded_video,
        inputs=video_file,
        outputs=tab1_preview
    )


    # -----------------------------------------
    # LINK → VIDEO PREVIEW
    # -----------------------------------------

    load_link_btn.click(
        fn=load_video_link,
        inputs=video_url,
        outputs=tab1_preview
    )


    # -----------------------------------------
    # RATIO CHANGE
    # -----------------------------------------

    ratio_picker.change(
        fn=change_ratio,
        inputs=ratio_picker,
        outputs=ratio_status
    )


    # -----------------------------------------
    # GENERATE SCRIPT
    # -----------------------------------------

    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[
            video_file,
            video_url,
            ratio_picker
        ],
        outputs=[
            input_text,
            script_display,
            srt_download_tab1,
            zip_download_tab1
        ]
    )


    # -----------------------------------------
    # TTS
    # -----------------------------------------

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
