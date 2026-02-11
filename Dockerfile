# FlowEdge 后端 Dockerfile
# 订单流驱动的量化交易系统 — 数据管道 + 特征引擎

FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 创建数据目录
RUN mkdir -p data

# 暴露端口
EXPOSE 8000

# 启动服务
CMD ["uvicorn", "flowedge.api:app", "--host", "0.0.0.0", "--port", "8000"]
