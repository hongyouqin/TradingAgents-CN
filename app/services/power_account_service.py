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



class PowerAccountService:
    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB]
        self.accounts_collection = self.db.power_accounts
        self.users_collection = self.db.users
        self.transactions_collection = self.db.power_transactions
        self._create_indexes()
        
    def _create_indexes(self):
        """创建索引：user_id 唯一索引 + 交易幂等"""
        # 🔥 核心：一个用户只能有一个账户
        self.accounts_collection.create_index("user_id", unique=True)
        self.accounts_collection.create_index("username")
        
        # 交易流水幂等保障
        self.transactions_collection.create_index("account_id")
        self.transactions_collection.create_index("order_no", unique=True)
        self.transactions_collection.create_index([("created_at", -1)])
        self.transactions_collection.create_index("status")
    
    def _decimal_to_128(self, value: Decimal) -> Decimal128:
        try:
            return Decimal128(str(value))
        except Exception as e:
            logger.error(f"Decimal转Decimal128失败: {e}")
            raise
    
    def _decimal_from_128(self, value: Any) -> Decimal:
        try:
            if isinstance(value, Decimal128):
                return Decimal(str(value))
            elif isinstance(value, (int, float, str)):
                return Decimal(str(value))
            elif isinstance(value, Decimal):
                return value
            else:
                raise TypeError(f"不支持的类型: {type(value)}")
        except Exception as e:
            logger.error(f"Decimal128转Decimal失败: {e}")
            raise
    
    def _clean_decimal_in_dict(self, data: Dict) -> Dict:
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
        try:
            user_id_str = str(user.id)
            if not ObjectId.is_valid(user_id_str):
                logger.error(f"❌ 无效的用户ID: {user_id_str}")
                return None

            real_user = self.users_collection.find_one({"_id": ObjectId(user_id_str)})
            if not real_user:
                logger.error(f"❌ 用户不存在于用户表，禁止创建账户: {user_id_str}")
                return None

            account = self.accounts_collection.find_one({"user_id": user_id_str})
            if account:
                account['balance'] = self._decimal_from_128(account['balance'])
                account['total_recharged'] = self._decimal_from_128(account['total_recharged'])
                account['total_consumed'] = self._decimal_from_128(account['total_consumed'])
                account['frozen_amount'] = self._decimal_from_128(account['frozen_amount'])
                logger.info(f"✅ 获取账户: {user.username}({user_id_str})")
                return account

            now = datetime.utcnow()
            zero = Decimal('0.00')
            account_doc = {
                "user_id": user_id_str,
                "username": user.username,
                "balance": self._decimal_to_128(zero),
                "total_recharged": self._decimal_to_128(zero),
                "total_consumed": self._decimal_to_128(zero),
                "frozen_amount": self._decimal_to_128(zero),
                "version": 0,
                "created_at": now,
                "updated_at": now
            }

            result = self.accounts_collection.insert_one(account_doc)
            account_doc['_id'] = result.inserted_id
            account_doc['balance'] = zero
            account_doc['total_recharged'] = zero
            account_doc['total_consumed'] = zero
            account_doc['frozen_amount'] = zero

            logger.info(f"✅ 新账户创建成功: {user.username}({user_id_str})")
            return account_doc

        except errors.DuplicateKeyError:
            logger.warning(f"⚠️ 账户并发冲突，重试获取: {user.username}")
            time.sleep(0.05)
            return self._get_or_create_account_sync(user)
        except Exception as e:
            logger.error(f"❌ 获取/创建账户失败: {e}", exc_info=True)
            return None

    # ------------------------------ 充值 ------------------------------
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
            return False, "金额必须大于0"
        
        account = self._get_or_create_account_sync(user)
        if not account:
            return False, "账户不存在或用户无效"

        existing = self.transactions_collection.find_one({"order_no": order_no})
        if existing:
            if existing.get('status') == 'CONFIRMED':
                return True, "订单已处理"
            elif existing.get('status') == 'PENDING':
                return self._complete_pending_recharge(existing, account)

        amount_128 = self._decimal_to_128(amount)
        current_balance = account['balance']
        clean_meta = self._clean_decimal_in_dict(metadata or {})

        tx = {
            "_id": ObjectId(),
            "account_id": str(account['_id']),
            "user_id": str(user.id),
            "username": user.username,
            "order_no": order_no,
            "transaction_type": "RECHARGE",
            "amount": amount_128,
            "before_balance": self._decimal_to_128(current_balance),
            "after_balance": None,
            "status": "PENDING",
            "description": description,
            "metadata": clean_meta,
            "created_at": datetime.utcnow(),
            "completed_at": None
        }

        try:
            self.transactions_collection.insert_one(tx)
        except errors.DuplicateKeyError:
            return True, "订单已存在"

        for retry in range(3):
            try:
                current = self.accounts_collection.find_one({"_id": account['_id']})
                if not current:
                    raise Exception("账户不存在")

                res = self.accounts_collection.find_one_and_update(
                    {"_id": account['_id'], "version": current['version']},
                    {
                        "$inc": {"balance": amount_128, "total_recharged": amount_128, "version": 1},
                        "$set": {"updated_at": datetime.utcnow()}
                    },
                    return_document=True
                )

                if res:
                    after = self._decimal_from_128(res['balance'])
                    self.transactions_collection.update_one(
                        {"_id": tx['_id']},
                        {"$set": {"status": "CONFIRMED", "completed_at": datetime.utcnow(),
                                  "after_balance": self._decimal_to_128(after)}}
                    )
                    logger.info(f"✅ 充值成功: {user.username} 金额:{amount} 余额:{after}")
                    return True, "充值成功"

                time.sleep(0.1 * (retry + 1))
            except Exception as e:
                if retry == 2:
                    raise
                time.sleep(0.1 * (retry + 1))

        self.transactions_collection.update_one({"_id": tx['_id']}, {"$set": {"status": "FAILED"}})
        return False, "充值失败"

    def _complete_pending_recharge(self, tx: Dict, account: Dict) -> Tuple[bool, str]:
        try:
            current = self.accounts_collection.find_one({"_id": account['_id']})
            if not current:
                return False, "账户不存在"

            tx_amt = self._decimal_from_128(tx['amount'])
            before = self._decimal_from_128(tx['before_balance'])
            expected = before + tx_amt
            actual = self._decimal_from_128(current['balance'])

            if abs(actual - expected) < Decimal('0.01'):
                self.transactions_collection.update_one(
                    {"_id": tx['_id']},
                    {"$set": {"status": "CONFIRMED", "completed_at": datetime.utcnow()}}
                )
                return True, "充值完成"
            return False, "需要重试"
        except:
            return False, "恢复失败"

    # ------------------------------ 消费 ------------------------------
    def _consume_sync(self, user: User, order_no: str, amount: Decimal, description: str='', metadata: Dict=None) -> Tuple[bool, str]:
        try:
            if amount <= 0:
                return False, "金额必须大于0"
            account = self._get_or_create_account_sync(user)
            if not account:
                return False, "账户不存在"

            existing = self.transactions_collection.find_one({"order_no": order_no})
            if existing:
                if existing.get('status') == 'CONFIRMED':
                    return True, "已处理"
                elif existing.get('status') == 'PENDING':
                    return self._complete_pending_consume(existing, account)

            if account['balance'] < amount:
                return False, "余额不足"

            amt_128 = self._decimal_to_128(amount)
            clean_meta = self._clean_decimal_in_dict(metadata or {})
            tx = {
                "_id": ObjectId(),
                "account_id": str(account['_id']),
                "user_id": str(user.id),
                "username": user.username,
                "order_no": order_no,
                "transaction_type": "CONSUME",
                "amount": self._decimal_to_128(-amount),
                "before_balance": self._decimal_to_128(account['balance']),
                "after_balance": None,
                "status": "PENDING",
                "description": description,
                "metadata": clean_meta,
                "created_at": datetime.utcnow(),
                "completed_at": None
            }

            try:
                self.transactions_collection.insert_one(tx)
            except errors.DuplicateKeyError:
                return True, "订单已存在"

            res = self.accounts_collection.find_one_and_update(
                {"user_id": str(user.id), "balance": {"$gte": amt_128}, "version": account['version']},
                {
                    "$inc": {"balance": self._decimal_to_128(-amount), "total_consumed": amt_128, "version": 1},
                    "$set": {"updated_at": datetime.utcnow()}
                },
                return_document=True
            )

            if not res:
                self.transactions_collection.update_one({"_id": tx['_id']}, {"$set": {"status": "FAILED"}})
                return False, "消费失败"

            after = self._decimal_from_128(res['balance'])
            self.transactions_collection.update_one(
                {"_id": tx['_id']},
                {"$set": {"status": "CONFIRMED", "completed_at": datetime.utcnow(),
                          "after_balance": self._decimal_to_128(after)}}
            )
            logger.info(f"✅ 消费成功: {user.username} 金额:{amount} 余额:{after}")
            return True, "消费成功"
        except Exception as e:
            return False, f"异常:{str(e)}"

    def _complete_pending_consume(self, tx: Dict, account: Dict) -> Tuple[bool, str]:
        return True, "已处理"

    # ------------------------------ 余额查询 ------------------------------
    def _get_balance_sync(self, user: User) -> Dict[str, Decimal]:
        account = self._get_or_create_account_sync(user)
        if not account:
            z = Decimal('0')
            return {'balance':z,'frozen':z,'available':z,'total_recharged':z,'total_consumed':z}
        return {
            'balance': account['balance'],
            'frozen': account['frozen_amount'],
            'available': account['balance'] - account['frozen_amount'],
            'total_recharged': account['total_recharged'],
            'total_consumed': account['total_consumed']
        }

    # ------------------------------ 流水查询 ------------------------------
    def _get_transactions_sync(self, user: User, limit=50, status=None, t_type=None):
        try:
            q = {"user_id": str(user.id)}
            if status: q["status"] = status
            if t_type and t_type != 'ALL': q["transaction_type"] = t_type

            cursor = self.transactions_collection.find(q).sort("created_at", -1).limit(limit)
            arr = []
            for t in cursor:
                t['amount'] = self._decimal_from_128(t['amount'])
                t['before_balance'] = self._decimal_from_128(t['before_balance'])
                if t.get('after_balance'): t['after_balance'] = self._decimal_from_128(t['after_balance'])
                t['_id'] = str(t['_id'])
                arr.append(t)
            return arr
        except:
            return []

    # ------------------------------ 对外异步接口 ------------------------------
    async def get_or_create_account(self, user: User):
        return await asyncio.to_thread(self._get_or_create_account_sync, user)

    async def recharge(self, user: User, order_no: str, amount: Decimal, description='', metadata=None):
        return await asyncio.to_thread(self._recharge_sync, user, order_no, amount, description, metadata)

    async def consume(self, user: User, order_no: str, amount: Decimal, description='', metadata=None):
        return await asyncio.to_thread(self._consume_sync, user, order_no, amount, description, metadata)

    async def get_balance(self, user: User):
        return await asyncio.to_thread(self._get_balance_sync, user)

    async def get_transactions(self, user: User, limit=50, status=None, t_type=None):
        return await asyncio.to_thread(self._get_transactions_sync, user, limit, status, t_type)

    # ------------------------------ 冻结/解冻/确认消费 ------------------------------
    def _freeze_sync(self, user: User, order_no: str, amount: Decimal, description='', metadata=None) -> Tuple[bool, str]:
        try:
            if amount <=0: return False, "金额必须大于0"
            account = self._get_or_create_account_sync(user)
            if not account: return False, "账户不存在"
            existing = self.transactions_collection.find_one({"order_no": order_no})
            if existing:
                if existing.get('status')=='FROZEN': return True,'已冻结'
                if existing.get('status')=='CONFIRMED': return True,'已完成'
            available = account['balance'] - account['frozen_amount']
            if available < amount: return False, '可用余额不足'
            amt128 = self._decimal_to_128(amount)
            res = self.accounts_collection.find_one_and_update(
                {"user_id": str(user.id), "balance": {"$gte": self._decimal_to_128(account['frozen_amount']+amount)}},
                {"$inc": {"frozen_amount": amt128, "version":1}, "$set": {"updated_at":datetime.utcnow()}},
                return_document=True
            )
            if not res: return False, "冻结失败"
            tx = {
                "_id": ObjectId(), "account_id": str(account['_id']), "user_id": str(user.id),
                "username": user.username, "order_no": order_no, "transaction_type": "FREEZE",
                "amount": amt128, "before_balance": self._decimal_to_128(account['balance']),
                "status": "FROZEN", "description": description, "metadata": self._clean_decimal_in_dict(metadata or {}),
                "created_at": datetime.utcnow()
            }
            try:
                self.transactions_collection.insert_one(tx)
            except errors.DuplicateKeyError:
                return True, "已冻结"
            logger.info(f"✅ 冻结成功: {user.username} 金额:{amount}")
            return True, "冻结成功"
        except Exception as e:
            return False, str(e)

    def _confirm_consume_sync(self, order_no: str) -> Tuple[bool, str]:
        tx = self.transactions_collection.find_one({"order_no":order_no,"status":"FROZEN","transaction_type":"FREEZE"})
        if not tx: return False, "无冻结记录"
        
        # 🔥 修复：先转 Decimal，再取负，再转回 Decimal128
        amt = self._decimal_from_128(tx['amount'])
        amt_neg_128 = self._decimal_to_128(-amt)
        
        res = self.accounts_collection.find_one_and_update(
            {"_id": ObjectId(tx['account_id']), "frozen_amount": {"$gte": tx['amount']}, "balance": {"$gte": tx['amount']}},
            {
                "$inc": {
                    "balance": amt_neg_128,
                    "frozen_amount": amt_neg_128,
                    "total_consumed": tx['amount'],
                    "version": 1
                }
            },
            return_document=True
        )
        if not res: return False, "确认失败"
        self.transactions_collection.update_one({"_id":tx['_id']},{"$set":{"status":"CONFIRMED","transaction_type":"CONSUME","completed_at":datetime.utcnow()}})
        return True, "确认成功"

    def _cancel_consume_sync(self, order_no: str, reason='') -> Tuple[bool, str]:
        tx = self.transactions_collection.find_one({"order_no":order_no,"status":"FROZEN"})
        if not tx: return False, "无记录"
        
        # 🔥 修复：先转 Decimal，再取负，再转回 Decimal128
        amt = self._decimal_from_128(tx['amount'])
        amt_neg_128 = self._decimal_to_128(-amt)
        
        res = self.accounts_collection.find_one_and_update(
            {"_id": ObjectId(tx['account_id']), "frozen_amount": {"$gte": tx['amount']}},
            {"$inc": {"frozen_amount": amt_neg_128, "version":1}},
            return_document=True
        )
        if not res: return False, "解冻失败"
        self.transactions_collection.update_one({"_id":tx['_id']},{"$set":{"status":"CANCELLED","completed_at":datetime.utcnow()}})
        return True, "已解冻"

    async def freeze(self, user: User, order_no: str, amount: Decimal, description='', metadata=None):
        return await asyncio.to_thread(self._freeze_sync, user, order_no, amount, description, metadata)

    async def confirm_consume(self, order_no: str):
        return await asyncio.to_thread(self._confirm_consume_sync, order_no)

    async def cancel_consume(self, order_no: str, reason=''):
        return await asyncio.to_thread(self._cancel_consume_sync, order_no, reason)

    # ------------------------------ 过期补偿 ------------------------------
    def _get_expired_frozen_sync(self, minutes=30):
        try:
            return list(self.transactions_collection.find({
                "status":"FROZEN","transaction_type":"FREEZE",
                "created_at":{"$lt":datetime.utcnow()-timedelta(minutes=minutes)}
            }).limit(100))
        except:
            return []

    def _compensate_expired_sync(self, tx):
        try:
            amt = self._decimal_from_128(tx['amount'])
            amt_neg_128 = self._decimal_to_128(-amt)
            
            res = self.accounts_collection.find_one_and_update(
                {"_id": ObjectId(tx['account_id']), "frozen_amount": {"$gte": tx['amount']}},
                {"$inc": {"frozen_amount": amt_neg_128, "version": 1}}
            )
            if not res: return False, "失败"
            self.transactions_collection.update_one({"_id": tx['_id']}, {"$set": {"status": "EXPIRED", "completed_at": datetime.utcnow()}})
            logger.info(f"✅ 过期解冻: {tx['order_no']}")
            return True, "成功"
        except Exception as e:
            logger.error(f"❌ 过期补偿失败: {e}")
            return False, "失败"

    async def get_expired_frozen_transactions(self, minutes=30):
        return await asyncio.to_thread(self._get_expired_frozen_sync, minutes)

    async def compensate_expired_freeze(self, tx):
        return await asyncio.to_thread(self._compensate_expired_sync, tx)

power_account_service = PowerAccountService()


