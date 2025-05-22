from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from TTS.api import TTS
import uuid
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
    return {"message": "TTS service is live"}

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
            logging.error(f"Task exception: {future.exception()}")

    @pc.on("datachannel")
    def on_datachannel(channel: RTCDataChannel):
        logging.info(f"Data channel created: {channel.label}")

        @channel.on("message")
        async def on_message(message):
            if not isinstance(message, str):
                return
                
            logging.info(f"Received message: {message}")
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                    tts.tts_to_file(text=message, file_path=tmp_file.name)

                    with open(tmp_file.name, "rb") as f:
                        data = f.read()

                    chunk_size = 16000
                    for i in range(0, len(data), chunk_size):
                        if channel.readyState != "open":
                            break
                        chunk = data[i:i+chunk_size]
                        channel.send(chunk)
                        await asyncio.sleep(0.01)

                    if channel.readyState == "open":
                        channel.send("END")

                os.remove(tmp_file.name)
            except Exception as e:
                logging.error(f"TTS error: {e}")
                if channel.readyState == "open":
                    channel.send(f"ERROR: {str(e)}")

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(f"ICE connection state changed to {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        try:
            if candidate and pc.iceConnectionState != "closed":
                await websocket.send_json({
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

            if "sdp" in data:
                if pc.connectionState == "closed":
                    raise Exception("PeerConnection is closed")

                offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                await pc.setRemoteDescription(offer)

                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                await websocket.send_json({
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                })

            elif "candidate" in data:
                if pc.iceConnectionState == "closed":
                    continue

                candidate_dict = data["candidate"]
                try:
                    candidate = RTCIceCandidate.from_sdp(candidate_dict["candidate"])
                    candidate.sdpMid = candidate_dict.get("sdpMid")
                    candidate.sdpMLineIndex = candidate_dict.get("sdpMLineIndex")
                    await pc.addIceCandidate(candidate)
                    
                except Exception as e:
                    logging.error(f"Failed to add ICE candidate: {e}")

    except WebSocketDisconnect:
        logging.info("Client disconnected")
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
    finally:
        try:
            pcs.discard(pc)
            await pc.close()
        except Exception as e:
            logging.error(f"Failed to close peer connection: {e}")
