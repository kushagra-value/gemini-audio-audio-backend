from fastapi import FastAPI

from google import genai
from google.genai import types
from dotenv import load_dotenv
import os
load_dotenv()


FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024 

MODEL = "models/gemini-2.5-flash-live-preview"


GEMINI_KEY = os.getenv("GEMINI_API_KEY")
# print(f"GEMINI_KEY: {GEMINI_KEY}")
client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=os.environ.get("GEMINI_API_KEY"),
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

# Try CONFIG without input_audio_transcription first to see if that's the issue
CONFIG = types.LiveConnectConfig(
    response_modalities=[
        "AUDIO",
    ],
    media_resolution="MEDIA_RESOLUTION_MEDIUM",
    speech_config=types.SpeechConfig(
        language_code="en-IN",
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
        )
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=25600,
        sliding_window=types.SlidingWindow(target_tokens=12800),
    ),
    realtime_input_config=types.RealtimeInputConfig(  # Added for VAD adjustment
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            # start_of_speech_sensitivity="START_SENSITIVITY_HIGH",
            # end_of_speech_sensitivity="END_SENSITIVITY_HIGH",
            prefix_padding_ms=200,
            silence_duration_ms=1000,
        )
    ),
    system_instruction=types.Content(
        parts=[types.Part.from_text(text=prompt)],
        role="user"
    ),
)