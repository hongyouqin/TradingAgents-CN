import hashlib
import time
import random
import string
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, Tuple
import httpx

from app.core.config import settings

try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)
logger = get_logger('wechat_pay_service')

class WeChatPayService:
    """微信支付服务"""
    
    def __init__(self):
        self.app_id = settings.WECHAT_APP_ID
        self.mch_id = settings.WECHAT_MCH_ID
        self.api_key = settings.WECHAT_API_KEY
        self.notify_url = settings.WECHAT_NOTIFY_URL
        self.api_base = "https://api.mch.weixin.qq.com"
    
    def _generate_nonce_str(self) -> str:
        """生成随机字符串"""
        return ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    
    def _sign(self, params: Dict) -> str:
        """生成签名"""
        # 按照key排序
        sorted_params = sorted(params.items())
        # 拼接字符串
        sign_str = '&'.join([f"{k}={v}" for k, v in sorted_params if v])
        sign_str += f"&key={self.api_key}"
        # MD5加密
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
    
    def _to_xml(self, params: Dict) -> str:
        """字典转XML"""
        xml = ['<xml>']
        for k, v in params.items():
            if v is not None:
                xml.append(f'<{k}><![CDATA[{v}]]></{k}>')
        xml.append('</xml>')
        return ''.join(xml)
    
    def _parse_xml(self, xml_str: str) -> Dict:
        """XML转字典"""
        try:
            root = ET.fromstring(xml_str)
            return {child.tag: child.text for child in root}
        except:
            return {}
    
    async def _post(self, url: str, data: Dict) -> Dict:
        """发送POST请求"""
        xml_data = self._to_xml(data)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, 
                content=xml_data.encode('utf-8'),
                headers={'Content-Type': 'text/xml'}
            )
            return self._parse_xml(response.text)
    
    async def unified_order(self, out_trade_no: str, total_fee: int, body: str,
                           trade_type: str, openid: str = None, 
                           spbill_create_ip: str = None) -> Dict:
        """
        统一下单接口
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
        
        params['sign'] = self._sign(params)
        
        return await self._post(f"{self.api_base}/pay/unifiedorder", params)
    
    async def query_order(self, out_trade_no: str = None, 
                         transaction_id: str = None) -> Dict:
        """
        查询订单
        """
        params = {
            'appid': self.app_id,
            'mch_id': self.mch_id,
            'nonce_str': self._generate_nonce_str(),
        }
        
        if out_trade_no:
            params['out_trade_no'] = out_trade_no
        elif transaction_id:
            params['transaction_id'] = transaction_id
        else:
            return {'return_code': 'FAIL', 'return_msg': '缺少订单号'}
        
        params['sign'] = self._sign(params)
        return await self._post(f"{self.api_base}/pay/orderquery", params)
    
    def generate_jsapi_params(self, prepay_id: str) -> Dict:
        """
        生成JSAPI调起支付参数
        """
        params = {
            'appId': self.app_id,
            'timeStamp': str(int(time.time())),
            'nonceStr': self._generate_nonce_str(),
            'package': f'prepay_id={prepay_id}',
            'signType': 'MD5',
        }
        params['paySign'] = self._sign(params)
        return params
    
    def verify_notify(self, xml_data: str) -> Tuple[bool, Dict]:
        """
        验证支付回调通知
        """
        try:
            data = self._parse_xml(xml_data)
            
            # 验证签名
            sign = data.pop('sign', '')
            if not sign:
                return False, {'return_msg': '缺少签名'}
            
            calculated_sign = self._sign(data)
            if calculated_sign != sign:
                return False, {'return_msg': '签名验证失败'}
            
            # 验证返回码
            if data.get('return_code') != 'SUCCESS' or data.get('result_code') != 'SUCCESS':
                return False, {'return_msg': data.get('return_msg', '支付失败')}
            
            data['sign'] = sign
            return True, data
            
        except Exception as e:
            logger.error(f"验证回调失败: {e}")
            return False, {'return_msg': str(e)}

# 全局微信支付服务实例
wechat_pay_service = WeChatPayService()