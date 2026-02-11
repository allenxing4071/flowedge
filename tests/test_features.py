"""
FlowEdge 特征计算单元测试
覆盖 ring_buffer、CVD、OFI、VPIN、LargeTrade 核心逻辑。
"""

import time
import pytest
import numpy as np

from flowedge.features.ring_buffer import RingBuffer
from flowedge.features.cvd import CVDCalculator
from flowedge.features.ofi import OFICalculator
from flowedge.features.vpin import VPINCalculator
from flowedge.features.large_trade import LargeTradeDetector


# ─── RingBuffer 测试 ───

class TestRingBuffer:
    """环形缓冲基础功能测试"""

    def test_empty(self):
        rb = RingBuffer(capacity=10)
        assert rb.size == 0
        assert rb.sum() == 0.0
        assert rb.mean() == 0.0
        assert rb.last() == 0.0

    def test_push_and_sum(self):
        rb = RingBuffer(capacity=5)
        for i in range(1, 4):
            rb.push(float(i), timestamp_ms=i * 1000)
        assert rb.size == 3
        assert rb.sum() == 6.0  # 1+2+3
        assert rb.mean() == 2.0

    def test_overflow_ring(self):
        """超过容量后，旧数据被覆盖，sum 保持正确"""
        rb = RingBuffer(capacity=3)
        for i in range(1, 6):  # push 1,2,3,4,5
            rb.push(float(i), timestamp_ms=i * 1000)
        assert rb.size == 3
        # 缓冲中应为 3,4,5
        assert rb.sum() == 12.0  # 3+4+5
        assert rb.last() == 5.0

    def test_window_sum(self):
        """时间窗口求和"""
        rb = RingBuffer(capacity=100)
        base_ts = 1000000
        for i in range(10):
            rb.push(10.0, timestamp_ms=base_ts + i * 100)  # 每 100ms 一条
        # 最近 500ms 的数据：应有 5 条
        since = base_ts + 500
        total = rb.window_sum(since)
        assert total == 50.0  # 5 * 10

    def test_window_count(self):
        """时间窗口计数"""
        rb = RingBuffer(capacity=100)
        base_ts = 1000000
        for i in range(10):
            rb.push(1.0, timestamp_ms=base_ts + i * 100)
        count = rb.window_count(base_ts + 500)
        assert count == 5

    def test_recent_values(self):
        """获取最近 N 条数据"""
        rb = RingBuffer(capacity=5)
        for i in range(1, 6):
            rb.push(float(i))
        vals = rb.recent_values(3)
        np.testing.assert_array_equal(vals, [3.0, 4.0, 5.0])

    def test_clear(self):
        """清空缓冲"""
        rb = RingBuffer(capacity=5)
        for i in range(3):
            rb.push(float(i))
        rb.clear()
        assert rb.size == 0
        assert rb.sum() == 0.0


# ─── CVD 测试 ───

class TestCVD:
    """CVD 累计成交量偏差测试"""

    def test_pure_buy(self):
        """纯买入，CVD 应为正"""
        cvd = CVDCalculator()
        now_ms = int(time.time() * 1000)
        for i in range(10):
            cvd.on_trade(1000.0, is_taker_buy=True, timestamp_ms=now_ms + i * 10)
        snap = cvd.snapshot()
        assert snap.cvd_total == 10000.0
        assert snap.cvd_1m > 0
        assert snap.buy_vol_1m == 10000.0
        assert snap.sell_vol_1m == 0.0

    def test_pure_sell(self):
        """纯卖出，CVD 应为负"""
        cvd = CVDCalculator()
        now_ms = int(time.time() * 1000)
        for i in range(10):
            cvd.on_trade(500.0, is_taker_buy=False, timestamp_ms=now_ms + i * 10)
        snap = cvd.snapshot()
        assert snap.cvd_total == -5000.0

    def test_balanced(self):
        """买卖平衡，CVD 趋近 0"""
        cvd = CVDCalculator()
        now_ms = int(time.time() * 1000)
        for i in range(10):
            cvd.on_trade(1000.0, is_taker_buy=True, timestamp_ms=now_ms + i * 20)
            cvd.on_trade(1000.0, is_taker_buy=False, timestamp_ms=now_ms + i * 20 + 10)
        snap = cvd.snapshot()
        assert snap.cvd_total == 0.0

    def test_trade_count(self):
        """成交笔数统计"""
        cvd = CVDCalculator()
        now_ms = int(time.time() * 1000)
        for i in range(5):
            cvd.on_trade(100.0, is_taker_buy=True, timestamp_ms=now_ms + i * 10)
        snap = cvd.snapshot()
        assert snap.trade_count_1m == 5


# ─── OFI 测试 ───

class TestOFI:
    """OFI 订单流不平衡测试"""

    def test_first_update_no_ofi(self):
        """第一次更新不产生 OFI（没有 prev 数据）"""
        ofi = OFICalculator()
        now_ms = int(time.time() * 1000)
        bids = [[100.0, 10.0], [99.0, 20.0], [98.0, 30.0], [97.0, 40.0], [96.0, 50.0]]
        asks = [[101.0, 10.0], [102.0, 20.0], [103.0, 30.0], [104.0, 40.0], [105.0, 50.0]]
        ofi.on_depth_update(bids, asks, now_ms)
        snap = ofi.snapshot()
        assert snap.ofi_10s == 0.0

    def test_bid_increase(self):
        """买方挂单增加，OFI 应为正"""
        ofi = OFICalculator(levels=3)
        now_ms = int(time.time() * 1000)

        bids_1 = [[100.0, 10.0], [99.0, 10.0], [98.0, 10.0]]
        asks_1 = [[101.0, 10.0], [102.0, 10.0], [103.0, 10.0]]
        ofi.on_depth_update(bids_1, asks_1, now_ms)

        # bid 全部增加 5，ask 不变 → OFI = 15 - 0 = 15
        bids_2 = [[100.0, 15.0], [99.0, 15.0], [98.0, 15.0]]
        asks_2 = [[101.0, 10.0], [102.0, 10.0], [103.0, 10.0]]
        ofi.on_depth_update(bids_2, asks_2, now_ms + 100)

        snap = ofi.snapshot()
        assert snap.ofi_instant == 15.0
        assert snap.ofi_10s == 15.0

    def test_ask_increase(self):
        """卖方挂单增加，OFI 应为负"""
        ofi = OFICalculator(levels=3)
        now_ms = int(time.time() * 1000)

        bids_1 = [[100.0, 10.0], [99.0, 10.0], [98.0, 10.0]]
        asks_1 = [[101.0, 10.0], [102.0, 10.0], [103.0, 10.0]]
        ofi.on_depth_update(bids_1, asks_1, now_ms)

        # ask 全部增加 5，bid 不变 → OFI = 0 - 15 = -15
        bids_2 = [[100.0, 10.0], [99.0, 10.0], [98.0, 10.0]]
        asks_2 = [[101.0, 15.0], [102.0, 15.0], [103.0, 15.0]]
        ofi.on_depth_update(bids_2, asks_2, now_ms + 100)

        snap = ofi.snapshot()
        assert snap.ofi_instant == -15.0


# ─── VPIN 测试 ───

class TestVPIN:
    """VPIN 知情交易概率测试"""

    def test_empty(self):
        vpin = VPINCalculator(bucket_size=1000, num_buckets=5)
        snap = vpin.snapshot()
        assert snap.vpin == 0.0
        assert snap.buckets_filled == 0

    def test_pure_buy_vpin(self):
        """纯买入，VPIN 应接近 1"""
        vpin = VPINCalculator(bucket_size=1000, num_buckets=5)
        # 填充 5 个桶，全部是买入
        for _ in range(5):
            vpin.on_trade(1000.0, is_taker_buy=True)
        snap = vpin.snapshot()
        assert snap.vpin >= 0.9  # 应接近 1.0
        assert snap.buckets_filled == 5

    def test_balanced_vpin(self):
        """买卖平衡，VPIN 应接近 0"""
        vpin = VPINCalculator(bucket_size=1000, num_buckets=5)
        for _ in range(5):
            vpin.on_trade(500.0, is_taker_buy=True)
            vpin.on_trade(500.0, is_taker_buy=False)
        snap = vpin.snapshot()
        assert snap.vpin < 0.1  # 应接近 0

    def test_bucket_fill(self):
        """桶填充进度"""
        vpin = VPINCalculator(bucket_size=1000, num_buckets=5)
        vpin.on_trade(300.0, is_taker_buy=True)
        snap = vpin.snapshot()
        assert snap.current_bucket_fill == pytest.approx(0.3, abs=0.01)


# ─── LargeTrade 测试 ───

class TestLargeTrade:
    """大单检测测试"""

    def test_small_trade_ignored(self):
        """小单不触发"""
        det = LargeTradeDetector(threshold_usdt=50000)
        now_ms = int(time.time() * 1000)
        result = det.on_trade(price=50000, qty_usdt=10000, is_taker_buy=True, timestamp_ms=now_ms)
        assert result is None

    def test_large_trade_detected(self):
        """大单被检测到"""
        det = LargeTradeDetector(threshold_usdt=50000)
        now_ms = int(time.time() * 1000)
        result = det.on_trade(price=50000, qty_usdt=100000, is_taker_buy=True, timestamp_ms=now_ms)
        assert result is not None
        assert result.qty_usdt == 100000
        assert result.is_taker_buy is True

    def test_window_stats(self):
        """窗口统计"""
        det = LargeTradeDetector(threshold_usdt=50000, window_ms=30000)
        now_ms = int(time.time() * 1000)
        det.on_trade(price=50000, qty_usdt=80000, is_taker_buy=True, timestamp_ms=now_ms)
        det.on_trade(price=50000, qty_usdt=60000, is_taker_buy=False, timestamp_ms=now_ms + 100)
        snap = det.snapshot()
        assert snap.count_30s == 2
        assert snap.buy_count_30s == 1
        assert snap.sell_count_30s == 1
        assert snap.net_flow_30s == 20000.0  # 80000 - 60000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
