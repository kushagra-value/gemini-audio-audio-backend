from fastapi import FastAPI
import pyaudio
from google import genai
from google.genai import types




FORMAT = pyaudio.paInt16
SEND_SR = 48_000
RECV_SR = 24_000
CHUNK = 1024   

MODEL = "models/gemini-2.0-flash-live-001"

client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key="AIzaSyDJsgZ9yRFF6lu4DMZxkg3n4MesTTkNpFQ",
)

end_call_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="end_interview_call",
            description="Ends the current interview call and disconnects the WebSocket when the user explicitly states they want to stop or end the interview (e.g., 'let's end the interview', 'I want to stop now', 'end the interview').",
            # No parameters are needed for this function.
            parameters=None
        )
    ]
)

# Try CONFIG without input_audio_transcription first to see if that's the issue
CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="puck"),
        )
    ),
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=100,
            silence_duration_ms=1000,
        )
    ),
    # Comment out input transcription for now
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    generation_config=types.GenerationConfig(
        temperature=0.7,
        top_p=0.95,
        top_k=70
    ),
    tools=[end_call_tool]
)