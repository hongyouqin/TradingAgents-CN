import hashlib
import time
import random
import string
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, Tuple
import httpx
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import settings

try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('wechat_pay_service')


class WeChatPayService:
    """微信支付服务（RSA 签名版本）"""
    
    def __init__(self):
        self.app_id = settings.WECHAT_APP_ID
        self.mch_id = settings.WECHAT_MCH_ID
        self.api_key = settings.WECHAT_API_KEY  # APIv2 密钥（用于统一下单签名）
        self.api_v3_key = settings.WECHAT_API_V3_KEY  # APIv3 密钥
        self.mch_serial_no = settings.WECHAT_MCH_SERIAL_NO  # 商户证书序列号
        self.notify_url = settings.WECHAT_NOTIFY_URL
        self.api_base = "https://api.mch.weixin.qq.com"
        self.oauth_base = "https://api.weixin.qq.com"
        
        mch_private_key_path = settings.MCH_PRIVATE_KEY_PATH
        with open(mch_private_key_path, "r", encoding="utf-8") as f:
            # 商户私钥（PEM 格式）
            self.mch_private_key = f.read()
    
    def _generate_nonce_str(self) -> str:
        """生成随机字符串，不长于32位"""
        return ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    
    def _sign_md5(self, params: Dict) -> str:
        """
        生成 MD5 签名（用于统一下单 APIv2）
        """
        # 过滤空值
        filtered_params = {k: v for k, v in params.items() if v is not None and v != ''}
        # 按照 key 排序
        sorted_params = sorted(filtered_params.items())
        # 拼接字符串
        sign_str = '&'.join([f"{k}={v}" for k, v in sorted_params])
        sign_str += f"&key={self.api_key}"
        # MD5 加密并转大写
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
    
    def _sign_rsa(self, app_id: str, timestamp: str, nonce_str: str, prepay_id: str) -> str:
        """
        生成 JSAPI 调起支付签名（RSA 签名）
        
        符合微信官方文档：https://pay.weixin.qq.com/doc/v3/partner/4012365867
        
        Args:
            app_id: 公众号 AppID
            timestamp: 时间戳（秒级字符串）
            nonce_str: 随机字符串
            prepay_id: 统一下单返回的 prepay_id
        
        Returns:
            Base64 编码的签名
        """
        # 1. 构造签名串（注意：每行末尾必须有 \n，最后一行也有 \n）
        signature_str = f"{app_id}\n{timestamp}\n{nonce_str}\n{prepay_id}\n"
        
        # 2. 加载商户私钥
        private_key = serialization.load_pem_private_key(
            self.mch_private_key.encode('utf-8'),
            password=None
        )
        
        # 3. RSA-SHA256 签名
        signature = private_key.sign(
            signature_str.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        
        # 4. Base64 编码
        return base64.b64encode(signature).decode('utf-8')
 
    def _to_xml(self, params: Dict) -> str:
        """字典转 XML"""
        xml = ['<xml>']
        for k, v in params.items():
            if v is not None:
                xml.append(f'<{k}><![CDATA[{v}]]></{k}>')
        xml.append('</xml>')
        return ''.join(xml)
    
    def _parse_xml(self, xml_str: str) -> Dict:
        """XML 转字典"""
        try:
            root = ET.fromstring(xml_str)
            return {child.tag: child.text for child in root}
        except Exception as e:
            logger.error(f"解析 XML 失败: {e}")
            return {}
    
    async def _post(self, url: str, data: Dict) -> Dict:
        """发送 POST 请求"""
        xml_data = self._to_xml(data)
        logger.debug(f"请求 URL: {url}")
        logger.debug(f"请求数据: {xml_data}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, 
                content=xml_data.encode('utf-8'),
                headers={'Content-Type': 'text/xml'}
            )
            logger.debug(f"响应数据: {response.text}")
            return self._parse_xml(response.text)
    
    # ==================== 统一下单（APIv2） ====================
    
    async def unified_order_v3(
        self,
        out_trade_no: str,
        total_fee: int,
        body: str,
        trade_type: str,
        openid: str = None,
        spbill_create_ip: str = None,
        scene_info: dict = None
    ) -> Dict:
        """
        微信支付 V3 统一下单（通用版）
        支持：JSAPI / NATIVE / MWEB
        兼容老接口参数，直接替换即可
        """
        url = "https://api.mch.weixin.qq.com/v3/pay/transactions/jsapi"
        path = "/v3/pay/transactions/jsapi"

        # 基础参数
        data = {
            "appid": self.app_id,
            "mchid": self.mch_id,
            "description": body,
            "out_trade_no": out_trade_no,
            "notify_url": self.notify_url,
            "amount": {
                "total": total_fee,
                "currency": "CNY"
            }
        }

        # 不同支付类型
        if trade_type == "JSAPI":
            data["payer"] = {"openid": openid}

        elif trade_type == "NATIVE":
            url = "https://api.mch.weixin.qq.com/v3/pay/transactions/native"
            path = "/v3/pay/transactions/native"

        elif trade_type == "MWEB":
            url = "https://api.mch.weixin.qq.com/v3/pay/transactions/h5"
            path = "/v3/pay/transactions/h5"
            data["scene_info"] = {
                "payer_client_ip": spbill_create_ip or "127.0.0.1"
            }

        # V3 请求头 + 发送请求
        headers = self._build_v3_header("POST", path, data)
        result = await self._post_json(url, data, headers)

        # 统一格式化返回值（兼容老代码）
        if trade_type == "JSAPI":
            return {"prepay_id": result["prepay_id"]}
        elif trade_type == "NATIVE":
            return {"code_url": result["code_url"]}
        elif trade_type == "MWEB":
            return {"mweb_url": result["url"]}

        return result
    
    async def unified_order(
        self,
        out_trade_no: str,
        total_fee: int,
        body: str,
        trade_type: str,
        openid: str = None,
        spbill_create_ip: str = None
    ) -> Dict:
        """
        统一下单接口（APIv2，返回 prepay_id）
        
        Args:
            out_trade_no: 商户订单号
            total_fee: 总金额（单位：分）
            body: 商品描述
            trade_type: 交易类型（JSAPI/NATIVE/H5）
            openid: 用户 openid（JSAPI 必填）
            spbill_create_ip: 用户端 IP
        
        Returns:
            微信返回的 XML 解析后的字典，包含 prepay_id
        """
        params = {
            'appid': self.app_id,
            'mch_id': self.mch_id,
            'nonce_str': self._generate_nonce_str(),
            'body': body,
            'out_trade_no': out_trade_no,
            'total_fee': total_fee,
            'spbill_create_ip': spbill_create_ip or '127.0.0.1',
            'notify_url': self.notify_url,
            'trade_type': trade_type,
        }
        
        if trade_type == 'JSAPI' and openid:
            params['openid'] = openid
        
        # 生成 MD5 签名（统一下单使用 MD5）
        params['sign'] = self._sign_md5(params)
        
        # 发送请求
        result = await self._post(f"{self.api_base}/pay/unifiedorder", params)
        
        # 检查返回结果
        if result.get('return_code') != 'SUCCESS':
            logger.error(f"统一下单失败: {result.get('return_msg')}")
            raise Exception(f"统一下单失败: {result.get('return_msg', '未知错误')}")
        
        if result.get('result_code') != 'SUCCESS':
            logger.error(f"统一下单业务失败: {result.get('err_code_des')}")
            raise Exception(f"统一下单失败: {result.get('err_code_des', '业务错误')}")
        
        return result
    
    # ==================== JSAPI 支付专用方法（RSA 签名） ====================
    
    def generate_jsapi_params(self, prepay_id: str) -> Dict[str, str]:
        """
        生成 JSAPI 调起支付参数（RSA 签名版本）
        
        符合微信支付文档：https://pay.weixin.qq.com/doc/v3/partner/4012365867
        """
        timestamp = str(int(time.time()))           # 时间戳（秒级）
        nonce_str = self._generate_nonce_str()      # 随机字符串
        package = f"prepay_id={prepay_id}"          # 订单扩展字符串
        
        # 签名值
        pay_sign = self._sign_rsa(
            app_id=self.app_id,
            timestamp=timestamp,
            nonce_str=nonce_str,
            prepay_id=prepay_id   # 注意：签名用 prepay_id，不是 package
        )
        
        return {
            "appId": self.app_id,
            "timeStamp": timestamp,
            "nonceStr": nonce_str,
            "package": package,
            "signType": "RSA",
            "paySign": pay_sign
        }
  
    async def get_jsapi_params(
        self,
        openid: str,
        out_trade_no: str,
        total_fee: int,
        description: str,
        spbill_create_ip: str = None
    ) -> Dict[str, Any]:
        """
        获取 JSAPI 调起支付参数（完整流程）
        
        Args:
            openid: 用户 openid（微信授权获取）
            out_trade_no: 商户订单号
            total_fee: 金额（单位：分）
            description: 商品描述
            spbill_create_ip: 用户端 IP
        
        Returns:
            JSAPI 调起支付参数
        """
        # 1. 统一下单获取 prepay_id
        unified_result = await self.unified_order(
            out_trade_no=out_trade_no,
            total_fee=total_fee,
            body=description,
            trade_type="JSAPI",
            openid=openid,
            spbill_create_ip=spbill_create_ip
        )
        
        prepay_id = unified_result.get('prepay_id')
        if not prepay_id:
            raise Exception("获取 prepay_id 失败")
        
        # 2. 生成调起支付参数
        return self.generate_jsapi_params(prepay_id)
    
    # ==================== 支付回调验证 ====================
    
    def verify_notify(self, xml_data: str) -> Tuple[bool, Dict]:
        """
        验证支付回调通知
        
        Args:
            xml_data: 微信回调的 XML 数据
        
        Returns:
            (是否成功, 解析后的数据)
        """
        try:
            data = self._parse_xml(xml_data)
            
            # 验证签名
            sign = data.pop('sign', '')
            if not sign:
                return False, {'return_msg': '缺少签名'}
            
            # 重新计算签名
            calculated_sign = self._sign_md5(data)
            
            if calculated_sign != sign:
                logger.error(f"签名验证失败: 计算签名={calculated_sign}, 微信签名={sign}")
                return False, {'return_msg': '签名验证失败'}
            
            # 验证返回码
            if data.get('return_code') != 'SUCCESS':
                return False, {'return_msg': data.get('return_msg', '支付失败')}
            
            if data.get('result_code') != 'SUCCESS':
                return False, {'return_msg': data.get('err_code_des', '支付失败')}
            
            # 恢复 sign 字段
            data['sign'] = sign
            return True, data
            
        except Exception as e:
            logger.error(f"验证回调失败: {e}")
            return False, {'return_msg': str(e)}
    
    # ==================== 获取 openid 相关 ====================
    
    async def get_openid_by_code(self, code: str) -> Dict[str, Any]:
        """
        通过微信授权 code 获取 openid
        
        Args:
            code: 微信授权回调携带的 code
        
        Returns:
            {
                "openid": "oUpF8uMuAJO_M2pxb1Q9zNjWeS6o",
                "access_token": "xxx",
                "expires_in": 7200,
                "refresh_token": "xxx",
                "scope": "snsapi_base"
            }
        """
        url = f"{self.oauth_base}/sns/oauth2/access_token"
        params = {
            "appid": self.app_id,
            "secret": settings.WECHAT_APP_SECRET,
            "code": code,
            "grant_type": "authorization_code"
        }
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params)
                data = resp.json()
            
            if "openid" not in data:
                logger.error(f"获取 openid 失败: {data}")
                raise Exception(f"微信授权失败: {data.get('errmsg', '未知错误')}")
            
            return data
        except Exception as e:
            logger.error(f"调用微信获取 openid 异常: {e}")
            raise
    
    def get_oauth_url(self, redirect_uri: str, state: str = "", scope: str = "snsapi_base") -> str:
        """
        生成微信网页授权 URL
        
        Args:
            redirect_uri: 授权后回调地址
            state: 状态参数，可以传订单号或页面地址
            scope: 授权范围（snsapi_base 静默授权 / snsapi_userinfo 需要用户授权）
        
        Returns:
            授权 URL
        """
        import urllib.parse
        redirect_uri_encoded = urllib.parse.quote(redirect_uri)
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?appid={self.app_id}&redirect_uri={redirect_uri_encoded}&response_type=code&scope={scope}&state={state}#wechat_redirect"


# 全局微信支付服务实例
wechat_pay_service = WeChatPayService()