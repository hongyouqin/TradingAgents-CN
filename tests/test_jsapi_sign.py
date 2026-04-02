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