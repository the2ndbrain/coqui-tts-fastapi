from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from TTS.api import TTS
import asyncio
import os
import tempfile
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCDataChannel

app = FastAPI()
logging.basicConfig(level=logging.INFO)

tts = TTS(model_name="tts_models/en/ljspeech/vits", progress_bar=False)
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

    @pc.on("datachannel")
    def on_datachannel(channel: RTCDataChannel):
        logging.info(f"Data channel opened: {channel.label}")

        @channel.on("message")
        async def on_message(message):
            if not isinstance(message, str):
                return

            logging.info(f"Processing TTS request: {message}")
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                    tts.tts_to_file(text=message, file_path=tmp_file.name)
                    
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
        logging.info(f"ICE connection state: {state}")
        if state in ["failed", "disconnected"]:
            await pc.close()
            pcs.discard(pc)

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        try:
            if candidate:
                await websocket.send_json({
                    "event": "candidate",
                    "data": {
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

            if "event" not in data:
                continue

            if data["event"] == "offer":
                try:
                    offer = RTCSessionDescription(sdp=data["data"]["sdp"], type="offer")
                    await pc.setRemoteDescription(offer)
                    
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)

                    await websocket.send_json({
                        "event": "answer",
                        "data": {
                            "sdp": pc.localDescription.sdp,
                            "type": "answer"
                        }
                    })
                except Exception as e:
                    logging.error(f"Offer handling error: {e}")
                    await websocket.send_json({
                        "event": "error",
                        "data": str(e)
                    })

            elif data["event"] == "candidate":
                try:
                    candidate_data = data["data"]
                    # Modern approach first
                    try:
                        await pc.addIceCandidate(RTCIceCandidate(
                            sdpMid=candidate_data["sdpMid"],
                            sdpMLineIndex=candidate_data["sdpMLineIndex"],
                            candidate=candidate_data["candidate"]
                        ))
                    except TypeError:
                        # Fallback parsing
                        parts = candidate_data["candidate"].split()
                        await pc.addIceCandidate(RTCIceCandidate(
                            foundation=parts[0],
                            component=int(parts[1]),
                            protocol=parts[2],
                            priority=int(parts[3]),
                            ip=parts[4],
                            port=int(parts[5]),
                            type=parts[7],
                            sdpMid=candidate_data["sdpMid"],
                            sdpMLineIndex=candidate_data["sdpMLineIndex"]
                        ))
                except Exception as e:
                    logging.error(f"Candidate error: {e}")

    except WebSocketDisconnect:
        logging.info("Client disconnected")
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
    finally:
        try:
            pcs.discard(pc)
            await pc.close()
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    for pc in pcs:
        try:
            await pc.close()
        except Exception as e:
            logging.error(f"Shutdown error: {e}")
    pcs.clear()
