from fastapi import Request

def get_real_client_ip(request: Request) -> str:
    """
    获取真实客户端 IP
    
    优先级：
    1. X-Forwarded-For (代理/CDN)
    2. X-Real-IP (Nginx)
    3. request.client.host (直连)
    """
    # 1. 尝试从 X-Forwarded-For 获取
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For 格式: client_ip, proxy1_ip, proxy2_ip
        # 取第一个 IP（最原始的真实 IP）
        real_ip = forwarded.split(",")[0].strip()
        if real_ip and real_ip not in ["127.0.0.1", "localhost"]:
            return real_ip
    
    # 2. 尝试从 X-Real-IP 获取（Nginx 常用）
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # 3. 回退到直连 IP
    if request.client:
        return request.client.host
    
    return "unknown"

