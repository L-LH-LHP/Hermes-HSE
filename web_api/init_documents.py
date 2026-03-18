#!/usr/bin/env python3
"""
文档加密存储初始化工具
根据database文件中的文件ID，创建模拟文档并加密存储
"""

import os
import sys
from pathlib import Path

# 添加当前目录到路径
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # 如果__file__不存在，使用当前工作目录
    script_dir = os.getcwd()
sys.path.insert(0, script_dir)

try:
    from hermes_python_client import HermesClient
except ImportError:
    print("Warning: hermes_python_client not found")

def create_mock_document(writer_id, file_id):
    """创建模拟文档内容"""
    # 根据文件ID生成不同的内容，包含一些常见关键词
    keywords_list = ["university", "security", "network", "system", "data", 
                     "information", "technology", "computer", "software", "hardware"]
    
    # 根据file_id选择关键词（确保不同文件有不同内容）
    selected_keywords = keywords_list[file_id % len(keywords_list):(file_id % len(keywords_list) + 3)]
    
    return f"""文档ID: {file_id}
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
""".encode('utf-8')

def init_documents_from_database(database_dir="../database", output_dir="../encrypted_docs", num_writers=25):
    """
    从database文件读取文件ID，创建并加密存储文档
    
    Args:
        database_dir: database文件目录
        output_dir: 加密文档输出目录
        num_writers: 写入者数量
    """
    database_path = Path(database_dir)
    output_path = Path(output_dir)
    
    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"初始化文档加密存储...")
    print(f"数据库目录: {database_path}")
    print(f"输出目录: {output_path}")
    
    # 统计信息
    total_files = 0
    total_keywords = 0
    
    # 处理每个写入者的数据库文件
    for writer_id in range(num_writers):
        db_file = database_path / f"{writer_id + 1}.txt"
        
        if not db_file.exists():
            print(f"警告: 数据库文件不存在: {db_file}")
            continue
        
        print(f"\n处理写入者 {writer_id + 1}...")
        
        # 读取数据库文件，提取所有文件ID
        file_ids = set()
        with open(db_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                keyword = parts[0]
                total_keywords += 1
                # 提取文件ID
                for file_id_str in parts[1:]:
                    try:
                        file_id = int(file_id_str)
                        file_ids.add(file_id)
                    except ValueError:
                        continue
        
        print(f"  找到 {len(file_ids)} 个唯一文件ID")
        
        # 为每个文件ID创建并加密存储文档
        for file_id in sorted(file_ids):
            # 创建模拟文档
            doc_content = create_mock_document(writer_id, file_id)
            
            # 保存为临时文件
            temp_file = output_path / f"temp_{writer_id}_{file_id}.txt"
            with open(temp_file, 'wb') as f:
                f.write(doc_content)
            
            # 使用C++库加密存储（如果可用）
            # 否则直接使用Python加密
            try:
                # 尝试使用C++库
                import ctypes
                lib_path = Path(__file__).parent / "libhermes_client.so"
                if lib_path.exists():
                    # 这里可以调用C++函数，但为了简化，我们使用Python加密
                    pass
            except:
                pass
            
            # 使用Python进行加密（简化版）
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            import hashlib
            
            # 派生密钥和IV
            key_seed = f"{writer_id}_{file_id}".encode('utf-8')
            key_hash = hashlib.sha512(key_seed).digest()
            key = key_hash[:32]  # AES-256需要32字节
            iv = key_hash[32:48]  # CTR模式需要16字节IV
            
            # 加密
            cipher = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend())
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(doc_content) + encryptor.finalize()
            
            # 保存加密文档
            enc_file = output_path / f"{writer_id}_{file_id}.enc"
            with open(enc_file, 'wb') as f:
                f.write(iv)  # 写入IV (16字节)
                f.write(len(ciphertext).to_bytes(4, 'big'))  # 写入长度 (4字节)
                f.write(ciphertext)  # 写入密文
            
            # 删除临时文件
            temp_file.unlink()
            
            total_files += 1
        
        print(f"  已加密存储 {len(file_ids)} 个文档")
    
    print(f"\n完成！")
    print(f"总计: {total_keywords} 个关键词, {total_files} 个文档已加密存储")
    print(f"加密文档保存在: {output_path}")

def init_documents_simple(output_dir="../encrypted_docs", num_writers=25, files_per_writer=10):
    """
    简单初始化：为每个写入者创建指定数量的模拟文档
    
    Args:
        output_dir: 输出目录
        files_per_writer: 每个写入者的文档数量
    """
    # 处理相对路径
    if output_dir.startswith('../'):
        script_dir = Path(__file__).parent.absolute()
        output_path = script_dir.parent / output_dir[3:]
    else:
        output_path = Path(output_dir).absolute()
    
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"创建模拟文档...")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"脚本目录: {Path(__file__).parent.absolute()}")
    print(f"输出目录: {output_path}")
    
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        import hashlib
        has_crypto = True
    except ImportError:
        print("警告: cryptography库未安装，使用简化加密")
        print("安装命令: pip install cryptography")
        has_crypto = False
    
    total = 0
    
    for writer_id in range(num_writers):
        if writer_id % 5 == 0:
            print(f"处理写入者 {writer_id + 1}/{num_writers}...")
        
        # 注意：file_id从1开始，到files_per_writer结束（包含）
        for file_id in range(1, files_per_writer + 1):
            # 创建模拟文档
            doc_content = create_mock_document(writer_id, file_id)
            
            if has_crypto:
                # 使用cryptography库加密
                key_seed = f"{writer_id}_{file_id}".encode('utf-8')
                key_hash = hashlib.sha512(key_seed).digest()
                key = key_hash[:32]
                iv = key_hash[32:48]
                
                cipher = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend())
                encryptor = cipher.encryptor()
                ciphertext = encryptor.update(doc_content) + encryptor.finalize()
            else:
                # 简化版：直接XOR（仅用于测试）
                key_seed = f"{writer_id}_{file_id}".encode('utf-8')
                key_hash = hashlib.sha512(key_seed).digest()
                key = key_hash[:32]
                iv = key_hash[32:48]
                
                # 简单的XOR加密（仅用于演示）
                ciphertext = bytearray(doc_content)
                for i in range(len(ciphertext)):
                    ciphertext[i] ^= key[i % 32]
                ciphertext = bytes(ciphertext)
            
            # 保存加密文档
            enc_file = output_path / f"{writer_id}_{file_id}.enc"
            try:
                with open(enc_file, 'wb') as f:
                    f.write(iv)
                    f.write(len(ciphertext).to_bytes(4, 'big'))
                    f.write(ciphertext)
                total += 1
            except Exception as e:
                print(f"错误: 无法写入文件 {enc_file}: {e}")
                return False
    
    print(f"\n完成！已创建 {total} 个加密文档")
    print(f"文档保存在: {output_path}")
    return True

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='初始化文档加密存储')
    parser.add_argument('--mode', choices=['database', 'simple'], default='simple',
                       help='初始化模式: database=从database文件读取, simple=创建模拟文档')
    parser.add_argument('--database-dir', default='../database',
                       help='database文件目录')
    parser.add_argument('--output-dir', default='../encrypted_docs',
                       help='加密文档输出目录')
    parser.add_argument('--num-writers', type=int, default=25,
                       help='写入者数量')
    parser.add_argument('--files-per-writer', type=int, default=50,
                       help='每个写入者的文档数量（simple模式）')
    
    args = parser.parse_args()
    
    try:
        if args.mode == 'database':
            init_documents_from_database(args.database_dir, args.output_dir, args.num_writers)
        else:
            success = init_documents_simple(args.output_dir, args.num_writers, args.files_per_writer)
            if not success:
                sys.exit(1)
        print("\n✓ 文档初始化成功！")
        print("现在可以刷新浏览器，点击文件ID查看文档了。")
    except Exception as e:
        print(f"\n✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
