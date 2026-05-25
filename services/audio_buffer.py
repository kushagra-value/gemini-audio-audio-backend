import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)

class AdaptiveBuffer:
    def __init__(self, name="Buffer", target_delay_ms=100, max_delay_ms=500, chunk_duration_ms=20):
        self.name = name
        self.queue = deque()
        self.target_delay_ms = target_delay_ms
        self.max_delay_ms = max_delay_ms
        self.chunk_duration_ms = chunk_duration_ms
        
        self.last_arrival_time = None
        self.jitter_ema = 0.0
        self.alpha = 0.1  # Smoothing factor for jitter EMA
        
        self.condition = asyncio.Condition()
        self.active = True

    def add_chunk(self, chunk):
        now = time.time()
        if self.last_arrival_time is not None:
            arrival_interval = (now - self.last_arrival_time) * 1000
            # Jitter is the variance in arrival intervals
            jitter = abs(arrival_interval - self.chunk_duration_ms)
            self.jitter_ema = (self.alpha * jitter) + (1 - self.alpha) * self.jitter_ema
            
            # Dynamically adjust target delay based on jitter
            # Target delay = Base delay + 2x Jitter (safety margin)
            new_target = 60 + (2 * self.jitter_ema) 
            self.target_delay_ms = min(max(60, new_target), self.max_delay_ms)
            
        self.last_arrival_time = now
        self.queue.append((now, chunk))
        
        # Fast-forward logic: if buffer is too large, drop oldest chunks
        current_buffer_ms = len(self.queue) * self.chunk_duration_ms
        if current_buffer_ms > self.max_delay_ms:
            # logger.warning(f"[{self.name}] Buffer overflow ({current_buffer_ms}ms). Dropping chunks.")
            while len(self.queue) * self.chunk_duration_ms > self.target_delay_ms:
                self.queue.popleft()
        
        async def notify():
            async with self.condition:
                self.condition.notify_all()
        
        asyncio.create_task(notify())

    async def get_chunk(self):
        async with self.condition:
            while self.active:
                if not self.queue:
                    await self.condition.wait()
                    continue
                
                # Check if we've buffered enough to meet the target delay
                # or if the first chunk has been waiting long enough
                first_chunk_time, chunk_data = self.queue[0]
                wait_time = (time.time() - first_chunk_time) * 1000
                
                if wait_time >= self.target_delay_ms or len(self.queue) > 5:
                    return self.queue.popleft()[1]
                
                # Wait a bit more for the buffer to fill
                sleep_time = max(0.005, (self.target_delay_ms - wait_time) / 1000)
                await asyncio.sleep(sleep_time)
                
                if self.queue:
                    return self.queue.popleft()[1]
        return None

    def close(self):
        self.active = False
        # No way to easily wake up the condition wait from here without being in a loop
        # but setting active=False will stop subsequent gets.
