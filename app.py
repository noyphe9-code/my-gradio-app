import gradio as gr
import os
import asyncio
import uuid  # သီးသန့် ဖိုင်နာမည်များ ဖန်တီးရန်
import google.generativeai as genai
import edge_tts
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip, TextClip, CompositeAudioClip, vfx

# --- SRT Parser ---
def time_to_seconds(time_str):
    h, m, s = time_str.split(':')
    s, ms = s.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

def parse_srt(srt_text):
    blocks = srt_text.strip().split('\n\n')
    parsed_data = []
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 3:
            time_line = lines[1]
            text_lines = " ".join(lines[2:])
            if '-->' in time_line:
                start_str, end_str = time_line.split(' --> ')
                parsed_data.append((time_to_seconds(start_str.strip()), time_to_seconds(end_str.strip()), text_lines))
    return parsed_data

# --- TTS ---
async def generate_tts(text, voice_gender, output_audio_path):
    voice = "my-MM-ThihaNeural" if voice_gender == "ကျား" else "my-MM-NilarNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_audio_path)
    return output_audio_path

# --- Main Video Processing ---
async def process_video(api_key, voice_mode, voice_select, video_file, bgm_file, srt_text, logo_file,
                   font_select, color_select, aspect_ratio, preview_time, text_position,
                   font_size, blur_amount, add_subtitle, add_blur, logo_size, logo_x, logo_y):
    
    if not api_key:
        return "⚠️ Gemini API Key ထည့်ပါ။", None
    if not video_file:
        return "⚠️ ဗီဒီယိုဖိုင် တင်ပါ။", None
    if not srt_text.strip():
        return "⚠️ SRT (ဇာတ်ညွှန်း) ထည့်ပါ။", None

    # User တစ်ဦးချင်းစီအတွက် သီးသန့် ID ဖန်တီးခြင်း (ဖိုင်နာမည်မထပ်စေရန်)
    session_id = str(uuid.uuid4())[:8]

    try:
        yield "အချက်အလက်များ စစ်ဆေးနေပါသည်...", None
        
        gender = "ကျား" if "တိုင်းကျော်" in voice_select else "မ"
        parsed_srt = parse_srt(srt_text)
        
        video = VideoFileClip(video_file)
        audio_clips_list = []
        subtitle_clips_list = []
        
        yield f"စာကြောင်းရေ {len(parsed_srt)} ကြောင်းကို အသံပြောင်းလဲနေပါသည်...", None
        
        for index, (start_sec, end_sec, text) in enumerate(parsed_srt):
            # Session ID ကိုသုံးပြီး သီးသန့် ဖိုင်နာမည်ပေးခြင်း
            temp_audio_path = f"temp_{session_id}_{index}.mp3"
            await generate_tts(text, gender, temp_audio_path)
            
            audio_clip = AudioFileClip(temp_audio_path)
            target_duration = end_sec - start_sec
            
            if audio_clip.duration > 0 and target_duration > 0:
                speed_factor = audio_clip.duration / target_duration
                synced_audio = audio_clip.fx(vfx.speedx, factor=speed_factor).set_start(start_sec)
                audio_clips_list.append(synced_audio)
            
            if add_subtitle:
                txt_color = 'yellow' if 'အဝါ' in color_select else 'white'
                txt_clip = (TextClip(text, fontsize=font_size, color=txt_color, font='Arial', method='caption', size=(video.w*0.9, None))
                            .set_start(start_sec)
                            .set_end(end_sec)
                            .set_position(('center', text_position / 100.0), relative=True))
                subtitle_clips_list.append(txt_clip)

        yield "ဗီဒီယိုနှင့် အသံကို ပေါင်းစပ်နေပါသည်...", None
        
        if audio_clips_list:
            final_voice_over = CompositeAudioClip(audio_clips_list)
            if bgm_file:
                bgm = AudioFileClip(bgm_file).volumex(0.15)
                if bgm.duration > video.duration:
                    bgm = bgm.subclip(0, video.duration)
                final_audio = CompositeAudioClip([final_voice_over, bgm])
            else:
                final_audio = final_voice_over
            video = video.set_audio(final_audio)

        clips_to_composite = [video]
        if add_subtitle and subtitle_clips_list:
            clips_to_composite.extend(subtitle_clips_list)
        if logo_file:
            logo = (ImageClip(logo_file)
                    .resize(height=video.h * (logo_size / 100.0))
                    .set_duration(video.duration)
                    .set_position((logo_x / 100.0, logo_y / 100.0), relative=True))
            clips_to_composite.append(logo)
            
        final_video = CompositeVideoClip(clips_to_composite)
        
        # သီးသန့် Video output ဖိုင်နာမည်
        output_video_path = f"final_output_{session_id}.mp4"
        yield "ဗီဒီယိုကို Export လုပ်နေပါသည်... (ခဏစောင့်ပါ)", None
        
        final_video.write_videofile(output_video_path, codec="libx264", audio_codec="aac", fps=24, preset="ultrafast")
        
        # သုံးပြီးသား Temp ဖိုင်များကို ရှင်းလင်းခြင်း
        for i in range(len(parsed_srt)):
            try:
                os.remove(f"temp_{session_id}_{i}.mp3")
            except:
                pass

        yield "✅ အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ။", output_video_path

    except Exception as e:
        yield f"❌ အမှားအယွင်းဖြစ်ပေါ်ခဲ့ပါသည်: {str(e)}", None

def run_async_process(*args):
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    gen = process_video(*args)
    while True:
        try:
            status, video = loop.run_until_complete(gen.__anext__())
            yield status, video
        except StopAsyncIteration:
            break

def cancel_process():
    return "🛑 လုပ်ဆောင်မှုကို ရပ်တန့်လိုက်ပါပြီ။"

# --- UI Setup ---
with gr.Blocks(title="ALL IN ONE Dubbing App", theme=gr.themes.Soft()) as app:
    gr.Markdown("<center><h1>🎬 ALL IN ONE (Safe Version)</h1></center>")
    
    with gr.Tab("🎬 Movie Dubbing"):
        api_key = gr.Textbox(label="၁။ Gemini API Key", type="password")
        voice_mode = gr.Radio(["အသံတစ်မျိုးတည်း သုံးမည်", "ကျား/မ စုံတွဲအသံ သုံးမည်"], label="၂။ စကားပြော ပုံစံ", value="အသံတစ်မျိုးတည်း သုံးမည်")
        with gr.Row():
            voice_select = gr.Dropdown(["တိုင်းကျော် (ကျား)", "ရွှေစင် (မ)"], label="အသံ ရွေးချယ်ရန်", value="တိုင်းကျော် (ကျား)")
            
        with gr.Row():
            font_select = gr.Dropdown(["KoZ033Uni-Bold", "Pyidaungsu"], label="📝 ဖောင့်", value="KoZ033Uni-Bold")
            color_select = gr.Dropdown(["အဝါရောင် (အနက်)", "အဖြူရောင် (အနက်)"], label="🎨 အရောင်", value="အဝါရောင် (အနက်)")
            
        aspect_ratio = gr.Dropdown(["9:16 (Shorts/TikTok)", "16:9 (YouTube)"], label="📐 အရွယ်အစား", value="9:16 (Shorts/TikTok)")
        
        with gr.Row():
            preview_time = gr.Slider(0, 100, 16, label="ပုံကြည့်လိုသော အချိန် (s)")
            text_position = gr.Slider(0, 100, 80, label="စာတန်း နေရာ (%)")
        with gr.Row():
            font_size = gr.Slider(10, 100, 46, label="စာလုံး အရွယ်အစား")
            blur_amount = gr.Slider(0, 100, 55, label="နောက်ခံ Blur အရွယ်အစား")
        with gr.Row():
            add_subtitle = gr.Checkbox(label="📝 စာတန်းထိုးမည်", value=True)
            add_blur = gr.Checkbox(label="🌫 နောက်ခံ Blur မည်", value=True)
            
        video_upload = gr.Video(label="📄 ၃။ ဗီဒီယိုအတို (.mp4) တင်ပါ")
        bgm_upload = gr.Audio(label="📄 နောက်ခံတီးလုံး (BGM)", type="filepath")
        
        srt_input = gr.Textbox(label="၄။ SRT (စာတန်းထိုး) ထည့်ပါ [Format: 00:00:01,000 --> 00:00:03,000]", lines=5)
        logo_upload = gr.Image(label="📄 Logo ပုံတင်ရန်", type="filepath")
        
        with gr.Row():
            logo_size = gr.Slider(0, 100, 18, label="Logo အရွယ်အစား (%)")
            logo_x = gr.Slider(-100, 100, 0, label="Logo ဘယ်/ညာ (%)")
            logo_y = gr.Slider(-100, 100, 14, label="Logo အပေါ်/အောက် (%)")
        
        generate_btn = gr.Button("🚀 ၅။ ဖန်တီးမည်", variant="primary")
        cancel_btn = gr.Button("🛑 ရပ်တန့်မည်", variant="stop")
        
        status_box = gr.Textbox(label="အခြေအနေ (Status)", interactive=False)
        video_result = gr.Video(label="📹 Video Result")

    generate_btn.click(
        fn=run_async_process, 
        inputs=[api_key, voice_mode, voice_select, video_upload, bgm_upload, srt_input, logo_upload,
                font_select, color_select, aspect_ratio, preview_time, text_position,
                font_size, blur_amount, add_subtitle, add_blur, logo_size, logo_x, logo_y], 
        outputs=[status_box, video_result]
    )
    cancel_btn.click(fn=cancel_process, outputs=status_box)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    # Queue စနစ်ကို ဖွင့်ထားမှသာ လူများပြိုင်တူသုံးသည့်အခါ အဆင်ပြေမည်ဖြစ်သည်
    app.queue(default_concurrency_limit=2)
    app.launch(server_name="0.0.0.0", server_port=port)
