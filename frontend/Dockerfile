# FlowEdge 交易驾驶舱前端 Dockerfile
# Next.js standalone 模式，极简生产镜像

FROM node:20-alpine AS builder
WORKDIR /app

# 依赖安装（利用缓存层）
COPY package.json package-lock.json ./
RUN npm ci

# 构建
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1

# Next.js NEXT_PUBLIC_* 变量在构建时烧入，必须通过 ARG 传入
ARG NEXT_PUBLIC_API_URL=https://flowedge.kline007.top
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

# 生产镜像 — 只包含 standalone 输出
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# 复制 standalone 输出
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
