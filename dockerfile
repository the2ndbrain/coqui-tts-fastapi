FROM ghcr.io/coqui-ai/tts-cpu

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Clear default TTS entrypoint
ENTRYPOINT []

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5002"]
