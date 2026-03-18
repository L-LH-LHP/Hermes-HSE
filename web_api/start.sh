#!/bin/bash

# Hermes Web API 启动脚本

echo "======================================"
echo "Hermes Web API 启动脚本"
echo "======================================"

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 Python3"
    exit 1
fi

# 检查Flask是否安装
if ! python3 -c "import flask" 2>/dev/null; then
    echo "安装Python依赖..."
    pip3 install -r requirements.txt
fi

# 检查C++库是否存在
LIB_PATH="./libhermes_client.so"
if [ ! -f "$LIB_PATH" ]; then
    echo "警告: C++库不存在，尝试编译..."
    if command -v make &> /dev/null; then
        make lib
    else
        echo "警告: 未找到make命令，跳过编译"
        echo "请手动运行: make lib"
    fi
fi

# 检查服务器是否运行
echo "检查C++服务器..."
if ! pgrep -f "server" > /dev/null; then
    echo "警告: 未检测到C++服务器进程"
    echo "请确保在另一个终端运行: cd ../server && ./server"
    echo ""
    read -p "是否继续启动Web服务? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 启动Web服务
echo ""
echo "启动Web服务..."
echo "访问地址: http://localhost:5000"
echo "按 Ctrl+C 停止服务"
echo ""

python3 app.py

