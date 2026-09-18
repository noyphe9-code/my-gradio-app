import os
import gradio as gr
import google.generativeai as genai

APP_TITLE = "AI Movie Recap Studio"

def call_gemini(api_key, prompt_text):
    if not api_key:
        return "ကျေးဇူးပြု၍ သင့်ရဲ့ Gemini API Key ကို အရင်ထည့်ပါ။"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        return f"အမှားအယွင်း ဖြစ်ပေါ်သွားသည်: {str(e)}"

# Tab 1: Movie Recap & Translation (Multi-language to Burmese)
def translate_and_recap(api_key, source_text, source_lang):
    prompt = f"Translate and create an engaging movie recap in Burmese from the following {source_lang} text: {source_text}"
    return call_gemini(api_key, prompt)

# Tab 2: Script Writing (Video/Info to Script)
def generate_script(api_key, summary, platform):
    prompt = f"Create a professional YouTube/TikTok video script optimized for {platform} based on this summary, written in Burmese with visual cues: {summary}"
    return call_gemini(api_key, prompt)

# Tab 3: AI Voiceover Text Generator (Text to Speech optimization)
def generate_tts_text(api_key, script_text):
    prompt = f"Optimize the following script into clear, natural voiceover text suitable for Text-to-Speech (TTS) tools, written in Burmese: {script_text}"
    return call_gemini(api_key, prompt)

# Gradio UI တည်ဆောက်ခြင်း
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}")
    gr.Markdown("ဘာသာစကားမျိုးစုံမှ မြန်မာလို ဘာသာပြန်ခြင်း၊ ရုပ်ရှင်ဇာတ်ညွှန်းထုတ်ခြင်းနှင့် အသံသွင်းရန် စာသားပြင်ဆင်ခြင်းများ လုပ်ဆောင်နိုင်သော စနစ်။")
    
    with gr.Row():
        api_key_input = gr.Textbox(
            label="Gemini API Key", 
            type="password", 
            placeholder="သင့်ရဲ့ Gemini API Key ကို ဤနေရာတွင် ထည့်ပါ..."
        )
    
    with gr.Tabs():
        # Tab 1: Multi-language Translation & Recap
        with gr.TabItem("🌐 Multi-Language to Burmese (ဘာသာပြန်နှင့် ဇာတ်လမ်း)"):
            with gr.Row():
                lang_dropdown = gr.Dropdown(
                    choices=["English (အင်္ဂလိပ်)", "Thai (ထိုင်း)", "Korean (ကိုရီးယား)", "Chinese (တရုတ်)"], 
                    value="English (အင်္ဂလိပ်)", 
                    label="မူရင်း ဘာသာစကား (Source Language)"
                )
            source_input = gr.Textbox(label="မူရင်း စာသားများ ထည့်ရန်", lines=5, placeholder="အခြားဘာသာစကားဖြင့် ရေးထားသော ဇာတ်လမ်း သို့မဟုတ် စာသားများကို ဤနေရာတွင် ထည့်ပါ...")
            trans_btn = gr.Button("မြန်မာလို ဘာသာပြန်၍ အနှစ်ချုပ်မည်", variant="primary")
            trans_output = gr.Textbox(label="မြန်မာလို ရလဒ်", lines=10)
            
            trans_btn.click(fn=translate_and_recap, inputs=[api_key_input, source_input, lang_dropdown], outputs=trans_output)

        # Tab 2: Script Writer
        with gr.TabItem("📝 Video to Script (ဗီဒီယိုဇာတ်ညွှန်း)"):
            with gr.Row():
                platform_dropdown = gr.Dropdown(
                    choices=["YouTube Long-form", "TikTok / Shorts", "Facebook Reel"], 
                    value="YouTube Long-form", 
                    label="ပလက်ဖောင်း အမျိုးအစား"
                )
            script_input = gr.Textbox(label="ဇာတ်လမ်း အကျဉ်းချုပ် (သို့) အချက်အလက်များ", lines=5, placeholder="ဇာတ်ညွှန်းထုတ်ရန် အချက်အလက်များ ထည့်ပါ...")
            script_btn = gr.Button("ဗီဒီယို ဇာတ်ညွှန်း ထုတ်လုပ်မည်", variant="primary")
            script_output = gr.Textbox(label="ထုတ်လုပ်ထားသော ဇာတ်ညွှန်း", lines=10)
            
            script_btn.click(fn=generate_script, inputs=[api_key_input, script_input, platform_dropdown], outputs=script_output)

        # Tab 3: Text to Speech (TTS) Helper
        with gr.TabItem("🎙️ Voiceover / TTS Text Prep (အသံထွက်စာသားပြင်ဆင်ရန်)"):
            tts_input = gr.Textbox(label="ဇာတ်ညွှန်း စာသားများ", lines=5, placeholder="အသံထွက် (Narakeet/TTS) အတွက် ပြင်ဆင်လိုသော စာသားများကို ထည့်ပါ...")
            tts_btn = gr.Button("အသံထွက်အတွက် စာသားပုံစံ ပြင်ဆင်မည်", variant="primary")
            tts_output = gr.Textbox(label="အသံထွက်ရန် သင့်လျော်သော စာသားရလဒ်", lines=10)
            
            tts_btn.click(fn=generate_tts_text, inputs=[api_key_input, tts_input], outputs=tts_output)

# Server ပေါ်တွင် Port ချိတ်ဆက်ခြင်း (Render / Railway အတွက်)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    demo.launch(
        server_name="0.0.0.0", 
        server_port=port, 
        share=False
    )
