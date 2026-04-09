# app/services/debug_analysis_detailed.py
#!/usr/bin/env python
"""
详细调试脚本：逐行跟踪执行过程
"""

import asyncio
import logging
import sys
import os
import traceback
from datetime import datetime
import time

# 获取项目根目录
current_dir = os.path.dirname(os.path.abspath(__file__))  # app/services/
project_root = os.path.dirname(os.path.dirname(current_dir))  # TradingAgents-CN/

# 添加项目根目录到 Python 路径
if project_root not in sys.path:
    sys.path.insert(0, project_root)
    print(f"📂 添加项目根目录: {project_root}")

# 配置详细日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(project_root, 'debug_detailed.log'), encoding='utf-8')
    ]
)

logger = logging.getLogger("debug_detailed")

async def init_database():
    """初始化数据库连接 - 使用 main.py 中的 init_db 方式"""
    logger.info("=" * 60)
    logger.info("🔄 初始化数据库连接...")
    logger.info("=" * 60)
    
    try:
        # 导入配置和数据库模块
        from app.core.config import settings
        from app.core.database import init_db, get_mongo_db, get_redis_client
        
        logger.info(f"📋 MongoDB URI: {settings.MONGO_URI}")
        logger.info(f"📋 Redis URL: {settings.REDIS_URL}")
        logger.info(f"📋 MongoDB Database: {settings.MONGO_DB}")
        
        # 使用 init_db 初始化数据库（与 main.py 保持一致）
        logger.info("🔄 调用 init_db()...")
        await init_db()
        logger.info("✅ init_db() 执行完成")
        
        # 验证 MongoDB 连接
        logger.info("🔄 验证 MongoDB 连接...")
        db = get_mongo_db()
        await db.command('ping')
        logger.info("✅ MongoDB ping 成功")
        
        # 验证 Redis 连接
        logger.info("🔄 验证 Redis 连接...")
        redis_client = get_redis_client()
        await redis_client.ping()
        logger.info("✅ Redis ping 成功")
        
        logger.info("=" * 60)
        logger.info("✅ 数据库初始化完成")
        logger.info("=" * 60)
        
        return True
        
    except ImportError as e:
        logger.error(f"❌ 导入失败: {e}")
        traceback.print_exc()
        return False
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        logger.error("📋 详细错误信息:")
        traceback.print_exc()
        return False

async def close_database():
    """关闭数据库连接 - 使用 main.py 中的 close_db 方式"""
    logger.info("=" * 60)
    logger.info("🔄 关闭数据库连接...")
    logger.info("=" * 60)
    
    try:
        from app.core.database import close_db
        await close_db()
        logger.info("✅ 数据库连接已关闭")
    except Exception as e:
        logger.error(f"❌ 关闭数据库连接失败: {e}")
        traceback.print_exc()

async def test_direct():
    """直接测试分析服务"""
    logger.info("=" * 80)
    logger.info("🔍 开始测试分析服务")
    logger.info("=" * 80)
    
    # 步骤1: 初始化数据库
    if not await init_database():
        logger.error("❌ 数据库初始化失败，退出")
        return
    
    try:
        # 步骤2: 导入模型
        logger.info("\n📦 导入模型...")
        from app.models.analysis import SingleAnalysisRequest, AnalysisParameters
        from app.services.simple_analysis_service import get_simple_analysis_service
        from app.core.database import get_mongo_db
        
        logger.info("✅ 模型导入成功")
        
        # 步骤3: 创建请求
        logger.info("\n📝 创建请求对象...")
        parameters = AnalysisParameters(
            market_type="A股",
            analysis_date=datetime.now(),
            research_depth="标准",
            selected_analysts=["market", "fundamentals"],
            include_sentiment=True,
            include_risk=True,
            language="zh-CN",
            quick_analysis_model="deepseek-reasoner",
            deep_analysis_model="deepseek-reasoner"
        )
        
        request = SingleAnalysisRequest(
            symbol="603538",
            stock_code="603538",
            parameters=parameters
        )
        logger.info(f"✅ 请求创建成功: symbol={request.symbol}")
        
        # 步骤4: 获取服务
        logger.info("\n🔄 获取分析服务...")
        service = get_simple_analysis_service()
        logger.info(f"✅ 服务实例ID: {id(service)}")
        
        # 步骤5: 创建任务
        logger.info("\n📋 创建分析任务...")
        result = await service.create_analysis_task("admin", request)
        task_id = result["task_id"]
        logger.info(f"✅ 任务创建成功: {task_id}")
        logger.info(f"   - status: {result['status']}")
        logger.info(f"   - message: {result['message']}")
        
        # 步骤6: 验证任务存在
        logger.info("\n🔍 验证任务存在...")
        task_status = await service.get_task_status(task_id)
        if task_status:
            logger.info(f"✅ 任务验证成功: {task_status.get('status')}")
        else:
            logger.warning("⚠️ 任务验证失败")
        
        # 步骤7: 执行分析（等待完成）
        logger.info("\n🚀 开始执行分析...")
        logger.info("   " + "=" * 40)
        
        start_time = time.time()
        
        try:
            await service.execute_analysis_background(task_id, "admin", request)
            elapsed = time.time() - start_time
            logger.info(f"\n✅ 分析执行完成，耗时: {elapsed:.2f}秒")
        except Exception as e:
            logger.error(f"\n❌ 分析执行失败: {e}")
            logger.error("📋 详细错误信息:")
            traceback.print_exc()
        
        # 步骤8: 获取最终结果
        logger.info("\n📊 获取任务最终状态...")
        final_status = await service.get_task_status(task_id)
        
        if final_status:
            logger.info(f"📊 最终状态: {final_status.get('status')}")
            logger.info(f"   - progress: {final_status.get('progress')}%")
            logger.info(f"   - message: {final_status.get('message')}")
            
            # 如果有结果数据，显示部分内容
            result_data = final_status.get('result_data')
            if result_data:
                logger.info(f"   - 有结果数据: {len(str(result_data))} 字符")
                if 'summary' in result_data:
                    logger.info(f"   - 摘要: {result_data['summary'][:100]}...")
        else:
            logger.warning("⚠️ 无法获取任务最终状态")
        
        logger.info("\n" + "=" * 80)
        logger.info("✅ 测试完成")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        logger.error("📋 完整错误堆栈:")
        traceback.print_exc()
    finally:
        # 步骤9: 等待一下确保所有任务完成
        logger.info("\n⏳ 等待2秒确保所有任务完成...")
        await asyncio.sleep(2)
        
        # 步骤10: 关闭数据库连接
        await close_database()

def check_mongodb_connection():
    """检查 MongoDB 连接（同步方式）"""
    try:
        import pymongo
        from app.core.config import settings
        
        logger.info("🔄 尝试直接连接 MongoDB...")
        client = pymongo.MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        logger.info("✅ 直接 MongoDB 连接成功")
        client.close()
        return True
    except Exception as e:
        logger.error(f"❌ 直接 MongoDB 连接失败: {e}")
        return False

if __name__ == "__main__":
    try:
        # 首先检查 MongoDB 是否可连接
        logger.info("=" * 80)
        logger.info("🔧 调试脚本启动")
        logger.info("=" * 80)
        
        # 检查直接连接
        if not check_mongodb_connection():
            logger.error("❌ MongoDB 无法连接，请检查:")
            logger.error("  1. MongoDB 服务是否运行")
            logger.error("  2. 连接字符串是否正确")
            logger.error("  3. 网络是否通畅")
            sys.exit(1)
        
        # 运行测试
        asyncio.run(test_direct())
        
    except KeyboardInterrupt:
        logger.info("\n👋 收到中断信号")
    except Exception as e:
        logger.error(f"\n❌ 未捕获的异常: {e}")
        traceback.print_exc()