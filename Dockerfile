FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# onnxruntime / opencv 运行所需的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt streamlit opencc-python-reimplemented rapidocr-onnxruntime

COPY . .

EXPOSE 8080

# 密钥请通过云托管环境变量 DEEPSEEK_API_KEY 注入，勿写进镜像
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", "--server.port=8080", "--server.headless=true"]
