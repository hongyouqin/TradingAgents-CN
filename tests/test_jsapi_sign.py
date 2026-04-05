# test_jsapi_sign.py
import base64
import sys
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from cryptography import x509
from cryptography.hazmat.primitives import serialization

# https://pay.weixin.qq.com/doc/v3/partner/4012365867

def test_sign():
    # 测试数据（微信官方文档中的示例）
    app_id = "wx2421b1c4370ec43b"
    timestamp = "1554208460"
    nonce_str = "593BEC0C930BF1AFEB40B4A08C8FB242"
    prepay_id = "prepay_id=wx201410272009395522657a690389285100"

    # 构造签名串（四行，每行以\n结尾）
    signature_str = f"{app_id}\n{timestamp}\n{nonce_str}\n{prepay_id}\n"

    print("=== 签名串 ===")
    print(repr(signature_str))
    print("\n实际内容:")
    print(signature_str)

    # 加载你的商户私钥
    # private_key_path = "./apiclient_test_key.pem"  # 替换为你的私钥路径
    private_key_path = "./apiclient_key.pem"  # 替换为你的私钥路径
    try:
        with open(private_key_path, 'r') as f:
            private_key_data = f.read()
        
        private_key = serialization.load_pem_private_key(
            private_key_data.encode('utf-8'),
            password=None
        )
        
        # 计算签名
        signature = private_key.sign(
            signature_str.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        
        pay_sign = base64.b64encode(signature).decode('utf-8')
        print(f"\n=== 生成的签名 ===")
        print(pay_sign)
        
    except Exception as e:
        print(f"签名失败: {e}")
        
def payment_verify_singature():
    '''
        支付验证签名
    '''    
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    # ======================
    # 你 最 新 的 真 实 数 据 ✅
    # ======================
    app_id = "wx183521434338da29"
    timestamp = "1775148110"
    nonce_str = "8WoH9XB6vphDcItIgVkX5sjL5Xip0QX1"
    prepay_id = "wx0300415143942227deceb7510f5f600000"

    # 你最新返回的签名
    my_pay_sign = "Gb6z1t0hY9VvmU4obiLeXK2REDZCD/UCgmekkhq+2pOA0YnBL1dvkbxgTIPXIqBVfKS7Vh2dnWR7H2IxIoijsM0p5rLMgajfFSvpYaGmR0uAkYQNRGdSvIn11egKS/ZxGeoV1gFgqOrnyP6lSXJoUukUhx+lXw6M7lM7VLhBa+atZ/lEoJSiGmnop241ECiyfN7T/2mdpCtN56suYYiwYsUtrkaL4HikEp1oj7juze6w02Iuv7HXzcwc595nif7Reih8vicSYcKp0hSCdss6RALgpP2POB3zxCM/hZP3Lk/ATXFBNtmVGXZG+1R5k5OlW8FYITMAZWhnPvMOj0A//Q=="

    # 你的私钥路径
    private_key_path = "./certs/apiclient_key.pem"

    # ======================
    # 开始验签
    # ======================
    try:
        # 1. 构造微信要求的签名串（必须严格这个格式！）
        signature_str = f"{app_id}\n{timestamp}\n{nonce_str}\nprepay_id={prepay_id}\n"

        print("🔒 签名原文：")
        print(repr(signature_str))
        print("-" * 60)

        # 2. 加载私钥
        with open(private_key_path, 'r', encoding='utf-8') as f:
            private_key_data = f.read()

        private_key = serialization.load_pem_private_key(
            private_key_data.encode('utf-8'),
            password=None
        )

        # 3. 重新计算签名
        signature = private_key.sign(
            signature_str.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        generated_sign = base64.b64encode(signature).decode('utf-8')

        print("✅ 重新计算的签名：")
        print(generated_sign)
        print("-" * 60)
        print("✅ 你接口返回的签名：")
        print(my_pay_sign)
        print("-" * 60)

        # 4. 比对
        if generated_sign == my_pay_sign:
            print("🎉 恭喜！签名 **完全正确**！前端可以正常调起！")
        else:
            print("❌ 签名错误！")

    except Exception as e:
        print(f"❌ 验签失败：{e}")

# 寻找证书对应的序列号
def test_match():
    CERT_FILE = "./certs/apiclient_cert.pem"
    KEY_FILE  = "./certs/apiclient_key.pem"

    try:
        # 读证书
        with open(CERT_FILE, 'rb') as f:
            cert = x509.load_pem_x509_certificate(f.read())
        
        # 读私钥
        with open(KEY_FILE, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        
        # ======================
        # 修复版：比对模数 n
        # ======================
        cert_n = cert.public_key().public_numbers().n
        key_n = key.public_key().public_numbers().n

        if cert_n == key_n:
            print("✅ 私钥 与 证书 匹配！")
            serial_hex = hex(cert.serial_number)[2:].upper()
            print(f"✅ 此证书的真实序列号 = {serial_hex}")
        else:
            print("❌ 严重错误：私钥 和 证书 不匹配！")
            sys.exit(1)

    except Exception as e: 
        print(f"❌ 错误：{str(e)}")

if __name__ == "__main__":
    # test_sign()
    test_match()