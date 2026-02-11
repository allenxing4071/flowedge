# FlowEdge UI 设计体系

## 触发词
用户说"UI 规范"、"设计系统"、"样式"、"页面视觉"、"仪表盘"、"前端"时执行本 Skill。

## 必须遵守的 Rules
- 新建/修改页面前先阅读本 Skill 了解设计令牌（R0 要求）
- 新经验写入本文件"经验沉淀"区（R8 要求）

## 设计哲学

融合三大顶级设计体系，服务于 **金融级数据监控 + 暗色终端** 场景：

| 来源 | 借鉴要素 |
|------|---------|
| **Vercel Geist** | 极简暗色、高对比 Mono 字体优先、无装饰网格 |
| **Grafana Node Graph** | 拓扑节点 Arc Ring、状态色环、连线箭头 |
| **Linear** | CSS 变量架构、语义状态色、模块化面板 |
| **Apple HIG** | SF 字体栈、动效克制（不花哨但有呼吸感） |

核心原则：**"Green means go, anything else grabs your eye."**

> 与 KKline 共享同一套设计语言，保持产品线视觉一致性。

---

## 1. 色彩系统（Design Tokens）

### 1.1 背景层级（4 级深度）

```css
--bg-0: #06070a;       /* 页面底色 — 最深 */
--bg-1: #0b0d12;       /* 卡片/面板背景 */
--bg-2: #10131a;       /* 次级容器/表头 */
--bg-3: #161a24;       /* 悬停高亮/激活态 */
--bg-hover: #1c2030;   /* 交互悬停 */
--bg-elevated: rgba(255,255,255,0.03);  /* 弹出层/抽屉 */
```

> 规则：层级越高数字越大越亮。弹出层/模态用 `--bg-elevated` 叠加半透明。

### 1.2 语义色（6 种 + glow 变体）

```css
/* 盈利/成功/运行中/买方 */
--green: #00d68f;
--green-dim: rgba(0,214,143,0.10);       /* badge/tag 背景 */
--green-glow: rgba(0,214,143,0.35);      /* 节点光晕 */

/* 亏损/错误/故障/卖方 */
--red: #ff5370;
--red-dim: rgba(255,83,112,0.10);
--red-glow: rgba(255,83,112,0.35);

/* 警告/异常/待处理 */
--amber: #ffb347;
--amber-dim: rgba(255,179,71,0.10);
--amber-glow: rgba(255,179,71,0.35);

/* 信息/链接/主操作 */
--blue: #4a90ff;
--blue-dim: rgba(74,144,255,0.10);
--blue-glow: rgba(74,144,255,0.35);

/* AI/特征/分析相关 */
--purple: #a78bfa;
--purple-dim: rgba(167,139,250,0.10);

/* 数据流/辅助 */
--cyan: #22d3ee;
--cyan-dim: rgba(34,211,238,0.10);

/* 未激活/休眠 */
--idle: #2a2e3a;
--idle-text: #545870;
```

### 1.3 文字层级

```css
--t1: #eaecf0;    /* 主文字 — 标题/数值 */
--t2: #8b90a3;    /* 次文字 — 描述/正文 */
--t3: #545870;    /* 辅助 — 标签/时间戳 */
```

### 1.4 边界

```css
--border: rgba(255,255,255,0.06);     /* 默认分割线 */
--border-h: rgba(255,255,255,0.10);   /* 悬停高亮边框 */
```

---

## 2. 字体栈

```css
--mono: 'SF Mono','Fira Code','JetBrains Mono',Menlo,Consolas,monospace;
--sans: -apple-system,BlinkMacSystemFont,'SF Pro Display','Inter','Segoe UI',sans-serif;
```

### 使用规则

| 场景 | 字体 | 字号 | 字重 |
|------|------|------|------|
| 价格/数值/指标值 | `--mono` | 14-34px | 700 |
| 时间戳/代码 | `--mono` | 11-12px | 500 |
| 标题/标签 | `--sans` | 12-14px | 600 |
| 正文/描述 | `--sans` | 13-14px | 400 |
| 全大写标签 | `--sans` | 10-12px | 600, `letter-spacing: 0.8-1.5px, text-transform: uppercase` |

---

## 3. 组件规范

### 3.1 统计卡片（Stats Grid）

特征引擎的核心展示组件。

```css
display: grid;
grid-template-columns: repeat(N, 1fr);
gap: 1px;
background: var(--border);  /* 利用 gap 做分割线 */
border-radius: 12px;
overflow: hidden;

.stat {
  background: var(--bg-2);
  padding: 22px 16px;
  text-align: center;
}
.stat-label { font-size: 12px; color: var(--t3); text-transform: uppercase; }
.stat-val   { font-family: var(--mono); font-size: 32px; font-weight: 700; }
```

### 3.2 特征值展示

```css
/* 正值（买方/看涨） */
.feat-positive { color: var(--green); }

/* 负值（卖方/看跌） */
.feat-negative { color: var(--red); }

/* 中性/无方向 */
.feat-neutral { color: var(--t2); }

/* 极端值高亮 */
.feat-extreme {
  animation: pulse 1s ease infinite;
  text-shadow: 0 0 8px currentColor;
}
```

### 3.3 数据源状态指示器

```css
.source-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.source-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
}
.source-dot.connected    { background: var(--green); }
.source-dot.disconnected { background: var(--red); }
.source-dot.degraded     { background: var(--amber); }
```

### 3.4 面板/卡片

```css
background: var(--bg-2);
border: 1px solid var(--border);
border-radius: 12px;
padding: 22px;
transition: border-color 0.2s;

&:hover { border-color: var(--border-h); }
```

### 3.5 Badge / Tag

```css
/* 方向标签 */
.tag-buy   { background: var(--green-dim); color: var(--green); }
.tag-sell  { background: var(--red-dim);   color: var(--red); }

/* 信号强度 */
.tag-high   { background: var(--green-dim); color: var(--green); }
.tag-medium { background: var(--amber-dim); color: var(--amber); }
.tag-low    { background: var(--red-dim);   color: var(--red); }

font-size: 13px; font-weight: 700;
padding: 4px 12px; border-radius: 5px;
text-transform: uppercase; letter-spacing: 0.5px;
```

---

## 4. 数据流拓扑视觉规范

FlowEdge 的数据流拓扑图用于展示 9 个数据源 → 11 个特征计算器的数据流向。

### 4.1 节点分层

```
┌─ 数据源层 ──────────────────────────────────────────────┐
│  [aggTrade] [depth] [bookTicker] [markPrice]             │
│  [forceOrder] [kline] [BinanceREST] [Coinglass] [Ext]   │
├─ 特征层 ────────────────────────────────────────────────┤
│  [CVD] [OFI] [VPIN] [大单] [深度] [Imbalance]           │
│  [费率] [清算] [OI] [情绪] [趋势]                        │
├─ 输出层 ────────────────────────────────────────────────┤
│  [SSE 推送] [REST API]                                   │
└─────────────────────────────────────────────────────────┘
```

### 4.2 节点视觉

```
尺寸：56×56px 圆形
外环：3px 状态色环
内部：24×24 图标
下方：节点名（11px, --t2, 居中）
下方2：状态文字（10px, 状态色）
```

### 4.3 节点状态动画

| 状态 | 外环色 | 动画 |
|------|--------|------|
| **active** | `--green` | 呼吸（opacity 0.6→1.0, 3s） |
| **warning** | `--amber` | 轻微抖动 |
| **error** | `--red` | 脉冲缩放 |
| **idle** | `--idle` | 无, opacity: 0.5 |

```css
@keyframes breathe {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.6; }
}
@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.08); }
}
```

### 4.4 连线规范

```
粗细：1.5px
默认色：rgba(255,255,255,0.08)
活跃色：rgba(状态色, 0.3)
箭头：终点 6px 等腰三角形（SVG marker）
```

---

## 5. FlowEdge 专属视觉元素

### 5.1 特征热力图

用于展示多币种 × 多特征的矩阵视图。

```css
.heatmap-cell {
  width: 48px; height: 48px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  transition: all 0.3s;
}
/* 颜色映射：-1 → red, 0 → neutral, +1 → green */
```

### 5.2 实时数据流指示器

```css
.data-flow {
  height: 3px;
  border-radius: 1.5px;
  overflow: hidden;
  background: var(--bg-3);
}
.data-flow-bar {
  height: 100%;
  background: var(--green);
  animation: flow 2s linear infinite;
  width: 30%;
}
@keyframes flow {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(400%); }
}
```

### 5.3 速率限制器仪表盘

```css
.rate-meter {
  width: 100px; height: 6px;
  background: var(--bg-3);
  border-radius: 3px;
  overflow: hidden;
}
.rate-fill {
  height: 100%;
  transition: width 0.5s;
}
.rate-fill.ok      { background: var(--green); }
.rate-fill.warning { background: var(--amber); }
.rate-fill.danger  { background: var(--red); }
```

---

## 6. 响应式断点

```css
@media (max-width: 1024px) {
  .main { grid-template-columns: 1fr; }
  .heatmap { overflow-x: auto; }
}
@media (max-width: 768px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  .node-label { display: none; }
}
```

---

## 7. 技术约束

- **与 KKline 设计语言一致**：共享色彩系统、字体栈、组件规范
- **静态前端**：如需仪表盘，使用纯 HTML + CSS + Vanilla JS（与 KKline dashboard.html 一致）
- **Admin 前端**：如需管理后台，使用 Next.js + React + Tailwind（与 KKline kkline-admin 一致）
- **Sparkline**：纯 Canvas 绘制，不引入图表库
- **数据刷新**：SSE `/features/stream` 实时推送 + REST 轮询兜底
- **不使用 CDN**：所有代码内联或本地文件

---

## 经验沉淀区

> 新经验写入此处（R8 要求）

<!-- 按 R8 模板追加经验条目 -->
