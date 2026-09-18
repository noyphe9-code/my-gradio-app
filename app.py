import os
import gradio as gr
import google.generativeai as genai

# Gemini API ကို 3.6-flash မော်ဒယ်ဖြင့် အသုံးပြုရန် ချိတ်ဆက်ခြင်း
APP_TITLE = "AI Movie Recap Studio"

def call_gemini(api_key, prompt_text):
    if not api_key:
        return "ကျေးဇူးပြု၍ သင့်ရဲ့ Gemini API Key ကို အရင်ထည့်ပါ။"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-3.6-flash')
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        return f"အမှားအယွင်း ဖြစ်ပေါ်သွားသည်: {str(e)}"

# Tab 1: Movie Recap (ရုပ်ရှင်ဇာတ်လမ်း အနှစ်ချုပ်)
def generate_recap(api_key, movie_info, tone):
    prompt = f"Create an engaging movie recap with a {tone} tone based on the following details, written in Burmese: {movie_info}"
    return call_gemini(api_key, prompt)

# Tab 2: Translation (ဘာသာပြန်ဆိုခြင်း - အင်္ဂလိပ်မှ မြန်မာသို့)
def translate_text(api_key, text):
    prompt = f"Translate the following text accurately into natural and engaging Burmese for content creation: {text}"
    return call_gemini(api_key, prompt)

# Tab 3: Script Writing (ဗီဒီယို ဇာတ်ညွှန်းနှင့် အသံထွက် ဇာတ်ကောင်ခွဲခြင်း)
def generate_script(api_key, summary):
    prompt = f"Create a professional YouTube video script with voiceover and visual cues based on this summary, written in Burmese: {summary}"
    return call_gemini(api_key, prompt)

# Gradio UI တည်ဆောက်ခြင်း (Tabs အစုံအလင်)
with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft()) as demo:
    gr.Markdown(f"# 🎬 {APP_TITLE}")
    gr.Markdown("Gemini (`gemini-3.6-flash`) ကို အသုံးပြု၍ ရုပ်ရှင်အနှစ်ချုပ်ခြင်း၊ ဘာသာပြန်ခြင်းနှင့် ဇာတ်ညွှန်းရေးသားခြင်းများ လုပ်ဆောင်နိုင်သော စနစ်။")
    
    with gr.Row():
        api_key_input = gr.Textbox(
            label="Gemini API Key", 
            type="password", 
            placeholder="သင့်ရဲ့ Gemini API Key ကို ဤနေရာတွင် ထည့်ပါ..."
        )
    
    with gr.Tabs():
        # Tab 1: Movie Recap
        with gr.TabItem("🎥 Movie Recap (ရုပ်ရှင်အနှစ်ချုပ်)"):
            with gr.Row():
                movie_input = gr.Textbox(label="ရုပ်ရှင်အကြောင်းအရာ (သို့) အချက်အလက်များ", lines=5, placeholder="ရုပ်ရှင်ဇာတ်လမ်းအကျဉ်းကို ရိုက်ထည့်ပါ...")
            with gr.Row():
                tone_dropdown = gr.Dropdown(choices=["Exciting & Dramatic (စိတ်လှုပ်ရှားစရာ)", "Funny & Casual (ဟာသ/ပေါ့ပေါ့ပါးပါး)", "Serious & Professional (လေးနက်သော)"], value="Exciting & Dramatic (စိတ်လှုပ်ရှားစရာ)", label="ပြောပုံဆိုပုံ ပုံစံ (Tone)")
            recap_btn = gr.Button("ဇာတ်လမ်းအနှစ်ချုပ် ထုတ်မည်", variant="primary")
            recap_output = gr.Textbox(label="ထွက်လာမည့် အနှစ်ချုပ် ရလဒ်", lines=10)
            
            recap_btn.click(fn=generate_recap, inputs=[api_key_input, movie_input, tone_dropdown], outputs=recap_output)

        # Tab 2: Translation
        with gr.TabItem("🌐 Translation (ဘာသာပြန်ရန်)"):
            with gr.Row():
                trans_input = gr.Textbox(label="ဘာသာပြန်လိုသော စာသား (English)", lines=5, placeholder="အင်္ဂလိပ်လို စာသားများကို ဤနေရာတွင် ထည့်ပါ...")
            trans_btn = gr.Button("မြန်မာလို ဘာသာပြန်မည်", variant="primary")
            trans_output = gr.Textbox(label="ဘာသာပြန် ရလဒ်", lines=10)
            
            trans_btn.click(fn=translate_text, inputs=[api_key_input, trans_input], outputs=trans_output)

        # Tab 3: Script Writer
        with gr.TabItem("📝 Script Writer (ဗီဒီယိုဇာတ်ညွှန်း)"):
            with gr.Row():
                script_input = gr.Textbox(label="ဇာတ်လမ်း အကျဉ်းချုပ်", lines=5, placeholder="ဇာတ်ညွှန်းထုတ်ရန် အချက်အလက်များ ထည့်ပါ...")
            script_btn = gr.Button("ဗီဒီယို ဇာတ်ညွှန်း ထုတ်လုပ်မည်", variant="primary")
            script_output = gr.Textbox(label="ထုတ်လုပ်ထားသော ဇာတ်ညွှန်း", lines=10)
            
            script_btn.click(fn=generate_script, inputs=[api_key_input, script_input], outputs=script_output)

# Server ပေါ်တွင် Port ချိတ်ဆက်ခြင်း (Render နှင့် Railway နှစ်ခုလုံးအတွက် အဆင်ပြေစေရန်)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(
        server_name="0.0.0.0", 
        server_port=port, 
        share=False
    )
