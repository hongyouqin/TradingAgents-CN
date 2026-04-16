# 添加项目根目录到Python路径
import asyncio
import os
import sys


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 测试微信消息通知功能
from app.services.wechat_message_service import wechat_message_service

async def test_wechat_message_notification():
    WECHAT_APP_ID="xxx"
    WECHAT_APP_SECRET="xxx"
    wechat_message_service.appid = WECHAT_APP_ID  # 替换为你的测试微信AppID
    wechat_message_service.appsecret = WECHAT_APP_SECRET  # 替换为你的测试微信AppSecret
    result = await wechat_message_service.send_analysis_result_notification(
            openid="o0vq63CtIXqko7i4a6EMPYQeCZ8o",
            task_id="26a2fd37-3b61-498c-9bf9-d49c9aa1a4b9",
            symbol="603906"
        )  
    print(result)

if __name__ == "__main__":
  asyncio.run(test_wechat_message_notification())
    