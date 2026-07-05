# Hermes 云端多写者可搜索加密系统

Hermes 是一个面向云端数据审计场景的多写者可搜索加密原型系统。项目在原 Hermes C++ 实现的基础上，增加了 Flask Web 控制台、Python 调用封装、文档/邮件数据预处理、角色登录、写者侧文件管理、审计员检索和管理员 Epoch 推进等演示能力，方便在浏览器中体验“密文索引存储在云端、授权用户按关键词检索”的完整流程。

> 说明：本项目是学术研究和课程/答辩演示用途的原型系统，尚未经过生产级安全审计，不建议直接用于真实生产环境。

## 功能特性

- 多写者 encrypted database：每个写者维护独立关键词索引，服务端执行跨写者搜索。
- 关键词搜索与更新：支持审计员检索、写者新增/更新文件、同步更新索引。
- Web 角色工作台：内置读者/审计员、写者、管理员三类角色页面。
- Enron 邮件数据预处理：可从 `maildir` 提取关键词，生成 Hermes 所需的 `database/` 与 `database_paths/`。
- 文档内容管理：支持预览、编辑、批量创建、ZIP 导入、文件删除等演示操作。
- 审计批次与 Epoch：支持全局审计批次推进、写者侧前向安全版本推进、关键词 TTL 清理。
- Python/C++ 桥接：`web_api/hermes_python_client.py` 通过 `ctypes` 调用 C++ 共享库，失败时会进入降级/模拟模式，便于调试 Web 页面。

## 项目结构

```text
.
├── README.md
├── auto_setup.sh                  # Linux 依赖自动安装脚本
├── extract_database.go            # 原始 Enron 数据提取脚本
└── Hermes/
    ├── config.hpp                 # C++ 服务端端口、线程数、搜索策略等配置
    ├── hickae.hpp                 # 核心加密索引逻辑
    ├── types.hpp
    ├── client/
    │   ├── client.cpp             # 原 C++ 命令行客户端
    │   └── client                 # 已构建的客户端二进制文件（如存在）
    ├── server/
    │   ├── server.cpp             # C++ 搜索服务端
    │   └── server                 # 已构建的服务端二进制文件（如存在）
    ├── include/                   # GMP、PBC、ZeroMQ、EMP 等头文件
    ├── param/                     # PBC pairing 参数
    └── web_api/
        ├── app.py                 # Flask 后端入口
        ├── config.py              # Web/API 环境配置
        ├── Makefile               # 构建 libhermes_client.so / dll
        ├── hermes_python_client.py
        ├── enron_preprocess.py    # Enron maildir 预处理
        ├── init_documents.py      # 初始化演示文档
        ├── templates/             # 登录、审计员、写者、管理员页面
        └── static/                # 前端样式与交互脚本
```

## 环境要求

推荐在 Linux 或 WSL2 环境运行核心 C++ 服务端和 Web API。Windows 下可以阅读代码和运行部分 Python 页面，但 C++ 依赖、动态库和 ZeroMQ 通信更建议放在 Linux/WSL 中完成。

基础依赖：

- C++ 编译工具链：`g++`、`make`、`cmake`
- Python 3.10+
- Go 1.17+（用于 `extract_database.go`）
- GMP
- PBC
- ZeroMQ / cppzmq
- OpenSSL
- EMP-Toolkit：`emp-tool`、`emp-ot`、`emp-agmpc`

可使用脚本安装主要依赖：

```bash
chmod +x auto_setup.sh
./auto_setup.sh
```

脚本默认把部分库安装到 `~/Hermes`，运行后请确保：

```bash
export LD_LIBRARY_PATH="$HOME/Hermes/lib:$LD_LIBRARY_PATH"
```

## 数据准备

### 方式一：使用 Enron 邮件数据集

下载 Enron 邮件数据集并解压得到 `maildir/`：

```text
https://www.cs.cmu.edu/~enron/
```

将 `maildir/` 放到 `Hermes/` 目录下，然后执行预处理：

```bash
cd Hermes/web_api
pip install -r requirements.txt

python enron_preprocess.py \
  --maildir ../maildir \
  --database-dir ../database \
  --database-paths-dir ../database_paths \
  --max-writers 25 \
  --extractor simple
```

生成结果：

- `Hermes/database/`：每个写者的关键词到文件 ID 映射，例如 `1.txt`、`2.txt`
- `Hermes/database_paths/`：每个写者的文件 ID 到原始文件路径映射

### 方式二：初始化演示文档

如果暂时没有 Enron 数据，可以先生成模拟文档：

```bash
cd Hermes/web_api
python init_documents.py --mode simple --output-dir ../encrypted_docs --num-writers 25 --files-per-writer 50
```

也可以按已有 `database/` 生成加密文档：

```bash
python init_documents.py --mode database --database-dir ../database --output-dir ../encrypted_docs --num-writers 25
```

## 构建与启动

### 1. 安装 Python 依赖

```bash
cd Hermes/web_api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 构建 Web API 共享库

`web_api/Makefile` 会把 C++ 客户端能力编译为 Python 可加载的共享库：

```bash
cd Hermes/web_api
make clean
make
```

成功后应生成：

```text
Hermes/web_api/libhermes_client.so
```

如果依赖安装在非默认目录，可以传入额外 include 路径：

```bash
make EXTRA_INC="-I/path/to/emp/include"
```

### 3. 启动 C++ Hermes 服务端

如果 `Hermes/server/server` 已存在，可直接启动：

```bash
cd Hermes/server
./server 25
```

参数 `25` 表示写者数量。不传参数时，服务端默认使用 25 个写者。默认监听端口在 `Hermes/config.hpp` 中配置：

```cpp
const int SERVER_PORT = 8888;
```

如果服务端二进制不存在，请先按项目当前 C++ 构建方式编译 `server/server.cpp`。早期 Hermes 版本通常通过 `Hermes/Makefile` 执行 `make` 生成 `server/server` 与 `client/client`。

### 4. 启动 Flask Web 控制台

另开一个终端：

```bash
cd Hermes/web_api
python app.py
```

默认访问地址：

```text
http://127.0.0.1:5000
```

## 常用环境变量

可以在启动 `app.py` 前按需覆盖配置：

```bash
export HERMES_SERVER="tcp://127.0.0.1:8888"
export HERMES_NUM_WRITERS=25
export FLASK_PORT=5000
export FLASK_DEBUG=false
export HERMES_ALLOWED_WRITERS=all
export HERMES_EPOCH=1

export HERMES_WEB_SECRET="change-this-secret"
export HERMES_READER_USERNAME="reader"
export HERMES_READER_PASSWORD="reader123"
export HERMES_WRITER_PASSWORD_PREFIX="writer"
export HERMES_ADMIN_USERNAME="admin"
export HERMES_ADMIN_PASSWORD="admin123"
```

## 默认登录账号

| 角色 | 用户名 / ID | 默认密码 | 说明 |
| --- | --- | --- | --- |
| 审计员 reader | `reader` | `reader123` | 可搜索被授权写者的数据 |
| 写者 writer | writer ID，例如 `0` | `writer1` | 密码规则是 `HERMES_WRITER_PASSWORD_PREFIX + (writer_id + 1)` |
| 管理员 admin | `admin` | `admin123` | 可推进全局 Epoch 和管理审计批次 |

示例：writer ID 为 `0` 时默认密码是 `writer1`；writer ID 为 `4` 时默认密码是 `writer5`。

## Web 使用流程

1. 启动 C++ 服务端：`./server 25`
2. 启动 Flask：`python app.py`
3. 浏览器打开 `http://127.0.0.1:5000`
4. 使用审计员账号登录，输入关键词进行跨写者检索
5. 使用写者账号登录，创建文件、批量导入文件、维护关键词黑白名单或推进写者 Epoch
6. 使用管理员账号登录，推进全局审计 Epoch，查看批处理状态

## 常用 API

所有 API 均由 `Hermes/web_api/app.py` 提供，部分接口需要先登录。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | 登录 |
| `POST` | `/api/auth/logout` | 退出登录 |
| `GET` | `/api/status` | 查看服务状态 |
| `POST` | `/api/search` | 按关键词搜索 |
| `POST` | `/api/update` | 更新关键词索引 |
| `GET` | `/api/document-content` | 读取原始文档内容 |
| `POST` | `/api/update-document` | 更新文档内容并同步索引 |
| `GET` | `/api/writers` | 获取写者列表 |
| `GET` | `/api/writer/files` | 写者查看文件 |
| `POST` | `/api/writer/create-file` | 写者创建文件 |
| `POST` | `/api/writer/batch-create-files` | 批量创建文件 |
| `POST` | `/api/writer/batch-import-zip` | 导入 ZIP 文件 |
| `POST` | `/api/reload-index` | 重新加载索引 |
| `POST` | `/api/admin/epoch/advance` | 管理员推进全局 Epoch |
| `GET` | `/api/global-epoch` | 查询当前全局 Epoch |

搜索请求示例：

```bash
curl -X POST http://127.0.0.1:5000/api/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"security","writer_ids":[0,1,2]}'
```

## C++ 命令行用法

服务端：

```bash
cd Hermes/server
./server 25
```

客户端关键词搜索：

```bash
cd Hermes/client
./client -s university 25
```

客户端关键词更新：

```bash
cd Hermes/client
./client -u 150
```

## 重要配置

`Hermes/config.hpp` 中包含核心 C++ 参数：

```cpp
const int MAX_THREADS_INIT = 8;
const int MAX_THREADS_SEARCH = 8;
const int MAX_THREADS_UPDATE = 4;
const int SERVER_PORT = 8888;

#define ENABLE_SEPARATE_SEARCH 1
#define WRITER_EFFICIENCY 1
#define SEARCH_EFFICIENCY 1
```

修改后需要重新编译相关 C++ 组件或共享库。

## 常见问题

### 1. Flask 能打开，但搜索没有结果

请检查：

- C++ 服务端是否已经启动
- `HERMES_SERVER` 是否指向正确地址，例如 `tcp://127.0.0.1:8888`
- `Hermes/database/` 是否存在并包含 `1.txt`、`2.txt` 等索引文件
- `Hermes/database_paths/` 是否存在，用于展示搜索结果对应的原始文档

### 2. `libhermes_client.so` 加载失败

请检查：

- 是否在 `Hermes/web_api` 下执行过 `make`
- `LD_LIBRARY_PATH` 是否包含依赖库目录
- GMP、PBC、ZeroMQ、OpenSSL、EMP-Toolkit 是否安装完成

### 3. 写者登录失败

写者 ID 是 0-based，密码默认是 `writer + (writer_id + 1)`。例如：

- writer ID `0`：密码 `writer1`
- writer ID `1`：密码 `writer2`

### 4. 修改端口或写者数量后不生效

Web 层读取环境变量，C++ 层读取 `config.hpp` 和启动参数。修改 `config.hpp` 后需要重新编译；修改环境变量后需要重启 Flask。

## 引用

本项目基于 Hermes 论文实现扩展：

```bibtex
@inproceedings{le2025hermes,
  author = {Le, Tung and Hoang, Thang},
  title = {{Hermes: Efficient and Secure Multi-Writer Encrypted Database}},
  booktitle = {46th IEEE Symposium on Security and Privacy (IEEE S&P 2025)},
  year = {2025},
  pages = {2642-2661},
  address = {San Francisco, CA, USA},
  month = {May}
}
```

## 许可证与安全声明

请根据你最终发布仓库时采用的许可证补充 `LICENSE` 文件。本 README 中的系统说明仅用于帮助他人了解和运行项目；涉及加密、安全、访问控制的代码仍需经过独立安全审计后才能用于真实业务场景。
