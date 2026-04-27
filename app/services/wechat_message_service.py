import logging
import httpx
from datetime import datetime, timedelta
from app.core.config import settings

logger = logging.getLogger('WechatMessageService')

class WechatMessageService:
    def __init__(self):
        self.appid = settings.WECHAT_APP_ID
        self.appsecret = settings.WECHAT_APP_SECRET
        self.access_token = None
        self.token_expire_time = datetime.min  # token 过期时间

    async def get_access_token(self):
        """
        获取微信公众号 access_token（自动处理过期 + 重试）
        微信 access_token 默认有效期 7200 秒
        """
        # 如果 token 存在且没过期，直接返回
        now = datetime.now()
        if self.access_token and now < self.token_expire_time:
            return self.access_token

        # 否则重新获取
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
            expires_in = data.get("expires_in", 7200)  # 有效期秒数
            self.token_expire_time = now + timedelta(seconds=expires_in - 60)  # 提前60秒过期，避免边界问题
            
            logger.info(f"获取 access_token 成功: {self.access_token[:10]}...，有效期至: {self.token_expire_time}")
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

        # ✅ 核心修复：自动检测 token 过期，清空后重试一次
        if result.get("errcode") == 42001:
            logger.warning("检测到 access_token 过期，自动刷新并重试发送...")
            self.access_token = None  # 清空旧token
            self.token_expire_time = datetime.min
            
            # 重试发送
            access_token = await self.get_access_token()
            api_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
            async with httpx.AsyncClient() as client:
                resp = await client.post(api_url, json=payload)
                result = resp.json()

        logger.info(f"发送模板消息结果: {result}")
        return result

    async def send_analysis_result_notification(self, openid: str, task_id: str, symbol: str):
        if not openid:
            logger.warning("用户openid为空，不发送微信通知")
            return

        now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        TEMPLATE_ID = "l0BxhG1_4TLJZw2SFtnVHo3qn-FuX81oj3m13vMIPqc"

        data = {
            "thing2": {"value": f"股票分析 {symbol}"},
            "time5": {"value": now},
        }

        # jump_url = f"https://nbstockai.com/reports/view/{task_id}"
        jump_url = f"https://nbstockai.com/analysis/{task_id}"
        logger.info(f"发送微信通知 → 用户：{openid}，股票：{symbol}")
        return await self.send_template_msg(
            openid=openid,
            template_id=TEMPLATE_ID,
            data=data,
            url=jump_url
        )


wechat_message_service = WechatMessageService()