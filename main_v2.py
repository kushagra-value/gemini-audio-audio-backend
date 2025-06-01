import os
import asyncio
import json
import traceback
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from config.config import CONFIG, MODEL, SEND_SR, RECV_SR, CHUNK, client
from config.prompts import general_interviewer_prompt
from google import genai
from google.genai import types

app = FastAPI()

# Ensure config aligns with frontend
SEND_SR = 16000
RECV_SR = 16000
CHUNK = 1024  # Buffer size for audio chunks

prompt = """ 
You are an ai interviewer and you are interviewing a candidate for a software engineering position.
You will ask the candidate questions and wait for their response.
You will then ask follow-up questions based on their response.
You will not ask the candidate to write code, but you will ask them to explain their thought process and how they would approach a problem.
"""

class AudioLoop:
    def __init__(self):
        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=20)
        self.session = None
        self.active = True
        self.last_audio_time = time.time()
        self.conversation = []
        self.agent_id = None
        self.dynamic_data = None
        self.general_interviewer_prompt = general_interviewer_prompt
        self.final_prompt = None
        self.ws = None

    def set_websocket(self, ws):
        self.ws = ws
    
    def set_dynamic_data(self, dynamic_data):
        self.dynamic_data = dynamic_data

    def set_agent_id(self, agent_id):
        self.agent_id = agent_id

    def add_label(self, label, text):
        return f"{label}: {text}"

    async def handle_websocket_messages(self):
        while self.active:
            try:
                data = await self.ws.receive()
                if data['type'] == 'websocket.disconnect':
                    raise WebSocketDisconnect
                elif data['type'] == 'websocket.receive':
                    if 'bytes' in data:
                        flag, pcm = data['bytes'][0], data['bytes'][1:]
                        if flag == 0x01:  # Mic audio
                            print(f"Received mic audio: {len(pcm)} bytes")
                            await self.out_queue.put({"data": pcm, "mime_type": "audio/pcm"})
                            self.last_audio_time = time.time()
                    elif 'text' in data:
                        json_data = json.loads(data['text'])
                        if json_data.get("action") == "end_call":
                            print("Received end_call action")
                            self.active = False
                            break
                        text = json_data.get("input", "")
                        if text:
                            print(f"Received text input: {text}")
                            await self.session.send(input=text or ".", end_of_turn=True)
                    else:
                        print(f"Received unknown data type: {data}")
            except WebSocketDisconnect:
                self.active = False
                break
            except Exception as e:
                print(f"Error in handle_websocket_messages: {e}")
                self.active = False
                break

    async def send_audio_to_gemini(self):
        try:
            while self.active:
                msg = await self.out_queue.get()
                print(f"Sending audio to Gemini: {len(msg['data'])} bytes")
                await self.session.send_realtime_input(audio=msg)
        except Exception as e:
            print(f"Error in send_audio_to_gemini: {e}")
            self.active = False

    async def receive_from_gemini(self):
        try:
            while self.active:
                turn = self.session.receive()
                ai_text = ""
                candidate_text = ""

                async for response in turn:
                    if data := response.data:
                        print(f"Received audio data from Gemini: {len(data)} bytes")
                        self.audio_in_queue.put_nowait(data)
                        print(f"Audio in queue size: {self.audio_in_queue.qsize()}")

                    if response.server_content.output_transcription:
                        chunk = response.server_content.output_transcription.text or ""
                        ai_text += chunk

                    if response.server_content.input_transcription:
                        chunk = response.server_content.input_transcription.text or ""
                        candidate_text += chunk

                if ai_text.strip() or candidate_text.strip():
                    transcript_data = {}
                    if ai_text.strip():
                        transcript_data["ai_text"] = ai_text.strip()
                    if candidate_text.strip():
                        transcript_data["candidate_text"] = candidate_text.strip()
                    await self.ws.send_json(transcript_data)

                    if candidate_text.strip():
                        self.conversation.append(self.add_label("User", candidate_text.strip()))
                    if ai_text.strip():
                        self.conversation.append(self.add_label("AI", ai_text.strip()))
                    print("Conversation:", self.conversation)

        except Exception as e:
            print(f"Error in receive_from_gemini: {e}")
            self.active = False

    async def play_audio(self):
        while self.active:
            try:
                pcm = await self.audio_in_queue.get()
                msg = b'\x02' + pcm
                print(f"Playing audio chunk of size: {len(msg)} bytes")
                await self.ws.send_bytes(msg)
            except Exception as e:
                print(f"Error in play_audio: {e}")
                self.active = False
                break

    async def monitor_silence(self):
        try:
            while self.active:
                await asyncio.sleep(0.5)
                time_since_last_audio = time.time() - self.last_audio_time
                if time_since_last_audio > 2.0:
                    print("Detected 2 seconds of silence. Signaling end of speech.")
                    self.last_audio_time = time.time()
        except Exception as e:
            print(f"Error in monitor_silence: {e}")
            self.active = False

    def create_final_prompt(self):
        json_data = self.dynamic_data or {}
        interviewer_personality = "professional, friendly, and encouraging"
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
        final_prompt = self.general_interviewer_prompt
        for placeholder, value in replacements.items():
            final_prompt = final_prompt.replace(placeholder, str(value))
        self.final_prompt = final_prompt
    
    async def run(self):
        try:
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                print("Sending initial prompt to Gemini...")
                await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                print("Initial prompt sent.")
                
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.handle_websocket_messages())
                    tg.create_task(self.send_audio_to_gemini())
                    tg.create_task(self.receive_from_gemini())
                    tg.create_task(self.play_audio())
                    tg.create_task(self.monitor_silence())
        except asyncio.CancelledError:
            print("Session cancelled")
        except Exception as e:
            print(f"Error in AudioLoop: {e}")
            traceback.print_exc()
        finally:
            self.active = False
            print("AudioLoop finished")

@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, agent_id: str = "default-agent"):
    await ws.accept()
    loop = AudioLoop()
    loop.set_websocket(ws)
    try:
        data = await ws.receive_text()
        data = json.loads(data)
        print(f"Received initial data: {data}")
        dynamic_data = data.get("dynamic_data", {})
        if not dynamic_data:
            raise ValueError("Dynamic data is required to start the interview.")
        loop.set_dynamic_data(dynamic_data)
        loop.set_agent_id(agent_id)
        loop.create_final_prompt()
        await loop.run()
    except WebSocketDisconnect:
        loop.active = False
        await ws.close()
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