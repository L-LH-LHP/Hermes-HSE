# 真实文档查看功能说明

## 功能概述

现在系统支持从加密索引点击进入，查看**真实的Enron邮件内容**，而不是模拟文档。

## 实现原理

### 1. 数据提取阶段（extract_database.go）

在运行 `extract_database.go` 时，系统会：

1. **创建映射文件**：在 `database_paths/` 目录下为每个写入者创建映射文件
   - 文件名：`{userID}.txt`（userID从1开始）
   - 格式：每行 `fileID 邮件文件路径`
   - 示例：`database_paths/1.txt`
     ```
     1 ./maildir/user1/inbox/1.
     2 ./maildir/user1/inbox/2.
     3 ./maildir/user1/sent/1.
     ```

2. **提取关键词**：同时生成 `database/{userID}.txt`（用于构建加密索引）

### 2. Web API阶段（app.py）

当用户点击文件ID时：

1. **前端请求**：`POST /api/document` 携带 `writer_id` 和 `file_id`
2. **后端查找**：
   - 读取 `database_paths/{writer_id+1}.txt`
   - 根据 `file_id` 查找对应的邮件文件路径
3. **读取邮件**：从 `maildir/` 目录读取原始邮件内容
4. **返回前端**：将邮件内容返回给前端显示

## 使用步骤

### 步骤1：重新运行数据提取（如果还没有映射文件）

```bash
# 确保 maildir 目录在项目根目录
# 运行提取脚本
go env -w GO111MODULE=off
go get github.com/montanaflynn/stats
go run extract_database.go
```

这会生成：
- `database/` 目录：关键词索引（用于构建加密索引）
- `database_paths/` 目录：fileID到邮件路径的映射

### 步骤2：启动服务器和Web服务

```bash
# 终端1：启动Hermes服务器
cd Hermes/server
./server 10  # 假设10个写入者

# 终端2：启动Web服务
cd Hermes/web_api
python3 app.py
```

### 步骤3：在浏览器中测试

1. 打开 `http://127.0.0.1:5000`
2. 搜索关键词（如 "ages"）
3. 点击搜索结果中的文件ID
4. 应该能看到真实的Enron邮件内容

## 文件结构

```
项目根目录/
├── maildir/                    # Enron邮件数据集
│   ├── user1/
│   │   ├── inbox/
│   │   └── sent/
│   └── ...
├── database/                   # 关键词索引（用于构建加密索引）
│   ├── 1.txt
│   └── ...
├── database_paths/             # fileID到邮件路径的映射（新增）
│   ├── 1.txt                   # 格式：fileID 邮件路径
│   └── ...
├── extract_database.go        # 数据提取脚本（已修改）
└── Hermes/
    ├── server/
    ├── client/
    └── web_api/
        └── app.py              # Web API（已修改）
```

## 安全性说明

- **C++ Hermes服务器**：只处理加密索引，看不到原始邮件内容
- **Web前端**：作为"读者客户端"，有权限读取原始邮件用于展示
- **映射文件**：存储在本地，不通过网络传输

## 注意事项

1. **路径问题**：确保 `maildir/` 目录在项目根目录，或修改 `extract_database.go` 中的路径
2. **编码问题**：邮件文件可能使用多种编码（UTF-8, Latin-1等），代码已自动处理
3. **文件不存在**：如果映射文件或邮件文件不存在，会返回404错误

## 故障排除

### 问题1：找不到映射文件

**错误**：`Mapping file not found for writer_id=X`

**解决**：重新运行 `extract_database.go` 生成映射文件

### 问题2：找不到邮件文件

**错误**：`Mail file not found: ./maildir/...`

**解决**：
1. 检查 `maildir/` 目录是否存在
2. 检查映射文件中的路径是否正确
3. 确保邮件文件确实存在

### 问题3：编码错误

**现象**：邮件内容显示乱码

**解决**：代码已自动尝试多种编码，如果仍有问题，可能需要手动指定编码

## 与模拟文档的区别

| 特性 | 模拟文档（旧） | 真实文档（新） |
|------|--------------|--------------|
| 数据来源 | `encrypted_docs/`（模拟生成） | `maildir/`（真实Enron邮件） |
| 映射文件 | 不需要 | `database_paths/` |
| 初始化 | `init_documents.py` | `extract_database.go` |
| 内容 | 模拟文本 | 真实邮件内容 |

## 下一步改进

1. 添加邮件格式解析（From, To, Subject等）
2. 支持邮件附件
3. 添加邮件搜索高亮
4. 优化大邮件显示性能
