# Use Python 3.9 (stable with Coqui TTS)
FROM python:3.9-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install Coqui TTS and FastAPI
RUN pip install TTS fastapi uvicorn python-multipart

# Copy the FastAPI app
COPY app.py /app/app.py

# Run the server
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
