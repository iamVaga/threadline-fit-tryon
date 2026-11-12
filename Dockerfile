FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates fonts-dejavu-core libgl1 libglib2.0-0 libportaudio2 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN curl -fsSL -o pose_landmarker_lite.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task \
    && curl -fsSL -o selfie_multiclass_256x256.tflite https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/1/selfie_multiclass_256x256.tflite \
    && echo "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a  pose_landmarker_lite.task" | sha256sum -c - \
    && echo "c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0  selfie_multiclass_256x256.tflite" | sha256sum -c -
COPY server.py tryon_model.py ./
COPY static/ ./static/
RUN mkdir -p /app/static/garments /tmp/fitcheck-sessions && chown 1000:1000 /tmp/fitcheck-sessions
USER 1000:1000
ENV PORT=7860 TMP_DIR=/tmp/fitcheck-sessions LOG_FILE=/tmp/fitcheck-render.csv GRADIO_ANALYTICS_ENABLED=False MPLCONFIGDIR=/tmp/fitcheck-matplotlib
EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log"]
