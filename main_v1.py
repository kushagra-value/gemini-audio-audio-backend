import os
import asyncio
import base64
import io
import traceback
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio, struct
from config.config import CONFIG, MODEL, client, SEND_SR, RECV_SR, CHUNK, FORMAT
from config.prompts import general_interviewer_prompt
import pyaudio

from google import genai
from google.genai import types

app = FastAPI()

pya = pyaudio.PyAudio()

prompt = """ 
You are an ai interviewer and you are interviewing a candidate for a software engineering position.
You will ask the candidate questions and wait for their response.
you will then ask follow-up questions based on their response.
You will not ask the candidate to write code, but you will ask them to explain their thought process and how they would approach a problem.
"""

class AudioLoop:
    def __init__(self):
        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=20)  # Match your WebSocket handler
        self.session = None
        self.active = True
        self.last_audio_time = time.time()
        self.conversation = []
        self.agent_id = None
        self.dynamic_data = None
        self.general_interviewer_prompt = general_interviewer_prompt
        self.final_prompt = None
    def set_websocket(self, ws):
        self.ws = ws
    
    def set_dynamic_data(self, dynamic_data):
            self.dynamic_data = dynamic_data

    def set_agent_id(self, agent_id):
        self.agent_id = agent_id

    def add_label(self, label, text):
        return f"{label}: {text}"

    async def send_text(self):
        while self.active:
            text = await asyncio.to_thread(input, "message > ")
            if text.lower() == "q":
                self.active = False
                break
            await self.session.send(input=text or ".", end_of_turn=True)

    async def listen_audio(self):          # REPLACE the PyAudio mic reader
        while self.active:
            flag, pcm = await self._read_ws_chunk()
            if flag == 0x01:               # mic-side chunk
                await self.out_queue.put({"data": pcm, "mime_type": "audio/pcm"})
                # print(f"Received {len(pcm)} bytes of audio data from mic")
                

    async def send_audio_to_gemini(self):
        """Match your WebSocket handler's method name and logic"""
        try:
            while self.active:
                msg = await self.out_queue.get()
                # print(f"Sending {len(msg['data'])} bytes of audio data to Gemini")
                await self.session.send_realtime_input(audio=msg)
        except Exception as e:
            print(f"Error in send_audio_to_gemini: {e}")
            traceback.print_exc()

    async def receive_from_gemini(self):
        """Match your WebSocket handler's method name and logic"""
        try:
            while self.active:
                turn = self.session.receive()

                # Buffer for each label
                ai_text = ""
                candidate_text = ""

                async for response in turn:
                    # Handle audio data
                    if data := response.data:
                        print(f"Received audio data from Gemini: {len(data)} bytes")
                        self.audio_in_queue.put_nowait(data)

                    # Handle transcriptions
                    if response.server_content.output_transcription:
                        chunk = response.server_content.output_transcription.text or ""
                        ai_text += chunk
                        print("AI Transcript:", chunk)

                    if response.server_content.input_transcription:
                        chunk = response.server_content.input_transcription.text or ""
                        candidate_text += chunk
                        print("User Transcript:", chunk)

                # Append only once per speaker at the end of the turn
                if candidate_text.strip():
                    self.conversation.append(self.add_label("User", candidate_text.strip()))
                    print("Appended User text to conversation")

                if ai_text.strip():
                    self.conversation.append(self.add_label("AI", ai_text.strip()))
                    print("Appended AI text to conversation")

                print("conversation:", self.conversation)
                print("Turn complete")

        except Exception as e:
            print(f"Error in receive_from_gemini: {e}")
            traceback.print_exc()

    async def play_audio(self):            # REPLACE the PyAudio speaker writer
        while self.active:
            pcm = await self.audio_in_queue.get()
            msg = struct.pack("B", 0x02) + pcm
            print(type(msg))
            await self.ws.send_bytes(msg)
            
    # helper – read one framed message
    async def _read_ws_chunk(self):
        data = await self.ws.receive_bytes()
        return data[0], data[1:]

    async def monitor_silence(self):
        """Add the silence monitoring from your WebSocket handler"""
        try:
            while self.active:
                await asyncio.sleep(0.5)  # Check every 0.5 seconds
                time_since_last_audio = time.time() - self.last_audio_time
                if time_since_last_audio > 2.0:  # 2 seconds threshold
                    print("Detected 2 seconds of silence. Signaling end of speech.")
                    # This might help trigger transcription
                    self.last_audio_time = time.time()  # Reset to prevent repeated signals
        except Exception as e:
            print(f"Error in monitor_silence: {e}")
            traceback.print_exc()

    def create_final_prompt(self):
        """
        Replaces placeholders in the general_interviewer_prompt with values from json_data.
        
        Args:
            json_data (dict): The JSON data containing values for placeholders.
            general_interviewer_prompt (str): The template string with placeholders.
        
        Returns:
            str: The final prompt with all placeholders replaced.
        """
        json_data = self.dynamic_data  # Assuming dynamic_data is set before this method is called
        # Default value for interviewerPersonality since it's not in the JSON
        interviewer_personality = "professional, friendly, and encouraging"
        
        # Create a dictionary of placeholders and their corresponding values
        replacements = {
            "{{role}}": json_data.get("role", ""),
            "{{mins}}": json_data.get("mins", ""),
            "{{name}}": json_data.get("name", ""),
            "{{objective}}": json_data.get("objective", ""),
            "{{questionFocus}}": json_data.get("questionFocus", ""),
            "{{description}}": json_data.get("description", ""),
            "{{interviewerName}}": json_data.get("interviewerName", ""),
            "{{interviewerPersonality}}": interviewer_personality,
            "{{candidateName}}": json_data.get("name", ""),
            "{{behavioralQuestions}}": json_data.get("behavioralQuestions", ""),
            "{{questions}}": json_data.get("questions", "")
        }
        
        # Replace placeholders in the prompt
        final_prompt = self.general_interviewer_prompt
        for placeholder, value in replacements.items():
            final_prompt = final_prompt.replace(placeholder, str(value))
        
        self.final_prompt = final_prompt
    
    async def run(self):
        """Match your WebSocket handler structure"""
        try:
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                
                # Send initial prompt like your WebSocket handler
                print("Sending initial prompt to Gemini...")
                await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                print("Initial prompt sent.")
                
                # Create tasks matching your WebSocket handler structure
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.send_audio_to_gemini())
                    tg.create_task(self.receive_from_gemini())
                    tg.create_task(self.listen_audio())
                    tg.create_task(self.play_audio())
                    tg.create_task(self.send_text())
                    # tg.create_task(self.monitor_silence())

        except asyncio.CancelledError:
            print("Session cancelled")
        except Exception as e:
            print(f"Error in AudioLoop: {e}")
            traceback.print_exc()
        finally:
            self.active = False
            if hasattr(self, 'audio_stream'):
                self.audio_stream.close()
            print("AudioLoop finished")



@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, agent_id: str):
    await ws.accept()
    loop = AudioLoop()            # your class, unchanged except ↓
    loop.set_websocket(ws)        # small helper you add
    try:
        data = await ws.receive_json()
        dynamic_data = data.get("dynamic_data", {})
        loop.set_dynamic_data(dynamic_data)
        loop.set_agent_id(agent_id)  # set agent_id in the loop
        loop.create_final_prompt()
        await loop.run()          # this now runs until the socket closes
    except WebSocketDisconnect:
        loop.active = False
    except Exception as e:
        print(f"Error: {e}")
        loop.active = False
        await ws.close()


@app.get("/")
async def root():
    return {"message": "WebSocket server is running. Connect to /ws/audio for audio processing."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)