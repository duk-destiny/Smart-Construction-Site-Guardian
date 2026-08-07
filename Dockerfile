FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o Acquire::Retries=3 \
    && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt

COPY agents/ config/ core/ dao/ services/ ui/ scripts/ app.py requirements.txt ./

# 非 root 运行（uid 1000 兼容常见宿主用户；Docker Desktop 绑定挂载默认可写，
# Linux 宿主若 data 目录非 1000 属主需先 chown -R 1000:1000 ./data）
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]
