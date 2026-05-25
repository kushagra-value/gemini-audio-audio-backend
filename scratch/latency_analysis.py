import asyncio
import time
from buffer_simulator import JitterSimulator, SlidingWindowBuffer

async def run_test(buffer_size_ms, jitter_ms, loss_rate):
    queue = asyncio.Queue()
    simulator = JitterSimulator(chunks_per_sec=50, jitter_ms=jitter_ms, loss_rate=loss_rate)
    buffer = SlidingWindowBuffer(target_window_sec=buffer_size_ms / 1000)

    # Run simulator
    sim_task = asyncio.create_task(simulator.stream_audio(queue))

    start_time = time.time()
    latencies = []
    
    # Process for 5 seconds
    while time.time() - start_time < 5:
        try:
            # Simulate receiving from WS
            chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
            buffer.add_chunk(chunk)
            
            # Simulate processing time (e.g., 20ms)
            await asyncio.sleep(0.02)
            
            # Simulate processing/playing
            processed_chunk = buffer.get_chunk()
            if processed_chunk:
                latencies.append(time.time() - processed_chunk["timestamp"])
                
        except asyncio.TimeoutError:
            pass

    sim_task.cancel()
    
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    metrics = buffer.get_metrics()
    
    return {
        "buffer_size": buffer_size_ms,
        "avg_latency": avg_latency * 1000,
        "underruns": metrics["underruns"]
    }

async def main():
    configs = [50, 100, 200]
    print(f"{'Buffer (ms)':<15} | {'Avg Latency (ms)':<20} | {'Underruns':<10}")
    print("-" * 50)
    for b in configs:
        result = await run_test(b, jitter_ms=50, loss_rate=0.02)
        print(f"{result['buffer_size']:<15} | {result['avg_latency']:<20.2f} | {result['underruns']:<10}")

if __name__ == "__main__":
    asyncio.run(main())
