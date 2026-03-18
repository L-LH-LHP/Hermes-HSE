#!/usr/bin/env python3
"""
诊断脚本：检查文档初始化环境
"""

import os
import sys
from pathlib import Path

print("=" * 60)
print("文档初始化环境诊断")
print("=" * 60)

# 1. 检查当前目录
print(f"\n1. 当前工作目录: {os.getcwd()}")

# 2. 检查脚本文件是否存在
script_files = ['init_documents.py', 'init_docs_simple.py']
print(f"\n2. 检查脚本文件:")
for script in script_files:
    script_path = Path(script)
    if script_path.exists():
        print(f"   ✓ {script} 存在")
        print(f"     绝对路径: {script_path.absolute()}")
    else:
        print(f"   ✗ {script} 不存在")

# 3. 检查Python版本
print(f"\n3. Python环境:")
print(f"   Python版本: {sys.version}")
print(f"   Python路径: {sys.executable}")

# 4. 检查依赖库
print(f"\n4. 检查依赖库:")
dependencies = {
    'cryptography': 'cryptography',
    'pathlib': 'pathlib (内置)',
    'hashlib': 'hashlib (内置)'
}

for module, name in dependencies.items():
    try:
        __import__(module)
        print(f"   ✓ {name} 已安装")
    except ImportError:
        print(f"   ✗ {name} 未安装")

# 5. 检查输出目录权限
print(f"\n5. 检查输出目录:")
output_dir = Path("../encrypted_docs")
output_dir_abs = output_dir.resolve()
print(f"   输出目录: {output_dir_abs}")

if output_dir_abs.exists():
    print(f"   ✓ 目录存在")
    if os.access(output_dir_abs, os.W_OK):
        print(f"   ✓ 有写入权限")
    else:
        print(f"   ✗ 无写入权限")
else:
    print(f"   - 目录不存在（将自动创建）")
    parent = output_dir_abs.parent
    if parent.exists() and os.access(parent, os.W_OK):
        print(f"   ✓ 父目录有写入权限，可以创建")
    else:
        print(f"   ✗ 父目录无写入权限")

# 6. 提供解决方案
print(f"\n6. 建议的解决方案:")
print(f"   如果 init_documents.py 无法运行，尝试:")
print(f"   python init_docs_simple.py --num-writers 25 --files-per-writer 100")
print(f"\n   或者直接运行:")
print(f"   python3 init_docs_simple.py --num-writers 25 --files-per-writer 100")

print("\n" + "=" * 60)
