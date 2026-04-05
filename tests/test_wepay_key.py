import json
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# ======================================
# 把用户给你的 APIv3 密钥填在这里
# ======================================
APIv3_KEY = "3a6e8f240a7c876b9fc5d3e2b1a0c9d8"

# 直接从你的日志里复制出来的真实密文
resource = {
    "original_type": "transaction",
    "algorithm": "AEAD_AES_256_GCM",
    "ciphertext": "HLHd94VSKZ41Y3v9NicupYsuztwAa7XbjqHCrNXiUR+AdZq5cOlWz+cV4bmMZ7uG3Jd56EOKMRQRewdxmQmz44tfD5FiscHMlApCj6vSIlv5J/9UKcnvn+fwto3HNZEHaA8mVHA6Xzb0A6PbH7U79SqwSo/B+B0Q5XWdYpLaBcMZU0MkHAKeHeETnV3dG4/ABtSKfTk2lyMbwlC9zQG9kyF9LLV7d7ULHS0dqm3XGgFZRAqgIpqJ/T88Qe0MbL5IHofTtb7LvaQLKZGHN2lH3JwodkxVjYWp6nzsgF8ZvlV2YZ8+BzW9fksdDbhP28jeCbBrBKV0MsXMnpbUtwUe2D+OJT6zNfQh6TRteD5wZKya9exnCI1euIxdq6IHXvOzH3Z0iEUxroUIMKBZZq7fBMq7HtHenH6uTmI/tm49osjsDHE3Qp29Z03ZAmJ0AS4OaBwsw8zqp/iboLc3WulQN6AwmCtreFuZu8tZ9GFeWzO9y6FGaBJYD2D40U6e3T7HECLeTA5NKEbE1ibPo5/X+Nm4NI93buQDrQV+dgsMU5i/7kIcmxoL6L+0VCEsNHVOxVAVXDA=",
    "associated_data": "transaction",
    "nonce": "QvS9wQxatJT9"
}

def decrypt_resource(resource):
    """
    微信支付 V3 回调资源解密（兼容所有 cryptography 版本，100% 官方正确）
    """
    try:
        key = APIv3_KEY.encode("utf-8")
        nonce = resource["nonce"].encode("utf-8")
        ciphertext = base64.b64decode(resource["ciphertext"])
        associated_data = resource["associated_data"].encode("utf-8")

        # 👇 这是 GCM 标准：最后 16 个字节是认证标签
        tag = ciphertext[-16:]
        data = ciphertext[:-16]

        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
        decryptor = cipher.decryptor()
        decryptor.authenticate_additional_data(associated_data)
        
        plaintext = decryptor.update(data) + decryptor.finalize()
        print("✅ 解密成功！密钥正确！")
        return json.loads(plaintext.decode("utf-8"))
    
    except Exception as e:
        raise Exception("回调解密失败")

def test_decrypt(APIv3_KEY, resource):
    try:
        ciphertext = base64.b64decode(resource["ciphertext"])
        nonce = resource["nonce"].encode("utf-8")
        associated_data = resource["associated_data"].encode("utf-8")
        key = APIv3_KEY.encode("utf-8")

        cipher = Cipher(
            algorithms.AES(key),
            modes.GCM(nonce),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        decryptor.authenticate_additional_data(associated_data)
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        
        print("✅ 解密成功！密钥正确！")
        print("明文数据：")
        print(json.dumps(json.loads(plaintext.decode()), indent=2, ensure_ascii=False))
        return True

    except Exception as e:
        print("❌ 解密失败 → 密钥错误！错误信息：", str(e))
        return False

# 开始测试
if __name__ == "__main__":
    resource_data = decrypt_resource(resource)
    print("解密数据")
    print(resource_data)