"""
多写作者邮件合规审计系统 - Web 后端

角色：读者（审计员）通过本 API 进行跨写作者关键字搜索，写作者为安然员工，管理员负责推进审计批次 Epoch。
云服务器仅存储加密索引、执行搜索，无法获知关键字明文。
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_cors import CORS
import os
import sys
import time
import base64
import json
import subprocess
import shutil
import threading
import uuid
from datetime import datetime
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from email.parser import Parser
from email.utils import getaddresses
from pathlib import Path
from functools import wraps

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

try:
    import zmq  
except Exception:
    zmq = None

try:
    import hermes_python_client
    from hermes_python_client import HermesClient
except ImportError:
    print("Warning: hermes_python_client not found. Using mock mode.")
    class HermesClient:
        def __init__(self, *args, **kwargs):
            self.server_address = kwargs.get('server_address', 'tcp://127.0.0.1:8888')
            self.num_writers = kwargs.get('num_writers', 25)
        def search(self, keyword, writer_ids=None):
            return {"results": []}
        def update(self, writer_id, keyword, file_id):
            return True

# 统一从 config 读取配置（审计员授权、Epoch、路径等）
try:
    from config import (
        HERMES_SERVER,
        HERMES_NUM_WRITERS,
        FLASK_PORT,
        FLASK_DEBUG,
        get_allowed_writers,
        ALLOWED_WRITERS,
        HERMES_EPOCH,
        BASE_DIR,
        PROJECT_ROOT,
        DB_PATHS_DIR,
        DATABASE_DIR,
    )
except ImportError:
    HERMES_SERVER = os.getenv('HERMES_SERVER', 'tcp://127.0.0.1:8888')
    HERMES_NUM_WRITERS = int(os.getenv('HERMES_NUM_WITERS', os.getenv('HERMES_NUM_WRITERS', '25')))
    FLASK_PORT = int(os.getenv('FLASK_PORT', '5000'))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    ALLOWED_WRITERS = None
    HERMES_EPOCH = 1
    BASE_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = BASE_DIR.parent
    DB_PATHS_DIR = PROJECT_ROOT / "database_paths"
    DATABASE_DIR = PROJECT_ROOT / "database"

    def get_allowed_writers():
        raw = os.getenv("HERMES_ALLOWED_WRITERS", "").strip()
        if not raw or raw.lower() == "all":
            return None
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            return None

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)
app.secret_key = os.getenv("HERMES_WEB_SECRET", "hermes-web-dev-secret-change-me")
_EMAIL_WRITER_MAP_CACHE = None

CLIENT_CONFIG = {
    'server_address': HERMES_SERVER,
    'num_writers': HERMES_NUM_WRITERS,
    'epoch': HERMES_EPOCH,
}
#  全局审计 Epoch 
GLOBAL_EPOCH_FILE = Path(__file__).parent / "global_epoch.txt"
def get_global_epoch() -> int:
    if GLOBAL_EPOCH_FILE.exists():
        try:
            return int(GLOBAL_EPOCH_FILE.read_text().strip())
        except:
            pass
    return HERMES_EPOCH
def set_global_epoch(epoch: int):
    GLOBAL_EPOCH_FILE.write_text(str(epoch))
if not GLOBAL_EPOCH_FILE.exists():
    set_global_epoch(HERMES_EPOCH)
hermes_client = HermesClient(**CLIENT_CONFIG)
if getattr(hermes_client, 'set_database_dir', None):
    hermes_client.set_database_dir(str(DATABASE_DIR.resolve()))

READER_USERNAME = os.getenv("HERMES_READER_USERNAME", "reader")
READER_PASSWORD = os.getenv("HERMES_READER_PASSWORD", "reader123")
WRITER_PASSWORD_PREFIX = os.getenv("HERMES_WRITER_PASSWORD_PREFIX", "writer")
# 管理员账号
ADMIN_USERNAME = os.getenv("HERMES_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("HERMES_ADMIN_PASSWORD", "admin123")



def _get_session_user():
    user = session.get("auth_user")
    if isinstance(user, dict) and user.get("role") in {"reader", "writer","admin"}:
        return user
    return None


def _get_user_accessible_writer_ids():
    user = _get_session_user()
    if not user:
        return []
    if user.get("role") == "reader":
        return get_auditor_writer_ids()
    if user.get("role") == "writer":
        wid = user.get("writer_id")
        if isinstance(wid, int):
            return [wid]
# 管理员可访问所有写者
    if user.get("role") == "admin":
        return list(range(get_server_num_writers()))
    return []


def _require_roles(roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _get_session_user()
            if not user:
                if request.path.startswith("/api/"):
                    return jsonify({"success": False, "error": "Unauthorized. Please login first."}), 401
                return redirect(url_for("login_page"))
            if roles and user.get("role") not in roles:
                if request.path.startswith("/api/"):
                    return jsonify({"success": False, "error": "Forbidden for current role."}), 403
                if user.get("role") == "reader":
                    return redirect(url_for("reader_home"))
                return redirect(url_for("writer_home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator
def _require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _get_session_user()
        if not user or user.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Admin required"}), 403
            return redirect(url_for("login_page"))
        return fn(*args, **kwargs)
    return wrapper

def _get_active_epoch() -> int:
    """
    返回当前会话的审计批次 Epoch（默认使用配置 HERMES_EPOCH）。
    e = session.get("active_epoch", HERMES_EPOCH)
    try:
        e = int(e)
    except Exception:
        e = HERMES_EPOCH
    if e < 1:
        e = 1
    return e
    """
    return get_global_epoch()

def get_server_num_writers() -> int:
    """
    直接向Hermes server发送'G'查询真实writer数量。
    优先使用：
      1. pyzmq 直接问server
      2. HermesClient.get_effective_num_writers()
      3. HermesClient.num_writers
      4. CLIENT_CONFIG['num_writers']
    """
    # 1) pyzmq直接问server
    if zmq is not None:
        try:
            ctx = zmq.Context.instance()
            sock = ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, 1500)
            sock.setsockopt(zmq.SNDTIMEO, 1500)
            sock.connect(CLIENT_CONFIG['server_address'])
            sock.send(b'G')
            rep = sock.recv()
            sock.close()
            if rep and len(rep) >= 4:
                n = int.from_bytes(rep[:4], byteorder='little', signed=True)
                if n > 0:
                    return n
        except Exception:
            pass

    # 2) HermesClient 的有效数量（cpp或cli_fallback内部逻辑）
    try:
        if hasattr(hermes_client, "get_effective_num_writers"):
            n = int(hermes_client.get_effective_num_writers())
            if n > 0:
                return n
    except Exception:
        pass

    # 3) HermesClient.num_writers
    try:
        nw = getattr(hermes_client, "num_writers", None)
        if isinstance(nw, int) and nw > 0:
            return nw
    except Exception:
        pass

    # 4) 配置默认值
    return CLIENT_CONFIG['num_writers']


def get_auditor_writer_ids():
    """返回当前审计员（读者）被授权可搜索的 writer_id 列表（0-based）。"""
    n = get_server_num_writers()
    all_ids = list(range(n))
    if ALLOWED_WRITERS is None:
        return all_ids
    return [i for i in ALLOWED_WRITERS if 0 <= i < n]


def _database_file_path(writer_id: int) -> Path:
    """写者对应的 database 文件路径，与 extract_database.go 一致：userID 从 1 开始。"""
    return DATABASE_DIR / f"{writer_id + 1}.txt"


def _database_paths_file_path(writer_id: int) -> Path:
    """写者对应的 database_paths 文件路径。"""
    return DB_PATHS_DIR / f"{writer_id + 1}.txt"


def sync_database_after_update(writer_id: int, keyword: str, file_id: int) -> tuple[bool, str]:
    """
    索引更新成功后，将 (keyword, file_id) 同步写入 database/(writer_id+1).txt。
    格式：每行 "关键字 文件ID1 文件ID2 ..."，若关键字已存在则追加 file_id，否则新增一行。
    返回 (成功, 错误信息)。
    """
    path = _database_file_path(writer_id)
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{keyword} {file_id}\n", encoding="utf-8")
            return True, ""
        except Exception as e:
            return False, str(e)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines = []
        for line in lines:
            parts = line.strip().split()
            if not parts:
                new_lines.append(line)
                continue
            kw = parts[0]
            ids = parts[1:]
            if kw == keyword:
                if str(file_id) in ids:
                    return True, ""  # 已存在，无需重复
                ids.append(str(file_id))
                new_lines.append(kw + " " + " ".join(ids))
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{keyword} {file_id}")
        path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


def sync_database_paths_after_update(writer_id: int, file_id: int, file_path: str) -> tuple[bool, str]:
    """
    仅当 file_id 尚未出现在 database_paths 中时，追加一行 "file_id file_path"。
    用于新增文档时同步路径映射。返回 (成功, 错误信息)。
    """
    path = _database_paths_file_path(writer_id)
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{file_id} {file_path.strip()}\n", encoding="utf-8")
            return True, ""
        except Exception as e:
            return False, str(e)
    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            parts = line.strip().split(None, 1)  # 最多分两段：file_id, path
            if parts and parts[0] == str(file_id):
                return True, ""  # 已存在，无需重复
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{file_id} {file_path.strip()}\n")
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------- 文档内容更新与按用户重建 keyword->fileID（与 extract_database.go 规则一致）----------
# 停用词表（与 extract_database.go 一致）
_STOPWORDS = frozenset({
    "a", "about", "above", "across", "after", "afterwards", "again", "against", "all", "almost", "alone", "along",
    "already", "also", "although", "always", "am", "among", "amongst", "amoungst", "amount", "an", "and", "another",
    "any", "anyhow", "anyone", "anything", "anyway", "anywhere", "are", "around", "as", "at", "back", "be", "became",
    "because", "become", "becomes", "becoming", "been", "before", "beforehand", "behind", "being", "below", "beside",
    "besides", "between", "beyond", "bill", "both", "bottom", "but", "by", "call", "can", "cannot", "cant", "co", "con",
    "could", "couldnt", "cry", "de", "describe", "detail", "do", "done", "down", "due", "during", "each", "eg", "eight",
    "either", "eleven", "else", "elsewhere", "empty", "enough", "etc", "even", "ever", "every", "everyone", "everything",
    "everywhere", "except", "few", "fifteen", "fify", "fill", "find", "fire", "first", "five", "for", "former", "formerly",
    "forty", "found", "four", "from", "front", "full", "further", "get", "give", "go", "had", "has", "hasnt", "have", "he",
    "hence", "her", "here", "hereafter", "hereby", "herein", "hereupon", "hers", "herself", "him", "himself", "his", "how",
    "however", "hundred", "ie", "if", "in", "inc", "indeed", "interest", "into", "is", "it", "its", "itself", "keep", "last",
    "latter", "latterly", "least", "less", "ltd", "made", "many", "may", "me", "meanwhile", "might", "mill", "mine", "more",
    "moreover", "most", "mostly", "move", "much", "must", "my", "myself", "name", "namely", "neither", "never", "nevertheless",
    "next", "nine", "no", "nobody", "none", "noone", "nor", "not", "nothing", "now", "nowhere", "of", "off", "often", "on",
    "once", "one", "only", "onto", "or", "other", "others", "otherwise", "our", "ours", "ourselves", "out", "over", "own",
    "part", "per", "perhaps", "please", "put", "rather", "re", "same", "see", "seem", "seemed", "seeming", "seems", "serious",
    "several", "she", "should", "show", "side", "since", "sincere", "six", "sixty", "so", "some", "somehow", "someone",
    "something", "sometime", "sometimes", "somewhere", "still", "such", "system", "take", "ten", "than", "that", "the",
    "their", "them", "themselves", "then", "thence", "there", "thereafter", "thereby", "therefore", "therein", "thereupon",
    "these", "they", "thickv", "thin", "third", "this", "those", "though", "three", "through", "throughout", "thru", "thus",
    "to", "together", "too", "top", "toward", "towards", "twelve", "twenty", "two", "un", "under", "until", "up", "upon",
    "us", "very", "via", "was", "we", "well", "were", "what", "whatever", "when", "whence", "whenever", "where", "whereafter",
    "whereas", "whereby", "wherein", "whereupon", "wherever", "whether", "which", "while", "whither", "who", "whoever",
    "whole", "whom", "whose", "why", "will", "with", "within", "without", "would", "yet", "you", "your", "yours",
    "yourself", "yourselves",
})


def _extract_keywords_from_text(text: str):
    """从文本中提取关键词，规则与 extract_database.go 一致：小写、长度 4–20、非停用词、仅字母。"""
    if not text:
        return []
    keywords = []
    for word in text.split():
        w = word.lower()
        if len(w) < 4 or len(w) > 20 or w in _STOPWORDS:
            continue
        if all((c >= 'a' and c <= 'z') or (c >= 'A' and c <= 'Z') for c in w):
            keywords.append(w)
    return keywords


def get_file_path_from_database_paths(writer_id: int, file_id: int):
    """
    从 database_paths/(writer_id+1).txt 中查找 file_id 对应的文件路径。
    返回绝对路径（基于 PROJECT_ROOT 解析），未找到返回 None。
    """
    path_file = _database_paths_file_path(writer_id)
    if not path_file.exists():
        return None
    for line in path_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) >= 2 and parts[0] == str(file_id):
            raw_path = parts[1].strip()
            if raw_path.startswith("./"):
                return (PROJECT_ROOT / raw_path[2:]).resolve()
            if not os.path.isabs(raw_path):
                return (PROJECT_ROOT / raw_path).resolve()
            return Path(raw_path)
    return None


def _read_email_headers(path: Path, max_bytes: int = 65536):
    """只读取邮件头部，不返回正文内容。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except Exception:
        return {}
    header_text = raw.split("\n\n", 1)[0]
    try:
        return Parser().parsestr(header_text)
    except Exception:
        return {}


def _extract_email_addresses(header_value: str):
    if not header_value:
        return []
    addresses = []
    seen = set()
    for _, addr in getaddresses([header_value]):
        addr = (addr or "").strip().lower()
        if addr and "@" in addr and addr not in seen:
            seen.add(addr)
            addresses.append(addr)
    return addresses


def _maildir_folder_from_path(path: Path):
    try:
        rel = path.resolve().relative_to(PROJECT_ROOT.resolve())
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "maildir":
            return parts[1]
    except Exception:
        pass
    return None


def _email_to_writer_map(max_files_per_writer: int = 80):
    """
    粗略建立 email -> writer_id(0-based) 映射。
    优先使用各 mailbox 的 sent 类目录里的 From 地址；这是展示同谋图谱用的元数据辅助，
    不参与 Hermes 密文检索。
    """
    global _EMAIL_WRITER_MAP_CACHE
    if _EMAIL_WRITER_MAP_CACHE is not None:
        return _EMAIL_WRITER_MAP_CACHE

    mapping = {}
    n = get_server_num_writers()
    for writer_id in range(n):
        path_file = _database_paths_file_path(writer_id)
        if not path_file.exists():
            continue
        scanned = 0
        lines = path_file.read_text(encoding="utf-8", errors="replace").splitlines()
        preferred = []
        fallback = []
        for line in lines:
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            raw_path = parts[1].strip()
            if raw_path.startswith("./"):
                abs_path = (PROJECT_ROOT / raw_path[2:]).resolve()
            elif not os.path.isabs(raw_path):
                abs_path = (PROJECT_ROOT / raw_path).resolve()
            else:
                abs_path = Path(raw_path)
            path_text = str(abs_path).lower()
            if "/sent" in path_text or "_sent" in path_text:
                preferred.append(abs_path)
            else:
                fallback.append(abs_path)
        for abs_path in preferred + fallback:
            if scanned >= max_files_per_writer:
                break
            if not abs_path.exists():
                continue
            headers = _read_email_headers(abs_path)
            for addr in _extract_email_addresses(headers.get("From", "")):
                mapping.setdefault(addr, writer_id)
            scanned += 1
    _EMAIL_WRITER_MAP_CACHE = mapping
    return mapping


def _email_metadata_for_file(writer_id: int, file_id: int, email_writer_map: dict):
    path = get_file_path_from_database_paths(writer_id, file_id)
    if path is None or not path.exists():
        return None
    headers = _read_email_headers(path)
    if not headers:
        return None
    from_addrs = _extract_email_addresses(headers.get("From", ""))
    to_addrs = _extract_email_addresses(headers.get("To", ""))
    cc_addrs = _extract_email_addresses(headers.get("Cc", "") or headers.get("X-cc", ""))
    bcc_addrs = _extract_email_addresses(headers.get("Bcc", "") or headers.get("X-bcc", ""))
    recipient_addrs = list(dict.fromkeys(to_addrs + cc_addrs + bcc_addrs))
    sender_writer = email_writer_map.get(from_addrs[0]) if from_addrs else None
    recipient_writers = sorted({
        email_writer_map[addr]
        for addr in recipient_addrs
        if addr in email_writer_map
    })
    folder = _maildir_folder_from_path(path)
    return {
        "writer_id": writer_id + 1,
        "writer_index": writer_id,
        "file_id": file_id,
        "file_key": f"{writer_id + 1}:{file_id}",
        "mailbox_folder": folder,
        "from": from_addrs[0] if from_addrs else "",
        "from_writer_id": (sender_writer + 1) if sender_writer is not None else None,
        "to": to_addrs,
        "cc": cc_addrs,
        "bcc": bcc_addrs,
        "recipient_writer_ids": [wid + 1 for wid in recipient_writers],
        "subject": headers.get("Subject", "") or "",
        "date": headers.get("Date", "") or "",
        "message_id": headers.get("Message-ID", "") or "",
        "in_reply_to": headers.get("In-Reply-To", "") or "",
        "references": headers.get("References", "") or "",
    }



def rebuild_database_for_writer(writer_id: int):
    path_file = _database_paths_file_path(writer_id)
    if not path_file.exists():
        return False, "database_paths 文件不存在"
    keyword_to_ids = {}
    for line in path_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            file_id = int(parts[0])
        except ValueError:
            continue
        raw_path = parts[1].strip()
        if raw_path.startswith("./"):
            abs_path = (PROJECT_ROOT / raw_path[2:]).resolve()
        elif not os.path.isabs(raw_path):
            abs_path = (PROJECT_ROOT / raw_path).resolve()
        else:
            abs_path = Path(raw_path)
        if not abs_path.exists():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # ---------- 应用黑白名单过滤 ----------
        raw_kws = _extract_keywords_from_text(content)
        kws = _apply_dict_filter(writer_id, raw_kws)
        for kw in kws:
            if kw not in keyword_to_ids:
                keyword_to_ids[kw] = []
            keyword_to_ids[kw].append(file_id)

    out_path = _database_file_path(writer_id)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            ids = list(dict.fromkeys(keyword_to_ids[kw]))
            lines.append(kw + " " + " ".join(str(i) for i in ids))
        out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)
      

def _rebuild_database_for_writer_incremental(writer_id: int, file_id: int, new_content: str) -> tuple[bool, str]:
    """
    仅针对「单文件更新」的增量重建：在已有 database 上移除该 file_id 的所有关键字关联，
    再根据 new_content 重新加入该 file_id 的关键字。不读取该写者的其他文件，显著加快更新。
    若当前 database 不存在则返回 (False, "no_database")，调用方应回退到全量重建。
    语义：原有关键字中不再含该 file_id；新内容中的关键字会关联该 file_id；其他文件不变。
    """
    out_path = _database_file_path(writer_id)
    if not out_path.exists():
        return False, "no_database"
    try:
        keyword_to_ids: dict[str, list[int]] = {}
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            kw = parts[0]
            ids = []
            for s in parts[1:]:
                try:
                    ids.append(int(s))
                except ValueError:
                    continue
            keyword_to_ids[kw] = ids
        # 从所有关键字中移除该 file_id，但用 -1 占位以保持其后元素的相对位置（即 count）不改变
        for kw in list(keyword_to_ids.keys()):
            if file_id in keyword_to_ids[kw]:
                keyword_to_ids[kw] = [(-1 if x == file_id else x) for x in keyword_to_ids[kw]]
        # 将 new_content 中的关键字与该 file_id 关联
        for kw in _extract_keywords_from_text(new_content):
            if kw not in keyword_to_ids:
                keyword_to_ids[kw] = []
            if file_id not in keyword_to_ids[kw]:
                keyword_to_ids[kw].append(file_id)
        # 写回：保持顺序，注意不要把多个 -1 去重成一个（所以不能用 dict.fromkeys）
        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            # 去重正常 id，同时保留所有 -1
            seen = set()
            ids = []
            for x in keyword_to_ids[kw]:
                if x == -1:
                    ids.append(x)
                elif x not in seen:
                    seen.add(x)
                    ids.append(x)
            lines.append(kw + " " + " ".join(str(i) for i in ids))
        out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)
def _rebuild_database_for_writer_incremental_with_filter(writer_id: int, file_id: int, filtered_keywords: set) -> tuple[bool, str]:
    """
    增量重建的变体，直接使用传入的已过滤关键词集合，避免内部再次提取。
    """
    out_path = _database_file_path(writer_id)
    if not out_path.exists():
        return False, "no_database"
    try:
        keyword_to_ids = {}
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            kw = parts[0]
            ids = []
            for s in parts[1:]:
                try:
                    ids.append(int(s))
                except ValueError:
                    continue
            keyword_to_ids[kw] = ids

        # 移除旧映射
        for kw in list(keyword_to_ids.keys()):
            if file_id in keyword_to_ids[kw]:
                keyword_to_ids[kw] = [(-1 if x == file_id else x) for x in keyword_to_ids[kw]]

        # 使用过滤后的关键词更新映射
        for kw in filtered_keywords:
            if kw not in keyword_to_ids:
                keyword_to_ids[kw] = []
            if file_id not in keyword_to_ids[kw]:
                keyword_to_ids[kw].append(file_id)

        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            seen = set()
            ids = []
            for x in keyword_to_ids[kw]:
                if x == -1:
                    ids.append(x)
                elif x not in seen:
                    seen.add(x)
                    ids.append(x)
            lines.append(kw + " " + " ".join(str(i) for i in ids))
        out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)
# 批量导入任务存储（ZIP/目录）
batch_tasks = {}
batch_tasks_lock = threading.Lock()
batch_executor = ThreadPoolExecutor(max_workers=2)

# 管理员 Epoch 推进任务存储
admin_epoch_tasks = {}
admin_epoch_tasks_lock = threading.Lock()
admin_epoch_executor = ThreadPoolExecutor(max_workers=1)
#管理员路由
@app.route('/admin')
@_require_admin
def admin_home():
    return render_template("admin.html", user=_get_session_user())

# 全局 Epoch 管理 
@app.route('/api/admin/epoch/advance', methods=['POST'])
@_require_admin
def advance_global_epoch():
    data = request.get_json() or {}
    target_epoch = data.get('target_epoch')

    old = get_global_epoch()

    if target_epoch is not None:
        try:
            target_epoch = int(target_epoch)
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "target_epoch 必须是整数"}), 400
        if target_epoch < 1 or target_epoch > 1024:
            return jsonify({"success": False, "error": "target_epoch 必须在 1～1024"}), 400
        new_epoch = target_epoch
    else:
        new_epoch = old + 1

    if new_epoch <= old:
        set_global_epoch(new_epoch)
        return jsonify({"success": True, "old_epoch": old, "new_epoch": new_epoch,
                        "message": f"Epoch 已设置为 {new_epoch}（未执行清理，因为 epoch 未增加）"})

   
    task_id = str(uuid.uuid4())
    with admin_epoch_tasks_lock:
        admin_epoch_tasks[task_id] = {
            'status': 'pending',
            'old_epoch': old,
            'new_epoch': new_epoch,
            'progress': 0,
            'total_writers': 0,
            'synced_writers': 0,
            'message': ''
        }

    # 提交后台执行（传递 task_id, old, new）
    admin_epoch_executor.submit(_run_epoch_advance, task_id, old, new_epoch)

    return jsonify({
        'success': True,
        'task_id': task_id,
        'message': f'审计 Epoch 推进任务已创建（{old} → {new_epoch}），后台执行中。可通过 /api/admin/epoch/task/{task_id} 查询进度。'
    })

def _run_epoch_advance(task_id, old_epoch, new_epoch):
    try:
        with admin_epoch_tasks_lock:
            admin_epoch_tasks[task_id]['status'] = 'processing'

        num_writers = get_server_num_writers()
        with admin_epoch_tasks_lock:
            admin_epoch_tasks[task_id]['total_writers'] = num_writers

        cleaned_count = 0
        for w_id in range(num_writers):
            if _cleanup_expired_keywords_for_writer(w_id, new_epoch):
                cleaned_count += 1
            with admin_epoch_tasks_lock:
                admin_epoch_tasks[task_id]['synced_writers'] = cleaned_count   # 仅用于进度展示
                admin_epoch_tasks[task_id]['progress'] = int((w_id + 1) / num_writers * 100)

        # 更新全局 Epoch
        set_global_epoch(new_epoch)

        # 全局索引重载，使过期关键词立即对读者不可见
        reload_ok = True
        if getattr(hermes_client, '_initialized', False):
            try:
                reload_ok = hermes_client.reload_index_from_database()
            except Exception:
                reload_ok = False

        msg = f'全局 Epoch 已从 {old_epoch} 推进至 {new_epoch}，清理了 {cleaned_count} 个写者的过期关键词。'

        msg += ' 索引已同步。'

        with admin_epoch_tasks_lock:
            admin_epoch_tasks[task_id]['status'] = 'completed'
            admin_epoch_tasks[task_id]['message'] = msg
    except Exception as e:
        with admin_epoch_tasks_lock:
            admin_epoch_tasks[task_id]['status'] = 'failed'
            admin_epoch_tasks[task_id]['message'] = str(e)
@app.route('/api/admin/epoch/task/<task_id>', methods=['GET'])
@_require_admin
def get_epoch_task_status(task_id):
    with admin_epoch_tasks_lock:
        task = admin_epoch_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    return jsonify({'success': True, 'task': task})
    
@app.route('/')
def index():
    user = _get_session_user()
    if not user:
        return redirect(url_for("login_page"))
    if user.get("role") == "reader":
        return redirect(url_for("reader_home"))
    if user.get("role") == "admin":
        return redirect(url_for("admin_home"))
    return redirect(url_for("writer_home"))


@app.route('/login', methods=['GET'])
def login_page():
    user = _get_session_user()
    if user:
        if user.get("role") == "reader":
            return redirect(url_for("reader_home"))
        if user.get("role") == "admin":
            return redirect(url_for("admin_home"))
        return redirect(url_for("writer_home"))
    return render_template('login.html')


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    try:
        data = request.get_json() or {}
        role = str(data.get("role", "")).strip().lower()
        password = str(data.get("password", ""))
        num_writers = get_server_num_writers()
        if role == "admin":
            username = str(data.get("username", "")).strip()
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session["auth_user"] = {"role": "admin", "username": username}
                return jsonify({"success": True, "role": "admin", "redirect": url_for("admin_home")})
            return jsonify({"success": False, "error": "管理员账号或密码错误"}), 401
        if role == "reader":
            username = str(data.get("username", "")).strip()
            if username == READER_USERNAME and password == READER_PASSWORD:
                session["auth_user"] = {
                    "role": "reader",
                    "username": username,
                }
                return jsonify({
                    "success": True,
                    "role": "reader",
                    "redirect": url_for("reader_home"),
                })
            return jsonify({"success": False, "error": "读者账号或密码错误"}), 401

        if role == "writer":
            writer_id_raw = data.get("writer_id")
            try:
                writer_id = int(writer_id_raw)
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "writer_id 必须是整数"}), 400

            if writer_id < 0 or writer_id >= num_writers:
                return jsonify({"success": False, "error": f"writer_id 必须在 0 到 {num_writers - 1} 之间"}), 400

            expected_password = f"{WRITER_PASSWORD_PREFIX}{writer_id + 1}"
            if password != expected_password:
                return jsonify({"success": False, "error": "写者密码错误"}), 401

            session["auth_user"] = {
                "role": "writer",
                "writer_id": writer_id,
                "username": f"writer{writer_id + 1}",
            }
            return jsonify({
                "success": True,
                "role": "writer",
                "redirect": url_for("writer_home"),
            })

        return jsonify({"success": False, "error": "role 必须是 reader 或 writer"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"登录失败: {str(e)}"}), 500


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.pop("auth_user", None)
    return jsonify({"success": True, "redirect": url_for("login_page")})


@app.route('/reader')
@_require_roles({"reader"})
def reader_home():
    user = _get_session_user()
    return render_template("reader.html", user=user)


@app.route('/writer')
@_require_roles({"writer"})
def writer_home():
    user = _get_session_user()
    return render_template("writer.html", user=user)


@app.route('/api/status', methods=['GET'])
@_require_roles({"reader", "writer"})
def status():
    """获取系统状态（含 Epoch、审计员授权信息）"""
    mode = "unknown"
    num_writers = get_server_num_writers()
    try:
        mode = "cpp" if getattr(hermes_client, "_initialized", False) else "cli_fallback"
    except Exception:
        mode = "unknown"
    allowed = _get_user_accessible_writer_ids()
    return jsonify({
        'status': 'online',
        'server_address': CLIENT_CONFIG['server_address'],
        'num_writers': num_writers,
        'search_mode': mode,
        'epoch': get_global_epoch(), # 'epoch': _get_active_epoch(),
        'default_epoch': HERMES_EPOCH,
        'allowed_writers': allowed,
        'allowed_writers_count': len(allowed),
    })


@app.route('/api/audit-batch', methods=['GET'])
@_require_roles({"reader"})
def get_audit_batch():
    return jsonify({
        'success': True,
        'active_epoch': _get_active_epoch(),
        'default_epoch': HERMES_EPOCH,
    })


@app.route('/api/audit-batch', methods=['POST'])
@_require_roles({"reader"})
def set_audit_batch():
    try:
        data = request.get_json() or {}
        epoch = int(data.get('epoch'))
        if epoch < 1 or epoch > 1024:
            return jsonify({'success': False, 'error': 'epoch 必须在 1..1024 范围内'}), 400
        session['active_epoch'] = epoch
        return jsonify({
            'success': True,
            'active_epoch': epoch,
            'message': f'已切换到审计批次 Epoch={epoch}。为符合前向隐私，需在该批次重新执行检索。',
        })
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'epoch 必须是整数'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/search', methods=['POST'])
@_require_roles({"reader"})
def search():
    """
    搜索API
    
    请求格式:
    {
        "keyword": "university",
        "writer_ids": [0, 1, 2]  # 可选，如果不提供则搜索所有写入者
    }
    
    返回格式:
    {
        "success": true,
        "results": [
            {"writer_id": 1, "file_ids": [1, 2, 3]},
            {"writer_id": 2, "file_ids": [5, 6]}
        ]
    }
    """
    try:
        data = request.get_json() or {}
        
        if not data or 'keyword' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing required field: keyword'
            }), 400
        
        keyword = data['keyword'].strip()
        if not keyword:
            return jsonify({
                'success': False,
                'error': 'Keyword cannot be empty'
            }), 400
        
        writer_ids = data.get('writer_ids')
        if writer_ids is not None and not isinstance(writer_ids, list):
            return jsonify({
                'success': False,
                'error': 'writer_ids must be a list'
            }), 400

        allowed = _get_user_accessible_writer_ids()
        if writer_ids is None:
            writer_ids = allowed
        else:
            writer_ids = [w for w in writer_ids if w in set(allowed)]

        t0 = time.perf_counter()
        active_epoch = _get_active_epoch()
        # 在子进程中执行搜索（加载 C++ 库），崩溃时仅 worker 退出，主进程返回 500
        worker_path = BASE_DIR / "run_search_worker.py"
        result = None
        if worker_path.exists():
            try:
                proc = subprocess.run(
                    [sys.executable, str(worker_path)],
                    input=json.dumps({"keyword": keyword, "writer_ids": writer_ids, "epoch": active_epoch}),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(BASE_DIR),
                )
                if proc.returncode == 0 and proc.stdout:
                    raw_stdout = proc.stdout.strip()
                    try:
                        result = json.loads(raw_stdout)
                    except json.JSONDecodeError:
                        # stdout 可能混入其他输出（如 client 初始化成功提示），取第一个 '{' 起的 JSON
                        start = raw_stdout.find('{')
                        if start >= 0:
                            try:
                                result = json.loads(raw_stdout[start:])
                            except json.JSONDecodeError:
                                result = {"error": f"Worker returned invalid JSON. stdout: {raw_stdout[:200]!r}"}
                        else:
                            result = {"error": f"Worker stdout had no JSON. stdout: {raw_stdout[:200]!r}"}
                else:
                    err = (proc.stderr or proc.stdout or "").strip()
                    try:
                        err_obj = json.loads(err) if err else {}
                        raw = err_obj.get("error", err or "Search worker failed")
                        if "munmap_chunk" in raw or "invalid pointer" in raw:
                            raw = "检索服务内部错误。请重新编译 web_api (make) 并重启 C++ server 与 Flask 后重试。"
                        result = {"error": raw}
                    except json.JSONDecodeError:
                        raw = err or "Search worker failed (check C++ server is running on tcp://127.0.0.1:8888)"
                        if "munmap_chunk" in raw or "invalid pointer" in raw:
                            raw = "检索服务内部错误。请重新编译 web_api (make) 并重启 C++ server 与 Flask 后重试。"
                        result = {"error": raw}
            except subprocess.TimeoutExpired:
                result = {"error": "Search timed out (60s). Ensure C++ server is running (tcp://127.0.0.1:8888)."}
            except Exception as e:
                result = {"error": f"Search subprocess error: {str(e)}"}
        if result is None:
            result = hermes_client.search(keyword, writer_ids)
        search_time_ms = round((time.perf_counter() - t0) * 1000, 2)

        if result and result.get('error'):
            return jsonify({
                'success': False,
                'error': result['error'],
                'results': [],
            }), 500

        results = (result or {}).get('results', [])
        # 【TTL 过滤】用本地 database 验证 C++ 返回的命中是否仍有效
        # 避免 C++ 索引与本地 database 不一致导致过期数据泄露
        filtered_results = []
        for r in results:
            wid = r.get('writer_id') - 1  # C++ 返回 1-based，转 0-based
            db_path = _database_file_path(wid)
            valid_ids = set()
            
            if db_path.exists():
                for line in db_path.read_text(encoding='utf-8', errors='replace').splitlines():
                    parts = line.strip().split()
                    if len(parts) < 2:
                        continue
                    kw = parts[0]
                    if kw.lower() == keyword.lower():
                        for s in parts[1:]:
                            try:
                                fid = int(s)
                                if fid > 0:
                                    valid_ids.add(fid)
                            except ValueError:
                                continue
            
            kept_ids = [fid for fid in r.get('file_ids', []) if fid in valid_ids]
            if kept_ids:
                filtered_results.append({
                    'writer_id': r['writer_id'],
                    'file_ids': kept_ids
                })
        
        return jsonify({
            'success': True,
            'keyword': keyword,
            'results': filtered_results, 
            'search_time_ms': search_time_ms,
            'epoch': active_epoch,
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Search failed: {str(e)}'
        }), 500


@app.route('/api/update', methods=['POST'])
@_require_roles({"writer"})
def update():
    try:
        data = request.get_json()
        
        required_fields = ['writer_id', 'keyword', 'file_id']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        writer_id = int(data['writer_id'])
        keyword = data['keyword'].strip()
        file_id = int(data['file_id'])
        file_path = data.get('file_path', "").strip()
        
        allowed = _get_user_accessible_writer_ids()
        if writer_id not in allowed:
            return jsonify({
                'success': False,
                'error': f'writer_id={writer_id} not in allowed writers for this auditor'
            }), 403
        if writer_id < 0 or writer_id >= CLIENT_CONFIG['num_writers']:
            return jsonify({
                'success': False,
                'error': f'writer_id must be between 0 and {CLIENT_CONFIG["num_writers"]-1}'
            }), 400

        if not keyword:
            return jsonify({
                'success': False,
                'error': 'Keyword cannot be empty'
            }), 400

        # 黑名单拦截 
        # 如果关键词在黑名单中，则拒绝添加
        blacklist = _load_list(_get_blacklist_file(writer_id))
        if keyword.lower() in [w.lower() for w in blacklist]:
            return jsonify({
                'success': False,
                'error': f'关键词 "{keyword}" 当前在黑名单中，无法添加到索引。'
            }), 400

        # 执行索引更新（C++ 客户端发往 Hermes 服务器）
        success = hermes_client.update(writer_id, keyword, file_id)
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'Update operation failed'
            }), 500

        # 同步写入 database：关键字 -> 文件 ID
        db_ok, db_err = sync_database_after_update(writer_id, keyword, file_id)
        if not db_ok:
            return jsonify({
                'success': True,
                'message': f'Index updated but database sync failed: {db_err}',
                'database_synced': False,
                'database_paths_synced': False,
            }), 200

        # 若提供 file_path（新文档），同步写入 database_paths
        paths_synced = False
        if file_path:
            paths_ok, _ = sync_database_paths_after_update(writer_id, file_id, file_path)
            paths_synced = paths_ok

        index_updated_on_server = getattr(hermes_client, '_initialized', False)
        resp = {
            'success': True,
            'message': f'Successfully updated: keyword="{keyword}", file_id={file_id}, writer_id={writer_id}',
            'database_synced': True,
            'database_paths_synced': paths_synced,
            'index_updated_on_server': index_updated_on_server,
        }
        if not index_updated_on_server:
            resp['reload_hint'] = 'database 已同步；当前未连接 C++ 服务，检索不会显示本次更新。请点击「从 database 重新加载索引」或重启 C++ server 后再检索。'
        return jsonify(resp)
            
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': f'Invalid parameter: {str(e)}'
        }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Update failed: {str(e)}'
        }), 500

@app.route('/api/document-content', methods=['GET'])
@_require_roles({"writer"})
def document_content():
    """
    根据 database_paths 获取指定用户、文件ID 对应的原文路径与内容（用于「文档内容更新」加载原文）。
    参数: writer_id (0-based), file_id
    """
    try:
        writer_id = request.args.get('writer_id', type=int)
        file_id = request.args.get('file_id', type=int)
        if writer_id is None or file_id is None:
            return jsonify({'success': False, 'error': 'Missing writer_id or file_id'}), 400
        allowed = _get_user_accessible_writer_ids()
        if writer_id not in allowed:
            return jsonify({'success': False, 'error': f'writer_id={writer_id} not allowed'}), 403
        path = get_file_path_from_database_paths(writer_id, file_id)
        if path is None:
            return jsonify({'success': False, 'error': f'未在 database_paths 中找到 writer_id={writer_id} file_id={file_id}'}), 404
        if not path.exists():
            return jsonify({'success': False, 'error': f'文件不存在: {path}'}), 404
        content = path.read_text(encoding='utf-8', errors='replace')
        return jsonify({
            'success': True,
            'path': str(path),
            'content': content,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/update-document', methods=['POST'])
@_require_roles({"writer"})
def update_document():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Missing JSON body'}), 400
        writer_id = data.get('writer_id')
        file_id = data.get('file_id')
        new_content = data.get('new_content')
        if writer_id is None or file_id is None:
            return jsonify({'success': False, 'error': 'Missing writer_id or file_id'}), 400
        writer_id = int(writer_id)
        file_id = int(file_id)
        new_content = str(new_content) if new_content is not None else ''
        allowed = _get_user_accessible_writer_ids()
        if writer_id not in allowed:
            return jsonify({'success': False, 'error': f'writer_id={writer_id} not allowed'}), 403
        path = get_file_path_from_database_paths(writer_id, file_id)
        if path is None:
            return jsonify({'success': False, 'error': '未在 database_paths 中找到该文件路径'}), 404
        if not path.exists():
            return jsonify({'success': False, 'error': f'文件不存在: {path}'}), 404

        # 1. 读取旧 database 用于后续增量同步
        db_path = _database_file_path(writer_id)
        keyword_to_ids_old = {}
        if db_path.exists():
            for line in db_path.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                kw = parts[0]
                ids = []
                for s in parts[1:]:
                    try:
                        ids.append(int(s))
                    except ValueError:
                        continue
                keyword_to_ids_old[kw] = ids

        old_keywords = {kw for kw, ids in keyword_to_ids_old.items() if file_id in ids}

        raw_new_keywords = set(_extract_keywords_from_text(new_content))
        new_keywords = set(_apply_dict_filter(writer_id, list(raw_new_keywords)))

        # 2. 覆盖物理文件
        path.write_text(new_content, encoding='utf-8')

        # 3. 本地 database 增量重建（传入过滤后的关键词集合，避免函数内部再次提取）
        ok, err = _rebuild_database_for_writer_incremental_with_filter(writer_id, file_id, new_keywords)
        if not ok:
            # 回退到全量重建（全量重建中也需要应用过滤）
            ok, err = rebuild_database_for_writer(writer_id)
        if not ok:
            return jsonify({
                'success': False,
                'error': f'文件已覆盖，但重建 database 失败: {err}',
            }), 500

        # 4. 同步到服务端（逻辑保持不变，但使用过滤后的 new_keywords）
        server_updated = 0
        if getattr(hermes_client, '_initialized', False):
            if keyword_to_ids_old and db_path.exists():
                to_del_kw = list(old_keywords - new_keywords)
                if to_del_kw and getattr(hermes_client, 'delete_updates', None):
                    counts_del, file_ids_prev_del = [], []
                    for kw in to_del_kw:
                        ids = keyword_to_ids_old.get(kw, [])
                        try:
                            idx = ids.index(file_id)
                        except ValueError:
                            continue
                        counts_del.append(idx)
                        file_ids_prev_del.append(ids[idx - 1] if idx > 0 else 0)
                    if len(counts_del) == len(to_del_kw):
                        hermes_client.delete_updates(writer_id, to_del_kw, counts_del, file_ids_prev_del)
                if getattr(hermes_client, 'load_update_state', None):
                    hermes_client.load_update_state(writer_id)
                to_add_kw = list(new_keywords - old_keywords)
                if to_add_kw:
                    if hermes_client.batch_update(writer_id, to_add_kw, [file_id] * len(to_add_kw)):
                        server_updated = len(to_add_kw)
            else:
                if getattr(hermes_client, 'clear_writer', None) and hermes_client.clear_writer(writer_id):
                    getattr(hermes_client, 'reset_update_state', lambda _: None)(writer_id)
                    if db_path.exists():
                        keywords_list, file_ids_list = [], []
                        for line in db_path.read_text(encoding='utf-8', errors='replace').splitlines():
                            parts = line.strip().split()
                            if len(parts) < 2:
                                continue
                            kw = parts[0]
                            for fid_str in parts[1:]:
                                try:
                                    keywords_list.append(kw)
                                    file_ids_list.append(int(fid_str))
                                except ValueError:
                                    continue
                        if keywords_list and hermes_client.batch_update(writer_id, keywords_list, file_ids_list):
                            server_updated = len(keywords_list)

        return jsonify({
            'success': True,
            'message': f'已更新文件并重建索引（已应用黑白名单）。' + (
                f'已同步到服务端。' if server_updated else '未连接 C++ 服务，请手动重新加载索引。'
            ),
            'index_updated_on_server': server_updated > 0,
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': f'Invalid parameter: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/client-status', methods=['GET'])
@_require_roles({"writer"})
def client_status():
    """返回当前是否已连接 C++ 服务（用于前端显示与重试提示）。"""
    connected = getattr(hermes_client, '_initialized', False)
    load_error = None
    if not connected:
        try:
            load_error = getattr(sys.modules.get('hermes_python_client'), '_hermes_lib_load_error', None)
        except Exception:
            pass
    return jsonify({
        'success': True,
        'connected': connected,
        'message': '已连接 C++ server' if connected else '未连接 C++ server，请点击「重试连接」或按下方说明检查',
        'library_load_error': load_error,
    })


@app.route('/api/reinit-client', methods=['POST'])
@_require_roles({"writer"})
def reinit_client():
    """
    重新尝试连接 C++ server（适用于 server 先启动、app 后启动导致初始化未连上的情况）。
    """
    try:
        ok, msg = hermes_client.reinit()
        if ok:
            if getattr(hermes_client, 'set_database_dir', None):
                hermes_client.set_database_dir(str(DATABASE_DIR.resolve()))
            return jsonify({'success': True, 'message': msg, 'connected': True})
        return jsonify({'success': False, 'error': msg, 'connected': False}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'connected': False}), 500


@app.route('/api/reload-index', methods=['POST'])
@_require_roles({"writer"})
def reload_index():
    """
    通知 C++ 服务器从 database 文件重新加载索引，使检索反映已更新的 database/*.txt。
    仅当 Web 已连接 C++ 客户端且 server 支持 'I' 消息时有效。
    """
    try:
        if not getattr(hermes_client, '_initialized', False):
            return jsonify({
                'success': False,
                'error': '未连接 C++ 服务，无法发送重新加载请求。请先点击「重试连接」或刷新页面以初始化客户端。',
                'need_reinit': True,
            }), 400
        ok = hermes_client.reload_index_from_database()
        if ok:
            return jsonify({
                'success': True,
                'message': '索引已从 database 重新加载，检索将反映最新 database 文件。',
            })
        return jsonify({
            'success': False,
            'error': '重新加载失败（请确认 C++ server 已重新编译并支持 reload）',
        }), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/writers', methods=['GET'])
@_require_roles({"reader", "writer"})
def get_writers():
    """获取当前审计员可搜索的写入者列表（受 HERMES_ALLOWED_WRITERS 限制）"""
    allowed = _get_user_accessible_writer_ids()
    return jsonify({
        'success': True,
        'writers': [
            {'id': i, 'name': f'Writer {i+1}'}
            for i in allowed
        ]
    })
@app.route('/api/writer/keywords', methods=['GET'])
@_require_roles({"writer"})
def writer_keywords():
    """
    获取当前写者的所有关键字及关联文件列表
    返回: [{keyword: "xxx", file_ids: [1,2,3], file_count: 3}, ...]
    """
    try:
        user = _get_session_user()
        writer_id = user.get("writer_id")
        
        db_path = _database_file_path(writer_id)
        if not db_path.exists():
            return jsonify({'success': True, 'keywords': [], 'total_keywords': 0})
        
        keywords = []
        for line in db_path.read_text(encoding="utf-8", errors='replace').splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            kw = parts[0]
            ids = []
            for s in parts[1:]:
                try:
                    fid = int(s)
                    if fid > 0:  # 排除占位符 -1
                        ids.append(fid)
                except ValueError:
                    continue
            if ids:
                keywords.append({
                    'keyword': kw,
                    'file_ids': ids,
                    'file_count': len(ids)
                })
        
        # 按文件数量降序排序
        keywords.sort(key=lambda x: x['file_count'], reverse=True)
        
        return jsonify({
            'success': True,
            'writer_id': writer_id,
            'keywords': keywords,
            'total_keywords': len(keywords),
            'total_files': len(set(fid for k in keywords for fid in k['file_ids']))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/writer/files', methods=['GET'])
@_require_roles({"writer"})
def writer_files():
    """
    获取当前写者的所有文件列表（带关键字聚合）
    支持按关键字过滤: ?keyword=xxx
    """
    try:
        user = _get_session_user()
        writer_id = user.get("writer_id")
        filter_keyword = request.args.get('keyword', '').strip().lower()
        
        path_file = _database_paths_file_path(writer_id)
        if not path_file.exists():
            return jsonify({'success': True, 'files': [], 'total': 0})
        
        # 构建 file_id -> keywords 映射
        file_to_keywords = {}
        db_path = _database_file_path(writer_id)
        if db_path.exists():
            for line in db_path.read_text(encoding="utf-8", errors='replace').splitlines():
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                kw = parts[0]
                if filter_keyword and filter_keyword not in kw.lower():
                    continue
                for s in parts[1:]:
                    try:
                        fid = int(s)
                        if fid > 0:
                            if fid not in file_to_keywords:
                                file_to_keywords[fid] = []
                            file_to_keywords[fid].append(kw)
                    except ValueError:
                        continue
        
        files = []
        for line in path_file.read_text(encoding="utf-8", errors='replace').splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            try:
                file_id = int(parts[0])
                file_path = parts[1]
                
                # 如果有关键字过滤且该文件无匹配关键字，则跳过
                if filter_keyword and file_id not in file_to_keywords:
                    continue
                
                files.append({
                    'file_id': file_id,
                    'path': file_path,
                    'keywords': file_to_keywords.get(file_id, []),
                    'keyword_count': len(file_to_keywords.get(file_id, []))
                })
            except ValueError:
                continue
        
        # 按文件ID排序
        files.sort(key=lambda x: x['file_id'])
        
        return jsonify({
            'success': True,
            'writer_id': writer_id,
            'files': files,
            'total': len(files),
            'filter': filter_keyword if filter_keyword else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route('/api/writer/keyword', methods=['DELETE'])
@_require_roles({"writer"})
def delete_keyword():
    """
    删除指定关键字（移除该关键字与所有文件的关联）
    请求: {"keyword": "xxx"}
    """
    try:
        data = request.get_json()
        if not data or 'keyword' not in data:
            return jsonify({'success': False, 'error': 'Missing keyword'}), 400
        
        keyword = data['keyword'].strip()
        if not keyword:
            return jsonify({'success': False, 'error': 'Empty keyword'}), 400
        
        user = _get_session_user()
        writer_id = user.get("writer_id")
        
        db_path = _database_file_path(writer_id)
        if not db_path.exists():
            return jsonify({'success': False, 'error': 'Database not found'}), 404
        
     
        lines = db_path.read_text(encoding="utf-8", errors='replace').splitlines()
        new_lines = []
        keyword_found = False
        
        for line in lines:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            kw = parts[0]
            if kw == keyword:
                keyword_found = True
               
                continue
            new_lines.append(line)
        
        if not keyword_found:
            return jsonify({'success': False, 'error': f'Keyword "{keyword}" not found'}), 404
      
        db_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
        
        return jsonify({
            'success': True,
            'message': f'Keyword "{keyword}" deleted successfully',
            'warning': '服务端索引未同步，请点击「重新加载索引」或重启服务'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/writer/search-own', methods=['POST'])
@_require_roles({"writer"})
def writer_search_own():
    """
    写者搜索自己的关键字（模糊匹配）
    请求: {"keyword_query": "sec"} 
    返回匹配的关键字列表及关联文件
    """
    try:
        data = request.get_json()
        if not data or 'keyword_query' not in data:
            return jsonify({'success': False, 'error': 'Missing keyword_query'}), 400
        
        query = data['keyword_query'].strip().lower()
        if not query:
            return jsonify({'success': False, 'error': 'Empty query'}), 400
        
        user = _get_session_user()
        writer_id = user.get("writer_id")
        
        db_path = _database_file_path(writer_id)
        if not db_path.exists():
            return jsonify({'success': True, 'results': [], 'query': query})
        
        results = []
        for line in db_path.read_text(encoding="utf-8", errors='replace').splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            kw = parts[0]
            if query in kw.lower():
                ids = []
                for s in parts[1:]:
                    try:
                        fid = int(s)
                        if fid > 0:          # 过滤占位符 -1
                            ids.append(fid)
                    except ValueError:
                        continue
                if ids:
                    results.append({
                        'keyword': kw,
                        'file_ids': ids,
                        'match_type': 'exact' if kw.lower() == query else 'partial'
                    })
        
        # 精确匹配优先
        results.sort(key=lambda x: (0 if x['match_type'] == 'exact' else 1, x['keyword']))
        
        return jsonify({
            'success': True,
            'query': query,
            'results': results,
            'total_matches': len(results)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/document', methods=['POST'])
@_require_roles({"reader"})
def get_document():
    """
    读者获取邮件定位信息：仅返回加密内容或密文占位，不返回明文。
    
    请求格式:
    {
        "writer_id": 0,
        "file_id": 1,
        "decrypt": false   // 可选；读者端即使传 true 也会被拒绝
    }
    
    返回:
    { "success": true, "encrypted": true, "content": "base64密文", "iv": "base64", "size": N }
    或占位: { "success": true, "encrypted": true, "placeholder": true, "message": "..." }
    """
    try:
        data = request.get_json() or {}
        
        required_fields = ['writer_id', 'file_id']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        writer_id = int(data['writer_id'])
        file_id = int(data['file_id'])
        decrypt = bool(data.get('decrypt', False))

        allowed = _get_user_accessible_writer_ids()
        if writer_id not in allowed:
            return jsonify({
                'success': False,
                'error': f'writer_id={writer_id} not in allowed writers for this auditor'
            }), 403

        num_writers = get_server_num_writers()
        if writer_id < 0 or writer_id >= num_writers:
            return jsonify({
                'success': False,
                'error': f'writer_id must be between 0 and {num_writers-1}'
            }), 400

        if decrypt:
            return jsonify({
                'success': False,
                'error': '读者无权解密或查看邮件明文。读者端仅返回文件ID和加密存储信息。'
            }), 403

        # 仅返回加密内容（密文 + IV），不解密
        enc = hermes_client.get_encrypted_document(writer_id, file_id)
        if enc:
            ciphertext, iv = enc
            return jsonify({
                'success': True,
                'encrypted': True,
                'content': base64.b64encode(ciphertext).decode('ascii'),
                'iv': base64.b64encode(iv).decode('ascii'),
                'size': len(ciphertext),
            })
        return jsonify({
            'success': True,
            'encrypted': True,
            'placeholder': True,
            'message': '未找到加密存储；读者端仅保留该文件ID作为检索命中线索，不提供原文查看。',
        })
        
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': f'Invalid parameter: {str(e)}'
        }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get document: {str(e)}'
        }), 500


@app.route('/api/email-metadata/batch', methods=['POST'])
@_require_roles({"reader"})
def email_metadata_batch():
    """
    为审计图谱批量返回命中文件的邮件头元数据。
    只读取 From/To/Cc/Bcc/Subject/Date/Message-ID 等头部，不返回邮件正文。
    请求: {"files": [{"writer_id": 0, "file_id": 123}, ...]}
    writer_id 使用后端 0-based 编号。
    """
    try:
        data = request.get_json() or {}
        files = data.get("files", [])
        if not isinstance(files, list):
            return jsonify({"success": False, "error": "files must be a list"}), 400
        if len(files) > 300:
            files = files[:300]

        allowed = set(_get_user_accessible_writer_ids())
        email_writer_map = _email_to_writer_map()
        results = []
        seen = set()
        for item in files:
            try:
                writer_id = int(item.get("writer_id"))
                file_id = int(item.get("file_id"))
            except Exception:
                continue
            if writer_id not in allowed or file_id <= 0:
                continue
            key = (writer_id, file_id)
            if key in seen:
                continue
            seen.add(key)
            meta = _email_metadata_for_file(writer_id, file_id, email_writer_map)
            if meta:
                results.append(meta)

        return jsonify({
            "success": True,
            "metadata": results,
            "count": len(results),
            "note": "metadata contains headers only; message body is not returned",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# 写者新建文件 
@app.route('/api/writer/create-file', methods=['POST'])
@_require_roles({"writer"})
def writer_create_file():
    try:
        data = request.get_json()
        if not data or 'content' not in data:
            return jsonify({'success': False, 'error': 'Missing content'}), 400

        user = _get_session_user()
        writer_id = user.get("writer_id")
        content = data['content']
        raw_path = data.get('path', '').strip()

        # 1. 确定新文件的 file_id
        paths_file = _database_paths_file_path(writer_id)
        existing_ids = set()
        if paths_file.exists():
            for line in paths_file.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.strip().split(None, 1)
                if parts:
                    try:
                        existing_ids.add(int(parts[0]))
                    except ValueError:
                        continue
        new_file_id = 1
        while new_file_id in existing_ids:
            new_file_id += 1

        # 2. 确定文件存储路径（安全限制）
        if not raw_path:
            relative_path = f"maildir/writer{writer_id + 1}/doc_{new_file_id}.txt"
        else:
            relative_path = raw_path
        safe_path = Path(PROJECT_ROOT) / relative_path
        safe_path = safe_path.resolve()
        if not str(safe_path).startswith(str(PROJECT_ROOT.resolve())):
            return jsonify({'success': False, 'error': '路径非法，必须在项目根目录内'}), 400
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding='utf-8')

        # 3. 写入 database_paths
        with open(paths_file, 'a', encoding='utf-8') as f:
            f.write(f"{new_file_id} {relative_path}\n")

        raw_keywords = list(set(_extract_keywords_from_text(content)))
        keywords = _apply_dict_filter(writer_id, raw_keywords)

        # 4. 更新 database 索引
        db_path = _database_file_path(writer_id)
        keyword_to_ids = {}
        if db_path.exists():
            for line in db_path.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                kw = parts[0]
                ids = []
                for s in parts[1:]:
                    try:
                        fid = int(s)
                        if fid > 0:
                            ids.append(fid)
                    except ValueError:
                        continue
                keyword_to_ids[kw] = ids

        for kw in keywords:         
            if kw in keyword_to_ids:
                if new_file_id not in keyword_to_ids[kw]:
                    keyword_to_ids[kw].append(new_file_id)
            else:
                keyword_to_ids[kw] = [new_file_id]

        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            ids_str = " ".join(str(i) for i in keyword_to_ids[kw])
            lines.append(f"{kw} {ids_str}")
        db_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding='utf-8')

        # 5. 同步到 C++ 服务端
        server_updated = 0
        if getattr(hermes_client, '_initialized', False):
            keywords_list = list(keywords)
            file_ids_list = [new_file_id] * len(keywords_list)
            if keywords_list and hermes_client.batch_update(writer_id, keywords_list, file_ids_list):
                server_updated = len(keywords_list)

        return jsonify({
            'success': True,
            'file_id': new_file_id,
            'path': str(relative_path),
            'message': f'文件创建成功，ID={new_file_id}，已建立 {len(keywords)} 个关键字索引。' +
                       ('已同步到服务端。' if server_updated else '未连接 C++ 服务，请稍后点击「重新加载索引」。')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route('/api/writer/batch-create-files', methods=['POST'])
@_require_roles({"writer"})
def writer_batch_create_files():
    try:
        data = request.get_json()
        if not data or 'files' not in data:
            return jsonify({'success': False, 'error': 'Missing files array'}), 400
        
        files_data = data['files']
        if not isinstance(files_data, list) or len(files_data) == 0:
            return jsonify({'success': False, 'error': 'files must be a non-empty array'}), 400
        
        user = _get_session_user()
        writer_id = user.get("writer_id")
        
        # 1. 一次性获取现有最大 file_id
        paths_file = _database_paths_file_path(writer_id)
        existing_ids = set()
        if paths_file.exists():
            for line in paths_file.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.strip().split(None, 1)
                if parts:
                    try:
                        existing_ids.add(int(parts[0]))
                    except ValueError:
                        continue
        next_id = 1
        while next_id in existing_ids:
            next_id += 1
        
        # 准备收集新数据
        new_paths_lines = []     
        new_keyword_map = {}     
        results = []
        
        # 2. 逐个处理文件，但只生成物理文件和收集映射
        for file_info in files_data:
            filename = file_info.get('filename', 'unknown')
            content = file_info.get('content', '')
            if not content:
                results.append({'filename': filename, 'success': False, 'error': '文件内容为空'})
                continue
            
            try:
                file_id = next_id
                next_id += 1
                
                # 生成相对路径并存储物理文件
                from pathlib import Path
                import time, os
                base, ext = os.path.splitext(filename)
                safe_name = f"{base}_{int(time.time())}_{file_id}{ext}" if ext else f"{base}_{int(time.time())}_{file_id}"
                relative_path = f"maildir/writer{writer_id + 1}/batch/{safe_name}"
                safe_path = Path(PROJECT_ROOT) / relative_path
                safe_path = safe_path.resolve()
                if not str(safe_path).startswith(str(PROJECT_ROOT.resolve())):
                    raise ValueError("路径非法")
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                safe_path.write_text(content, encoding='utf-8')
                
                # 提取关键字并应用黑白名单过滤
                raw_keywords = list(set(_extract_keywords_from_text(content)))
                keywords = _apply_dict_filter(writer_id, raw_keywords)
                
                # 记录到批量映射中
                new_paths_lines.append(f"{file_id} {relative_path}")
                for kw in keywords:
                    new_keyword_map.setdefault(kw, []).append(file_id)
                
                results.append({
                    'filename': filename,
                    'success': True,
                    'file_id': file_id,
                    'path': relative_path,
                    'keywords_count': len(keywords)
                })
            except Exception as e:
                results.append({'filename': filename, 'success': False, 'error': str(e)})
        
        # 3. 一次性写入 database_paths（追加）
        if new_paths_lines:
            with open(paths_file, 'a', encoding='utf-8') as f:
                f.write("\n".join(new_paths_lines) + ("\n" if new_paths_lines else ""))
        
        # 4. 一次性更新 database（关键字索引）
        db_path = _database_file_path(writer_id)
        keyword_to_ids = {}
        if db_path.exists():
            for line in db_path.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                kw = parts[0]
                ids = []
                for s in parts[1:]:
                    try:
                        fid = int(s)
                        if fid > 0:
                            ids.append(fid)
                    except ValueError:
                        continue
                keyword_to_ids[kw] = ids
        
        # 合并新关键字映射
        for kw, new_ids in new_keyword_map.items():
            if kw in keyword_to_ids:
                existing = set(keyword_to_ids[kw])
                existing.update(new_ids)
                keyword_to_ids[kw] = sorted(existing)
            else:
                keyword_to_ids[kw] = sorted(set(new_ids))
        
        # 写回 database
        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            ids_str = " ".join(str(i) for i in keyword_to_ids[kw])
            lines.append(f"{kw} {ids_str}")
        db_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding='utf-8')
        
        # 5. 批量同步 C++ 服务端
        all_pairs = []
        for kw, ids in new_keyword_map.items():
            for fid in ids:
                all_pairs.append((kw, fid))
        cpp_synced = False
        if all_pairs and getattr(hermes_client, '_initialized', False):
            try:
                keywords_list, file_ids_list = zip(*all_pairs)
                if hermes_client.batch_update(writer_id, list(keywords_list), list(file_ids_list)):
                    cpp_synced = True
            except Exception:
                pass
        
        return jsonify({
            'success': True,
            'results': results,
            'total_success': sum(1 for r in results if r['success']),
            'total_failed': sum(1 for r in results if not r['success']),
            'cpp_synced': cpp_synced,
            'message': f'批量处理完成。{"已同步至C++服务端。" if cpp_synced else "本地索引已更新，但未连接C++服务，请点击「重新加载索引」。"}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route('/api/writer/batch-import-zip', methods=['POST'])
@_require_roles({"writer"})
def batch_import_zip():
    """接收一个 ZIP 文件，解压到临时目录，然后提交后台处理。"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有上传文件'}), 400
    file = request.files['file']
    if not file.filename.lower().endswith('.zip'):
        return jsonify({'success': False, 'error': '仅支持 ZIP 文件'}), 400

    user = _get_session_user()
    writer_id = user.get("writer_id")

    # 创建临时目录（放在 PROJECT_ROOT 下以保证相对路径可用，处理完后删除）
    temp_root = PROJECT_ROOT / 'temp_imports'
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.mkdtemp(dir=str(temp_root))
    try:
        zip_path = os.path.join(temp_dir, 'upload.zip')
        file.save(zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'success': False, 'error': f'ZIP 文件解析失败: {str(e)}'}), 400

    task_id = str(uuid.uuid4())
    with batch_tasks_lock:
        batch_tasks[task_id] = {'status': 'pending', 'progress': 0, 'total': 0, 'done': 0, 'result': None}

    def zip_wrapper():
        try:
            process_directory(task_id, temp_dir, writer_id)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    batch_executor.submit(zip_wrapper)
    return jsonify({'success': True, 'task_id': task_id})
# ---------- 批量导入任务存储（全局） ----------

def process_directory(task_id, dir_path, writer_id):
    try:
        with batch_tasks_lock:
            batch_tasks[task_id]['status'] = 'processing'
        dir_path = Path(dir_path)
        if not dir_path.exists() or not dir_path.is_dir():
            raise ValueError("目录不存在或不可访问")

        files = list(dir_path.rglob('*'))
        files = [f for f in files if f.is_file() and f.suffix.lower() in ('.txt', '.md', '.eml', '.csv', '.log')]
        total = len(files)
        with batch_tasks_lock:
            batch_tasks[task_id]['total'] = total
            batch_tasks[task_id]['done'] = 0

        paths_file = _database_paths_file_path(writer_id)
        existing_ids = set()
        if paths_file.exists():
            for line in paths_file.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.strip().split(None, 1)
                if parts:
                    try:
                        existing_ids.add(int(parts[0]))
                    except:
                        pass
        next_id = 1
        while next_id in existing_ids:
            next_id += 1

        all_keywords = []   # (keyword, file_id)
        project_root = PROJECT_ROOT.resolve()
        # 永久存储目录（项目内），确保文件不会被自动清理
        import_base = project_root / 'imported' / f'writer_{writer_id}'
        import_base.mkdir(parents=True, exist_ok=True)

        for idx, file_path in enumerate(files):
            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')
            except:
                continue

            raw_keywords = list(set(_extract_keywords_from_text(content)))
            keywords = _apply_dict_filter(writer_id, raw_keywords)
            if not keywords:
                continue

            file_id = next_id
            next_id += 1

            # 一律复制到永久目录，避免临时文件被清理后无法访问
            safe_name = f"{file_path.stem}_{uuid.uuid4().hex[:8]}{file_path.suffix}"
            dst_path = import_base / safe_name
            shutil.copy2(file_path.resolve(), dst_path)
            relative_path = str(dst_path.relative_to(project_root))

            with open(paths_file, 'a', encoding='utf-8') as pf:
                pf.write(f"{file_id} {relative_path}\n")

            for kw in keywords:
                all_keywords.append((kw, file_id))

            with batch_tasks_lock:
                batch_tasks[task_id]['done'] = idx + 1
                batch_tasks[task_id]['progress'] = int((idx + 1) / total * 100) if total > 0 else 100

        # 合并到 database
        db_path = _database_file_path(writer_id)
        keyword_to_ids = {}
        if db_path.exists():
            for line in db_path.read_text(encoding='utf-8').splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    kw = parts[0]
                    ids = [int(x) for x in parts[1:] if x.lstrip('-').isdigit()]
                    keyword_to_ids[kw] = ids
        for kw, fid in all_keywords:
            if kw not in keyword_to_ids:
                keyword_to_ids[kw] = []
            if fid not in keyword_to_ids[kw]:
                keyword_to_ids[kw].append(fid)
        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            lines.append(kw + " " + " ".join(str(i) for i in keyword_to_ids[kw]))
        db_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding='utf-8')

        if getattr(hermes_client, '_initialized', False) and all_keywords:
            kw_list, fid_list = zip(*all_keywords)
            hermes_client.batch_update(writer_id, list(kw_list), list(fid_list))

        with batch_tasks_lock:
            batch_tasks[task_id]['status'] = 'completed'
            batch_tasks[task_id]['result'] = {'total_files': total, 'indexed_files': len(all_keywords)}
    except Exception as e:
        with batch_tasks_lock:
            batch_tasks[task_id]['status'] = 'failed'
            batch_tasks[task_id]['result'] = {'error': str(e)}

# 查询任务状态（ZIP/目录通用）
@app.route('/api/writer/batch-task/<task_id>', methods=['GET'])
@_require_roles({"writer"})
def get_batch_task_status(task_id):
    with batch_tasks_lock:
        task = batch_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    return jsonify({
        'success': True,
        'status': task['status'],
        'progress': task.get('progress', 0),
        'total': task.get('total', 0),
        'done': task.get('done', 0),
        'result': task.get('result')
    })


# 写者删除文件 
@app.route('/api/writer/delete-file', methods=['POST'])
@_require_roles({"writer"})
def writer_delete_file():
    """
    删除写者自己的文件（物理文件、映射、关键字索引全部清理）
    请求: {"file_id": 123}
    """
    try:
        data = request.get_json()
        if not data or 'file_id' not in data:
            return jsonify({'success': False, 'error': 'Missing file_id'}), 400

        file_id = int(data['file_id'])
        user = _get_session_user()
        writer_id = user.get("writer_id")

        # 1. 从 database_paths 中找到文件路径并删除该行
        paths_file = _database_paths_file_path(writer_id)
        if not paths_file.exists():
            return jsonify({'success': False, 'error': 'database_paths 文件不存在'}), 404

        lines = paths_file.read_text(encoding='utf-8', errors='replace').splitlines()
        new_lines = []
        file_path = None
        for line in lines:
            parts = line.strip().split(None, 1)
            if len(parts) >= 2 and parts[0] == str(file_id):
                file_path = parts[1].strip()
                continue  
            new_lines.append(line)

        if file_path is None:
            return jsonify({'success': False, 'error': f'文件 ID {file_id} 不存在'}), 404

        # 2. 删除物理文件
        project_root = Path(PROJECT_ROOT).resolve()
        abs_path = (project_root / file_path).resolve()
        if str(abs_path).startswith(str(project_root)) and abs_path.exists():
            abs_path.unlink()
        else:
            # 路径不安全或文件不存在，仅记录，不阻止删除映射
            pass

        # 3. 写回 database_paths
        paths_file.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding='utf-8')

        # 4. 从 database 关键字索引中移除该 file_id
        db_path = _database_file_path(writer_id)
        if db_path.exists():
            db_lines = db_path.read_text(encoding='utf-8', errors='replace').splitlines()
            new_db_lines = []
            for line in db_lines:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                kw = parts[0]
                ids = []
                for s in parts[1:]:
                    try:
                        fid = int(s)
                        if fid != file_id and fid > 0:  
                            ids.append(fid)
                    except ValueError:
                        continue
                if ids:  
                    new_db_lines.append(f"{kw} " + " ".join(str(i) for i in ids))
            db_path.write_text("\n".join(new_db_lines) + ("\n" if new_db_lines else ""), encoding='utf-8')

        # 5. 同步到 C++ 服务端：清除该 file_id 的所有关键字映射
        server_cleared = 0
        if getattr(hermes_client, '_initialized', False):
            if hasattr(hermes_client, 'delete_file'):
                server_cleared = hermes_client.delete_file(writer_id, file_id)
            # 否则提示用户手动重新加载索引

        return jsonify({
            'success': True,
            'message': f'文件 ID {file_id} 已删除，索引已更新。' + (
                '已同步到服务端。' if server_cleared else '未连接 C++ 服务或客户端不支持自动同步，请点击「重新加载索引」。'
            )
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route('/api/writer/preview-keywords', methods=['POST'])
@_require_roles({"writer"})
def preview_keywords():
    """返回文本中提取并过滤后的关键词列表，且仅包含当前 database 中仍存在的关键词（匹配 TTL 清理后的状态）"""
    data = request.get_json()
    if not data or 'content' not in data:
        return jsonify({'success': False, 'error': 'Missing content'}), 400

    content = data['content']
    user = _get_session_user()
    writer_id = user.get("writer_id")

    # 1. 正常提取并过滤黑白名单
    raw_keywords = list(set(_extract_keywords_from_text(content)))
    keywords = _apply_dict_filter(writer_id, raw_keywords)

    # 2. 获取当前 database 中存在的关键词集合（只有这些才实际可被检索）
    existing_keywords = set()
    db_path = _database_file_path(writer_id)
    if db_path.exists():
        for line in db_path.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = line.strip().split()
            if parts:
                existing_keywords.add(parts[0])

    # 3. 只保留仍在 database 中的关键词
    final_keywords = [kw for kw in keywords if kw in existing_keywords]

    return jsonify({
        'success': True,
        'keywords': sorted(final_keywords),
        'count': len(final_keywords)
    })
#写者独立索引版本 
WRITER_CLEAR_VERSION_DIR = Path(__file__).parent / "writer_clear_versions"
WRITER_CLEAR_VERSION_DIR.mkdir(exist_ok=True)

def _get_writer_clear_version_file(writer_id: int) -> Path:
    return WRITER_CLEAR_VERSION_DIR / f"clear_{writer_id}.txt"

def get_writer_clear_version(writer_id: int) -> int:
    fpath = _get_writer_clear_version_file(writer_id)
    if fpath.exists():
        try:
            return int(fpath.read_text().strip())
        except:
            return 1
    return 1

def set_writer_clear_version(writer_id: int, version: int):
    fpath = _get_writer_clear_version_file(writer_id)
    fpath.write_text(str(version))

@app.route('/api/writer/epoch', methods=['GET'])
@_require_roles({"writer"})
def get_writer_epoch_info():
    user = _get_session_user()
    writer_id = user.get("writer_id")
    current = get_writer_clear_version(writer_id)
    is_cleared = (current > 1)
    return jsonify({
        'success': True,
        'index_version': current,
        'previous_index_version': current - 1,
        'is_index_cleared': is_cleared,
        'message': '索引已清空，旧搜索令牌失效' if is_cleared else '索引正常'
    })

@app.route('/api/writer/epoch/advance', methods=['POST'])
@_require_roles({"writer"})
def advance_writer_epoch():
    try:
        user = _get_session_user()
        writer_id = user.get("writer_id")
        old_version = get_writer_clear_version(writer_id)
        new_version = old_version + 1
        
        db_path = _database_file_path(writer_id)
        if db_path.exists():
            backup_dir = DATABASE_DIR / f"index_backups/writer_{writer_id}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"version_{old_version}_{timestamp}.txt"
            shutil.copy(db_path, backup_path)
        
        db_path.write_text("", encoding='utf-8')
        
        if getattr(hermes_client, '_initialized', False):
            if hasattr(hermes_client, 'clear_writer'):
                hermes_client.clear_writer(writer_id)
            if hasattr(hermes_client, 'reset_update_state'):
                hermes_client.reset_update_state(writer_id)
            if hasattr(hermes_client, 'load_update_state'):
                hermes_client.load_update_state(writer_id)
        
        set_writer_clear_version(writer_id, new_version)
        
        return jsonify({
            'success': True,
            'old_version': old_version,
            'new_version': new_version,
            'message': f'索引已从版本 {old_version} 升级到 {new_version}。旧索引已备份，审计员无法检索。'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

#  关键词 TTL 
TTL_DIR = Path(__file__).parent / "keyword_ttl"
TTL_DIR.mkdir(exist_ok=True)

def _get_ttl_file(writer_id: int) -> Path:
    return TTL_DIR / f"ttl_{writer_id}.json"

def load_ttl(writer_id: int) -> dict:
    fpath = _get_ttl_file(writer_id)
    if fpath.exists():
        try:
            return json.loads(fpath.read_text())
        except:
            return {}
    return {}

def save_ttl(writer_id: int, ttl_dict: dict):
    fpath = _get_ttl_file(writer_id)
    fpath.write_text(json.dumps(ttl_dict, indent=2))
    
def _cleanup_expired_keywords_for_writer(writer_id: int, global_epoch: int) -> bool:
    """清理过期关键词，返回 True 表示发生了实际清理"""
    ttl = load_ttl(writer_id)
    expired = [kw for kw, exp in ttl.items() if exp <= global_epoch]
    if not expired:
        return False
    db_path = _database_file_path(writer_id)
    if db_path.exists():
        lines = db_path.read_text(encoding='utf-8').splitlines()
        new_lines = [line for line in lines if not any(line.startswith(kw + ' ') for kw in expired)]
        db_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""))
    for kw in expired:
        del ttl[kw]
    save_ttl(writer_id, ttl)
    return True

    
   

@app.route('/api/writer/ttl', methods=['GET'])
@_require_roles({"writer"})
def get_ttl():
    user = _get_session_user()
    writer_id = user.get("writer_id")
    ttl = load_ttl(writer_id)
    return jsonify({'success': True, 'ttl': ttl})

@app.route('/api/writer/ttl', methods=['POST'])
@_require_roles({"writer"})
def set_keyword_ttl():
    data = request.get_json()

    # 1. 参数提取与校验
    keyword = data.get('keyword', '').strip()
    if not keyword:
        return jsonify({'success': False, 'error': '关键词不能为空'}), 400

    requested_ttl = data.get('expire_epoch')
    if not isinstance(requested_ttl, int):
        return jsonify({'success': False, 'error': 'expire_epoch 必须是整数'}), 400
    if requested_ttl < 1:
        return jsonify({'success': False, 'error': 'expire_epoch 必须 >= 1'}), 400

    # 2. 获取写者身份
    user = _get_session_user()
    writer_id = user.get("writer_id")

    # 3. 强制最短保留期
    MIN_TTL = int(os.getenv("HERMES_MIN_TTL", "5"))   # 支持通过环境变量配置
    current_epoch = get_global_epoch()
    effective_ttl = max(requested_ttl, current_epoch + MIN_TTL)

    # 4. 加载现有 TTL、合并、保存
    ttl = load_ttl(writer_id)
    ttl[keyword] = effective_ttl
    save_ttl(writer_id, ttl)

    return jsonify({
        'success': True,
        'keyword': keyword,
        'requested_epoch': requested_ttl,
        'effective_epoch': effective_ttl,
        'message': f'关键词 "{keyword}" 有效期至 Epoch {effective_ttl}'
    })
  
@app.route('/api/writer/ttl/cleanup', methods=['POST'])
@_require_roles({"writer"})
def manual_cleanup_ttl():
    user = _get_session_user()
    writer_id = user.get("writer_id")
    current_epoch = get_global_epoch()
    cleaned = _cleanup_expired_keywords_for_writer(writer_id, current_epoch)
    
    # 【兜底】如果该写者发生了清理且 C++ 客户端已连接，reload 确保全局一致
    if cleaned and getattr(hermes_client, '_initialized', False):
        try:
            hermes_client.reload_index_from_database()
        except Exception:
            pass
    
    return jsonify({'success': True, 'message': '已清理过期关键词'})
# 后台自动清理线程（在启动时启用）
def ttl_cleanup_loop():
    import time
    while True:
        time.sleep(3600)
        try:
            global_epoch = get_global_epoch()
            num = get_server_num_writers()
            any_cleaned = False
            for w_id in range(num):
                if _cleanup_expired_keywords_for_writer(w_id, global_epoch):
                    any_cleaned = True
            
            # 【新增】如果发生了清理，统一 reload 确保 C++ 服务端同步
            if any_cleaned and getattr(hermes_client, '_initialized', False):
                try:
                    hermes_client.reload_index_from_database()
                except Exception:
                    pass
        except:
            pass
#  黑/白名单管理（每个写者独立） 
BLACKLIST_DIR = Path(__file__).parent / "keyword_lists"
BLACKLIST_DIR.mkdir(exist_ok=True)

def _get_blacklist_file(writer_id: int) -> Path:
    return BLACKLIST_DIR / f"blacklist_{writer_id}.txt"

def _get_whitelist_file(writer_id: int) -> Path:
    return BLACKLIST_DIR / f"whitelist_{writer_id}.txt"

def _load_list(file_path: Path) -> list:
    if not file_path.exists():
        return []
    return [line.strip() for line in file_path.read_text(encoding='utf-8').splitlines() if line.strip()]

def _save_list(file_path: Path, items: list):
    file_path.write_text("\n".join(items), encoding='utf-8')

def _apply_dict_filter(writer_id: int, keywords: list) -> list:
    blacklist = _load_list(_get_blacklist_file(writer_id))
    whitelist = _load_list(_get_whitelist_file(writer_id))
    filtered = [kw for kw in keywords if kw not in blacklist]
    for w in whitelist:
        if w not in filtered:
            filtered.append(w)
    return filtered

@app.route('/api/writer/blacklist', methods=['GET'])
@_require_roles({"writer"})
def get_blacklist():
    user = _get_session_user()
    writer_id = user.get("writer_id")
    items = _load_list(_get_blacklist_file(writer_id))
    return jsonify({'success': True, 'blacklist': items})

@app.route('/api/writer/blacklist', methods=['POST'])
@_require_roles({"writer"})
def add_blacklist():
    data = request.get_json()
    word = data.get('word', '').strip().lower()
    if not word:
        return jsonify({'success': False, 'error': '关键词不能为空'}), 400
    user = _get_session_user()
    writer_id = user.get("writer_id")
    path = _get_blacklist_file(writer_id)
    items = _load_list(path)
    if word not in items:
        items.append(word)
        _save_list(path, items)
    return jsonify({'success': True, 'message': f'已添加黑名单词: {word}'})

@app.route('/api/writer/blacklist', methods=['DELETE'])
@_require_roles({"writer"})
def remove_blacklist():
    data = request.get_json()
    word = data.get('word', '').strip().lower()
    if not word:
        return jsonify({'success': False, 'error': '关键词不能为空'}), 400
    user = _get_session_user()
    writer_id = user.get("writer_id")
    path = _get_blacklist_file(writer_id)
    items = _load_list(path)
    if word in items:
        items.remove(word)
        _save_list(path, items)
    return jsonify({'success': True, 'message': f'已删除黑名单词: {word}'})

@app.route('/api/writer/whitelist', methods=['GET'])
@_require_roles({"writer"})
def get_whitelist():
    user = _get_session_user()
    writer_id = user.get("writer_id")
    items = _load_list(_get_whitelist_file(writer_id))
    return jsonify({'success': True, 'whitelist': items})

@app.route('/api/writer/whitelist', methods=['POST'])
@_require_roles({"writer"})
def add_whitelist():
    data = request.get_json()
    word = data.get('word', '').strip().lower()
    if not word:
        return jsonify({'success': False, 'error': '关键词不能为空'}), 400
    user = _get_session_user()
    writer_id = user.get("writer_id")
    path = _get_whitelist_file(writer_id)
    items = _load_list(path)
    if word not in items:
        items.append(word)
        _save_list(path, items)
    return jsonify({'success': True, 'message': f'已添加白名单词: {word}'})

@app.route('/api/writer/whitelist', methods=['DELETE'])
@_require_roles({"writer"})
def remove_whitelist():
    data = request.get_json()
    word = data.get('word', '').strip().lower()
    if not word:
        return jsonify({'success': False, 'error': '关键词不能为空'}), 400
    user = _get_session_user()
    writer_id = user.get("writer_id")
    path = _get_whitelist_file(writer_id)
    items = _load_list(path)
    if word in items:
        items.remove(word)
        _save_list(path, items)
    return jsonify({'success': True, 'message': f'已删除白名单词: {word}'})

#  全局 Epoch 查询接口 
@app.route('/api/global-epoch', methods=['GET'])
@_require_roles({"reader", "writer", "admin"})
def get_current_global_epoch():
    return jsonify({"success": True, "global_epoch": get_global_epoch()})
if __name__ == '__main__':
    print("Starting Hermes Compliance Audit Web Server (Reader / Auditor API)")
    print(f"  Port: {FLASK_PORT}")
    print(f"  C++ server: {CLIENT_CONFIG['server_address']}")
    print(f"  Writers (server): {CLIENT_CONFIG['num_writers']}")
    print(f"  Epoch: {HERMES_EPOCH}")
    print(f"  Allowed writers: {'all' if ALLOWED_WRITERS is None else ALLOWED_WRITERS}")
    print(f"  请在浏览器打开: http://127.0.0.1:{FLASK_PORT} 或 http://localhost:{FLASK_PORT}")
    threading.Thread(target=ttl_cleanup_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG)