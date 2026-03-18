#pragma once
#include <string>
#include <unordered_map>
#include <fstream>
// 不包含 utils.h，避免与 hermes_client_api 链接时多重定义；Encrypt/Decrypt 在 .cpp 中通过 utils_decl.h 声明

using namespace std;

// 文档加密存储结构
struct EncryptedDocument {
    unsigned char* ciphertext;
    int ciphertext_len;
    unsigned char iv[16];  // AES-256-CTR需要16字节IV
};

// 文档存储管理器
class DocumentStorage {
private:
    // 存储：file_id -> 加密文档
    unordered_map<string, EncryptedDocument> encrypted_docs;
    string storage_path;
    
public:
    DocumentStorage(const string& path = "../encrypted_docs/");
    ~DocumentStorage();
    
    // 从原始文档文件加密并存储
    bool encrypt_and_store(int writer_id, int file_id, const string& original_file_path);
    
    // 根据文件ID获取加密文档
    bool get_encrypted_document(int writer_id, int file_id, unsigned char** ciphertext, int* len, unsigned char* iv);
    
    // 生成文件存储路径
    string get_storage_path(int writer_id, int file_id);
    
    // 检查文档是否存在
    bool document_exists(int writer_id, int file_id);
};

// 文档解密工具（客户端使用）
class DocumentDecryptor {
public:
    // 使用文件ID和写入者密钥派生解密密钥
    // 密钥派生：KDF(file_id, writer_id, reader_secret)
    static void derive_decryption_key(int writer_id, int file_id, 
                                      const unsigned char* reader_secret_key,
                                      unsigned char* decryption_key);
    
    // 解密文档
    static int decrypt_document(const unsigned char* ciphertext, int ciphertext_len,
                                const unsigned char* key, const unsigned char* iv,
                                unsigned char* plaintext);
};
