import gradio as gr
import edge_tts
import asyncio
import os
import re
import time
import zipfile
import yt_dlp
from google import genai


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
        return "✅ API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"

    return "⚠️ ကျေးဇူးပြု၍ မှန်ကန်သော Gemini API Key ထည့်ပါ။"


# =========================================================
# CLEAN SCRIPT
# =========================================================

def clean_script_for_tts(script_text):

    if not script_text:
        return ""

    lines = script_text.split("\n")
    cleaned_lines = []

    for line in lines:

        line_str = line.strip()

        if re.match(
            r'^\[(Visual|Scene|Video|Audio).*?\]',
            line_str,
            re.IGNORECASE
        ):
            continue

        if (
            "recap script" in line_str.lower()
            or "ဒီမှာ" in line_str
            or line_str.startswith("---")
        ):
            continue

        line_str = re.sub(
            r'^[🎙️🗣️💬📢\s]*Narrator:\s*',
            '',
            line_str,
            flags=re.IGNORECASE
        )

        line_str = re.sub(
            r'^[🎙️🗣️💬📢\s]*\[.*?\]:\s*',
            '',
            line_str
        )

        line_str = re.sub(
            r'^[🎙️🗣️💬📢\s]*.*?:\s*',
            '',
            line_str
        )

        line_str = (
            line_str
            .replace("**", "")
            .replace("#", "")
            .replace("__", "")
            .replace("`", "")
        )

        if line_str.strip():
            cleaned_lines.append(line_str.strip())

    return "\n".join(cleaned_lines)


# =========================================================
# SRT
# =========================================================

def generate_srt_and_zip(script_text):

    clean_lines = clean_script_for_tts(script_text).split("\n")

    srt_content = ""
    start_sec = 0
    subtitle_index = 1

    for line in clean_lines:

        if not line.strip():
            continue

        duration = max(3, len(line) // 10)

        end_sec = start_sec + duration

        start_time = (
            f"{start_sec//3600:02d}:"
            f"{(start_sec%3600)//60:02d}:"
            f"{start_sec%60:02d},000"
        )

        end_time = (
            f"{end_sec//3600:02d}:"
            f"{(end_sec%3600)//60:02d}:"
            f"{end_sec%60:02d},000"
        )

        srt_content += (
            f"{subtitle_index}\n"
            f"{start_time} --> {end_time}\n"
            f"{line}\n\n"
        )

        subtitle_index += 1
        start_sec = end_sec

    srt_filename = "myanmar_subtitle.srt"
    zip_filename = "myanmar_subtitle.zip"

    with open(
        srt_filename,
        "w",
        encoding="utf-8-sig"
    ) as f:
        f.write(srt_content)

    with zipfile.ZipFile(
        zip_filename,
        "w"
    ) as zipf:
        zipf.write(srt_filename)

    return srt_filename, zip_filename


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_video_from_link(link):

    if not link or not link.strip():
        return None

    out_template = "temp_downloaded_video.%(ext)s"

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_template,
        "quiet": True,
        "overwrites": True
    }

    try:

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                link.strip(),
                download=True
            )

            filename = ydl.prepare_filename(info)

            if os.path.exists(filename):
                return filename

    except Exception as e:

        print(f"Download Error: {e}")

        return None

    return None


# =========================================================
# RATIO PREVIEW CSS + JS
# =========================================================

RATIO_PREVIEW_CSS = """
<style>

#tab1_preview_container {
    width: 100% !important;
    max-width: 650px !important;
    margin: 0 auto !important;
}

#tab1_preview_container .video-container {
    width: 100% !important;
    height: auto !important;
    aspect-ratio: 9 / 16 !important;
    background: #000 !important;
    overflow: hidden !important;
    border-radius: 12px !important;
}

#tab1_preview_container video {
    width: 100% !important;
    height: 100% !important;
    object-fit: contain !important;
    background: #000 !important;
}

</style>
"""


RATIO_PREVIEW_JS = """
<script>

function applyVideoRatio(ratio) {

    const container =
        document.querySelector(
            '#tab1_preview_container .video-container'
        );

    if (!container) return;

    const video =
        container.querySelector('video');

    if (!video) return;

    let aspect = '9 / 16';
    let maxWidth = '360px';

    if (ratio === '9:16') {
        aspect = '9 / 16';
        maxWidth = '360px';
    }

    if (ratio === '3:4') {
        aspect = '3 / 4';
        maxWidth = '420px';
    }

    if (ratio === '1:1') {
        aspect = '1 / 1';
        maxWidth = '500px';
    }

    if (ratio === '16:9') {
        aspect = '16 / 9';
        maxWidth = '650px';
    }

    container.style.aspectRatio = aspect;
    container.style.width = '100%';
    container.style.maxWidth = maxWidth;

    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'contain';
}

function watchVideoPreview() {

    const container =
        document.querySelector(
            '#tab1_preview_container'
        );

    if (!container) return;

    const observer =
        new MutationObserver(function() {

            const video =
                container.querySelector('video');

            if (video) {

                const selected =
                    document.querySelector(
                        'input[name="ratio_picker"]'
                    );

                applyVideoRatio(
                    window.currentSelectedRatio || '9:16'
                );
            }

        });

    observer.observe(
        container,
        {
            childList: true,
            subtree: true
        }
    );
}

window.currentSelectedRatio = '9:16';

setTimeout(
    watchVideoPreview,
    500
);

</script>
"""


# =========================================================
# RATIO EVENT HTML
# =========================================================

def ratio_change_html(ratio):

    safe_ratio = ratio or "9:16"

    return f"""
    <div style="display:none"
         data-selected-ratio="{safe_ratio}">
    </div>

    <script>

    window.currentSelectedRatio = "{safe_ratio}";

    function updateRatioNow() {{

        const container =
            document.querySelector(
                '#tab1_preview_container .video-container'
            );

        if (!container) return;

        const video =
            container.querySelector('video');

        if (!video) return;

        let aspect = '9 / 16';
        let maxWidth = '360px';

        if ("{safe_ratio}" === "9:16") {{
            aspect = '9 / 16';
            maxWidth = '360px';
        }}

        if ("{safe_ratio}" === "3:4") {{
            aspect = '3 / 4';
            maxWidth = '420px';
        }}

        if ("{safe_ratio}" === "1:1") {{
            aspect = '1 / 1';
            maxWidth = '500px';
        }}

        if ("{safe_ratio}" === '16:9') {{
            aspect = '16 / 9';
            maxWidth = '650px';
        }}

        container.style.aspectRatio = aspect;
        container.style.maxWidth = maxWidth;
        container.style.width = '100%';

        video.style.width = '100%';
        video.style.height = '100%';
        video.style.objectFit = 'contain';
    }}

    let attempts = 0;

    const timer = setInterval(() => {{

        updateRatioNow();

        attempts++;

        if (attempts > 30) {{
            clearInterval(timer);
        }}

    }}, 200);

    </script>
    """


# =========================================================
# LOAD LINK
# =========================================================

def load_tab1_link(link):

    if not link or not link.strip():
        return None

    return download_video_from_link(link)


# =========================================================
# ANALYZE VIDEO
# =========================================================

def analyze_and_generate_script(
    video_file,
    video_link,
    ratio_choice
):

    global SAVED_API_KEY

    if not SAVED_API_KEY:

        return (
            "",
            "⚠️ API Key မရှိသေးပါ။ "
            "ကျေးဇူးပြု၍ API Key Setting တွင် API Key ထည့်ပါ။",
            None,
            None
        )

    target_media = (
        video_file
        if video_file
        else download_video_from_link(video_link)
    )

    if (
        not target_media
        or not os.path.exists(target_media)
    ):

        return (
            "",
            "⚠️ ဗီဒီယိုဖိုင်တင်ပါ "
            "သို့မဟုတ် Link မှန်ကန်စွာထည့်ပါ။",
            None,
            None
        )

    try:

        client = genai.Client(
            api_key=SAVED_API_KEY
        )

    except Exception as e:

        return (
            "",
            f"⚠️ Client Error: {str(e)}",
            None,
            None
        )

    selected_ratio = (
        ratio_choice
        if ratio_choice
        else "9:16"
    )

    try:

        uploaded_file = client.files.upload(
            file=target_media
        )

        while uploaded_file.state.name == "PROCESSING":

            time.sleep(3)

            uploaded_file = client.files.get(
                name=uploaded_file.name
            )

        if uploaded_file.state.name == "FAILED":

            return (
                "",
                "⚠️ Gemini ဘက်မှ ဗီဒီယိုဖတ်မရပါ။",
                None,
                None
            )

        prompt = f"""
သင်သည် မြန်မာ Movie Recapper တစ်ဦးဖြစ်သည်။

ပေးထားသော ဗီဒီယိုကို အစမှအဆုံး သေချာကြည့်ရှုပါ။

Aspect Ratio:
{selected_ratio}

အောက်ပါစည်းမျဉ်းများအတိုင်း
မြန်မာ Movie Recap Script ရေးပါ။

1. Video ထဲတွင် တကယ်ဖြစ်ပျက်သောအရာကိုသာရေးပါ။
2. ဇာတ်လမ်းမတီထွင်ပါနှင့်။
3. Visual cue မရေးပါနှင့်။
4. Scene / Visual / Audio label မထည့်ပါနှင့်။
5. အသံထွက်ဖတ်ရန် သဘာဝကျသော မြန်မာစကားဖြင့်ရေးပါ။
6. ဇာတ်လမ်းကို စိတ်ဝင်စားဖွယ် Movie Recap ပုံစံရေးပါ။
7. ဇာတ်ကောင်ပြောသောစကားရှိလျှင် အဓိပ္ပာယ်မပျက်အောင်
   မြန်မာလို သဘာဝကျကျရေးပါ။
8. Video ထဲတွင်မရှိသော Dialogue မထည့်ပါနှင့်။
9. Output သည် TTS အသံထုတ်ရန် အဆင်ပြေသော
   plain Burmese text ဖြစ်ရမည်။
"""

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[
                uploaded_file,
                prompt
            ]
        )

        script_text = response.text

        clean_text = clean_script_for_tts(
            script_text
        )

        srt_file, zip_file = (
            generate_srt_and_zip(
                script_text
            )
        )

        return (
            clean_text,
            script_text,
            srt_file,
            zip_file
        )

    except Exception as e:

        return (
            "",
            f"⚠️ Error ဖြစ်ပွားပါသည်: {str(e)}",
            None,
            None
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
            None
        )

    clean_text = clean_script_for_tts(
        text
    )

    selected_voice = VOICES.get(
        voice_choice,
        "my-MM-ThihaNeural"
    )

    rate_str = f"{speed_percent:+d}%"

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


def tts_interface(
    text,
    voice_choice,
    speed
):

    return asyncio.run(
        generate_myanmar_tts(
            text,
            voice_choice,
            speed
        )
    )


# =========================================================
# GRADIO UI
# =========================================================

with gr.Blocks(
    title="AI Movie Recap Studio Pro",
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown(
        "# 🎬 AI Movie Recap Studio Pro"
    )

    # Global CSS
    gr.HTML(
        RATIO_PREVIEW_CSS
        + RATIO_PREVIEW_JS
    )

    with gr.Tabs():

        # =================================================
        # API KEY
        # =================================================

        with gr.TabItem(
            "🔑 API Key Setting"
        ):

            api_key_input = gr.Textbox(
                label="Gemini API Key",
                type="password",
                placeholder="AIzaSy..."
            )

            save_key_btn = gr.Button(
                "💾 Key သိမ်းမည်",
                variant="primary"
            )

            key_status = gr.Markdown("")


        # =================================================
        # TAB 1
        # =================================================

        with gr.TabItem(
            "1️⃣ Video Analysis & Script"
        ):

            with gr.Row():

                # LEFT
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
                        placeholder="https://..."
                    )

                    load_link_btn = gr.Button(
                        "🔍 Link မှ ဗီဒီယိုရယူမည်",
                        variant="secondary"
                    )

                    ratio_picker = gr.Radio(
                        choices=[
                            "9:16",
                            "3:4",
                            "1:1",
                            "16:9"
                        ],
                        value="9:16",
                        label="📐 Preview Aspect Ratio"
                    )

                    ratio_status = gr.HTML(
                        ratio_change_html("9:16")
                    )

                    gen_script_btn = gr.Button(
                        "🚀 Script ထုတ်မည်",
                        variant="primary"
                    )


                # RIGHT
                with gr.Column(
                    scale=1
                ):

                    gr.Markdown(
                        "### 📺 Video Preview"
                    )

                    tab1_preview = gr.Video(
                        label="Preview",
                        elem_id="tab1_preview_container",
                        show_label=False,
                        interactive=False
                    )

                    script_display = gr.Markdown(
                        label="ထွက်ရှိလာသော Script"
                    )

                    with gr.Row():

                        srt_download_tab1 = gr.File(
                            label="📄 SRT"
                        )

                        zip_download_tab1 = gr.File(
                            label="📦 ZIP"
                        )


        # =================================================
        # TAB 2
        # =================================================

        with gr.TabItem(
            "2️⃣ Text-to-Speech"
        ):

            with gr.Row():

                with gr.Column(
                    scale=1
                ):

                    input_text = gr.Textbox(
                        label="🎙️ စာသား",
                        lines=12,
                        placeholder="စာသားများ ရိုက်ထည့်နိုင်ပါသည်..."
                    )

                    voice_dropdown = gr.Dropdown(
                        choices=list(
                            VOICES.keys()
                        ),
                        value=(
                            "Thiha "
                            "(အမျိုးသားအသံ) - Natural"
                        ),
                        label="ရွေးချယ်မည့် အသံ"
                    )

                    speed_slider = gr.Slider(
                        minimum=-30,
                        maximum=50,
                        value=5,
                        step=1,
                        label="အသံအမြန်နှုန်း Speed (%)"
                    )

                    gen_voice_btn = gr.Button(
                        "⚡ အသံထုတ်မည်",
                        variant="primary"
                    )


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
                            label="📄 SRT"
                        )

                        zip_download_tab2 = gr.File(
                            label="📦 ZIP"
                        )


    # =====================================================
    # EVENTS
    # =====================================================

    save_key_btn.click(
        fn=save_api_key,
        inputs=api_key_input,
        outputs=key_status
    )


    # ---------------------------------------------
    # VIDEO FILE → PREVIEW
    # ---------------------------------------------

    video_file.change(
        fn=lambda video: video,
        inputs=video_file,
        outputs=tab1_preview
    )


    # ---------------------------------------------
    # LINK → PREVIEW
    # ---------------------------------------------

    load_link_btn.click(
        fn=load_tab1_link,
        inputs=video_url,
        outputs=tab1_preview
    )


    # ---------------------------------------------
    # RATIO CHANGE
    # ---------------------------------------------

    ratio_picker.change(
        fn=ratio_change_html,
        inputs=ratio_picker,
        outputs=ratio_status
    )


    # ---------------------------------------------
    # GENERATE SCRIPT
    # ---------------------------------------------

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


    # ---------------------------------------------
    # TTS
    # ---------------------------------------------

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
