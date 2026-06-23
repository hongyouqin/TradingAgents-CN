
# app/services/tools/position_calc_tool.py
from typing import Dict, Any


class PositionCalcTool:
    """仓位规模计算器，资金管理公式
    公式：
    $R1 = EP - SP
    RpT = RCF × TC
    TS = RpT / $R1
    """

    def calc_trade_size(self, ep: float, sp: float, rcf: float, tc: float) -> Dict[str, Any]:
        """
        计算单笔交易最大可持仓数量
        :param ep: float 买入价格 EP
        :param sp: float 止损价格 SP
        :param rcf: float 风险控制系数，例0.02 = 总资金单笔最大亏损2%
        :param tc: float 账户总资金 TC
        :return: 完整分步计算结果字典
        """
        # 每股单笔风险
        r1 = ep - sp
        if r1 <= 0:
            raise ValueError(f"参数校验失败：止损价SP({sp})必须小于买入价EP({ep})，R1={r1}不合法")

        # 单笔允许最大亏损总金额
        rpt = rcf * tc
        # 理论可交易股数
        ts = rpt / r1
        # 实际可交易整数股（A股适配100股一手）
        ts_floor = int(ts)
        ts_lot = ts_floor // 100 * 100

        # 持仓总市值、最大亏损
        total_position_value = ts * ep
        max_loss = rpt

        return {
            "输入参数": {
                "EP_买入价": ep,
                "SP_止损价": sp,
                "RCF_风险系数": rcf,
                "TC_账户总资金": tc
            },
            "分步计算": {
                "R1_每股风险": round(r1, 4),
                "RpT_单笔最大允许亏损": round(rpt, 2),
                "TS_理论可买股数": round(ts, 2),
                "TS_整数股数": ts_floor,
                "TS_适配A股100手股数": ts_lot
            },
            "辅助指标": {
                "预估持仓总市值": round(total_position_value, 2),
                "极端行情最大亏损": round(max_loss, 2)
            }
        }