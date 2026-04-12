import httpx
from datetime import datetime
from typing import Optional, Tuple, Dict

class WechatQRCodeService:
    def __init__(self, appid: str, appsecret: str):
        self.appid = appid
        self.appsecret = appsecret
        self.access_token = None
        self.token_expire_time = 0

    async def get_access_token(self) -> str:
        """获取公众号 access_token（缓存 2 小时）"""
        current_ts = int(datetime.now().timestamp())

        if self.access_token and current_ts < self.token_expire_time:
            return self.access_token

        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.appid,
            "secret": self.appsecret
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
            data = resp.json()

            if "access_token" not in data:
                raise Exception(f"获取 access_token 失败: {data}")

            self.access_token = data["access_token"]
            self.token_expire_time = current_ts + data["expires_in"] - 100
            return self.access_token

    async def create_temp_qrcode(self, scene_id: int, expire_seconds: int = 300):
        """生成临时二维码（默认5分钟过期）"""
        access_token = await self.get_access_token()
        url = f"{self.base_url}/qrcode/create?access_token={access_token}"
        
        data = {
            "expire_seconds": expire_seconds,
            "action_name": "QR_SCENE",
            "action_info": {"scene": {"scene_id": scene_id}}
        }
        
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data)
            return res.json()


    async def create_permanent_qrcode(self, scene_id: int) -> Dict:
        """
        创建永久带参数二维码
        scene_id: 数字场景值，必须唯一
        """
        access_token = await self.get_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/qrcode/create?access_token={access_token}"

        payload = {
            "action_name": "QR_LIMIT_SCENE",
            "action_info": {
                "scene": {
                    "scene_id": scene_id
                }
            }
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            return resp.json()

    @staticmethod
    def get_qrcode_image_url(ticket: str) -> str:
        """通过 ticket 获取二维码图片 URL"""
        return f"https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket={ticket}"