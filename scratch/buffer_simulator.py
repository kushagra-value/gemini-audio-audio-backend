import asyncio
import time
import random
from collections import deque

class JitterSimulator:
    def __init__(self, chunks_per_sec=50, jitter_ms=10, loss_rate=0.0):
        self.interval = 1.0 / chunks_per_sec
        self.jitter_ms = jitter_ms
        self.loss_rate = loss_rate

    async def stream_audio(self, queue):
        chunk_id = 0
        while True:
            # Simulate packet loss
            if random.random() < self.loss_rate:
                chunk_id += 1
                continue

            # Simulate jitter
            jitter = random.uniform(-self.jitter_ms / 1000, self.jitter_ms / 1000)
            await asyncio.sleep(max(0, self.interval + jitter))

            # Simulate PCM audio chunk (e.g., 20ms of audio)
            audio_data = b'\x00' * 1024 
            await queue.put({"id": chunk_id, "data": audio_data, "timestamp": time.time()})
            chunk_id += 1

class SlidingWindowBuffer:
    def __init__(self, target_window_sec=0.1):
        self.buffer = deque()
        self.target_window_sec = target_window_sec
        self.underruns = 0
        self.overflows = 0

    def add_chunk(self, chunk):
        self.buffer.append(chunk)
        # Simple backpressure/overflow handling
        if len(self.buffer) > 100: # Max 2 seconds of buffer
            self.buffer.popleft()
            self.overflows += 1

    def get_chunk(self):
        if not self.buffer:
            self.underruns += 1
            return None
        return self.buffer.popleft()

    def get_metrics(self):
        return {
            "buffer_size": len(self.buffer),
            "underruns": self.underruns,
            "overflows": self.overflows
        }
