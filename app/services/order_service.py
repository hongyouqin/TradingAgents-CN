
import uuid
import time
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from app.core.config import settings
from app.services.user_service import user_service
from app.services.power_account_service import power_account_service
from app.services.wechat_pay_service import wechat_pay_service

# 尝试导入日志管理器
try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)
logger = get_logger('order_service')

class OrderService:
    """订单服务 - 支持 JSAPI/NATIVE/H5 三种支付方式"""
    
    def __init__(self):
        from pymongo import MongoClient
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        self.orders_collection = self.db.recharge_orders
        
        # 创建索引
        self.orders_collection.create_index("order_no", unique=True)
        self.orders_collection.create_index("user_id")
        self.orders_collection.create_index([("user_id", -1), ("created_at", -1)])
        self.orders_collection.create_index("status")
        # 为时间戳字段创建索引
        self.orders_collection.create_index("created_timestamp")
        self.orders_collection.create_index("expired_timestamp")
        self.orders_collection.create_index([("status", 1), ("expired_timestamp", 1)])
    
    def _generate_order_no(self) -> str:
        """生成订单号：前缀R表示充值（Recharge）"""
        timestamp = int(time.time() * 1000)
        random_str = uuid.uuid4().hex[:8].upper()
        return f"R{timestamp}{random_str}"  # R开头表示充值订单
    
    def _current_timestamp(self) -> int:
        """获取当前时间戳（毫秒）"""
        return int(time.time() * 1000)
    
    def _generate_h5_scene_info(self) -> str:
        """生成H5支付所需的scene_info参数（微信要求）"""
        # 需替换为你的前端备案域名和网站名称
        return f'''{{
            "h5_info": {{
                "type": "WAP",
                "wap_url": "{settings.WECHAT_H5_REDIRECT_URL}",
                "wap_name": "算力充值"
            }}
        }}'''
    
    async def create_recharge_order(
        self, 
        user, 
        recharge_data: Dict[str, Any], 
        payment_scene: str, 
        client_ip: str
    ) -> Tuple[Optional[Dict], str]:
        """
        创建充值订单（新增支持H5支付场景）
        :param payment_scene: JSAPI/NATIVE/H5
        """
        try:
            # 确定交易类型：H5对应MWEB，JSAPI对应JSAPI，NATIVE对应NATIVE
            trade_type_map = {
                'JSAPI': 'JSAPI',
                'NATIVE': 'NATIVE',
                'H5': 'MWEB'  # H5支付对应的微信trade_type
            }
            if payment_scene not in trade_type_map:
                return None, f"不支持的支付场景: {payment_scene}，仅支持JSAPI/NATIVE/H5"
            
            trade_type = trade_type_map[payment_scene]
            
            # 获取用户信息
            user_id = str(user['id']) if isinstance(user, dict) else str(user.id)
            username = user['username'] if isinstance(user, dict) else user.username
            
            logger.info(f"👤 创建充值订单 - 用户ID: {user_id}, 用户名: {username}, 支付场景: {payment_scene}")
            
            # 时间戳
            now_timestamp = self._current_timestamp()
            expired_timestamp = now_timestamp + (30 * 60 * 1000)  # 30分钟后过期
            
            # 保留datetime对象
            now_dt = datetime.utcnow()
            expired_dt = now_dt + timedelta(minutes=30)
            
            # 创建充值订单文档（新增H5相关字段）
            order_doc = {
                "order_no": self._generate_order_no(),
                "user_id": user_id,
                "username": username,
                
                # 订单类型
                "order_type": "RECHARGE",
                
                # 套餐信息
                "package_id": recharge_data.get('package_id'),
                "package_name": recharge_data.get('package_name'),
                
                # 金额信息
                "price": float(recharge_data['price']),           # 支付金额（人民币）
                "power_amount": float(recharge_data['power_amount']),  # 基础算力
                "bonus_amount": float(recharge_data.get('bonus_amount', 0)),  # 赠送算力
                "total_power": float(recharge_data['total_power']),  # 总获得算力
                
                # 支付信息（兼容H5）
                "payment_scene": payment_scene,
                "trade_type": trade_type,
                "status": "PENDING",
                "client_ip": client_ip,
                
                # 描述
                "description": recharge_data.get('description', ''),
                
                # 时间戳字段
                "created_timestamp": now_timestamp,
                "expired_timestamp": expired_timestamp,
                "created_at": now_dt,
                "expired_at": expired_dt,
                "updated_at": now_dt,
                
                # 微信支付相关（新增mweb_url字段用于H5）
                "prepay_id": None,
                "code_url": None,
                "mweb_url": None,  # H5支付跳转链接
                "transaction_id": None,
                "paid_timestamp": None,
                "paid_at": None,
                
                # 元数据
                "metadata": recharge_data.get('metadata', {})
            }
            
            # 保存到数据库
            result = self.orders_collection.insert_one(order_doc)
            order_doc['_id'] = str(result.inserted_id)
            
            logger.info(f"✅ 充值订单创建成功: {order_doc['order_no']}, "
                       f"用户: {username}, 金额: {recharge_data['price']}元, "
                       f"获得: {recharge_data['total_power']}⚡, 支付场景: {payment_scene}")
            
            # 返回前端需要的字段
            return {
                "order_no": order_doc['order_no'],
                "price": float(recharge_data['price']),
                "power_amount": float(recharge_data['power_amount']),
                "bonus_amount": float(recharge_data.get('bonus_amount', 0)),
                "total_power": float(recharge_data['total_power']),
                "package_name": recharge_data.get('package_name'),
                "created_timestamp": now_timestamp,
                "expired_timestamp": expired_timestamp,
                "expired_at": expired_dt.isoformat() + 'Z',
                "status": "PENDING",
                "payment_scene": payment_scene  # 新增返回支付场景
            }, ""
            
        except KeyError as e:
            logger.error(f"❌ 创建充值订单失败 - 缺少字段: {e}")
            return None, f"缺少必要字段: {e}"
        except Exception as e:
            logger.error(f"❌ 创建充值订单失败: {e}", exc_info=True)
            return None, str(e)
        
    async def prepare_recharge_payment(
        self, 
        user: Dict[str, Any], 
        order_no: str, 
        openid: str = None,
        redirect_url: str = None  # 新增：H5支付完成后跳转地址
    ) -> Tuple[Optional[Dict], str]:
        """
        准备充值支付（新增支持H5支付）
        :param redirect_url: H5支付完成后跳转的前端地址（需备案）
        """
        try:
            # 查询订单
            order = self.orders_collection.find_one({
                "order_no": order_no,
                "user_id": str(user['id'])
            })
            
            if not order:
                return None, "充值订单不存在"
            
            if order['status'] != 'PENDING':
                return None, f"订单状态异常: {order['status']}"
            
            # 检查是否过期
            now_timestamp = self._current_timestamp()
            if now_timestamp > order['expired_timestamp']:
                self.orders_collection.update_one(
                    {"order_no": order_no},
                    {
                        "$set": {
                            "status": "EXPIRED",
                            "updated_at": datetime.utcnow()
                        }
                    }
                )
                return None, "充值订单已过期"
            
            # 调用微信统一下单（兼容H5）
            total_fee = int(order['price'] * 100)  # 元转分
            unified_order_params = {
                "out_trade_no": order_no,
                "total_fee": total_fee,
                "body": order.get('description', f"充值{order['total_power']}⚡")[:128],
                "trade_type": order['trade_type'],
                "openid": openid if order['trade_type'] == 'JSAPI' else None,
                "spbill_create_ip": order['client_ip']
            }
            
            # H5支付额外参数：scene_info（微信必填）
            if order['trade_type'] == 'MWEB':
                unified_order_params['scene_info'] = self._generate_h5_scene_info()
            
            # 调用微信统一下单
            result = await wechat_pay_service.unified_order(**unified_order_params)
            
            logger.info(f"📱 微信统一下单结果: {result}, openid={openid} 订单号: {order_no}, 支付类型: {order['trade_type']}")
            if result.get('return_code') != 'SUCCESS' or result.get('result_code') != 'SUCCESS':
                return None, f"微信支付下单失败: {result.get('return_msg')}"
            
            # 更新订单（新增mweb_url字段）
            update_data = {
                "updated_at": datetime.utcnow()
            }
            payment_params = {}
            
            if order['trade_type'] == 'JSAPI':
                # JSAPI支付：生成调起参数
                update_data['prepay_id'] = result.get('prepay_id')
                payment_params = wechat_pay_service.generate_jsapi_params(result.get('prepay_id'))
            elif order['trade_type'] == 'NATIVE':
                # NATIVE支付：返回二维码地址
                update_data['code_url'] = result.get('code_url')
                payment_params = {'code_url': result.get('code_url')}
            elif order['trade_type'] == 'MWEB':
                # H5支付：返回跳转链接（拼接回跳地址）
                mweb_url = result.get('mweb_url')
                if redirect_url:
                    mweb_url += f"&redirect_url={redirect_url}"
                update_data['mweb_url'] = mweb_url
                payment_params = {
                    'mweb_url': mweb_url,  # H5支付跳转链接
                    'redirect_url': redirect_url  # 返回回跳地址
                }
            
            # 更新订单数据
            self.orders_collection.update_one(
                {"order_no": order_no},
                {"$set": update_data}
            )
            
            return payment_params, ""
            
        except Exception as e:
            logger.error(f"❌ 准备充值支付失败: {e}", exc_info=True)
            return None, str(e)
    
    async def handle_recharge_success(
                self, 
                order_no: str, 
                transaction_id: str, 
                paid_amount: int = None
            ) -> Tuple[bool, str]:
            """处理充值成功回调（无需修改，H5支付回调逻辑与其他方式一致）"""
            try:
                # 查询订单
                order = self.orders_collection.find_one({"order_no": order_no})
                if not order:
                    return False, f"充值订单不存在: {order_no}"
                
                # 幂等性校验（核心！避免重复处理）
                if order['status'] == 'PAID':
                    logger.info(f"充值订单已处理: {order_no}")
                    return True, "订单已处理"
                
                if order['status'] not in ['PENDING', 'EXPIRED']:
                    return False, f"订单状态无法处理: {order['status']}"
                
                # 金额校验
                if paid_amount:
                    expected_amount = int(round(order['price'] * 100))
                    if paid_amount != expected_amount:
                        logger.error(f"支付金额不符: 预期={expected_amount}分, 实际={paid_amount}分")
                        return False, "支付金额校验失败"
                
                # 获取用户
                user = await user_service.get_user_by_id(order['user_id'])
                if not user:
                    return False, f"用户不存在: {order['user_id']}"
                
                # 转Decimal（匹配recharge参数）
                total_power = Decimal(str(order['total_power']))
                
                # 调用充值（事务版，失败则余额不更新）
                success, msg = await power_account_service.recharge(
                    user=user,
                    order_no=order_no,
                    amount=total_power,
                    description=order.get('description', f"微信充值{total_power}⚡"),
                    metadata={
                        'transaction_id': transaction_id,
                        'payment_scene': order['payment_scene'],
                        'package_id': order.get('package_id'),
                        'package_name': order.get('package_name'),
                        'price': Decimal(str(order['price'])),  # 传Decimal，内部会转字符串
                        'power_amount': Decimal(str(order['power_amount'])),
                        'bonus_amount': Decimal(str(order.get('bonus_amount', 0)))
                    }
                )
                
                if not success:
                    logger.error(f"算力充值失败: {order_no}, {msg}")
                    return False, f"算力充值失败: {msg}"
                
                # 更新订单状态（原子操作）
                now_timestamp = self._current_timestamp()
                now_dt = datetime.utcnow()
                update_result = self.orders_collection.update_one(
                    {
                        "order_no": order_no,
                        "status": {"$in": ["PENDING", "EXPIRED"]}
                    },
                    {
                        "$set": {
                            "status": "PAID",
                            "transaction_id": transaction_id,
                            "paid_timestamp": now_timestamp,
                            "paid_at": now_dt,
                            "updated_at": now_dt
                        }
                    }
                )
                
                if update_result.modified_count == 0:
                    logger.warning(f"订单{order_no}状态已被更新，无需重复处理")
                    return True, "订单已处理"
                
                logger.info(
                    f"✅ 充值成功 [订单:{order_no}] [用户:{user.id}/{user.username}] "
                    f"[金额:{order['price']}元] [算力:{total_power}⚡] [微信交易号:{transaction_id}] [支付场景:{order['payment_scene']}]"
                )
                return True, "充值成功"
                
            except Exception as e:
                logger.error(f"❌ 处理充值成功回调失败: {e}", exc_info=True)
                return False, str(e)
        
    async def query_recharge_status(
        self, 
        user: Dict[str, Any], 
        order_no: str
    ) -> Dict[str, Any]:
        """
        查询充值订单状态（新增返回H5相关字段）
        """
        try:
            order = self.orders_collection.find_one({
                "order_no": order_no,
                "user_id": str(user['id'])
            })
            
            if not order:
                return {'status': 'NOT_FOUND', 'message': '充值订单不存在'}
            
            # 检查是否过期
            now_timestamp = self._current_timestamp()
            is_expired = now_timestamp > order.get('expired_timestamp', 0)
            
            # 如果订单是PENDING但已过期，自动更新状态
            if order['status'] == 'PENDING' and is_expired:
                self.orders_collection.update_one(
                    {"order_no": order_no},
                    {"$set": {"status": "EXPIRED", "updated_at": datetime.utcnow()}}
                )
                order['status'] = 'EXPIRED'
            
            result = {
                'status': order['status'],
                'order_no': order['order_no'],
                'order_type': 'RECHARGE',
                'price': order['price'],
                'power_amount': order['power_amount'],
                'bonus_amount': order.get('bonus_amount', 0),
                'total_power': order['total_power'],
                'package_name': order.get('package_name'),
                'description': order.get('description'),
                'created_timestamp': order.get('created_timestamp'),
                'expired_timestamp': order.get('expired_timestamp'),
                'is_expired': is_expired,
                'payment_scene': order.get('payment_scene')  # 新增返回支付场景
            }
            
            # 按支付场景返回对应字段
            if order['trade_type'] == 'JSAPI':
                result['prepay_id'] = order.get('prepay_id')
            elif order['trade_type'] == 'NATIVE':
                result['code_url'] = order.get('code_url')
            elif order['trade_type'] == 'MWEB':
                result['mweb_url'] = order.get('mweb_url')
            
            if order['status'] == 'PAID':
                result['paid_timestamp'] = order.get('paid_timestamp')
                result['transaction_id'] = order.get('transaction_id')
            
            return result
            
        except Exception as e:
            logger.error(f"❌ 查询充值订单状态失败: {e}")
            return {'status': 'ERROR', 'message': str(e)}
    
    
    async def get_user_recharge_orders(
        self, 
        user: Dict[str, Any], 
        skip: int = 0, 
        limit: int = 20
    ) -> List[Dict]:
        """
        获取用户的充值订单列表（新增支付场景字段）
        """
        try:
            cursor = self.orders_collection.find(
                {"user_id": str(user['id'])}
            ).sort("created_timestamp", -1).skip(skip).limit(limit)
            
            orders = []
            now_timestamp = self._current_timestamp()
            
            for order in cursor:
                # 转换ObjectId为字符串
                order['_id'] = str(order['_id'])
                
                # 添加是否过期字段
                if order['status'] == 'PENDING':
                    order['is_expired'] = now_timestamp > order.get('expired_timestamp', 0)
                else:
                    order['is_expired'] = False
                
                # 只返回需要的字段（新增支付场景）
                orders.append({
                    'order_no': order['order_no'],
                    'status': order['status'],
                    'price': order['price'],
                    'power_amount': order['power_amount'],
                    'bonus_amount': order.get('bonus_amount', 0),
                    'total_power': order['total_power'],
                    'package_name': order.get('package_name'),
                    'description': order.get('description'),
                    'created_timestamp': order.get('created_timestamp'),
                    'expired_timestamp': order.get('expired_timestamp'),
                    'paid_timestamp': order.get('paid_timestamp'),
                    'is_expired': order.get('is_expired', False),
                    'payment_scene': order.get('payment_scene')  # 新增
                })
            
            return orders
            
        except Exception as e:
            logger.error(f"❌ 获取用户充值订单列表失败: {e}")
            return []

# 全局订单服务实例
order_service = OrderService()