import os
import re
import time
import zipfile
import json
import subprocess
import asyncio
import gradio as gr
import edge_tts
import yt_dlp
from google import genai

# =========================================================
# CONFIGURATION & SETTINGS
# =========================================================
APP_TITLE = "AI Movie Recap Studio Pro"
MAX_VIDEO_MINUTES = 10
SAVED_API_KEY = ""

# API မှ တိုက်ရိုက်တောင်းဆိုထားသော နောက်ဆုံးထွက် Model များ
GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

VOICES = {
    "Thiha (အမျိုးသားအသံ) - Natural": "my-MM-ThihaNeural",
    "Nilar (အမျိုးသမီးအသံ) - Natural": "my-MM-NilarNeural",
}

# =========================================================
# SYSTEM HELPERS
# =========================================================
def save_api_key(api_key):
    global SAVED_API_KEY
    if api_key and api_key.strip():
        SAVED_API_KEY = api_key.strip()
        return "✅ Gemini API Key ကို အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။"
    return "⚠️ Gemini API Key ထည့်ပေးပါ။"

def get_video_duration(video_path):
    if not video_path or not os.path.exists(video_path):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print("Duration Error:", e)
    return None

def validate_video_duration(video_path):
    duration = get_video_duration(video_path)
    if duration is None:
        return True, "ℹ️ Video duration ကို စစ်ဆေးနေပါသည်..."
    minutes = duration / 60
    if minutes > MAX_VIDEO_MINUTES:
        return False, f"⚠️ Video သည် {minutes:.1f} မိနစ်ရှိပါသည်။ အများဆုံး {MAX_VIDEO_MINUTES} မိနစ်အထိသာ လက်ခံပါသည်။"
    return True, f"✅ Video Length: {minutes:.1f} မိနစ်"

def clean_script_for_tts(script_text):
    if not script_text:
        return ""
    cleaned = []
    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(r"^\s*\[(?:Visual|Scene|Video|Audio|Camera|Action|Narration|Narrator|Dialogue|Intro)\]\s*[:\-]?\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"^\s*Narrator\s*:\s*", "", line, flags=re.IGNORECASE)
        if line.lower() in ["movie recap", "recap script", "burmese recap script", "script"] or line.startswith("---"):
            continue
        cleaned.append(line.strip())
    return "\n".join(cleaned)

def seconds_to_srt_time(seconds):
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},000"

def generate_srt_and_zip(script_text, prefix="myanmar_recap", total_target_duration=None):
    clean_text = clean_script_for_tts(script_text)
    if not clean_text:
        return None, None
    lines = [x.strip() for x in clean_text.splitlines() if x.strip()]
    srt_content = ""
    current_time = 0
    natural_durations = [max(2, min(8, round(len(line) / 11))) for line in lines]
    if total_target_duration and sum(natural_durations) > 0:
        factor = float(total_target_duration) / sum(natural_durations)
        durations = [max(0.5, d * factor) for d in natural_durations]
    else:
        durations = natural_durations
    for idx, line in enumerate(lines, 1):
        duration = durations[idx - 1]
        start_time = current_time
        end_time = current_time + duration
        srt_content += f"{idx}\n{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n{line}\n\n"
        current_time = end_time

    srt_filename = f"{prefix}_subtitle.srt"
    zip_filename = f"{prefix}_subtitle.zip"
    with open(srt_filename, "w", encoding="utf-8-sig") as f:
        f.write(srt_content)
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(srt_filename, arcname=srt_filename)
    return srt_filename, zip_filename

def download_video_from_link(link):
    if not link or not link.strip():
        return None
    output_template = "temp_downloaded_video.%(ext)s"
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "overwrites": True,
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link.strip(), download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
            base = os.path.splitext(filename)[0]
            for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                if os.path.exists(base + ext):
                    return base + ext
    except Exception as e:
        print("Download Error:", e)
    return None

# =========================================================
# DYNAMIC RATIO STYLING
# =========================================================
def get_ratio_css(ratio, container_id="tab1_preview_container"):
    configs = {
        "1:1": {"aspect": "1 / 1", "max_w": "450px"},
        "3:4": {"aspect": "3 / 4", "max_w": "380px"},
        "16:9": {"aspect": "16 / 9", "max_w": "640px"},
        "9:16": {"aspect": "9 / 16", "max_w": "320px"},
    }
    cfg = configs.get(ratio, configs["1:1"])
    return f"""
    <style id="{container_id}-style">
    #{container_id} {{
        width: 100% !important;
        max-width: {cfg["max_w"]} !important;
        margin: 0 auto !important;
        transition: all 0.3s ease-in-out !important;
    }}
    #{container_id} .video-container {{
        width: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        height: auto !important;
        background: #000 !important;
        border-radius: 12px !important;
        overflow: hidden !important;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3) !important;
    }}
    #{container_id} video {{
        width: 100% !important;
        height: 100% !important;
        aspect-ratio: {cfg["aspect"]} !important;
        object-fit: cover !important;
        display: block !important;
    }}
    </style>
    """

def tab3_preview_html(ratio, blur_bg, bg_color, zoom, brightness, contrast, crop_w, crop_h, symmetric, font_size, font_color, sub_x, sub_y):
    aspect={"9:16":"9 / 16","3:4":"3 / 4","16:9":"16 / 9","1:1":"1 / 1"}.get(ratio,"9 / 16")
    ix=(100-float(crop_w))/2 if symmetric else 0; iy=(100-float(crop_h))/2 if symmetric else 0
    bg="linear-gradient(135deg,#263238,#607d8b,#37474f)" if blur_bg else bg_color
    return f"""<div style='max-width:420px;margin:auto;aspect-ratio:{aspect};background:{bg};border-radius:12px;overflow:hidden;position:relative;filter:brightness({brightness}) contrast({contrast});'><div style='position:absolute;inset:{iy}% {ix}%;transform:scale({zoom});background:linear-gradient(135deg,#4fc3f7,#7e57c2,#ef5350);opacity:.82;'></div><div style='position:absolute;left:calc(50% + {sub_x}px);top:calc(50% + {sub_y/2}px);transform:translate(-50%,-50%);width:94%;text-align:center;color:{font_color};font-size:{font_size}px;font-weight:800;text-shadow:2px 2px 3px #000;line-height:1.35;'>နမူနာစာတန်းထိုးစာ<br>အောက်တစ်ကြောင်း နမူနာစာ</div></div><p style='text-align:center'>Ratio {ratio} · Crop အလယ်ညီ · Blur/Color Preview</p>"""

# =========================================================
# GEMINI GENERATION
# =========================================================
def build_recap_prompt(selected_ratio):
    return f"""
သင်သည် ထိပ်တန်း Professional Movie Recap Scriptwriter ဖြစ်သည်။ 
Target Video Frame Ratio: {selected_ratio}
ပေးထားသော ဗီဒီယိုကို အစမှအဆုံးအထိ တိကျသေချာစွာ ကြည့်ရှုနားထောင်ပြီး အောက်ပါစည်းမျဉ်းများအတိုင်း "မြန်မာ Movie Recap Script" ကို ရေးသားပေးပါ-

[စည်းမျဉ်းများ]
၁။ ဗီဒီယိုထဲတွင် တကယ်ဖြစ်ပျက်နေသော အဖြစ်အပျက် အမှန်များကိုသာ အချိန်အစဉ်လိုက် အတိုချုံး၍ တိကျမှန်ကန်စွာ ရေးပါ။
၂။ ဇာတ်ကောင်များ၏ ပြောစကားများကို သဘာဝကျကျ ဆွဲဆောင်မှုရှိသော မြန်မာစကားပြောအဖြစ် တိုက်ရိုက်ပြန်ဆိုပါ။
၃။ [Visual], [Scene], [Narrator], [Dialogue], [Intro] စသည့် Technical Label များနှင့် စကားအပိုများ လုံးဝမထည့်ပါနှင့်။
၄။ TTS အသံထွက်ဖတ်ရာတွင် ချောမွေ့စေရန် စာကြောင်းတစ်ကြောင်းချင်းစီကို တိုတိုရှင်းရှင်းနှင့် အဓိပ္ပာယ်ပြည့်စုံစွာ ရေးပေးပါ။
"""

def generate_with_retry(client, uploaded_file, prompt):
    retry_delays = [3, 7]
    last_error = None
    for model_name in GEMINI_MODELS:
        for attempt in range(len(retry_delays) + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded_file, prompt],
                )
                if response and response.text:
                    return response.text, model_name
            except Exception as e:
                last_error = e
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                else:
                    break
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")

def run_gemini_video_analysis(target_media, ratio_choice):
    global SAVED_API_KEY
    if not SAVED_API_KEY:
        raise ValueError("Gemini API Key မရှိသေးပါ။ 🔑 API Key Setting ထဲတွင် အရင်ထည့်သွင်းပေးပါ။")
    valid, msg = validate_video_duration(target_media)
    if not valid:
        raise ValueError(msg)

    client = genai.Client(api_key=SAVED_API_KEY)
    uploaded_file = client.files.upload(file=target_media)
    
    start_wait = time.time()
    while True:
        if uploaded_file.state and uploaded_file.state.name == "ACTIVE":
            break
        if uploaded_file.state and uploaded_file.state.name == "FAILED":
            raise RuntimeError("Gemini Video Processing မအောင်မြင်ပါ။ ဗီဒီယိုဖိုင်ကို စစ်ဆေးပါ။")
        if time.time() - start_wait > 900:
            raise TimeoutError("Gemini Video Processing ကြာမြင့်လွန်းနေပါသည်။")
        time.sleep(3)
        uploaded_file = client.files.get(name=uploaded_file.name)

    prompt = build_recap_prompt(ratio_choice)
    script_text, used_model = generate_with_retry(client, uploaded_file, prompt)
    clean_text = clean_script_for_tts(script_text)
    return clean_text, used_model, msg

# =========================================================
# TTS LOGIC
# =========================================================
async def generate_myanmar_tts(text, voice_choice, speed_percent, output_name="tab2_output.mp3"):
    clean_text = clean_script_for_tts(text)
    if not clean_text:
        return None, None, None
    selected_voice = VOICES.get(voice_choice, "my-MM-ThihaNeural")
    rate_str = f"{int(speed_percent):+d}%"
    communicate = edge_tts.Communicate(clean_text, selected_voice, rate=rate_str)
    await communicate.save(output_name)
    srt_file, zip_file = generate_srt_and_zip(clean_text, prefix=output_name.replace(".mp3", ""))
    return output_name, srt_file, zip_file

# =========================================================
# TAB 3: ONE-CLIP SCENE-AWARE COMPOSER
# =========================================================
def _freeze_intervals(path, minimum=1.5):
    try:
        r=subprocess.run(["ffmpeg","-hide_banner","-i",path,"-vf",f"freezedetect=n=-60dB:d={minimum}","-an","-f","null","-"],capture_output=True,text=True,timeout=300)
        starts=[float(x) for x in re.findall(r"freeze_start:([0-9]+(?:\.[0-9]+)?)",r.stderr)]
        ends=[float(x) for x in re.findall(r"freeze_end:([0-9]+(?:\.[0-9]+)?)",r.stderr)]
        return [(a,b) for a,b in zip(starts,ends) if b-a>=minimum]
    except Exception as e:
        print("Freeze detection skipped:",e); return []

def _semantic_keep(path, narration):
    if not SAVED_API_KEY: return None
    try:
        client=genai.Client(api_key=SAVED_API_KEY); f=client.files.upload(file=path); started=time.time()
        while f.state and f.state.name not in ("ACTIVE","FAILED"):
            if time.time()-started>900: return None
            time.sleep(3); f=client.files.get(name=f.name)
        if not f.state or f.state.name!="ACTIVE": return None
        prompt=f"""Inspect this video as a scene-aware editor. Return ONLY JSON {{"segments":[{{"start":0,"end":2,"keep":true}}]}}. Keep chronological scenes that visually support this narration; cut only irrelevant, duplicate, frozen, or clearly mismatched scenes. Do not remove more than 40 percent. Narration: {narration}"""
        raw,_=generate_with_retry(client,f,prompt); m=re.search(r"\{.*\}",raw,re.S)
        if not m: return None
        data=json.loads(m.group(0)); dur=get_video_duration(path) or 0; out=[]
        for x in data.get("segments",[]):
            if x.get("keep",True):
                a=max(0,float(x.get("start",0))); b=min(dur,float(x.get("end",0)))
                if b-a>.12: out.append((a,b))
        if out and sum(b-a for a,b in out)>=dur*.60: return out
    except Exception as e: print("Semantic mapping fallback:",e)
    return None

def _subtract_freezes(duration, frozen):
    keep=[]; cur=0
    for a,b in sorted(frozen):
        if a>cur+.12: keep.append((cur,a))
        cur=max(cur,b)
    if duration>cur+.12: keep.append((cur,duration))
    return keep or [(0,duration)]

def scene_auto_cut(path,narration,out="oneclip_autocut.mp4"):
    dur=get_video_duration(path)
    if not dur or dur<2: return path
    semantic=_semantic_keep(path,narration)
    keep=semantic or _subtract_freezes(dur,_freeze_intervals(path))
    if len(keep)<=1: return path
    chains=[]
    for i,(a,b) in enumerate(keep):
        d=max(.12,b-a); fade=min(.08,d/3)
        chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,fade=t=in:st=0:d={fade:.3f},fade=t=out:st={max(0,d-fade):.3f}:d={fade:.3f}[v{i}]")
    chains.append("".join(f"[v{i}]" for i in range(len(keep)))+f"concat=n={len(keep)}:v=1:a=0[vout]")
    try:
        subprocess.run(["ffmpeg","-y","-i",path,"-filter_complex",";".join(chains),"-map","[vout]","-an","-c:v","libx264","-preset","veryfast","-crf","23",out],check=True,capture_output=True,text=True,timeout=900)
        return out if os.path.exists(out) else path
    except Exception as e: print("Auto-cut fallback:",e); return path

def render_one_clip(source,voice_audio,srt,ratio,resolution,quality,speed,crop_w,crop_h,symmetric,blur_bg,bg_color,zoom,brightness,contrast,orig_audio,bgm,bgm_volume,font_size,font_color,sub_y):
    dims={"9:16":(1080,1920),"3:4":(810,1080),"16:9":(1920,1080),"1:1":(1080,1080)}; bw,bh=dims.get(ratio,(1080,1920))
    short={"480p":480,"720p":720,"1080p":1080}.get(resolution,1080); scale=short/min(bw,bh); w=int(bw*scale)//2*2; h=int(bh*scale)//2*2
    preset={"Fast":"veryfast","Balanced":"medium","High":"slow"}.get(quality,"medium")
    cw=max(30,min(100,float(crop_w)))/100; ch=max(30,min(100,float(crop_h)))/100
    crop=f"crop=iw*{cw:.4f}:ih*{ch:.4f}:(iw-iw*{cw:.4f})/2:(ih-ih*{ch:.4f})/2" if symmetric else f"crop=iw*{cw:.4f}:ih*{ch:.4f}:0:0"
    bg=f"color=c=0x{bg_color.lstrip('#')}:s={w}x{h}[bg]" if not blur_bg else f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=18:8[bg]"
    clean_color=font_color.lstrip('#')
    ass_color='&H00'+clean_color[4:6]+clean_color[2:4]+clean_color[0:2]+'&' if len(clean_color)==6 else '&H00FFFFFF&'
    subtitle_filter=f"subtitles='{srt.replace('\\','/').replace(':','\\:')}':force_style='FontSize={int(font_size)},PrimaryColour={ass_color},OutlineColour=&H00000000&,Outline=2,Shadow=1,Alignment=2,MarginV={max(10,int(120+float(sub_y)/4))}'"
    vf=f"[0:v]split=2[b0][f0];{bg.replace('[0:v]','[b0]')};[f0]{crop},scale=iw*{zoom}:ih*{zoom}:force_original_aspect_ratio=decrease,eq=brightness={brightness-1:.2f}:contrast={contrast:.2f}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v];[v]{subtitle_filter}[vout]"
    inputs=["ffmpeg","-y","-stream_loop","-1","-i",source,"-i",voice_audio]; amap=[]
    if bgm and os.path.exists(bgm): inputs += ["-stream_loop","-1","-i",bgm]
    af=f"[1:a]atempo={max(.5,min(2,float(speed))):.3f}[tts]"
    if bgm and os.path.exists(bgm): af += f";[2:a]volume={bgm_volume}[bg];[tts][bg]amix=inputs=2:duration=first[a]"
    else: af += ";[tts]anull[a]"
    if orig_audio: af += ";[0:a]volume=.2[orig];[a][orig]amix=inputs=2:duration=first[aout]"
    else: af += ";[a]anull[aout]"
    out="oneclip_final.mp4"; cmd=inputs+["-filter_complex",vf+";"+af,"-map","[vout]","-map","[aout]","-t",str((get_video_duration(voice_audio) or 10)/max(.5,min(2,float(speed)))),"-c:v","libx264","-preset",preset,"-crf","23","-c:a","aac","-b:a","192k","-movflags","+faststart",out]
    subprocess.run(cmd,check=True,capture_output=True,text=True,timeout=1200); return out

def tab3_one_clip(v_file,v_url,bgm,voice,speed,ratio,resolution,quality,blur_bg,bg_color,crop_w,crop_h,symmetric,zoom,brightness,contrast,orig_audio,bgm_volume,font_size,font_color,sub_x,sub_y):
    source=v_file or download_video_from_link(v_url)
    if not source or not os.path.exists(source): return None,None,"",None,"⚠️ Video file/link ထည့်ပါ။"
    try:
        script,model,msg=run_gemini_video_analysis(source,ratio); audio,_,_=asyncio.run(generate_myanmar_tts(script,voice,speed,"tab3_voice.mp3")); dur=get_video_duration(audio) or 10
        srt,z=generate_srt_and_zip(script,"tab3",dur); cut=scene_auto_cut(source,script); final=render_one_clip(cut,audio,srt,ratio,resolution,quality,speed,crop_w,crop_h,symmetric,blur_bg,bg_color,zoom,brightness,contrast,orig_audio,bgm,bgm_volume,font_size,font_color,sub_y)
        return final,final,script,srt,f"✅ One-Clip ပြီးပါပြီ။ AI scene auto-cut + Voice/Subtitle sync အောင်မြင်ပါသည်။ Model: {model}"
    except Exception as e: return None,None,"",None,f"❌ Tab 3 Error: {e}"

# =========================================================
# TAB CONTROLLERS
# =========================================================
def tab1_analyze(v_file, v_url, ratio):
    target = v_file if v_file else download_video_from_link(v_url)
    if not target or not os.path.exists(target):
        return "", "", "⚠️ Video ရှာမတွေ့ပါ။ ဖိုင် သို့မဟုတ် Link ထည့်ပါ။", None, None
    try:
        clean_text, model, dur_msg = run_gemini_video_analysis(target, ratio)
        srt, zip_f = generate_srt_and_zip(clean_text)
        status = f"✅ Script ရေးသားပြီးပါပြီ! (Model: {model})\n{dur_msg}"
        return clean_text, clean_text, status, srt, zip_f
    except Exception as e:
        return "", "", f"❌ Error: {str(e)}", None, None

def tab2_tts(text, voice, speed):
    try:
        mp3, srt, zip_f = asyncio.run(generate_myanmar_tts(text, voice, speed, "tab2_output.mp3"))
        return mp3, mp3, srt, zip_f
    except Exception as e:
        print("Tab 2 Error:", e)
        return None, None, None, None

# =========================================================
# GRADIO UI
# =========================================================
with gr.Blocks(title=APP_TITLE) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}\n**AI Video Recap Script & Myanmar Voice-Over**")

    with gr.Tabs() as main_tabs:
        # --- API KEY TAB ---
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
                    
                    v1_ratio = gr.Radio(["1:1", "3:4", "16:9", "9:16"], value="1:1", label="📐 Preview Screen Aspect Ratio")
                    v1_gen_btn = gr.Button("🚀 Recap Script စတင်ထုတ်မည်", variant="primary")
                
                with gr.Column(scale=1):
                    v1_css = gr.HTML(get_ratio_css("1:1", "tab1_preview_container"))
                    v1_preview = gr.Video(label="📺 Video Preview (Selected Ratio View)", elem_id="tab1_preview_container")
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
                    v2_speed = gr.Slider(-30, 50, value=5, step=1, label="⚡ Speed (%)")
                    v2_btn = gr.Button("⚡ မြန်မာအသံဖိုင် ဖန်တီးမည်", variant="primary")
                with gr.Column(scale=1):
                    v2_audio = gr.Audio(label="🔊 Voice Preview (အသံစမ်းနားထောင်ရန်)", autoplay=True)
                    v2_mp3 = gr.File(label="🎵 MP3 ဖိုင် ဒေါင်းလုဒ်")
            with gr.Row():
                v2_srt = gr.File(label="📄 SRT")
                v2_zip = gr.File(label="📦 SRT ZIP")

            v2_btn.click(tab2_tts, inputs=[v2_input_text, v2_voice, v2_speed], outputs=[v2_audio, v2_mp3, v2_srt, v2_zip])

        # --- TAB 3: ONE-CLIP STUDIO ---
        with gr.TabItem("3️⃣ One-Clip Auto Edit Studio", id="tab_one_clip"):
            gr.Markdown("### 🎬 Video + Voice + Subtitle + AI Scene Auto-Cut")
            with gr.Row():
                with gr.Column(scale=1):
                    t3_file=gr.Video(label="📹 Video File")
                    t3_url=gr.Textbox(label="🔗 YouTube / TikTok Link")
                    t3_bgm=gr.Audio(label="🎵 Background Music", type="filepath")
                    t3_voice=gr.Dropdown(list(VOICES.keys()), value=list(VOICES.keys())[0], label="🎤 Voice")
                    t3_speed=gr.Slider(-30,50,value=5,step=1,label="⚡ Voice Speed (%)")
                    t3_orig=gr.Checkbox(value=False,label="🔊 Original Audio ဖွင့်မည်")
                    t3_bgm_vol=gr.Slider(0,1,value=.15,step=.05,label="🎚️ BGM Volume")
                    t3_ratio=gr.Radio(["9:16","3:4","16:9","1:1"],value="9:16",label="📐 Ratio")
                    t3_resolution=gr.Radio(["480p","720p","1080p"],value="1080p",label="🖥️ Resolution")
                    t3_quality=gr.Radio(["Fast","Balanced","High"],value="Balanced",label="🎞️ Quality")
                    t3_blur=gr.Checkbox(value=True,label="🌫️ Crop အလွတ်နေရာ Blur Background")
                    t3_bg=gr.ColorPicker(value="#202020",label="🎨 Blur ပိတ်လျှင် Background Color")
                    with gr.Row():
                        t3_cw=gr.Slider(30,100,value=100,step=1,label="✂️ Crop Width %")
                        t3_ch=gr.Slider(30,100,value=100,step=1,label="✂️ Crop Height %")
                    t3_sym=gr.Checkbox(value=True,label="↔️↕️ အလယ်တည့်တည့် နှစ်ဖက်တူ Crop")
                    t3_zoom=gr.Slider(.5,2.5,value=1,step=.05,label="🔍 Zoom")
                    with gr.Row():
                        t3_bright=gr.Slider(.5,1.5,value=1,step=.05,label="☀️ Brightness")
                        t3_contrast=gr.Slider(.5,1.5,value=1,step=.05,label="🌓 Contrast")
                    with gr.Row():
                        t3_fsize=gr.Slider(10,80,value=26,step=1,label="🔤 Subtitle Size")
                        t3_fcolor=gr.ColorPicker(value="#FFFFFF",label="🎨 Subtitle Color")
                    with gr.Row():
                        t3_sx=gr.Slider(-500,500,value=0,step=5,label="↔️ Subtitle X")
                        t3_sy=gr.Slider(-500,500,value=-260,step=5,label="↕️ Subtitle Y")
                    t3_run=gr.Button("✨ Generate One-Clip Video",variant="primary",size="lg")
                with gr.Column(scale=1):
                    t3_preview_css=gr.HTML(get_ratio_css("9:16","tab3_preview_container"))
                    t3_preview=gr.Video(label="📺 Preview (Ratio အတိုင်း)",elem_id="tab3_preview_container")
                    t3_sample=gr.HTML(tab3_preview_html("9:16",True,"#202020",1,1,1,100,100,True,26,"#FFFFFF",0,-260))
                    t3_final=gr.Video(label="🎬 Final One-Clip")
                    t3_download=gr.File(label="📥 Download Video")
                    t3_srt=gr.File(label="📄 Download SRT")
                    t3_script=gr.Textbox(label="📝 Generated Script",lines=8)
                    t3_status=gr.Markdown("အဆင်သင့်ဖြစ်ပါသည်။")

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

    t3_file.change(lambda f:f,inputs=t3_file,outputs=t3_preview)
    t3_load = t3_url.submit(download_video_from_link,inputs=t3_url,outputs=t3_preview)
    t3_ratio.change(lambda r:get_ratio_css(r,"tab3_preview_container"),inputs=t3_ratio,outputs=t3_preview_css)
    preview_style_inputs=[t3_ratio,t3_blur,t3_bg,t3_zoom,t3_bright,t3_contrast,t3_cw,t3_ch,t3_sym,t3_fsize,t3_fcolor,t3_sx,t3_sy]
    for _c in preview_style_inputs:
        _c.change(tab3_preview_html,inputs=preview_style_inputs,outputs=t3_sample)
    t3_run.click(
        tab3_one_clip,
        inputs=[t3_file,t3_url,t3_bgm,t3_voice,t3_speed,t3_ratio,t3_resolution,t3_quality,t3_blur,t3_bg,t3_cw,t3_ch,t3_sym,t3_zoom,t3_bright,t3_contrast,t3_orig,t3_bgm_vol,t3_fsize,t3_fcolor,t3_sx,t3_sy],
        outputs=[t3_final,t3_download,t3_script,t3_srt,t3_status]
    )


# Server Port Configuration for Render & Railway
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())
