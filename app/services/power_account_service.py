from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from bson import ObjectId, Decimal128
from pymongo import MongoClient, errors
import asyncio
import time

from app.core.config import settings
from app.models.user import User

# 尝试导入日志管理器
try:
    from tradingagents.utils.logging_manager import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger('power_account_service')


'''
    适配单节点MongoDB（无事务）+ 异步FastAPI + Decimal序列化
    核心：先记录后更新 + 乐观锁 + 唯一索引 + 幂等性校验 保障数据一致性
'''
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
    '''
    
    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        self.accounts_collection = self.db.power_accounts
        self.transactions_collection = self.db.power_transactions
        self._create_indexes()
        
    def _create_indexes(self):
        """创建数据库索引（重点：订单号唯一索引，防止重复流水）"""
        # 账户表索引
        self.accounts_collection.create_index("user_id", unique=True)
        self.accounts_collection.create_index("username")
        
        # 交易流水表索引（核心：order_no唯一，防止重复插入）
        self.transactions_collection.create_index("account_id")
        self.transactions_collection.create_index("order_no", unique=True)  # 幂等性关键
        self.transactions_collection.create_index([("created_at", -1)])
        self.transactions_collection.create_index("status")  # 添加状态索引
        
        self.transactions_collection.create_index(
            [("order_no", 1), ("status", 1)],
            unique=False
        )
        self.transactions_collection.create_index(
            [("account_id", 1), ("status", 1), ("created_at", -1)]
        )
    
    def _decimal_to_128(self, value: Decimal) -> Decimal128:
        """Decimal转MongoDB Decimal128"""
        try:
            return Decimal128(str(value))
        except (InvalidOperation, TypeError) as e:
            logger.error(f"❌ Decimal转Decimal128失败: value={value}, error={e}")
            raise
    
    def _decimal_from_128(self, value: Any) -> Decimal:
        """MongoDB Decimal128转Decimal"""
        try:
            if isinstance(value, Decimal128):
                return Decimal(str(value))
            elif isinstance(value, (int, float, str)):
                return Decimal(str(value))
            elif isinstance(value, Decimal):
                return value
            else:
                raise TypeError(f"不支持的类型: {type(value)}")
        except (InvalidOperation, TypeError) as e:
            logger.error(f"❌ Decimal128转Decimal失败: value={value}, error={e}")
            raise
    
    def _clean_decimal_in_dict(self, data: Dict) -> Dict:
        """递归清理字典中的Decimal类型（转换为字符串）"""
        if not data:
            return {}
        
        result = {}
        for k, v in data.items():
            if isinstance(v, Decimal):
                result[k] = str(v)
            elif isinstance(v, dict):
                result[k] = self._clean_decimal_in_dict(v)
            elif isinstance(v, (list, tuple)):
                result[k] = [
                    self._clean_decimal_in_dict(item) if isinstance(item, dict)
                    else str(item) if isinstance(item, Decimal)
                    else item
                    for item in v
                ]
            else:
                result[k] = v
        return result
    
    def _get_or_create_account_sync(self, user: User) -> Optional[Dict[str, Any]]:
        """同步：获取/创建用户算力账户"""
        try:
            account = self.accounts_collection.find_one({"user_id": str(user.id)})
            if account:
                # 转换为Decimal供上层使用
                account['balance'] = self._decimal_from_128(account['balance'])
                account['total_recharged'] = self._decimal_from_128(account['total_recharged'])
                account['total_consumed'] = self._decimal_from_128(account['total_consumed'])
                account['frozen_amount'] = self._decimal_from_128(account['frozen_amount'])
                logger.info(f"✅ 获取现有算力账户: {user.username}, 余额: {account['balance']}")
                return account
            
            # 创建新账户（存储为Decimal128）
            now = datetime.utcnow()
            zero_dec = Decimal('0.00')
            account_doc = {
                "user_id": str(user.id),
                "username": user.username,
                "balance": self._decimal_to_128(zero_dec),
                "total_recharged": self._decimal_to_128(zero_dec),
                "total_consumed": self._decimal_to_128(zero_dec),
                "frozen_amount": self._decimal_to_128(zero_dec),
                "version": 0,  # 乐观锁版本
                "created_at": now,
                "updated_at": now
            }
            result = self.accounts_collection.insert_one(account_doc)
            account_doc['_id'] = result.inserted_id
            # 返回Decimal类型给上层
            account_doc['balance'] = zero_dec
            account_doc['total_recharged'] = zero_dec
            account_doc['total_consumed'] = zero_dec
            account_doc['frozen_amount'] = zero_dec
            logger.info(f"✅ 创建新算力账户: {user.username}")
            return account_doc
        except errors.DuplicateKeyError:
            # 并发创建时重试一次
            logger.warning(f"⚠️ 账户创建冲突，重试获取: {user.username}")
            return self._get_or_create_account_sync(user)
        except Exception as e:
            logger.error(f"❌ 获取/创建算力账户失败: {e}", exc_info=True)
            return None
    
    def _recharge_sync(self, user: User, order_no: str, amount: Decimal, 
                       description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        """
        同步：充值算力 - 先记录后更新模式
        
        核心保障：
        1. 先创建待处理流水（PENDING状态）
        2. 乐观锁更新余额
        3. 更新流水状态为SUCCESS
        4. 唯一索引防止重复
        """
        # 前置校验
        if amount <= Decimal('0'):
            return False, "充值金额必须大于0"
        
        account = self._get_or_create_account_sync(user)
        if not account:
            return False, "账户不存在"
        
        # 🔥 幂等性校验：先检查该订单是否已处理
        existing_transaction = self.transactions_collection.find_one({"order_no": order_no})
        if existing_transaction:
            if existing_transaction.get('status') == 'CONFIRMED':
                logger.info(f"✅ 订单{order_no}已成功充值，跳过重复处理")
                return True, "订单已处理"
            elif existing_transaction.get('status') == 'PENDING':
                # 待处理状态，尝试完成
                logger.info(f"⏳ 订单{order_no}处于待处理状态，尝试完成")
                return self._complete_pending_recharge(existing_transaction, account)
        
        # 转换为Decimal128（用于MongoDB存储）
        amount_128 = self._decimal_to_128(amount)
        current_balance = account['balance']
        after_balance = current_balance + amount
        
        # 清理metadata中的Decimal
        clean_metadata = self._clean_decimal_in_dict(metadata or {})
        
        # 1. 先创建待处理交易流水
        now = datetime.utcnow()
        transaction = {
            "_id": ObjectId(),
            "account_id": str(account['_id']),
            "user_id": str(user.id),
            "username": user.username,
            "order_no": order_no,
            "transaction_type": "RECHARGE",
            "amount": amount_128,
            "before_balance": self._decimal_to_128(current_balance),
            "after_balance": None,  # 成功后才设置
            "status": "PENDING",
            "description": description,
            "metadata": clean_metadata,
            "created_at": now,
            "completed_at": None,
            "retry_count": 0
        }
        
        try:
            # 插入待处理流水（唯一索引保证不重复）
            self.transactions_collection.insert_one(transaction)
            logger.info(f"📝 创建待处理充值记录: {order_no}")
        except errors.DuplicateKeyError:
            # 订单号重复，说明已经存在
            existing = self.transactions_collection.find_one({"order_no": order_no})
            if existing:
                return self._complete_pending_recharge(existing, account)
            return False, "订单已存在"
        
        # 2. 尝试更新余额（乐观锁，最多重试3次）
        for retry in range(3):
            try:
                # 重新获取最新账户信息
                current_account = self.accounts_collection.find_one(
                    {"_id": account['_id']}
                )
                if not current_account:
                    raise Exception("账户不存在")
                
                current_version = current_account['version']
                
                # 原子更新余额
                result = self.accounts_collection.find_one_and_update(
                    {
                        "_id": account['_id'],
                        "version": current_version
                    },
                    {
                        "$inc": {
                            "balance": amount_128,
                            "total_recharged": amount_128,
                            "version": 1
                        },
                        "$set": {
                            "updated_at": datetime.utcnow()
                        }
                    },
                    return_document=True
                )
                
                if result:
                    # 更新成功
                    after_balance = self._decimal_from_128(result['balance'])
                    
                    # 更新流水状态为成功
                    self.transactions_collection.update_one(
                        {"_id": transaction['_id']},
                        {
                            "$set": {
                                "status": "CONFIRMED",
                                "completed_at": datetime.utcnow(),
                                "after_balance": self._decimal_to_128(after_balance)
                            }
                        }
                    )
                    
                    logger.info(
                        f"✅ 算力充值成功: 用户{user.username}, "
                        f"金额: {amount}⚡, 余额: {after_balance}⚡, 订单: {order_no}"
                    )
                    return True, "充值成功"
                
                # 版本冲突，重试
                logger.warning(f"⚠️ 充值重试 {retry + 1}/3: 版本冲突")
                if retry < 2:  # 最后一次不等待
                    time.sleep(0.1 * (retry + 1))  # 递增延迟
                    
            except Exception as e:
                logger.error(f"❌ 充值重试 {retry + 1}/3 失败: {e}")
                if retry == 2:  # 最后一次重试
                    raise
                time.sleep(0.1 * (retry + 1))
        
        # 所有重试都失败，标记流水为失败
        self.transactions_collection.update_one(
            {"_id": transaction['_id']},
            {
                "$set": {
                    "status": "FAILED",
                    "error": "所有重试都失败",
                    "completed_at": datetime.utcnow()
                }
            }
        )
        
        logger.error(f"❌ 充值失败: 订单 {order_no}，所有重试都失败")
        return False, "充值失败，请稍后重试"
    
    def _complete_pending_recharge(self, pending_tx: Dict, account: Dict) -> Tuple[bool, str]:
        """完成待处理的充值交易"""
        try:
            # 检查余额是否已更新
            current_account = self.accounts_collection.find_one(
                {"_id": ObjectId(pending_tx['account_id'])}
            )
            if not current_account:
                return False, "账户不存在"
            
            current_balance = self._decimal_from_128(current_account['balance'])
            tx_amount = self._decimal_from_128(pending_tx['amount'])
            before_balance = self._decimal_from_128(pending_tx['before_balance'])
            
            # 计算期望余额
            expected_balance = before_balance + tx_amount
            
            # 如果余额已经达到期望值，说明已经充值成功
            if abs(current_balance - expected_balance) < Decimal('0.01'):
                # 更新流水为成功
                self.transactions_collection.update_one(
                    {"_id": pending_tx['_id']},
                    {
                        "$set": {
                            "status": "CONFIRMED",
                            "completed_at": datetime.utcnow(),
                            "after_balance": self._decimal_to_128(current_balance)
                        }
                    }
                )
                logger.info(f"✅ 待处理充值恢复成功: {pending_tx['order_no']}")
                return True, "充值已完成"
            
            # 余额未更新，需要重新执行充值
            logger.warning(f"⚠️ 待处理充值需要重新执行: {pending_tx['order_no']}")
            return False, "需要重新充值"
            
        except Exception as e:
            logger.error(f"❌ 恢复待处理充值失败: {e}")
            return False, f"恢复失败: {str(e)}"
    
    def _consume_sync(self, user: User, order_no: str, amount: Decimal,
                      description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        """
        同步：消费算力 - 先记录后更新模式
        """
        try:
            # 基础校验
            if amount <= Decimal('0'):
                return False, "消费金额必须大于0"
            
            # 获取/创建账户
            account = self._get_or_create_account_sync(user)
            if not account:
                return False, "账户不存在"
            
            # 幂等性校验
            existing_transaction = self.transactions_collection.find_one({"order_no": order_no})
            if existing_transaction:
                if existing_transaction.get('status') == 'CONFIRMED':
                    logger.info(f"✅ 订单{order_no}已成功消费，跳过重复处理")
                    return True, "订单已处理"
                elif existing_transaction.get('status') == 'PENDING':
                    return self._complete_pending_consume(existing_transaction, account)
            
            # 余额校验
            current_balance = account['balance']
            if current_balance < amount:
                return False, f"余额不足，当前余额: {current_balance}⚡，需要: {amount}⚡"
            
            # 转换为Decimal128
            amount_128 = self._decimal_to_128(amount)
            after_balance = current_balance - amount
            
            # 清理metadata
            clean_metadata = self._clean_decimal_in_dict(metadata or {})
            
            # 1. 先创建待处理交易流水
            now = datetime.utcnow()
            transaction = {
                "_id": ObjectId(),
                "account_id": str(account['_id']),
                "user_id": str(user.id),
                "username": user.username,
                "order_no": order_no,
                "transaction_type": "CONSUME",
                "amount": self._decimal_to_128(-amount),
                "before_balance": self._decimal_to_128(current_balance),
                "after_balance": None,
                "status": "PENDING",
                "description": description,
                "metadata": clean_metadata,
                "created_at": now,
                "completed_at": None
            }
            
            try:
                self.transactions_collection.insert_one(transaction)
                logger.info(f"📝 创建待处理消费记录: {order_no}")
            except errors.DuplicateKeyError:
                existing = self.transactions_collection.find_one({"order_no": order_no})
                if existing:
                    return self._complete_pending_consume(existing, account)
                return False, "订单已存在"
            
            # 2. 更新余额（带余额检查）
            result = self.accounts_collection.find_one_and_update(
                {
                    "user_id": str(user.id),
                    "balance": {"$gte": amount_128},
                    "version": account['version']
                },
                {
                    "$inc": {
                        "balance": self._decimal_to_128(-amount),
                        "total_consumed": amount_128,
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                # 更新失败，标记流水为失败
                self.transactions_collection.update_one(
                    {"_id": transaction['_id']},
                    {
                        "$set": {
                            "status": "FAILED",
                            "error": "余额不足或版本冲突",
                            "completed_at": datetime.utcnow()
                        }
                    }
                )
                logger.warning(f"❌ 消费失败: 用户{user.username}，余额不足")
                return False, "消费失败，请重试"
            
            # 更新成功
            after_balance = self._decimal_from_128(result['balance'])
            
            # 更新流水为成功
            self.transactions_collection.update_one(
                {"_id": transaction['_id']},
                {
                    "$set": {
                        "status": "CONFIRMED",
                        "completed_at": datetime.utcnow(),
                        "after_balance": self._decimal_to_128(after_balance)
                    }
                }
            )
            
            logger.info(
                f"✅ 算力消费成功: 用户{user.username}, "
                f"消费金额: {amount}⚡, 余额: {after_balance}⚡, 订单: {order_no}"
            )
            return True, "消费成功"
            
        except InvalidOperation as e:
            logger.error(f"❌ 算力消费失败: 金额格式错误 - {e}")
            return False, f"消费失败: 金额格式错误"
        except Exception as e:
            logger.error(f"❌ 算力消费失败: {e}", exc_info=True)
            return False, f"消费失败: {str(e)}"
    
    def _complete_pending_consume(self, pending_tx: Dict, account: Dict) -> Tuple[bool, str]:
        """完成待处理的消费交易"""
        try:
            current_account = self.accounts_collection.find_one(
                {"_id": ObjectId(pending_tx['account_id'])}
            )
            if not current_account:
                return False, "账户不存在"
            
            current_balance = self._decimal_from_128(current_account['balance'])
            tx_amount = abs(self._decimal_from_128(pending_tx['amount']))
            before_balance = self._decimal_from_128(pending_tx['before_balance'])
            
            expected_balance = before_balance - tx_amount
            
            if abs(current_balance - expected_balance) < Decimal('0.01'):
                self.transactions_collection.update_one(
                    {"_id": pending_tx['_id']},
                    {
                        "$set": {
                            "status": "CONFIRMED",
                            "completed_at": datetime.utcnow(),
                            "after_balance": self._decimal_to_128(current_balance)
                        }
                    }
                )
                logger.info(f"✅ 待处理消费恢复成功: {pending_tx['order_no']}")
                return True, "消费已完成"
            
            return False, "需要重新消费"
            
        except Exception as e:
            logger.error(f"❌ 恢复待处理消费失败: {e}")
            return False, f"恢复失败: {str(e)}"
    
    def _get_balance_sync(self, user: User) -> Dict[str, Decimal]:
        """同步：获取用户算力余额"""
        account = self._get_or_create_account_sync(user)
        if not account:
            zero_dec = Decimal('0.00')
            return {
                'balance': zero_dec,
                'frozen': zero_dec,
                'available': zero_dec,
                'total_recharged': zero_dec,
                'total_consumed': zero_dec
            }
        
        return {
            'balance': account['balance'],
            'frozen': account['frozen_amount'],
            'available': account['balance'] - account['frozen_amount'],
            'total_recharged': account['total_recharged'],
            'total_consumed': account['total_consumed']
        }
    
    def _get_transactions_sync(self, user: User, limit: int = 50, 
                                status: str = None,
                                transaction_type: str = None) -> list:
        """
        同步：获取用户交易流水
        
        Args:
            user: 用户对象
            limit: 返回数量限制
            status: 筛选状态 (FROZEN/CONFIRMED/CANCELLED/EXPIRED)
            transaction_type: 筛选类型 (RECHARGE/CONSUME/FREEZE/REFUND)
        """
        try:
            query = {"user_id": str(user.id)}
            
            # 添加状态筛选
            if status:
                query["status"] = status
            
            # 🔥 添加交易类型筛选 - 支持 FREEZE
            if transaction_type and transaction_type != 'ALL':
                query["transaction_type"] = transaction_type
            
            logger.info(f"🔍 查询交易流水: {query}, limit={limit}")
            
            cursor = self.transactions_collection.find(query)\
                    .sort("created_at", -1)\
                    .limit(limit)
            
            transactions = []
            for t in cursor:
                # 转换所有Decimal128为Decimal
                t['amount'] = self._decimal_from_128(t['amount'])
                t['before_balance'] = self._decimal_from_128(t['before_balance'])
                if t.get('after_balance'):
                    t['after_balance'] = self._decimal_from_128(t['after_balance'])
                
                # ObjectId转字符串
                if '_id' in t:
                    t['_id'] = str(t['_id'])
                if 'account_id' in t:
                    t['account_id'] = str(t['account_id'])
                transactions.append(t)
            
            logger.info(f"✅ 获取到 {len(transactions)} 条交易流水")
            return transactions
            
        except Exception as e:
            logger.error(f"❌ 获取交易流水失败: {e}", exc_info=True)
            return []
    
    # ========== 对外暴露的异步方法 ==========
    async def get_or_create_account(self, user: User) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_or_create_account_sync, user)
    
    async def recharge(self, user: User, order_no: str, amount: Decimal, 
                      description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        return await asyncio.to_thread(
            self._recharge_sync, user, order_no, amount, description, metadata
        )
    
    async def recharge_with_retry(self, user: User, order_no: str, amount: Decimal,
                                 description: str = '', metadata: Dict = None,
                                 max_retries: int = 3) -> Tuple[bool, str]:
        """带重试的充值（处理乐观锁冲突）"""
        for i in range(max_retries):
            success, msg = await self.recharge(user, order_no, amount, description, metadata)
            if success:
                return True, msg
            if "版本冲突" in msg and i < max_retries - 1:
                await asyncio.sleep(0.1 * (i + 1))
                continue
            return False, msg
        return False, f"充值失败，已重试{max_retries}次"
    
    async def consume(self, user: User, order_no: str, amount: Decimal,
                     description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        return await asyncio.to_thread(
            self._consume_sync, user, order_no, amount, description, metadata
        )
    
    async def get_balance(self, user: User) -> Dict[str, Decimal]:
        return await asyncio.to_thread(self._get_balance_sync, user)
    

    async def get_transactions(self, user: User, limit: int = 50, 
                            transaction_type: str = None,
                            status: str = None) -> list:
        """
        获取用户交易流水
        
        Args:
            user: 用户对象
            limit: 返回数量限制
            transaction_type: 交易类型 (RECHARGE/CONSUME/FREEZE)
            status: 交易状态 (FROZEN/CONFIRMED/CANCELLED/EXPIRED)
        """
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._get_transactions_sync,
            user, limit, status, transaction_type
        )
    
    async def get_pending_transactions(self, minutes: int = 5) -> list:
        """获取待处理的交易（用于补偿）"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
            return await asyncio.to_thread(
                lambda: list(self.transactions_collection.find({
                    "status": "PENDING",
                    "created_at": {"$lt": cutoff_time}
                }))
            )
        except Exception as e:
            logger.error(f"❌ 获取待处理交易失败: {e}")
            return []
        
    
    def _freeze_sync(self, user: User, order_no: str, amount: Decimal,
                 description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        """
        同步：冻结金额（预扣款）
        核心：增加frozen_amount字段，减少available balance
        """
        try:
            if amount <= Decimal('0'):
                return False, "冻结金额必须大于0"
            
            account = self._get_or_create_account_sync(user)
            if not account:
                return False, "账户不存在"
            
            # 幂等性校验
            existing_transaction = self.transactions_collection.find_one({"order_no": order_no})
            if existing_transaction:
                if existing_transaction.get('status') == 'FROZEN':
                    logger.info(f"✅ 订单{order_no}已冻结，跳过重复处理")
                    return True, "已冻结"
                elif existing_transaction.get('status') == 'CONFIRMED':
                    return True, "已确认扣款"
            
            current_balance = account['balance']
            current_frozen = account['frozen_amount']
            
            # 检查可用余额（balance - frozen）是否足够
            available_balance = current_balance - current_frozen
            if available_balance < amount:
                return False, f"可用余额不足，当前可用: {available_balance}⚡，需要: {amount}⚡"
            
            amount_128 = self._decimal_to_128(amount)
            
            # 更新余额和冻结金额
            result = self.accounts_collection.find_one_and_update(
                {
                    "user_id": str(user.id),
                    "balance": {"$gte": self._decimal_to_128(current_frozen + amount)},
                    "version": account['version']
                },
                {
                    "$inc": {
                        "frozen_amount": amount_128,
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                return False, "冻结失败，余额可能不足"
            
            # 创建冻结交易记录
            clean_metadata = self._clean_decimal_in_dict(metadata or {})
            transaction = {
                "_id": ObjectId(),
                "account_id": str(account['_id']),
                "user_id": str(user.id),
                "username": user.username,
                "order_no": order_no,
                "transaction_type": "FREEZE",
                "amount": amount_128,
                "before_balance": self._decimal_to_128(current_balance),
                "after_balance": None,
                "status": "FROZEN",
                "description": description,
                "metadata": clean_metadata,
                "created_at": datetime.utcnow(),
                "completed_at": None
            }
            
            try:
                self.transactions_collection.insert_one(transaction)
            except errors.DuplicateKeyError:
                # 订单已存在，可能是并发创建
                existing = self.transactions_collection.find_one({"order_no": order_no})
                if existing and existing.get('status') == 'FROZEN':
                    return True, "已冻结"
                return False, "订单冲突"
            
            logger.info(f"✅ 金额冻结成功: {user.username}, 冻结: {amount}⚡")
            return True, "冻结成功"
            
        except Exception as e:
            logger.error(f"❌ 冻结失败: {e}", exc_info=True)
            return False, f"冻结失败: {str(e)}"

    def _confirm_consume_sync(self, order_no: str) -> Tuple[bool, str]:
        """
        同步：确认消费（将冻结转为实际扣款）
        """
        try:
            # 查找冻结记录
            frozen_tx = self.transactions_collection.find_one({
                "order_no": order_no,
                "status": "FROZEN",
                "transaction_type": "FREEZE"
            })
            
            if not frozen_tx:
                return False, "未找到冻结记录"
            
            # 更新账户：减少balance，减少frozen_amount
            account = self.accounts_collection.find_one({
                "_id": ObjectId(frozen_tx['account_id'])
            })
            
            if not account:
                return False, "账户不存在"
            
            frozen_amount = self._decimal_from_128(frozen_tx['amount'])
            amount_128 = self._decimal_to_128(frozen_amount)
            
            result = self.accounts_collection.find_one_and_update(
                {
                    "_id": account['_id'],
                    "frozen_amount": {"$gte": amount_128},
                    "balance": {"$gte": amount_128}
                },
                {
                    "$inc": {
                        "balance": self._decimal_to_128(-frozen_amount),
                        "frozen_amount": self._decimal_to_128(-frozen_amount),
                        "total_consumed": amount_128,
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                return False, "确认扣款失败"
            
            # 更新交易记录
            self.transactions_collection.update_one(
                {"_id": frozen_tx['_id']},
                {
                    "$set": {
                        "transaction_type": "CONSUME",
                        "status": "CONFIRMED",
                        "completed_at": datetime.utcnow(),
                        "after_balance": self._decimal_to_128(
                            self._decimal_from_128(result['balance'])
                        )
                    }
                }
            )
            
            logger.info(f"✅ 确认扣款成功: {order_no}, 金额: {frozen_amount}⚡")
            return True, "扣款成功"
            
        except Exception as e:
            logger.error(f"❌ 确认扣款失败: {e}", exc_info=True)
            return False, f"确认失败: {str(e)}"

    def _cancel_consume_sync(self, order_no: str, reason: str = '') -> Tuple[bool, str]:
        """
        同步：取消消费（解冻金额）
        """
        try:
            # 查找冻结记录
            frozen_tx = self.transactions_collection.find_one({
                "order_no": order_no,
                "status": "FROZEN",
                "transaction_type": "FREEZE"
            })
            
            if not frozen_tx:
                return False, "未找到冻结记录"
            
            # 幂等性检查
            if frozen_tx.get('status') == 'CANCELLED':
                return True, "已取消"
            
            # 获取账户
            account = self.accounts_collection.find_one({
                "_id": ObjectId(frozen_tx['account_id'])
            })
            
            if not account:
                return False, "账户不存在"
            
            frozen_amount = self._decimal_from_128(frozen_tx['amount'])
            amount_128 = self._decimal_to_128(frozen_amount)
            
            # 解冻：只减少 frozen_amount（balance 不变）
            result = self.accounts_collection.find_one_and_update(
                {
                    "_id": account['_id'],
                    "frozen_amount": {"$gte": amount_128}
                },
                {
                    "$inc": {
                        "frozen_amount": self._decimal_to_128(-frozen_amount),
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                return False, "解冻失败"
            
            # 计算解冻后的可用余额
            current_balance = self._decimal_from_128(result['balance'])
            current_frozen = self._decimal_from_128(result['frozen_amount'])
            after_balance = current_balance - current_frozen
            
            # 更新交易记录：改为取消状态，保持 FREEZE 类型
            self.transactions_collection.update_one(
                {"_id": frozen_tx['_id']},
                {
                    "$set": {
                        "status": "CANCELLED",  # ✅ 状态改为取消
                        "completed_at": datetime.utcnow(),
                        "after_balance": self._decimal_to_128(after_balance),
                        "metadata": {
                            **(frozen_tx.get('metadata') or {}),
                            "cancel_reason": reason
                        }
                    }
                }
            )
            
            logger.info(f"✅ 取消冻结成功: {order_no}, 解冻: {frozen_amount}⚡")
            return True, "解冻成功"
            
        except Exception as e:
            logger.error(f"❌ 取消冻结失败: {e}", exc_info=True)
            return False, f"取消失败: {str(e)}"

    # 对外暴露的异步方法
    async def freeze(self, user: User, order_no: str, amount: Decimal,
                    description: str = '', metadata: Dict = None) -> Tuple[bool, str]:
        return await asyncio.to_thread(
            self._freeze_sync, user, order_no, amount, description, metadata
        )

    async def confirm_consume(self, order_no: str) -> Tuple[bool, str]:
        return await asyncio.to_thread(self._confirm_consume_sync, order_no)

    async def cancel_consume(self, order_no: str, reason: str = '') -> Tuple[bool, str]:
        return await asyncio.to_thread(self._cancel_consume_sync, order_no, reason)
    
    def _get_expired_frozen_transactions_sync(self, timeout_minutes: int = 30) -> list:
        """
        同步：获取过期的冻结交易记录
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(minutes=timeout_minutes)
            
            # 查找状态为 FROZEN 且创建时间超过阈值的记录
            expired_transactions = list(self.transactions_collection.find({
                "status": "FROZEN",
                "transaction_type": "FREEZE",
                "created_at": {"$lt": cutoff_time}
            }).limit(100))  # 每次最多处理100条
            
            logger.info(f"🔍 找到 {len(expired_transactions)} 条过期冻结记录")
            return expired_transactions
            
        except Exception as e:
            logger.error(f"❌ 获取过期冻结记录失败: {e}", exc_info=True)
            return []
    
    def _compensate_expired_freeze_sync(self, transaction: Dict) -> Tuple[bool, str]:
        """
        同步：补偿处理单个过期的冻结记录
        """
        try:
            order_no = transaction['order_no']
            account_id = transaction['account_id']
            frozen_amount = self._decimal_from_128(transaction['amount'])
            amount_128 = self._decimal_to_128(frozen_amount)
            
            # 检查是否已经被处理过（防止重复补偿）
            current_tx = self.transactions_collection.find_one({
                "_id": transaction['_id']
            })
            
            if current_tx['status'] != 'FROZEN':
                logger.info(f"⏭️ 订单 {order_no} 状态已变更，跳过补偿")
                return True, "已处理"
            
            # 更新账户：解冻金额
            result = self.accounts_collection.find_one_and_update(
                {
                    "_id": ObjectId(account_id),
                    "frozen_amount": {"$gte": amount_128}
                },
                {
                    "$inc": {
                        "frozen_amount": self._decimal_to_128(-frozen_amount),
                        "version": 1
                    },
                    "$set": {
                        "updated_at": datetime.utcnow()
                    }
                },
                return_document=True
            )
            
            if not result:
                logger.error(f"❌ 补偿解冻失败: {order_no}")
                return False, "解冻失败"
            
            # ✅ 计算解冻后的可用余额
            current_balance = self._decimal_from_128(result['balance'])
            current_frozen = self._decimal_from_128(result['frozen_amount'])
            after_balance = current_balance - current_frozen
            
            # 更新交易记录状态
            self.transactions_collection.update_one(
                {"_id": transaction['_id']},
                {
                    "$set": {
                        "status": "EXPIRED",
                        "completed_at": datetime.utcnow(),
                        "after_balance": self._decimal_to_128(after_balance),  # ✅ 设置 after_balance
                        "metadata": {
                            **(transaction.get('metadata') or {}),
                            "compensated": True,
                            "compensated_at": datetime.utcnow().isoformat(),
                            "compensate_reason": "超时未确认"
                        }
                    }
                }
            )
            
            logger.info(f"✅ 补偿解冻成功: {order_no}, 金额: {frozen_amount}⚡, 解冻后可用余额: {after_balance}")
            return True, "补偿成功"
            
        except Exception as e:
            logger.error(f"❌ 补偿处理失败: {transaction.get('order_no')}, {e}", exc_info=True)
            return False, str(e)
    
    async def get_expired_frozen_transactions(self, timeout_minutes: int = 30) -> list:
        """获取过期的冻结交易记录"""
        return await asyncio.to_thread(
            self._get_expired_frozen_transactions_sync, 
            timeout_minutes
        )
    
    async def compensate_expired_freeze(self, transaction: Dict) -> Tuple[bool, str]:
        """补偿处理单个过期的冻结记录"""
        return await asyncio.to_thread(
            self._compensate_expired_freeze_sync,
            transaction
        )
    

# 全局算力账户服务实例
power_account_service = PowerAccountService()