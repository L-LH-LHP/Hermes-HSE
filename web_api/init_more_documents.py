#!/usr/bin/env python3
"""
扩展文档初始化工具
为已存在的文档集合添加更多文件ID的文档
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

def extend_documents(output_dir="../encrypted_docs", num_writers=25, 
                     start_file_id=1, end_file_id=300):
    """
    扩展文档：为指定范围的文件ID创建文档
    
    Args:
        output_dir: 输出目录
        start_file_id: 起始文件ID
        end_file_id: 结束文件ID（包含）
    """
    # 处理相对路径
    if output_dir.startswith('../'):
        script_dir = Path(__file__).parent.absolute()
        output_path = script_dir.parent / output_dir[3:]
    else:
        output_path = Path(output_dir).absolute()
    
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"扩展文档范围...")
    print(f"输出目录: {output_path}")
    print(f"文件ID范围: {start_file_id} - {end_file_id}")
    
    # 检查cryptography库
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        has_crypto = True
        print("使用cryptography库进行加密")
    except ImportError:
        print("警告: cryptography库未安装，使用简化XOR加密")
        has_crypto = False
    
    total_created = 0
    total_skipped = 0
    
    for writer_id in range(num_writers):
        if writer_id % 5 == 0:
            print(f"处理写入者 {writer_id + 1}/{num_writers}...")
        
        for file_id in range(start_file_id, end_file_id + 1):
            enc_file = output_path / f"{writer_id}_{file_id}.enc"
            
            # 如果文件已存在，跳过
            if enc_file.exists():
                total_skipped += 1
                continue
            
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
            try:
                with open(enc_file, 'wb') as f:
                    f.write(iv)
                    f.write(len(ciphertext).to_bytes(4, 'big'))
                    f.write(ciphertext)
                total_created += 1
            except Exception as e:
                print(f"错误: 无法写入文件 {enc_file}: {e}")
                return False
    
    print(f"\n完成！")
    print(f"新创建: {total_created} 个文档")
    print(f"已跳过: {total_skipped} 个已存在的文档")
    print(f"文档保存在: {output_path}")
    return True

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='扩展文档范围')
    parser.add_argument('--output-dir', default='../encrypted_docs',
                       help='加密文档输出目录')
    parser.add_argument('--num-writers', type=int, default=25,
                       help='写入者数量')
    parser.add_argument('--start-id', type=int, default=1,
                       help='起始文件ID')
    parser.add_argument('--end-id', type=int, default=300,
                       help='结束文件ID（包含）')
    
    args = parser.parse_args()
    
    success = extend_documents(args.output_dir, args.num_writers, 
                              args.start_id, args.end_id)
    
    if success:
        print("\n✓ 文档扩展成功！")
    else:
        print("\n✗ 文档扩展失败，请检查错误信息。")
        sys.exit(1)
