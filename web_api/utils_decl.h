#pragma once
// 仅声明 Encrypt/Decrypt，供 document_storage.cpp 使用，避免与 hermes_client_api.cpp
// 通过 utils.h 引入的完整定义产生链接时多重定义（multiple definition）。

int Encrypt(unsigned char *plaintext, int plaintext_len, unsigned char *key,
            unsigned char *iv, unsigned char *ciphertext);
int Decrypt(unsigned char *ciphertext, int ciphertext_len, unsigned char *key,
            unsigned char *iv, unsigned char *plaintext);
