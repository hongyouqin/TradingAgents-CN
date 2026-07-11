"""
股票分析服务
将现有的TradingAgents分析功能包装成API服务
"""

import asyncio
import uuid
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Set
from pathlib import Path
import sys

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 初始化TradingAgents日志系统
from tradingagents.utils.logging_init import init_logging
init_logging()

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from app.services.simple_analysis_service import create_analysis_config, get_provider_by_model_name
from app.models.analysis import (
    AnalysisParameters, AnalysisResult, AnalysisTask, AnalysisBatch,
    AnalysisStatus, BatchStatus, SingleAnalysisRequest, BatchAnalysisRequest
)
from app.models.user import PyObjectId
from bson import ObjectId
from app.core.database import get_mongo_db
from app.core.redis_client import get_redis_service, RedisKeys
from app.services.queue_service import QueueService
from app.core.database import get_redis_client
from app.services.redis_progress_tracker import RedisProgressTracker
from app.services.config_provider import provider as config_provider
from app.services.task_queue import DEFAULT_USER_CONCURRENT_LIMIT, GLOBAL_CONCURRENT_LIMIT, VISIBILITY_TIMEOUT_SECONDS
from app.services.usage_statistics_service import UsageStatisticsService
from app.models.config import UsageRecord

import logging
logger = logging.getLogger(__name__)

class AnalysisService:
    """股票分析服务类"""

    def __init__(self):
        # 获取Redis客户端
        redis_client = get_redis_client()
        self.queue_service = QueueService(redis_client)
        # 初始化使用统计服务
        self.usage_service = UsageStatisticsService()
        self._trading_graph_cache = {}
        # 进度跟踪器缓存
        self._progress_trackers: Dict[str, RedisProgressTracker] = {}

    def _convert_user_id(self, user_id: str) -> PyObjectId:
        """将字符串用户ID转换为PyObjectId"""
        try:
            logger.info(f"🔄 开始转换用户ID: {user_id} (类型: {type(user_id)})")

            # 如果是admin用户，使用固定的ObjectId
            if user_id == "admin":
                # 使用固定的ObjectId作为admin用户ID
                admin_object_id = ObjectId("507f1f77bcf86cd799439011")
                logger.info(f"🔄 转换admin用户ID: {user_id} -> {admin_object_id}")
                return PyObjectId(admin_object_id)
            else:
                # 尝试将字符串转换为ObjectId
                object_id = ObjectId(user_id)
                logger.info(f"🔄 转换用户ID: {user_id} -> {object_id}")
                return PyObjectId(object_id)
        except Exception as e:
            logger.error(f"❌ 用户ID转换失败: {user_id} -> {e}")
            # 如果转换失败，生成一个新的ObjectId
            new_object_id = ObjectId()
            logger.warning(f"⚠️ 生成新的用户ID: {new_object_id}")
            return PyObjectId(new_object_id)
    
    def _get_trading_graph(self, config: Dict[str, Any]) -> TradingAgentsGraph:
        """获取或创建TradingAgents图实例（带缓存）- 与单股分析保持一致"""
        config_key = json.dumps(config, sort_keys=True)

        if config_key not in self._trading_graph_cache:
            # 直接使用完整配置，不再合并DEFAULT_CONFIG（因为create_analysis_config已经处理了）
            # 这与单股分析服务和web目录的方式一致
            self._trading_graph_cache[config_key] = TradingAgentsGraph(
                selected_analysts=config.get("selected_analysts", ["market", "fundamentals"]),
                debug=config.get("debug", False),
                config=config
            )

            logger.info(f"创建新的TradingAgents实例: {config.get('llm_provider', 'default')}")

        return self._trading_graph_cache[config_key]

    # ====================== 🔥 全新：纯异步无阻塞分析函数 ======================
    async def _execute_analysis_async_with_progress(self, task: AnalysisTask, progress_tracker: RedisProgressTracker) -> AnalysisResult:
        """纯异步、无阻塞、高性能版本（兼容现有同步 propagate）"""
        try:
            from tradingagents.utils.logging_init import init_logging, get_logger
            init_logging()
            thread_logger = get_logger('analysis_async')

            thread_logger.info(f"🔄 [异步] 开始执行分析任务: {task.task_id} - {task.symbol}")
            logger.info(f"🔄 [异步] 开始执行分析任务: {task.task_id} - {task.symbol}")

            # 环境检查
            progress_tracker.update_progress("🔧 检查环境配置")

            # 使用标准配置函数创建完整配置
            from app.core.unified_config import unified_config

            quick_model = getattr(task.parameters, 'quick_analysis_model', None) or unified_config.get_quick_analysis_model()
            deep_model = getattr(task.parameters, 'deep_analysis_model', None) or unified_config.get_deep_analysis_model()

            # 🔧 从 MongoDB 数据库读取模型的完整配置参数
            quick_model_config = None
            deep_model_config = None

            try:
                from pymongo import MongoClient
                from app.core.config import settings

                client = MongoClient(settings.MONGO_URI)
                db = client[settings.MONGO_DB]
                collection = db.system_configs

                doc = collection.find_one({"is_active": True}, sort=[("version", -1)])

                if doc and "llm_configs" in doc:
                    llm_configs = doc["llm_configs"]
                    logger.info(f"✅ 从 MongoDB 读取到 {len(llm_configs)} 个模型配置")

                    for llm_config in llm_configs:
                        if llm_config.get("model_name") == quick_model:
                            quick_model_config = {
                                "max_tokens": llm_config.get("max_tokens", 4000),
                                "temperature": llm_config.get("temperature", 0.7),
                                "timeout": llm_config.get("timeout", 180),
                                "retry_times": llm_config.get("retry_times", 3),
                                "api_base": llm_config.get("api_base")
                            }

                        if llm_config.get("model_name") == deep_model:
                            deep_model_config = {
                                "max_tokens": llm_config.get("max_tokens", 4000),
                                "temperature": llm_config.get("temperature", 0.7),
                                "timeout": llm_config.get("timeout", 180),
                                "retry_times": llm_config.get("retry_times", 3),
                                "api_base": llm_config.get("api_base")
                            }
            except Exception as e:
                logger.warning(f"⚠️ 从 MongoDB 读取模型配置失败: {e}")

            # 成本估算
            progress_tracker.update_progress("💰 预估分析成本")
            llm_provider = "dashscope"

            # 参数配置
            progress_tracker.update_progress("⚙️ 配置分析参数")
            from app.services.simple_analysis_service import create_analysis_config
            config = create_analysis_config(
                research_depth=task.parameters.research_depth,
                selected_analysts=task.parameters.selected_analysts or ["market", "fundamentals"],
                quick_model=quick_model,
                deep_model=deep_model,
                llm_provider=llm_provider,
                market_type=getattr(task.parameters, 'market_type', "A股"),
                quick_model_config=quick_model_config,
                deep_model_config=deep_model_config
            )

            # 启动引擎
            progress_tracker.update_progress("🚀 初始化AI分析引擎")
            trading_graph = self._get_trading_graph(config)

            from datetime import timezone
            start_time = datetime.now(timezone.utc)
            analysis_date = task.parameters.analysis_date or datetime.now().strftime("%Y-%m-%d")

            def progress_callback(message: str):
                progress_tracker.update_progress(message)

            # ====================== ✅ 核心：异步包装同步函数 ======================
            from functools import partial
            func = partial(trading_graph.propagate, task.symbol, analysis_date, progress_callback)
            _, decision = await asyncio.to_thread(func)
            # ====================================================================

            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            progress_tracker.update_progress("📊 生成分析报告")

            model_info = decision.get('model_info', 'Unknown') if isinstance(decision, dict) else 'Unknown'

            # 从 create_simplified_report_node 输出的简化报告中提取摘要和推荐内容
            simplified_report = decision.get("simplified_report", {}) or {}
            summary_text = simplified_report.get("executive_summary", "") or decision.get("summary", "")
            recommendation_text = simplified_report.get("insight_and_decision", "") or decision.get("recommendation", "")

            result = AnalysisResult(
                analysis_id=str(uuid.uuid4()),
                summary=summary_text,
                recommendation=recommendation_text,
                confidence_score=decision.get("confidence_score", 0.0),
                risk_level=decision.get("risk_level", "中等"),
                key_points=decision.get("key_points", []),
                detailed_analysis=decision,
                execution_time=execution_time,
                tokens_used=decision.get("tokens_used", 0),
                model_info=model_info
            )

            logger.info(f"✅ [异步] 分析任务完成: {task.task_id} - 耗时{execution_time:.2f}秒")
            return result

        except Exception as e:
            logger.error(f"❌ [异步] 执行分析任务失败: {task.task_id} - {e}")
            raise

    # ====================== 🔥 全新：纯异步任务执行 ======================
    async def _execute_single_analysis_async(self, task: AnalysisTask):
        """异步执行单股分析任务（纯异步无阻塞）"""
        progress_tracker = None
        try:
            logger.info(f"🔄 开始执行分析任务: {task.task_id} - {task.symbol}")

            # 创建进度跟踪器
            progress_tracker = RedisProgressTracker(
                task_id=task.task_id,
                analysts=task.parameters.selected_analysts or ["market", "fundamentals"],
                research_depth=task.parameters.research_depth or "标准",
                llm_provider="dashscope"
            )

            self._progress_trackers[task.task_id] = progress_tracker
            progress_tracker.update_progress("🚀 开始股票分析")
            await self._update_task_status_with_tracker(task.task_id, AnalysisStatus.PROCESSING, progress_tracker)

            # ====================== ✅ 纯异步调用（无阻塞！） ======================
            result = await self._execute_analysis_async_with_progress(task, progress_tracker)
            # ====================================================================

            # 标记完成
            progress_tracker.mark_completed("✅ 分析完成")
            await self._update_task_status_with_tracker(task.task_id, AnalysisStatus.COMPLETED, progress_tracker, result)

            # 记录 token 使用
            try:
                quick_model = getattr(task.parameters, 'quick_analysis_model', None)
                deep_model = getattr(task.parameters, 'deep_analysis_model', None)
                model_name = deep_model or quick_model or "qwen-plus"
                from app.services.simple_analysis_service import get_provider_by_model_name
                provider = get_provider_by_model_name(model_name)
                await self._record_token_usage(task, result, provider, model_name)
            except Exception as e:
                logger.error(f"⚠️  记录 token 使用失败: {e}")

            logger.info(f"✅ 分析任务完成: {task.task_id}")

        except Exception as e:
            logger.error(f"❌ 分析任务失败: {task.task_id} - {e}")
            if progress_tracker:
                progress_tracker.mark_failed(str(e))
                await self._update_task_status_with_tracker(task.task_id, AnalysisStatus.FAILED, progress_tracker)
            else:
                await self._update_task_status(task.task_id, AnalysisStatus.FAILED, 0, str(e))
        finally:
            if task.task_id in self._progress_trackers:
                del self._progress_trackers[task.task_id]

    async def submit_single_analysis(
        self,
        user_id: str,
        request: SingleAnalysisRequest
    ) -> Dict[str, Any]:
        """提交单股分析任务"""
        try:
            stock_symbol = request.get_symbol()
            task_id = str(uuid.uuid4())
            converted_user_id = self._convert_user_id(user_id)

            effective_settings = await config_provider.get_effective_system_settings()

            params = request.parameters or AnalysisParameters()
            if not getattr(params, 'quick_analysis_model', None):
                params.quick_analysis_model = effective_settings.get("quick_analysis_model", "qwen-turbo")
            if not getattr(params, 'deep_analysis_model', None):
                params.deep_analysis_model = effective_settings.get("deep_analysis_model", "qwen-max")

            try:
                self.queue_service.user_concurrent_limit = int(effective_settings.get("max_concurrent_tasks", DEFAULT_USER_CONCURRENT_LIMIT))
                self.queue_service.global_concurrent_limit = int(effective_settings.get("max_concurrent_tasks", GLOBAL_CONCURRENT_LIMIT))
                self.queue_service.visibility_timeout = int(effective_settings.get("default_analysis_timeout", VISIBILITY_TIMEOUT_SECONDS))
            except Exception:
                pass

            task = AnalysisTask(
                task_id=task_id,
                user_id=converted_user_id,
                symbol=stock_symbol,
                stock_code=stock_symbol,
                parameters=params,
                status=AnalysisStatus.PENDING
            )

            db = get_mongo_db()
            await db.analysis_tasks.insert_one(task.model_dump(by_alias=True))

            # 后台异步任务
            background_task = asyncio.create_task(
                self._execute_single_analysis_async(task)
            )

            return {
                "task_id": task_id,
                "symbol": stock_symbol,
                "stock_code": stock_symbol,
                "status": AnalysisStatus.PENDING,
                "message": "任务已在后台启动（纯异步无阻塞）"
            }
            
        except Exception as e:
            logger.error(f"提交单股分析任务失败: {e}")
            raise
    
    async def submit_batch_analysis(
        self, 
        user_id: str, 
        request: BatchAnalysisRequest
    ) -> Dict[str, Any]:
        """提交批量分析任务"""
        try:
            batch_id = str(uuid.uuid4())
            converted_user_id = self._convert_user_id(user_id)

            try:
                effective_settings = await config_provider.get_effective_system_settings()
            except Exception:
                effective_settings = {}

            params = request.parameters or AnalysisParameters()
            if not getattr(params, 'quick_analysis_model', None):
                params.quick_analysis_model = effective_settings.get("quick_analysis_model", "qwen-turbo")
            if not getattr(params, 'deep_analysis_model', None):
                params.deep_analysis_model = effective_settings.get("deep_analysis_model", "qwen-max")

            try:
                self.queue_service.user_concurrent_limit = int(effective_settings.get("max_concurrent_tasks", DEFAULT_USER_CONCURRENT_LIMIT))
                self.queue_service.global_concurrent_limit = int(effective_settings.get("max_concurrent_tasks", GLOBAL_CONCURRENT_LIMIT))
                self.queue_service.visibility_timeout = int(effective_settings.get("default_analysis_timeout", VISIBILITY_TIMEOUT_SECONDS))
            except Exception:
                pass

            stock_symbols = request.get_symbols()

            batch = AnalysisBatch(
                batch_id=batch_id,
                user_id=converted_user_id,
                title=request.title,
                description=request.description,
                total_tasks=len(stock_symbols),
                parameters=params,
                status=BatchStatus.PENDING
            )

            tasks = []
            for symbol in stock_symbols:
                task_id = str(uuid.uuid4())
                task = AnalysisTask(
                    task_id=task_id,
                    batch_id=batch_id,
                    user_id=converted_user_id,
                    symbol=symbol,
                    stock_code=symbol,
                    parameters=batch.parameters,
                    status=AnalysisStatus.PENDING
                )
                tasks.append(task)
            
            db = get_mongo_db()
            await db.analysis_batches.insert_one(batch.dict(by_alias=True))
            await db.analysis_tasks.insert_many([task.dict(by_alias=True) for task in tasks])
            
            for task in tasks:
                queue_params = task.parameters.dict() if task.parameters else {}
                queue_params.update({
                    "task_id": task.task_id,
                    "symbol": task.symbol,
                    "stock_code": task.symbol,
                    "user_id": str(task.user_id),
                    "batch_id": task.batch_id,
                    "created_at": task.created_at.isoformat() if task.created_at else None
                })

                await self.queue_service.enqueue_task(
                    user_id=str(converted_user_id),
                    symbol=task.symbol,
                    params=queue_params,
                    batch_id=task.batch_id
                )
            
            logger.info(f"批量分析任务已提交: {batch_id} - {len(tasks)}个股票")
            
            return {
                "batch_id": batch_id,
                "total_tasks": len(tasks),
                "status": BatchStatus.PENDING,
                "message": f"已提交{len(tasks)}个分析任务到队列（异步模式）"
            }
            
        except Exception as e:
            logger.error(f"提交批量分析任务失败: {e}")
            raise
    
    async def execute_analysis_task(
        self, 
        task: AnalysisTask,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> AnalysisResult:
        """执行单个分析任务"""
        try:
            logger.info(f"开始执行分析任务: {task.task_id} - {task.symbol}")
            await self._update_task_status(task.task_id, AnalysisStatus.PROCESSING, 0)
            
            if progress_callback:
                progress_callback(10, "初始化分析引擎...")
            
            from app.core.unified_config import unified_config
            quick_model = getattr(task.parameters, 'quick_analysis_model', None) or unified_config.get_quick_analysis_model()
            deep_model = getattr(task.parameters, 'deep_analysis_model', None) or unified_config.get_deep_analysis_model()

            quick_model_config = None
            deep_model_config = None
            llm_configs = unified_config.get_llm_configs()

            for llm_config in llm_configs:
                if llm_config.model_name == quick_model:
                    quick_model_config = {
                        "max_tokens": llm_config.max_tokens,
                        "temperature": llm_config.temperature,
                        "timeout": llm_config.timeout,
                        "retry_times": llm_config.retry_times,
                        "api_base": llm_config.api_base
                    }

                if llm_config.model_name == deep_model:
                    deep_model_config = {
                        "max_tokens": llm_config.max_tokens,
                        "temperature": llm_config.temperature,
                        "timeout": llm_config.timeout,
                        "retry_times": llm_config.retry_times,
                        "api_base": llm_config.api_base
                    }

            llm_provider = await get_provider_by_model_name(quick_model)

            config = create_analysis_config(
                research_depth=task.parameters.research_depth,
                selected_analysts=task.parameters.selected_analysts or ["market", "fundamentals"],
                quick_model=quick_model,
                deep_model=deep_model,
                llm_provider=llm_provider,
                market_type=getattr(task.parameters, 'market_type', "A股"),
                quick_model_config=quick_model_config,
                deep_model_config=deep_model_config
            )
            
            if progress_callback:
                progress_callback(30, "创建分析图...")
            
            trading_graph = self._get_trading_graph(config)
            
            if progress_callback:
                progress_callback(50, "执行股票分析...")
            
            start_time = datetime.utcnow()
            analysis_date = task.parameters.analysis_date or datetime.now().strftime("%Y-%m-%d")
            
            # 异步执行
            from functools import partial
            func = partial(trading_graph.propagate, task.symbol, analysis_date)
            _, decision = await asyncio.to_thread(func)
            
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            
            if progress_callback:
                progress_callback(80, "处理分析结果...")

            model_info = decision.get('model_info', 'Unknown') if isinstance(decision, dict) else 'Unknown'

            # 从 create_simplified_report_node 输出的简化报告中提取摘要和推荐内容
            simplified_report = decision.get("simplified_report", {}) or {}
            summary_text = simplified_report.get("executive_summary", "") or decision.get("summary", "")
            recommendation_text = simplified_report.get("insight_and_decision", "") or decision.get("recommendation", "")

            result = AnalysisResult(
                analysis_id=str(uuid.uuid4()),
                summary=summary_text,
                recommendation=recommendation_text,
                confidence_score=decision.get("confidence_score", 0.0),
                risk_level=decision.get("risk_level", "中等"),
                key_points=decision.get("key_points", []),
                detailed_analysis=decision,
                execution_time=execution_time,
                tokens_used=decision.get("tokens_used", 0),
                model_info=model_info
            )

            if progress_callback:
                progress_callback(100, "分析完成")

            await self._update_task_status(task.task_id, AnalysisStatus.COMPLETED, 100, result)

            try:
                await self._record_token_usage(task, result, llm_provider, deep_model or quick_model)
            except Exception as e:
                logger.error(f"⚠️  记录 token 使用失败: {e}")

            logger.info(f"分析任务完成: {task.task_id} - 耗时{execution_time:.2f}秒")
            return result
            
        except Exception as e:
            logger.error(f"执行分析任务失败: {task.task_id} - {e}")
            error_result = AnalysisResult(error_message=str(e))
            await self._update_task_status(task.task_id, AnalysisStatus.FAILED, 0, error_result)
            raise
    
    async def _update_task_status(
        self,
        task_id: str,
        status: AnalysisStatus,
        progress: int,
        result: Optional[AnalysisResult] = None,
    ) -> None:
        """更新任务状态（委托至拆分的工具函数）"""
        try:
            from app.services.analysis.status_update_utils import perform_update_task_status
            await perform_update_task_status(task_id, status, progress, result)
        except Exception as e:
            logger.error(f"更新任务状态失败: {task_id} - {e}")

    async def _update_task_status_with_tracker(
        self,
        task_id: str,
        status: AnalysisStatus,
        progress_tracker: RedisProgressTracker,
        result: Optional[AnalysisResult] = None,
    ) -> None:
        """使用进度跟踪器更新任务状态（委托至拆分的工具函数）"""
        try:
            from app.services.analysis.status_update_utils import perform_update_task_status_with_tracker
            await perform_update_task_status_with_tracker(task_id, status, progress_tracker, result)
        except Exception as e:
            logger.error(f"更新任务状态失败: {task_id} - {e}")

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        try:
            if task_id in self._progress_trackers:
                progress_tracker = self._progress_trackers[task_id]
                progress_data = progress_tracker.to_dict()

                db = get_mongo_db()
                task = await db.analysis_tasks.find_one({"task_id": task_id})

                if task:
                    return {
                        "task_id": task_id,
                        "user_id": task.get("user_id"),
                        "symbol": task.get("stock_symbol") or task.get("symbol"),
                        "stock_code": task.get("stock_symbol") or task.get("symbol"),
                        "status": progress_data["status"],
                        "progress": progress_data["progress"],
                        "current_step": progress_data["current_step"],
                        "message": progress_data["message"],
                        "elapsed_time": progress_data["elapsed_time"],
                        "remaining_time": progress_data["remaining_time"],
                        "estimated_total_time": progress_data.get("estimated_total_time", 0),
                        "steps": progress_data["steps"],
                        "start_time": progress_data["start_time"],
                        "end_time": None,
                        "last_update": progress_data["last_update"],
                        "parameters": task.get("parameters", {}),
                        "execution_time": None,
                        "tokens_used": None,
                        "result_data": task.get("result"),
                        "error_message": None
                    }

            redis_service = get_redis_service()
            progress_key = RedisKeys.TASK_PROGRESS.format(task_id=task_id)
            cached_status = await redis_service.get_json(progress_key)

            if cached_status:
                return cached_status

            db = get_mongo_db()
            task = await db.analysis_tasks.find_one({"task_id": task_id})

            if task:
                elapsed_time = 0
                remaining_time = 0
                estimated_total_time = 0

                if task.get("started_at"):
                    from datetime import datetime
                    start_time = task.get("started_at")
                    if task.get("completed_at"):
                        elapsed_time = (task.get("completed_at") - start_time).total_seconds()
                        estimated_total_time = elapsed_time
                        remaining_time = 0
                    else:
                        elapsed_time = (datetime.utcnow() - start_time).total_seconds()
                        estimated_total_time = task.get("estimated_duration", 300)
                        remaining_time = max(0, estimated_total_time - elapsed_time)

                return {
                    "task_id": task_id,
                    "status": task.get("status"),
                    "progress": task.get("progress", 0),
                    "current_step": task.get("current_step", ""),
                    "message": task.get("message", ""),
                    "elapsed_time": elapsed_time,
                    "remaining_time": remaining_time,
                    "estimated_total_time": estimated_total_time,
                    "start_time": task.get("started_at").isoformat() if task.get("started_at") else None,
                    "updated_at": task.get("updated_at", "").isoformat() if task.get("updated_at") else None,
                    "result_data": task.get("result")
                }

            return None

        except Exception as e:
            logger.error(f"获取任务状态失败: {task_id} - {e}")
            return None
    
    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        try:
            await self._update_task_status(task_id, AnalysisStatus.CANCELLED, 0)
            await self.queue_service.remove_task(task_id)
            logger.info(f"任务已取消: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"取消任务失败: {task_id} - {e}")
            return False

    async def _record_token_usage(
        self,
        task: AnalysisTask,
        result: AnalysisResult,
        provider: str,
        model_name: str
    ):
        """记录 token 使用情况"""
        try:
            input_tokens = result.tokens_used // 2 if result.tokens_used > 0 else 0
            output_tokens = result.tokens_used - input_tokens if result.tokens_used > 0 else 0

            if result.tokens_used == 0:
                input_tokens = 2000
                output_tokens = 1000

            from app.services.config_service import config_service
            config = await config_service.get_system_config()

            llm_config = None
            if config and config.llm_configs:
                for cfg in config.llm_configs:
                    if cfg.provider == provider and cfg.model_name == model_name:
                        llm_config = cfg
                        break

            cost = 0.0
            currency = "CNY"
            if llm_config:
                input_price = llm_config.input_price_per_1k or 0.0
                output_price = llm_config.output_price_per_1k or 0.0
                cost = (input_tokens / 1000 * input_price) + (output_tokens / 1000 * output_price)
                currency = llm_config.currency or "CNY"

            usage_record = UsageRecord(
                timestamp=datetime.now().isoformat(),
                provider=provider,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                currency=currency,
                session_id=task.task_id,
                analysis_type="stock_analysis",
                stock_code=task.symbol
            )

            success = await self.usage_service.add_usage_record(usage_record)

            if success:
                logger.info(f"💰 记录使用成本: {provider}/{model_name} - ¥{cost:.4f}")
            else:
                logger.warning(f"⚠️  记录使用成本失败")

        except Exception as e:
            logger.error(f"❌ 记录 token 使用失败: {e}")


# 全局分析服务实例（延迟初始化）
analysis_service: Optional[AnalysisService] = None


def get_analysis_service() -> AnalysisService:
    """获取分析服务实例（延迟初始化）"""
    global analysis_service
    if analysis_service is None:
        analysis_service = AnalysisService()
    return analysis_service