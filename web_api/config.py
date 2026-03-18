"""
多写作者邮件合规审计系统 - 后端配置

角色对应：
- 写作者 (Writers): 安然员工，对应 server 的 writer_id
- 读者 (Reader): 合规审计官，使用本 Web API 进行跨写作者搜索
- 云服务器: C++ server，仅存储加密索引与执行搜索，无法获知关键字明文

配置项通过环境变量读取，便于部署与演示。
"""

import os
from pathlib import Path
from typing import List, Optional

# ---------- 服务连接 ----------
HERMES_SERVER = os.getenv("HERMES_SERVER", "tcp://127.0.0.1:8888")
HERMES_NUM_WRITERS = int(os.getenv("HERMES_NUM_WITERS", os.getenv("HERMES_NUM_WRITERS", "25")))
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"

# ---------- 审计员授权（读者可搜索的写作者集合）----------
# HERMES_ALLOWED_WRITERS: 逗号分隔的 writer_id（0-based），例如 "0,1,2,3"
# 不设置或设为 "all" 表示允许搜索所有写作者
def get_allowed_writers() -> Optional[List[int]]:
    raw = os.getenv("HERMES_ALLOWED_WRITERS", "").strip()
    if not raw or raw.lower() == "all":
        return None  # 表示全部允许
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return None

ALLOWED_WRITERS: Optional[List[int]] = get_allowed_writers()

# ---------- 前向安全 / 纪元（演示用）----------
# 当前审计阶段 Epoch，用于状态展示与演示“旧 epoch 无法搜索新邮件”
HERMES_EPOCH = int(os.getenv("HERMES_EPOCH", "1"))

# ---------- 路径 ----------
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DB_PATHS_DIR = PROJECT_ROOT / "database_paths"
DATABASE_DIR = PROJECT_ROOT / "database"
MAILDIR_DIR = PROJECT_ROOT / "maildir"
