FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 安裝 ffmpeg + 字型 + 基本工具
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-noto-cjk \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# 複製 requirements 並安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式
COPY . .

# Railway 會注入 PORT，但這裡先宣告預設值
ENV PORT=7860

CMD ["python", "app.py"]
