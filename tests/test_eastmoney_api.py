#!/usr/bin/env python3
"""
测试东方财富网新闻 API
"""

import json
import time
import requests
from urllib.parse import quote

def test_eastmoney_news(symbol="000001", limit=5):
    """
    测试东方财富网新闻接口
    
    Args:
        symbol: 股票代码
        limit: 返回数量
    """
    
    # 构建请求参数
    param = {
        "uid": "",
        "keyword": symbol,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": limit,
                "preTag": "<em>",
                "postTag": "</em>"
            }
        }
    }
    
    # 生成时间戳
    timestamp = int(time.time() * 1000)
    
    # 构建 URL
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": f"jQuery{timestamp}",
        "param": json.dumps(param),
        "_": str(timestamp)
    }
    
    # 请求头
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://search.eastmoney.com/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    
    print(f"📡 测试东方财富网新闻 API")
    print(f"股票代码: {symbol}")
    print(f"请求数量: {limit}")
    print(f"请求 URL: {url}")
    print(f"请求参数: {json.dumps(params, ensure_ascii=False, indent=2)}")
    print("-" * 60)
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        print(f"响应状态码: {response.status_code}")
        print(f"响应内容长度: {len(response.text)} 字符")
        print("-" * 60)
        
        if response.status_code == 200:
            # 解析 JSONP 响应
            text = response.text
            print(f"原始响应 (前500字符):")
            print(text[:500])
            print("-" * 60)
            
            # 提取 JSON 数据
            if text.startswith("jQuery"):
                json_str = text[text.find("(")+1:text.rfind(")")]
                data = json.loads(json_str)
                
                print("解析后的 JSON 结构:")
                print(json.dumps(data, ensure_ascii=False, indent=2)[:1000])
                print("-" * 60)
                
                # 提取新闻
                if "result" in data and "cmsArticleWebOld" in data["result"]:
                    articles = data["result"]["cmsArticleWebOld"]
                    print(f"✅ 成功获取 {len(articles)} 条新闻")
                    print("\n新闻列表:")
                    for i, article in enumerate(articles[:3], 1):
                        print(f"\n{i}. 标题: {article.get('title', 'N/A')}")
                        print(f"   时间: {article.get('date', 'N/A')}")
                        print(f"   来源: {article.get('source', 'N/A')}")
                        print(f"   链接: {article.get('url', 'N/A')}")
                        if article.get('content'):
                            content = article.get('content', '')[:100]
                            print(f"   内容: {content}...")
                    return data
                else:
                    print("❌ 响应数据中没有找到新闻")
                    return None
            else:
                print("❌ 响应格式不是 JSONP")
                return None
        else:
            print(f"❌ 请求失败: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return None

def test_multiple_stocks():
    """测试多个股票"""
    stocks = ["000001", "600519", "000858"]
    
    for stock in stocks:
        print("\n" + "=" * 60)
        test_eastmoney_news(stock, limit=3)
        time.sleep(1)  # 避免请求过快

if __name__ == "__main__":
    # 测试单个股票
    test_eastmoney_news("603986", limit=5)
    
    # 测试多个股票（可选）
    # test_multiple_stocks()