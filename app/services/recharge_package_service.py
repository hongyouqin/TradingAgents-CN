from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.recharge_package import RechargePackage, RechargePackageCreate, RechargePackageUpdate
from app.core.database import get_database


class RechargePackageService:
    """充值套餐服务"""

    def __init__(self):
        self.collection_name = "recharge_packages"

    async def _get_collection(self):
        """获取集合"""
        db = get_database()
        return db[self.collection_name]

    async def init_default_packages(self):
        """初始化默认套餐（如果没有数据）"""
        collection = await self._get_collection()
        
        # 检查是否有数据
        count = await collection.count_documents({})
        if count > 0:
            return

        # 默认套餐数据
        default_packages = [
            {
                "package_id": "PACK_000",
                "name": "测试包",
                "price": 0.01,
                "power": 100,
                "bonus": 0,
                "popular": False,
                "description": "测试包⚡",
                "sort_order": 0,
                "is_active": True
            },
            {
                "package_id": "PACK_001",
                "name": "体验包",
                "price": 9.90,
                "power": 10,
                "bonus": 0,
                "popular": False,
                "description": "9.9元充值10⚡",
                "sort_order": 1,
                "is_active": True
            },
            {
                "package_id": "PACK_002",
                "name": "标准包",
                "price": 19.80,
                "power": 20,
                "bonus": 0,
                "popular": True,
                "description": "19.8元充值20⚡",
                "sort_order": 2,
                "is_active": True
            },
            {
                "package_id": "PACK_003",
                "name": "畅享包",
                "price": 49.00,
                "power": 50,
                "bonus": 2,
                "popular": False,
                "description": "49元充值50⚡+赠送2⚡",
                "sort_order": 3,
                "is_active": True
            },
            {
                "package_id": "PACK_004",
                "name": "尊享包",
                "price": 98.00,
                "power": 100,
                "bonus": 5,
                "popular": True,
                "description": "98元充值100⚡+赠送5⚡",
                "sort_order": 4,
                "is_active": True
            },
            {
                "package_id": "PACK_005",
                "name": "企业包",
                "price": 198.00,
                "power": 200,
                "bonus": 15,
                "popular": False,
                "description": "198元充值200⚡+赠送15⚡",
                "sort_order": 5,
                "is_active": True
            }
        ]

        for pkg in default_packages:
            pkg["created_at"] = datetime.utcnow()
            pkg["updated_at"] = datetime.utcnow()
            await collection.insert_one(pkg)

        print(f"已初始化 {len(default_packages)} 个默认充值套餐")

    def _process_document(self, doc: dict) -> dict:
            """处理MongoDB文档，转换ObjectId为字符串"""
            if doc and "_id" in doc and isinstance(doc["_id"], ObjectId):
                doc["_id"] = str(doc["_id"])
            return doc
    
    async def get_active_packages(self) -> List[RechargePackage]:
        """获取所有上架套餐"""
        collection = await self._get_collection()
        cursor = collection.find({"is_active": True}).sort("sort_order", 1)
        packages = []
        async for doc in cursor:
            packages.append(RechargePackage(**self._process_document(doc)))
        return packages

    async def get_package_by_id(self, package_id: str) -> Optional[RechargePackage]:
        """根据套餐ID获取套餐（兼容旧版PACK_XXX格式）"""
        collection = await self._get_collection()
        doc = await collection.find_one({"package_id": package_id, "is_active": True})
        if doc:
            return RechargePackage(**self._process_document(doc))
        return None

    async def get_package_by_object_id(self, id: str) -> Optional[RechargePackage]:
        """根据MongoDB ObjectId获取套餐"""
        collection = await self._get_collection()
        try:
            # 验证id是否为有效的ObjectId格式
            if not ObjectId.is_valid(id):
                return None
                
            doc = await collection.find_one({"_id": ObjectId(id)})
            if doc:
                return RechargePackage(**self._process_document(doc))
        except Exception as e:
            # 可以记录日志
            print(f"Error fetching package by object id: {e}")
        return None

    async def create_package(self, package_data: RechargePackageCreate) -> RechargePackage:
        """创建新套餐"""
        collection = await self._get_collection()
        
        # 检查package_id是否已存在
        existing = await collection.find_one({"package_id": package_data.package_id})
        if existing:
            raise ValueError(f"套餐ID {package_data.package_id} 已存在")

        now = datetime.utcnow()
        # 对于Pydantic v2
        doc = package_data.model_dump() if hasattr(package_data, 'model_dump') else package_data.dict()
        doc["created_at"] = now
        doc["updated_at"] = now
        
        result = await collection.insert_one(doc)
        
        # 获取插入的文档
        created_doc = await collection.find_one({"_id": result.inserted_id})
        return RechargePackage(**self._process_document(created_doc))

    async def update_package(self, package_id: str, update_data: RechargePackageUpdate) -> Optional[RechargePackage]:
        """更新套餐"""
        collection = await self._get_collection()
        
        # 对于Pydantic v2
        if hasattr(update_data, 'model_dump'):
            update_dict = {k: v for k, v in update_data.model_dump(exclude_unset=True).items() if v is not None}
        else:
            update_dict = {k: v for k, v in update_data.dict(exclude_unset=True).items() if v is not None}
            
        if not update_dict:
            return None

        update_dict["updated_at"] = datetime.utcnow()
        
        result = await collection.find_one_and_update(
            {"package_id": package_id},
            {"$set": update_dict},
            return_document=ReturnDocument.AFTER  # 使用ReturnDocument枚举
        )
        
        if result:
            return RechargePackage(**self._process_document(result))
        return None

    async def delete_package(self, package_id: str) -> bool:
        """软删除套餐"""
        collection = await self._get_collection()
        result = await collection.update_one(
            {"package_id": package_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
        )
        return result.modified_count > 0

    async def hard_delete_package(self, package_id: str) -> bool:
        """硬删除套餐（谨慎使用）"""
        collection = await self._get_collection()
        result = await collection.delete_one({"package_id": package_id})
        return result.deleted_count > 0

# 创建单例
recharge_package_service = RechargePackageService()