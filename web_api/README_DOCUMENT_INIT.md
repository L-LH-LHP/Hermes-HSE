# 文档初始化说明

## 问题

如果遇到"Document not found or decryption failed"错误，说明文档还没有被加密存储。

## 解决方案

### 方法1：使用初始化脚本（推荐）

运行文档初始化脚本：

```bash
cd Hermes/web_api

# 简单模式：为每个写入者创建50个模拟文档
python init_documents.py --mode simple --num-writers 25 --files-per-writer 50

# 或者从database文件读取文件ID（如果有database目录）
python init_documents.py --mode database --database-dir ../database --num-writers 25
```

### 方法2：手动安装依赖

如果使用cryptography库进行加密，需要安装：

```bash
pip install cryptography
```

### 方法3：检查文件路径

确保加密文档存储在正确的位置：
- 默认路径：`Hermes/encrypted_docs/`
- 文件命名格式：`{writer_id}_{file_id}.enc`
- 例如：`0_200.enc` 表示写入者0的文件ID 200

## 验证

初始化后，检查加密文档是否创建：

```bash
ls -la ../encrypted_docs/
```

应该看到类似 `0_1.enc`, `0_2.enc`, `1_1.enc` 等文件。

## 注意事项

1. **文件ID范围**：确保初始化的文件ID范围覆盖搜索返回的文件ID
2. **权限**：确保`encrypted_docs/`目录有写入权限
3. **存储空间**：大量文档会占用存储空间

## 快速测试

如果只是想测试功能，可以运行：

```bash
python init_documents.py --mode simple --num-writers 25 --files-per-writer 10
```

这会为每个写入者创建10个文档（文件ID 1-10），足以进行基本测试。
