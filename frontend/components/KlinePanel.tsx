'use client';

import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from 'lightweight-charts';

/**
 * 本文件核心用途：订单流区右上角 K 线面板（币安风格增强版）
 * - 使用 lightweight-charts 绘制蜡烛图 + 成交量 + MA 均线
 * - 展示 24h 关键行情：涨跌、高低、成交量、成交额
 * - 支持周期切换（1m/5m/15m/1h/4h/1d）与自动轮询更新
 */

type Interval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d' | '1w' | '1M';

const INTERVAL_OPTIONS: Array<{ value: Interval; label: string }> = [
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
  { value: '1h', label: '1时' },
  { value: '4h', label: '4时' },
  { value: '1d', label: '日线' },
  { value: '1w', label: '周线' },
  { value: '1M', label: '月线' },
];

const INTERVAL_LABEL: Record<Interval, string> = {
  '1m': '1分',
  '5m': '5分',
  '15m': '15分',
  '1h': '1时',
  '4h': '4时',
  '1d': '日线',
  '1w': '周线',
  '1M': '月线',
};

function visibleBarsByInterval(interval: Interval): number {
  switch (interval) {
    case '1m':
      return 70;
    case '5m':
      return 90;
    case '15m':
      return 110;
    case '1h':
      return 120;
    case '4h':
      return 140;
    case '1d':
      return 160;
    case '1w':
      return 130;
    case '1M':
      return 100;
    default:
      return 110;
  }
}

function fmt(v: number): string {
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function fmtCompact(v: number): string {
  if (!isFinite(v)) return '--';
  if (Math.abs(v) >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(2)}K`;
  return v.toFixed(2);
}

function buildMAData(candles: CandlestickData<UTCTimestamp>[], period: number): LineData<UTCTimestamp>[] {
  const out: LineData<UTCTimestamp>[] = [];
  let rolling = 0;
  const values = candles.map((c) => c.close);
  for (let i = 0; i < values.length; i += 1) {
    rolling += values[i];
    if (i >= period) rolling -= values[i - period];
    if (i >= period - 1) {
      out.push({
        time: candles[i].time,
        value: rolling / period,
      });
    }
  }
  return out;
}

interface Ticker24h {
  priceChangePercent: number;
  highPrice: number;
  lowPrice: number;
  volume: number;
  quoteVolume: number;
}

interface KlineBarStats {
  open: number;
  high: number;
  low: number;
  close: number;
}

export default function KlinePanel({ symbol }: { symbol: string }) {
  // 主图与量能区分割比例（0~1）。值越大，量能区越小、分割越明显。
  const volumeSplit = 0.8;
  const chartWrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const ma5SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ma10SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ma20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  // 视图锁定：用户手动缩放/拖动后，不再被自动刷新覆盖
  const userViewLockedRef = useRef(false);
  const suppressViewEventRef = useRef(false);
  const viewKeyRef = useRef('');

  const [interval, setInterval] = useState<Interval>('1m');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [priceChangePct, setPriceChangePct] = useState(0);
  const [barStats, setBarStats] = useState<KlineBarStats | null>(null);
  const [maSnapshot, setMaSnapshot] = useState<{ ma5: number | null; ma10: number | null; ma20: number | null }>({
    ma5: null,
    ma10: null,
    ma20: null,
  });
  const [ticker24h, setTicker24h] = useState<Ticker24h | null>(null);

  useEffect(() => {
    const nextKey = `${symbol}:${interval}`;
    if (viewKeyRef.current !== nextKey) {
      // 切换币种/周期后，允许重新自动定位到默认可视范围
      userViewLockedRef.current = false;
      viewKeyRef.current = nextKey;
    }
  }, [symbol, interval]);

  useEffect(() => {
    if (!chartWrapRef.current) return;

    const chart = createChart(chartWrapRef.current, {
      width: chartWrapRef.current.clientWidth,
      height: chartWrapRef.current.clientHeight || 320,
      layout: {
        background: { type: ColorType.Solid, color: '#0b0e11' },
        textColor: '#8f9db0',
      },
      grid: {
        vertLines: { color: 'rgba(143, 157, 176, 0.09)' },
        horzLines: { color: 'rgba(143, 157, 176, 0.09)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'rgba(143, 157, 176, 0.16)' },
      timeScale: {
        borderColor: 'rgba(143, 157, 176, 0.16)',
        timeVisible: true,
        secondsVisible: false,
        // 贴近币安默认观感：蜡烛更宽，不是细线感
        barSpacing: 10,
        minBarSpacing: 7,
        rightOffset: 4,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#00c853',
      downColor: '#ff1744',
      borderUpColor: '#00c853',
      borderDownColor: '#ff1744',
      wickUpColor: '#00c853',
      wickDownColor: '#ff1744',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    const ma5Series = chart.addSeries(LineSeries, {
      color: '#f59e0b',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const ma10Series = chart.addSeries(LineSeries, {
      color: '#60a5fa',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const ma20Series = chart.addSeries(LineSeries, {
      color: '#a78bfa',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // 主图与量能区都设置独立边距，避免蜡烛压进量能区导致“粘连”
    chart.priceScale('right').applyOptions({
      scaleMargins: {
        top: 0.03,
        bottom: Math.max(0.02, 1 - volumeSplit + 0.03),
      },
      borderVisible: true,
      borderColor: 'rgba(143, 157, 176, 0.16)',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: volumeSplit, bottom: 0.01 },
      borderVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ma5SeriesRef.current = ma5Series;
    ma10SeriesRef.current = ma10Series;
    ma20SeriesRef.current = ma20Series;

    const onVisibleRangeChange = () => {
      if (suppressViewEventRef.current) return;
      userViewLockedRef.current = true;
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);

    const ro = new ResizeObserver(() => {
      if (!chartWrapRef.current || !chartRef.current) return;
      chartRef.current.applyOptions({
        width: chartWrapRef.current.clientWidth,
        height: chartWrapRef.current.clientHeight || 320,
      });
    });
    ro.observe(chartWrapRef.current);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      ma5SeriesRef.current = null;
      ma10SeriesRef.current = null;
      ma20SeriesRef.current = null;
    };
  }, [volumeSplit]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const pull = async () => {
      try {
        if (alive) setLoading(true);

        const res = await fetch(
          `https://fapi.binance.com/fapi/v1/klines?symbol=${symbol}&interval=${interval}&limit=240`,
          { cache: 'no-store' },
        );
        if (!res.ok) {
          throw new Error(`K线接口异常：${res.status}`);
        }

        const rows = (await res.json()) as Array<Array<string | number>>;
        if (!rows.length) throw new Error('未获取到K线数据');

        const candles: CandlestickData<UTCTimestamp>[] = rows.map((k) => ({
          time: Math.floor(Number(k[0]) / 1000) as UTCTimestamp,
          open: Number(k[1]),
          high: Number(k[2]),
          low: Number(k[3]),
          close: Number(k[4]),
        }));

        const volumes = rows.map((k) => {
          const open = Number(k[1]);
          const close = Number(k[4]);
          return {
            time: Math.floor(Number(k[0]) / 1000) as UTCTimestamp,
            value: Number(k[5]),
            color: close >= open ? 'rgba(0, 200, 83, 0.45)' : 'rgba(255, 23, 68, 0.45)',
          };
        });

        const ma5Data = buildMAData(candles, 5);
        const ma10Data = buildMAData(candles, 10);
        const ma20Data = buildMAData(candles, 20);

        candleSeriesRef.current?.setData(candles);
        volumeSeriesRef.current?.setData(volumes);
        ma5SeriesRef.current?.setData(ma5Data);
        ma10SeriesRef.current?.setData(ma10Data);
        ma20SeriesRef.current?.setData(ma20Data);
        if (!userViewLockedRef.current) {
          const visibleBars = visibleBarsByInterval(interval);
          const from = Math.max(0, candles.length - visibleBars);
          suppressViewEventRef.current = true;
          chartRef.current?.timeScale().applyOptions({
            barSpacing: interval === '1m' ? 11 : 9,
            minBarSpacing: 6,
            rightOffset: 4,
          });
          chartRef.current?.timeScale().setVisibleLogicalRange({
            from,
            to: candles.length + 1,
          });
          setTimeout(() => {
            suppressViewEventRef.current = false;
          }, 0);
        }

        const last = candles[candles.length - 1];
        const prev = candles[candles.length - 2] || candles[0];
        const lastRaw = rows[rows.length - 1];
        if (alive && last && prev) {
          setLastPrice(last.close);
          setPriceChangePct(prev.close > 0 ? ((last.close - prev.close) / prev.close) * 100 : 0);
          setBarStats({
            open: Number(lastRaw[1]),
            high: Number(lastRaw[2]),
            low: Number(lastRaw[3]),
            close: Number(lastRaw[4]),
          });
          setMaSnapshot({
            ma5: ma5Data.length ? ma5Data[ma5Data.length - 1].value : null,
            ma10: ma10Data.length ? ma10Data[ma10Data.length - 1].value : null,
            ma20: ma20Data.length ? ma20Data[ma20Data.length - 1].value : null,
          });
          setError(null);
        }
      } catch (e) {
        if (alive) {
          setError(e instanceof Error ? e.message : 'K线加载失败');
        }
      } finally {
        if (alive) setLoading(false);
      }

      if (alive) {
        timer = setTimeout(pull, interval === '1m' ? 2200 : 4500);
      }
    };

    pull();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [symbol, interval]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const pullTicker = async () => {
      try {
        const res = await fetch(`https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=${symbol}`, {
          cache: 'no-store',
        });
        if (!res.ok) return;
        const ticker = (await res.json()) as Record<string, string>;
        if (!alive) return;
        setTicker24h({
          priceChangePercent: Number(ticker.priceChangePercent || 0),
          highPrice: Number(ticker.highPrice || 0),
          lowPrice: Number(ticker.lowPrice || 0),
          volume: Number(ticker.volume || 0),
          quoteVolume: Number(ticker.quoteVolume || 0),
        });
      } catch {
        // 24h 数据失败时不打断主图
      }
      if (alive) timer = setTimeout(pullTicker, 8000);
    };

    pullTicker();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [symbol]);

  return (
    <div className="bg-surface-1 rounded-xl border border-surface-3/30 overflow-hidden h-full flex flex-col">
      <div className="px-2.5 py-1.5 border-b border-surface-3/30 flex items-center justify-between gap-3 bg-[#0b0e11]">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-medium text-text-primary">{symbol}</span>
          <span className="text-[10px] text-text-tertiary">{INTERVAL_LABEL[interval]}</span>
          {lastPrice !== null && (
            <span className={`text-xxs font-mono ${priceChangePct >= 0 ? 'text-bull' : 'text-bear'}`}>
              {fmt(lastPrice)} ({priceChangePct >= 0 ? '+' : ''}{priceChangePct.toFixed(2)}%)
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {INTERVAL_OPTIONS.map((item) => (
            <button
              key={item.value}
              onClick={() => setInterval(item.value)}
              className={`px-2 py-0.5 rounded text-xxs transition-colors ${
                interval === item.value
                  ? 'bg-info text-white'
                  : 'text-text-tertiary hover:bg-surface-2/70'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="relative flex-1 min-h-[280px] bg-[#0b0e11]">
        <div ref={chartWrapRef} className="absolute inset-0" />

        {/* 量能区背景遮罩 + 分割线：解决“上下粘连”视觉问题 */}
        <div
          className="absolute left-0 right-0 bottom-0 pointer-events-none"
          style={{
            top: `${volumeSplit * 100}%`,
            zIndex: 4,
            borderTop: '2px solid rgba(120, 148, 188, 0.68)',
            background:
              'linear-gradient(to bottom, rgba(22, 30, 44, 0.22) 0%, rgba(16, 22, 34, 0.52) 55%, rgba(12, 16, 25, 0.78) 100%)',
            boxShadow: '0 -10px 18px rgba(0,0,0,0.42)',
          }}
        />

        {/* 币安风格：指标信息悬浮在图层上方，避免把图切成两段 */}
        <div className="absolute top-2 left-2 z-10 pointer-events-none max-w-[70%] rounded bg-black/35 px-2 py-1 backdrop-blur-sm">
          <div className="text-[10px] text-text-tertiary font-mono flex flex-wrap gap-x-2 gap-y-0.5">
            {barStats && (
              <>
                <span>开:{fmt(barStats.open)}</span>
                <span className="text-bull">高:{fmt(barStats.high)}</span>
                <span className="text-bear">低:{fmt(barStats.low)}</span>
                <span>收:{fmt(barStats.close)}</span>
              </>
            )}
            <span className="text-amber-400">MA5:{maSnapshot.ma5 ? fmt(maSnapshot.ma5) : '--'}</span>
            <span className="text-blue-400">MA10:{maSnapshot.ma10 ? fmt(maSnapshot.ma10) : '--'}</span>
            <span className="text-violet-400">MA20:{maSnapshot.ma20 ? fmt(maSnapshot.ma20) : '--'}</span>
          </div>
        </div>

        {ticker24h && (
          <div className="absolute top-2 right-2 z-10 pointer-events-none rounded bg-black/35 px-2 py-1 backdrop-blur-sm">
            <div className="text-[10px] text-text-tertiary font-mono flex items-center gap-2">
              <span className={ticker24h.priceChangePercent >= 0 ? 'text-bull' : 'text-bear'}>
                24h:{ticker24h.priceChangePercent >= 0 ? '+' : ''}{ticker24h.priceChangePercent.toFixed(2)}%
              </span>
              <span>量:{fmtCompact(ticker24h.volume)}</span>
              <span>额:{fmtCompact(ticker24h.quoteVolume)}</span>
            </div>
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-text-tertiary bg-surface-0/35">
            K线加载中...
          </div>
        )}
        {error && !loading && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-bear bg-surface-0/55 px-3 text-center">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

