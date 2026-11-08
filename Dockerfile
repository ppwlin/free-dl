FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DENO_INSTALL=/usr/local \
    PATH=/usr/local/bin:$PATH

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unzip ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Deno：yt-dlp 解析 YouTube 签名所需的 JS 运行时
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y \
 && deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py run.py ./

EXPOSE 8000
CMD ["python3", "run.py"]
