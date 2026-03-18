#pragma once
#include <string>
#include <vector>
#include <unordered_map>
#include <zmq.hpp>
#include <iostream>
#include "config.hpp"
#include "hickae.hpp"
#include "utils.h"
#include "types.hpp"

using namespace std;

// Hermes客户端API - 为Python提供C接口
extern "C" {

// 初始化系统（只需要调用一次）
// 返回: 0成功, -1失败
int hermes_init_system(int num_writers, const char* server_address);

// 搜索操作
// keyword: 搜索关键词
// writer_ids: 写入者ID数组
// num_writers: 写入者数量
// 返回: JSON格式字符串，需要调用者释放内存 (使用hermes_free_string)
const char* hermes_search(const char* keyword, int* writer_ids, int num_writers);

// 更新操作
// writer_id: 写入者ID
// keyword: 关键词
// file_id: 文件ID
// 返回: 0成功, -1失败
int hermes_update(int writer_id, const char* keyword, int file_id);

// 批量更新
// keywords: 关键词数组
// file_ids: 文件ID数组
// count: 数量
// 返回: 0成功, -1失败
int hermes_batch_update(int writer_id, const char** keywords, int* file_ids, int count);

// 获取加密文档
// writer_id: 写入者ID
// file_id: 文件ID
// ciphertext: 输出参数，加密文档内容（需要调用hermes_free_buffer释放）
// len: 输出参数，加密文档长度
// iv: 输出参数，初始化向量（16字节）
// 返回: 0成功, -1失败
int hermes_get_encrypted_document(int writer_id, int file_id, unsigned char** ciphertext, int* len, unsigned char* iv);

// 解密文档（客户端操作）
// ciphertext: 加密文档内容
// ciphertext_len: 加密文档长度
// writer_id: 写入者ID
// file_id: 文件ID
// plaintext: 输出参数，明文内容（需要调用hermes_free_buffer释放）
// plaintext_len: 输出参数，明文长度
// 返回: 0成功, -1失败
int hermes_decrypt_document(const unsigned char* ciphertext, int ciphertext_len, 
                            int writer_id, int file_id, 
                            unsigned char** plaintext, int* plaintext_len,
                            const unsigned char* iv);

// 释放内存缓冲区
void hermes_free_buffer(unsigned char* buffer);

// 设置当前 Epoch（用于索引更新），应在 init 之后调用
// 返回: 0成功, -1失败
int hermes_set_epoch(int epoch);

// 获取写入者数量
int hermes_get_num_writers();

// 通知服务器从 database 重新加载索引（检索会反映已更新的 database 文件）
// 返回: 0成功, -1失败
int hermes_reload_index();

// 清除服务端指定写者的索引（便于随后用 hermes_update 全量推送该写者的 database）
// 返回: 0成功, -1失败
int hermes_clear_writer(int writer_id);

// 重置客户端指定写者的 update 计数（便于按 database 文件顺序全量推送）
void hermes_reset_update_state(int writer_id);

// 删除更新：对不再适用的旧关键字发送 op=删除 的令牌（UpdtTkn 删除 + Updt）
// keywords/counts/file_ids_prev 长度相同；count 为该关键字链中的下标，file_ids_prev 为前驱节点 file_id（count=0 时未使用）
int hermes_delete_updates(int writer_id, const char** keywords, int* counts, int* file_ids_prev, int count);

// 从 database 文件重新加载该写者的 update 计数（增量更新前先删后加时，在“加”之前调用）
int hermes_load_update_state(int writer_id);

// 为增量添加准备 state：先 load_update_state，再对即将 batch_update 的 (keyword,file_id) 逐条将 state[keyword] 减 1，使添加时使用的 count 为“当前服务端链上已有条数”
int hermes_prepare_state_for_incremental_add(int writer_id, const char** keywords, int* file_ids, int count);

// 设置 database 目录的绝对路径（与 Flask 写入的 database 一致，避免 load_update_state 读错文件导致链被覆盖）
void hermes_set_database_dir(const char* path);

// 释放hermes_search返回的字符串
void hermes_free_string(const char* str);

// 释放内存缓冲区（用于文档内容）
void hermes_free_buffer(unsigned char* buffer);

// 断开连接并清理资源
void hermes_cleanup();

// 获取最后一次错误信息
const char* hermes_get_last_error();

}

// C++内部实现类（不导出）
class HermesClientImpl {
private:
    zmq::context_t* context_client;
    zmq::socket_t* socket_client;
    int num_writers;
    uint64_t epoch;
    string encoded_epoch;
    unordered_map<int, unordered_map<string, uint64_t>> update_state_;
    bool initialized;

    bool load_update_state_impl(int writer_id);
    
public:
    HermesClientImpl();
    ~HermesClientImpl();
    
    bool init(int num_writers, const string& server_address);
    bool set_epoch(int epoch);
    string search(const string& keyword, vector<int>& writer_subset);
    bool update(int writer_id, const string& keyword, int file_id);
    bool batch_update(int writer_id, const vector<string>& keywords, const vector<int>& file_ids);
    bool delete_updates(int writer_id, const vector<string>& keywords, const vector<int>& counts, const vector<int>& file_ids_prev);
    bool load_update_state(int writer_id);  // 从 database 文件加载该写者 update 计数（增量“先删后加”时在加之前调用）
    bool prepare_state_for_incremental_add(int writer_id, const vector<string>& keywords, const vector<int>& file_ids);
    bool clear_writer(int writer_id);
    void reset_update_state(int writer_id);
    bool reload_index();
    void cleanup();
    int get_num_writers() { return num_writers; }
};


