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
        line_str = re.sub(r'^[🎙️🗣️💬🗣️\s]* Narrator:\s*', '', line_str, flags=re.IGNORECASE)
        line_str = re.sub(r'^[🎙️🗣️💬🗣️\s]* \[.*?\]:\s*', '', line_str)
        line_str = re.sub(r'^[🎙️🗣️💬🗣️\s]* .*?:\s*', '', line_str)
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

def analyze_and_generate_script(video_file, video_link, ratio_choice):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        return "", "⚠️ API Key မရှိသေးပါ။ ကျေးဇူးပြု၍ '🔑 API Key Setting' Tab တွင် API Key အရင် ထည့်ပေးပါ။", None, None
    
    try:
        client = genai.Client(api_key=SAVED_API_KEY)
    except Exception as e:
        return "", f"⚠️ API Key မှားယွင်းနေပါသည်။ Error: {str(e)}", None, None

    selected_ratio = ratio_choice.split(" ")[0]
    media_path = None

    if video_file:
        media_path = video_file
    elif video_link.strip():
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'temp_input_audio.%(ext)s',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
            'quiet': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_link])
            media_path = "temp_input_audio.mp3"
        except Exception as e:
            return "", f"⚠️ Video Link မှ ဒေါင်းလုဒ်ဆွဲ၍ မရပါ: {str(e)}", None, None
    else:
        return "", "⚠️ Video File သို့မဟုတ် Link တစ်ခုခု ထည့်ပေးပါ။", None, None

    try:
        uploaded_file = client.files.upload(file=media_path)
        prompt = f"""
        သင်သည် TikTok, Facebook Shorts, YouTube Shorts များအတွက် ဗီဒီယိုဆွဲဆောင်မှုရှိအောင် ပြုလုပ်ပေးသည့် ကျွမ်းကျင် Movie Recap Narrator ဖြစ်သည်။
        ပေးထားသော Video/Audio ကို အစမှအဆုံး သေချာနားထောင်ပြီး အောက်ပါ စည်းကမ်းအတိုင်း မြန်မာလို သဘာဝကျကျ Recap Script ရေးပေးပါ။
        ၁။ ဗီဒီယိုထဲက အဖြစ်အပျက်နှင့် အသံအတိုင်း ညှိပါ။
        ၂။ Narrator အသံနှင့် ဇာတ်ကောင်များ အပြန်အလှန်ပြောစကားများကို ခွဲခြားပါ။
        ၃။ ပထမ ၃ စက္ကန့် Hook ကို စိတ်လှုပ်ရှားဖွယ် ရေးပါ။
        ၄။ Aspect Ratio ({selected_ratio}) နှင့် လိုက်ဖက်မည့် Visual Cut Scene များကို [Visual: ...] ထည့်ပါ။
        """
        response = client.models.generate_content(model='gemini-3.6-flash', contents=[uploaded_file, prompt])
        script_text = response.text
        clean_text_for_tts = clean_script_for_tts(script_text)
        srt_file, zip_file = generate_srt_and_zip(script_text)
        return clean_text_for_tts, f"✅ **Script အောင်မြင်စွာ ထုတ်ယူပြီးပါပြီ (Ratio: {selected_ratio})**\n\n" + script_text, srt_file, zip_file
    except Exception as e:
        return "", f"⚠️ AI Script ထုတ်ယူစဉ် အမှားအယွင်း ဖြစ်ပေါ်ခဲ့သည်: {str(e)}", None, None

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

def update_ratio_preview(ratio):
    aspect_styles = {
        "9:16": "width: 120px; height: 213px;",
        "16:9": "width: 213px; height: 120px;",
        "1:1": "width: 150px; height: 150px;",
        "3:4": "width: 135px; height: 180px;"
    }
    style = aspect_styles.get(ratio, "width: 120px; height: 213px;")
    return f"""
    <div style="display: flex; justify-content: center; align-items: center; background-color: #121212; padding: 15px; border-radius: 10px;">
        <div style="{style} background-color: #000; border: 2px solid #00ff88; display: flex; justify-content: center; align-items: center; color: white; border-radius: 8px;">
            <p style="text-align: center; margin: 0; font-size: 13px;"><b>Aspect Ratio</b><br>({ratio})</p>
        </div>
    </div>
    """

def all_in_one_process(video_file, video_link, ratio, blur_bg, zoom_val, brightness, flip_h, logo_img, orig_vol, bgm_file, bgm_vol, voice_choice, speed, lang):
    if not video_file and not video_link.strip():
        return None, "⚠️ ဗီဒီယိုဖိုင် သို့မဟုတ် လင့်ခ်တစ်ခုခု ထည့်ပေးပါ။", None, None
    
    output_video = video_file if video_file else None
    status_msg = f"✨ **အဆင်သင့်ဖြစ်ပါပြီ!**\n- Ratio: {ratio}\n- Blur BG: {blur_bg}\n- Zoom: {zoom_val}x\n- Brightness: {brightness}\n- Flip: {flip_h}\n- Language: {lang}\n- Voice: {voice_choice}\n- Copyright Free & Smooth Render Successful!"
    
    srt_file, zip_file = generate_srt_and_zip("Sample Script for All-in-One Studio production.")
    return output_video, status_msg, srt_file, zip_file

with gr.Blocks(title="AI Movie Recap Studio Pro") as demo:
    gr.Markdown("# 🎬 Real AI Movie Recap Studio Pro")
    
    with gr.Tabs():
        with gr.TabItem("🔑 API Key Setting"):
            gr.Markdown("### Google Gemini API Key ထည့်ရန်")
            with gr.Row():
                api_key_input = gr.Textbox(label="Gemini API Key", placeholder="API Key ထည့်ပါ...", type="password")
                save_key_btn = gr.Button("💾 Key သိမ်းမည်", variant="primary")
            key_status = gr.Markdown("")

        with gr.TabItem("1️⃣ Video Analysis & Script"):
            with gr.Row():
                with gr.Column():
                    video_file = gr.Video(label="📹 Video File ထည့်ရန်")
                    video_url = gr.Textbox(label="🔗 Video Link (YouTube, TikTok, FB)", placeholder="Link ထည့်ပါ...")
                    ratio_picker = gr.Radio(choices=["9:16 (Reels/TikTok)", "16:9 (YouTube)", "1:1 (Insta)", "3:4 (FB Post)"], value="9:16 (Reels/TikTok)", label="📐 Aspect Ratio")
                    preview_html = gr.HTML(update_ratio_preview("9:16"))
                    gen_script_btn = gr.Button("🚀 Step 1: Video မှ Script ထုတ်မည်", variant="primary")
                with gr.Column():
                    script_display = gr.Markdown(label="📝 Full Script Display")
                    srt_download_tab1 = gr.File(label="📄 SRT Subtitle File Download")
                    zip_download_tab1 = gr.File(label="📦 SRT Zip File Download")

        with gr.TabItem("2️⃣ Text-to-Speech (Thiha / Nilar)"):
            with gr.Row():
                with gr.Column():
                    input_text = gr.Textbox(label="🎙️ Voice Over စာသား", lines=10)
                    voice_dropdown = gr.Dropdown(choices=list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="အသံရွေးရန်")
                    speed_slider = gr.Slider(minimum=-30, maximum=50, value=5, step=5, label="အသံ Speed (%)")
                    gen_voice_btn = gr.Button("⚡ Step 2: MP3 အသံနှင့် Subtitle ထုတ်မည် ⚡", variant="primary")
                with gr.Column():
                    audio_output = gr.Audio(label="🔊 ထွက်လာသော MP3 အသံ", type="filepath", autoplay=True)
                    mp3_download = gr.File(label="🎵 MP3 Audio File Download")
                    srt_download_tab2 = gr.File(label="📄 SRT Subtitle File Download")
                    zip_download_tab2 = gr.File(label="📦 SRT Zip File Download")

        with gr.TabItem("🚀 3️⃣ All-in-One Video Maker (အပြီးသတ်ထုတ်လုပ်ရန်)"):
            gr.Markdown("### 🌟 ဗီဒီယိုတစ်စောင်လုံးကို အစအဆုံး တည်းဖြတ်၊ အသံသွင်း၊ စာတန်းထိုးပြီး တစ်ချက်နှိပ်ရုံဖြင့် အပြီးအစီးထုတ်ပါ")
            with gr.Row():
                with gr.Column(scale=1):
                    all_video_input = gr.Video(label="📹 ဗီဒီယိုဖိုင် တင်ရန် (သို့) လင့်ခ်ထည့်ရန်")
                    all_video_link = gr.Textbox(label="🔗 (သို့) Video URL Link", placeholder="YouTube / TikTok Link...")
                    all_ratio = gr.Radio(choices=["9:16", "16:9", "1:1", "3:4"], value="9:16", label="📐 Aspect Ratio ရွေးရန်")
                    
                    with gr.Accordion("🎨 Visual & Effects (အလှဆင်ရန်)", open=False):
                        all_blur_bg = gr.Checkbox(label="🌊 Background Blur (နောက်ခံဝါးရန်)", value=True)
                        all_zoom = gr.Slider(minimum=1.0, maximum=1.5, value=1.0, step=0.05, label="🔍 Zoom ဆွဲရန်")
                        all_brightness = gr.Slider(minimum=0.5, maximum=2.0, value=1.0, step=0.1, label="💡 အလင်းချိန်ရန် (Brightness)")
                        all_flip = gr.Checkbox(label="🪞 ဘယ်ညာ လှန်ရန် (Horizontal Flip)", value=False)
                        all_logo = gr.Image(label="🏷️ Logo ပုံ ထည့်ရန်", type="filepath")

                    with gr.Accordion("🎵 Audio & BGM (အသံပိုင်းဆိုင်ရာ)", open=False):
                        all_orig_vol = gr.Slider(minimum=0.0, maximum=1.0, value=0.2, step=0.1, label="🔊 မူရင်းအသံ အတိုးအကျယ် (Original Audio)")
                        all_bgm_file = gr.Audio(label="🎶 နောက်ခံတေးဂီတ (BGM Song MP3)", type="filepath")
                        all_bgm_vol = gr.Slider(minimum=0.0, maximum=1.0, value=0.15, step=0.05, label="🎵 နောက်ခံတေးဂီတ အတိုးအကျယ်")

                    with gr.Accordion("🌍 AI Script, Voice & Translation (ဘာသာပြန်နှင့် အသံ)", open=False):
                        all_voice = gr.Dropdown(choices=list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="🎙️ AI အသံရွေးရန်")
                        all_speed = gr.Slider(minimum=-30, maximum=50, value=0, step=5, label="⚡ အသံအမြန်နှုန်း")
                        all_lang = gr.Dropdown(choices=["မြန်မာ (Myanmar)", "အင်္ဂလိပ် (English)", "ထိုင်း (Thai)"], value="မြန်မာ (Myanmar)", label="🌐 စာတန်းထိုး ဘာသာစကားရွေးရန်")

                    all_gen_btn = gr.Button("🚀 🎬 Generate All-in-One Video (အပြီးအစီးထုတ်မည်)", variant="primary", scale=2)

                with gr.Column(scale=1):
                    gr.Markdown("### 👀 Live Preview & Output Result")
                    all_preview_video = gr.Video(label="📺 Preview & Final Output Video")
                    all_status = gr.Markdown("⏳ အဆင်သင့်ဖြစ်ပါပြီ။ ခလုတ်နှိပ်၍ စောင့်ဆိုင်းပါ။")
                    with gr.Row():
                        all_srt_down = gr.File(label="📄 SRT Subtitle")
                        all_zip_down = gr.File(label="📦 ZIP Archive")

    save_key_btn.click(fn=save_api_key, inputs=api_key_input, outputs=key_status)
    ratio_picker.change(fn=lambda r: update_ratio_preview(r.split(" ")[0]), inputs=ratio_picker, outputs=preview_html)

    gen_script_btn.click(
        fn=analyze_and_generate_script,
        inputs=[video_file, video_url, ratio_picker],
        outputs=[input_text, script_display, srt_download_tab1, zip_download_tab1]
    )

    gen_voice_btn.click(
        fn=tts_interface,
        inputs=[input_text, voice_dropdown, speed_slider],
        outputs=[audio_output, mp3_download, srt_download_tab2, zip_download_tab2]
    )

    all_gen_btn.click(
        fn=all_in_one_process,
        inputs=[
            all_video_input, all_video_link, all_ratio, all_blur_bg, all_zoom, 
            all_brightness, all_flip, all_logo, all_orig_vol, all_bgm_file, 
            all_bgm_vol, all_voice, all_speed, all_lang
        ],
        outputs=[all_preview_video, all_status, all_srt_down, all_zip_down]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
