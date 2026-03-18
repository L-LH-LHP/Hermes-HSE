# 文档加密存储和解密功能说明

## 功能概述

本功能实现了完整的文档加密存储和客户端解密查看功能，确保：
- **服务器端**：只存储加密的文档，无法查看明文内容
- **客户端**：可以根据文件ID获取加密文档并解密查看

## 架构设计

```
┌─────────────────┐
│   原始文档文件    │  (明文)
│  (maildir/...)  │
└────────┬────────┘
         │ 加密存储
┌────────▼────────┐
│   服务器存储     │  (加密文档)
│ encrypted_docs/ │  - writer_id_file_id.enc
└────────┬────────┘
         │ 客户端请求（仅文件ID）
┌────────▼────────┐
│  Web API        │  (返回加密文档)
└────────┬────────┘
         │ 客户端解密
┌────────▼────────┐
│   客户端显示     │  (解密后的明文)
│  (浏览器)       │
└─────────────────┘
```

## 关键组件

### 1. 文档加密存储模块

**文件**: `document_storage.hpp`, `document_storage.cpp`

**功能**:
- `DocumentStorage::encrypt_and_store()`: 加密原始文档并存储
- `DocumentStorage::get_encrypted_document()`: 获取加密文档
- 使用AES-256-CTR加密算法

**加密密钥派生**:
```cpp
// 密钥 = SHA512(writer_id + "_" + file_id)[:32]
// IV = SHA512(writer_id + "_" + file_id)[32:48]
```

### 2. 客户端解密模块

**文件**: `document_storage.cpp` 中的 `DocumentDecryptor` 类

**功能**:
- `DocumentDecryptor::derive_decryption_key()`: 派生解密密钥
- `DocumentDecryptor::decrypt_document()`: 解密文档

**解密密钥派生**:
```cpp
// 解密密钥 = SHA512(reader_secret + writer_id + file_id)[:32]
// 注意：在实际应用中，reader_secret应该从HICKAE系统获取
```

### 3. C++ API接口

**文件**: `hermes_client_api.hpp`, `hermes_client_api.cpp`

**新增API**:
- `hermes_get_encrypted_document()`: 获取加密文档
- `hermes_decrypt_document()`: 解密文档
- `hermes_free_buffer()`: 释放内存缓冲区

### 4. Python客户端接口

**文件**: `hermes_python_client.py`

**新增方法**:
- `HermesClient.get_document(writer_id, file_id)`: 获取并解密文档

### 5. Web API接口

**文件**: `app.py`

**新增路由**:
- `POST /api/document`: 获取并解密文档内容

**请求格式**:
```json
{
    "writer_id": 0,
    "file_id": 1
}
```

**响应格式**:
```json
{
    "success": true,
    "writer_id": 0,
    "file_id": 1,
    "content": "解密后的文档内容...",
    "encoding": "utf-8",
    "size": 1234
}
```

### 6. 前端界面

**文件**: `static/script.js`, `static/style.css`

**新增功能**:
- 搜索结果中的文件ID可点击
- 点击后弹出文档查看模态框
- 显示解密后的文档内容
- 支持文本和二进制文件的查看和下载

## 使用流程

### 1. 加密存储文档（初始化阶段）

在服务器启动时，需要将原始文档加密存储：

```cpp
DocumentStorage storage("../encrypted_docs/");
// 对每个文档进行加密存储
storage.encrypt_and_store(writer_id, file_id, original_file_path);
```

### 2. 客户端搜索文档

用户通过Web界面搜索关键词，获取匹配的文件ID列表。

### 3. 客户端查看文档

用户点击文件ID，系统会：
1. 客户端请求 `/api/document`，仅发送文件ID
2. Web API调用C++库获取加密文档
3. 客户端使用密钥解密文档
4. 在浏览器中显示解密后的内容

## 安全性说明

### 服务器端保护

- 服务器只存储加密文档（`.enc`文件）
- 服务器没有解密密钥，无法查看明文
- 即使服务器被攻击，攻击者也无法获取明文内容

### 客户端解密

- 解密密钥在客户端派生（基于reader_secret）
- 解密过程在客户端完成
- 服务器无法看到解密后的内容

### 改进建议

**当前实现（简化版）**:
- 使用固定的reader_secret（"reader_secret"）
- 密钥派生基于SHA512

**生产环境建议**:
- reader_secret应该从HICKAE系统的读者密钥派生
- 使用更安全的密钥管理机制
- 考虑添加访问控制（只有授权读者可以解密）

## 文件结构

```
Hermes/
├── web_api/
│   ├── document_storage.hpp      # 文档存储头文件
│   ├── document_storage.cpp      # 文档存储实现
│   ├── hermes_client_api.hpp     # 客户端API（已更新）
│   ├── hermes_client_api.cpp     # 客户端API实现（已更新）
│   ├── hermes_python_client.py   # Python客户端（已更新）
│   ├── app.py                    # Web API（已更新）
│   └── static/
│       ├── script.js             # 前端脚本（已更新）
│       └── style.css             # 样式表（已更新）
└── encrypted_docs/               # 加密文档存储目录（运行时创建）
    ├── 0_1.enc
    ├── 0_2.enc
    └── ...
```

## 注意事项

1. **文档初始化**: 需要在实际使用前将原始文档加密存储
2. **密钥管理**: 当前使用简化版密钥派生，生产环境需要改进
3. **存储路径**: 确保`encrypted_docs/`目录有写入权限
4. **性能考虑**: 大文件解密可能较慢，考虑添加进度提示

## 下一步改进

1. 集成到服务器初始化流程，自动加密存储文档
2. 改进密钥管理，使用HICKAE系统的读者密钥
3. 添加文档访问日志和审计
4. 支持文档更新和删除操作的加密处理
5. 添加文档缓存机制以提高性能
