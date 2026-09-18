import os
import sys
import time
import asyncio
import subprocess
import edge_tts
import gradio as gr
from google import genai

# System default encoding ကို UTF-8 သို့ အတင်းအကြပ်ပြောင်းရန် (Render အတွက် အရေးကြီးသည်)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# =========================================================
# GLOBAL CONFIG & UTILS
# =========================================================
APP_TITLE = "AI Movie Recap Studio Pro (Tab 1 & 2)"
VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural"
}

def save_api_key(key):
    if key and key.strip():
        os.environ["GEMINI_API_KEY"] = key.strip()
        return "✅ API Key အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ!"
    return "❌ ကျေးဇူးပြု၍ မှန်ကန်သော API Key ထည့်ပါ။"

def download_video_from_link(url):
    if not url:
        return None
    output_filename = "downloaded_video.mp4"
    try:
        cmd = ["yt-dlp", "-f", "mp4/best", "-o", output_filename, url.strip()]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(output_filename):
            return output_filename
    except Exception as e:
        print(f"Video Download Error: {e}")
    return None

def get_ratio_css(ratio, container_id):
    ratio_map = {
        "1:1": "aspect-ratio: 1/1; max-height: 400px;",
        "3:4": "aspect-ratio: 3/4; max-height: 450px;",
        "16:9": "aspect-ratio: 16/9; max-height: 350px;",
        "9:16": "aspect-ratio: 9/16; max-height: 500px;"
    }
    style = ratio_map.get(ratio, "aspect-ratio: 1/1; max-height: 400px;")
    return f"<style>#{container_id} {{ {style} margin: auto; object-fit: contain; }}</style>"

def tab1_analyze(v_file, v_url, ratio):
    video_path = v_file if v_file else download_video_from_link(v_url)
    if not video_path or not os.path.exists(video_path):
        return "❌ Error: ဗီဒီယိုဖိုင် သို့မဟုတ် Link မှန်ကန်မှုမရှိပါ။", "", "❌ ဗီဒီယိုရယူ၍မရပါ။", None, None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "❌ Error: ကျေးဇူးပြု၍ 'API Key Setting' Tab တွင် Gemini API Key အရင်ထည့်ပါ။", "", "❌ API Key လိုအပ်ပါသည်။", None, None

    try:
        client = genai.Client(api_key=api_key)
        print("Uploading video to Gemini with MIME type...")
        
        # MIME type အတိအကျ သတ်မှတ်ပေးခြင်းဖြင့် Error ကို ဖြေရှင်းသည်
        with open(video_path, "rb") as f:
            video_file = client.files.upload(file=f, config={"mime_type": "video/mp4"})
        
        # Upload ပြီးဆုံးသည်အထိ စောင့်ဆိုင်းခြင်း
        while video_file.state.name == "PROCESSING":
            time.sleep(3)
            video_file = client.files.get(name=video_file.name)

        prompt = (
            "ဤဗီဒီယိုကို ကြည့်ရှုပြီး ဆွဲဆောင်မှုရှိသော မြန်မာဘာသာဖြင့် ရုပ်ရှင်/ဗီဒီယို ဇာတ်ကြောင်းပြန် (Movie Recap Script) ကို "
            "အသေးစိတ် ရေးသားပေးပါ။ အသံထွက်ဖတ်ရန်အတွက် အလွန်ကောင်းမွန်သော စာသားပုံစံဖြစ်စေရမည်။"
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[video_file, prompt]
        )
        script_text = response.text

        # SRT စာတန်းထိုးဖိုင်ကို UTF-8 ဖြင့် သိမ်းဆည်းခြင်း
        srt_path = "subtitles.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:01,000 --> 00:00:10,000\n" + script_text[:100].replace('\n', ' ') + "\n")

        return script_text, script_text, "✅ Video Analysis ပြီးမြောက်ပြီး Script ထွက်ရှိလာပါပြီ။", srt_path, None
    except Exception as e:
        err_msg = f"❌ Gemini API Error: {str(e)}"
        return err_msg, "", err_msg, None, None

async def generate_myanmar_tts(text, voice_name, rate_percent, output_file):
    rate_str = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"
    communicate = edge_tts.Communicate(text, VOICES.get(voice_name, "my-MM-ThihaNeural"), rate=rate_str)
    await communicate.save(output_file)

def tab2_tts(text, voice_key, speed):
    if not text or not text.strip():
        return None, None, None, None
    
    audio_path = "output_voice.mp3"
    try:
        asyncio.run(generate_myanmar_tts(text, voice_key, speed, audio_path))
        
        srt_path = "voice_subtitles.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:15,000\n" + text[:60].replace('\n', ' ') + "\n")
            
        return audio_path, audio_path, srt_path, None
    except Exception as e:
        print(f"TTS Error: {e}")
        return None, None, None, None

# =========================================================
# GRADIO UI & APP LAUNCH
# =========================================================
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**AI Video Recap Script & Myanmar Voice-Over Studio**")

    with gr.Tabs() as main_tabs:
        # --- TAB 0: API KEY ---
        with gr.TabItem("🔑 API Key Setting", id="tab_key"):
            gr.Markdown("### 🔐 Gemini API Key ထည့်သွင်းပါ")
            api_key_input = gr.Textbox(label="Gemini API Key", type="password", placeholder="AIzaSy...")
            save_key_btn = gr.Button("💾 API Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")
            save_key_btn.click(save_api_key, inputs=api_key_input, outputs=key_status)

        # --- TAB 1: SCRIPT ---
        with gr.TabItem("1️⃣ Video Analysis & Script", id="tab_script"):
            with gr.Row():
                with gr.Column(scale=1):
                    v1_file = gr.Video(label="📹 Video File တင်ရန်")
                    v1_url = gr.Textbox(label="🔗 Video URL Link (YouTube, TikTok, Facebook စသည်)")
                    v1_load_btn = gr.Button("🔍 Link မှ Video ရယူမည်", variant="secondary")
                    
                    v1_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="16:9", label="📐 Preview Screen Aspect Ratio")
                    v1_gen_btn = gr.Button("🚀 Recap Script စတင်ထုတ်မည်", variant="primary")
                
                with gr.Column(scale=1):
                    v1_css = gr.HTML(get_ratio_css("16:9", "tab1_preview_container"))
                    v1_preview = gr.Video(label="📺 Video Preview", elem_id="tab1_preview_container")
                    v1_status = gr.Markdown("ဗီဒီယိုထည့်သွင်းရန် အဆင်သင့်ဖြစ်ပါသည်။")
                    v1_script_out = gr.Textbox(label="🎬 ထွက်ရှိလာသော Script", lines=10)
                    
                    go_to_tts_btn = gr.Button("🎙️ Tab 2 (TTS) သို့ သွားရောက် အသံထုတ်မည် ➡️", variant="secondary")

            with gr.Row():
                v1_srt = gr.File(label="📄 SRT စာတန်းထိုး ဖိုင်")
                v1_zip = gr.File(label="📦 SRT ZIP ဒေါင်းလုဒ်")

        # --- TAB 2: TTS ---
        with gr.TabItem("2️⃣ Text-to-Speech", id="tab_tts"):
            with gr.Row():
                with gr.Column(scale=1):
                    v2_input_text = gr.Textbox(label="🎙️ Burmese Script (Tab 1 မှ အလိုအလျောက် ရောက်ရှိပါမည်)", lines=12)
                    v2_voice = gr.Dropdown(list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎤 အသံ ရွေးချယ်ပါ")
                    v2_speed = gr.Slider(-30, 50, value=0, step=1, label="⚡ Speed (%)")
                    v2_btn = gr.Button("⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်", variant="primary")
                with gr.Column(scale=1):
                    v2_audio = gr.Audio(label="🔊 Voice Preview (အသံစမ်းနားထောင်ရန်)")
                    v2_mp3 = gr.File(label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်")
            with gr.Row():
                v2_srt = gr.File(label="📄 SRT စာတန်းထိုး ဖိုင်")
                v2_zip = gr.File(label="📦 SRT ZIP ဖိုင်")

            v2_btn.click(tab2_tts, inputs=[v2_input_text, v2_voice, v2_speed], outputs=[v2_audio, v2_mp3, v2_srt, v2_zip])

    # ================= EVENT BINDINGS =================
    v1_file.change(lambda f: f, inputs=v1_file, outputs=v1_preview)
    v1_load_btn.click(download_video_from_link, inputs=v1_url, outputs=v1_preview)
    v1_ratio.change(lambda r: get_ratio_css(r, "tab1_preview_container"), inputs=v1_ratio, outputs=v1_css)
    
    v1_gen_btn.click(
        tab1_analyze, 
        inputs=[v1_file, v1_url, v1_ratio], 
        outputs=[v1_script_out, v2_input_text, v1_status, v1_srt, v1_zip]
    )

    go_to_tts_btn.click(lambda: gr.Tabs(selected="tab_tts"), outputs=main_tabs)

# Server Launch for Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
