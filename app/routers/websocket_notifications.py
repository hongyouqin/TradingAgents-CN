"""
WebSocket 通知系统
替代 SSE + Redis PubSub，解决连接泄漏问题
"""
import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from datetime import datetime

from app.services.auth_service import AuthService

router = APIRouter()
logger = logging.getLogger("webapi.websocket")

# 🔥 全局 WebSocket 连接管理器
class ConnectionManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        # user_id -> Set[WebSocket]
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, user_id: str):
        """连接 WebSocket"""
        await websocket.accept()
        
        async with self._lock:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = set()
            self.active_connections[user_id].add(websocket)
            
            total_connections = sum(len(conns) for conns in self.active_connections.values())
            logger.info(f"✅ [WS] 新连接: user={user_id}, "
                       f"该用户连接数={len(self.active_connections[user_id])}, "
                       f"总连接数={total_connections}")
    
    async def disconnect(self, websocket: WebSocket, user_id: str):
        """断开 WebSocket"""
        async with self._lock:
            if user_id in self.active_connections:
                self.active_connections[user_id].discard(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
            
            total_connections = sum(len(conns) for conns in self.active_connections.values())
            logger.info(f"🔌 [WS] 断开连接: user={user_id}, 总连接数={total_connections}")
    
    async def send_personal_message(self, message: dict, user_id: str):
        """发送消息给指定用户的所有连接"""
        async with self._lock:
            if user_id not in self.active_connections:
                logger.debug(f"⚠️ [WS] 用户 {user_id} 没有活跃连接")
                return
            
            connections = list(self.active_connections[user_id])
        
        # 在锁外发送消息，避免阻塞
        message_json = json.dumps(message, ensure_ascii=False)
        dead_connections = []
        
        for connection in connections:
            try:
                await connection.send_text(message_json)
                logger.debug(f"📤 [WS] 发送消息给 user={user_id}")
            except Exception as e:
                logger.warning(f"❌ [WS] 发送消息失败: {e}")
                dead_connections.append(connection)
        
        # 清理死连接
        if dead_connections:
            async with self._lock:
                if user_id in self.active_connections:
                    for conn in dead_connections:
                        self.active_connections[user_id].discard(conn)
                    if not self.active_connections[user_id]:
                        del self.active_connections[user_id]
    
    async def broadcast(self, message: dict):
        """广播消息给所有连接"""
        async with self._lock:
            all_connections = []
            for connections in self.active_connections.values():
                all_connections.extend(connections)
        
        message_json = json.dumps(message, ensure_ascii=False)
        
        for connection in all_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.warning(f"❌ [WS] 广播消息失败: {e}")
    
    def get_stats(self) -> dict:
        """获取连接统计"""
        return {
            "total_users": len(self.active_connections),
            "total_connections": sum(len(conns) for conns in self.active_connections.values()),
            "users": {user_id: len(conns) for user_id, conns in self.active_connections.items()}
        }


# 全局连接管理器实例
manager = ConnectionManager()


# @router.websocket("/ws/notifications")
async def websocket_notifications_endpoint(
    websocket: WebSocket,
    token: str = Query(...)
):
    """
    WebSocket 通知端点
    
    客户端连接: ws://localhost:8000/api/ws/notifications?token=<jwt_token>
    """
    connection_id = None
    try:
        # 验证 token
        token_data = AuthService.verify_token(token)
        if not token_data:
            logger.warning(f"❌ [WS] 认证失败: token无效")
            await websocket.close(code=1008, reason="Unauthorized")
            return
    
        user_id = token_data.sub if token_data.sub else "admin"
        connection_id = f"{user_id}_{id(websocket)}"
        
        # 接受 WebSocket 连接
        await websocket.accept()
        logger.info(f"🔌 [WS] 新连接: {connection_id}")
        
        # 连接 WebSocket
        await manager.connect(websocket, user_id)
        
        # 发送连接确认
        try:
            await websocket.send_json({
                "type": "connected",
                "data": {
                    "user_id": user_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "message": "WebSocket 连接成功"
                }
            })
            logger.info(f"✅ [WS] 连接确认已发送: {connection_id}")
        except Exception as e:
            logger.error(f"❌ [WS] 发送连接确认失败: {connection_id}, 错误: {e}")
            raise
        
        # 心跳任务
        async def send_heartbeat():
            try:
                while True:
                    await asyncio.sleep(30)
                    try:
                        await websocket.send_json({
                            "type": "heartbeat",
                            "data": {
                                "timestamp": datetime.utcnow().isoformat()
                            }
                        })
                        logger.debug(f"💓 [WS] 心跳发送成功: {connection_id}")
                    except Exception as e:
                        logger.debug(f"💓 [WS] 心跳发送失败: {connection_id}, 错误: {e}")
                        break
            except asyncio.CancelledError:
                logger.debug(f"🛑 [WS] 心跳任务被取消: {connection_id}")
                raise
        
        # 启动心跳任务
        heartbeat_task = asyncio.create_task(send_heartbeat())
        
        # 接收客户端消息（主要用于保持连接）
        while True:
            try:
                # 使用 wait_for 添加超时，避免永久阻塞
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                logger.debug(f"📥 [WS] 收到客户端消息: {connection_id}, data={data[:100] if data else ''}")
                
                # 处理心跳响应
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
                    
            except asyncio.TimeoutError:
                # 超时是正常的，继续循环
                logger.debug(f"⏱️ [WS] 接收超时: {connection_id}")
                continue
                
            except WebSocketDisconnect as e:
                logger.info(f"🔌 [WS] 客户端主动断开: {connection_id}, code={e.code}")
                break
                
            except asyncio.CancelledError:
                logger.info(f"🛑 [WS] 连接任务被取消: {connection_id}")
                raise
                
            except Exception as e:
                logger.error(f"❌ [WS] 接收消息错误: {connection_id}, 错误: {type(e).__name__}: {e}")
                break
    
    except asyncio.CancelledError:
        logger.info(f"🛑 [WS] WebSocket 端点被取消: {connection_id}")
        # 重新抛出，让上层处理
        raise
        
    except Exception as e:
        logger.error(f"❌ [WS] WebSocket 端点异常: {connection_id}, 错误: {type(e).__name__}: {e}", exc_info=True)
        
    finally:
        # 确保资源被清理
        logger.info(f"🧹 [WS] 清理连接: {connection_id}")
        
        # 取消心跳任务
        if 'heartbeat_task' in locals():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"❌ [WS] 取消心跳任务失败: {e}")
        
        # 断开连接
        try:
            if 'user_id' in locals():
                await manager.disconnect(websocket, user_id)
                logger.info(f"✅ [WS] 连接已从管理器移除: {connection_id}")
        except Exception as e:
            logger.error(f"❌ [WS] 从管理器断开连接失败: {e}")
        
        # 关闭 WebSocket
        try:
            await websocket.close()
        except:
            pass
        
        logger.info(f"✅ [WS] 连接清理完成: {connection_id}")

@router.websocket("/ws/tasks/{task_id}")
async def websocket_task_progress_endpoint(
    websocket: WebSocket,
    task_id: str,
    token: str = Query(...)
):
    """
    WebSocket 任务进度端点
    
    客户端连接: ws://localhost:8000/api/ws/tasks/<task_id>?token=<jwt_token>
    
    消息格式:
    {
        "type": "progress",  // 消息类型: progress, completed, error, heartbeat
        "data": {
            "task_id": "...",
            "message": "正在分析...",
            "step": 1,
            "total_steps": 5,
            "progress": 20.0,
            "timestamp": "2025-10-23T12:00:00"
        }
    }
    """
    # 验证 token
    token_data = AuthService.verify_token(token)
    if not token_data:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    
    user_id = "admin"
    channel = f"task_progress:{task_id}"
    
    # 连接 WebSocket
    await websocket.accept()
    logger.info(f"✅ [WS-Task] 新连接: task={task_id}, user={user_id}")
    
    # 发送连接确认
    await websocket.send_json({
        "type": "connected",
        "data": {
            "task_id": task_id,
            "timestamp": datetime.utcnow().isoformat(),
            "message": "已连接任务进度流"
        }
    })
    
    try:
        # 这里可以从 Redis 或数据库获取任务进度
        # 暂时保持连接，等待任务完成
        while True:
            try:
                data = await websocket.receive_text()
                logger.debug(f"📥 [WS-Task] 收到客户端消息: task={task_id}, data={data}")
            except WebSocketDisconnect:
                logger.info(f"🔌 [WS-Task] 客户端主动断开: task={task_id}")
                break
            except Exception as e:
                logger.error(f"❌ [WS-Task] 接收消息错误: {e}")
                break
    
    finally:
        logger.info(f"🔌 [WS-Task] 断开连接: task={task_id}")


@router.get("/ws/stats")
async def get_websocket_stats():
    """获取 WebSocket 连接统计"""
    return manager.get_stats()


# 🔥 辅助函数：供其他模块调用，发送通知
async def send_notification_via_websocket(user_id: str, notification: dict):
    """
    通过 WebSocket 发送通知
    
    Args:
        user_id: 用户 ID
        notification: 通知数据
    """
    message = {
        "type": "notification",
        "data": notification
    }
    await manager.send_personal_message(message, user_id)


async def send_task_progress_via_websocket(task_id: str, progress_data: dict):
    """
    通过 WebSocket 发送任务进度
    
    Args:
        task_id: 任务 ID
        progress_data: 进度数据
    """
    # 注意：这里需要知道任务属于哪个用户
    # 可以从数据库查询或在 progress_data 中传递
    # 暂时简化处理
    message = {
        "type": "progress",
        "data": progress_data
    }
    # 广播给所有连接（生产环境应该只发给任务所属用户）
    await manager.broadcast(message)

