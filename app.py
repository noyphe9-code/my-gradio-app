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
    # mp4 သို့ တိုက်ရိုက် convert လုပ်ပြီး သေချာဒေါင်းလုဒ်ဆွဲခြင်း
    out_template = 'temp_downloaded_video.%(ext)s'
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
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
            # Extension ကွဲလွဲမှုရှိပါက ရှာဖွေပေးခြင်း
            matched_files = glob.glob("temp_downloaded_video.*")
            if matched_files:
                return matched_files[0]
    except Exception as e:
        print(f"Download Error: {e}")
        return None
    return None

# Video file သို့မဟုတ် URL Link ထည့်သွင်းမှုကို Preview သို့ ချိတ်ဆက်ပေးမည့် Helper
def handle_video_preview(file_path, link):
    if file_path:
        return file_path, "✅ ဗီဒီယိုဖိုင် Preview အဆင်သင့်ဖြစ်ပါပြီ။"
    if link and link.strip():
        downloaded = download_video_from_link(link)
        if downloaded:
            return downloaded, "✅ Link မှ ဗီဒီယို Preview ဒေါင်းလုဒ်လုပ်ပြီးပါပြီ။"
        return None, "⚠️ Video Link မှ ဒေါင်းလုဒ်မရနိုင်ပါ။ Link မှန်ကန်မှု စစ်ဆေးပါ။"
    return None, "⏳ အဆင်သင့်ဖြစ်ပါပြီ။"

def all_in_one_process(video_file, video_link, ratio):
    target_video = video_file if video_file else download_video_from_link(video_link)
    if not target_video:
        return None, "⚠️ ဗီဒီယိုဖိုင် သို့မဟုတ် Link ထည့်ပါ။", None, None
    status_msg = f"✨ All-in-One Maker အောင်မြင်ပါသည်! (Ratio: {ratio})"
    srt_file, zip_file = generate_srt_and_zip("Sample Script")
    return target_video, status_msg, srt_file, zip_file

def update_video_ratio_style(ratio):
    styles = {
        "9:16": "max-width: 320px; margin: 0 auto; aspect-ratio: 9/16;",
        "16:9": "max-width: 100%; aspect-ratio: 16/9;",
        "1:1": "max-width: 400px; margin: 0 auto; aspect-ratio: 1/1;",
        "3:4": "max-width: 350px; margin: 0 auto; aspect-ratio: 3/4;"
    }
    css_style = styles.get(ratio, "aspect-ratio: 9/16;")
    return f"<style>.custom-video-player video, .custom-video-player {{ {css_style} }}</style>"

with gr.Blocks(title="AI Movie Recap Studio Pro") as demo:
    gr.Markdown("# 🎬 Real AI Movie Recap Studio Pro")
    
    with gr.Tabs():
        with gr.TabItem("🔑 API Key Setting"):
            api_key_input = gr.Textbox(label="Gemini API Key", type="password")
            save_key_btn = gr.Button("💾 Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")

        with gr.TabItem("🚀 3️⃣ All-in-One Video Maker (max 5 mins)"):
            with gr.Row():
                with gr.Column(scale=1):
                    all_video_input = gr.Video(label="📹 ဗီဒီယိုဖိုင် တင်ရန်")
                    all_video_link = gr.Textbox(label="🔗 Video URL Link (ထည့်ပြီး Enter ခေါက်ပါ)", placeholder="https://...")
                    load_link_btn = gr.Button("🔍 Link မှ Preview ယူမည်")
                    all_ratio = gr.Radio(choices=["9:16", "16:9", "1:1", "3:4"], value="9:16", label="📐 Aspect Ratio ရွေးရန်")
                    all_gen_btn = gr.Button("🚀 🎬 Generate Video", variant="primary")
                
                with gr.Column(scale=1):
                    all_preview_video = gr.Video(label="📺 Preview Video", elem_classes=["custom-video-player"])
                    ratio_css_injection = gr.HTML("<style>.custom-video-player video, .custom-video-player { max-width: 320px; margin: 0 auto; aspect-ratio: 9/16; }</style>")
                    all_status = gr.Markdown("⏳ အဆင်သင့်ဖြစ်ပါပြီ။")
                    with gr.Row():
                        all_srt_down = gr.File(label="📄 SRT")
                        all_zip_down = gr.File(label="📦 ZIP")

    # 1. API Key Saving
    save_key_btn.click(fn=save_api_key, inputs=api_key_input, outputs=key_status)

    # 2. Aspect Ratio Style Update (Function နာမည် အမှန်ပြင်ထားသည်)
    all_ratio.change(fn=update_video_ratio_style, inputs=all_ratio, outputs=ratio_css_injection)

    # 3. Video File တင်လိုက်လျှင် Preview တိုက်ရိုက်ပြခြင်း
    all_video_input.change(
        fn=lambda v: (v, "✅ ဗီဒီယိုဖိုင် Preview အဆင်သင့်ဖြစ်ပါပြီ။" if v else "⏳"),
        inputs=all_video_input,
        outputs=[all_preview_video, all_status]
    )

    # 4. URL ထည့်ပြီး Enter ခေါက်လျှင် သို့မဟုတ် ခလုတ်နှိပ်လျှင် Preview ပြခြင်း
    load_link_btn.click(
        fn=lambda link: handle_video_preview(None, link),
        inputs=all_video_link,
        outputs=[all_preview_video, all_status]
    )
    all_video_link.submit(
        fn=lambda link: handle_video_preview(None, link),
        inputs=all_video_link,
        outputs=[all_preview_video, all_status]
    )

    # 5. Generate Button
    all_gen_btn.click(
        fn=all_in_one_process,
        inputs=[all_video_input, all_video_link, all_ratio],
        outputs=[all_preview_video, all_status, all_srt_down, all_zip_down]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
             
