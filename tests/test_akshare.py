# 帮我实现ak.stock_zt_pool_em的测试用例

# import akshare as ak

# def test_ak_stock_zt_pool_em():
#     try:
#         df = ak.stock_zt_pool_em()
#         assert not df.empty, "返回的DataFrame为空"
#         assert "股票代码" in df.columns, "缺少'股票代码'列"
#         assert "股票名称" in df.columns, "缺少'股票名称'列"
#         assert "连板数" in df.columns, "缺少'连板数'列"
#         print("test_ak_stock_zt_pool_em: 测试通过")
#     except Exception as e:
#         print(f"test_ak_stock_zt_pool_em: 测试失败 - {e}")
        
# if __name__ == "__main__":
#     test_ak_stock_zt_pool_em()

import akshare as ak
from datetime import datetime, timedelta

# 获取最近交易日（简单判断，周末直接减2天）
def get_latest_trade_date():
    today = datetime.now()
    if today.weekday() == 5:     # 周六
        return (today - timedelta(days=1)).strftime("%Y%m%d")
    elif today.weekday() == 6:   # 周日
        return (today - timedelta(days=2)).strftime("%Y%m%d")
    else:
        return today.strftime("%Y%m%d")

date = get_latest_trade_date()
print("取交易日:", date)
df = ak.stock_zt_pool_em(date=date)
print("数据行数:", len(df))
print(df.head())