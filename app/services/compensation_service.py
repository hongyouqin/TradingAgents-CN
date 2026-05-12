# app/services/compensation_service.py
import asyncio
import logging
from app.services.power_account_service import power_account_service

class CompensationService:
    """补偿算力账户消费冻结服务：处理各种异常情况"""
    
    def __init__(self, logger=None):
        self.is_running = False
        self.compensation_task = None
        self.logger = logger
        
    def set_logger(self, logger):
        self.logger = logger
    
    def start_compensation_loop(self):
        """启动补偿任务循环"""
        if self.is_running:
            self.logger.warning("补偿任务已在运行中")
            return
        
        self.logger.info("✅ 补偿任务已启动")
        self.is_running = True
        self.compensation_task = asyncio.create_task(self._compensation_loop())
    
    async def stop_compensation_loop(self):
        """停止补偿任务循环"""
        if self.compensation_task:
            self.is_running = False
            self.compensation_task.cancel()
            try:
                await self.compensation_task
            except asyncio.CancelledError:
                pass
            self.logger.info("⏹️ 补偿任务已停止")
    
    async def _compensation_loop(self):
        """补偿任务主循环"""
        while self.is_running:
            try:
                # 处理过期的冻结记录
                await self._handle_expired_frozen_transactions()
                
                # 处理其他可能的补偿场景
                # await self._handle_other_compensations()
                
                # 等待下一次执行
                await asyncio.sleep(60 * 5)  # 5分钟执行一次
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ 补偿任务执行失败: {e}", exc_info=True)
                await asyncio.sleep(60)  # 出错后等待1分钟再重试
    
    async def _handle_expired_frozen_transactions(self):
        """处理过期的冻结交易"""
        try:
            # 获取过期的冻结记录
            expired_transactions = await power_account_service.get_expired_frozen_transactions(
                minutes=60  # 30分钟未确认的冻结记录
            )
            
            if not expired_transactions:
                return
            
            self.logger.info(f"🔧 开始处理 {len(expired_transactions)} 条过期冻结记录")
            
            success_count = 0
            fail_count = 0
            
            for tx in expired_transactions:
                try:
                    success, msg = await power_account_service.compensate_expired_freeze(tx)
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                        self.logger.warning(f"处理失败: {tx.get('order_no')}, {msg}")
                        
                except Exception as e:
                    fail_count += 1
                    self.logger.error(f"处理异常: {tx.get('order_no')}, {e}")
            
            self.logger.info(
                f"📊 过期冻结记录处理完成: 成功={success_count}, 失败={fail_count}"
            )
            
        except Exception as e:
            self.logger.error(f"❌ 处理过期冻结记录失败: {e}", exc_info=True)
    
    # 可以添加其他补偿处理方法
    async def _handle_orphan_tasks(self):
        """处理孤儿任务（任务已创建但从未执行）"""
        pass

# 全局补偿服务实例
compensation_service = CompensationService()