import os
import gradio as gr


APP_TITLE = "🎬 AI Movie Dubbing Studio"


def preview_video(video):
    if not video:
        return None
    return video


def generate():
    return "🚀 Generate system ကို နောက်အဆင့်မှာ ထည့်ပါမယ်။"


with gr.Blocks(
    title=APP_TITLE,
    theme=gr.themes.Soft()
) as app:

    gr.Markdown(
        """
        # 🎬 ALL IN ONE
        ### 🎙️ Movie Dubbing Studio
        """
    )

    with gr.Tab("🎬 Movie Dubbing"):

        with gr.Group():
            gr.Markdown("### 🔑 Gemini API Key")

            api_key = gr.Textbox(
                label="Gemini API Key",
                placeholder="Enter your Gemini API Key",
                type="password"
            )

        with gr.Group():
            gr.Markdown("### 🎙️ Voice Mode")

            voice_mode = gr.Radio(
                choices=[
                    "Auto",
                    "Male",
                    "Female"
                ],
                value="Auto",
                label="Voice Mode"
            )

        with gr.Group():
            gr.Markdown("### 🗣️ Voice")

            voice = gr.Dropdown(
                choices=[
                    "Myanmar Female",
                    "Myanmar Male"
                ],
                value="Myanmar Female",
                label="Voice"
            )

        with gr.Group():
            gr.Markdown("### 📤 Upload Video")

            video_input = gr.Video(
                label="Original Video",
                sources=["upload"],
                type="filepath"
            )

        with gr.Group():
            gr.Markdown("### 👁️ Live Preview")

            video_preview = gr.Video(
                label="Video Preview",
                interactive=False
            )

        video_input.change(
            fn=preview_video,
            inputs=video_input,
            outputs=video_preview
        )

        with gr.Group():
            gr.Markdown("### 🎵 Voice Preview")

            audio_preview = gr.Audio(
                label="Voice Preview",
                type="filepath"
            )

        generate_btn = gr.Button(
            "🚀 Generate",
            variant="primary",
            size="lg"
        )

        status = gr.Textbox(
            label="📊 Status",
            interactive=False
        )

        generate_btn.click(
            fn=generate,
            inputs=None,
            outputs=status
        )

    gr.Markdown(
        """
        ---
        **🎬 AI Movie Dubbing Studio**
        """
    )


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 7860))

    app.launch(
        server_name="0.0.0.0",
        server_port=port
    )
