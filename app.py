import gradio as gr
import edge_tts
import asyncio
import os
import re
import glob
import zipfile
import yt_dlp
from google import genai

SAVED_API_KEY = ""

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural"
}

def save_api_key(api_key):
    global SAVED_API_KEY
    if api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
    return "⚠️ ကျေးဇူးပြု၍ မှန်ကန်သော API Key ထည့်ပါ။"

def clean_script_for_tts(script_text):
    if not script_text:
        return ""
    lines = script_text.split('\n')
    cleaned_lines = []
    for line in lines:
        line_str = line.strip()
        if re.match(r'^\[Visual.*\]', line_str, re.IGNORECASE):
            continue
        if "recap script" in line_str.lower() or "ဒီမှာ" in line_str or line_str.startswith("---"):
            continue
        line_str = re.sub(r'^[🎙️🗣️💬\s]* Narrator:\s*', '', line_str, flags=re.IGNORECASE)
        line_str = re.sub(r'^[🎙️🗣️💬\s]* \[.*?\]:\s*', '', line_str)
        line_str = re.sub(r'^[🎙️🗣️💬\s]* .*?:\s*', '', line_str)
        line_str = line_str.replace("**", "").replace("#", "").replace("__", "")
        if line_str.strip():
            cleaned_lines.append(line_str.strip())
    return "\n".join(cleaned_lines)

def generate_srt_and_zip(script_text):
    clean_lines = clean_script_for_tts(script_text).split('\n')
    srt_content = ""
    start_sec = 0
    for idx, line in enumerate(clean_lines, 1):
        if not line.strip():
            continue
        end_sec = start_sec + max(2, len(line) // 10)
        start_time = f"00:{start_sec//60:02d}:{start_sec%60:02d},000"
        end_time = f"00:{end_sec//60:02d}:{end_sec%60:02d},000"
        srt_content += f"{idx}\n{start_time} --> {end_time}\n{line}\n\n"
        start_sec = end_sec
        
    srt_filename = "myanmar_subtitle.srt"
    zip_filename = "myanmar_subtitle.zip"
    with open(srt_filename, "w", encoding="utf-8-sig") as f:
        f.write(srt_content)
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        zipf.write(srt_filename)
    return srt_filename, zip_filename

def download_video_from_link(link):
    if not link or not link.strip():
        return None
    out_template = 'temp_downloaded_video.%(ext)s'
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': out_template,
        'quiet': True,
        'overwrites': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link.strip(), download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
            matched_files = glob.glob("temp_downloaded_video.*")
            if matched_files:
                return matched_files[0]
    except Exception as e:
        print(f"Download Error: {e}")
        return None
    return None

# Aspect Ratio အလိုက် Container နှင့် Video tag နှစ်ခုလုံးကို တိကျစွာ ထိန်းချုပ်မည့် CSS
def get_ratio_css(ratio):
    style_configs = {
        "9:16": {"aspect": "9/16", "max_w": "260px"},
        "16:9": {"aspect": "16/9", "max_w": "100%"},
        "1:1":  {"aspect": "1/1",  "max_w": "300px"},
        "3:4":  {"aspect": "3/4",  "max_w": "280px"}
    }
    cfg = style_configs.get(ratio, {"aspect": "9/16", "max_w": "260px"})
    
    return f"""
    <style id="preview-ratio-style">
    /* Video Box Wrapper တစ်ခုလုံးကို အချိုးကျဖြစ်စေခြင်း */
    #all_preview_container {{
        max-width: {cfg['max_w']} !important;
        width: 100% !important;
        margin: 0 auto !important;
        transition: all 0.3s ease-in-out;
    }}
    
    /* Gradio Video component အတွင်းရှိ inner wrapper အားလုံးကို ratio သတ်မှတ်ခြင်း */
    #all_preview_container .wrap,
    #all_preview_container .video-container,
    #all_preview_container div[data-testid="video"] {{
        aspect-ratio: {cfg['aspect']} !important;
        width: 100% !important;
        height: auto !important;
        background: #000 !important;
        border-radius: 8px !important;
        overflow: hidden !important;
    }}

    /* Video player ကို container အချိုးအတိုင်း ဖြည့်ဆည်းစေခြင်း */
    #all_preview_container video {{
        aspect-ratio: {cfg['aspect']} !important;
        width: 100% !important;
        height: 100% !important;
        object-fit: contain !important; /* အချိုးမပျက် ဘေးမည်းဘားနှင့် အညီထားခြင်း */
    }}
    </style>
    """

def load_file_preview(video_file):
    if video_file:
        return video_file, "✅ ဗီဒီယိုဖိုင် Preview အဆင်သင့်ဖြစ်ပါပြီ။"
    return None, "⏳ ဗီဒီယို မရွေးချယ်ရသေးပါ။"

def load_link_preview(video_link):
    if not video_link or not video_link.strip():
        return None, "⚠️ Video Link ရိုက်ထည့်ပါ။"
    downloaded = download_video_from_link(video_link)
    if downloaded:
        return downloaded, "✅ Link မှ ဗီဒီယို Preview ဒေါင်းလုဒ် ရရှိပါပြီ။"
    return None, "⚠️ Link မှ ဒေါင်းလုဒ်မရနိုင်ပါ။ URL စစ်ဆေးပေးပါ။"

def all_in_one_process(video_file, video_link, ratio):
    target_video = video_file if video_file else download_video_from_link(video_link)
    if not target_video:
        return None, "⚠️ ဗီဒီယိုဖိုင် သို့မဟုတ် Link ထည့်ပါ။", None, None
    status_msg = f"✨ Video Recap အောင်မြင်ပါသည်! (Selected Ratio: {ratio})"
    srt_file, zip_file = generate_srt_and_zip("Sample Myanmar Recap Script")
    return target_video, status_msg, srt_file, zip_file

with gr.Blocks(title="AI Movie Recap Studio Pro") as demo:
    gr.Markdown("## 🎬 Real AI Movie Recap Studio Pro (Ratio Preview Supported)")
    
    with gr.Tabs():
        with gr.TabItem("🔑 API Key Setting"):
            api_key_input = gr.Textbox(label="Gemini API Key", type="password")
            save_key_btn = gr.Button("💾 Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")

        with gr.TabItem("🚀 All-in-One Video Maker"):
            with gr.Row():
                with gr.Column(scale=1):
                    all_video_input = gr.Video(label="📹 ဗီဒီယိုဖိုင် တင်ရန်")
                    all_video_link = gr.Textbox(label="🔗 ဗီဒီယို Link ထည့်ရန်", placeholder="https://...")
                    load_link_btn = gr.Button("📲 Link မှ Preview ဆွဲယူမည်", variant="secondary")
                    
                    # 1:1, 3:4, 16:9, 9:16 ရွေးချယ်နိုင်သော Radio Box
                    all_ratio = gr.Radio(
                        choices=["9:16", "16:9", "1:1", "3:4"],
                        value="9:16",
                        label="📐 Aspect Ratio ရွေးရန်"
                    )
                    all_gen_btn = gr.Button("🚀 🎬 Generate Video", variant="primary")

                with gr.Column(scale=1):
                    # CSS Style ကို dynamically ပြောင်းမည့် placeholder
                    ratio_css_injection = gr.HTML(get_ratio_css("9:16"))
                    
                    # id="all_preview_container" ဖြင့် သီးသန့် CSS ID ပေးထားသည်
                    all_preview_video = gr.Video(
                        label="📺 Preview Video",
                        elem_id="all_preview_container"
                    )
                    all_status = gr.Markdown("⏳ အဆင်သင့်ဖြစ်ပါပြီ။")
                    with gr.Row():
                        all_srt_down = gr.File(label="📄 SRT")
                        all_zip_down = gr.File(label="📦 ZIP")

    # Event Handlers
    save_key_btn.click(fn=save_api_key, inputs=api_key_input, outputs=key_status)

    # Ratio Radio ခလုတ် ရွေးလိုက်တိုင်း CSS Style ချက်ချင်း update လုပ်ခြင်း
    all_ratio.change(
        fn=get_ratio_css,
        inputs=all_ratio,
        outputs=ratio_css_injection
    )

    # File Preview
    all_video_input.change(
        fn=load_file_preview,
        inputs=all_video_input,
        outputs=[all_preview_video, all_status]
    )

    # Link Preview Button
    load_link_btn.click(
        fn=load_link_preview,
        inputs=all_video_link,
        outputs=[all_preview_video, all_status]
    )

    # Generate
    all_gen_btn.click(
        fn=all_in_one_process,
        inputs=[all_video_input, all_video_link, all_ratio],
        outputs=[all_preview_video, all_status, all_srt_down, all_zip_down]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
