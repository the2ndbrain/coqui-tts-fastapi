from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from TTS.api import TTS
import uuid
import asyncio
import os
import tempfile
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCDataChannel
from aiortc.contrib.media import MediaPlayer, MediaRecorder

app = FastAPI()
logging.basicConfig(level=logging.INFO)

# Initialize TTS model
tts = TTS(model_name="tts_models/en/ljspeech/vits", progress_bar=False)

# Store active peer connections
pcs = set()

@app.get("/")
def read_root():
    return {"message": "TTS WebRTC Service"}

@app.post("/tts")
async def text_to_speech(text: str):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            tts.tts_to_file(text=text, file_path=tmp_file.name)
            return FileResponse(tmp_file.name, media_type="audio/wav", filename="speech.wav")
    except Exception as e:
        return {"error": str(e)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    pc = RTCPeerConnection()
    pcs.add(pc)

    def log_exception(future):
        if future.exception():
            logging.error(f"WebRTC error: {future.exception()}")

    @pc.on("datachannel")
    def on_datachannel(channel: RTCDataChannel):
        logging.info(f"Data channel opened: {channel.label}")

        @channel.on("message")
        async def on_message(message):
            if not isinstance(message, str):
                return

            logging.info(f"Processing TTS request: {message}")
            try:
                # Generate speech to temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                    tts.tts_to_file(text=message, file_path=tmp_file.name)
                    
                    # Stream audio chunks
                    with open(tmp_file.name, "rb") as f:
                        while True:
                            chunk = f.read(4096)
                            if not chunk:
                                break
                            if channel.readyState == "open":
                                channel.send(chunk)
                                await asyncio.sleep(0.01)
                    
                    if channel.readyState == "open":
                        channel.send("END")

                os.unlink(tmp_file.name)
            except Exception as e:
                logging.error(f"TTS error: {e}")
                if channel.readyState == "open":
                    channel.send(f"ERROR: {str(e)}")

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        state = pc.iceConnectionState
        logging.info(f"ICE connection state changed to {state}")
        if state == "failed":
            await pc.close()
            pcs.discard(pc)

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        try:
            if candidate:
                await websocket.send_json({
                    "type": "candidate",
                    "candidate": {
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    }
                })
        except Exception as e:
            logging.error(f"Failed to send ICE candidate: {e}")

    try:
        while True:
            data = await websocket.receive_json()

            if data["type"] == "offer":
                # Handle SDP offer
                offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                await pc.setRemoteDescription(offer)

                # Create and send answer
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                await websocket.send_json({
                    "type": "answer",
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                })

            elif data["type"] == "candidate":
                # Handle ICE candidate
                try:
                    candidate_dict = data["candidate"]
                    await pc.addIceCandidate(RTCIceCandidate(
                        candidate=candidate_dict["candidate"],
                        sdpMid=candidate_dict["sdpMid"],
                        sdpMLineIndex=candidate_dict["sdpMLineIndex"]
                    ))
                except Exception as e:
                    logging.error(f"Failed to add ICE candidate: {e}")
                    continue

    except WebSocketDisconnect:
        logging.info("Client disconnected")
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
    finally:
        try:
            pcs.discard(pc)
            await pc.close()
        except Exception as e:
            logging.error(f"Peer connection cleanup error: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    # Close all peer connections on shutdown
    for pc in pcs:
        try:
            await pc.close()
        except Exception as e:
            logging.error(f"Error closing peer connection: {e}")
    pcs.clear()
