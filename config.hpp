#pragma once
const int NUM_BITS              = 224;
const int MAX_KEYWORDS          = 100;
const int MAX_THREADS_INIT      = 8;
const int MAX_THREADS_SEARCH    = 8;
const int MAX_THREADS_UPDATE    = 4;
const int MAX_THREADS_REBUILD   = 4;
const int SERVER_PORT           = 8888;

// The maximum number of partitions is based on the largest database including 57,639 keywords
const int MAX_PARTITIONS        = 240; 
const int MAX_TOKEN_SIZE        = 148;
const size_t MAX_MPZ_IMPORT_SIZE = 256;  // mpz k1 为 224bit，正常 ≤32 字节；防止恶意/损坏的 search_query 导致 GMP overflow
const int MAX_MATCH_OUTPUT      = 4096;

const int RECURSIVE_LEVEL       = 3;
const int PARTITION_SIZE        = 10;
const int NUM_PARTITIONS        = 1000;

#define ENABLE_SEPARATE_SEARCH  1
#define WRITER_EFFICIENCY       1
#define SEARCH_EFFICIENCY       1

// 设为 1：不执行 in-process reload（避免 libpbc curve_mul SIGBUS），服务端不崩溃可继续关键字搜索；点击“重新加载”会提示重启 server
#define RELOAD_DISABLE_IN_PROCESS 1
