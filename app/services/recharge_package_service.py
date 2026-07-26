from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.models.recharge_package import RechargePackage, RechargePackageCreate, RechargePackageUpdate
from app.core.database import get_database


class RechargePackageService:
    """充值套餐服务"""

    def __init__(self):
        self.collection_name = "recharge_packages"

    async def _get_collection(self):
        """获取集合"""
        db: AsyncIOMotorDatabase = get_database()
        coll = db[self.collection_name]
        # 创建唯一索引（仅首次执行生效）
        await coll.create_index("package_id", unique=True)
        return coll

    async def init_default_packages(self):
        """初始化默认套餐（无数据时初始化，兼容多进程并发）"""
        collection = await self._get_collection()
        # 使用count_documents存在微小并发问题，改用find_one判断
        exist_doc = await collection.find_one({}, {"_id": 1})
        if exist_doc:
            return

        # ==========【优化后的套餐配置｜方案1梯度赠送版本】==========
        default_packages = [
            {
                "package_id": "PACK_000",
                "name": "测试包",
                "price": 0.01,
                "power": 100,
                "bonus": 0,
                "popular": False,
                "description": "测试包⚡（前端面向普通用户隐藏）",
                "sort_order": 0,
                "is_active": False
            },
            {
                "package_id": "PACK_001",
                "name": "体验包",
                "price": 9.90,
                "power": 10,
                "bonus": 0,
                "popular": False,
                "description": "9.9元充值10⚡，新人小额试用",
                "sort_order": 1,
                "is_active": True
            },
            {
                "package_id": "PACK_002",
                "name": "标准包",
                "price": 19.80,
                "power": 20,
                "bonus": 1,
                "popular": False,
                "description": "19.8元充值20⚡，额外赠送1⚡",
                "sort_order": 2,
                "is_active": True
            },
            {
                "package_id": "PACK_003",
                "name": "畅享包",
                "price": 49.00,
                "power": 50,
                "bonus": 5,
                "popular": False,
                "description": "49元充值50⚡+赠送5⚡，轻度囤电优选",
                "sort_order": 3,
                "is_active": True
            },
            {
                "package_id": "PACK_004",
                "name": "尊享包",
                "price": 98.00,
                "power": 100,
                "bonus": 12,
                "popular": True,  # 主推热门套餐
                "description": "98元充值100⚡+赠送12⚡，性价比首选",
                "sort_order": 4,
                "is_active": True
            },
            {
                "package_id": "PACK_005",
                "name": "企业包",
                "price": 198.00,
                "power": 200,
                "bonus": 35,
                "popular": False,
                "description": "198元充值200⚡+赠送35⚡，大额囤电最划算",
                "sort_order": 5,
                "is_active": True
            }
        ]

        now = datetime.utcnow()
        insert_list = []
        for pkg in default_packages:
            pkg["created_at"] = now
            pkg["updated_at"] = now
            insert_list.append(pkg)

        await collection.insert_many(insert_list)
        print(f"✅ 已初始化 {len(default_packages)} 个默认充值套餐")

    @staticmethod
    def _process_document(doc: Optional[dict]) -> Optional[dict]:
        """处理MongoDB文档，转换ObjectId为字符串"""
        if not doc:
            return None
        if "_id" in doc and isinstance(doc["_id"], ObjectId):
            doc["_id"] = str(doc["_id"])
        return doc

    async def get_active_packages(self) -> List[RechargePackage]:
        """获取所有上架套餐（前端用户展示）"""
        collection = await self._get_collection()
        cursor = collection.find({"is_active": True}).sort("sort_order", 1)
        packages = []
        async for doc in cursor:
            pkg_doc = self._process_document(doc)
            packages.append(RechargePackage(**pkg_doc))
        return packages

    async def get_all_packages(self) -> List[RechargePackage]:
        """【后台管理】获取全部套餐（包含已下架）"""
        collection = await self._get_collection()
        cursor = collection.find({}).sort("sort_order", 1)
        packages = []
        async for doc in cursor:
            pkg_doc = self._process_document(doc)
            packages.append(RechargePackage(**pkg_doc))
        return packages

    async def get_package_by_id(self, package_id: str) -> Optional[RechargePackage]:
        """根据套餐package_id获取上架套餐（用户下单使用）"""
        collection = await self._get_collection()
        doc = await collection.find_one({"package_id": package_id, "is_active": True})
        doc = self._process_document(doc)
        return RechargePackage(**doc) if doc else None

    async def get_package_admin_by_id(self, package_id: str) -> Optional[RechargePackage]:
        """【后台】根据package_id查询，忽略上下架状态"""
        collection = await self._get_collection()
        doc = await collection.find_one({"package_id": package_id})
        doc = self._process_document(doc)
        return RechargePackage(**doc) if doc else None

    async def get_package_by_object_id(self, oid_str: str) -> Optional[RechargePackage]:
        """根据MongoDB ObjectId获取套餐"""
        collection = await self._get_collection()
        if not ObjectId.is_valid(oid_str):
            return None
        doc = await collection.find_one({"_id": ObjectId(oid_str)})
        doc = self._process_document(doc)
        return RechargePackage(**doc) if doc else None

    async def create_package(self, package_data: RechargePackageCreate) -> RechargePackage:
        """创建新套餐"""
        collection = await self._get_collection()

        existing = await collection.find_one({"package_id": package_data.package_id})
        if existing:
            raise ValueError(f"套餐ID {package_data.package_id} 已存在")

        now = datetime.utcnow()
        # 兼容Pydantic v1 / v2
        if hasattr(package_data, "model_dump"):
            doc = package_data.model_dump()
        else:
            doc = package_data.dict()
        doc["created_at"] = now
        doc["updated_at"] = now

        result = await collection.insert_one(doc)
        created_doc = await collection.find_one({"_id": result.inserted_id})
        return RechargePackage(**self._process_document(created_doc))

    async def update_package(self, package_id: str, update_data: RechargePackageUpdate) -> Optional[RechargePackage]:
        """更新套餐"""
        collection = await self._get_collection()

        if hasattr(update_data, "model_dump"):
            update_dict = update_data.model_dump(exclude_unset=True)
        else:
            update_dict = update_data.dict(exclude_unset=True)
        update_dict = {k: v for k, v in update_dict.items() if v is not None}
        if not update_dict:
            return None

        update_dict["updated_at"] = datetime.utcnow()

        result = await collection.find_one_and_update(
            {"package_id": package_id},
            {"$set": update_dict},
            return_document=ReturnDocument.AFTER
        )
        result = self._process_document(result)
        return RechargePackage(**result) if result else None

    async def delete_package(self, package_id: str) -> bool:
        """软删除套餐（下架，推荐业务使用）"""
        collection = await self._get_collection()
        res = await collection.update_one(
            {"package_id": package_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
        )
        return res.modified_count > 0

    async def hard_delete_package(self, package_id: str) -> bool:
        """硬删除套餐（谨慎使用，仅清理测试数据）"""
        collection = await self._get_collection()
        res = await collection.delete_one({"package_id": package_id})
        return res.deleted_count > 0


# 创建单例
recharge_package_service = RechargePackageService()