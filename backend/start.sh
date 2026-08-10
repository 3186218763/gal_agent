#!/bin/bash

# 启动后端服务器

# 检查是否在 backend 目录
if [ ! -f "requirements.txt" ]; then
    echo "错误：请在 backend 目录下运行此脚本"
    exit 1
fi

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "警告：未找到 .env 文件"
    echo "请创建 .env 文件并设置 OPENAI_API_KEY"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "安装依赖..."
pip install -r requirements.txt

# 创建数据目录
mkdir -p data

# 启动服务器
echo "启动服务器..."
python -m src.main
