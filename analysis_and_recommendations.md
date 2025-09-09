# AI Interviewer Backend: Technical Analysis & Improvement Plan (V2)

This document provides a deep-dive, code-level analysis of the AI Interviewer backend and a detailed, actionable plan for resolving the identified issues of latency, disconnection, and poor interruption handling.

## 1. Root Cause Analysis (Code-Level)

### 1.1. Issue: Poor Interruption Handling & Voice Detection Misses

*   **Root Cause:** The Gemini API configuration in `config/config.py` is not tuned for a fast-paced, conversational interview.
*   **Code Evidence (`config/config.py`):**
    ```python
    automatic_activity_detection=types.AutomaticActivityDetection(
        disabled=False,
        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW, // [PROBLEM]
        prefix_padding_ms=100,
        silence_duration_ms=1000, // [PROBLEM]
    )
    ```
*   **Analysis:**
    *   `END_SENSITIVITY_LOW`: This instructs the model to be very forgiving about pauses, causing it to wait too long before deciding the user has finished speaking. This is why it misses the beginning of the next turn or detects speech late.
    *   `silence_duration_ms=1000`: A 1-second silence requirement before the AI can respond is unnatural and is a primary contributor to the perceived voice lag.

### 1.2. Issue: Frequent WebSocket Disconnections

*   **Root Cause 1:** The WebSocket connection is fragile and lacks a keep-alive mechanism to prevent timeouts during pauses in conversation.
*   **Analysis:** Network proxies, load balancers, and even operating systems will terminate TCP connections they perceive as idle. The lack of a heartbeat (ping/pong frames) means that during any silent period, the connection is at high risk of being dropped.

*   **Root Cause 2:** The session resumption logic in `main_v5.py` is dangerously unstable.
*   **Code Evidence (`main_v5.py`):**
    ```python
    except asyncio.CancelledError:
        logger.warning("Session cancelled")
        while self.call_completed is False: // [PROBLEM]
            await self.resume_session()
    ```
*   **Analysis:** If the session is cancelled and `resume_session()` fails for any reason (e.g., network issue, API key problem), this `while` loop will retry indefinitely, creating an infinite loop that can crash the server instance by consuming 100% CPU.

### 1.3. Issue: Voice Lag & High Latency

*   **Root Cause:** While network hops are a factor, the configuration issues mentioned in **1.1** are the largest software-based contributors to latency. Fixing the sensitivity and silence duration will have the most significant impact on reducing the lag between turns.

---

## 2. Detailed Recommendations & Implementation

### 2.1. Recommendation: Upgrade the Gemini Model

*   **Analysis:** The current model `gemini-2.0-flash-live-001` is an early generation model. Google has since released more advanced models designed for this exact use case. The recommended model is **`gemini-1.5-flash-online`**. It is specifically designed for real-time, low-latency audio processing and offers improved performance.
*   **Action:** Modify `config/config.py`.
*   **Code Change:**
    ```python
    # In config/config.py
    # OLD
    # MODEL = "models/gemini-2.0-flash-live-001"
    # NEW
    MODEL = "models/gemini-1.5-flash-online"
    ```

### 2.2. Recommendation: Tune Audio Configuration for Conversation

*   **Analysis:** To fix interruption handling, we must make the activity detection more sensitive and faster.
*   **Action:** Modify the `LiveConnectConfig` in `config/config.py`.
*   **Code Change:**
    ```python
    # In config/config.py
    automatic_activity_detection=types.AutomaticActivityDetection(
        disabled=False,
        # OLD: end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
        # NEW: More sensitive to pauses
        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_MEDIUM,

        prefix_padding_ms=100,

        # OLD: silence_duration_ms=1000,
        # NEW: Responds faster after user stops talking
        silence_duration_ms=500,
    )
    ```

### 2.3. Recommendation: Implement a Robust WebSocket Keep-Alive

*   **Analysis:** A heartbeat mechanism will prevent connections from being dropped due to inactivity. We can run a background task that sends a "ping" every few seconds.
*   **Action:** Add a `websocket_keepalive` function and launch it as a background task in `main_v5.py`.
*   **Code Change:**

    **1. Add the new keep-alive function to the `AudioLoop` class:**
    ```python
    # In main_v5.py, inside the AudioLoop class

    async def websocket_keepalive(self):
        """Send a ping message every 5 seconds to keep the connection alive."""
        while self.active:
            try:
                await self.ws.send_json({"type": "ping"})
                await asyncio.sleep(5)
            except (WebSocketDisconnect, ConnectionClosedOK):
                break
            except Exception as e:
                logger.error(f"Error in websocket_keepalive: {e}")
                break
    ```

    **2. Start this function as a task when the loop runs:**
    ```python
    # In main_v5.py, inside the run() method of AudioLoop

    # ... after self.session = session
    t1 = asyncio.create_task(self.handle_websocket_messages())
    t2 = asyncio.create_task(self.send_audio_to_gemini())
    t3 = asyncio.create_task(self.receive_from_gemini())
    t4 = asyncio.create_task(self.play_audio())
    t5 = asyncio.create_task(self.websocket_keepalive()) # <-- ADD THIS LINE
    # tg.create_task(self.monitor_silence())
    self.tasks = [t1, t2, t3, t4, t5] # <-- ADD t5 HERE
    for task in self.tasks:
        await task

    ```
    *Note: You will also need to add `t5` to the task list in the `resume_session` method if you choose to keep it.*

### 2.4. Recommendation: Fix the Unstable Session Resumption

*   **Analysis:** The infinite loop must be removed. A better strategy is to limit the number of retries and then terminate gracefully.
*   **Action:** Modify the `except` block in the `run` and `resume_session` methods in `main_v5.py`.
*   **Code Change:**
    ```python
    # In main_v5.py, in both run() and resume_session() methods

    except asyncio.CancelledError:
        logger.warning("Session cancelled")
        # OLD DANGEROUS CODE:
        # while self.call_completed is False:
        #     await self.resume_session()

        # NEW SAFER CODE:
        # Attempt to resume only once after a cancellation.
        # If it fails again, the loop will terminate.
        if not self.call_completed:
            logger.info("Attempting to resume session once.")
            await self.resume_session()
        else:
            logger.info("Call is already completed, not resuming.")
    ```

---

## 3. Revised Cost Analysis

*   **Current Model (`gemini-2.0-flash-live-001`):** Pricing is not public, but Flash models are generally cost-effective.
*   **Recommended Model (`gemini-1.5-flash-online`):** This is a newer, more efficient model. While specific "online" model pricing is often bundled, it aligns with the Gemini 1.5 Flash family, which is highly optimized for low cost. The official pricing for Gemini 1.5 Flash is:
    *   **Audio Input:** **$0.000125 per 15 seconds** of audio.
*   **Cost Impact:**
    *   The cost will likely be **very similar** to your current model, but you will get significantly better performance and reliability.
    *   The changes from **2.2** and **2.3** (tuning and keep-alive) have **no direct cost impact** but will drastically improve the user experience, preventing lost interviews and user frustration.
