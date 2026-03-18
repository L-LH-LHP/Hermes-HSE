#include "document_storage.hpp"
#include <iostream>
#include <sys/stat.h>
#include <cstring>
#include <openssl/sha.h>
#include <openssl/evp.h>
#include "utils_decl.h"  // 仅 Encrypt/Decrypt 声明，避免与 hermes_client_api 的 utils.h 多重定义

using namespace std;

DocumentStorage::DocumentStorage(const string& path) : storage_path(path) {
    // 创建存储目录（如果不存在）
    struct stat info;
    if (stat(storage_path.c_str(), &info) != 0) {
        // 目录不存在，创建它
        string cmd = "mkdir -p " + storage_path;
        system(cmd.c_str());
    }
}

DocumentStorage::~DocumentStorage() {
    // 清理内存
    for (auto& pair : encrypted_docs) {
        if (pair.second.ciphertext) {
            delete[] pair.second.ciphertext;
        }
    }
}

string DocumentStorage::get_storage_path(int writer_id, int file_id) {
    return storage_path + to_string(writer_id) + "_" + to_string(file_id) + ".enc";
}

bool DocumentStorage::document_exists(int writer_id, int file_id) {
    string path = get_storage_path(writer_id, file_id);
    ifstream file(path, ios::binary);
    return file.good();
}

bool DocumentStorage::encrypt_and_store(int writer_id, int file_id, const string& original_file_path) {
    // 读取原始文档
    ifstream infile(original_file_path, ios::binary);
    if (!infile.is_open()) {
        cerr << "Failed to open original file: " << original_file_path << endl;
        return false;
    }
    
    // 获取文件大小
    infile.seekg(0, ios::end);
    size_t file_size = infile.tellg();
    infile.seekg(0, ios::beg);
    
    // 读取文件内容
    unsigned char* plaintext = new unsigned char[file_size];
    infile.read((char*)plaintext, file_size);
    infile.close();
    
    // 生成加密密钥（基于文件ID和写入者ID的密钥派生）
    // 在实际应用中，这个密钥应该由写入者密钥派生
    unsigned char key[32];  // AES-256需要32字节密钥
    unsigned char iv[16];   // CTR模式需要16字节IV
    
    // 简化版密钥派生：使用SHA512(文件ID+写入者ID)作为密钥（OpenSSL 3.x 兼容）
    unsigned char hash[SHA512_DIGEST_LENGTH];
    string key_seed = to_string(writer_id) + "_" + to_string(file_id);
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
    EVP_MD_CTX* md_ctx = EVP_MD_CTX_new();
    if (md_ctx && EVP_DigestInit_ex(md_ctx, EVP_sha512(), NULL) == 1
        && EVP_DigestUpdate(md_ctx, key_seed.c_str(), key_seed.length()) == 1
        && EVP_DigestFinal_ex(md_ctx, hash, NULL) == 1) {
        EVP_MD_CTX_free(md_ctx);
    } else {
        if (md_ctx) EVP_MD_CTX_free(md_ctx);
        return false;
    }
#else
    SHA512_CTX sha512;
    SHA512_Init(&sha512);
    SHA512_Update(&sha512, key_seed.c_str(), key_seed.length());
    SHA512_Final(hash, &sha512);
#endif
    memcpy(key, hash, 32);  // 使用前32字节作为密钥
    
    // 生成IV（使用SHA512的后16字节）
    memcpy(iv, hash + 32, 16);
    
    // 加密文档（AES-256-CTR）
    unsigned char* ciphertext = new unsigned char[file_size + 16];  // 预留一些空间
    int ciphertext_len = Encrypt(plaintext, file_size, key, iv, ciphertext);
    
    delete[] plaintext;
    
    // 存储加密文档到文件
    string storage_file = get_storage_path(writer_id, file_id);
    ofstream outfile(storage_file, ios::binary);
    if (!outfile.is_open()) {
        delete[] ciphertext;
        return false;
    }
    
    // 写入格式：IV(16字节) + 密文长度(4字节) + 密文
    outfile.write((char*)iv, 16);
    outfile.write((char*)&ciphertext_len, sizeof(int));
    outfile.write((char*)ciphertext, ciphertext_len);
    outfile.close();
    
    // 也存储在内存中（用于快速访问）
    EncryptedDocument doc;
    doc.ciphertext = new unsigned char[ciphertext_len];
    memcpy(doc.ciphertext, ciphertext, ciphertext_len);
    doc.ciphertext_len = ciphertext_len;
    memcpy(doc.iv, iv, 16);
    
    string doc_key = to_string(writer_id) + "_" + to_string(file_id);
    encrypted_docs[doc_key] = doc;
    
    delete[] ciphertext;
    return true;
}

bool DocumentStorage::get_encrypted_document(int writer_id, int file_id, 
                                              unsigned char** ciphertext, int* len, 
                                              unsigned char* iv) {
    string doc_key = to_string(writer_id) + "_" + to_string(file_id);
    
    // 首先检查内存
    if (encrypted_docs.find(doc_key) != encrypted_docs.end()) {
        EncryptedDocument& doc = encrypted_docs[doc_key];
        *len = doc.ciphertext_len;
        *ciphertext = new unsigned char[*len];
        memcpy(*ciphertext, doc.ciphertext, *len);
        memcpy(iv, doc.iv, 16);
        return true;
    }
    
    // 从文件读取
    string storage_file = get_storage_path(writer_id, file_id);
    ifstream infile(storage_file, ios::binary);
    if (!infile.is_open()) {
        return false;
    }
    
    // 读取IV
    infile.read((char*)iv, 16);
    
    // 读取密文长度
    int ciphertext_len;
    infile.read((char*)&ciphertext_len, sizeof(int));
    
    // 读取密文
    *ciphertext = new unsigned char[ciphertext_len];
    infile.read((char*)*ciphertext, ciphertext_len);
    infile.close();
    
    *len = ciphertext_len;
    
    // 缓存到内存
    EncryptedDocument doc;
    doc.ciphertext = new unsigned char[ciphertext_len];
    memcpy(doc.ciphertext, *ciphertext, ciphertext_len);
    doc.ciphertext_len = ciphertext_len;
    memcpy(doc.iv, iv, 16);
    encrypted_docs[doc_key] = doc;
    
    return true;
}

void DocumentDecryptor::derive_decryption_key(int writer_id, int file_id,
                                               const unsigned char* reader_secret_key,
                                               unsigned char* decryption_key) {
    // 密钥派生：使用SHA512(reader_secret + writer_id + file_id)
    SHA512_CTX sha512;
    unsigned char hash[SHA512_DIGEST_LENGTH];
    
    SHA512_Init(&sha512);
    SHA512_Update(&sha512, reader_secret_key, 32);
    
    string writer_str = to_string(writer_id);
    SHA512_Update(&sha512, writer_str.c_str(), writer_str.length());
    
    string file_str = to_string(file_id);
    SHA512_Update(&sha512, file_str.c_str(), file_str.length());
    
    SHA512_Final(hash, &sha512);
    memcpy(decryption_key, hash, 32);  // 使用前32字节作为密钥
}

int DocumentDecryptor::decrypt_document(const unsigned char* ciphertext, int ciphertext_len,
                                        const unsigned char* key, const unsigned char* iv,
                                        unsigned char* plaintext) {
    return Decrypt((unsigned char*)ciphertext, ciphertext_len, (unsigned char*)key, (unsigned char*)iv, plaintext);
}
