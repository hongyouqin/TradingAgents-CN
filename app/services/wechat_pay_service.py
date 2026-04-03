import time
import random
import string
import json
import base64
import httpx
from typing import Dict, Any
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64
from app.core.config import settings
import logging

logger = logging.getLogger('wechat_pay')

class WeChatPayService:
    def __init__(self):
        self.app_id = settings.WECHAT_APP_ID
        self.mch_id = settings.WECHAT_MCH_ID
        self.api_v3_key = settings.WECHAT_API_V3_KEY
        self.mch_serial_no = settings.WECHAT_MCH_SERIAL_NO
        self.notify_url = settings.WECHAT_NOTIFY_URL
        self.api_base = "https://api.mch.weixin.qq.com"

        with open(settings.MCH_PRIVATE_KEY_PATH, 'r', encoding='utf-8') as f:
            self.private_key = load_pem_private_key(f.read().encode(), password=None)

    def _nonce(self):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

    def _sign(self, data: str):
        sign = self.private_key.sign(
            data.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(sign).decode()

    def _auth(self, method, path, body=""):
        ts = str(int(time.time()))
        nonce = self._nonce()
        sign_str = f"{method}\n{path}\n{ts}\n{nonce}\n{body}\n"
        sign = self._sign(sign_str)
        return f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mch_id}",nonce_str="{nonce}",timestamp="{ts}",serial_no="{self.mch_serial_no}",signature="{sign}"'

    async def _req(self, method, path, data=None):
        body = json.dumps(data) if data else ""
        headers = {
            "Authorization": self._auth(method, path, body),
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient() as c:
            r = await c.request(method, self.api_base + path, content=body.encode(), headers=headers)
        return r.json()

    async def jsapi_order(self, openid, out_trade_no, total_fee, desc, ip):
        data = {
            "appid": self.app_id, "mchid": self.mch_id,
            "description": desc, "out_trade_no": out_trade_no,
            "notify_url": self.notify_url,
            "amount": {"total": total_fee, "currency": "CNY"},
            "payer": {"openid": openid},
            "scene_info": {"payer_client_ip": ip}
        }
        return await self._req("POST", "/v3/pay/transactions/jsapi", data)

    async def native_order(self, out_trade_no, total_fee, desc, ip):
        data = {
            "appid": self.app_id, "mchid": self.mch_id,
            "description": desc, "out_trade_no": out_trade_no,
            "notify_url": self.notify_url,
            "amount": {"total": total_fee, "currency": "CNY"},
            "scene_info": {"payer_client_ip": ip}
        }
        return await self._req("POST", "/v3/pay/transactions/native", data)

    async def h5_order(self, out_trade_no, total_fee, desc, ip):
        data = {
            "appid": self.app_id, "mchid": self.mch_id,
            "description": desc, "out_trade_no": out_trade_no,
            "notify_url": self.notify_url,
            "amount": {"total": total_fee, "currency": "CNY"},
            "scene_info": {"payer_client_ip": ip, "h5_info": {"type": "Wap"}}
        }
        return await self._req("POST", "/v3/pay/transactions/h5", data)
    
    def decrypt_resource(self, resource):
        """
        微信支付 V3 回调资源解密（官方兼容版）
        """
        try:
            key = self.api_v3_key.encode("utf-8")
            nonce = resource["nonce"].encode("utf-8")
            ciphertext = base64.b64decode(resource["ciphertext"])
            associated_data = resource["associated_data"].encode("utf-8")

            # GCM 解密：拆分 认证标签 tag (16字节)
            tag = ciphertext[-16:]
            data = ciphertext[:-16]

            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
            decryptor = cipher.decryptor()
            decryptor.authenticate_additional_data(associated_data)
            
            plaintext = decryptor.update(data) + decryptor.finalize()
            return json.loads(plaintext.decode("utf-8"))
        
        except Exception as e:
            logger.error(f"解密失败: {str(e)}")
            raise Exception("回调解密失败")

    def jsapi_params(self, prepay_id):
        ts = str(int(time.time()))
        nonce = self._nonce()
        pkg = f"prepay_id={prepay_id}"
        
        # 👇 把这一行改成下面这样！！！
        # 错误：s = f"{self.app_id}\n{ts}\n{nonce}\n{prepay_id}\n"
        # 正确：
        s = f"{self.app_id}\n{ts}\n{nonce}\n{pkg}\n"

        sign = self._sign(s)
        return {
            "appId": self.app_id, "timeStamp": ts,
            "nonceStr": nonce, "package": pkg,
            "signType": "RSA", "paySign": sign
        }

    def decrypt(self, resource):
        nonce = base64.b64decode(resource["nonce"])
        ct = base64.b64decode(resource["ciphertext"])
        ad = resource["associated_data"].encode()
        aes = AESGCM(self.api_v3_key.encode())
        return json.loads(aes.decrypt(nonce, ct, ad).decode())

    def verify_notify(self, xml_data):
        try:
            data = json.loads(xml_data)
            if data.get("event_type") != "TRANSACTION.SUCCESS":
                return False, {}
            res = self.decrypt(data["resource"])
            return True, res
        except:
            return False, {}

    async def get_openid_by_code(self, code):
        url = "https://api.weixin.qq.com/sns/oauth2/access_token"
        params = {
            "appid": self.app_id,
            "secret": settings.WECHAT_APP_SECRET,
            "code": code, "grant_type": "authorization_code"
        }
        async with httpx.AsyncClient() as c:
            r = await c.get(url, params=params)
        return r.json()

wechat_pay_service = WeChatPayService()