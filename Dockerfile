FROM python:3.12-slim

# Keep Python lean and unbuffered for clean container logs
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

# Optional: CLAP-based audio genre prediction. Enable with
#   docker build --build-arg INSTALL_GENRE=true   (compose sets this for you)
# It pulls in ffmpeg + torch/transformers (large), so it's off by default.
ARG INSTALL_GENRE=false

RUN if [ "$INSTALL_GENRE" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
      rm -rf /var/lib/apt/lists/* ; \
    fi

COPY requirements.txt requirements-genre.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_GENRE" = "true" ]; then \
      pip install --no-cache-dir -r requirements-genre.txt ; \
    fi

COPY app ./app

# SQLite lives in /data; the HuggingFace model cache lives in /models (its own
# volume, so resetting the song DB doesn't force a re-download of the ~1.7GB
# CLAP weights).
ENV HF_HOME=/models
RUN mkdir -p /data /models
VOLUME ["/data", "/models"]

EXPOSE 8080

# PORT is fixed inside the container (compose maps the host port). Set RELOAD=1
# to enable --reload (useful with a bind-mounted app dir for live code edits).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8080 ${RELOAD:+--reload}"]
