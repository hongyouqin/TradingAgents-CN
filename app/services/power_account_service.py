
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from bson import ObjectId, Decimal128

from app.core.config import settings
from app.models.user import User
from pymongo import MongoClient

# 尝试导入日志管理器
try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('power_account_service')

class PowerAccountService:
    '''
    ┌─────────────────────────────────────────────────────────────┐
    │                      用户服务 (已有)                          │
    │  ┌─────────────┐                                            │
    │  │   User      │  username, email, phone, is_active...      │
    │  └─────────────┘                                            │
    │           │ 1:1                                              │
    │           ▼                                                  │
    │  ┌─────────────┐   1:N   ┌─────────────┐   1:1   ┌─────────┐│
    │  │PowerAccount │◄────────┤    Order    │◄────────┤Payment  ││
    │  │             │         │             │         │(微信)   ││
    │  └─────────────┘         └─────────────┘         └─────────┘│
    │                                                              │
    │  ┌─────────────────────────────────────────┐                │
    │  │        PowerTransaction                  │                │
    │  │  记录每笔算力的增减，关联到订单号           │                │
    │  └─────────────────────────────────────────┘                │
    └─────────────────────────────────────────────────────────────┘
    
    ┌─────────────────┐       ┌─────────────────┐
│     users       │       │  power_accounts │
├─────────────────┤       ├─────────────────┤
│ _id             │──────▶│ user_id         │
│ username        │       │ username        │
│ email           │       │ balance         │
│ phone           │       │ ...             │
│ ...             │       └─────────────────┘
└─────────────────┘               │
         │                         │
         │                         │ 1:N
         │                         ▼
         │                ┌─────────────────┐
         │                │power_transactions│
         │                ├─────────────────┤
         │                │ account_id      │
         │                │ order_no        │
         │                │ amount          │
         │                │ ...             │
         │                └─────────────────┘
         │                         ▲
         │                         │
         │ 1:N              N:1    │
         │                         │
         ▼                ┌────────┴────────┐
┌─────────────────┐       │                 │
│     orders      │       │  微信支付回调    │
├─────────────────┤       │                 │
│ _id             │       │ out_trade_no    │
│ order_no        │──────▶│ transaction_id  │
│ user_id         │       │ total_fee       │
│ username        │       │ ...             │
│ amount          │       └─────────────────┘
│ status          │
│ transaction_id  │
│ ...             │
└─────────────────┘  

    算力账户与用户账户是一对一关系，每个用户有一个算力账户。算力账户记录了用户当前的算力余额，以及与订单和支付相关的交易记录。

    '''
    
    def __init__(self):
        
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        self.accounts_collection = self.db.power_accounts
        self.transactions_collection = self.db.power_transactions
        
        # 创建索引
        self._create_indexes()
    
    def _create_indexes(self):
        """创建数据库索引"""
        # 账户表索引
        self.accounts_collection.create_index("user_id", unique=True)
        self.accounts_collection.create_index("username")
        
        # 交易流水表索引
        self.transactions_collection.create_index("account_id")
        self.transactions_collection.create_index("order_no", unique=True)
        self.transactions_collection.create_index([("created_at", -1)])
    
    def _decimal_to_128(self, value: Decimal) -> Decimal128:
        """Decimal转MongoDB Decimal128"""
        return Decimal128(str(value))
    
    def _decimal_from_128(self, value) -> Decimal:
        """MongoDB Decimal128转Decimal"""
        if isinstance(value, Decimal128):
            return Decimal(str(value))
        return Decimal(str(value))
    
    async def get_or_create_account(self, user: User) -> Dict[str, Any]:
        """
        获取或创建用户的算力账户
        :param user: 用户对象
        :return: 账户信息
        """
        try:
            # 查找现有账户
            account = self.accounts_collection.find_one({"user_id": str(user.id)})
            
            if account:
                # 转换Decimal128为Decimal
                account['balance'] = self._decimal_from_128(account['balance'])
                account['total_recharged'] = self._decimal_from_128(account['total_recharged'])
                account['total_consumed'] = self._decimal_from_128(account['total_consumed'])
                account['frozen_amount'] = self._decimal_from_128(account['frozen_amount'])
                logger.info(f"✅ 获取现有算力账户: {user.username}, 余额: {account['balance']}")
                return account
            
            # 创建新账户
            now = datetime.utcnow()
            account_doc = {
                "user_id": str(user.id),
                "username": user.username,
                "balance": self._decimal_to_128(Decimal('0.00')),
                "total_recharged": self._decimal_to_128(Decimal('0.00')),
                "total_consumed": self._decimal_to_128(Decimal('0.00')),
                "frozen_amount": self._decimal_to_128(Decimal('0.00')),
                "version": 0,  # 乐观锁版本
                "created_at": now,
                "updated_at": now
            }
            
            result = self.accounts_collection.insert_one(account_doc)
            account_doc['_id'] = result.inserted_id
            account_doc['balance'] = Decimal('0.00')
            account_doc['total_recharged'] = Decimal('0.00')
            account_doc['total_consumed'] = Decimal('0.00')
            account_doc['frozen_amount'] = Decimal('0.00')
            
            logger.info(f"✅ 创建新算力账户: {user.username}")
            return account_doc
            
        except Exception as e:
            logger.error(f"❌ 获取/创建算力账户失败: {e}")
            return None
    
    async def get_balance(self, user: User) -> Dict[str, Decimal]:
        """
        获取用户算力余额
        :param user: 用户对象
        :return: 余额信息
        """
        account = await self.get_or_create_account(user)
        if not account:
            return {
                'balance': Decimal('0.00'),
                'frozen': Decimal('0.00'),
                'available': Decimal('0.00'),
                'total_recharged': Decimal('0.00'),
                'total_consumed': Decimal('0.00')
            }
        
        return {
            'balance': account['balance'],
            'frozen': account['frozen_amount'],
            'available': account['balance'] - account['frozen_amount'],
            'total_recharged': account['total_recharged'],
            'total_consumed': account['total_consumed']
        }
    
    async def recharge(self, user: User, order_no: str, amount: Decimal, 
                      description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        """
        充值算力（支付成功后调用）
        :param user: 用户对象
        :param order_no: 订单号
        :param amount: 充值金额
        :param description: 描述
        :param metadata: 元数据
        :return: (成功状态, 消息)
        """
        try:
            # 使用find_one_and_update保证原子性
            account = await self.get_or_create_account(user)
            if not account:
                return False, "账户不存在"
            
            # 更新账户余额（原子操作）
            result = self.accounts_collection.find_one_and_update(
                {
                    "user_id": str(user.id),
                    "version": account['version']  # 乐观锁
                },
                {
                    "$inc": {
                        "balance": self._decimal_to_128(amount),
                        "total_recharged": self._decimal_to_128(amount),
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                return False, "账户更新失败，请重试"
            
            # 记录交易流水
            now = datetime.utcnow()
            transaction = {
                "account_id": str(account['_id']),
                "user_id": str(user.id),
                "username": user.username,
                "order_no": order_no,
                "transaction_type": "RECHARGE",
                "amount": self._decimal_to_128(amount),
                "before_balance": account['balance'],
                "after_balance": account['balance'] + amount,
                "status": "SUCCESS",
                "description": description,
                "metadata": metadata or {},
                "created_at": now,
                "completed_at": now
            }
            
            self.transactions_collection.insert_one(transaction)
            
            logger.info(f"✅ 算力充值成功: {user.username}, 金额: {amount}, 订单: {order_no}")
            return True, "充值成功"
            
        except Exception as e:
            logger.error(f"❌ 算力充值失败: {e}")
            return False, f"充值失败: {str(e)}"
    
    async def consume(self, user: User, order_no: str, amount: Decimal,
                     description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        """
        消费算力（购买商品时调用）
        :param user: 用户对象
        :param order_no: 订单号
        :param amount: 消费金额
        :param description: 描述
        :param metadata: 元数据
        :return: (成功状态, 消息)
        """
        try:
            account = await self.get_or_create_account(user)
            if not account:
                return False, "账户不存在"
            
            # 检查余额
            if account['balance'] < amount:
                return False, f"余额不足，当前余额: {account['balance']}，需要: {amount}"
            
            # 更新账户余额（原子操作）
            result = self.accounts_collection.find_one_and_update(
                {
                    "user_id": str(user.id),
                    "balance": {"$gte": self._decimal_to_128(amount)},
                    "version": account['version']
                },
                {
                    "$inc": {
                        "balance": self._decimal_to_128(-amount),
                        "total_consumed": self._decimal_to_128(amount),
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                return False, "消费失败，请重试"
            
            # 记录交易流水
            now = datetime.utcnow()
            transaction = {
                "account_id": str(account['_id']),
                "user_id": str(user.id),
                "username": user.username,
                "order_no": order_no,
                "transaction_type": "CONSUME",
                "amount": self._decimal_to_128(-amount),  # 消费为负数
                "before_balance": account['balance'],
                "after_balance": account['balance'] - amount,
                "status": "SUCCESS",
                "description": description,
                "metadata": metadata or {},
                "created_at": now,
                "completed_at": now
            }
            
            self.transactions_collection.insert_one(transaction)
            
            logger.info(f"✅ 算力消费成功: {user.username}, 金额: {amount}, 订单: {order_no}")
            return True, "消费成功"
            
        except Exception as e:
            logger.error(f"❌ 算力消费失败: {e}")
            return False, f"消费失败: {str(e)}"
    
    async def get_transactions(self, user: User, limit: int = 50) -> list:
        """
        获取用户交易流水
        :param user: 用户对象
        :param limit: 限制条数
        :return: 交易流水列表
        """
        try:
            cursor = self.transactions_collection.find(
                {"user_id": str(user.id)}
            ).sort("created_at", -1).limit(limit)
            
            transactions = []
            for t in cursor:
                t['amount'] = self._decimal_from_128(t['amount'])
                t['before_balance'] = self._decimal_from_128(t['before_balance'])
                t['after_balance'] = self._decimal_from_128(t['after_balance'])
                transactions.append(t)
            
            return transactions
            
        except Exception as e:
            logger.error(f"❌ 获取交易流水失败: {e}")
            return []

# 全局算力账户服务实例
power_account_service = PowerAccountService()