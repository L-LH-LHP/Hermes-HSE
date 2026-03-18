#include "hermes_client_api.hpp"
#include "document_storage.hpp"
#include <sstream>
#include <cstring>
#include <mutex>
#include <fstream>
#include <array>
#include <openssl/sha.h>

using namespace std;
// 定义PRG全局变量（hickae.hpp中声明为extern）
PRG prg;
bool g_hickae_in_reload = false;  // 仅 server 在 reload 时设为 true


// 全局错误信息
static string last_error;
static mutex error_mutex;

// 全局客户端实例
static HermesClientImpl* g_client = nullptr;
static mutex client_mutex;

// 搜索结果使用静态缓冲区返回，避免 malloc 指针交给 Python 释放导致 munmap_chunk (不同运行时/堆)
static string g_search_result_buffer;
static const char* g_last_search_ptr = nullptr;

// database 目录路径；若由 Flask 设置则 load_update_state 与写入的 database 一致，避免增量添加时用错 count 覆盖链
static string g_database_dir;
static mutex g_database_dir_mutex;

// 辅助函数：设置错误信息
static void set_error(const string& err) {
    lock_guard<mutex> lock(error_mutex);
    last_error = err;
}

// HermesClientImpl实现
HermesClientImpl::HermesClientImpl() : 
    context_client(nullptr), socket_client(nullptr), 
    num_writers(0), epoch(1), initialized(false) {
}

HermesClientImpl::~HermesClientImpl() {
    cleanup();
}

bool HermesClientImpl::init(int n_writers, const string& server_address) {
    try {
        num_writers = n_writers;
        epoch = 1;
        encoded_epoch = "";
        
        // 连接服务器
        context_client = new zmq::context_t(1);
        socket_client = new zmq::socket_t(*context_client, ZMQ_REQ);
        // 设置收发超时(毫秒)，避免 server 未启动时无限阻塞导致进程无响应
        int recv_timeout = 15000;
        int send_timeout = 5000;
        socket_client->setsockopt(ZMQ_RCVTIMEO, &recv_timeout, sizeof(recv_timeout));
        socket_client->setsockopt(ZMQ_SNDTIMEO, &send_timeout, sizeof(send_timeout));
        socket_client->connect(server_address);
        
        // 获取写入者数量
        zmq::message_t msg_get_num_writers(1);
        *((uint8_t*)msg_get_num_writers.data()) = 'G';
        socket_client->send(msg_get_num_writers);
        
        zmq::message_t msg_reply_num_writers;
        socket_client->recv(&msg_reply_num_writers);
        memcpy(&num_writers, msg_reply_num_writers.data(), 4);
        
        // 初始化系统
        HICKAE_Setup(num_writers);
        HICKAE_KeyGen();
        HICKAE_IGen(num_writers);
        HICKAE_Prep(num_writers);
        set_epoch(1);
        initialized = true;
        return true;
    } catch (exception& e) {
        set_error("Initialization failed: " + string(e.what()));
        return false;
    }
}

string HermesClientImpl::search(const string& keyword, vector<int>& writer_subset) {
    if (!initialized) {
        return "{\"error\":\"System not initialized\"}";
    }
    
    try {
        // 资源约定：所有 PEKS_AggKey 由 HICKAE_Extract 内 mpz_init/element_init_G1 初始化；
        // 必须在 delete/退栈前对 k1,k2,k3 做 mpz_clear/element_clear，且 cw_bytes (new[]) 必须 delete[]，避免泄漏与 munmap_chunk。
        // 创建分区匹配搜索令牌（避免 to_string(pid).c_str() 临时对象悬垂指针导致崩溃）
#ifdef SEARCH_EFFICIENCY
        PEKS_AggKey cp[RECURSIVE_LEVEL];
        int num_partitions = NUM_PARTITIONS;
        array<uint64_t, 2> hash_value = mm_hash((uint8_t*)keyword.c_str(), keyword.length());
        uint64_t pid = ((hash_value[0] % num_partitions) << 2) | RECURSIVE_LEVEL;
        { string pid_str = to_string(pid); HICKAE_Extract(writer_subset, (char*)pid_str.c_str(), &cp[RECURSIVE_LEVEL - 1]); }
        
        for(int k = 1; k < RECURSIVE_LEVEL; ++k) {
            num_partitions /= PARTITION_SIZE;
            hash_value = mm_hash((uint8_t*)&pid, sizeof(pid));
            pid = ((hash_value[0] % num_partitions) << 2) | (RECURSIVE_LEVEL - k);
            { string pid_str = to_string(pid); HICKAE_Extract(writer_subset, (char*)pid_str.c_str(), &cp[RECURSIVE_LEVEL - 1 - k]); }
        }
#else 
        PEKS_AggKey cp;
        array<uint64_t, 2> hash_value = mm_hash((uint8_t*)keyword.c_str(), keyword.length());
        uint64_t pid = hash_value[0] % MAX_PARTITIONS;
        { string pid_str = to_string(pid); HICKAE_Extract(writer_subset, (char*)pid_str.c_str(), &cp); }
#endif 
        
        // 创建关键词匹配搜索令牌
#ifdef WRITER_EFFICIENCY
        vector<string> children_epochs;
        string padded_encoded_epoch = encoded_epoch;
        padded_encoded_epoch.insert(padded_encoded_epoch.end(), DEPTH_EPOCH_TREE - padded_encoded_epoch.size(), '0');
        children_epochs.push_back(padded_encoded_epoch);

        for(int i = encoded_epoch.length() - 1; i >= 0; --i) {
            string temp = encoded_epoch.substr(0, i);
            temp.insert(temp.end(), DEPTH_EPOCH_TREE - temp.size(), '0');
            children_epochs.push_back(temp);
        }
        
        PEKS_AggKey *cw = new PEKS_AggKey[children_epochs.size()];
        string *id = new string[children_epochs.size()];

        for(int i = 0; i < children_epochs.size(); ++i) {
            cw[i].eepoch = children_epochs[i];
            id[i] = keyword + children_epochs[i];
        }

        HICKAE_Extract(writer_subset, id, cw, children_epochs.size());
#else 
        string id = keyword + to_string(epoch);
        PEKS_AggKey cw;
        HICKAE_Extract(writer_subset, (char*)id.c_str(), &cw);
#endif 

        // 发送搜索查询
        size_t temp;
        
#ifdef SEARCH_EFFICIENCY
        uint8_t cp_bytes[MAX_TOKEN_SIZE * RECURSIVE_LEVEL];
        size_t cp_size = 0;

        for(int i = 0; i < RECURSIVE_LEVEL; ++i) {
            mpz_export(cp_bytes + cp_size + sizeof(size_t), &temp, 1, 1, 0, 0, cp[i].k1);
            memcpy(cp_bytes + cp_size, &temp, sizeof(size_t));
            cp_size += sizeof(size_t);
            cp_size += temp;
            temp = element_to_bytes(cp_bytes + cp_size, cp[i].k2);
            cp_size += temp;
            temp = element_to_bytes(cp_bytes + cp_size, cp[i].k3);
            cp_size += temp;
        }
        for (int i = 0; i < RECURSIVE_LEVEL; ++i) {
            mpz_clear(cp[i].k1);
            element_clear(cp[i].k2);
            element_clear(cp[i].k3);
        }
#else 
        uint8_t cp_bytes[MAX_TOKEN_SIZE];
        size_t cp_size = sizeof(size_t);
        mpz_export(cp_bytes + cp_size, &temp, 1, 1, 0, 0, cp.k1);
        memcpy(cp_bytes, &temp, sizeof(size_t));
        cp_size += temp;
        temp = element_to_bytes(cp_bytes + cp_size, cp.k2);
        cp_size += temp;
        temp = element_to_bytes(cp_bytes + cp_size, cp.k3);
        cp_size += temp;
        mpz_clear(cp.k1);
        element_clear(cp.k2);
        element_clear(cp.k3);
#endif 

#ifdef WRITER_EFFICIENCY
        uint8_t *cw_bytes = new uint8_t[(MAX_TOKEN_SIZE + DEPTH_EPOCH_TREE) * children_epochs.size()];
        size_t cw_size = 0;
        for(int i = 0; i < children_epochs.size(); ++i) {
            mpz_export(cw_bytes + cw_size + sizeof(size_t), &temp, 1, 1, 0, 0, cw[i].k1);
            memcpy(cw_bytes + cw_size, &temp, sizeof(size_t));
            cw_size += sizeof(size_t);
            cw_size += temp;
            temp = element_to_bytes(cw_bytes + cw_size, cw[i].k2);
            cw_size += temp;
            temp = element_to_bytes(cw_bytes + cw_size, cw[i].k3);
            cw_size += temp;
            memcpy(cw_bytes + cw_size, cw[i].eepoch.c_str(), DEPTH_EPOCH_TREE);
            cw_size += DEPTH_EPOCH_TREE;
        }
        for (size_t i = 0; i < children_epochs.size(); ++i) {
            mpz_clear(cw[i].k1);
            element_clear(cw[i].k2);
            element_clear(cw[i].k3);
        }
        delete [] cw;
        delete [] id;
#else
        uint8_t cw_bytes[MAX_TOKEN_SIZE];
        size_t cw_size = sizeof(size_t);
        mpz_export(cw_bytes + cw_size, &temp, 1, 1, 0, 0, cw.k1);
        memcpy(cw_bytes, &temp, sizeof(size_t));
        cw_size += temp;
        temp = element_to_bytes(cw_bytes + cw_size, cw.k2);
        cw_size += temp;
        temp = element_to_bytes(cw_bytes + cw_size, cw.k3);
        cw_size += temp;
        mpz_clear(cw.k1);
        element_clear(cw.k2);
        element_clear(cw.k3);
#endif 

#ifdef WRITER_EFFICIENCY
        size_t subset_array_size = writer_subset.size() * sizeof(int);
        zmq::message_t search_query(1 + sizeof(int) + subset_array_size + cp_size + sizeof(int) + cw_size);
#else 
        size_t subset_array_size = writer_subset.size() * sizeof(int);
        zmq::message_t search_query(1 + sizeof(int) + subset_array_size + cp_size + cw_size);
#endif 
        uint8_t *search_query_data = (uint8_t*)search_query.data();
        search_query_data[0] = 'S';
        int writer_subset_size = writer_subset.size();
        memcpy(search_query_data + 1, &writer_subset_size, sizeof(int));
        
        uint8_t *p = search_query_data + 1 + sizeof(int);
        if (writer_subset_size > 0) {
            memcpy(p, writer_subset.data(), subset_array_size);
        }
        p += subset_array_size;
        
        memcpy(p, cp_bytes, cp_size);
        p += cp_size;

#ifdef WRITER_EFFICIENCY
        int n = children_epochs.size();
        memcpy(p, &n, sizeof(int)); 
        p += sizeof(int);
        memcpy(p, cw_bytes, cw_size); 
        delete[] cw_bytes;
        cw_bytes = nullptr;
#else 
        memcpy(p, cw_bytes, cw_size); 
#endif 

        socket_client->send(search_query, zmq::send_flags::dontwait);

        // 接收搜索结果（阻塞等待，使用已设置的 ZMQ_RCVTIMEO，避免 dontwait 导致空回复触发 "reply too short"）
        zmq::message_t search_outcome;
        socket_client->recv(search_outcome);
        size_t outcome_size = search_outcome.size();

        // 空 writer 或无数据时直接返回空结果，避免对空/过小缓冲区解析导致段错误
        if (writer_subset.empty()) {
            return "{\"results\":[]}";
        }
        size_t min_size = writer_subset.size() * sizeof(int);
        if (outcome_size < min_size) {
            if (outcome_size == 0) {
                set_error("Search reply empty (server timeout or not responding)");
                return "{\"error\":\"检索服务无响应或超时，请确认 C++ server 已启动 (tcp://127.0.0.1:8888) 并重试\"}";
            }
            set_error("Search reply too short or invalid");
            return "{\"error\":\"Search reply too short or server error\"}";
        }

        const uint8_t *search_outcome_data = (const uint8_t*)search_outcome.data();
        const uint8_t *search_outcome_end = search_outcome_data + outcome_size;

        ostringstream json;
        json << "{\"results\":[";
        int file_id;
        int count = 0;
        bool first_writer = true;

        for (int writer_id : writer_subset) {
            if (!first_writer) json << ",";
            first_writer = false;
            json << "{\"writer_id\":" << (writer_id + 1) << ",\"file_ids\":[";

            if (search_outcome_data + sizeof(int) > search_outcome_end) break;
            memcpy(&count, search_outcome_data, sizeof(int));
            search_outcome_data += sizeof(int);
            if (count < 0 || count > (int)(MAX_MATCH_OUTPUT)) count = 0;
            size_t need = (size_t)count * sizeof(int);
            if (search_outcome_data + need > search_outcome_end)
                count = (int)((search_outcome_end - search_outcome_data) / sizeof(int));

            bool first_file = true;
            for (int k = 0; k < count; ++k) {
                if (!first_file) json << ",";
                first_file = false;
                memcpy(&file_id, search_outcome_data, sizeof(int));
                json << file_id;
                search_outcome_data += sizeof(int);
            }
            json << "]}";
        }
        json << "]}";
        return json.str();
        
    } catch (const exception& e) {
        set_error("Search failed: " + string(e.what()));
        return "{\"error\":\"Search failed (check server and logs)\"}";
    } catch (...) {
        set_error("Search failed: unknown exception");
        return "{\"error\":\"Search failed: internal error (check C++ server is running on tcp://127.0.0.1:8888)\"}";
    }
}

bool HermesClientImpl::set_epoch(int e) {
    epoch = static_cast<uint64_t>(e);
#ifdef WRITER_EFFICIENCY
    encoded_epoch = "";
    for (int i = 1; i <= e - 1; ++i)
        encoded_epoch = encode_epoch(encoded_epoch);
#endif
    return true;
}

bool HermesClientImpl::load_update_state_impl(int writer_id) {
    string dir;
    {
        lock_guard<mutex> lock(g_database_dir_mutex);
        dir = g_database_dir.empty() ? "../database/" : g_database_dir;
    }
    if (!dir.empty() && dir.back() != '/' && dir.back() != '\\')
        dir += '/';
    string path = dir + to_string(writer_id + 1) + ".txt";
    ifstream file(path);
    if (!file.is_open())
        return false;
    string line, keyword;
    update_state_[writer_id].clear();
    while (getline(file, line)) {
        stringstream wss(line);
        wss >> keyword;
        istringstream iss(line.substr(keyword.length() + 1));
        int fid;
        int n = 0;
        while (iss >> fid) n++;
        update_state_[writer_id][keyword] = (n > 0) ? (uint64_t)(n - 1) : 0;
    }
    file.close();
    return true;
}

bool HermesClientImpl::load_update_state(int writer_id) {
    return load_update_state_impl(writer_id);
}

bool HermesClientImpl::update(int writer_id, const string& keyword, int file_id) {
    if (!initialized) {
        set_error("System not initialized");
        return false;
    }
    vector<string> kw = {keyword};
    vector<int> fid = {file_id};
    return batch_update(writer_id, kw, fid);
}

bool HermesClientImpl::batch_update(int writer_id, const vector<string>& keywords, const vector<int>& file_ids) {
    if (!initialized) {
        set_error("System not initialized");
        return false;
    }
    if (keywords.size() != file_ids.size() || keywords.empty()) {
        set_error("keywords and file_ids size mismatch or empty");
        return false;
    }
    // When state is empty (e.g. after clear_writer+reset_update_state), do NOT load from file:
    // we must use count 0,1,2,... so the server chain matches. Loading would set count = current
    // size and break the chain so search returns stale results.
    // if (update_state_[writer_id].empty() && !load_update_state(writer_id))
    //     load_update_state(writer_id);

    unsigned char writer_secret_key[32];
    prg.reseed((block*)"generaterwritersecretkeys", writer_id + 1);
    prg.random_block((block*)writer_secret_key, 2);

    unsigned char token[32], prev_token[32];
    SHA512_CTX sha512;
    unsigned char tmp[SHA512_DIGEST_LENGTH];
    char addr_buf[21];

#ifdef WRITER_EFFICIENCY
    vector<string> gamma_t;
    string padded_encoded_epoch = encoded_epoch;
    padded_encoded_epoch.insert(padded_encoded_epoch.end(), DEPTH_EPOCH_TREE - padded_encoded_epoch.size(), '0');
    gamma_t.push_back(padded_encoded_epoch);
    for (int i = DEPTH_EPOCH_TREE - 1; i >= 0; --i) {
        if (i < (int)encoded_epoch.length() && (encoded_epoch.substr(0, i) + "1") == encoded_epoch.substr(0, i + 1)) {
            string temp = encoded_epoch.substr(0, i) + "2";
            temp.insert(temp.end(), DEPTH_EPOCH_TREE - (int)temp.size(), '0');
            gamma_t.push_back(temp);
        }
    }
    size_t n = gamma_t.size();
    const size_t num_updates = keywords.size();
    zmq::message_t update_query(17 + (UPDATE_TOKEN_SIZE + (541 + DEPTH_EPOCH_TREE) * n) * num_updates);
#else
    const size_t num_updates = keywords.size();
    zmq::message_t update_query(9 + UPDATE_TOKEN_SIZE * num_updates);
#endif

    uint8_t* update_query_data = (uint8_t*)update_query.data();
    int num_updates_i = (int)num_updates;
    update_query_data[0] = 'U';
    update_query_data += 1;
    memcpy(update_query_data, &writer_id, 4);
    update_query_data += 4;
    memcpy(update_query_data, &num_updates_i, 4);
    update_query_data += 4;
#ifdef WRITER_EFFICIENCY
    memcpy(update_query_data, &n, sizeof(size_t));
    update_query_data += sizeof(size_t);
#endif

    for (size_t idx = 0; idx < num_updates; ++idx) {
        const string& keyword = keywords[idx];
        int file_id = file_ids[idx];
        uint64_t count = update_state_[writer_id][keyword];

        string seed;
        if (count == 0)
            memset(prev_token, 0, sizeof(prev_token));
        else {
            seed = keyword + to_string(count - 1);
            prf((unsigned char*)seed.c_str(), (int)seed.length(), writer_secret_key, prev_token);
        }
        seed = keyword + to_string(count);
        prf((unsigned char*)seed.c_str(), (int)seed.length(), writer_secret_key, token);

        SHA512_Init(&sha512);
        SHA512_Update(&sha512, token, 16);
        SHA512_Final(tmp, &sha512);
        memset(addr_buf, 0, sizeof(addr_buf));
        for (int j = 0; j < 10; ++j)
            sprintf(addr_buf + j * 2, "%02x", tmp[j]);

        DSSE_Token value;
        memset(value, 0, sizeof(value));
        if (file_id == -1) {
            value[0] = 0; // tombstone
        } else {
            value[0] = 1;
        }
        memcpy((uint8_t*)value + 1, &file_id, sizeof(int));
        memcpy((uint8_t*)value + 5, prev_token, 32);
        for (int j = 0; j < 37; ++j)
            value[j] ^= tmp[j + 10];

        memcpy(update_query_data, addr_buf, 20);
        update_query_data += 20;
        memcpy(update_query_data, value, 37);
        update_query_data += 37;

#ifdef SEARCH_EFFICIENCY
        int num_partitions = NUM_PARTITIONS;
        array<uint64_t, 2> hash_value = mm_hash((uint8_t*)keyword.c_str(), keyword.length());
        uint64_t pid = ((hash_value[0] % num_partitions) << 2) | RECURSIVE_LEVEL;
        unsigned char partition_tag[32];
        prf((unsigned char*)&pid, sizeof(pid), writer_secret_key, partition_tag);
        for (int j = 0; j < 10; ++j)
            sprintf(addr_buf + j * 2, "%02x", partition_tag[j]);
        string paddr(addr_buf, 20);
        memcpy(update_query_data, paddr.c_str(), 20);
        update_query_data += 20;
        PEKS_Token eptkn;
        { string pid_str = to_string(pid); HICKAE_Encrypt(writer_id, (char*)pid_str.c_str(), partition_tag, &eptkn); }
        element_to_bytes(update_query_data, eptkn.c1);
        update_query_data += 168;
        element_to_bytes(update_query_data, eptkn.c2);
        update_query_data += 168;
        element_to_bytes(update_query_data, eptkn.c3);
        update_query_data += 168;
        memcpy(update_query_data, eptkn.c4, 37);
        update_query_data += 37;
        element_clear(eptkn.c1);
        element_clear(eptkn.c2);
        element_clear(eptkn.c3);
#else
        array<uint64_t, 2> hash_value = mm_hash((uint8_t*)keyword.c_str(), keyword.length());
        uint64_t pid = hash_value[0] % MAX_PARTITIONS;
        unsigned char partition_tag[32];
        prf((unsigned char*)&pid, sizeof(pid), writer_secret_key, partition_tag);
        for (int j = 0; j < 10; ++j)
            sprintf(addr_buf + j * 2, "%02x", partition_tag[j]);
        string paddr(addr_buf, 20);
        memcpy(update_query_data, paddr.c_str(), 20);
        update_query_data += 20;
        PEKS_Token eptkn;
        { string pid_str = to_string(pid); HICKAE_Encrypt(writer_id, (char*)pid_str.c_str(), partition_tag, &eptkn); }
        element_to_bytes(update_query_data, eptkn.c1);
        update_query_data += 168;
        element_to_bytes(update_query_data, eptkn.c2);
        update_query_data += 168;
        element_to_bytes(update_query_data, eptkn.c3);
        update_query_data += 168;
        memcpy(update_query_data, eptkn.c4, 37);
        update_query_data += 37;
        element_clear(eptkn.c1);
        element_clear(eptkn.c2);
        element_clear(eptkn.c3);
#endif

#ifdef WRITER_EFFICIENCY
        Encrypted_Search_Token ewtkn;
        for (size_t k = 0; k < gamma_t.size(); ++k) {
            string id = keyword + gamma_t[k];
            PEKS_Token ewtkn_data;
            HICKAE_Encrypt(writer_id, (char*)id.c_str(), token, &ewtkn_data);
            element_init_G2(ewtkn.data[gamma_t[k]].c1, pairing);
            element_set(ewtkn.data[gamma_t[k]].c1, ewtkn_data.c1);
            element_init_G2(ewtkn.data[gamma_t[k]].c2, pairing);
            element_set(ewtkn.data[gamma_t[k]].c2, ewtkn_data.c2);
            element_init_G2(ewtkn.data[gamma_t[k]].c3, pairing);
            element_set(ewtkn.data[gamma_t[k]].c3, ewtkn_data.c3);
            memcpy(ewtkn.data[gamma_t[k]].c4, ewtkn_data.c4, 37);
            element_clear(ewtkn_data.c1);
            element_clear(ewtkn_data.c2);
            element_clear(ewtkn_data.c3);
        }
        for (size_t i = 0; i < gamma_t.size(); ++i) {
            memcpy(update_query_data, gamma_t[i].c_str(), DEPTH_EPOCH_TREE);
            update_query_data += DEPTH_EPOCH_TREE;
            element_to_bytes(update_query_data, ewtkn.data[gamma_t[i]].c1);
            update_query_data += 168;
            element_to_bytes(update_query_data, ewtkn.data[gamma_t[i]].c2);
            update_query_data += 168;
            element_to_bytes(update_query_data, ewtkn.data[gamma_t[i]].c3);
            update_query_data += 168;
            memcpy(update_query_data, ewtkn.data[gamma_t[i]].c4, 37);
            update_query_data += 37;
        }
        for (size_t i = 0; i < gamma_t.size(); ++i) {
            element_clear(ewtkn.data[gamma_t[i]].c1);
            element_clear(ewtkn.data[gamma_t[i]].c2);
            element_clear(ewtkn.data[gamma_t[i]].c3);
        }
#else
        string id = keyword + to_string(epoch);
        PEKS_Token ewtkn;
        HICKAE_Encrypt(writer_id, (char*)id.c_str(), token, &ewtkn);
        element_to_bytes(update_query_data, ewtkn.c1);
        update_query_data += 168;
        element_to_bytes(update_query_data, ewtkn.c2);
        update_query_data += 168;
        element_to_bytes(update_query_data, ewtkn.c3);
        update_query_data += 168;
        memcpy(update_query_data, ewtkn.c4, 37);
        update_query_data += 37;
        element_clear(ewtkn.c1);
        element_clear(ewtkn.c2);
        element_clear(ewtkn.c3);
#endif
        update_state_[writer_id][keyword]++;
    }

    try {
        socket_client->send(update_query);
        zmq::message_t update_reply;
        socket_client->recv(&update_reply);
        return true;
    } catch (exception& e) {
        set_error("Update send/recv failed: " + string(e.what()));
        return false;
    }
}

bool HermesClientImpl::delete_updates(int writer_id, const vector<string>& keywords, const vector<int>& counts, const vector<int>& file_ids_prev) {
    if (!initialized) {
        set_error("System not initialized");
        return false;
    }
    if (keywords.size() != counts.size() || keywords.size() != file_ids_prev.size() || keywords.empty()) {
        set_error("delete_updates: keywords/counts/file_ids_prev size mismatch or empty");
        return false;
    }
    const size_t num_del = keywords.size();
    const size_t DEL_ENTRY_SIZE = 20 + 37;
    zmq::message_t del_query(1 + 4 + 4 + num_del * DEL_ENTRY_SIZE);
    uint8_t* p = (uint8_t*)del_query.data();
    p[0] = 'D';
    memcpy(p + 1, &writer_id, 4);
    int num_del_i = (int)num_del;
    memcpy(p + 5, &num_del_i, 4);
    p += 9;

    unsigned char writer_secret_key[32];
    prg.reseed((block*)"generaterwritersecretkeys", writer_id + 1);
    prg.random_block((block*)writer_secret_key, 2);

    unsigned char token[32], prev_token[32];
    SHA512_CTX sha512;
    unsigned char tmp[SHA512_DIGEST_LENGTH];
    char addr_buf[21];
    DSSE_Token value_tombstone;

    for (size_t idx = 0; idx < num_del; ++idx) {
        const string& keyword = keywords[idx];
        int cnt = counts[idx];

        // 找到该节点的地址
        string seed = keyword + to_string(cnt);
        prf((unsigned char*)seed.c_str(), (int)seed.length(), writer_secret_key, token);
        SHA512_Init(&sha512);
        SHA512_Update(&sha512, token, 16);
        SHA512_Final(tmp, &sha512);
        memset(addr_buf, 0, sizeof(addr_buf));
        for (int j = 0; j < 10; ++j) sprintf(addr_buf + j * 2, "%02x", tmp[j]);
        memcpy(p, addr_buf, 20);
        p += 20;

        // 生成前驱的 token，保证链不断
        if (cnt == 0) {
            memset(prev_token, 0, sizeof(prev_token));
        } else {
            string seed_prev = keyword + to_string(cnt - 1);
            prf((unsigned char*)seed_prev.c_str(), (int)seed_prev.length(), writer_secret_key, prev_token);
        }

        memset(value_tombstone, 0, sizeof(value_tombstone));
        value_tombstone[0] = 0; // op=删除
        memcpy((uint8_t*)value_tombstone + 5, prev_token, 32); // 指向旧的前驱节点
        for (int j = 0; j < 37; ++j) value_tombstone[j] ^= tmp[j + 10];
        
        memcpy(p, value_tombstone, 37);
        p += 37;
    }

    try {
        socket_client->send(del_query);
        zmq::message_t reply;
        socket_client->recv(&reply);
        return true;
    } catch (exception& e) {
        set_error("Delete updates send/recv failed: " + string(e.what()));
        return false;
    }
}

bool HermesClientImpl::clear_writer(int writer_id) {
    if (!initialized) {
        set_error("System not initialized");
        return false;
    }
    try {
        zmq::message_t msg(5);
        uint8_t* p = (uint8_t*)msg.data();
        p[0] = 'C';
        memcpy(p + 1, &writer_id, 4);
        socket_client->send(msg);
        zmq::message_t reply;
        socket_client->recv(&reply);
        return reply.size() >= 2 && memcmp(reply.data(), "OK", 2) == 0;
    } catch (exception& e) {
        set_error("Clear writer failed: " + string(e.what()));
        return false;
    }
}

void HermesClientImpl::reset_update_state(int writer_id) {
    update_state_[writer_id].clear();
}

bool HermesClientImpl::reload_index() {
    if (!initialized) {
        set_error("System not initialized");
        return false;
    }
    try {
        zmq::message_t msg(1);
        *((uint8_t*)msg.data()) = 'I';
        socket_client->send(msg);
        zmq::message_t reply;
        socket_client->recv(&reply);
        if (reply.size() >= 3 && memcmp(reply.data(), "RST", 3) == 0) {
            set_error("请重启 C++ server 以加载最新索引");
            return false;
        }
        return true;
    } catch (exception& e) {
        set_error("Reload index failed: " + string(e.what()));
        return false;
    }
}

void HermesClientImpl::cleanup() {
    if (socket_client) {
        delete socket_client;
        socket_client = nullptr;
    }
    if (context_client) {
        delete context_client;
        context_client = nullptr;
    }
    initialized = false;
}

// C API实现（显式导出符号，确保 libhermes_client.so 对 ctypes 可见）
extern "C" {

#if defined(__GNUC__) || defined(__clang__)
#define HERMES_C_API __attribute__((visibility("default")))
#else
#define HERMES_C_API
#endif

HERMES_C_API int hermes_init_system(int num_writers, const char* server_address) {
    lock_guard<mutex> lock(client_mutex);
    
    if (g_client) {
        g_client->cleanup();
        delete g_client;
    }
    
    g_client = new HermesClientImpl();
    if (g_client->init(num_writers, string(server_address))) {
        return 0;
    }
    return -1;
}

HERMES_C_API const char* hermes_search(const char* keyword, int* writer_ids, int num_writers) {
    lock_guard<mutex> lock(client_mutex);

    if (!g_client) {
        set_error("Client not initialized");
        g_search_result_buffer = "{\"error\":\"Client not initialized\"}";
        g_last_search_ptr = g_search_result_buffer.c_str();
        return g_last_search_ptr;
    }
    if (num_writers <= 0) {
        g_search_result_buffer = "{\"results\":[]}";
        g_last_search_ptr = g_search_result_buffer.c_str();
        return g_last_search_ptr;
    }
    if (writer_ids == nullptr) {
        set_error("writer_ids is null");
        g_search_result_buffer = "{\"error\":\"writer_ids is null\"}";
        g_last_search_ptr = g_search_result_buffer.c_str();
        return g_last_search_ptr;
    }

    vector<int> writer_subset(writer_ids, writer_ids + num_writers);
    string result = g_client->search(string(keyword ? keyword : ""), writer_subset);
    g_search_result_buffer = std::move(result);
    g_last_search_ptr = g_search_result_buffer.c_str();
    return g_last_search_ptr;
}

HERMES_C_API int hermes_update(int writer_id, const char* keyword, int file_id) {
    lock_guard<mutex> lock(client_mutex);
    
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    
    if (g_client->update(writer_id, string(keyword), file_id)) {
        return 0;
    }
    return -1;
}

HERMES_C_API int hermes_batch_update(int writer_id, const char** keywords, int* file_ids, int count) {
    lock_guard<mutex> lock(client_mutex);
    
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    
    vector<string> kw_vec;
    vector<int> id_vec;
    
    for (int i = 0; i < count; ++i) {
        kw_vec.push_back(string(keywords[i]));
        id_vec.push_back(file_ids[i]);
    }
    
    if (g_client->batch_update(writer_id, kw_vec, id_vec)) {
        return 0;
    }
    return -1;
}

HERMES_C_API int hermes_set_epoch(int epoch) {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    return g_client->set_epoch(epoch) ? 0 : -1;
}

HERMES_C_API int hermes_reload_index() {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    return g_client->reload_index() ? 0 : -1;
}

HERMES_C_API int hermes_clear_writer(int writer_id) {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    return g_client->clear_writer(writer_id) ? 0 : -1;
}

HERMES_C_API void hermes_reset_update_state(int writer_id) {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) return;
    g_client->reset_update_state(writer_id);
}

HERMES_C_API int hermes_delete_updates(int writer_id, const char** keywords, int* counts, int* file_ids_prev, int count) {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    if (!keywords || !counts || !file_ids_prev || count <= 0) return -1;
    vector<string> kw_vec;
    vector<int> cnt_vec, fid_prev_vec;
    for (int i = 0; i < count; ++i) {
        kw_vec.push_back(string(keywords[i]));
        cnt_vec.push_back(counts[i]);
        fid_prev_vec.push_back(file_ids_prev[i]);
    }
    return g_client->delete_updates(writer_id, kw_vec, cnt_vec, fid_prev_vec) ? 0 : -1;
}

HERMES_C_API int hermes_load_update_state(int writer_id) {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) {
        set_error("Client not initialized");
        return -1;
    }
    return g_client->load_update_state(writer_id) ? 0 : -1;
}

HERMES_C_API void hermes_set_database_dir(const char* path) {
    lock_guard<mutex> lock(g_database_dir_mutex);
    g_database_dir = path ? path : "";
}

HERMES_C_API int hermes_get_num_writers() {
    lock_guard<mutex> lock(client_mutex);
    if (!g_client) return 0;
    return g_client->get_num_writers();
}

HERMES_C_API void hermes_free_string(const char* str) {
    // 搜索结果现由 g_search_result_buffer 返回，不可 free；仅对非该指针调用 free 避免 munmap_chunk
    if (!str) return;
    if (g_last_search_ptr && str == g_last_search_ptr) return;
    free((void*)str);
}

HERMES_C_API void hermes_cleanup() {
    lock_guard<mutex> lock(client_mutex);
    if (g_client) {
        g_client->cleanup();
        delete g_client;
        g_client = nullptr;
    }
}

HERMES_C_API int hermes_get_encrypted_document(int writer_id, int file_id, unsigned char** ciphertext, int* len, unsigned char* iv) {
    // 这里应该从服务器获取加密文档
    // 为了简化，我们从本地存储读取
    static DocumentStorage storage("../encrypted_docs/");
    
    if (!storage.document_exists(writer_id, file_id)) {
        set_error("Document not found");
        return -1;
    }
    
    unsigned char* enc_data = nullptr;
    int enc_len = 0;
    unsigned char enc_iv[16];
    
    if (storage.get_encrypted_document(writer_id, file_id, &enc_data, &enc_len, enc_iv)) {
        *ciphertext = enc_data;
        *len = enc_len;
        memcpy(iv, enc_iv, 16);
        return 0;
    }
    
    set_error("Failed to retrieve encrypted document");
    return -1;
}

HERMES_C_API int hermes_decrypt_document(const unsigned char* ciphertext, int ciphertext_len, 
                            int writer_id, int file_id, 
                            unsigned char** plaintext, int* plaintext_len,
                            const unsigned char* iv) {
    // 派生解密密钥（使用读者密钥）
    // 注意：在实际应用中，读者密钥应该从HICKAE系统获取
    unsigned char reader_secret_key[32];
    // 简化版：使用固定密钥（实际应该从HICKAE系统获取）
    // 这里使用SHA512(reader_identity)作为读者密钥
    SHA512_CTX sha512;
    unsigned char hash[SHA512_DIGEST_LENGTH];
    string reader_id = "reader_secret";  // 在实际应用中，这应该是读者的身份信息
    SHA512_Init(&sha512);
    SHA512_Update(&sha512, reader_id.c_str(), reader_id.length());
    SHA512_Final(hash, &sha512);
    memcpy(reader_secret_key, hash, 32);
    
    // 派生解密密钥
    unsigned char decryption_key[32];
    DocumentDecryptor::derive_decryption_key(writer_id, file_id, reader_secret_key, decryption_key);
    
    // 解密文档
    *plaintext = new unsigned char[ciphertext_len + 16];  // 预留空间
    *plaintext_len = DocumentDecryptor::decrypt_document(ciphertext, ciphertext_len, 
                                                         decryption_key, iv, 
                                                         *plaintext);
    
    if (*plaintext_len <= 0) {
        delete[] *plaintext;
        *plaintext = nullptr;
        set_error("Decryption failed");
        return -1;
    }
    
    return 0;
}

HERMES_C_API void hermes_free_buffer(unsigned char* buffer) {
    if (buffer) {
        delete[] buffer;
    }
}

HERMES_C_API const char* hermes_get_last_error() {
    lock_guard<mutex> lock(error_mutex);
    return last_error.c_str();
}

}


