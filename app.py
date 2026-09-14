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
        return "✅ API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။ Script စတင် ထုတ်ယူနိုင်ပါပြီ။"
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
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
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
        
        ပေးထားသော Video/Audio ကို အစမှအဆုံး သေချာနားထောင်/ကြည့်ရှုပြီး အောက်ပါ စည်းကမ်းအတိုင်း မြန်မာလို သဘာဝကျကျ Recap Script ရေးပေးပါ။
        
        အရေးကြီးသတိပေးချက်- မိတ်ဆက်နိဒါန်း စာသားများ သို့မဟုတ် အပြိုင်မျဉ်း (---) များကို လုံးဝ မရေးပါနှင့်။ ချက်ချင်း Script ကိုသာ စတင်ရေးပါ။
        
        ၁။ ဗီဒီယိုထဲက အဖြစ်အပျက်နှင့် အသံအတိုင်း တိတိကျကျ ကောက်နှုတ်ပါ။ စိတ်ကူးယဉ် အပိုမရေးပါနှင့်။
        ၂။ Narrator ရဲ့ အသံနှင့် Video ထဲမှ ဇာတ်ကောင်များ အပြန်အလှန် ပြောစကားများကို ပြတ်ပြတ်သားသား ခွဲခြားပေးပါ။
        ၃။ TikTok/FB Reels များတွင် ကြည့်သူတန်းရပ်သွားအောင် ပထမ ၃ စက္ကန့် Hook ကို စိတ်လှုပ်ရှားဖွယ် ရေးပါ။
        ၄။ Screen Aspect Ratio ({selected_ratio}) နှင့် လိုက်ဖက်မည့် Visual Cut Scene ညွှန်ကြားချက်များကို [Visual: ...] ဆိုပြီး အခန်းတိုင်းမှာ ထည့်ပေးပါ။
        
        Output Formating ပုံစံ-
        [Visual: ...]
        🎙️ Narrator: ...
        🗣️ [ဇာတ်ကောင်အမည်/အမျိုးအစား]: ...
        """

        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[uploaded_file, prompt]
        )
        
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
                    ratio_picker = gr.Radio(
                        choices=["9:16 (Reels/TikTok)", "16:9 (YouTube)", "1:1 (Insta)", "3:4 (FB Post)"], 
                        value="9:16 (Reels/TikTok)", 
                        label="📐 Aspect Ratio"
                    )
                    preview_html = gr.HTML(update_ratio_preview("9:16"))
                    gen_script_btn = gr.Button("🚀 Step 1: Video မှ Script ထုတ်မည်", variant="primary")

                with gr.Column():
                    script_display = gr.Markdown(label="📝 Full Script Display")
                    srt_download_tab1 = gr.File(label="📄 SRT Subtitle File Download")
                    zip_download_tab1 = gr.File(label="📦 SRT Zip File Download (ဖုန်းအတွက် အကြံပြုပါသည်)")

        with gr.TabItem("2️⃣ Text-to-Speech (Thiha / Nilar)"):
            with gr.Row():
                with gr.Column():
                    input_text = gr.Textbox(label="🎙️ Voice Over စာသား (ပြောစကား သီးသန့်)", lines=10)
                    voice_dropdown = gr.Dropdown(choices=list(VOICES.keys()), value="Thiha (အမျိုးသားအသံ) - Natural", label="အသံရွေးရန်")
                    speed_slider = gr.Slider(minimum=-30, maximum=50, value=5, step=5, label="အသံ Speed (%)")
                    gen_voice_btn = gr.Button("⚡ Step 2: MP3 အသံနှင့် Subtitle ထုတ်မည် ⚡", variant="primary")
                    
                with gr.Column():
                    audio_output = gr.Audio(label="🔊 ထွက်လာသော MP3 အသံ (Auto-Play)", type="filepath", autoplay=True)
                    mp3_download = gr.File(label="🎵 MP3 Audio File Download")
                    srt_download_tab2 = gr.File(label="📄 SRT Subtitle File Download")
                    zip_download_tab2 = gr.File(label="📦 SRT Zip File Download (ဖုန်းအတွက် အကြံပြုပါသည်)")

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

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
