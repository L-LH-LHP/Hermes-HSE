"""
多写作者邮件合规审计系统 - Web 后端

角色：读者（审计员）通过本 API 进行跨写作者关键字搜索，写作者为安然员工，
云服务器仅存储加密索引、执行搜索，无法获知关键字明文。
"""

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import os
import sys
import time
import base64
import json
import subprocess
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

try:
    import zmq  # 用于直接向Hermes server查询writer数量
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

CLIENT_CONFIG = {
    'server_address': HERMES_SERVER,
    'num_writers': HERMES_NUM_WRITERS,
    'epoch': HERMES_EPOCH,
}
hermes_client = HermesClient(**CLIENT_CONFIG)
# 使 C++ 客户端的 load_update_state 与 Flask 写入的 database 目录一致，避免增量添加时用错 count 覆盖链导致原有关键字-文件对消失
if getattr(hermes_client, 'set_database_dir', None):
    hermes_client.set_database_dir(str(DATABASE_DIR.resolve()))


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


def rebuild_database_for_writer(writer_id: int):
    """
    根据 database_paths 和磁盘上的文件内容，重建该用户的 keyword->fileID 索引，写入 database/(writer_id+1).txt。
    格式与 extract_database.go 一致：每行 "关键词 fileID1 fileID2 ..."
    返回 (成功, 错误信息)。
    """
    path_file = _database_paths_file_path(writer_id)
    if not path_file.exists():
        return False, "database_paths 文件不存在"
    keyword_to_ids = {}  # keyword -> list of file_id (with duplicates, then we dedup per line)
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
        for kw in _extract_keywords_from_text(content):
            if kw not in keyword_to_ids:
                keyword_to_ids[kw] = []
            keyword_to_ids[kw].append(file_id)
    out_path = _database_file_path(writer_id)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for kw in sorted(keyword_to_ids.keys()):
            ids = list(dict.fromkeys(keyword_to_ids[kw]))  # 去重且保持顺序
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


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/status', methods=['GET'])
def status():
    """获取系统状态（含 Epoch、审计员授权信息）"""
    mode = "unknown"
    num_writers = get_server_num_writers()
    try:
        mode = "cpp" if getattr(hermes_client, "_initialized", False) else "cli_fallback"
    except Exception:
        mode = "unknown"
    allowed = get_auditor_writer_ids()
    return jsonify({
        'status': 'online',
        'server_address': CLIENT_CONFIG['server_address'],
        'num_writers': num_writers,
        'search_mode': mode,
        'epoch': HERMES_EPOCH,
        'allowed_writers': allowed,
        'allowed_writers_count': len(allowed),
    })


@app.route('/api/search', methods=['POST'])
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
        data = request.get_json()
        
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

        allowed = get_auditor_writer_ids()
        if writer_ids is None:
            writer_ids = allowed
        else:
            writer_ids = [w for w in writer_ids if w in set(allowed)]

        t0 = time.perf_counter()
        # 在子进程中执行搜索（加载 C++ 库），崩溃时仅 worker 退出，主进程返回 500
        worker_path = BASE_DIR / "run_search_worker.py"
        result = None
        if worker_path.exists():
            try:
                proc = subprocess.run(
                    [sys.executable, str(worker_path)],
                    input=json.dumps({"keyword": keyword, "writer_ids": writer_ids}),
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
        return jsonify({
            'success': True,
            'keyword': keyword,
            'results': results,
            'search_time_ms': search_time_ms,
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Search failed: {str(e)}'
        }), 500


@app.route('/api/update', methods=['POST'])
def update():
    """
    更新API
    
    请求格式:
    {
        "writer_id": 0,
        "keyword": "security",
        "file_id": 2025,
        "file_path": "./maildir/allen-p/all_documents/555."   // 可选，仅当 file_id 为新文档时填写，用于同步 database_paths
    }
    
    返回格式:
    {
        "success": true,
        "message": "Update successful",
        "database_synced": true,
        "database_paths_synced": false
    }
    """
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
        file_path = data.get('file_path', "").strip()  # 可选：新文档时填写，用于同步 database_paths
        
        allowed = get_auditor_writer_ids()
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
        allowed = get_auditor_writer_ids()
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
def update_document():
    """
    更新文件：用 new_content 覆盖指定用户、文件ID 对应的原文，重建该用户的 database 索引，
    并对新内容中的每个关键字调用 C++ 服务端 update，使无需重启即可检索。
    请求体: { "writer_id": 0, "file_id": 123, "new_content": "新文件内容..." }
    """
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
        if new_content is None:
            new_content = ''
        else:
            new_content = str(new_content)
        allowed = get_auditor_writer_ids()
        if writer_id not in allowed:
            return jsonify({'success': False, 'error': f'writer_id={writer_id} not allowed'}), 403
        path = get_file_path_from_database_paths(writer_id, file_id)
        if path is None:
            return jsonify({'success': False, 'error': f'未在 database_paths 中找到该用户、文件ID 对应路径'}), 404
        if not path.exists():
            return jsonify({'success': False, 'error': f'文件不存在: {path}'}), 404
        # 1) 在覆盖前读取当前 database，用于「删除」旧关键字映射（标准流程：清理旧搜索映射）
        db_path = _database_file_path(writer_id)
        keyword_to_ids_old: dict[str, list[int]] = {}
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
        new_keywords = set(_extract_keywords_from_text(new_content))
        # 2) 物理存储更新：替换原文件内容（文件 ID 不变）
        path.write_text(new_content, encoding='utf-8')
        # 3) 本地 database 重建（增量或全量）
        ok, err = _rebuild_database_for_writer_incremental(writer_id, file_id, new_content)
        if not ok:
            ok, err = rebuild_database_for_writer(writer_id)
        if not ok:
            return jsonify({
                'success': False,
                'error': f'文件已覆盖，但重建 database 失败: {err}',
            }), 500
        # 4) 同步到服务端：标准流程「先删旧关键字映射，再建新映射」或（无现有 database 时）全量清空后推送
        server_updated = 0
        if getattr(hermes_client, '_initialized', False):
            if keyword_to_ids_old and db_path.exists():
                # 增量：对不再适用的旧关键字发 op=删除，再对提取出的新关键字发 op=添加
                to_del_kw = list(old_keywords - new_keywords)
                if to_del_kw and getattr(hermes_client, 'delete_updates', None):
                    counts_del, file_ids_prev_del = [], []
                    for kw in to_del_kw:
                        ids = keyword_to_ids_old.get(kw, [])
                        try:
                            # 找出实际的 count（即排除占位符后的索引位置）
                            # 因为之前可能是 -1 占位，我们需要找到当前这个 file_id 在“真正的链”中的位置
                            # 幸好这里是获取“旧”映射的位置，old_keywords 里还没替换成 -1
                            idx = ids.index(file_id)
                        except ValueError:
                            continue
                        counts_del.append(idx)
                        file_ids_prev_del.append(ids[idx - 1] if idx > 0 else 0)
                    if len(counts_del) == len(to_del_kw):
                        hermes_client.delete_updates(writer_id, to_del_kw, counts_del, file_ids_prev_del)
                if getattr(hermes_client, 'load_update_state', None):
                    hermes_client.load_update_state(writer_id)
                # 只对「新出现」的关键字发添加（已在链中的不重复添加）
                to_add_kw = list(new_keywords - old_keywords)
                if to_add_kw:
                    kw_add = to_add_kw
                    id_add = [file_id] * len(to_add_kw)
                    if hermes_client.batch_update(writer_id, kw_add, id_add):
                        server_updated = len(kw_add)
            else:
                # 无现有 database：清空该写者后整份推送
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
            'message': f'已更新文件并重建该用户关键字索引（database）。' + (
                f'已按标准流程同步到服务端（删除旧映射+添加新映射），可直接检索。' if server_updated else '未连接 C++ 服务或不可用，请连接后重试或重启 server 后从 database 加载。'
            ),
            'index_updated_on_server': server_updated > 0,
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': f'Invalid parameter: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/client-status', methods=['GET'])
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
def get_writers():
    """获取当前审计员可搜索的写入者列表（受 HERMES_ALLOWED_WRITERS 限制）"""
    allowed = get_auditor_writer_ids()
    return jsonify({
        'success': True,
        'writers': [
            {'id': i, 'name': f'Writer {i+1}'}
            for i in allowed
        ]
    })


@app.route('/api/document', methods=['POST'])
def get_document():
    """
    获取邮件文档：decrypt=false 仅返回加密内容，decrypt=true 返回解密后原文。
    
    请求格式:
    {
        "writer_id": 0,
        "file_id": 1,
        "decrypt": false   // 可选，默认 true；false=仅返回密文，true=返回明文
    }
    
    返回（decrypt=false）:
    { "success": true, "encrypted": true, "content": "base64密文", "iv": "base64", "size": N }
    或占位: { "success": true, "encrypted": true, "placeholder": true, "message": "..." }
    
    返回（decrypt=true）:
    { "success": true, "content": "邮件内容...", "encoding": "utf-8", "size": N }
    """
    try:
        data = request.get_json()
        
        required_fields = ['writer_id', 'file_id']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        writer_id = int(data['writer_id'])
        file_id = int(data['file_id'])
        decrypt = data.get('decrypt', True)

        allowed = get_auditor_writer_ids()
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

        # 仅返回加密内容（密文 + IV），不解密
        if not decrypt:
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
                'message': '（未找到加密存储，点击「解密」查看原文）',
            })

        # 以下为解密后原文：优先从 maildir 读取，失败时尝试 C++ 解密
        from pathlib import Path
        import os
        
        # extract_database.go 中 userID 从1开始，所以映射文件是 {writer_id+1}.txt
        mapping_file = Path(__file__).parent.parent / "database_paths" / f"{writer_id + 1}.txt"
        
        # 尝试多个可能的路径
        if not mapping_file.exists():
            mapping_file = Path("../database_paths") / f"{writer_id + 1}.txt"
        if not mapping_file.exists():
            mapping_file = Path("database_paths") / f"{writer_id + 1}.txt"
        
        if not mapping_file.exists():
            return jsonify({
                'success': False,
                'error': f'Mapping file not found for writer_id={writer_id}. Please run extract_database.go first.'
            }), 404
        
        # 读取映射文件，查找对应的fileID
        file_path = None
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split(' ', 1)  # 分割为 fileID 和 filePath
                    if len(parts) == 2:
                        fid_str, fpath = parts
                        if int(fid_str) == file_id:
                            file_path = fpath
                            break
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Failed to read mapping file: {str(e)}'
            }), 500
        
        if file_path is None:
            return jsonify({
                'success': False,
                'error': f'File ID {file_id} not found in mapping for writer_id={writer_id}'
            }), 404
        
        # 读取原始邮件文件
        # 邮件文件可能使用多种编码，尝试多种方式
        mail_path = Path(file_path)
        if not mail_path.exists():
            # 尝试相对路径
            mail_path = Path(__file__).parent.parent / file_path
        if not mail_path.exists():
            mail_path = Path("../") / file_path
        
        if not mail_path.exists():
            return jsonify({
                'success': False,
                'error': f'Mail file not found: {file_path}. Please check if maildir is in the correct location.'
            }), 404
        
        # 读取邮件内容（尝试多种编码）
        content = None
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(mail_path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
                    break
            except Exception:
                continue
        
        if content is None:
            # 如果所有编码都失败，尝试二进制读取
            try:
                with open(mail_path, 'rb') as f:
                    raw_content = f.read()
                    # 尝试解码为UTF-8，忽略错误
                    content = raw_content.decode('utf-8', errors='replace')
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'Failed to read mail file: {str(e)}'
                }), 500
        
        return jsonify({
            'success': True,
            'writer_id': writer_id,
            'file_id': file_id,
            'content': content,
            'encoding': 'utf-8',
            'size': len(content),
            'file_path': str(mail_path)  # 用于调试
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


if __name__ == '__main__':
    print("Starting Hermes Compliance Audit Web Server (Reader / Auditor API)")
    print(f"  Port: {FLASK_PORT}")
    print(f"  C++ server: {CLIENT_CONFIG['server_address']}")
    print(f"  Writers (server): {CLIENT_CONFIG['num_writers']}")
    print(f"  Epoch: {HERMES_EPOCH}")
    print(f"  Allowed writers: {'all' if ALLOWED_WRITERS is None else ALLOWED_WRITERS}")
    print(f"  请在浏览器打开: http://127.0.0.1:{FLASK_PORT} 或 http://localhost:{FLASK_PORT}")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG)


