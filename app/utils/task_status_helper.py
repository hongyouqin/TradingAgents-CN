from typing import Dict, Optional, Union


class TaskStatusHelper:
    """任务状态辅助类"""
    
    # 前端可用的状态
    FRONTEND_STATUSES = ["processing", "pending", "completed", "failed", "cancelled"]
    
    # 内部使用的状态
    INTERNAL_STATUSES = ["running", "pending", "completed", "failed", "cancelled"]
    
    # 映射表：前端 -> 内部
    FRONTEND_TO_INTERNAL = {
        "processing": "running",
        "pending": "pending",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled"
    }
    
    # 映射表：内部 -> 前端
    INTERNAL_TO_FRONTEND = {
        "running": "processing",
        "pending": "pending",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled"
    }
    
    @classmethod
    def to_internal(cls, frontend_status: Optional[str]) -> Optional[str]:
        """前端状态转内部状态"""
        if not frontend_status:
            return None
        return cls.FRONTEND_TO_INTERNAL.get(frontend_status, frontend_status)
    
    @classmethod
    def to_frontend(cls, internal_status: Optional[str]) -> Optional[str]:
        """内部状态转前端状态"""
        if not internal_status:
            return None
        return cls.INTERNAL_TO_FRONTEND.get(internal_status, internal_status)
    
    @classmethod
    def get_db_condition(cls, frontend_status: Optional[str]) -> Optional[Union[str, Dict]]:
        """获取数据库查询条件"""
        if not frontend_status:
            return None
        
        internal = cls.to_internal(frontend_status)
        
        # 特殊处理：查询 processing 时要同时查 running 和 processing
        if frontend_status == "processing":
            return {"$in": ["running", "processing"]}
        
        return internal
    
    @classmethod
    def get_mem_condition(cls, frontend_status: Optional[str]) -> Optional[str]:
        """获取内存查询条件"""
        return cls.to_internal(frontend_status)
    
    @classmethod
    def get_conditions(cls, frontend_status: Optional[str]) -> tuple:
        """同时获取内存和数据库查询条件"""
        return (
            cls.get_mem_condition(frontend_status),
            cls.get_db_condition(frontend_status)
        )
