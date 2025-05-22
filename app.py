from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from TTS.api import TTS
import uuid
import asyncio
import os

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel

app = FastAPI()

tts = TTS(model_name="tts_models/en/ljspeech/vits", progress_bar=False)

# Keep track of peer connections to close on disconnect
pcs = set()

@app.get("/")
def read_root():
    return {"message": "TTS service is live"}

@app.post("/tts")
async def text_to_speech(text: str):
    try:
        output_file = f"/tmp/output_{uuid.uuid4()}.wav"
        tts.tts_to_file(text=text, file_path=output_file)
        return FileResponse(output_file, media_type="audio/wav", filename="speech.wav")
    except Exception as e:
        return {"error": str(e)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("datachannel")
    def on_datachannel(channel: RTCDataChannel):
        print(f"Data channel created: {channel.label}")

        @channel.on("message")
        async def on_message(message):
            print(f"Received message: {message}")

            # message is the text sent by client for TTS
            try:
                output_file = f"/tmp/output_{uuid.uuid4()}.wav"
                tts.tts_to_file(text=message, file_path=output_file)

                # Read audio file as bytes
                with open(output_file, "rb") as f:
                    data = f.read()

                # Break into chunks, send over data channel
                chunk_size = 16000  # 16KB chunks
                for i in range(0, len(data), chunk_size):
                    chunk = data[i:i+chunk_size]
                    channel.send(chunk)
                    await asyncio.sleep(0.01)  # slight delay to avoid congestion

                # Send an 'end' message to mark completion
                channel.send("END")

                # Cleanup file
                os.remove(output_file)

            except Exception as e:
                channel.send(f"ERROR: {str(e)}")

    try:
        while True:
            # Receive SDP offer from client
            data = await websocket.receive_json()
            if "sdp" in data:
                offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                await pc.setRemoteDescription(offer)
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                # Send SDP answer back
                await websocket.send_json({
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                })
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    finally:
        pcs.discard(pc)
        await pc.close()

