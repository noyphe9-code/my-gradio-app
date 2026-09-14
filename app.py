import gradio as gr
import edge_tts
import asyncio
import os
import re
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
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'temp_downloaded_video.%(ext)s',
        'quiet': True,
        'overwrites': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link.strip(), download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
    except Exception as e:
        print(f"Download Error: {e}")
        return None
    return None

def analyze_and_generate_script(video_file, video_link, ratio_choice):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        return "", "⚠️ API Key မရှိသေးပါ။ ကျေးဇူးပြု၍ '🔑 API Key Setting' တွင် API Key ထည့်ပါ။", None, None
    
    # Video File သို့မဟုတ် Link မှ ဒေါင်းလုဒ်ရယူထားသော ဖိုင်ကို အသုံးပြုခြင်း
    target_media = video_file if video_file else download_video_from_link(video_link)
    if not target_media or not os.path.exists(target_media):
        return "", "⚠️ ကျေးဇူးပြု၍ ဗီဒီယိုဖိုင် တင်ပါ သို့မဟုတ် မှန်ကန်သော ဗီဒီယို Link ထည့်ပေးပါ။", None, None

    try:
        client = genai.Client(api_key=SAVED_API_KEY)
    except Exception as e:
        return "", f"⚠️ API Key မှားယွင်းနေပါသည်။ Error: {str(e)}", None, None

    selected_ratio = ratio_choice.split(" ")[0]

    try:
        uploaded_file = client.files.upload(file=target_media)
        prompt = f"Aspect Ratio ({selected_ratio}) နှင့် လိုက်ဖက်မည့် မြန်မာလို Movie Recap Script အပြည့်အစုံ ရေးပေးပါ။"
        response = client.models.generate_content(model='gemini-2.5-flash', contents=[uploaded_file, prompt])
        script_text = response.text
        clean_text_for_tts = clean_script_for_tts(script_text)
        srt_file, zip_file = generate_srt_and_zip(script_text)
        return clean_text_for_tts, script_text, srt_file, zip_file
    except Exception as e:
        return "", f"⚠️ Error: {str(e)}", None, None

async def generate_myanmar_tts(text, voice_choice, speed_percent):
    if not text or not text.strip():
        return None, None, None, None
    clean_text = clean_script_for_tts(text)
    selected_voice = VOICES[voice_choice]
    rate_str = f"{speed_percent:+d}%"
    output_filename = "recap_voice_over.mp3"
    communicate = edge_tts.Communicate(clean_text, selected_voice, rate=rate_str)
    await communicate.save(output_filename)
    srt_file, zip_file = generate_srt_and_zip(clean_text)
    return output_filename, output_filename, srt_file, zip_file

def tts_interface(text, voice_choice, speed):
    return asyncio.run(generate_myanmar_tts(text, voice_choice, speed))

with gr.Blocks(title="AI Movie Recap Studio Pro") as demo:
    gr.Markdown("# 🎬 Real AI Movie Recap Studio Pro")
    
    with gr.Tabs():
        # API Key Setting Tab
        with gr.TabItem("🔑 API Key Setting"):
            api_key_input = gr.Textbox(label="Gemini API Key", type="password")
            save_key_btn = gr.Button("💾 Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")

        # 1️⃣ Video Analysis & Script Tab (မူလအတိုင်း File ရော Link ရော ပါဝင်သည်)
        with gr.TabItem("1️⃣ Video Analysis & Script"):
            with gr.Row():
                with gr.Column():
                    video_file = gr.Video(label="📹 Video File")
                    video_url = gr.Textbox(label="🔗 Video Link (YouTube/Facebook/TikTok...)")
                    ratio_picker = gr.Radio(choices=["9:16", "16:9", "1:1", "3:4"], value="9:16", label="📐 Aspect Ratio")
                    gen_script_btn = gr.Button("🚀 Script ထုတ်မည်", variant="primary")
                with gr.Column():
                    script_display = gr.Markdown(label="Script Output")
                    with gr.Row():
                        srt_download_tab1 = gr.File(label="📄 SRT")
                        zip_download_tab1 = gr.File(label="📦 ZIP")

        # 2️⃣ Text-to-Speech Tab
        with gr.TabItem("2️⃣ Text-to-Speech"):
            with gr.Row():
                with gr.Column():
                    input_text = gr.Textbox(label="🎙️ စာသား (Script မှ အလိုအလျောက် ရောက်လာမည်)", lines=10)
                    voice_dropdown = gr.Dropdown(choices=list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="အသံ")
                    speed_slider = gr.Slider(minimum=-30, maximum=50, value=5, label="Speed (%)")
                    gen_voice_btn = gr.Button("⚡ အသံထုတ်မည်", variant="primary")
                with gr.Column():
                    audio_output = gr.Audio(type="filepath", autoplay=True, label="🔊 အသံနားဆင်ရန်")
                    mp3_download = gr.File(label="🎵 MP3 ဒေါင်းလုဒ်")
                    with gr.Row():
                        srt_download_tab2 = gr.File(label="📄 SRT")
                        zip_download_tab2 = gr.File(label="📦 ZIP")

    # API Key Event
    save_key_btn.click(fn=save_api_key, inputs=api_key_input, outputs=key_status)

    # 1️⃣ Script Generator Click (Tab 1 မှထွက်သော clean script ကို Tab 2 ရဲ့ input_text ထဲကိုပါ auto ထည့်ပေးသည်)
    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[video_file, video_url, ratio_picker],
        outputs=[input_text, script_display, srt_download_tab1, zip_download_tab1]
    )

    # 2️⃣ TTS Generator Click
    gen_voice_btn.click(
        fn=tts_interface,
        inputs=[input_text, voice_dropdown, speed_slider],
        outputs=[audio_output, mp3_download, srt_download_tab2, zip_download_tab2]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
