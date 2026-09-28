# Homify als Container (z. B. direkt auf dem NAS oder einem Ubuntu-Server)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOMIFY_DATA=/data \
    HOMIFY_SPOTDL_VENV=/opt/spotdl \
    HOMIFY_SUPERVISED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m venv /opt/spotdl \
 && /opt/spotdl/bin/pip install --no-cache-dir spotdl

COPY homify ./homify
# Deno für yt-dlp (YouTube) vorab laden – schlägt das fehl, geht es später per „spotDL aktualisieren“
RUN /opt/spotdl/bin/python -c "from spotdl.utils.deno import download_deno; download_deno()" || true

VOLUME ["/data", "/music"]
EXPOSE 8484
CMD ["python", "-m", "homify", "run"]
