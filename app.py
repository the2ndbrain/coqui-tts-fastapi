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

    @pc.on("datachannel")
    def on_datachannel(channel: RTCDataChannel):
        logging.info(f"Data channel created: {channel.label}")

        @channel.on("message")
        async def on_message(message):
            logging.info(f"Received message: {message}")

            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                    tts.tts_to_file(text=message, file_path=tmp_file.name)

                    with open(tmp_file.name, "rb") as f:
                        data = f.read()

                    chunk_size = 16000
                    for i in range(0, len(data), chunk_size):
                        chunk = data[i:i+chunk_size]
                        channel.send(chunk)
                        await asyncio.sleep(0.01)

                    channel.send("END")

                os.remove(tmp_file.name)

            except Exception as e:
                channel.send(f"ERROR: {str(e)}")

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if candidate:
            logging.info(f"Sending ICE candidate to client: {candidate}")
            await websocket.send_json({
                "candidate": {
                    "candidate": candidate.to_sdp(),
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex
                }
            })

    try:
        while True:
            data = await websocket.receive_json()

            if "sdp" in data:
                offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                await pc.setRemoteDescription(offer)

                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                await websocket.send_json({
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                })

            elif "candidate" in data:
                candidate_dict = data["candidate"]
                candidate = RTCIceCandidate(
                    sdpMid=candidate_dict.get("sdpMid"),
                    sdpMLineIndex=candidate_dict.get("sdpMLineIndex"),
                    candidate=candidate_dict.get("candidate")  # Changed from 'sdp' to 'candidate'
                )
                await pc.addIceCandidate(candidate)

    except WebSocketDisconnect:
        logging.info("WebSocket disconnected")
    finally:
        pcs.discard(pc)
        await pc.close()
