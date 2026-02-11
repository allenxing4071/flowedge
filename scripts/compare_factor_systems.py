"""
三套交易框架对比分析脚本。

对比：
  A. 旧 11 因子（评分过线就交易）
  B. 新 14 因子（评分过线就交易）
  C. 南哥四层门卫框架（环境+位置+行为+方向，全部通过才交易）

方法论：
  1. 通过币安 REST API 获取最近 24h 的真实 1m K 线数据
  2. 从 K 线模拟特征值（CVD、VWAP、VP、吸收等）
  3. 分别用三套框架生成交易信号
  4. 模拟交易并对比胜率、盈亏比、回撤等

注意：K 线数据无法完全体现逐笔数据优势，门卫框架在实盘中表现会更好。
"""

import math
import time
from collections import defaultdict
from dataclasses import dataclass

import requests

# ── 配置 ──

SYMBOL = "BTCUSDT"
INTERVAL = "1m"
LIMIT = 1440       # 24 小时的 1 分钟 K 线
BINANCE_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"


# ── 数据结构 ──

@dataclass
class Kline:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int
    taker_buy_volume: float
    taker_buy_quote: float


@dataclass
class SimFeatures:
    """从 K 线模拟的特征值"""
    cvd_ratio: float
    price_change_pct: float
    volume_usdt: float
    volatility: float
    vwap: float
    vwap_deviation: float
    trend_score: float
    poc_price: float
    in_value_area: bool
    va_pct: float           # 0=VAL, 1=VAH
    val_price: float
    vah_price: float
    absorption_score: float
    absorption_events: int
    band_width_pct: float
    hvn_above: float
    hvn_below: float


@dataclass
class TradeResult:
    entry_idx: int
    exit_idx: int
    side: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    duration_bars: int


# ── 数据获取 ──

def fetch_klines(symbol: str, interval: str, limit: int) -> list[Kline]:
    resp = requests.get(BINANCE_KLINE_URL, params={
        "symbol": symbol, "interval": interval, "limit": limit
    }, timeout=10)
    resp.raise_for_status()
    return [Kline(
        open_time=int(k[0]), open=float(k[1]), high=float(k[2]),
        low=float(k[3]), close=float(k[4]), volume=float(k[5]),
        quote_volume=float(k[7]), trades=int(k[8]),
        taker_buy_volume=float(k[9]), taker_buy_quote=float(k[10]),
    ) for k in resp.json()]


# ── 特征模拟 ──

def simulate_features(klines: list[Kline], idx: int, lookback: int = 60) -> SimFeatures:
    k = klines[idx]
    start = max(0, idx - lookback)
    history = klines[start:idx + 1]

    # CVD
    total_vol = k.quote_volume if k.quote_volume > 0 else 1
    cvd_ratio = (k.taker_buy_quote / total_vol - 0.5) * 2

    # 价格变化 / 波动率
    price_change = (k.close - k.open) / k.open * 100 if k.open > 0 else 0
    volatility = (k.high - k.low) / k.open * 100 if k.open > 0 else 0

    # VWAP
    sum_pv = sum(h.close * h.quote_volume for h in history)
    sum_v = sum(h.quote_volume for h in history)
    vwap = sum_pv / sum_v if sum_v > 0 else k.close
    vwap_dev = (k.close - vwap) / vwap * 100 if vwap > 0 else 0

    # VWAP band width（标准差模拟）
    if sum_v > 0 and len(history) > 1:
        mean_p = sum_pv / sum_v
        var = sum(h.quote_volume * (h.close - mean_p) ** 2 for h in history) / sum_v
        std = var ** 0.5
        band_width = (std * 4) / mean_p * 100 if mean_p > 0 else 0
    else:
        band_width = 0

    # 趋势
    recent = history[-20:] if len(history) >= 20 else history
    ups = sum(1 for h in recent if h.close > h.open)
    downs = sum(1 for h in recent if h.close < h.open)
    trend = (ups - downs) / len(recent) if recent else 0
    # 映射到 [-5, 5] 的 alignment_score 范围
    alignment_score = trend * 5

    # VP 模拟
    price_bins = defaultdict(float)
    for h in history:
        mid = round((h.high + h.low) / 2, 0)
        price_bins[mid] += h.quote_volume
    poc = max(price_bins, key=price_bins.get) if price_bins else k.close
    total_vp = sum(price_bins.values())

    # Value Area
    sorted_bins = sorted(price_bins.items(), key=lambda x: -x[1])
    accumulated = 0
    va_prices = []
    for p, v in sorted_bins:
        accumulated += v
        va_prices.append(p)
        if accumulated >= total_vp * 0.7:
            break
    val_p = min(va_prices) if va_prices else k.close - 100
    vah_p = max(va_prices) if va_prices else k.close + 100
    in_va = val_p <= k.close <= vah_p
    va_range = vah_p - val_p if vah_p > val_p else 1
    va_pct = (k.close - val_p) / va_range

    # HVN 模拟
    hvn_above = 0
    hvn_below = 0
    hvn_threshold = total_vp * 0.03
    for p, v in sorted(price_bins.items()):
        if v >= hvn_threshold:
            if p > k.close and (hvn_above == 0 or p < hvn_above):
                hvn_above = p
            elif p < k.close and p > hvn_below:
                hvn_below = p

    # 吸收模拟
    if volatility > 0.001:
        absorption = min(k.quote_volume / (abs(price_change) * 10000 + 1), 1.0)
    else:
        absorption = 1.0 if k.quote_volume > 100000 else 0.0

    # 吸收事件计数（近 5 根 K 线中高吸收的次数）
    abs_events = 0
    for h in history[-5:]:
        h_vol = h.quote_volume
        h_change = abs((h.close - h.open) / h.open * 100) if h.open > 0 else 0
        if h_vol > 50000 and h_change < 0.05:
            abs_events += 1

    return SimFeatures(
        cvd_ratio=cvd_ratio, price_change_pct=price_change,
        volume_usdt=k.quote_volume, volatility=volatility,
        vwap=vwap, vwap_deviation=vwap_dev, trend_score=alignment_score,
        poc_price=poc, in_value_area=in_va, va_pct=va_pct,
        val_price=val_p, vah_price=vah_p,
        absorption_score=absorption, absorption_events=abs_events,
        band_width_pct=band_width, hvn_above=hvn_above, hvn_below=hvn_below,
    )


# ── 评分系统 ──

def tanh_scale(v: float, s: float = 1.0) -> float:
    return math.tanh(v * s)


def score_old(feat: SimFeatures) -> float:
    """旧 11 因子"""
    s = feat.cvd_ratio * 0.15
    s += tanh_scale(feat.cvd_ratio, 0.8) * 0.15
    s += feat.cvd_ratio * 0.5 * 0.10
    vol_z = tanh_scale((feat.volume_usdt - 500000) / 300000, 0.5)
    s += vol_z * feat.cvd_ratio * 0.12
    s += feat.cvd_ratio * 0.3 * 0.08
    s += tanh_scale(feat.trend_score / 5, 1.5) * 0.08
    return s


def score_new(feat: SimFeatures) -> float:
    """新 14 因子"""
    s = feat.cvd_ratio * 0.12
    s += tanh_scale(feat.cvd_ratio, 0.8) * 0.12
    s += feat.cvd_ratio * 0.5 * 0.08
    vol_z = tanh_scale((feat.volume_usdt - 500000) / 300000, 0.5)
    s += vol_z * feat.cvd_ratio * 0.10
    s += feat.cvd_ratio * 0.3 * 0.06
    s += tanh_scale(feat.trend_score / 5, 1.5) * 0.05

    # VWAP
    mean_rev = tanh_scale(-feat.vwap_deviation / 0.5, 1.0)
    s += max(-1, min(1, mean_rev)) * 0.08

    # VP
    poc_dev = (feat.vwap - feat.poc_price) / feat.poc_price * 100 if feat.poc_price > 0 else 0
    vp_s = tanh_scale(-poc_dev / 0.3, 0.5) if feat.in_value_area else tanh_scale(poc_dev / 0.5, 1.0)
    s += max(-1, min(1, vp_s)) * 0.07

    # 吸收
    if feat.absorption_score > 0.3:
        s += max(-1, min(1, feat.absorption_score * feat.cvd_ratio)) * 0.10

    return s


def classify(score: float) -> str:
    if score >= 0.40:
        return "STRONG_BUY"
    elif score >= 0.15:
        return "BUY"
    elif score <= -0.40:
        return "STRONG_SELL"
    elif score <= -0.15:
        return "SELL"
    return "NEUTRAL"


# ── 南哥门卫框架 ──

def gate_check(feat: SimFeatures, score: float) -> tuple[bool, str, str, float, float]:
    """
    四层门卫检查。

    返回: (passed, signal, side, stop_loss_pct, take_profit_pct)
    """
    alignment = abs(feat.trend_score)

    # Layer 1: 环境分类
    if feat.band_width_pct > 2.0:
        return False, "NEUTRAL", "NONE", 2.0, 1.5

    regime = "unclear"
    if alignment >= 3.0 and feat.band_width_pct > 0.5:
        regime = "trending"
    elif not feat.in_value_area and alignment >= 2.0:
        regime = "breakout"
    elif alignment <= 1.0 and feat.in_value_area and feat.absorption_events >= 2:
        regime = "ranging"
    else:
        return False, "NEUTRAL", "NONE", 2.0, 1.5

    # Layer 2: 位置过滤
    side = "NONE"
    if regime == "ranging":
        if feat.va_pct <= 0.1:
            side = "LONG"
        elif feat.va_pct >= 0.9:
            side = "SHORT"
        elif abs(feat.vwap_deviation) < 0.15:
            side = "SHORT" if feat.vwap_deviation > 0 else "LONG"
        else:
            return False, "NEUTRAL", "NONE", 2.0, 1.5

    elif regime == "trending":
        if feat.trend_score > 0 and feat.vwap_deviation <= 0.3:
            side = "LONG"
        elif feat.trend_score < 0 and feat.vwap_deviation >= -0.3:
            side = "SHORT"
        else:
            return False, "NEUTRAL", "NONE", 2.0, 1.5

    elif regime == "breakout":
        if feat.trend_score > 0 and not feat.in_value_area:
            side = "LONG"
        elif feat.trend_score < 0 and not feat.in_value_area:
            side = "SHORT"
        else:
            return False, "NEUTRAL", "NONE", 2.0, 1.5

    # Layer 3: 行为确认
    has_behavior = False
    if feat.absorption_score > 0.5:
        has_behavior = True
    if feat.absorption_events >= 3:
        has_behavior = True
    if feat.volume_usdt > 800000 and abs(feat.cvd_ratio) > 0.3:
        has_behavior = True
    if not has_behavior:
        return False, "NEUTRAL", "NONE", 2.0, 1.5

    # Layer 4: 方向确认
    if abs(score) < 0.30:
        return False, "NEUTRAL", "NONE", 2.0, 1.5
    score_side = "LONG" if score > 0 else "SHORT"
    if score_side != side:
        return False, "NEUTRAL", "NONE", 2.0, 1.5

    # 通过 — 计算动态止损止盈
    current = feat.vwap * (1 + feat.vwap_deviation / 100)
    if side == "LONG":
        sl = max(0.3, min((current - feat.val_price) / current * 100 + 0.1, 2.0)) if feat.val_price > 0 else 2.0
        tp = max(0.5, (feat.hvn_above - current) / current * 100) if feat.hvn_above > current else 1.5
    else:
        sl = max(0.3, min((feat.vah_price - current) / current * 100 + 0.1, 2.0)) if feat.vah_price > 0 else 2.0
        tp = max(0.5, (current - feat.hvn_below) / current * 100) if feat.hvn_below > 0 and feat.hvn_below < current else 1.5

    if regime == "ranging":
        tp = min(tp, 1.0)

    signal = "BUY" if side == "LONG" else "SELL"
    if abs(score) >= 0.40:
        signal = "STRONG_BUY" if side == "LONG" else "STRONG_SELL"

    return True, signal, side, sl, tp


# ── 交易模拟 ──

def simulate_trades(klines, score_fn, use_gate=False, min_hold=5, cooldown=2, fee_pct=0.09) -> list[TradeResult]:
    trades = []
    position = None
    cooldown_until = 0

    for i in range(60, len(klines) - 1):
        feat = simulate_features(klines, i)
        score = score_fn(feat)

        if use_gate:
            passed, signal, side, sl_pct, tp_pct = gate_check(feat, score)
            if not passed:
                signal = "NEUTRAL"
        else:
            signal = classify(score)
            sl_pct = 5.0
            tp_pct = 1.5

        if position:
            entry_idx, p_side, entry_price, p_sl, p_tp = position
            held = i - entry_idx

            if p_side == "LONG":
                pnl_pct = (klines[i].close - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - klines[i].close) / entry_price * 100

            should_close = False

            # 动态止损
            if pnl_pct <= -p_sl:
                should_close = True
            # 动态止盈
            elif pnl_pct >= p_tp:
                should_close = True
            elif held >= min_hold:
                if signal == "NEUTRAL":
                    should_close = True
                elif p_side == "LONG" and signal in ("SELL", "STRONG_SELL"):
                    should_close = True
                elif p_side == "SHORT" and signal in ("BUY", "STRONG_BUY"):
                    should_close = True

            if should_close:
                exit_price = klines[i].close
                if p_side == "LONG":
                    raw_pnl = (exit_price - entry_price) / entry_price * 100
                else:
                    raw_pnl = (entry_price - exit_price) / entry_price * 100
                net_pnl = raw_pnl - fee_pct * 2

                trades.append(TradeResult(
                    entry_idx=entry_idx, exit_idx=i,
                    side=p_side, entry_price=entry_price, exit_price=exit_price,
                    pnl_pct=net_pnl, duration_bars=held,
                ))
                position = None
                cooldown_until = i + cooldown

        elif i >= cooldown_until:
            if use_gate:
                if passed and side != "NONE":
                    position = (i, side, klines[i].close, sl_pct, tp_pct)
            else:
                if signal in ("STRONG_BUY", "BUY"):
                    position = (i, "LONG", klines[i].close, sl_pct, tp_pct)
                elif signal in ("STRONG_SELL", "SELL"):
                    position = (i, "SHORT", klines[i].close, sl_pct, tp_pct)

    return trades


# ── 统计 ──

def calc_stats(trades: list[TradeResult]) -> dict:
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "total_pnl_pct": 0, "avg_pnl_pct": 0, "avg_win_pct": 0,
                "avg_loss_pct": 0, "profit_factor": 0, "sharpe": 0,
                "max_drawdown_pct": 0, "avg_duration": 0}

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    pnls = [t.pnl_pct for t in trades]
    total_pnl = sum(pnls)
    avg = total_pnl / len(pnls)
    std = (sum((p - avg) ** 2 for p in pnls) / len(pnls)) ** 0.5 if len(pnls) > 1 else 0

    win_total = sum(t.pnl_pct for t in wins) if wins else 0
    loss_total = sum(abs(t.pnl_pct) for t in losses) if losses else 0

    cum = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cum += t.pnl_pct
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl_pct": round(total_pnl, 2),
        "avg_pnl_pct": round(avg, 3),
        "avg_win_pct": round(win_total / len(wins), 3) if wins else 0,
        "avg_loss_pct": round(-loss_total / len(losses), 3) if losses else 0,
        "profit_factor": round(win_total / loss_total, 2) if loss_total > 0 else float("inf"),
        "sharpe": round(avg / std * (252 ** 0.5), 2) if std > 0 else 0,
        "max_drawdown_pct": round(max_dd, 2),
        "avg_duration": round(sum(t.duration_bars for t in trades) / len(trades), 1),
    }


# ── 信号质量 ──

def signal_quality(klines, score_fn, use_gate=False, forward=5) -> dict:
    correct = 0
    total = 0
    for i in range(60, len(klines) - forward):
        feat = simulate_features(klines, i)
        score = score_fn(feat)

        if use_gate:
            passed, signal, side, _, _ = gate_check(feat, score)
            if not passed:
                continue
        else:
            signal = classify(score)

        if signal == "NEUTRAL":
            continue

        future = klines[i + forward].close
        actual = (future - klines[i].close) / klines[i].close * 100
        is_buy = signal in ("STRONG_BUY", "BUY")
        is_correct = (is_buy and actual > 0) or (not is_buy and actual < 0)

        total += 1
        if is_correct:
            correct += 1

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total * 100, 1) if total > 0 else 0,
    }


# ── 主流程 ──

def main():
    print("=" * 75)
    print("  FlowEdge 三套框架对比分析")
    print("  A: 旧11因子  B: 新14因子  C: 南哥四层门卫")
    print("=" * 75)
    print()

    print(f"获取 {SYMBOL} 最近 {LIMIT} 根 {INTERVAL} K 线...")
    klines = fetch_klines(SYMBOL, INTERVAL, LIMIT)
    print(f"  {len(klines)} 根 K 线")
    print(f"  时间: {time.strftime('%m-%d %H:%M', time.localtime(klines[0].open_time / 1000))} "
          f"→ {time.strftime('%m-%d %H:%M', time.localtime(klines[-1].open_time / 1000))}")
    total_vol = sum(k.quote_volume for k in klines)
    print(f"  成交额: ${total_vol:,.0f}")
    print(f"  价格: {min(k.low for k in klines):,.0f} ~ {max(k.high for k in klines):,.0f}")
    print()

    # ── 1. 信号质量 ──
    print("─" * 75)
    print("  一、信号方向准确率（未来 5 分钟）")
    print("─" * 75)
    print()

    qa = signal_quality(klines, score_old, forward=5)
    qb = signal_quality(klines, score_new, forward=5)
    qc = signal_quality(klines, score_new, use_gate=True, forward=5)

    print(f"{'指标':15s} {'A:旧11因子':>12s} {'B:新14因子':>12s} {'C:南哥门卫':>12s}")
    print("-" * 53)
    print(f"{'信号总数':15s} {qa['total']:>12d} {qb['total']:>12d} {qc['total']:>12d}")
    print(f"{'准确率':15s} {qa['accuracy']:>11.1f}% {qb['accuracy']:>11.1f}% {qc['accuracy']:>11.1f}%")
    print()

    # ── 2. 模拟交易 ──
    print("─" * 75)
    print("  二、模拟交易回测")
    print("─" * 75)
    print()

    ta = simulate_trades(klines, score_old)
    tb = simulate_trades(klines, score_new)
    tc = simulate_trades(klines, score_new, use_gate=True)
    sa = calc_stats(ta)
    sb = calc_stats(tb)
    sc = calc_stats(tc)

    print(f"{'指标':15s} {'A:旧11因子':>12s} {'B:新14因子':>12s} {'C:南哥门卫':>12s}")
    print("-" * 53)

    rows = [
        ("交易次数", "total", "d"),
        ("胜率", "win_rate", ".1f%"),
        ("总盈亏%", "total_pnl_pct", ".2f%"),
        ("平均盈亏%", "avg_pnl_pct", ".3f%"),
        ("平均盈利%", "avg_win_pct", ".3f%"),
        ("平均亏损%", "avg_loss_pct", ".3f%"),
        ("盈亏比", "profit_factor", ".2f"),
        ("Sharpe", "sharpe", ".2f"),
        ("最大回撤%", "max_drawdown_pct", ".2f%"),
        ("平均持仓(bars)", "avg_duration", ".1f"),
    ]

    for label, key, fmt in rows:
        va = sa.get(key, 0)
        vb = sb.get(key, 0)
        vc = sc.get(key, 0)
        if fmt.endswith("%"):
            f = fmt[:-1]
            print(f"{label:15s} {va:>11{f}}% {vb:>11{f}}% {vc:>11{f}}%")
        else:
            print(f"{label:15s} {va:>12{fmt}} {vb:>12{fmt}} {vc:>12{fmt}}")
    print()

    # ── 3. 不同时间窗口 ──
    print("─" * 75)
    print("  三、不同预测窗口的准确率")
    print("─" * 75)
    print()

    print(f"{'窗口':8s} {'A:旧11因子':>12s} {'B:新14因子':>12s} {'C:南哥门卫':>12s}")
    print("-" * 46)

    for fwd in [1, 3, 5, 10, 15, 30]:
        a = signal_quality(klines, score_old, forward=fwd)
        b = signal_quality(klines, score_new, forward=fwd)
        c = signal_quality(klines, score_new, use_gate=True, forward=fwd)
        print(f"{fwd:2d}分钟    {a['accuracy']:>11.1f}% {b['accuracy']:>11.1f}% {c['accuracy']:>11.1f}%")

    print()

    # ── 4. 总结 ──
    print("═" * 75)
    print("  总结")
    print("═" * 75)
    print()

    print(f"  {'':15s} {'A:旧11因子':>12s} {'B:新14因子':>12s} {'C:南哥门卫':>12s}")
    print(f"  {'信号准确率':15s} {qa['accuracy']:>11.1f}% {qb['accuracy']:>11.1f}% {qc['accuracy']:>11.1f}%")
    print(f"  {'交易次数':15s} {sa['total']:>12d} {sb['total']:>12d} {sc['total']:>12d}")
    print(f"  {'胜率':15s} {sa['win_rate']:>11.1f}% {sb['win_rate']:>11.1f}% {sc['win_rate']:>11.1f}%")
    print(f"  {'盈亏比':15s} {sa['profit_factor']:>12.2f} {sb['profit_factor']:>12.2f} {sc['profit_factor']:>12.2f}")
    print(f"  {'总盈亏%':15s} {sa['total_pnl_pct']:>11.2f}% {sb['total_pnl_pct']:>11.2f}% {sc['total_pnl_pct']:>11.2f}%")
    print()

    # 关键对比
    if sc['total'] == 0:
        print("  C(南哥门卫) 在此时段内未触发任何交易。")
        print("  这说明门卫过滤非常严格 — 宁可不交易也不乱交易。")
        print("  实盘中，门卫会在真正的好机会出现时才出手。")
    else:
        if sc['win_rate'] > sa['win_rate']:
            print(f"  南哥门卫胜率 {sc['win_rate']:.1f}% 高于旧框架 {sa['win_rate']:.1f}%")
        print(f"  南哥门卫交易 {sc['total']} 笔 vs 旧框架 {sa['total']} 笔（减少 {sa['total'] - sc['total']} 笔）")

    print()
    print("  注意：K 线数据无法完全体现逐笔数据的优势。")
    print("  门卫框架的真正价值在实盘 paper trading 中才能充分体现，")
    print("  因为它依赖实时吸收检测、盘口假墙、大单方向等逐笔数据。")
    print()


if __name__ == "__main__":
    main()
