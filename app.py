import gradio as gr
import google.generativeai as genai
import os

def translate_and_process(text, api_key, target_lang="Myanmar"):
    if not api_key:
        return "❌ Error: API Key မထည့်သွင်းရသေးပါ။ ကျေးဇူးပြု၍ API Key ထည့်ပေးပါ။"
    
    try:
        # Gemini API Key အား သတ်မှတ်ခြင်း
        genai.configure(api_key=api_key)
        
        # Error မက်ဆေ့ချ်တွင် ညွှန်ကြားထားသည့် model အသစ် (gemini-3.6-flash) သို့ ပြောင်းလဲထားပါသည်
        model = genai.GenerativeModel("gemini-3.6-flash")
        
        prompt = f"""
        You are an expert translator and movie recap writer.
        Translate and refine the following text into natural, engaging {target_lang} suitable for a video recap script or voice-over.
        
        Text to translate:
        {text}
        """
        
        response = model.generate_content(prompt)
        return response.text
    
    except Exception as e:
        return f"❌ Error: {str(e)}"

# Gradio Interface ဖန်တီးခြင်း
with gr.Blocks(title="AI Movie Recap Studio Pro") as demo:
    gr.Markdown("# 🎬 AI Movie Recap Studio Pro (Multi-Language to Myanmar TTS)")
    gr.Markdown("AI Video Recap Script, Multi-Language Translator & Myanmar Voice-Over Studio")
    
    with gr.Accordion("🔑 API Key Setting", open=True):
        api_key_input = gr.Textbox(
            label="Gemini API Key ထည့်ရန်",
            placeholder="AIzaSy...",
            type="password"
        )
    
    gr.Markdown("### 🌐 အင်္ဂလိပ်၊ ထိုင်း၊ ကိုရီးယား၊ တရုတ် စာသားများကို မြန်မာလို ဘာသာပြန်ပြီး အသံထုတ်နိုင်ပါသည်။")
    
    with gr.Row():
        with gr.Column():
            text_input = gr.Textbox(
                label="📝 (English, Thai, Korean, Chinese, Myanmar) စာသားထည့်ရန်",
                lines=8,
                placeholder="ဒီနေရာမှာ Recap Script သို့မဟုတ် ဘာသာပြန်ချင်သော စာသားကို ထည့်ပါ..."
            )
            submit_btn = gr.Button("🌐 မြန်မာသို့ ဘာသာပြန်မည်", variant="primary")
        
        with gr.Column():
            output_text = gr.Textbox(
                label="✨ ဘာသာပြန်ပြီးသား စာသား (Output)",
                lines=8
            )

    submit_btn.click(
        fn=translate_and_process,
        inputs=[text_input, api_key_input],7
        outputs=output_text
    )

if __name__ == "__main__":
    # Railway/Docker environment အတွက် Server Port နှင့် Host သတ်မှတ်ခြင်း
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
