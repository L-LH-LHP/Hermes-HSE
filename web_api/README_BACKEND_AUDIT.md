# 多写作者邮件合规审计系统 - 后端说明

## 角色与对应

| 角色 | 对应实现 |
|------|----------|
| **写作者 (Writers)** | 安然员工，对应 C++ server 的 `writer_id`（每个员工独立密钥空间） |
| **读者 (Reader)** | 合规审计官，使用本 Web API 进行跨写作者关键字搜索 |
| **云服务器 (Cloud)** | C++ `server`：仅存储加密索引 (EIDX/ESTkn)，执行搜索，无法获知关键字明文 |

## 后端新增/修改文件

- **`config.py`**：统一配置（审计员授权、Epoch、路径），通过环境变量读取。
- **`enron_preprocess.py`**：Enron maildir 数据预处理，NLP 关键词提取，输出 `database/` 与 `database_paths/`（与 Go 版本格式兼容）。
- **`app.py`**：已接入授权、Epoch、搜索耗时；搜索/文档/更新接口均受 `HERMES_ALLOWED_WRITERS` 限制。

## Linux 下 C++ 客户端库（解决「C++ 服务未连接」）

Web 页显示「C++ 服务：未连接」通常是因为 **未成功加载 libhermes_client.so**（不是 server 没启动）。请按以下步骤操作：

1. **编译客户端库**（在 `web_api` 目录）：
   ```bash
   cd web_api
   make
   ```
   生成 `libhermes_client.so`。若报错找不到 `emp-tool/emp-tool.h`，需先安装 [emp-toolkit](https://github.com/emp-toolkit)（与主项目 server/client 相同依赖），或将头文件路径加入编译：
   ```bash
   make EXTRA_INC=-I/path/to/emp-toolkit/include
   ```
   若项目在 `~/Hermes/Hermes` 下且库在上级目录，可指定库路径：`make HERMES_LIB=../lib`。

2. **若 Flask 启动时报错加载 .so 失败**（如 `cannot open shared object file`）：
   - 安装依赖：`sudo apt install libzmq3-dev libpbc-dev libgmp-dev libssl-dev`
   - 若依赖库在项目 `Hermes/lib` 下，启动 Flask 前设置库路径：
     ```bash
     export LD_LIBRARY_PATH=/path/to/Hermes/lib:$LD_LIBRARY_PATH
     python app.py
     ```

3. **启动顺序**：先启动 C++ server，再启动 Flask。若仍显示未连接，在 Web「索引更新」页点击「重试连接」。

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `HERMES_SERVER` | C++ server 地址 | `tcp://127.0.0.1:8888` |
| `HERMES_NUM_WRITERS` | 写作者数量（与 server 一致） | `25` |
| `HERMES_ALLOWED_WRITERS` | 审计员可搜索的 writer_id，逗号分隔，`all` 或不设表示全部 | 全部 |
| `HERMES_EPOCH` | 当前审计阶段（前向安全演示用） | `1` |
| `FLASK_PORT` | Web 服务端口 | `5000` |
| `FLASK_DEBUG` | 是否开启调试 | `False` |

示例：仅允许审计员搜索 writer 0,1,2,3：

```bash
export HERMES_ALLOWED_WRITERS=0,1,2,3
python app.py
```

## 数据预处理（Enron）

使用 Python 预处理（可选 nltk + TF-IDF），输出与 `extract_database.go` 相同格式，供 C++ server 的 `init()` 使用。

```bash
cd Hermes/web_api
pip install -r requirements.txt
# 若使用 nltk，首次运行会下载数据
python enron_preprocess.py --maildir ../maildir --database-dir ../database --database-paths-dir ../database_paths
```

参数说明：

- `--maildir`：maildir 根目录（与 Go 版本一致）。
- `--database-dir` / `--database-paths-dir`：输出目录，建议为 Hermes 下的 `database`、`database_paths`。
- `--max-writers N`：只处理前 N 个写作者。
- `--extractor simple|tfidf`：`simple` 与 Go 逻辑一致；`tfidf` 为每封邮件 TF-IDF top-k。
- `--top-k`：TF-IDF 时每封邮件保留的关键词数。
- `--no-nltk`：不使用 nltk 分词，仅用正则。

预处理完成后，在 Hermes 目录启动 C++ server 并加载同一 `database` 目录即可。

## API 行为说明

- **`GET /api/status`**：增加 `epoch`、`allowed_writers`、`allowed_writers_count`。
- **`GET /api/writers`**：仅返回当前审计员被授权可搜索的写作者列表。
- **`POST /api/search`**：仅在被授权 writer 范围内搜索；返回中增加 `search_time_ms`（毫秒级，用于亚线性检索演示）。
- **`POST /api/document`**：仅允许访问授权 writer 的文档，否则 403。
- **`POST /api/update`**：仅允许对授权 writer 执行更新，否则 403。

## 前向安全（Epoch）演示

通过 `HERMES_EPOCH` 标识当前审计阶段，状态接口返回该值。演示“旧 epoch 无法搜索新邮件”时，可：

1. 使用 Epoch 1 的索引启动 server，审计员搜索可见旧邮件。
2. 切换至 Epoch 2（例如新索引目录或新 server 实例），旧会话/旧密钥不访问新索引，从而体现前向安全。

C++ 端已有 epoch 编码逻辑（如 `encoded_epoch`、epoch 树），本后端仅做配置与展示，便于与前端或演示脚本配合。
