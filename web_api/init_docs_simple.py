#!/usr/bin/env python3
"""
简化版文档初始化工具
如果init_documents.py无法运行，可以使用这个简化版本
"""

import os
import sys
from pathlib import Path
import hashlib

def create_mock_document(writer_id, file_id):
    """创建模拟文档内容"""
    keywords_list = ["university", "security", "network", "system", "data", 
                     "information", "technology", "computer", "software", "hardware"]
    
    selected_keywords = keywords_list[file_id % len(keywords_list):(file_id % len(keywords_list) + 3)]
    
    content = f"""文档ID: {file_id}
写入者ID: {writer_id + 1}
创建时间: 2026-01-20

这是一个模拟文档内容。在实际应用中，这里应该是从原始数据源（如maildir）读取的真实文档内容。

文件ID {file_id} 对应的原始文档内容将在这里显示。
关键词搜索功能可以找到包含特定关键词的所有文档。

文档内容示例：
- 这是文档的第一段内容
- 包含关键词: {', '.join(selected_keywords)}
- 用于演示可搜索加密系统的功能

相关关键词: {', '.join(selected_keywords)}
文档编号: {file_id}
写入者: Writer {writer_id + 1}

这是文档的正文内容。在实际应用中，这里会显示从原始数据源读取的真实文档内容。
文档可能包含多个段落，涉及不同的主题和关键词。

文档结束。
"""
    return content.encode('utf-8')

def init_documents_simple(output_dir="../encrypted_docs", num_writers=25, files_per_writer=100):
    """创建并加密存储模拟文档"""
    
    # 获取绝对路径
    script_dir = Path(__file__).parent.absolute()
    if output_dir.startswith('../'):
        output_path = script_dir.parent / output_dir[3:]
    else:
        output_path = Path(output_dir).absolute()
    
    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"创建模拟文档...")
    print(f"当前目录: {os.getcwd()}")
    print(f"脚本目录: {script_dir}")
    print(f"输出目录: {output_path}")
    
    # 检查cryptography库
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        has_crypto = True
        print("使用cryptography库进行加密")
    except ImportError:
        print("警告: cryptography库未安装，使用简化XOR加密")
        print("安装命令: pip install cryptography")
        has_crypto = False
    
    total = 0
    
    for writer_id in range(num_writers):
        if writer_id % 5 == 0:
            print(f"处理写入者 {writer_id + 1}/{num_writers}...")
        
        for file_id in range(1, files_per_writer + 1):
            # 创建模拟文档
            doc_content = create_mock_document(writer_id, file_id)
            
            # 派生密钥和IV
            key_seed = f"{writer_id}_{file_id}".encode('utf-8')
            key_hash = hashlib.sha512(key_seed).digest()
            key = key_hash[:32]
            iv = key_hash[32:48]
            
            if has_crypto:
                # 使用cryptography库加密
                cipher = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend())
                encryptor = cipher.encryptor()
                ciphertext = encryptor.update(doc_content) + encryptor.finalize()
            else:
                # 简化版：XOR加密
                ciphertext = bytearray(doc_content)
                for i in range(len(ciphertext)):
                    ciphertext[i] ^= key[i % 32]
                ciphertext = bytes(ciphertext)
            
            # 保存加密文档
            enc_file = output_path / f"{writer_id}_{file_id}.enc"
            try:
                with open(enc_file, 'wb') as f:
                    f.write(iv)  # IV (16字节)
                    f.write(len(ciphertext).to_bytes(4, 'big'))  # 长度 (4字节)
                    f.write(ciphertext)  # 密文
                total += 1
            except Exception as e:
                print(f"错误: 无法写入文件 {enc_file}: {e}")
                return False
    
    print(f"\n完成！已创建 {total} 个加密文档")
    print(f"文档保存在: {output_path}")
    return True

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='初始化文档加密存储（简化版）')
    parser.add_argument('--output-dir', default='../encrypted_docs',
                       help='加密文档输出目录')
    parser.add_argument('--num-writers', type=int, default=25,
                       help='写入者数量')
    parser.add_argument('--files-per-writer', type=int, default=100,
                       help='每个写入者的文档数量')
    
    args = parser.parse_args()
    
    success = init_documents_simple(args.output_dir, args.num_writers, args.files_per_writer)
    
    if success:
        print("\n✓ 文档初始化成功！")
        print("现在可以刷新浏览器，点击文件ID查看文档了。")
    else:
        print("\n✗ 文档初始化失败，请检查错误信息。")
        sys.exit(1)
