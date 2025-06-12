"""
Sample script to quickly test `run` and `resume_session` logic with dummy tasks.
"""
import asyncio
import types

class DummyClient:
    class aio:
        class live:
            @staticmethod
            def connect(model, config):
                # Return an async context manager stub directly
                class CM:
                    async def __aenter__(self):
                        print("[DummyClient] Connected")
                        return self

                    async def __aexit__(self, exc_type, exc, tb):
                        print("[DummyClient] Disconnected")
                return CM()

class AudioLoop:
    def __init__(self):
        self.session = None
        self.tasks = []
        self.session_handle = "dummy-handle"
        # Dummy config and client
        global CONFIG, MODEL, client
        CONFIG = {}
        MODEL = "dummy-model"
        client = DummyClient()
        self.final_prompt = "Hello, Gemini!"
        self.active = True

    def set_start_time(self):
        print("[AudioLoop] Start time set.")

    async def handle_websocket_messages(self):
        try:
            while True:
                print("[Task] Handling websocket messages...")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            print("[Task] handle_websocket_messages cancelled")

    async def send_audio_to_gemini(self):
        try:
            while True:
                print("[Task] Sending audio to Gemini...")
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            print("[Task] send_audio_to_gemini cancelled")

    async def receive_from_gemini(self):
        try:
            while True:
                print("[Task] Receiving from Gemini...")
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            print("[Task] receive_from_gemini cancelled")

    async def play_audio(self):
        try:
            while True:
                print("[Task] Playing audio...")
                await asyncio.sleep(2.5)
        except asyncio.CancelledError:
            print("[Task] play_audio cancelled")

    async def resume_session(self):
        """Resume the session if it was interrupted."""
        try:
            if self.tasks:
                print("Cancelling previous session tasks...")
                for task in self.tasks:
                    if not task.done():
                        try:
                            task.cancel()
                        except RecursionError:
                            print("Skipped cancelling a task due to recursion depth")
                await asyncio.gather(*self.tasks, return_exceptions=True)
                self.tasks = []

            CONFIG["session_resumption"] = types.SimpleNamespace(handle=self.session_handle)
            print("Resuming session with config:", CONFIG)

            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                self.set_start_time()
                self.tasks = [
                    asyncio.create_task(self.handle_websocket_messages()),
                    asyncio.create_task(self.send_audio_to_gemini()),
                    asyncio.create_task(self.receive_from_gemini()),
                    asyncio.create_task(self.play_audio()),
                ]
                # Run a short resume test
                await asyncio.sleep(5)
        except Exception as e:
            print(f"Error in resume_session: {e}")

    async def run(self):
        try:
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                print("Sending initial prompt to Gemini...")
                await asyncio.sleep(0.5)
                print("Initial prompt sent.")
                self.set_start_time()
                self.tasks = [
                    asyncio.create_task(self.handle_websocket_messages()),
                    asyncio.create_task(self.send_audio_to_gemini()),
                    asyncio.create_task(self.receive_from_gemini()),
                    asyncio.create_task(self.play_audio()),
                ]
                # Run for a short period
                await asyncio.sleep(5)
        except Exception as e:
            print(f"Error in run: {e}")
        finally:
            print("Cleaning up tasks...")
            for task in self.tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            print("Cleanup complete.")

async def main():
    loop = AudioLoop()
    # First run
    print("=== Running first session ===")
    await loop.run()
    # Resume session
    print("=== Resuming session ===")
    await loop.resume_session()

if __name__ == "__main__":
    asyncio.run(main())
