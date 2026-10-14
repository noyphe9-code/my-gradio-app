import gradio as gr
import os
import asyncio
import uuid
import edge_tts
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, TextClip, ColorClip, CompositeAudioClip, vfx

# -------------------------------------------------------------
# ၁။ SRT Parser (အချိန်နှင့် စာသားများကို ခွဲထုတ်ရန်)
# -------------------------------------------------------------
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

# -------------------------------------------------------------
# ၂။ Text-to-Speech (အသံဖန်တီးရန်)
# -------------------------------------------------------------
async def generate_tts(text, voice_name, output_audio_path):
    try:
        communicate = edge_tts.Communicate(text, voice_name)
        await communicate.save(output_audio_path)
        return output_audio_path
    except Exception as e:
        print(f"TTS Error: {str(e)}")
        raise e

# -------------------------------------------------------------
# ၃။ Main Video Processing Function
# -------------------------------------------------------------
async def process_advanced_video(
    video_file, bgm_file, srt_text, dialogue_mode, manual_voice, 
    font_color, text_position, font_size, 
    blur_height, blur_opacity, flip_video
):
    if not video_file:
        yield "⚠️ ကျေးဇူးပြု၍ ဗီဒီယိုဖိုင် တင်ပေးပါ။", None
        return
    if not srt_text.strip():
        yield "⚠️ ကျေးဇူးပြု၍ SRT (စာတန်းထိုး) ထည့်ပေးပါ။", None
        return

    session_id = str(uuid.uuid4())[:8]

    try:
        yield "🔄 အချက်အလက်များကို စစ်ဆေးနေပါသည်...", None
        
        parsed_srt = parse_srt(srt_text)
        video = VideoFileClip(video_file)
        
        # ၁။ Copyright လွတ်ကင်းရန် ဗီဒီယိုအမြန်နှုန်းကို အနည်းငယ်ချိန်ညှိခြင်း (1.02x) နှင့် မူရင်းအသံကို လုံးဝဖယ်ထုတ်ခြင်း
        video = video.fx(vfx.speedx, 1.02)
        
        # ၂။ ဗီဒီယိုကို ဘယ်/ညာ လှန်ခြင်း (Mirror / Flip) တောင်းဆိုထားပါက လုပ်ဆောင်ရန်
        if flip_video:
            video = video.fx(vfx.mirror_x)

        audio_clips_list = []
        subtitle_clips_list = []
        blur_clips_list = []
        
        yield f"🎙️ စာကြောင်းရေ {len(parsed_srt)} ကြောင်းအတွက် အသံ၊ စာတန်းနှင့် နောက်ခံဖုံးအုပ်ခြင်းများကို လုပ်ဆောင်နေပါသည်...", None
        
        voice_a = "my-MM-ThihaNeural"  # တိုင်းကျော် (ကျား)
        voice_b = "my-MM-NilarNeural"  # ရွှေစင် (မ)
        
        txt_col = 'yellow' if 'အဝါ' in font_color else 'white'

        for index, (start_sec, end_sec, text) in enumerate(parsed_srt):
            temp_audio_path = f"temp_{session_id}_{index}.mp3"
            
            # အသံရွေးချယ်မှု ပုံစံ
            if dialogue_mode == "အပြန်အလှန် စကားပြော (ကျား/မ အလှည့်ကျ)":
                current_voice = voice_a if index % 2 == 0 else voice_b
            else:
                current_voice = voice_a if "တိုင်းကျော်" in manual_voice else voice_b

            # အသံဖိုင်ထုတ်ယူခြင်း
            await generate_tts(text, current_voice, temp_audio_path)
            
            if os.path.exists(temp_audio_path):
                audio_clip = AudioFileClip(temp_audio_path)
                target_duration = end_sec - start_sec
                
                if audio_clip.duration > 0 and target_duration > 0:
                    speed_factor = audio_clip.duration / target_duration
                    synced_audio = audio_clip.fx(vfx.speedx, factor=speed_factor).set_start(start_sec)
                    audio_clips_list.append(synced_audio)
            
            # ၃။ မူရင်းစာသားဖုံးရန် (Blur / Dark Shield) Box ဖန်တီးခြင်း
            if blur_height > 0:
                # စာတန်းပေါ်မည့် နေရာတစ်ဝိုက်တွင် အရောင်မှိုင်း သို့မဟုတ် အမည်းရောင် ဖုံးအုပ်ပေးခြင်း
                blur_box = (ColorClip(size=(int(video.w * 0.95), int(blur_height)), color=(0, 0, 0))
                            .set_opacity(blur_opacity)
                            .set_start(start_sec)
                            .set_end(end_sec)
                            .set_position(('center', (text_position / 100.0)), relative=True))
                blur_clips_list.append(blur_box)

            # ၄။ စာတန်းထိုး (Subtitle Clip) ဖန်တီးခြင်း (စာလုံးအရွယ်အစား ချိန်ညှိနိုင်သည်)
            txt_clip = (TextClip(text, fontsize=font_size, color=txt_col, font='Arial', method='caption', size=(video.w * 0.9, None))
                        .set_start(start_sec)
                        .set_end(end_sec)
                        .set_position(('center', (text_position / 100.0) + 0.01), relative=True))
            subtitle_clips_list.append(txt_clip)

        yield "🎞️ ဗီဒီယို၊ အသံ၊ စာတန်းများနှင့် ဖုံးအုပ်ချက်များကို အပြည့်အစုံ ပေါင်းစပ်နေပါသည်...", None
        
        # အသံများ ပေါင်းစပ်ခြင်း
        if audio_clips_list:
            final_voice_over = CompositeAudioClip(audio_clips_list)
            if bgm_file:
                bgm = AudioFileClip(bgm_file).volumex(0.12)
                if bgm.duration > video.duration:
                    bgm = bgm.subclip(0, video.duration)
                final_audio = CompositeAudioClip([final_voice_over, bgm])
            else:
                final_audio = final_voice_over
            video = video.set_audio(final_audio)

        # ဗီဒီယို၊ Blur Box များနှင့် စာတန်းများကို ပေါင်းစပ်ခြင်း
        clips_to_composite = [video]
        if blur_clips_list:
            clips_to_composite.extend(blur_clips_list)
        if subtitle_clips_list:
            clips_to_composite.extend(subtitle_clips_list)
            
        final_video = CompositeVideoClip(clips_to_composite)
        output_video_path = f"final_pro_video_{session_id}.mp4"
        
        yield "🚀 ဗီဒီယိုကို အပြီးသတ် Export လုပ်နေပါသည်... (ခဏစောင့်ပါ)", None
        final_video.write_videofile(output_video_path, codec="libx264", audio_codec="aac", fps=24, preset="ultrafast")
        
        # Temp ဖိုင်များကို ရှင်းလင်းခြင်း
        for i in range(len(parsed_srt)):
            try:
                os.remove(f"temp_{session_id}_{i}.mp3")
            except:
                pass

        yield "✅ အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ။ အောက်တွင် ဒေါင်းလုဒ်ရယူနိုင်ပါပြီ။", output_video_path

    except Exception as e:
        yield f"❌ အမှားအယွင်းဖြစ်ပေါ်ခဲ့ပါသည်: {str(e)}", None

# -------------------------------------------------------------
# ၄။ Async Wrapper Function
# -------------------------------------------------------------
def run_async_process(*args):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    gen = process_advanced_video(*args)
    while True:
        try:
            status, video = loop.run_until_complete(gen.__anext__())
            yield status, video
        except StopAsyncIteration:
            break

# -------------------------------------------------------------
# ၅။ UI (Frontend Setup)
# -------------------------------------------------------------
with gr.Blocks(title="Pro Auto Video Dubbing Studio", theme=gr.themes.Soft()) as app:
    gr.Markdown("<center><h1>🎬 Pro Auto Video Dubbing, Subtitle & Copyright Shield Studio</h1></center>")
    gr.Markdown("<p align='center'>မူရင်းအသံဖျောက်ခြင်း၊ Thiha/Nilar အသံများဖြင့် အော်တိုဒါဘင်းလုပ်ခြင်း၊ မူရင်းစာသားဖုံးရန် Blur Box ထည့်ခြင်းနှင့် ဗီဒီယိုဘယ်/ညာလှန်ခြင်းများ အစုံအလင်ပါဝင်သည်။</p>")
    
    with gr.Row():
        dialogue_mode = gr.Radio(
            ["အပြန်အလှန် စကားပြော (ကျား/မ အလှည့်ကျ)", "အသံတစ်မျိုးတည်း သုံးမည်"], 
            label="🗣️ အသံပြောဆိုမည့် ပုံစံ", 
            value="အပြန်အလှန် စကားပြော (ကျား/မ အလှည့်ကျ)"
        )
        manual_voice = gr.Radio(
            ["တိုင်းကျော် (ကျား - Thiha)", "ရွှေစင် (မ - Nilar)"], 
            label="🎙️ အသံတစ်မျိုးတည်း ရွေးချယ်မည်ဆိုလျှင်", 
            value="တိုင်းကျော် (ကျား - Thiha)"
        )

    with gr.Row():
        flip_video = gr.Checkbox(label="🔄 ဗီဒီယိုကို ဘယ်/ညာ လှန်မည် (Mirror / Flip)", value=False)
        font_color = gr.Radio(["အဝါရောင်", "အဖြူရောင်"], label="🎨 စာတန်းအရောင်", value="အဝါရောင်")

    with gr.Row():
        font_size = gr.Slider(20, 80, 42, label="🔤 စာတန်း စာလုံးအရွယ်အစား (Font Size)")
        text_position = gr.Slider(50, 95, 82, label="📍 စာတန်းနှင့် ဖုံးအုပ်မည့် နေရာ (အပေါ်/အောက် %)")

    with gr.Row():
        blur_height = gr.Slider(0, 150, 60, label="🛡️ မူရင်းစာသားဖုံးရန် Box အထူ/အပါး အရွယ်အစား (0 ဆိုလျှင် ပိတ်မည်)")
        blur_opacity = gr.Slider(0.1, 1.0, 0.7, label="🕶️ ဖုံးအုပ်မည့် Box ၏ အရောင်မှိုင်းစပ်မှု (Opacity)")

    with gr.Row():
        video_upload = gr.Video(label="📹 မူရင်းဗီဒီယိုဖိုင် (.mp4) တင်ရန်")
        bgm_upload = gr.Audio(label="🎵 နောက်ခံတီးလုံး BGM တင်ရန် (optional)", type="filepath")
        
    srt_input = gr.Textbox(
        label="📝 SRT ဇာတ်ညွှန်းထည့်ရန် [Format: 00:00:01,000 --> 00:00:03,000]", 
        lines=6,
        placeholder="ဥပမာ -\n1\n00:00:01,000 --> 00:00:03,000\nမင်္ဂလာပါ ဒီနေ့ ဘာတွေလုပ်ကြမလဲ။\n\n2\n00:00:03,500 --> 00:00:06,000\nငါတို့ ဒီနေ့ ပရောဂျက်အသစ် စလုပ်ကြမယ်လေ။"
    )
    
    generate_btn = gr.Button("🚀 အပြည့်အစုံဖြင့် ဗီဒီယို အပြီးသတ်ထုတ်မည် (Generate)", variant="primary")
    
    status_box = gr.Textbox(label="အခြေအနေ (Status)", interactive=False, lines=2)
    video_result = gr.Video(label="📥 ထွက်လာမည့် ဗီဒီယိုရလဒ်နှင့် ဒေါင်းလုဒ်ရယူရန်")

    generate_btn.click(
        fn=run_async_process, 
        inputs=[
            video_upload, bgm_upload, srt_input, dialogue_mode, manual_voice, 
            font_color, text_position, font_size, 
            blur_height, blur_opacity, flip_video
        ], 
        outputs=[status_box, video_result]
    )

# -------------------------------------------------------------
# ၆။ Server Setup
# -------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.queue(default_concurrency_limit=2)
    app.launch(server_name="0.0.0.0", server_port=port)
