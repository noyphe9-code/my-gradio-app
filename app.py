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
# ၂။ Text-to-Speech (အသံဖန်တီးရန် Core Function)
# -------------------------------------------------------------
async def generate_tts(text, voice_name, output_audio_path, volume_gain=1.0):
    try:
        communicate = edge_tts.Communicate(text, voice_name)
        await communicate.save(output_audio_path)
        
        # အသံအကျယ်အဝန်း (Volume) ချိန်ညှိခြင်း
        if volume_gain != 1.0 and os.path.exists(output_audio_path):
            audio_clip = AudioFileClip(output_audio_path).volumex(volume_gain)
            audio_clip.write_audiofile(output_audio_path, logger=None)
            audio_clip.close()
            
        return output_audio_path
    except Exception as e:
        print(f"TTS Error: {str(e)}")
        raise e

# -------------------------------------------------------------
# ၃။ Tab 2 အတွက် သီးသန့် TTS Generator Function
# -------------------------------------------------------------
async def generate_standalone_tts(text, voice_choice, volume_level):
    if not text.strip():
        yield "⚠️ ကျေးဇူးပြု၍ ထုတ်ယူလိုသော စာသားများကို ထည့်ပေးပါ။", None
        return

    session_id = str(uuid.uuid4())[:8]
    output_audio_path = f"standalone_audio_{session_id}.mp3"
    
    # အသံရွေးချယ်မှု
    voice_name = "my-MM-ThihaNeural" if "တိုင်းကျော်" in voice_choice else "my-MM-NilarNeural"
    
    try:
        yield "🎙️ အသံဖိုင်ကို စတင်ဖန်တီးနေပါသည်... (ခဏစောင့်ပါ)", None
        
        await generate_tts(text, voice_name, output_audio_path, volume_gain=volume_level)
        
        yield "✅ အသံဖိုင် အောင်မြင်စွာ ထွက်ရှိပါပြီ။ အောက်တွင် နားဆင်ပြီး ဒေါင်းလုဒ်ရယူနိုင်ပါပြီ။", output_audio_path
    except Exception as e:
        yield f"❌ အမှားအယွင်းဖြစ်ပေါ်ခဲ့ပါသည်: {str(e)}", None

def run_async_standalone_tts(text, voice_choice, volume_level):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    gen = generate_standalone_tts(text, voice_choice, volume_level)
    while True:
        try:
            status, audio = loop.run_until_complete(gen.__anext__())
            yield status, audio
        except StopAsyncIteration:
            break

# -------------------------------------------------------------
# ၄။ Tab 1 အတွက် Video Preview Function
# -------------------------------------------------------------
def generate_preview(video_file, srt_text, font_size, text_position, blur_height, flip_video):
    if not video_file:
        return "⚠️ ကျေးဇူးပြု၍ ဗီဒီယိုဖိုင် အရင်တင်ပါ။", None
    
    session_id = str(uuid.uuid4())[:8]
    preview_output_path = f"preview_sample_{session_id}.mp4"
    
    try:
        video = VideoFileClip(video_file)
        sub_video = video.subclip(0, min(3, video.duration))
        sub_video = sub_video.fx(vfx.speedx, 1.02)
        
        if flip_video:
            sub_video = sub_video.fx(vfx.mirror_x)
            
        clips = [sub_video]
        
        parsed = parse_srt(srt_text) if srt_text.strip() else []
        sample_text = parsed[0][2] if parsed else "နမူနာ စာတန်းထိုး စာသားဖြစ်ပါသည်။"
        
        if blur_height > 0:
            blur_box = ColorClip(size=(int(sub_video.w * 0.95), int(blur_height)), color=(0, 0, 0))\
                       .set_opacity(0.6)\
                       .set_duration(sub_video.duration)\
                       .set_position(('center', (text_position / 100.0)), relative=True)
            clips.append(blur_box)
            
        txt_clip = TextClip(sample_text, fontsize=font_size, color='yellow', font='Arial', method='caption', size=(sub_video.w * 0.9, None))\
                   .set_duration(sub_video.duration)\
                   .set_position(('center', (text_position / 100.0) + 0.01), relative=True)
        clips.append(txt_clip)
        
        preview_clip = CompositeVideoClip(clips)
        preview_clip.write_videofile(preview_output_path, codec="libx264", audio_codec="aac", fps=24, preset="ultrafast")
        
        return "✅ နမူနာ (Preview) ထွက်ရှိပါပြီ။ ဗီဒီယို ရွေ့လျားမှုနှုန်း: 102%", preview_output_path
    except Exception as e:
        return f"❌ နမူနာထုတ်ရာတွင် အမှားဖြစ်သည်: {str(e)}", None

# -------------------------------------------------------------
# ၅။ Tab 1 အတွက် Main Video Processing Function
# -------------------------------------------------------------
async def process_advanced_video(
    api_key, video_file, bgm_file, srt_text, dialogue_mode, manual_voice, 
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
        yield "🔄 [5%] အချက်အလက်များနှင့် ဗီဒီယိုကို စစ်ဆေးနေပါသည်...", None
        
        parsed_srt = parse_srt(srt_text)
        video = VideoFileClip(video_file)
        
        video = video.fx(vfx.speedx, 1.02)
        if flip_video:
            video = video.fx(vfx.mirror_x)

        audio_clips_list = []
        subtitle_clips_list = []
        blur_clips_list = []
        
        total_lines = len(parsed_srt)
        yield f"🎙️ [15%] စာကြောင်းရေ {total_lines} ကြောင်းအတွက် အသံများကို စတင်ဖန်တီးနေပါသည်...", None
        
        voice_a = "my-MM-ThihaNeural"
        voice_b = "my-MM-NilarNeural"
        txt_col = 'yellow' if 'အဝါ' in font_color else 'white'

        for index, (start_sec, end_sec, text) in enumerate(parsed_srt):
            temp_audio_path = f"temp_{session_id}_{index}.mp3"
            
            if dialogue_mode == "အပြန်အလှန် စကားပြော (ကျား/မ အလှည့်ကျ)":
                current_voice = voice_a if index % 2 == 0 else voice_b
            else:
                current_voice = voice_a if "တိုင်းကျော်" in manual_voice else voice_b

            await generate_tts(text, current_voice, temp_audio_path)
            
            if os.path.exists(temp_audio_path):
                audio_clip = AudioFileClip(temp_audio_path)
                target_duration = end_sec - start_sec
                
                if audio_clip.duration > 0 and target_duration > 0:
                    speed_factor = audio_clip.duration / target_duration
                    synced_audio = audio_clip.fx(vfx.speedx, factor=speed_factor).set_start(start_sec)
                    audio_clips_list.append(synced_audio)
            
            if blur_height > 0:
                blur_box = ColorClip(size=(int(video.w * 0.95), int(blur_height)), color=(0, 0, 0))\
                           .set_opacity(blur_opacity)\
                           .set_start(start_sec)\
                           .set_end(end_sec)\
                           .set_position(('center', (text_position / 100.0)), relative=True)
                blur_clips_list.append(blur_box)

            txt_clip = TextClip(text, fontsize=font_size, color=txt_col, font='Arial', method='caption', size=(video.w * 0.9, None))\
                       .set_start(start_sec)\
                       .set_end(end_sec)\
                       .set_position(('center', (text_position / 100.0) + 0.01), relative=True)
            subtitle_clips_list.append(txt_clip)

            current_progress = 15 + int((index + 1) / total_lines * 50)
            yield f"⏳ [ တိုးတက်မှု {current_progress}% ] စာကြောင်း {index+1}/{total_lines} ကို လုပ်ဆောင်နေပါသည်...", None

        yield "🎞️ [70%] ဗီဒီယို၊ အသံနှင့် စာတန်းများကို အပြည့်အစုံ ပေါင်းစပ်နေပါသည်...", None
        
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

        clips_to_composite = [video]
        if blur_clips_list:
            clips_to_composite.extend(blur_clips_list)
        if subtitle_clips_list:
            clips_to_composite.extend(subtitle_clips_list)
            
        final_video = CompositeVideoClip(clips_to_composite)
        output_video_path = f"final_pro_video_{session_id}.mp4"
        
        yield "🚀 [90%] ဗီဒီယိုကို အပြီးသတ် Export လုပ်နေပါသည်... (ခဏစောင့်ပါ)", None
        final_video.write_videofile(output_video_path, codec="libx264", audio_codec="aac", fps=24, preset="ultrafast")
        
        for i in range(len(parsed_srt)):
            try:
                os.remove(f"temp_{session_id}_{i}.mp3")
            except:
                pass

        yield "✅ [100%] အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ။ ဗီဒီယို ရွေ့လျားမှုနှုန်း: 102% (Copyright Free)", output_video_path

    except Exception as e:
        yield f"❌ အမှားအယွင်းဖြစ်ပေါ်ခဲ့ပါသည်: {str(e)}", None

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
# ၆။ UI (Frontend Setup with Tab 1 & Tab 2)
# -------------------------------------------------------------
with gr.Blocks(title="Pro Auto Video & TTS Studio", theme=gr.themes.Soft()) as app:
    gr.Markdown("<center><h1>🎬 Pro Auto Video Dubbing & TTS Studio</h1></center>")
    
    with gr.Tabs():
        # --- TAB 1: Auto Video Dubbing ---
        with gr.TabItem("🎬 Auto Video Dubbing"):
            api_key = gr.Textbox(label="🔑 Gemini API Key ထည့်ရန် (Optional)", type="password", placeholder="AI API Key ထည့်ပါ...")
            
            with gr.Row():
                dialogue_mode = gr.Radio(["အပြန်အလှန် စကားပြော (ကျား/မ အလှည့်ကျ)", "အသံတစ်မျိုးတည်း သုံးမည်"], label="🗣️ အသံပြောဆိုမည့် ပုံစံ", value="အပြန်အလှန် စကားပြော (ကျား/မ အလှည့်ကျ)")
                manual_voice = gr.Radio(["တိုင်းကျော် (ကျား - Thiha)", "ရွှေစင် (မ - Nilar)"], label="🎙️ အသံတစ်မျိုးတည်း ရွေးချယ်မည်ဆိုလျှင်", value="တိုင်းကျော် (ကျား - Thiha)")

            with gr.Row():
                flip_video = gr.Checkbox(label="🔄 ဗီဒီယိုကို ဘယ်/ညာ လှန်မည် (Mirror / Flip - 102% Speed)", value=False)
                font_color = gr.Radio(["အဝါရောင်", "အဖြူရောင်"], label="🎨 စာတန်းအရောင်", value="အဝါရောင်")

            with gr.Row():
                font_size = gr.Slider(20, 80, 42, label="🔤 စာတန်း စာလုံးအရွယ်အစား (Font Size)")
                text_position = gr.Slider(50, 95, 82, label="📍 စာတန်းနှင့် ဖုံးအုပ်မည့် နေရာ (အပေါ်/အောက် %)")

            with gr.Row():
                blur_height = gr.Slider(0, 150, 60, label="🛡️ မူရင်းစာသားဖုံးရန် Box အထူ/အပါး အရွယ်အစား")
                blur_opacity = gr.Slider(0.1, 1.0, 0.7, label="🕶️ ဖုံးအုပ်မည့် Box ၏ အရောင်မှိုင်းစပ်မှု (Opacity)")

            with gr.Row():
                video_upload = gr.Video(label="📹 မူရင်းဗီဒီယိုဖိုင် (.mp4) တင်ရန်")
                bgm_upload = gr.Audio(label="🎵 နောက်ခံတီးလုံး BGM တင်ရန် (optional)", type="filepath")
                
            srt_input = gr.Textbox(
                label="📝 SRT ဇာတ်ညွှန်းထည့်ရန် [Format: 00:00:01,000 --> 00:00:03,000]", 
                lines=5,
                placeholder="ဥပမာ -\n1\n00:00:01,000 --> 00:00:03,000\nမင်္ဂလာပါ ဒီနေ့ ဘာတွေလုပ်ကြမလဲ။"
            )
            
            with gr.Row():
                preview_btn = gr.Button("👀 နမူနာ (Preview) ကြည့်မည်", variant="secondary")
                generate_btn = gr.Button("🚀 ဗီဒီယို အပြီးသတ်ထုတ်မည် (Generate)", variant="primary")
            
            status_box_v = gr.Textbox(label="အခြေအနေနှင့် တိုးတက်မှု (%) Status", interactive=False, lines=2)
            
            with gr.Row():
                preview_result = gr.Video(label="📺 စာတန်းထိုး နမူနာ (Preview Video)")
                video_result = gr.Video(label="📥 အပြီးသတ် ထွက်လာမည့် ဗီဒီယိုရလဒ်")

            preview_btn.click(
                fn=generate_preview,
                inputs=[video_upload, srt_input, font_size, text_position, blur_height, flip_video],
                outputs=[status_box_v, preview_result]
            )

            generate_btn.click(
                fn=run_async_process, 
                inputs=[
                    api_key, video_upload, bgm_upload, srt_input, dialogue_mode, manual_voice, 
                    font_color, text_position, font_size, 
                    blur_height, blur_opacity, flip_video
                ], 
                outputs=[status_box_v, video_result]
            )

        # --- TAB 2: Standalone Text-to-Speech (TTS Only) ---
        with gr.TabItem("🎙️ Text-to-Speech (အသံဖိုင်သက်သက်ထုတ်ရန်)"):
            gr.Markdown("### စာသားရိုက်ထည့်ရုံဖြင့် Thiha (ကျား) / Nilar (မ) အသံဖိုင်များကို ထုတ်ယူပြီး ဒေါင်းလုဒ်ရယူနိုင်ပါသည်။")
            
            with gr.Row():
                tts_voice_select = gr.Radio(
                    ["တိုင်းကျော် (ကျား - Thiha)", "ရွှေစင် (မ - Nilar)"], 
                    label="🗣️ အသံ ရွေးချယ်ရန်", 
                    value="တိုင်းကျော် (ကျား - Thiha)"
                )
                tts_volume = gr.Slider(0.5, 2.0, 1.0, label="🔊 အသံအကျယ်အဝန်း (Volume Gain)")

            tts_text_input = gr.Textbox(
                label="📝 အသံပြောင်းလိုသော မြန်မာစာသားများကို ထည့်ပါ", 
                lines=6, 
                placeholder="ဤနေရာတွင် စာသားများ ရိုက်ထည့်ပါ..."
            )
            
            tts_generate_btn = gr.Button("🚀 အသံဖိုင် ထုတ်မည် (Generate Audio)", variant="primary")
            
            tts_status_box = gr.Textbox(label="အခြေအနေ (Status)", interactive=False)
            tts_audio_result = gr.Audio(label="🔊 ထွက်လာမည့် အသံဖိုင်နှင့် ဒေါင်းလုဒ်ရယူရန်", type="filepath")

            tts_generate_btn.click(
                fn=run_async_standalone_tts,
                inputs=[tts_text_input, tts_voice_select, tts_volume],
                outputs=[tts_status_box, tts_audio_result]
            )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.queue(default_concurrency_limit=2)
    app.launch(server_name="0.0.0.0", server_port=port)
