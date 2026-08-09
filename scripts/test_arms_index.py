"""
全A阿姆氏指标 (Arms Index / TRIN) 本地测试 + 可视化

用法:
    python scripts/test_arms_index.py

依赖:
    pip install matplotlib pandas mplcursors

优化修复：
    ✅ 移除log坐标轴，均线波动清晰可见
    ✅ 均线粗细分层：周期越长线条越粗，MA21更醒目
    ✅ 统一均线计算（前端唯一数据源，消除前后端不一致）
    ✅ 严格rolling窗口，禁止min_periods伪均值，必须满窗口才输出均线
    ✅ 保留X轴左右留白，避免最新日期被边界遮挡
    ✅ 支持切换 SMA / EMA（推荐EMA用于TRIN）
"""
import asyncio
import logging
import sys
from datetime import datetime
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 加入项目根目录
sys.path.insert(0, ".")


# ====================== 异步函数：只查询数据，不绘图 ======================
async def fetch_arms_data():
    # 初始化 MongoDB 连接
    from app.core.database import init_database, get_mongo_db
    from app.services.arms_index_service import ArmsIndexService

    await init_database()
    svc = ArmsIndexService()

    logger.info("=" * 60)
    logger.info("📊 全A阿姆氏指标 (Arms Index / TRIN)")
    logger.info("=" * 60)

    # 后端拉取180交易日，留足缓冲，防止部分日期缺失
    result = await svc.get_arms_index(days=240, periods=[5, 10, 21], include_realtime=True)
    logger.info(f"🔍 后端返回原始TRIN记录条数：{len(result.get('daily',[]))}")
    return result


# ====================== 同步绘图函数（不在协程内！） ======================
def draw_chart(result):
    from datetime import timedelta, datetime
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import mplcursors
    import pandas as pd

    daily = result.get("daily", [])
    # ========= 修改绘图展示天数：120个交易日，保证MA21有足够长有效曲线 =========
    max_display_days = 120
    daily = daily[:max_display_days]

    realtime_included = result.get("realtime_included", False)

    if not daily:
        logger.error("❌ 未获取到 TRIN 数据")
        return

    # 调试关键打印
    logger.info(f"✅ 绘图接收数据，最新日期 = {daily[0]['trade_date']}")
    logger.info(f"\n📅 绘图实际展示交易日数量: {len(daily)} 天")
    logger.info(f"🔄 实时合并: {'✅ 是' if realtime_included else '❌ 否（非交易日或数据未就绪）'}")

    # 打印最新一天详情
    latest = daily[0]
    logger.info(f"\n📌 最新日期: {latest['trade_date']}")
    logger.info(f"   📈 上涨家数: {latest['advance_count']}  |  下跌家数: {latest['decline_count']}")
    logger.info(f"   📊 TRIN: {latest['trin']:.4f}  |  Breadth Ratio: {latest['breadth_ratio']:.4f}")

    # ---------- 打印最近 21 天明细 ----------
    logger.info(f"\n📋 最近 21 交易日明细:")
    logger.info(f"   {'日期':<12} {'上涨':<6} {'下跌':<6} {'涨量(亿)':<12} {'跌量(亿)':<12} {'TRIN':<8} {'宽度比':<8}")
    logger.info(f"   {'-'*70}")
    for d in daily[:21]:
        trin_str = f"{d['trin']:.4f}"
        br_str = f"{d['breadth_ratio']:.4f}"
        logger.info(f"   {d['trade_date']:<12} {d['advance_count']:<6} {d['decline_count']:<6} "
                    f"{d['advance_volume']/1e8:<12.2f} {d['decline_volume']/1e8:<12.2f} "
                    f"{trin_str:<8} {br_str:<8}")

    # ==================== Matplotlib 绘图 ====================
    try:
        matplotlib.use("TkAgg")
    except Exception as e:
        logger.warning(f"⚠️ TkAgg后端不可用，尝试默认后端: {e}")

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    # 准备数据（原始：新日期在前，反转升序：旧→新）
    daily_sorted = list(reversed(daily))
    dates = []
    trin_vals = []
    adv_cnt = []
    dec_cnt = []
    adv_vol = []
    dec_vol = []
    breadth_vals = []
    raw_data_cache = []

    for d in daily_sorted:
        trade_date = d["trade_date"]
        dt = datetime.strptime(trade_date, "%Y%m%d")
        dates.append(dt)
        trin_vals.append(d["trin"])
        adv_cnt.append(d["advance_count"])
        dec_cnt.append(d["decline_count"])
        adv_vol.append(d["advance_volume"])
        dec_vol.append(d["decline_volume"])
        breadth_vals.append(d["breadth_ratio"])
        raw_data_cache.append(d)

    trin_series = pd.Series(trin_vals)

    # ==================== 均线计算【严格满窗口，禁止min_periods伪均值】 ====================
    # 方案1：SMA简单均线（原版逻辑，必须凑够窗口长度才输出有效值，前面为NaN）
    ma5 = trin_series.rolling(window=5).mean()
    ma10 = trin_series.rolling(window=10).mean()
    ma21 = trin_series.rolling(window=21).mean()

    # 【可选切换为EMA指数均线，TRIN指标推荐使用，取消下方注释即可】
    # ma5 = trin_series.ewm(span=5, adjust=False).mean()
    # ma10 = trin_series.ewm(span=10, adjust=False).mean()
    # ma21 = trin_series.ewm(span=21, adjust=False).mean()

    fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)

    # ========== 子图1 TRIN曲线【线条粗细优化：周期越长越粗】 ==========
    ax1 = axes[0]
    # TRIN日线：蓝色圆点主线
    line_trin, = ax1.plot(dates, trin_vals, "o-", color="#2196F3", linewidth=2.2, markersize=4, zorder=5, label="TRIN(日值)")
    # MA5 橙色
    line_ma5, = ax1.plot(dates, ma5, "-", color="#FF8800", linewidth=1.5, zorder=4, label="MA5")
    # MA10 深绿色
    line_ma10, = ax1.plot(dates, ma10, "-", color="#009933", linewidth=1.8, zorder=4, label="MA10")
    # MA21 紫色【最粗，突出长期均线】
    line_ma21, = ax1.plot(dates, ma21, "-", color="#8822BB", linewidth=2.3, zorder=4, label="MA21")

    # TRIN基准线
    ax1.axhline(y=1.0, color="black", linestyle="-", linewidth=1.4, alpha=0.75, zorder=6, label="TRIN=1.0(中性)")
    ax1.axhline(y=1.5, color="#c0392b", linestyle="--", linewidth=1, alpha=0.5, label="警戒1.5")
    ax1.axhline(y=0.7, color="#27ae60", linestyle="--", linewidth=1, alpha=0.5, label="警戒0.7")

    ax1.fill_between(dates, 1.0, trin_vals, where=[v > 1.0 for v in trin_vals], color="#F44336", alpha=0.12, zorder=1)
    ax1.fill_between(dates, 1.0, trin_vals, where=[v < 1.0 for v in trin_vals], color="#4CAF50", alpha=0.12, zorder=1)

    ax1.set_ylabel("TRIN 阿姆氏指标")
    ax1.set_title("全A阿姆氏指标 (Arms Index / TRIN)", fontsize=14, fontweight="bold")
    leg1 = ax1.legend(loc="upper left", ncol=6, fontsize=9)
    leg1.get_frame().set_alpha(0.8)
    ax1.grid(True, alpha=0.3)
    # 放宽Y轴范围，不要硬锁死0.3‑3.0，让均线波动更容易观察
    ax1.set_ylim(0.1, 4.0)
    # ✅ 关闭对数坐标，修复均线视觉不明显问题
    # ax1.set_yscale("log")

    # ========== 子图2 涨跌家数柱状 + 成交量曲线 ==========
    ax2 = axes[1]
    bar_width = timedelta(hours=10)
    date_left = [d - bar_width/2 for d in dates]
    date_right = [d + bar_width/2 for d in dates]

    ax2.bar(date_left, adv_cnt, width=bar_width, color="#4CAF50", alpha=0.7, label="上涨家数")
    ax2.bar(date_right, dec_cnt, width=bar_width, color="#F44336", alpha=0.7, label="下跌家数")

    ax2.set_ylabel("股票家数")
    ax2.set_title("全A每日涨跌家数", fontsize=12)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, alpha=0.3, axis="y")

    # 成交量双轴曲线
    ax2b = ax2.twinx()
    ax2b.plot(dates, [v / 1e8 for v in adv_vol], "-", color="#66BB6A", linewidth=1.5, alpha=0.6, label="上涨量(亿)")
    ax2b.plot(dates, [v / 1e8 for v in dec_vol], "-", color="#EF5350", linewidth=1.5, alpha=0.6, label="下跌量(亿)")
    ax2b.set_ylabel("成交量 (亿)", fontsize=9)
    ax2b.legend(loc="upper right", fontsize=8)

    # ========== 子图3 涨跌比 ==========
    ax3 = axes[2]
    line_br, = ax3.plot(dates, breadth_vals, "o-", color="#9C27B0", linewidth=1.5, markersize=3, label="涨跌比")
    ax3.axhline(y=0.5, color="black", linestyle="-", linewidth=1.2, alpha=0.7, label="0.5(中性)")
    ax3.fill_between(dates, 0.5, breadth_vals, where=[v > 0.5 for v in breadth_vals], color="#4CAF50", alpha=0.15)
    ax3.fill_between(dates, 0.5, breadth_vals, where=[v < 0.5 for v in breadth_vals], color="#F44336", alpha=0.15)
    ax3.set_ylabel("Breadth Ratio")
    ax3.set_title("全A涨跌比 (上涨家数/总家数)", fontsize=12)
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(True, alpha=0.3)
    ax3.set_ylim(0, 1)

    # X轴日期格式化
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    axes[2].xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(axes[2].xaxis.get_majorticklabels(), rotation=45, ha="right")

    # ===== 拓宽X轴左右空白，避免最后一日紧贴边框视觉丢失 =====
    all_num_dates = [mdates.date2num(dt) for dt in dates]
    if all_num_dates:
        left_pad = min(all_num_dates) - 0.5
        right_pad = max(all_num_dates) + 0.5
        for ax in axes:
            ax.set_xlim(left_pad, right_pad)

    plt.tight_layout()

    # ====================== 悬浮气泡提示 ======================
    cursor = mplcursors.cursor(
        [line_trin, line_br],
        hover=True,
        highlight=True
    )

    @cursor.connect("add")
    def on_hover(sel):
        idx = int(sel.index)
        if 0 <= idx < len(raw_data_cache):
            item = raw_data_cache[idx]
            text = (
                f"日期: {item['trade_date']}\n"
                f"TRIN = {item['trin']:.4f}\n"
                f"上涨:{item['advance_count']} 下跌:{item['decline_count']}\n"
                f"上涨量:{item['advance_volume']/1e8:.2f}亿\n"
                f"下跌量:{item['decline_volume']/1e8:.2f}亿\n"
                f"涨跌比:{item['breadth_ratio']:.4f}"
            )
            sel.annotation.set_text(text)
            sel.annotation.get_bbox_patch().set(facecolor="white", alpha=0.85)
            sel.annotation.set_fontsize(9)

    plt.show(block=True)


if __name__ == "__main__":
    # 第一步：异步拉取数据
    data_result = asyncio.run(fetch_arms_data())
    # 第二步：主线程同步绘图
    draw_chart(data_result)