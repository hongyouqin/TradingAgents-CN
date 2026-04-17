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
            logger.warning("用户无openid，无法发送微信通知")
            return
        logger.info(f"准备发送分析结果通知给用户 {openid}，任务ID: {task_id}, 股票代码: {symbol}")

        TEMPLATE_ID = "3AF-CmgWE-NQevnHL02HLRmmhQwHZ4hMpvjoKFxaH2M"

        data = {
            "first": {"value": f"您的{symbol}分析报告已完成！"},
            "keyword1": {"value": symbol},
            "keyword2": {"value": "刚刚"},
            "character_string3": {"value": "完成"},  # 这里改短！
            "remark": {"value": "点击查看完整报告"}
        }

        jump_url = f"https://nbstockai.com/api/reports/view/{task_id}"

        return await self.send_template_msg(
            openid=openid,
            template_id=TEMPLATE_ID,
            data=data,
            url=jump_url
        )

wechat_message_service = WechatMessageService()