#!/usr/bin/env bash
set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  启动 AI 合同审查系统${NC}"
echo -e "${GREEN}========================================${NC}"

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠ 虚拟环境不存在，正在创建...${NC}"
    python3 -m venv .venv
    echo -e "${GREEN}✓ 虚拟环境创建完成${NC}"
fi

# 激活虚拟环境
echo -e "${GREEN}▶ 激活 Python 虚拟环境...${NC}"
source .venv/bin/activate

# 检查依赖
if ! python -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}⚠ 检测到缺少依赖，正在安装...${NC}"
    pip install -r backend/requirements.txt
    echo -e "${GREEN}✓ 依赖安装完成${NC}"
fi

# 检查环境变量
if [ ! -f ".env" ]; then
    echo -e "${RED}⚠ 警告: .env 文件不存在，请确保设置了 GEMINI_API_KEY${NC}"
else
    echo -e "${GREEN}✓ 环境变量文件存在${NC}"
fi

# 检查前端依赖
if [ ! -d "frontend/node_modules" ]; then
    echo -e "${YELLOW}⚠ 前端依赖未安装，正在安装...${NC}"
    cd frontend
    npm install
    cd ..
    echo -e "${GREEN}✓ 前端依赖安装完成${NC}"
fi

# 检查并清理端口
echo -e "${YELLOW}▶ 检查端口占用...${NC}"
BACKEND_PORT=8000
FRONTEND_PORT=3000

# 清理后端端口
if lsof -ti:$BACKEND_PORT > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠ 端口 $BACKEND_PORT 被占用，正在清理...${NC}"
    lsof -ti:$BACKEND_PORT | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# 清理前端端口
if lsof -ti:$FRONTEND_PORT > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠ 端口 $FRONTEND_PORT 被占用，正在清理...${NC}"
    lsof -ti:$FRONTEND_PORT | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# 清空旧日志
> /tmp/backend.log
> /tmp/frontend.log

# 启动后端
echo -e "${GREEN}▶ 启动后端 (FastAPI) - http://localhost:8000${NC}"
cd backend
uvicorn app.main:app --port 8000 --reload > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
cd ..
echo -e "${GREEN}✓ 后端已启动 (PID: $BACKEND_PID)${NC}"

# 等待后端启动
sleep 3

# 启动前端
echo -e "${GREEN}▶ 启动前端 (Next.js) - http://localhost:3000${NC}"
cd frontend
npm run dev > /tmp/frontend.log 2>&1 &
FRONTEND_PID=$!
cd ..
echo -e "${GREEN}✓ 前端已启动 (PID: $FRONTEND_PID)${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  服务已启动！${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "前端: ${GREEN}http://localhost:3000${NC}"
echo -e "后端: ${GREEN}http://localhost:8000${NC}"
echo ""
echo -e "${YELLOW}查看日志:${NC}"
echo -e "  - 后端日志: ${GREEN}tail -f /tmp/backend.log${NC}"
echo -e "  - 前端日志: ${GREEN}tail -f /tmp/frontend.log${NC}"
echo -e "  - 实时查看后端日志（新终端）:"
echo -e "    ${GREEN}tail -f /tmp/backend.log | grep -E '(DEBUG|ERROR|INFO)'${NC}"
echo ""
echo -e "${YELLOW}停止服务:${NC}"
echo -e "  - 按 ${GREEN}Ctrl+C${NC} 停止所有服务"
echo -e "  - 或运行: ${GREEN}pkill -f 'uvicorn|next dev'${NC}"
echo ""
echo -e "${GREEN}按 Ctrl+C 停止所有服务${NC}"
echo ""

# 等待用户中断
trap "echo ''; echo -e '${YELLOW}正在停止服务...${NC}'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM

wait
