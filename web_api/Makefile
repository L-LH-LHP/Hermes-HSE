# Makefile for Hermes Web API
# 编译C++客户端库（依赖与主项目一致：emp-tool、emp-agmpc、zmq、pbc、openssl）

CC = g++
CFLAGS = -march=native -std=c++11 -O2 -pthread -funroll-loops -fPIC -shared
# 头文件：上级目录(hickae.hpp等)、include、emp-tool/emp-agmpc(通常在 /usr/local/include)
INCLUDE_PATH = -I.. -I../include -I/usr/local/include
# 若 emp-tool 在项目内，可设置： make EXTRA_INC=-I/path/to/emp
ifdef EXTRA_INC
INCLUDE_PATH += $(EXTRA_INC)
endif

# 库路径：与主项目 Makefile 一致，支持 HERMES_LIB 与系统路径
HERMES_LIB ?= $(HOME)/Hermes/lib
SYS_LIB = -L/usr/local/lib -L/usr/lib
ZEROMQ_LIB = -L$(HERMES_LIB) $(SYS_LIB)
GMP_LIB = -L$(HERMES_LIB)
OPENSSL_LIB = -L/usr/local/lib $(SYS_LIB)
PBC_LIB = -L$(HERMES_LIB) $(SYS_LIB)

DEPS = $(ZEROMQ_LIB) $(GMP_LIB) $(OPENSSL_LIB) $(PBC_LIB)
LIBS = -lzmq -lgmp -lm -lcrypto -lpbc
# 链接时发现未定义符号则报错；版本脚本显式导出 C API 符号供 Python ctypes 使用
LD_FLAGS ?= -Wl,--no-undefined -Wl,--version-script=hermes_client.ver

# 目标文件
TARGET_LIB = libhermes_client.so
TARGET_LIB_WIN = libhermes_client.dll

# 源文件
SOURCES = libhermes_all.cpp
HEADERS = hermes_client_api.hpp document_storage.hpp ../hickae.hpp ../types.hpp ../config.hpp

.PHONY: all clean lib install test

all: lib

# 编译共享库 (Linux)
lib:
	$(CC) $(CFLAGS) $(INCLUDE_PATH) $(SOURCES) -o $(TARGET_LIB) $(LD_FLAGS) $(DEPS) $(LIBS)
	@echo "Library built: $(TARGET_LIB)"
	@echo "Checking C API symbol..."
	@nm -D $(TARGET_LIB) | grep -q hermes_init_system && echo "  hermes_init_system: OK" || echo "  WARNING: hermes_init_system not found in $(TARGET_LIB)"

# Windows DLL
lib-win:
	$(CC) $(CFLAGS) $(INCLUDE_PATH) $(SOURCES) -o $(TARGET_LIB_WIN) $(LD_FLAGS) $(DEPS) $(LIBS)
	@echo "Library built: $(TARGET_LIB_WIN)"

clean:
	rm -f $(TARGET_LIB) $(TARGET_LIB_WIN)
	rm -f *.o

install: lib
	@echo "Installing library..."
	@echo "Please copy $(TARGET_LIB) to a directory in your library path"
	@echo "Or set LD_LIBRARY_PATH to include the current directory"

test:
	@echo "Run Python tests:"
	@echo "  python -m pytest tests/"

