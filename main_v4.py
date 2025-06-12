from contextlib import asynccontextmanager
import logging
import os
import asyncio
import json
import traceback
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from config.config import CONFIG, MODEL, SEND_SR, RECV_SR, CHUNK, client
from config.prompts import general_interviewer_prompt
from google import genai
from google.genai import types
from config.database import connect_to_mongo, close_mongo_connection, get_database
from services.call_service import call_service
app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioLoop:
    def __init__(self):
        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue()
        self.session = None
        self.active = True
        self.last_audio_time = time.time()
        self.conversation = []
        self.agent_id = None
        self.dynamic_data = None
        self.general_interviewer_prompt = general_interviewer_prompt
        self.final_prompt = None
        self.ws = None
        self.call_id = None
        self.start_time = None
        self.end_time = None
        self.session_handle = None  # Store the session resumption handle
        self.new_session_handle = None  # Store the new handle from SessionResumptionUpdate

    def set_call_id(self, call_id):
        self.call_id = call_id
        print(f"Call ID set to: {self.call_id}")

    def set_websocket(self, ws):
        self.ws = ws
    
    def set_start_time(self):
        """Set the start time of the call."""
        self.start_time = int(time.time())
        print(f"Call started at: {self.start_time}")
    
    def set_end_time(self):
        """Set the end time of the call."""
        self.end_time = int(time.time())
        print(f"Call ended at: {self.end_time}")
    
    def set_dynamic_data(self, dynamic_data):
        self.dynamic_data = dynamic_data

    def set_agent_id(self, agent_id):
        self.agent_id = agent_id

    def add_label(self, label, text):
        return f"{label}: {text}"
    
    async def save_conversation_and_timestamp(self):
        try:
            conversation_str = "\n".join(self.conversation)
            self.set_end_time()
            await call_service.add_transcript_and_timestamp(self.call_id, conversation_str, self.start_time, self.end_time)
            print(f"Conversation saved for call ID: {self.call_id}")
            self.conversation = []
        except Exception as e:
            print(f"Error saving conversation: {e}")

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
                            await self.out_queue.put({"data": pcm, "mime_type": "audio/pcm"})
                            self.last_audio_time = time.time()
                    elif 'text' in data:
                        json_data = json.loads(data['text'])
                        if json_data.get("action") == "end_call":
                            print("Received end_call action")
                            self.active = False
                            await self.save_conversation_and_timestamp()
                            await self.ws.close()
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
                await self.session.send_realtime_input(audio=msg)
        except Exception as e:
            print(f"Error in send_audio_to_gemini: {e}")
            self.active = False

    async def handle_function_call(self, function_call):
        try:
            function_name = function_call.name
            function_args = function_call.args if function_call.args else {}
            print(f"Function call received: {function_name} with args: {function_args}")
            
            if function_name == "end_interview_call":
                print("Ending interview call as requested by user")
                await self.ws.send_json({
                    "action": "interview_ended",
                    "message": "Interview has been ended. Thank you for your time!"
                })
                await self.session.send(
                    input="Interview ended successfully. Goodbye!",
                    end_of_turn=True
                )
                self.active = False
                return True
            return False
        except Exception as e:
            print(f"Error handling function call: {e}")
            return False

    async def receive_from_gemini(self):
        try:
            while self.active:
                turn = self.session.receive()
                ai_text = ""
                candidate_text = ""

                async for response in turn:
                    # Handle SessionResumptionUpdate
                    if hasattr(response, 'session_resumption_update') and response.session_resumption_update:
                        update = response.session_resumption_update
                        if update.resumable and update.new_handle:
                            self.new_session_handle = update.new_handle
                            print(f"Received new session handle: {self.new_session_handle}")
                    
                    # Handle GoAway message
                    if hasattr(response, 'go_away') and response.go_away is not None:
                        print(f"Received GoAway message. Time left: {response.go_away.time_left} seconds")
                        if self.new_session_handle:
                            print("Initiating session resumption...")
                            await self.resume_session()

                    # Handle function calls in different possible locations
                    function_call_found = False
                    
                    # Check in server_content.model_turn
                    if hasattr(response, 'server_content') and response.server_content:
                        if hasattr(response.server_content, 'model_turn') and response.server_content.model_turn:
                            if hasattr(response.server_content.model_turn, 'parts'):
                                for part in response.server_content.model_turn.parts:
                                    if hasattr(part, 'function_call') and part.function_call:
                                        print(f"Found function call in model_turn: {part.function_call}")
                                        function_ended = await self.handle_function_call(part.function_call)
                                        if function_ended:
                                            return
                                        function_call_found = True
                                    elif hasattr(part, 'executable_code'):
                                        print(f"Found executable_code part: {part.executable_code}")
                                        if hasattr(part.executable_code, 'code'):
                                            code_content = part.executable_code.code
                                            print(f"Executable code content: {code_content}")
                                            if "end_interview_call" in code_content:
                                                print("Detected end_interview_call in executable code")
                                                class MockFunctionCall:
                                                    def __init__(self):
                                                        self.name = "end_interview_call"
                                                        self.args = {}
                                                function_ended = await self.handle_function_call(MockFunctionCall())
                                                if function_ended:
                                                    return
                                                function_call_found = True
                    
                    # Check in direct candidates (fallback)
                    if not function_call_found and hasattr(response, 'candidates'):
                        for candidate in response.candidates:
                            if hasattr(candidate, 'content') and candidate.content:
                                if hasattr(candidate.content, 'parts'):
                                    for part in candidate.content.parts:
                                        print(f"Candidate part type: {type(part)}, attributes: {dir(part)}")
                                        if hasattr(part, 'function_call') and part.function_call:
                                            print(f"Found function call in candidates: {part.function_call}")
                                            function_ended = await self.handle_function_call(part.function_call)
                                            if function_ended:
                                                return
                                            function_call_found = True

                    # Handle audio data
                    if data := response.data:
                        self.audio_in_queue.put_nowait(data)

                    # Handle transcriptions
                    if hasattr(response, 'server_content') and response.server_content:
                        if response.server_content.output_transcription:
                            chunk = response.server_content.output_transcription.text or ""
                            ai_text += chunk
                            await self.ws.send_json({"ai_text": ai_text})

                        if response.server_content.input_transcription:
                            chunk = response.server_content.input_transcription.text or ""
                            candidate_text += chunk
                            await self.ws.send_json({"candidate_text": candidate_text})

                if ai_text.strip() or candidate_text.strip():
                    transcript_data = {}
                    if ai_text.strip():
                        transcript_data["ai_text"] = ai_text.strip()
                    if candidate_text.strip():
                        transcript_data["candidate_text"] = candidate_text.strip()
                    if candidate_text.strip():
                        self.conversation.append(self.add_label("User", candidate_text.strip()))
                    if ai_text.strip():
                        self.conversation.append(self.add_label("AI", ai_text.strip()))
                    print("Conversation:", self.conversation)

        except Exception as e:
            print(f"Error in receive_from_gemini: {e}")
            traceback.print_exc()
            self.active = False

    async def resume_session(self):
        """Resume the session with the new handle."""
        try:
            print(f"Resuming session with handle: {self.new_session_handle}")
            async with client.aio.live.connect(
                model=MODEL,
                config=types.LiveConnectConfig(
                    response_modalities=["AUDIO"],
                    session_resumption=types.SessionResumptionConfig(
                        handle=self.new_session_handle
                    ),
                ),
            ) as new_session:
                self.session = new_session
                self.session_handle = self.new_session_handle
                self.new_session_handle = None  # Reset for the next update
                print("Session resumed successfully.")
                # Restart tasks to continue the conversation
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.handle_websocket_messages())
                    tg.create_task(self.send_audio_to_gemini())
                    tg.create_task(self.receive_from_gemini())
                    tg.create_task(self.play_audio())
        except Exception as e:
            print(f"Error resuming session: {e}")
            self.active = False

    async def play_audio(self):
        while self.active:
            try:
                pcm = await self.audio_in_queue.get()
                msg = b'\x02' + pcm
                if self.active:
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
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError:
                json_data = {}
                print("Error: Could not parse dynamic_data as JSON. Using empty dict.")
        
        interviewer_personality = "professional, friendly, and encouraging"
        questions_data = json_data.get("questions", [])
        if isinstance(questions_data, str):
            try:
                questions_data = json.loads(questions_data)
            except json.JSONDecodeError:
                questions_data = []
                print("Error: Could not parse questions as JSON. Using empty list.")
        
        formatted_questions = ""
        for idx, q in enumerate(questions_data, 1):
            main_question = q.get("question", "")
            follow_ups = q.get("follow_ups", [])
            formatted_questions += f"{idx}. {main_question}\n"
            if follow_ups:
                formatted_questions += "   Follow-up questions:\n"
                for f_idx, follow_up in enumerate(follow_ups, 1):
                    formatted_questions += f"     {f_idx}. {follow_up}\n"
            formatted_questions += "\n"
        
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
            "{{questions}}": formatted_questions
        }
        
        final_prompt = self.general_interviewer_prompt
        for placeholder, value in replacements.items():
            final_prompt = final_prompt.replace(placeholder, str(value))
        
        tool_instruction = """
        IMPORTANT: You have access to an 'end_interview_call' function. Use this function ONLY when the candidate explicitly asks to end the interview or says they want to stop (e.g., "let's end the interview", "I want to stop now", "end the interview", "that's all for today"). Do not use this function for any other reason.
        """
        self.final_prompt = final_prompt + tool_instruction
        with open("final_prompt.txt", "w") as f:
            f.write(self.final_prompt)
    
    async def run(self):
        try:
            # Initial connection with session resumption enabled
            async with client.aio.live.connect(
                model=MODEL,
                config=types.LiveConnectConfig(
                    response_modalities=["AUDIO"],
                    session_resumption=types.SessionResumptionConfig(
                        handle=self.session_handle  # Use existing handle if resuming
                    ),
                ),
            ) as session:
                self.session = session
                print("Sending initial prompt to Gemini...")
                if not self.session_handle:  # Send prompt only for new sessions
                    await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                    print("Initial prompt sent.")
                self.set_start_time()
                
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.handle_websocket_messages())
                    tg.create_task(self.send_audio_to_gemini())
                    tg.create_task(self.receive_from_gemini())
                    tg.create_task(self.play_audio())
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
    call_id = ws.query_params.get("access_token", None)
    print("call_id is ", call_id)
    loop = AudioLoop()
    loop.set_websocket(ws)
    try:
        data = await ws.receive_text()
        data = json.loads(data)
        print(f"Received initial data: {data}")
        dynamic_data = data.get("dynamic_data", {})
        if not dynamic_data:
            raise ValueError("Dynamic data is required to start the interview.")
        print("data is ",data)
        loop.set_call_id(call_id)
        print(f"Call ID: {loop.call_id}")
        loop.set_dynamic_data(dynamic_data)
        loop.set_agent_id(agent_id)
        
        loop.create_final_prompt()
        await loop.run()
    except WebSocketDisconnect:
        loop.active = False
        try:
            await loop.save_conversation_and_timestamp()
            await ws.close()
        except:
            pass
    except Exception as e:
        print(f"Error: {e}")
        print(traceback.format_exc())
        loop.active = False
        try:
            await ws.close()
        except:
            pass

@app.get("/")
async def root():
    return {"message": "WebSocket server is running. Connect to /ws/audio for audio processing."}

@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    logger.info("Connected to MongoDB on startup.")

@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()
    logger.info("Disconnected from MongoDB on shutdown.")

class RegisterCallRequest(BaseModel):
    interviewer_id: str
    dynamic_data: dict
    interview_id: str
    
@app.post("/api/register")
async def register_call(request: RegisterCallRequest):
    interviewer_id = request.interviewer_id
    dynamic_data = request.dynamic_data
    interview_id = request.interview_id
    call_id = f"call_{interview_id}_{int(time.time())}"
    call_data = {
        "call_id": call_id,
        "dynamic_data": dynamic_data,
        "interviewer_id": interviewer_id,
        "interview_id": interview_id,
        "call_analysis": {
            "call_successful": True,
            "call_duration": 0
        }
    }
    await call_service.save_call(call_data)
    logger.info(f"Call registered with ID: {call_id} for interviewer: {interviewer_id}")
    print(call_service.get_call(call_id))
    
    return {"message": "Call registered successfully.", "call_id": call_id, "access_token": call_id}

@app.get("/api/call/{call_id}")
async def get_call(call_id: str):
    call = await call_service.get_call(call_id)
    if not call:
        return {"message": "Call not found."}
    return call

@app.get("/api/calls")
async def get_all_calls():
    calls = await call_service.get_all_calls()
    return {"calls": calls}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)