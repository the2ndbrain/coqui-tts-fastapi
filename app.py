from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from TTS.api import TTS
import uuid

app = FastAPI()

tts = TTS(model_name="tts_models/en/ljspeech/vits", progress_bar=False)

@app.post("/tts")
async def text_to_speech(text: str):
    try:
        output_file = f"/tmp/output_{uuid.uuid4()}.wav"
        tts.tts_to_file(text=text, file_path=output_file)
        return FileResponse(output_file, media_type="audio/wav", filename="speech.wav")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
