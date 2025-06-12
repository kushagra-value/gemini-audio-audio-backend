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
from websockets.exceptions import ConnectionClosedError


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
        self.max_retries = 5
        self.retry_delay = 5  # seconds
        self.connection_timeout = 30  # seconds
        self.last_heartbeat = time.time()
        self.context_restoration_enabled = True 
        self.reconnecting = False  # Flag to prevent multiple reconnection attempts
        self.critical_error = False  # Flag for non-recoverable errors
        
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
            # convert conversation to a string
            conversation_str = "\n".join(self.conversation)
            self.set_end_time()  # Set end time when saving conversation
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
                            # print(f"Received mic audio: {len(pcm)} bytes")
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
        """Enhanced send method with robust error handling and retry logic"""
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        while self.active and not self.critical_error:
            try:
                # Wait for audio data with timeout
                msg = await asyncio.wait_for(self.out_queue.get(), timeout=1.0)
                
                # Check if session exists and is healthy
                if not self.session:
                    print("Session not available, attempting reconnect...")
                    if not await self.safe_reconnect():
                        await asyncio.sleep(1)
                        continue
                
                # Send audio to session
                await self.session.send_realtime_input(audio=msg)
                consecutive_failures = 0  # Reset on successful send
                
            except asyncio.TimeoutError:
                # No audio to send, this is normal - continue without incrementing failures
                continue
                
            except (ConnectionClosedError, OSError, ConnectionResetError, Exception) as e:
                consecutive_failures += 1
                print(f"Connection error in send_audio_to_gemini (failure {consecutive_failures}/{max_consecutive_failures}): {e}")
                
                # Don't give up immediately - try to reconnect
                if consecutive_failures >= max_consecutive_failures:
                    print(f"Too many consecutive failures in send_audio_to_gemini. Attempting reconnection...")
                    if await self.safe_reconnect():
                        consecutive_failures = 0  # Reset counter after successful reconnect
                    else:
                        print("Failed to reconnect in send_audio_to_gemini. Waiting before retry...")
                        await asyncio.sleep(5)  # Wait longer before next attempt
                else:
                    # Short wait before retry for temporary issues
                    await asyncio.sleep(1)
        
        print("send_audio_to_gemini ended")


    async def handle_function_call(self, function_call):
        """Handle function calls from Gemini"""
        try:
            function_name = function_call.name
            function_args = function_call.args if function_call.args else {}
            
            print(f"Function call received: {function_name} with args: {function_args}")
            
            if function_name == "end_interview_call":
                print("Ending interview call as requested by user")
                
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
                
                # Set active to False to stop all loops
                self.active = False
                
                # Close the websocket
                # await self.ws.close()
                
                return True
            
            return False
            
        except Exception as e:
            print(f"Error handling function call: {e}")
            return False

    async def reconnect_session(self):
        """Attempt to reconnect to Gemini API with exponential backoff"""
        for attempt in range(self.max_retries):
            try:
                print(f"Attempting to reconnect... (attempt {attempt + 1}/{self.max_retries})")
                
                # Wait with exponential backoff
                if attempt > 0:
                    wait_time = self.retry_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(wait_time)
                
                # Create new session
                async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                    self.session = session
                    print("Reconnection successful!")
                    
                    # Resend the initial prompt
                    await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                    return True
                    
            except Exception as e:
                print(f"Reconnection attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    print("Max reconnection attempts reached. Giving up.")
                    return False
        
        return False

    async def receive_from_gemini(self):
            """Enhanced receive method with better error handling"""
            consecutive_failures = 0
            max_consecutive_failures = 3
            
            while self.active and not self.critical_error:
                try:
                    if not self.session:
                        print("Session not available in receive_from_gemini, attempting reconnect...")
                        if not await self.safe_reconnect():
                            await asyncio.sleep(1)
                            continue
                    
                    turn = self.session.receive()
                    ai_text = ""
                    candidate_text = ""

                    async for response in turn:
                        consecutive_failures = 0  # Reset on successful receive
                        self.last_heartbeat = time.time()
                        
                        # Handle function calls
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
                                if self.active:  # Check before sending
                                    try:
                                        await self.ws.send_json({"ai_text": ai_text})
                                    except Exception as ws_e:
                                        print(f"WebSocket send error: {ws_e}")

                            if response.server_content.input_transcription:
                                chunk = response.server_content.input_transcription.text or ""
                                candidate_text += chunk
                                if self.active:  # Check before sending
                                    try:
                                        await self.ws.send_json({"candidate_text": candidate_text})
                                    except Exception as ws_e:
                                        print(f"WebSocket send error: {ws_e}")

                    # Process conversation after receiving full turn
                    if ai_text.strip() or candidate_text.strip():
                        if candidate_text.strip():
                            self.conversation.append(self.add_label("User", candidate_text.strip()))
                        if ai_text.strip():
                            self.conversation.append(self.add_label("AI", ai_text.strip()))
                        print("Conversation:", self.conversation)

                except (ConnectionClosedError, OSError, ConnectionResetError) as e:
                    consecutive_failures += 1
                    print(f"Connection error in receive_from_gemini (failure {consecutive_failures}/{max_consecutive_failures}): {e}")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        print("Too many consecutive failures in receive_from_gemini. Attempting reconnection...")
                        if await self.safe_reconnect():
                            consecutive_failures = 0
                        else:
                            print("Failed to reconnect in receive_from_gemini. Waiting before retry...")
                            await asyncio.sleep(5)
                    else:
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    print(f"Unexpected error in receive_from_gemini: {e}")
                    traceback.print_exc()
                    consecutive_failures += 1
                    
                    if consecutive_failures >= max_consecutive_failures:
                        if not await self.safe_reconnect():
                            await asyncio.sleep(5)
                        consecutive_failures = 0
                    else:
                        await asyncio.sleep(1)
                        
    async def safe_reconnect(self):
        """Thread-safe reconnection method"""
        if self.reconnecting or self.critical_error:
            return False
        
        self.reconnecting = True
        try:
            success = await self.reconnect_session()
            return success
        finally:
            self.reconnecting = False

    async def play_audio(self):
        """Enhanced play_audio with better error handling"""
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        while self.active and not self.critical_error:
            try:
                pcm = await self.audio_in_queue.get()
                msg = b'\x02' + pcm
                
                if self.active:  # Double-check before sending
                    await self.ws.send_bytes(msg)
                    consecutive_failures = 0  # Reset on success
                    
            except WebSocketDisconnect:
                print("WebSocket disconnected in play_audio")
                self.active = False
                break
            except Exception as e:
                consecutive_failures += 1
                print(f"Error in play_audio (failure {consecutive_failures}/{max_consecutive_failures}): {e}")
                
                if consecutive_failures >= max_consecutive_failures:
                    print("Too many consecutive failures in play_audio")
                    self.active = False
                    break
                else:
                    await asyncio.sleep(0.1)  # Short wait before retry
            
            
    async def connection_monitor(self):
        """Enhanced connection monitor with better health checks"""
        while self.active and not self.critical_error:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                
                if not self.session or self.reconnecting:
                    continue
                
                # Check if connection is stale
                time_since_heartbeat = time.time() - self.last_heartbeat
                if time_since_heartbeat > self.connection_timeout:
                    print(f"Connection appears stale (no activity for {time_since_heartbeat:.1f}s)")
                    
                    # Try a lightweight health check
                    try:
                        await asyncio.wait_for(
                            self.session.send(input=".", end_of_turn=True),
                            timeout=10
                        )
                        self.last_heartbeat = time.time()
                        print("Connection health check passed")
                    except Exception as e:
                        print(f"Connection health check failed: {e}")
                        if not await self.safe_reconnect():
                            print("Failed to recover connection in monitor")
                            await asyncio.sleep(10)  # Wait before next check
                            
            except Exception as e:
                print(f"Error in connection_monitor: {e}")
                await asyncio.sleep(10)
  
            
    async def reconnect_session(self):
        """Enhanced reconnection with exponential backoff and better error handling"""
        print("Starting reconnection process...")
        
        for attempt in range(self.max_retries):
            if not self.active or self.critical_error:
                print("Connection cancelled or critical error occurred")
                return False
                
            try:
                print(f"Reconnection attempt {attempt + 1}/{self.max_retries}")
                
                # Progressive backoff
                if attempt > 0:
                    wait_time = min(self.retry_delay * (2 ** (attempt - 1)), 30)  # Max 30 seconds
                    print(f"Waiting {wait_time} seconds before retry...")
                    await asyncio.sleep(wait_time)
                
                # Close existing session if any
                if self.session:
                    try:
                        # Don't await close as it might hang
                        self.session = None
                    except:
                        pass
                
                # Create new session with timeout
                print("Creating new Gemini session...")
                async with asyncio.timeout(30):  # 30 second timeout for connection
                    session = await client.aio.live.connect(model=MODEL, config=CONFIG).__aenter__()
                    self.session = session
                    print("New session created successfully!")
                
                # Send context restoration
                context_message = self.build_context_restoration_message()
                await asyncio.wait_for(
                    self.session.send(input=context_message, end_of_turn=True),
                    timeout=15  # 15 second timeout for sending
                )
                print("Context restoration message sent successfully")
                
                # Update heartbeat and return success
                self.last_heartbeat = time.time()
                print(f"Reconnection successful after {attempt + 1} attempts")
                return True
                
            except asyncio.TimeoutError:
                print(f"Reconnection attempt {attempt + 1} timed out")
            except Exception as e:
                print(f"Reconnection attempt {attempt + 1} failed with error: {e}")
                
            # Don't sleep after the last attempt
            if attempt == self.max_retries - 1:
                print("All reconnection attempts exhausted")
        
        print("Failed to reconnect after all attempts")
        return False

    def build_context_restoration_message(self):
        """Build context restoration with intelligent conversation summarization"""
        
        context_parts = [self.final_prompt]
        
        if self.conversation:
            # Check if conversation is getting too long (Gemini has token limits)
            conversation_text = "\n".join(self.conversation)
            
            if len(conversation_text) > 2000:  # If conversation is long
                # Use summarized version
                context_parts.append("\n\n--- CONVERSATION SUMMARY (for context restoration) ---")
                context_parts.append(self.get_conversation_summary())
                context_parts.append("\n--- END OF CONVERSATION SUMMARY ---")
                
                context_parts.append(
                    "\nNote: This is a summary of our conversation so far due to reconnection. "
                    "Please continue the interview naturally from where we left off, "
                    "taking into account what has already been discussed."
                )
            else:
                # Use full conversation history
                context_parts.append("\n\n--- CONVERSATION HISTORY (for context restoration) ---")
                context_parts.append(conversation_text)
                context_parts.append("\n--- END OF CONVERSATION HISTORY ---")
                
                context_parts.append(
                    "\nPlease continue the interview naturally from where we left off. "
                    "Do not repeat questions that have already been asked."
                )
        
        return "".join(context_parts)


    def get_conversation_summary(self, max_length=1500):
        """Get a summarized version of the conversation if it's too long"""
        if not self.conversation:
            return ""
        
        full_conversation = "\n".join(self.conversation)
        
        # If conversation is short enough, return as is
        if len(full_conversation) <= max_length:
            return full_conversation
        
        # Otherwise, create a summary
        recent_exchanges = self.conversation[-10:]  # Last 10 exchanges
        summary_parts = [
            "--- CONVERSATION SUMMARY ---",
            f"Total exchanges so far: {len(self.conversation)}",
            "",
            "Recent conversation:",
            "\n".join(recent_exchanges),
            "",
            "--- END SUMMARY ---"
        ]
        
        return "\n".join(summary_parts)

    # Alternative method for very long conversations
    # def build_smart_context_restoration_message(self):
    #     """Build context restoration with intelligent conversation summarization"""
        
    #     context_parts = [self.final_prompt]
        
    #     if self.conversation:
    #         # Check if conversation is getting too long (Gemini has token limits)
    #         conversation_text = "\n".join(self.conversation)
            
    #         if len(conversation_text) > 2000:  # If conversation is long
    #             # Use summarized version
    #             context_parts.append("\n\n--- CONVERSATION SUMMARY (for context restoration) ---")
    #             context_parts.append(self.get_conversation_summary())
    #             context_parts.append("\n--- END OF CONVERSATION SUMMARY ---")
                
    #             context_parts.append(
    #                 "\nNote: This is a summary of our conversation so far due to reconnection. "
    #                 "Please continue the interview naturally from where we left off, "
    #                 "taking into account what has already been discussed."
    #             )
    #         else:
    #             # Use full conversation history
    #             context_parts.append("\n\n--- CONVERSATION HISTORY (for context restoration) ---")
    #             context_parts.append(conversation_text)
    #             context_parts.append("\n--- END OF CONVERSATION HISTORY ---")
                
    #             context_parts.append(
    #                 "\nPlease continue the interview naturally from where we left off. "
    #                 "Do not repeat questions that have already been asked."
    #             )
        
    #     return "".join(context_parts)

    # Enhanced method to track conversation state
    def get_interview_progress_summary(self):
        """Analyze conversation to understand interview progress"""
        if not self.conversation:
            return "Interview just started"
        
        # Count questions asked
        ai_messages = [msg for msg in self.conversation if msg.startswith("AI:")]
        user_responses = [msg for msg in self.conversation if msg.startswith("User:")]
        
        # Simple analysis
        questions_asked = len([msg for msg in ai_messages if "?" in msg])
        
        progress_info = {
            "total_exchanges": len(self.conversation),
            "questions_asked": questions_asked,
            "user_responses": len(user_responses),
            "last_speaker": "AI" if self.conversation[-1].startswith("AI:") else "User"
        }
        
        return progress_info

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
        # Initialize json_data
        json_data = self.dynamic_data or {}
        
        # If dynamic_data is a string, parse it as JSON
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError:
                json_data = {}
                print("Error: Could not parse dynamic_data as JSON. Using empty dict.")
        
        interviewer_personality = "professional, friendly, and encouraging"
        
        # Extract and parse questions
        questions_data = json_data.get("questions", [])
        if isinstance(questions_data, str):
            try:
                questions_data = json.loads(questions_data)
            except json.JSONDecodeError:
                questions_data = []
                print("Error: Could not parse questions as JSON. Using empty list.")
        
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
        
        # Define replacements for the prompt
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
        
        # Replace placeholders in the general prompt
        final_prompt = self.general_interviewer_prompt
        for placeholder, value in replacements.items():
            final_prompt = final_prompt.replace(placeholder, str(value))
        
        # Add tool usage instructions to the prompt
        tool_instruction = """
        
        IMPORTANT: You have access to an 'end_interview_call' function. Use this function ONLY when the candidate explicitly asks to end the interview or says they want to stop (e.g., "let's end the interview", "I want to stop now", "end the interview", "that's all for today"). Do not use this function for any other reason.
        """
        
        self.final_prompt = final_prompt + tool_instruction
        
        # Write the final prompt to a file for debugging
        with open("final_prompt.txt", "w") as f:
            f.write(self.final_prompt)
    
    async def run(self):
        """Enhanced run method with individual task management"""
        try:
            # Initial connection
            print("Establishing initial connection to Gemini...")
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                print("Sending initial prompt to Gemini...")
                await self.session.send(input=f"{self.final_prompt}", end_of_turn=True)
                print("Initial prompt sent.")
                self.set_start_time()
                self.last_heartbeat = time.time()
                
                # Create tasks individually to avoid TaskGroup cancellation issues
                tasks = []
                try:
                    task1 = asyncio.create_task(self.handle_websocket_messages())
                    task2 = asyncio.create_task(self.send_audio_to_gemini())
                    task3 = asyncio.create_task(self.receive_from_gemini())
                    task4 = asyncio.create_task(self.play_audio())
                    task5 = asyncio.create_task(self.connection_monitor())
                    
                    tasks = [task1, task2, task3, task4, task5]
                    
                    # Wait for any task to complete (which usually means an error or end condition)
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    
                    # Cancel remaining tasks
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    
                    # Check if any task raised an exception
                    for task in done:
                        try:
                            await task
                        except Exception as e:
                            print(f"Task completed with error: {e}")
                    
                except Exception as e:
                    print(f"Error managing tasks: {e}")
                    # Cancel all tasks
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                    
        except Exception as e:
            print(f"Error in AudioLoop run: {e}")
            traceback.print_exc()
        finally:
            self.active = False
            
            # Save conversation before closing
            try:
                await self.save_conversation_and_timestamp()
            except Exception as e:
                print(f"Error saving conversation on cleanup: {e}")
            
            print("AudioLoop finished")

@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, agent_id: str = "default-agent"):
    await ws.accept()
    call_id = ws.query_params.get("access_token", None)
    print("call_id is ", call_id)
    loop = AudioLoop()
    loop.set_websocket(ws)
    try:
        # ws.client.settimeout(30)  # 30 second timeout

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
            loop.save_conversation_and_timestamp()
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
    """Get all calls from the database"""
    # TypeError: object list can't be used in 'await' expression
    calls = await call_service.get_all_calls()
    return {"calls": calls}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)