# Hermes Web API

Hermes可搜索加密系统的Web界面，基于Python Flask和C++客户端库。

## 功能特性

- 🔍 **搜索功能**: 在加密数据中搜索关键词，返回匹配的文件ID列表
- 📝 **更新功能**: 向加密数据库中添加新的关键词-文件关联
- 📊 **系统状态**: 查看服务器连接状态和配置信息
- 🎨 **现代UI**: 响应式设计，支持移动设备

## 架构说明

```
┌─────────────────┐
│  Web浏览器       │  (前端界面)
└────────┬────────┘
         │ HTTP
┌────────▼────────┐
│  Flask Web服务   │  (Python后端)
└────────┬────────┘
         │ 调用C++库
┌────────▼────────┐
│  C++客户端库     │  (加密核心)
└────────┬────────┘
         │ ZeroMQ
┌────────▼────────┐
│  C++ Server     │  (搜索服务器)
└─────────────────┘
```

## 安装步骤

### 1. 安装Python依赖

```bash
cd Hermes/web_api
pip install -r requirements.txt
```

### 2. 编译C++客户端库

```bash
# 在 web_api 目录下
make lib
```

如果编译成功，会生成 `libhermes_client.so` (Linux) 或 `libhermes_client.dll` (Windows)。

**注意**: 确保以下库已安装：
- GMP
- PBC (Pairing-Based Cryptography)
- ZeroMQ
- OpenSSL

### 3. 启动C++服务器

```bash
# 在 Hermes 目录下
cd server
./server [num_writers]
```

例如启动25个写入者的服务器：
```bash
./server 25
```

### 4. 启动Web服务

```bash
# 在 web_api 目录下
python app.py
```

默认在 `http://localhost:5000` 启动。

## 配置

可以通过环境变量配置：

```bash
# 服务器地址
export HERMES_SERVER="tcp://127.0.0.1:8888"

# 写入者数量
export HERMES_NUM_WRITERS=25

# Flask端口
export FLASK_PORT=5000

# 调试模式
export FLASK_DEBUG=True
```

## 使用说明

### 搜索操作

1. 打开Web界面 (`http://localhost:5000`)
2. 切换到"搜索"标签页
3. 输入关键词（例如："university"）
4. （可选）选择要搜索的写入者
5. 点击"搜索"按钮
6. 查看搜索结果：每个写入者的匹配文件ID列表

### 更新操作

1. 切换到"更新"标签页
2. 选择写入者ID
3. 输入关键词
4. 输入文件ID
5. 点击"更新"按钮
6. 确认更新成功消息

### API接口

#### 搜索API

```bash
POST /api/search
Content-Type: application/json

{
    "keyword": "university",
    "writer_ids": [0, 1, 2]  # 可选
}
```

响应：
```json
{
    "success": true,
    "keyword": "university",
    "results": [
        {"writer_id": 1, "file_ids": [1, 2, 3]},
        {"writer_id": 2, "file_ids": [5, 6]}
    ]
}
```

#### 更新API

```bash
POST /api/update
Content-Type: application/json

{
    "writer_id": 0,
    "keyword": "security",
    "file_id": 2025
}
```

响应：
```json
{
    "success": true,
    "message": "Successfully updated..."
}
```

#### 状态API

```bash
GET /api/status
```

响应：
```json
{
    "status": "online",
    "server_address": "tcp://127.0.0.1:8888",
    "num_writers": 25
}
```

## 故障排除

### 1. 库加载失败

如果看到 "Hermes library not found" 警告：
- 确保已编译C++库：`make lib`
- 确保库文件在Python可以访问的路径
- 或者设置 `LD_LIBRARY_PATH` (Linux) 或 `PATH` (Windows)

### 2. 连接服务器失败

- 确保C++服务器正在运行
- 检查服务器地址配置
- 确认ZeroMQ端口未被占用

### 3. 搜索返回空结果

- 确认数据已通过更新API添加
- 检查关键词拼写
- 查看C++服务器的日志输出

## 开发说明

### 文件结构

```
web_api/
├── app.py                    # Flask Web应用
├── hermes_python_client.py   # Python客户端包装器
├── hermes_client_api.hpp     # C++ API头文件
├── hermes_client_api.cpp     # C++ API实现
├── Makefile                  # 构建脚本
├── requirements.txt          # Python依赖
├── templates/
│   └── index.html           # 前端HTML
└── static/
    ├── style.css            # 样式表
    └── script.js            # 前端JavaScript
```

### 扩展开发

1. **添加新API**: 在 `app.py` 中添加新的路由
2. **修改前端**: 编辑 `templates/index.html` 和 `static/` 下的文件
3. **扩展C++功能**: 修改 `hermes_client_api.cpp` 并重新编译

## 许可证

与Hermes主项目相同。

## 贡献

欢迎提交Issue和Pull Request！

