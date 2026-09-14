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

def save_api_key(api_key):
    global SAVED_API_KEY
    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
    return "⚠️ ကျေးဇူးပြု၍ မှန်ကန်သော Gemini API Key ထည့်ပါ။"

def clean_script_for_tts(script_text):
    if not script_text:
        return ""
    lines = script_text.split('\n')
    cleaned_lines = []
    for line in lines:
        line_str = line.strip()
        if re.match(r'^\[(Visual|Scene|Video|Audio).*?\]', line_str, re.IGNORECASE):
            continue
        if "recap script" in line_str.lower() or "ဒီမှာ" in line_str or line_str.startswith("---"):
            continue
        line_str = re.sub(r'^[🎙️🗣️💬📢\s]* Narrator:\s*', '', line_str, flags=re.IGNORECASE)
        line_str = re.sub(r'^[🎙️🗣️💬📢\s]* \[.*?\]:\s*', '', line_str)
        line_str = re.sub(r'^[🎙️🗣️💬📢\s]* .*?:\s*', '', line_str)
        line_str = line_str.replace("**", "").replace("#", "").replace("__", "").replace("`", "")
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
        duration = max(3, len(line) // 10)
        end_sec = start_sec + duration
        start_time = f"{start_sec//3600:02d}:{(start_sec%3600)//60:02d}:{start_sec%60:02d},000"
        end_time = f"{end_sec//3600:02d}:{(end_sec%3600)//60:02d}:{end_sec%60:02d},000"
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
    except Exception as e:
        print(f"Download Error: {e}")
        return None
    return None

# Ratio အလိုက် တိကျသည့် CSS ထုတ်ပေးသော Function
def get_ratio_css(ratio):
    style_configs = {
        "9:16": {"aspect": "9/16", "max_w": "260px"},
        "16:9": {"aspect": "16/9", "max_w": "100%"},
        "1:1":  {"aspect": "1/1",  "max_w": "300px"},
        "3:4":  {"aspect": "3/4",  "max_w": "280px"}
    }
    cfg = style_configs.get(ratio, {"aspect": "9/16", "max_w": "260px"})
    
    return f"""
    <style id="tab1-ratio-style">
    #tab1_preview_container {{
        max-width: {cfg['max_w']} !important;
        width: 100% !important;
        margin: 0 auto !important;
    }}
    #tab1_preview_container .wrap,
    #tab1_preview_container .video-container,
    #tab1_preview_container div[data-testid="video"] {{
        aspect-ratio: {cfg['aspect']} !important;
        width: 100% !important;
        height: auto !important;
        background: #000 !important;
        border-radius: 8px !important;
        overflow: hidden !important;
    }}
    #tab1_preview_container video {{
        aspect-ratio: {cfg['aspect']} !important;
        width: 100% !important;
        height: 100% !important;
        object-fit: contain !important;
    }}
    </style>
    """

def load_tab1_link(link):
    if not link or not link.strip():
        return None
    return download_video_from_link(link)

def analyze_and_generate_script(video_file, video_link, ratio_choice):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        return "", "⚠️ API Key မရှိသေးပါ။ ကျေးဇူးပြု၍ '🔑 API Key Setting' တွင် API Key အရင်ထည့်ပါ။", None, None
    
    target_media = video_file if video_file else download_video_from_link(video_link)
    if not target_media or not os.path.exists(target_media):
        return "", "⚠️ ဗီဒီယိုဖိုင် တင်ပါ သို့မဟုတ် Link မှန်ကန်စွာ ထည့်ပေးပါ။", None, None

    try:
        client = genai.Client(api_key=SAVED_API_KEY)
    except Exception as e:
        return "", f"⚠️ Client ချိတ်ဆက်မှု Error: {str(e)}", None, None

    selected_ratio = ratio_choice.split(" ")[0]

    try:
        uploaded_file = client.files.upload(file=target_media)
        
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(3)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        if uploaded_file.state.name == "FAILED":
            return "", "⚠️ Gemini ဘက်မှ ဗီဒီယိုဖတ်ရှုရာတွင် မအောင်မြင်ပါ။", None, None

        prompt = (
            f"သင်သည် မြန်မာ Movie Recapper တစ်ဦးဖြစ်သည်။ ပေးထားသော ဗီဒီယိုဇာတ်လမ်းကို သေချာကြည့်ရှုပြီး "
            f"Aspect Ratio ({selected_ratio}) အတွက် ဆွဲဆောင်မှုရှိသော မြန်မာလို Movie Recap Script တစ်ခု ရေးပေးပါ။ "
            f"ရုပ်သံညွှန်းချက်များ (Visual cues) မပါဘဲ အသံထွက်ဖတ်ရုံသက်သက် ရှင်းလင်းသော မြန်မာစာသားဖြင့်သာ ရေးပေးပါ။"
        )

        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[uploaded_file, prompt]
        )
        
        script_text = response.text
        clean_text_for_tts = clean_script_for_tts(script_text)
        srt_file, zip_file = generate_srt_and_zip(script_text)
        
        return clean_text_for_tts, script_text, srt_file, zip_file

    except Exception as e:
        return "", f"⚠️ Error ဖြစ်ပွားပါသည်: {str(e)}", None, None

async def generate_myanmar_tts(text, voice_choice, speed_percent):
    if not text or not text.strip():
        return None, None, None, None
    clean_text = clean_script_for_tts(text)
    selected_voice = VOICES.get(voice_choice, "my-MM-ThihaNeural")
    rate_str = f"{speed_percent:+d}%"
    output_filename = "recap_voice_over.mp3"
    
    communicate = edge_tts.Communicate(clean_text, selected_voice, rate=rate_str)
    await communicate.save(output_filename)
    srt_file, zip_file = generate_srt_and_zip(clean_text)
    return output_filename, output_filename, srt_file, zip_file

def tts_interface(text, voice_choice, speed):
    return asyncio.run(generate_myanmar_tts(text, voice_choice, speed))

# ================= Gradio UI =================
with gr.Blocks(title="AI Movie Recap Studio Pro", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎬 AI Movie Recap Studio Pro")
    
    with gr.Tabs():
        # 🔑 API Key Setting
        with gr.TabItem("🔑 API Key Setting"):
            api_key_input = gr.Textbox(label="Gemini API Key", type="password", placeholder="AIzaSy...")
            save_key_btn = gr.Button("💾 Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")

        # 1️⃣ Video Analysis & Script (Ratio Preview အပြည့်အစုံပါဝင်သည်)
        with gr.TabItem("1️⃣ Video Analysis & Script"):
            with gr.Row():
                # ဘယ်ဘက်ခြမ်း - Inputs & Ratio Controls
                with gr.Column(scale=1):
                    video_file = gr.Video(label="📹 Video File တင်ရန်")
                    video_url = gr.Textbox(label="🔗 Video URL Link (YouTube/TikTok/FB)", placeholder="https://...")
                    load_link_btn = gr.Button("🔍 Link မှ ဗီဒီယိုရယူမည်", variant="secondary")
                    
                    # 1:1, 3:4, 16:9, 9:16 Aspect Ratio Selector
                    ratio_picker = gr.Radio(
                        choices=["9:16", "16:9", "1:1", "3:4"], 
                        value="9:16", 
                        label="📐 Aspect Ratio ရွေးချယ်ပါ (Preview ချက်ချင်းပြောင်းမည်)"
                    )
                    gen_script_btn = gr.Button("🚀 Script ထုတ်မည်", variant="primary")

                # ညာဘက်ခြမ်း - Dynamic Ratio Video Preview & Outputs
                with gr.Column(scale=1):
                    # Aspect Ratio ပုံစံကို တိုက်ရိုက်ထိန်းချုပ်သော CSS Tag
                    ratio_css_injection = gr.HTML(get_ratio_css("9:16"))
                    
                    # တိကျသော Ratio ဖြင့် ပြသပေးမည့် Preview Player
                    tab1_preview = gr.Video(
                        label="📺 Video Preview (Aspect Ratio အလိုက် ပြသခြင်း)",
                        elem_id="tab1_preview_container"
                    )
                    
                    script_display = gr.Markdown(label="ထွက်ရှိလာသော Script")
                    with gr.Row():
                        srt_download_tab1 = gr.File(label="📄 SRT (Subtitle)")
                        zip_download_tab1 = gr.File(label="📦 ZIP ဖိုင်")

        # 2️⃣ Text-to-Speech
        with gr.TabItem("2️⃣ Text-to-Speech"):
            with gr.Row():
                with gr.Column(scale=1):
                    input_text = gr.Textbox(
                        label="🎙️ စာသား (Tab 1 မှ Script အလိုအလျောက် ရောက်ရှိပါမည်)", 
                        lines=12,
                        placeholder="စာသားများ ရိုက်ထည့်နိုင်ပါသည်..."
                    )
                    voice_dropdown = gr.Dropdown(
                        choices=list(VOICES.keys()), 
                        value="Thiha (အမျိုးသားအသံ) - Natural", 
                        label="ရွေးချယ်မည့် အသံ"
                    )
                    speed_slider = gr.Slider(minimum=-30, maximum=50, value=5, label="အသံအမြန်နှုန်း Speed (%)")
                    gen_voice_btn = gr.Button("⚡ အသံထုတ်မည်", variant="primary")
                with gr.Column(scale=1):
                    audio_output = gr.Audio(type="filepath", autoplay=True, label="🔊 အသံနားဆင်ရန်")
                    mp3_download = gr.File(label="🎵 MP3 ဒေါင်းလုဒ်")
                    with gr.Row():
                        srt_download_tab2 = gr.File(label="📄 SRT (Subtitle)")
                        zip_download_tab2 = gr.File(label="📦 ZIP ဖိုင်")

    # API Key Event
    save_key_btn.click(fn=save_api_key, inputs=api_key_input, outputs=key_status)

    # Ratio ရွေးလိုက်တာနဲ့ Video Preview အချိုးချက်ချင်းပြောင်းစေခြင်း
    ratio_picker.change(fn=get_ratio_css, inputs=ratio_picker, outputs=ratio_css_injection)

    # Video ဖိုင်တင်လျှင် Preview ထဲ အလိုအလျောက် ရောက်ရှိစေခြင်း
    video_file.change(fn=lambda v: v, inputs=video_file, outputs=tab1_preview)

    # Link ထည့်ပြီး ခလုတ်နှိပ်လျှင် Preview ပြပေးခြင်း
    load_link_btn.click(fn=load_tab1_link, inputs=video_url, outputs=tab1_preview)

    # Script ထုတ်လုပ်ခြင်း
    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[video_file, video_url, ratio_picker],
        outputs=[input_text, script_display, srt_download_tab1, zip_download_tab1]
    )

    # TTS ထုတ်လုပ်ခြင်း
    gen_voice_btn.click(
        fn=tts_interface,
        inputs=[input_text, voice_dropdown, speed_slider],
        outputs=[audio_output, mp3_download, srt_download_tab2, zip_download_tab2]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
