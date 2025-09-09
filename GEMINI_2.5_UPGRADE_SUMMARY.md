# Gemini 2.5 Flash Preview Native Audio Dialog Upgrade

## Overview
This document summarizes the upgrade from `models/gemini-2.0-flash-live-001` to `models/gemini-2.5-flash-preview-native-audio-dialog` across both the backend (gemini-audio-audio-backend) and frontend (InterviewBot) codebases.

## Changes Made

### Backend Changes (`gemini-audio-audio-backend/`)

#### 1. Configuration Updates (`config/config.py`)
- **Model**: Updated from `models/gemini-2.0-flash-live-001` to `models/gemini-2.5-flash-preview-native-audio-dialog`
- **Sample Rates**: Changed `SEND_SR` from 48,000 to 16,000 Hz (matches frontend)
- **Client Configuration**: Updated `http_options` to use dictionary format `{"api_version": "v1beta"}`
- **LiveConnectConfig**: Completely rewritten with new Gemini 2.5 features:
  - Added `media_resolution="MEDIA_RESOLUTION_MEDIUM"`
  - Updated voice from "puck" to "Zephyr"
  - Added `turn_coverage="TURN_INCLUDES_ALL_INPUT"`
  - Added `context_window_compression` with sliding window (25600 trigger tokens, 12800 target tokens)

#### 2. Main Application Updates (`main_v5.py`)
- Updated session resumption configuration (`CONFIGR`) to match new model requirements
- Updated inline comments to reflect Gemini 2.5 integration

#### 3. Dependencies (`requirements.txt`)
- Updated `google-genai` to version `>=0.8.0` to ensure compatibility with new model

### Frontend Changes (`InterviewBot/src/lib/UseGeminiAudio.tsx`)

#### 1. Audio Processing Optimizations
- Improved `playPCMChunk` function with better audio clamping for quality enhancement
- Added explicit comments indicating optimization for Gemini 2.5
- Enhanced `float32ToInt16` conversion function documentation

## Key Features Enabled

### 1. Turn Coverage
- `TURN_INCLUDES_ALL_INPUT`: Ensures all audio input is included in processing turns

### 2. Affective Dialog
- The new model supports more natural, emotionally aware conversations through the "Zephyr" voice

### 3. Context Window Compression
- Automatic sliding window compression when conversation exceeds 25,600 tokens
- Maintains 12,800 tokens in active context for better memory management

### 4. Media Resolution
- Set to `MEDIA_RESOLUTION_MEDIUM` for optimal balance of quality and processing speed

## Audio Configuration

### Sample Rates
- **Send (Client → Backend)**: 16,000 Hz (unchanged from frontend perspective)
- **Receive (Backend → Client)**: 24,000 Hz (unchanged)

### Audio Quality
- Enhanced audio processing with improved clamping and conversion
- Better compatibility with the new model's audio processing pipeline

## Testing Instructions

### Prerequisites
1. Update backend dependencies:
   ```bash
   cd gemini-audio-audio-backend
   pip install -r requirements.txt
   ```

2. Ensure `GEMINI_API_KEY` is set in environment variables

### Backend Testing
1. Start the backend server:
   ```bash
   cd gemini-audio-audio-backend
   python main_v5.py
   ```

2. Verify the server starts without errors and shows:
   - MongoDB connection successful
   - WebSocket server running on port 9000
   - No configuration errors

### Frontend Testing
1. Start the frontend development server:
   ```bash
   cd InterviewBot
   yarn dev
   ```

2. Test the audio interview flow:
   - Navigate to an interview session
   - Verify microphone access is granted
   - Test audio recording and playback
   - Verify real-time transcription works
   - Test the new "Zephyr" voice quality

### Integration Testing
1. **Audio Quality**: Compare audio clarity with previous version
2. **Response Time**: Monitor latency for audio responses
3. **Turn Management**: Test conversation flow with the new turn coverage
4. **Session Resumption**: Test long conversations that trigger context window compression
5. **Function Calls**: Verify the `end_interview_call` tool still works correctly

## Expected Improvements

### 1. Audio Quality
- Better voice synthesis with "Zephyr"
- Improved audio processing pipeline

### 2. Conversation Flow
- More natural turn-taking with `TURN_INCLUDES_ALL_INPUT`
- Better context retention through sliding window compression

### 3. Performance
- Efficient memory management with context window compression
- Optimized audio processing

## Troubleshooting

### Common Issues
1. **API Version Errors**: Ensure `google-genai>=0.8.0` is installed
2. **Audio Rate Mismatches**: Verify sample rates match between frontend/backend
3. **Voice Errors**: Confirm "Zephyr" voice is available (fallback to available voices if needed)
4. **Context Limits**: Monitor for context window compression triggers in long conversations

### Debug Steps
1. Check backend logs for model connection errors
2. Verify WebSocket connection stability
3. Monitor audio queue sizes in frontend
4. Test with different conversation lengths to trigger compression

## Rollback Plan
If issues arise, revert these files to previous versions:
- `gemini-audio-audio-backend/config/config.py`
- `gemini-audio-audio-backend/main_v5.py`
- `gemini-audio-audio-backend/requirements.txt`
- `InterviewBot/src/lib/UseGeminiAudio.tsx`

The previous model configuration is preserved in git history.
