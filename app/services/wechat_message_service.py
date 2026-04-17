import logging
import httpx
from app.core.config import settings

logger = logging.getLogger('WechatMessageService')

class WechatMessageService:
    def __init__(self):
        self.appid = settings.WECHAT_APP_ID
        self.appsecret = settings.WECHAT_APP_SECRET
        self.access_token = None

    async def get_access_token(self):
        """获取微信公众号 access_token（线上服务器可用）"""
        if self.access_token:
            return self.access_token

        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.appid,
            "secret": self.appsecret
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            data = response.json()

        if "access_token" in data:
            self.access_token = data["access_token"]
            logger.info(f"获取 access_token 成功: {self.access_token[:10]}...")
            return self.access_token
        else:
            logger.error(f"获取 access_token 失败: {data}")
            raise Exception(f"获取access_token失败: {data}")

    async def send_template_msg(self, openid: str, template_id: str, data: dict, url: str = None):
        access_token = await self.get_access_token()
        api_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"

        payload = {
            "touser": openid,
            "template_id": template_id,
            "data": data
        }
        if url:
            payload["url"] = url

        async with httpx.AsyncClient() as client:
            resp = await client.post(api_url, json=payload)
            result = resp.json()
            logger.info(f"发送模板消息结果: {result}")
            return result

    async def send_analysis_result_notification(self, openid: str, task_id: str, symbol: str):
        if not openid:
            logger.warning("用户openid为空，不发送微信通知")
            return

        # 真实当前时间
        from datetime import datetime
        now = datetime.now().strftime("%Y年%m月%d日 %H:%M")

        # ✅ 你最新的模板ID
        TEMPLATE_ID = "l0BxhG1_4TLJZw2SFtnVHo3qn-FuX81oj3m13vMIPqc"

        # ✅ 100% 匹配你的新模板字段
        data = {
            "thing2": {"value": f"股票分析 {symbol}"},  # 产品名称
            "time5": {"value": now},                   # 完成时间
        }

        jump_url = f"https://nbstockai.com/reports/view/{task_id}"

        logger.info(f"发送微信通知 → 用户：{openid}，股票：{symbol}")
        logger.info(f"模板数据：{data}")

        return await self.send_template_msg(
            openid=openid,
            template_id=TEMPLATE_ID,
            data=data,
            url=jump_url
        )


wechat_message_service = WechatMessageService()