"""
Hermes Python客户端包装器
这个模块提供了Python接口来调用Hermes C++客户端功能
"""

import ctypes
import os
import json
from typing import List, Dict, Optional
import subprocess
import re

# 可选：用于在cli_fallback模式下直接向server查询真实writer数量
try:
    import zmq  # type: ignore
except Exception:
    zmq = None

# 尝试加载C++共享库
LIB_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_PATH = os.path.join(LIB_DIR, 'libhermes_client.so')
if os.name == 'nt':  # Windows
    LIB_PATH = LIB_PATH.replace('.so', '.dll')

_hermes_lib_load_error = None  # 保存加载失败原因，便于 API 返回给前端（加载成功时为 None）

try:
    if not os.path.exists(LIB_PATH):
        _hermes_lib = None
        _hermes_lib_load_error = f"库文件不存在: {LIB_PATH}。请在 web_api 目录执行 make 编译。"
        print(f"Warning: {_hermes_lib_load_error} Running in mock mode.")
    else:
        try:
            _hermes_lib = ctypes.CDLL(LIB_PATH)
        except Exception as e:
            _hermes_lib = None
            _hermes_lib_load_error = str(e)
            print(f"Warning: Failed to load Hermes library: {_hermes_lib_load_error}")
            print("  -> 若报错含 'cannot open shared object file'，请安装依赖: sudo apt install libzmq3-dev libpbc-dev libgmp-dev libssl-dev")
            print("  -> 并设置库路径后启动 Flask: export LD_LIBRARY_PATH=/path/to/Hermes/lib:$LD_LIBRARY_PATH")
except Exception as e:
    _hermes_lib = None
    _hermes_lib_load_error = getattr(e, 'message', str(e))
    print(f"Warning: Failed to load Hermes library: {e}. Running in mock mode.")


class HermesClient:
    """Hermes可搜索加密系统Python客户端"""
    
    def __init__(self, server_address: str = "tcp://127.0.0.1:8888", num_writers: int = 25, epoch: int = 1):
        """
        初始化Hermes客户端

        Args:
            server_address: 服务器地址
            num_writers: 写入者数量
            epoch: 当前 Epoch（用于索引更新），需与服务器一致
        """
        self.server_address = server_address
        self.num_writers = num_writers
        self.epoch = epoch
        self._initialized = False
        self._cached_server_num_writers: Optional[int] = None
        
        if _hermes_lib:
            self._init_c_functions()
            self._init_c_api()

    def get_effective_num_writers(self) -> int:
        """
        返回当前应使用的writer数量：
        - C++库初始化成功：用self.num_writers（已从server同步）
        - 否则：尝试通过ZeroMQ向server发送'G'查询
        - 再否则：退回到当前self.num_writers（配置值）
        """
        # cpp模式：init后已同步
        if self._initialized and isinstance(self.num_writers, int) and self.num_writers > 0:
            return self.num_writers

        # cli_fallback模式：尝试从server查询
        n = self._get_num_writers_from_server()
        if isinstance(n, int) and n > 0:
            self.num_writers = n
            return n

        return self.num_writers if isinstance(self.num_writers, int) and self.num_writers > 0 else 0

    def _get_num_writers_from_server(self) -> Optional[int]:
        """
        使用ZeroMQ向Hermes server发送'G'查询writer数量。
        server端协议：发送1字节'G'，返回4字节int。
        """
        if self._cached_server_num_writers is not None:
            return self._cached_server_num_writers

        # 没有pyzmq则无法查询
        if zmq is None:
            return None

        try:
            ctx = zmq.Context.instance()
            sock = ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            # 超时避免卡住
            sock.setsockopt(zmq.RCVTIMEO, 1500)
            sock.setsockopt(zmq.SNDTIMEO, 1500)
            sock.connect(self.server_address)
            sock.send(b'G')
            rep = sock.recv()
            sock.close()

            if rep is None or len(rep) < 4:
                return None

            # server.cpp 用 memcpy(reply.data(), &num_writers, 4) -> 小端
            n = int.from_bytes(rep[:4], byteorder="little", signed=True)
            if n > 0:
                self._cached_server_num_writers = n
                return n
            return None
        except Exception:
            return None
    
    def _init_c_functions(self):
        """初始化C函数绑定"""
        if not _hermes_lib:
            return
            
        # hermes_init_system
        _hermes_lib.hermes_init_system.argtypes = [ctypes.c_int, ctypes.c_char_p]
        _hermes_lib.hermes_init_system.restype = ctypes.c_int

        # hermes_set_epoch
        try:
            _hermes_lib.hermes_set_epoch.argtypes = [ctypes.c_int]
            _hermes_lib.hermes_set_epoch.restype = ctypes.c_int
        except AttributeError:
            pass

        # hermes_get_num_writers
        try:
            _hermes_lib.hermes_get_num_writers.argtypes = []
            _hermes_lib.hermes_get_num_writers.restype = ctypes.c_int
        except AttributeError:
            # 旧版共享库可能没有这个接口，忽略
            pass

        # hermes_reload_index
        try:
            _hermes_lib.hermes_reload_index.argtypes = []
            _hermes_lib.hermes_reload_index.restype = ctypes.c_int
        except AttributeError:
            pass

        # hermes_clear_writer / hermes_reset_update_state / hermes_delete_updates / hermes_load_update_state
        try:
            _hermes_lib.hermes_clear_writer.argtypes = [ctypes.c_int]
            _hermes_lib.hermes_clear_writer.restype = ctypes.c_int
            _hermes_lib.hermes_reset_update_state.argtypes = [ctypes.c_int]
            _hermes_lib.hermes_reset_update_state.restype = None
            _hermes_lib.hermes_load_update_state.argtypes = [ctypes.c_int]
            _hermes_lib.hermes_load_update_state.restype = ctypes.c_int
            _hermes_lib.hermes_prepare_state_for_incremental_add.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
            ]
            _hermes_lib.hermes_prepare_state_for_incremental_add.restype = ctypes.c_int
            _hermes_lib.hermes_delete_updates.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
            ]
            _hermes_lib.hermes_delete_updates.restype = ctypes.c_int
            _hermes_lib.hermes_set_database_dir.argtypes = [ctypes.c_char_p]
            _hermes_lib.hermes_set_database_dir.restype = None
        except AttributeError:
            pass

        # hermes_search
        _hermes_lib.hermes_search.argtypes = [
            ctypes.c_char_p,  # keyword
            ctypes.POINTER(ctypes.c_int),  # writer_ids
            ctypes.c_int  # num_writers
        ]
        _hermes_lib.hermes_search.restype = ctypes.c_char_p
        
        # hermes_update
        _hermes_lib.hermes_update.argtypes = [
            ctypes.c_int,  # writer_id
            ctypes.c_char_p,  # keyword
            ctypes.c_int  # file_id
        ]
        _hermes_lib.hermes_update.restype = ctypes.c_int

        # hermes_batch_update
        try:
            _hermes_lib.hermes_batch_update.argtypes = [
                ctypes.c_int,  # writer_id
                ctypes.POINTER(ctypes.c_char_p),  # keywords
                ctypes.POINTER(ctypes.c_int),  # file_ids
                ctypes.c_int,  # count
            ]
            _hermes_lib.hermes_batch_update.restype = ctypes.c_int
        except AttributeError:
            pass

        # hermes_free_string
        _hermes_lib.hermes_free_string.argtypes = [ctypes.c_char_p]
        _hermes_lib.hermes_free_string.restype = None
        
        # hermes_cleanup
        _hermes_lib.hermes_cleanup.argtypes = []
        _hermes_lib.hermes_cleanup.restype = None
        
        # hermes_get_last_error
        _hermes_lib.hermes_get_last_error.argtypes = []
        _hermes_lib.hermes_get_last_error.restype = ctypes.c_char_p
    
    def _init_c_api(self):
        """初始化C API"""
        if not _hermes_lib:
            return
            
        try:
            server_addr_bytes = self.server_address.encode('utf-8')
            result = _hermes_lib.hermes_init_system(
                self.num_writers,
                ctypes.c_char_p(server_addr_bytes)
            )
            
            if result == 0:
                self._initialized = True
                # 从C++侧获取真实writer数量，避免与server不一致（例如server只启动10个）
                try:
                    real_num = _hermes_lib.hermes_get_num_writers()
                    if isinstance(real_num, int) and real_num > 0:
                        self.num_writers = real_num
                except Exception:
                    pass
                # 设置 Epoch（与服务器一致，索引更新需要）
                try:
                    if hasattr(_hermes_lib, 'hermes_set_epoch'):
                        _hermes_lib.hermes_set_epoch(self.epoch)
                except Exception:
                    pass

                if os.environ.get("HERMES_QUIET") != "1":
                    print(f"Successfully initialized Hermes client (C++ mode, num_writers={self.num_writers})")
            else:
                error_msg = _hermes_lib.hermes_get_last_error()
                print(f"Failed to initialize Hermes client: {error_msg.decode('utf-8') if error_msg else 'Unknown error'}")
        except Exception as e:
            if os.environ.get("HERMES_QUIET") != "1":
                print(f"Error initializing C API: {e}")
            self._initialized = False

    def reinit(self) -> tuple[bool, str]:
        """
        重新尝试连接 C++ server（用于 server 先启动、app 后启动时连接失败后的重试）。
        返回 (成功, 说明信息)。
        """
        if not _hermes_lib:
            msg = _hermes_lib_load_error or "C++ 客户端库未加载"
            msg += "。请先在 web_api 目录执行 make 编译 libhermes_client.so；若已存在仍失败，请安装依赖（如 sudo apt install libzmq3-dev libpbc-dev）并设置 LD_LIBRARY_PATH 后重启 Flask。"
            return False, msg
        try:
            if self._initialized:
                try:
                    _hermes_lib.hermes_cleanup()
                except Exception:
                    pass
                self._initialized = False
            self._init_c_api()
            if self._initialized:
                return True, "已连接 C++ server"
            err = ""
            try:
                err = _hermes_lib.hermes_get_last_error()
                if err:
                    err = err.decode("utf-8", errors="replace")
            except Exception:
                pass
            return False, err or "连接失败，请确认 C++ server 已启动且地址正确（如 tcp://127.0.0.1:8888）"
        except Exception as e:
            return False, str(e)
    
    def search(self, keyword: str, writer_ids: Optional[List[int]] = None) -> Dict:
        """
        搜索关键词。优先使用 C++ 库（libhermes_client.so）以保证与 server 协议一致、结果正确；
        若库不可用或未初始化则回退到 client 可执行文件（CLI）。
        
        Args:
            keyword: 搜索关键词
            writer_ids: 要搜索的写入者ID列表，如果为None则搜索所有写入者
            
        Returns:
            包含搜索结果的字典：{"results": [{"writer_id": 1, "file_ids": [...]}, ...]} 或 {"error": "..."}
        """
        if writer_ids is None:
            writer_ids = list(range(self.num_writers))

        if not self._initialized or not _hermes_lib:
            return self._cli_search(keyword, writer_ids)

        try:
            keyword_bytes = keyword.encode('utf-8')
            writer_array = (ctypes.c_int * len(writer_ids))(*writer_ids)
            result_ptr = _hermes_lib.hermes_search(
                ctypes.c_char_p(keyword_bytes),
                writer_array,
                len(writer_ids),
            )
            if result_ptr:
                result_str = ctypes.string_at(result_ptr).decode('utf-8')
                return json.loads(result_str)
            return {"error": "Search failed"}
        except Exception as e:
            return {"error": f"Search exception: {str(e)}"}
    
    def update(self, writer_id: int, keyword: str, file_id: int) -> bool:
        """
        更新操作：添加关键词和文件ID的关联
        
        Args:
            writer_id: 写入者ID
            keyword: 关键词
            file_id: 文件ID
            
        Returns:
            成功返回True，失败返回False
        """
        if not self._initialized:
            print(f"Mock update: writer_id={writer_id}, keyword={keyword}, file_id={file_id}")
            return True
        
        try:
            keyword_bytes = keyword.encode('utf-8')
            result = _hermes_lib.hermes_update(
                writer_id,
                ctypes.c_char_p(keyword_bytes),
                file_id
            )
            return result == 0
        except Exception as e:
            print(f"Update exception: {e}")
            return False

    def batch_update(self, writer_id: int, keywords: List[str], file_ids: List[int]) -> bool:
        """
        批量更新：一次请求发送多组 (keyword, file_id)，减少网络往返，提升同步速度。
        keywords 与 file_ids 长度必须相同，顺序与 database 行内一致（同一 keyword 可多次出现）。
        """
        if not self._initialized or not _hermes_lib:
            return False
        if len(keywords) != len(file_ids) or not keywords:
            return False
        try:
            if not hasattr(_hermes_lib, 'hermes_batch_update'):
                # 回退：逐条 update
                for kw, fid in zip(keywords, file_ids):
                    if not self.update(writer_id, kw, fid):
                        return False
                return True
            n = len(keywords)
            kw_arr = (ctypes.c_char_p * n)(*[k.encode('utf-8') for k in keywords])
            id_arr = (ctypes.c_int * n)(*file_ids)
            return _hermes_lib.hermes_batch_update(writer_id, kw_arr, id_arr, n) == 0
        except Exception as e:
            print(f"Batch update exception: {e}")
            return False

    def clear_writer(self, writer_id: int) -> bool:
        """清除服务端指定写者的索引，便于随后全量推送该写者的 database。"""
        if not self._initialized or not _hermes_lib:
            return False
        try:
            if hasattr(_hermes_lib, 'hermes_clear_writer'):
                return _hermes_lib.hermes_clear_writer(writer_id) == 0
        except Exception:
            pass
        return False

    def reset_update_state(self, writer_id: int) -> None:
        """重置客户端指定写者的 update 计数，便于按 database 顺序全量推送。"""
        if not _hermes_lib:
            return
        try:
            if hasattr(_hermes_lib, 'hermes_reset_update_state'):
                _hermes_lib.hermes_reset_update_state(writer_id)
        except Exception:
            pass

    def load_update_state(self, writer_id: int) -> bool:
        """从 database 文件重新加载该写者的 update 计数（增量「先删后加」时在「加」之前调用）。"""
        if not self._initialized or not _hermes_lib:
            return False
        try:
            if hasattr(_hermes_lib, 'hermes_load_update_state'):
                return _hermes_lib.hermes_load_update_state(writer_id) == 0
        except Exception:
            pass
        return False

    def prepare_state_for_incremental_add(self, writer_id: int, keywords: List[str], file_ids: List[int]) -> bool:
        """为增量添加准备 state：先 load，再对即将添加的 (keyword,file_id) 将 state[keyword] 各减 1，使 batch_update 使用正确 count。"""
        if not self._initialized or not _hermes_lib or len(keywords) != len(file_ids) or not keywords:
            return False
        try:
            if not hasattr(_hermes_lib, 'hermes_prepare_state_for_incremental_add'):
                return False
            n = len(keywords)
            kw_arr = (ctypes.c_char_p * n)(*[k.encode('utf-8') for k in keywords])
            id_arr = (ctypes.c_int * n)(*file_ids)
            return _hermes_lib.hermes_prepare_state_for_incremental_add(writer_id, kw_arr, id_arr, n) == 0
        except Exception:
            return False

    def set_database_dir(self, path: str) -> None:
        """设置 database 目录的绝对路径，与 Flask 写入的 database 一致，避免 load_update_state 读错文件导致链被覆盖。"""
        if not _hermes_lib:
            return
        try:
            if hasattr(_hermes_lib, 'hermes_set_database_dir'):
                s = path if isinstance(path, str) else path.decode('utf-8')
                buf = s.encode('utf-8') + b'\0'
                _hermes_lib.hermes_set_database_dir(buf)
        except Exception:
            pass

    def delete_updates(self, writer_id: int, keywords: List[str], counts: List[int], file_ids_prev: List[int]) -> bool:
        """对不再适用的旧关键字发送 op=删除 的更新令牌（UpdtTkn 删除 + 服务端 Updt）。"""
        if not self._initialized or not _hermes_lib:
            return False
        if len(keywords) != len(counts) or len(keywords) != len(file_ids_prev) or not keywords:
            return False
        try:
            if not hasattr(_hermes_lib, 'hermes_delete_updates'):
                return False
            n = len(keywords)
            kw_arr = (ctypes.c_char_p * n)(*[k.encode('utf-8') for k in keywords])
            cnt_arr = (ctypes.c_int * n)(*counts)
            prev_arr = (ctypes.c_int * n)(*file_ids_prev)
            return _hermes_lib.hermes_delete_updates(writer_id, kw_arr, cnt_arr, prev_arr, n) == 0
        except Exception:
            return False

    def reload_index_from_database(self) -> bool:
        """
        通知 C++ 服务器从 database 文件重新加载索引，使检索反映已更新的 database 内容。
        返回 True 表示成功。
        """
        if not self._initialized or not _hermes_lib:
            return False
        try:
            if hasattr(_hermes_lib, 'hermes_reload_index'):
                return _hermes_lib.hermes_reload_index() == 0
        except Exception:
            pass
        return False
    
    def _cli_search(self, keyword: str, writer_ids: List[int]) -> Dict:
        """
        通过调用 ../client/client 可执行文件执行搜索，并解析输出。
        目标：与用户在终端运行 ./client -s <kw> <subset_size> 的结果一致。
        """
        try:
            # Hermes 原生 client 只支持 writer_subset_size（前N个writer），不支持任意集合。
            # 同时，server 端真实 writer 数量可能与 web 侧配置不一致（例如 server 只启动了10个writer）。
            # 因此：优先不传 writer_subset_size，让 client 自己向 server 取 num_writers 并搜索全量；
            # 然后再按 writer_ids 过滤结果，保证不因 subset_size 过大而失败。
            if not writer_ids:
                return {"results": []}

            # 定位 client 可执行文件
            candidates = []
            env_client = os.getenv("HERMES_CLIENT_BIN")
            if env_client:
                candidates.append(env_client)

            # 常见路径：Hermes/client/client（从 web_api 目录；兼容 Hermes 或 Hermes-main/Hermes 等结构）
            web_api_dir = os.path.dirname(os.path.abspath(__file__))
            candidates.extend([
                os.path.join(web_api_dir, "..", "client", "client"),
                os.path.join(web_api_dir, "..", "client", "client.exe"),
                os.path.join(web_api_dir, "..", "..", "client", "client"),
                os.path.join(web_api_dir, "..", "..", "client", "client.exe"),
                os.path.join(web_api_dir, "..", "Hermes", "client", "client"),
                os.path.join(web_api_dir, "..", "Hermes", "client", "client.exe"),
            ])

            client_bin = None
            for c in candidates:
                c_path = os.path.abspath(c)
                if os.path.exists(c_path) and os.access(c_path, os.X_OK):
                    client_bin = c_path
                    break

            if not client_bin:
                return {"error": "Hermes 搜索依赖 client 可执行文件未找到。请在 Hermes 目录执行 make client 生成 client/client，或设置环境变量 HERMES_CLIENT_BIN 指向该可执行文件。"}

            # 调用 client（不传第三参：搜索 server 返回的全部 writers）
            # client 连接 tcp://127.0.0.1:8888，且用 ../database 等相对路径，故 cwd 设为 client 所在目录
            cmd = [client_bin, "-s", keyword]
            client_dir = os.path.dirname(client_bin)
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=client_dir,
            )

            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                out = (proc.stdout or "").strip()
                return {"error": f"client search failed (code={proc.returncode}). stderr={err[:500]} stdout={out[-500:]}"}

            output = proc.stdout.splitlines()

            # 解析形如：
            # Writer 1: 413 230 201
            # Writer 2: no matched documents.
            writer_line_re = re.compile(r"^Writer\s+(\d+):\s*(.*)$")
            results_map: Dict[int, List[int]] = {}

            for line in output:
                m = writer_line_re.match(line.strip())
                if not m:
                    continue
                wid_1based = int(m.group(1))
                rest = m.group(2).strip()
                if "no matched documents" in rest:
                    results_map[wid_1based] = []
                    continue
                ids = []
                for tok in rest.split():
                    try:
                        ids.append(int(tok))
                    except ValueError:
                        pass
                results_map[wid_1based] = ids

            # 按请求 writer_ids 过滤并输出（如果前端选择的 writer 超过 server 实际数量，则自然返回空）
            wanted_1based = set([w + 1 for w in writer_ids])
            results = []
            for wid_1based in sorted(wanted_1based):
                results.append({
                    "writer_id": wid_1based,
                    "file_ids": results_map.get(wid_1based, []),
                })

            return {"results": results}
        except subprocess.TimeoutExpired:
            return {"error": "client search timed out (60s). Check if Hermes server is running and responsive."}
        except Exception as e:
            return {"error": f"cli search exception: {str(e)}"}
    
    def get_encrypted_document(self, writer_id: int, file_id: int) -> Optional[tuple[bytes, bytes]]:
        """
        仅获取加密文档（密文 + IV），不解密。
        
        Args:
            writer_id: 写入者ID
            file_id: 文件ID
            
        Returns:
            (ciphertext, iv) 或 None
        """
        if not _hermes_lib:
            return None
        try:
            _hermes_lib.hermes_get_encrypted_document.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_ubyte * 16),
            ]
            _hermes_lib.hermes_get_encrypted_document.restype = ctypes.c_int
            ciphertext_ptr = ctypes.POINTER(ctypes.c_ubyte)()
            ciphertext_len = ctypes.c_int()
            iv_array = (ctypes.c_ubyte * 16)()
            result = _hermes_lib.hermes_get_encrypted_document(
                writer_id, file_id,
                ctypes.byref(ciphertext_ptr),
                ctypes.byref(ciphertext_len),
                ctypes.byref(iv_array),
            )
            if result != 0:
                return None
            ciphertext = bytes(ctypes.string_at(ciphertext_ptr, ciphertext_len.value))
            iv = bytes(iv_array)
            _hermes_lib.hermes_free_buffer(ciphertext_ptr)
            return (ciphertext, iv)
        except Exception:
            return None

    def get_document(self, writer_id: int, file_id: int) -> Optional[bytes]:
        """
        获取并解密文档内容
        
        Args:
            writer_id: 写入者ID
            file_id: 文件ID
            
        Returns:
            解密后的文档内容（字节），失败返回None
        """
        # 如果C++库未初始化，尝试直接从文件读取（模拟模式）
        if not self._initialized or not _hermes_lib:
            return self._get_document_from_file(writer_id, file_id)
        
        try:
            # 定义函数签名
            _hermes_lib.hermes_get_encrypted_document.argtypes = [
                ctypes.c_int,  # writer_id
                ctypes.c_int,  # file_id
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),  # ciphertext
                ctypes.POINTER(ctypes.c_int),  # len
                ctypes.POINTER(ctypes.c_ubyte * 16)  # iv
            ]
            _hermes_lib.hermes_get_encrypted_document.restype = ctypes.c_int
            
            _hermes_lib.hermes_decrypt_document.argtypes = [
                ctypes.POINTER(ctypes.c_ubyte),  # ciphertext
                ctypes.c_int,  # ciphertext_len
                ctypes.c_int,  # writer_id
                ctypes.c_int,  # file_id
                ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),  # plaintext
                ctypes.POINTER(ctypes.c_int),  # plaintext_len
                ctypes.POINTER(ctypes.c_ubyte * 16)  # iv
            ]
            _hermes_lib.hermes_decrypt_document.restype = ctypes.c_int
            
            _hermes_lib.hermes_free_buffer.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
            _hermes_lib.hermes_free_buffer.restype = None
            
            # 获取加密文档
            ciphertext_ptr = ctypes.POINTER(ctypes.c_ubyte)()
            ciphertext_len = ctypes.c_int()
            iv_array = (ctypes.c_ubyte * 16)()
            
            result = _hermes_lib.hermes_get_encrypted_document(
                writer_id, file_id, 
                ctypes.byref(ciphertext_ptr), 
                ctypes.byref(ciphertext_len),
                ctypes.byref(iv_array)
            )
            
            if result != 0:
                return None
            
            # 解密文档
            plaintext_ptr = ctypes.POINTER(ctypes.c_ubyte)()
            plaintext_len = ctypes.c_int()
            
            result = _hermes_lib.hermes_decrypt_document(
                ciphertext_ptr,
                ciphertext_len,
                writer_id,
                file_id,
                ctypes.byref(plaintext_ptr),
                ctypes.byref(plaintext_len),
                ctypes.byref(iv_array)
            )
            
            # 释放加密文档内存
            _hermes_lib.hermes_free_buffer(ciphertext_ptr)
            
            if result != 0:
                return None
            
            # 复制明文数据
            plaintext_bytes = bytes(ctypes.string_at(plaintext_ptr, plaintext_len.value))
            
            # 释放明文内存
            _hermes_lib.hermes_free_buffer(plaintext_ptr)
            
            return plaintext_bytes
            
        except Exception as e:
            print(f"Error getting document: {e}")
            # 如果C++库失败，尝试从文件读取
            return self._get_document_from_file(writer_id, file_id)
    
    def _get_document_from_file(self, writer_id: int, file_id: int) -> Optional[bytes]:
        """
        从文件系统直接读取并解密文档（备用方法）
        
        Args:
            writer_id: 写入者ID
            file_id: 文件ID
            
        Returns:
            解密后的文档内容，失败返回None
        """
        try:
            import hashlib
            from pathlib import Path
            import os
            
            # 尝试多个可能的路径
            possible_paths = [
                # 相对于脚本文件的路径
                Path(__file__).parent.parent / "encrypted_docs",
                # 相对于当前工作目录
                Path("../encrypted_docs"),
                Path("encrypted_docs"),
                # 绝对路径（从web_api目录）
                Path(__file__).parent.parent.absolute() / "encrypted_docs",
                # 从Hermes目录
                Path(__file__).parent.parent.parent / "encrypted_docs" if Path(__file__).parent.parent.parent.name == "Hermes" else None,
            ]
            
            # 过滤掉None值
            possible_paths = [p for p in possible_paths if p is not None]
            
            enc_file = None
            for encrypted_docs_dir in possible_paths:
                test_file = encrypted_docs_dir / f"{writer_id}_{file_id}.enc"
                if test_file.exists():
                    enc_file = test_file
                    break
            
            if enc_file is None:
                # 尝试从环境变量或配置获取路径
                env_path = os.getenv('HERMES_ENCRYPTED_DOCS_DIR')
                if env_path:
                    enc_file = Path(env_path) / f"{writer_id}_{file_id}.enc"
                    if not enc_file.exists():
                        enc_file = None
            
            if enc_file is None or not enc_file.exists():
                # 列出所有可能的路径供调试
                print(f"加密文档不存在: writer_id={writer_id}, file_id={file_id}")
                print(f"尝试的路径:")
                for path in possible_paths:
                    if path.exists():
                        print(f"  - {path} (存在)")
                        # 列出该目录下的一些文件示例
                        try:
                            files = list(path.glob(f"{writer_id}_*.enc"))[:5]
                            if files:
                                print(f"    示例文件: {[f.name for f in files]}")
                        except:
                            pass
                    else:
                        print(f"  - {path} (不存在)")
                return None
            
            # 读取加密文档
            with open(enc_file, 'rb') as f:
                iv = f.read(16)
                len_bytes = f.read(4)
                ciphertext_len = int.from_bytes(len_bytes, 'big')
                ciphertext = f.read(ciphertext_len)
            
            # 派生解密密钥
            key_seed = f"{writer_id}_{file_id}".encode('utf-8')
            key_hash = hashlib.sha512(key_seed).digest()
            decryption_key = key_hash[:32]
            
            # 尝试使用cryptography库解密
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                from cryptography.hazmat.backends import default_backend
                
                cipher = Cipher(algorithms.AES(decryption_key), modes.CTR(iv), backend=default_backend())
                decryptor = cipher.decryptor()
                plaintext = decryptor.update(ciphertext) + decryptor.finalize()
                return plaintext
            except ImportError:
                # 如果没有cryptography库，使用简化解密（XOR）
                plaintext = bytearray(ciphertext)
                for i in range(len(plaintext)):
                    plaintext[i] ^= decryption_key[i % 32]
                return bytes(plaintext)
                
        except Exception as e:
            print(f"Error reading document from file: {e}")
            return None
    
    def cleanup(self):
        """清理资源"""
        if self._initialized and _hermes_lib:
            _hermes_lib.hermes_cleanup()
            self._initialized = False
    
    def __del__(self):
        """析构函数"""
        self.cleanup()


