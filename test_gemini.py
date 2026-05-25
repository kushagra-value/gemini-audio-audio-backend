import os
import asyncio
import websockets
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

async def test_gemini_handshake():
    url = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not found in .env")
        return

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
    }

    try:
        async with websockets.connect(url, additional_headers=headers) as ws:
            print("Connected to Gemini WebSocket.")
            
            setup_message = {
                "setup": {
                    "model": "models/gemini-3.1-flash-live-preview",
                    "generation_config": {
                        "response_modalities": ["AUDIO"]
                    }
                }
            }
            await ws.send(json.dumps(setup_message))
            
            response = json.loads(await ws.recv())
            print(f"Received: {response}")
            
            if "setupComplete" in response:
                print("SUCCESS! setupComplete received.")
                return
            else:
                print("Failed to receive setupComplete.")
                
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_gemini_handshake())
