from fastapi import FastAPI

from google import genai
from google.genai import types
from dotenv import load_dotenv
import os
load_dotenv()




SEND_SR = 16_000  # Updated to match new model requirements
RECV_SR = 24_000
CHUNK = 1024   

MODEL = "models/gemini-2.5-flash-preview-native-audio-dialog"
print()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
# print(f"GEMINI_KEY: {GEMINI_KEY}")
client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=GEMINI_KEY,
)

end_call_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="end_interview_call",
            description="First returns a final audio message to user. Say final ending remarks. Ends the current interview call and disconnects the WebSocket when the user explicitly states they want to stop or end the interview (e.g., 'let's end the interview', 'I want to stop now', 'end the interview').",
            # No parameters are needed for this function.
            parameters=None
        )
    ]
)

# Updated CONFIG for Gemini 2.5 Flash Preview Native Audio Dialog
CONFIG = types.LiveConnectConfig(
    response_modalities=[
        "AUDIO",
    ],
    media_resolution="MEDIA_RESOLUTION_MEDIUM",
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
        )
    ),
    realtime_input_config=types.RealtimeInputConfig(
        turn_coverage="TURN_INCLUDES_ALL_INPUT",
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
            prefix_padding_ms=100,
            silence_duration_ms=1000,
        )
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=25600,
        sliding_window=types.SlidingWindow(target_tokens=12800),
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    generation_config=types.GenerationConfig(
        temperature=0.7,
        top_p=0.95,
        top_k=70
    ),
    tools=[end_call_tool],
    system_instruction=types.Content(parts=[types.Part(text="TRANSCRIBE ONLY IN ENGLISH, NO OTHER LANGUAGES")]),
    session_resumption=types.SessionResumptionConfig(
        handle=None
    )
)