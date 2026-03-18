# 快速修复：文档获取失败问题

## 问题
点击文件ID时显示"Document not found or decryption failed"错误。

## 原因
文档还没有被加密存储到服务器。

## 解决方案（3步）

### 步骤1：安装依赖（如果还没有）
```bash
cd Hermes/web_api
pip install cryptography
```

### 步骤2：初始化文档
```bash
# 在 web_api 目录下运行
python init_documents.py --mode simple --num-writers 25 --files-per-writer 100
```

这会为每个写入者创建100个加密文档（文件ID 1-100），应该能覆盖大部分搜索结果。

### 步骤3：重新测试
刷新浏览器页面，再次点击文件ID，应该可以正常查看文档了。

## 验证

检查文档是否创建成功：
```bash
ls -la ../encrypted_docs/ | head -20
```

应该看到类似这样的文件：
```
0_1.enc
0_2.enc
0_3.enc
...
1_1.enc
1_2.enc
...
```

## 如果还有问题

1. **检查文件路径**：确保 `Hermes/encrypted_docs/` 目录存在且有写入权限
2. **检查文件ID范围**：确保初始化的文件ID覆盖搜索返回的文件ID
3. **查看日志**：检查Python Web服务的控制台输出，查看详细错误信息

## 高级选项

### 从database文件初始化（如果有真实数据）
```bash
python init_documents.py --mode database --database-dir ../database --num-writers 25
```

这会从 `database/` 目录读取真实的文件ID，并创建对应的加密文档。

### 创建更多文档
```bash
# 每个写入者创建200个文档
python init_documents.py --mode simple --num-writers 25 --files-per-writer 200
```
