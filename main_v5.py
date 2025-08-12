from contextlib import asynccontextmanager
import logging
import os
import asyncio
import json
import traceback
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from websockets import ConnectionClosedOK
from config.config import CONFIG, MODEL, SEND_SR, RECV_SR, CHUNK, client, end_call_tool
from config.prompts import general_interviewer_prompt
from google import genai
from google.genai import types
from config.database import connect_to_mongo, close_mongo_connection, get_database
from services.call_service import call_service
app = FastAPI()

# # Ensure config aligns with frontend
# SEND_SR = 16000
# RECV_SR = 16000
# CHUNK = 1024  # Buffer size for audio chunks

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
        self.session_handle = None
        self.task_group = None
        self.tasks = []
        self.call_completed = False
        self.once=1
        
        
        
    def set_call_id(self, call_id):
        self.call_id = call_id
        logger.info(f"Call ID set to: {self.call_id}")

    def set_websocket(self, ws):
        self.ws = ws
    
    def set_start_time(self):
        """Set the start time of the call."""
        self.start_time = int(time.time())
        logger.info(f"Call started at: {self.start_time}")
    
    def set_end_time(self):
        """Set the end time of the call."""
        self.end_time = int(time.time())
        logger.info(f"Call ended at: {self.end_time}")
    
    def set_dynamic_data(self, dynamic_data):
        self.dynamic_data = dynamic_data

    def set_agent_id(self, agent_id):
        self.agent_id = agent_id

    def add_label(self, label, text):
        return f"{label}: {text}"
    
    async def save_conversation_and_timestamp(self):
        try:
            # convert conversation to a string
            conversation_str = "\n".join(self.conversation)
            self.set_end_time()  # Set end time when saving conversation
            await call_service.add_transcript_and_timestamp(self.call_id, conversation_str, self.start_time, self.end_time)
            logger.info(f"Conversation saved for call ID: {self.call_id}")
            self.conversation = []
            self.call_completed = True
        except Exception as e:
            logger.error(f"Error saving conversation: {e}")
  
   

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
                            # logger.info(f"Received mic audio: {len(pcm)} bytes")
                            await self.out_queue.put({"data": pcm, "mime_type": "audio/pcm"})
                            self.last_audio_time = time.time()
                    elif 'text' in data:
                        json_data = json.loads(data['text'])
                        # logger.info(f"Received JSON data: {json_data}")
                        if json_data.get("action") == "end_call":
                            logger.info("Received end_call action")
                            self.active = False
                            await self.save_conversation_and_timestamp()
                            await self.ws.close()
                            self.tasks[2].cancel()  # Cancel handle_websocket_messages
                            self.tasks[1].cancel()  # Cancel send_audio_to_gemini
                            self.tasks[3].cancel()  
                            asyncio.current_task().cancel()  # Cancel receive_from_gemini
                            break
                        text = json_data.get("input", "")
                        if text:
                            # logger.info(f"Received text input: {text}")
                            await self.session.send(input=text or ".", end_of_turn=True)
                    else:
                        logger.warning(f"Received unknown data type: {data}")
            except WebSocketDisconnect:
                self.active = False
                break
            except Exception as e:
                logger.error(f"Error in handle_websocket_messages: {e}")
                self.active = False
                break

    async def send_audio_to_gemini(self):
        try:
            while self.active:
                msg = await self.out_queue.get()
                # logger.info(f"Sending audio to Gemini: {len(msg['data'])} bytes")
                await self.session.send_realtime_input(audio=msg)
        except Exception as e:
            logger.error(f"Error in send_audio_to_gemini: {e}")
            self.active = False

    async def handle_function_call(self, function_call):
        """Handle function calls from Gemini"""
        try:
            function_name = function_call.name
            function_args = function_call.args if function_call.args else {}
            
            logger.info(f"Function call received: {function_name} with args: {function_args}")
            
            if function_name == "end_interview_call":
                logger.info("Ending interview call as requested by user")
                
                # Send a final message to the client
                await self.ws.send_json({
                    "action": "interview_ended",
                    "message": "Interview has been ended. Thank you for your time!"
                })
                
                # Send function response back to Gemini
                await self.session.send(
                    input="Interview ended successfully. Goodbye!",
                    end_of_turn=True
                )
                self.save_conversation_and_timestamp()
                self.tasks[0].cancel()  # Cancel handle_websocket_messages
                self.tasks[1].cancel()  # Cancel send_audio_to_gemini
                self.tasks[3].cancel()  
                asyncio.current_task().cancel()  # Cancel receive_from_gemini
                
                # Set active to False to stop all loops
                self.active = False

                
                # Close the websocket
                # await self.ws.close()
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error handling function call: {e}")
            return False

    async def receive_from_gemini(self):
        try:
            while self.active:
                turn = self.session.receive()
                ai_text = ""
                candidate_text = ""

                async for response in turn:
                    # Debug: Log the full response structure
                    # logger.info(f"Response type: {type(response)}")
                    # logger.info(f"Response attributes: {dir(response)}")
                    
                    # Handle function calls in different possible locations
                    function_call_found = False
                    if response.session_resumption_update:
                        logger.info(f"Session resumption update found: {response.session_resumption_update}")
                        update = response.session_resumption_update
                        if update.resumable and update.new_handle:
                            new_handle = update.new_handle
                            logger.info(f"Session resumable: {update.resumable}, new handle: {new_handle}")
                            self.session_handle = new_handle
                            logger.info(f"Session handle updated to: {self.session_handle}")
                    
                    if hasattr(response, 'go_away') and response.go_away is not None:
                        logger.info(f"Received GoAway message. Time left: {response.go_away.time_left} seconds")
                        # Start a new session with the new handle if available
                        if self.session_handle:
                            logger.info("Initiating session resumption...")
                            # self.active = False
                            # self.once+=1
                            # logger.info("once",self.once)
                            logger.info("Cancelling previous session tasks…")
                            self.tasks[0].cancel()  # Cancel handle_websocket_messages
                            self.tasks[1].cancel()  # Cancel send_audio_to_gemini
                            self.tasks[3].cancel()  
                            asyncio.current_task().cancel()  # Cancel receive_from_gemini
                            # await self.resume_session()  # Attempt to resume session if cancelled
                            # Give time for cancellation to propagate
                            # await asyncio.sleep(1)
                            # await asyncio.gather(*self.tasks, return_exceptions=True)
                           
                    
                            
                            
                    
                    # Check in server_content.model_turn
                    if hasattr(response, 'server_content') and response.server_content:
                        if hasattr(response.server_content, 'model_turn') and response.server_content.model_turn:
                            if hasattr(response.server_content.model_turn, 'parts'):
                                for part in response.server_content.model_turn.parts:
                                    # logger.info(f"Part type: {type(part)}, attributes: {dir(part)}")
                                    if hasattr(part, 'function_call') and part.function_call:
                                        logger.info(f"Found function call in model_turn: {part.function_call}")
                                        function_ended = await self.handle_function_call(part.function_call)
                                        if function_ended:
                                            return
                                        function_call_found = True
                                    elif hasattr(part, 'executable_code'):
                                        logger.info(f"Found executable_code part: {part.executable_code}")
                                        # Check if this is actually a function call disguised as executable_code
                                        if hasattr(part.executable_code, 'code'):
                                            code_content = part.executable_code.code
                                            logger.info(f"Executable code content: {code_content}")
                                            # Look for end_interview_call in the code
                                            if "end_interview_call" in code_content:
                                                logger.info("Detected end_interview_call in executable code")
                                                # Create a mock function call object
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
                                        logger.info(f"Candidate part type: {type(part)}, attributes: {dir(part)}")
                                        if hasattr(part, 'function_call') and part.function_call:
                                            logger.info(f"Found function call in candidates: {part.function_call}")
                                            function_ended = await self.handle_function_call(part.function_call)
                                            if function_ended:
                                                return
                                            function_call_found = True

                    # Handle audio data
                    if data := response.data:
                        # logger.info(f"Received audio data from Gemini: {len(data)} bytes")
                        self.audio_in_queue.put_nowait(data)
                        # logger.info(f"Audio in queue size: {self.audio_in_queue.qsize()}")

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
                    
                    
                    # Only send if websocket is still active
                    # if self.active:
                    #     await self.ws.send_json(transcript_data)

                    if candidate_text.strip():
                        self.conversation.append(self.add_label("User", candidate_text.strip()))
                    if ai_text.strip():
                        self.conversation.append(self.add_label("AI", ai_text.strip()))
                    logger.info(f"Conversation: {self.conversation}")

        
        
        except ConnectionClosedOK:
            # Normal shutdown: nothing to do here
            logger.info("receive_from_gemini: connection closed normally")
        except asyncio.CancelledError:
            # Task was cancelled: clean up if needed
            
            logger.info("receive_from_gemini cancelled")
            
        except Exception as e:
            # Other errors you might still want to log
            logger.error(f"Error in receive_from_gemini: {e}")


    async def play_audio(self):
        while self.active:
            try:
                pcm = await self.audio_in_queue.get()
                msg = b'\x02' + pcm
                # logger.info(f"Playing audio chunk of size: {len(msg)} bytes")
                if self.active:  # Check if still active before sending
                    await self.ws.send_bytes(msg)
            except Exception as e:
                logger.error(f"Error in play_audio: {e}")
                self.active = False
                break

    async def monitor_silence(self):
        try:
            while self.active:
                await asyncio.sleep(0.5)
                time_since_last_audio = time.time() - self.last_audio_time
                if time_since_last_audio > 2.0:
                    logger.info("Detected 2 seconds of silence. Signaling end of speech.")
                    self.last_audio_time = time.time()
        except Exception as e:
            logger.error(f"Error in monitor_silence: {e}")
            self.active = False

    def create_final_prompt(self):
        # Initialize json_data
        json_data = self.dynamic_data or {}
        
        # If dynamic_data is a string, parse it as JSON
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError:
                json_data = {}
                logger.error("Error: Could not parse dynamic_data as JSON. Using empty dict.")
        
        interviewer_personality = "professional, friendly, and encouraging"
        
        # Extract and parse questions
        questions_data = json_data.get("questions", [])
        if isinstance(questions_data, str):
            try:
                questions_data = json.loads(questions_data)
            except json.JSONDecodeError:
                questions_data = []
                logger.error("Error: Could not parse questions as JSON. Using empty list.")
        
        # Format questions and follow-ups
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
        
        print(f"json_data: {json_data}")
        
        # Handle organization_data parsing
        org_data = {}
        if "organization_data" in json_data:
            if isinstance(json_data["organization_data"], str):
                try:
                    org_data = json.loads(json_data["organization_data"])
                except json.JSONDecodeError:
                    org_data = {}
                    logger.error("Error: Could not parse organization_data as JSON. Using empty dict.")
            elif isinstance(json_data["organization_data"], dict):
                org_data = json_data["organization_data"]
            else:
                org_data = {}
        
        # Log organization data for debugging
        logger.info(f"Organization data: {org_data}")
        
        # Helper function to safely get organization data with proper fallbacks
        def get_org_value(key, fallback=""):
            value = org_data.get(key, fallback)
            # Check if value is valid (not empty, not just whitespace, not placeholder syntax)
            if value and str(value).strip() and not str(value).startswith('{{') and not str(value).endswith('}}'):
                return str(value).strip()
            return fallback
        
        # Define replacements for the prompt with improved organization data handling
        replacements = {
            "{{role}}": str(json_data.get("role", "")),
            "{{mins}}": str(json_data.get("mins", "")),
            "{{name}}": str(json_data.get("name", "")),
            "{{objective}}": str(json_data.get("objective", "")),
            "{{questionFocus}}": str(json_data.get("questionFocus", "")),
            "{{description}}": str(json_data.get("description", "")),
            "{{interviewerName}}": str(json_data.get("interviewerName", "")),
            "{{interviewerPersonality}}": interviewer_personality,
            "{{candidateName}}": str(json_data.get("name", "")),
            "{{behavioralQuestions}}": str(json_data.get("behavioralQuestions", "")),
            "{{questions}}": formatted_questions,

            # Organization data with better handling
            "{{organization_data.name}}": get_org_value("name"),
            "{{org_name}}": get_org_value("name"),
            "{{org_industry}}": get_org_value("industry"),
            "{{org_company_size}}": get_org_value("company_size"),
            "{{org_company_type}}": get_org_value("company_type"),
            "{{org_website}}": get_org_value("website"),
            "{{org_address}}": get_org_value("address"),
            "{{org_values}}": get_org_value("values"),
            "{{org_key_technologies}}": get_org_value("key_technologies"),
            
            # Additional organization data patterns that might appear in prompt
            "{{organization_data.industry}}": get_org_value("industry"),
            "{{organization_data.company_size}}": get_org_value("company_size"),
            "{{organization_data.company_type}}": get_org_value("company_type"),
            "{{organization_data.website}}": get_org_value("website"),
            "{{organization_data.address}}": get_org_value("address"),
            "{{organization_data.values}}": get_org_value("values"),
            "{{organization_data.key_technologies}}": get_org_value("key_technologies"),
        }
        
        # Replace placeholders in the general prompt
        final_prompt = self.general_interviewer_prompt
        for placeholder, value in replacements.items():
            if placeholder in final_prompt:  # Only replace if placeholder exists
                final_prompt = final_prompt.replace(placeholder, str(value))
                logger.info(f"Replaced {placeholder} with: {value}")
                
        # Add tool usage instructions to the prompt
        tool_instruction = """
        
        IMPORTANT: You have access to an 'end_interview_call' function. Use this function ONLY when the candidate explicitly asks to end the interview or says they want to stop (e.g., "let's end the interview", "I want to stop now", "end the interview", "that's all for today"). Do not use this function for any other reason.
        """
        
        self.final_prompt = final_prompt + tool_instruction
        
        # Write the final prompt to a file for debugging
        # with open("final_prompt.txt", "w", encoding="utf-8") as f:
        #     f.write(self.final_prompt)
            
    async def resume_session(self):
        """Resume the session if it was interrupted."""
        try:
            # 1) Close old session so the SDK stops its internal loops
            if self.session is not None:
                try:
                    await self.session.close()
                except Exception as e:
                    logger.error(f"Error closing old session: {e}")

            # 2) Cancel *only* your four background tasks
            # if self.tasks:
            #     logger.info("Cancelling previous session tasks…")
            #     for task in self.tasks:
            #         if not task.done():
            #             try:
            #                 task.cancel()
            #             except RecursionError:
            #                 pass
            #     await asyncio.gather(*self.tasks, return_exceptions=True)
            #     self.tasks = []

            # 3) Reconfigure and reconnect
            CONFIGR = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="puck"),
                    )
                ),
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        # start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
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
                tools=[end_call_tool],
                system_instruction=types.Content(parts=[types.Part(text="TRANSCRIBE ONLY IN ENGLISH, NO OTHER LANGUAGES")]),
                session_resumption=types.SessionResumptionConfig(
                    handle=self.session_handle
                )
            )

            # logger.info(f"Resuming session with config: {CONFIGR}")

            async with client.aio.live.connect(model=MODEL, config=CONFIGR) as session:
                self.session = session
                # await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                self.active = True
                t1 = asyncio.create_task(self.handle_websocket_messages())
                t2 = asyncio.create_task(self.send_audio_to_gemini())
                t3 = asyncio.create_task(self.receive_from_gemini())
                t4 = asyncio.create_task(self.play_audio())
                    # tg.create_task(self.monitor_silence())
                self.tasks = [t1, t2, t3, t4]
                for task in self.tasks:
                    await task
                    
                logger.info("Session resumed and initial prompt sent.")
             
        except asyncio.CancelledError:
            logger.warning("Session cancelled")
            while self.call_completed is False:
                await self.resume_session()
        except Exception as e:
            logger.error(f"Error in AudioLoop: {e}", exc_info=True)
        finally:
            self.active = False
            self.call_completed = True
            logger.info("AudioLoop finished")


    async def run(self):
        try:
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                logger.info("Sending initial prompt to Gemini...")
                await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                logger.info("Initial prompt sent.")
                self.set_start_time()  # Set start time when session starts
                
                
                t1 = asyncio.create_task(self.handle_websocket_messages())
                t2 = asyncio.create_task(self.send_audio_to_gemini())
                t3 = asyncio.create_task(self.receive_from_gemini())
                t4 = asyncio.create_task(self.play_audio())
                    # tg.create_task(self.monitor_silence())
                self.tasks = [t1, t2, t3, t4]
                for task in self.tasks:
                    await task
            
        except asyncio.CancelledError:
            logger.warning("Session cancelled")
            while self.call_completed is False:
                await self.resume_session()
        except Exception as e:
            logger.error(f"Error in AudioLoop: {e}", exc_info=True)
        finally:
            self.active = False
            logger.info("AudioLoop finished")
            
            
@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, agent_id: str = "default-agent"):
    await ws.accept()
    call_id = ws.query_params.get("access_token", None)
    logger.info(f"call_id is {call_id}")
    loop = AudioLoop()
    loop.set_websocket(ws)
    try:
        data = await ws.receive_text()
        data = json.loads(data)
        # logger.info(f"Received initial data: {data}")
        dynamic_data = data.get("dynamic_data", {})
        if not dynamic_data:
            raise ValueError("Dynamic data is required to start the interview.")
        # logger.info(f"data is {data}")
        loop.set_call_id(call_id)
        logger.info(f"Call ID: {loop.call_id}")
        loop.set_dynamic_data(dynamic_data)
        loop.set_agent_id(agent_id)
        
        loop.create_final_prompt()
        await loop.run()
    except WebSocketDisconnect:
        loop.active = False
        try:
            loop.save_conversation_and_timestamp()
            await ws.close()
        except:
            pass
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.error("An unhandled exception occurred in audio_ws", exc_info=True)
        loop.active = False
        try:
            await ws.close()
        except:
            pass
    finally:
        loop.active = False
        if loop.session:
            await loop.session.close()
        for i in loop.tasks:
            i.cancel()




@app.get("/")
async def root():
    return {"message": "WebSocket server is running. Connect to /ws/audio for audio processing."}


#connect to mongo on startup
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
    #generate a call_id based on current time
    
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
    # logger.info(call_service.get_call(call_id))
    
    return {"message": "Call registered successfully.", "call_id": call_id, "access_token": call_id}

@app.get("/api/call/{call_id}")
async def get_call(call_id: str):
    call = await call_service.get_call(call_id)
    
    if not call:
        return {"message": "Call not found."}
    return call

@app.get("/api/calls")
async def get_all_calls():
    """Get all calls from the database"""
    # TypeError: object list can't be used in 'await' expression
    calls = await call_service.get_all_calls()
    return {"calls": calls}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)