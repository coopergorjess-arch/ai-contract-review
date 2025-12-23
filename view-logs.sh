#!/usr/bin/env bash

# 日志查看脚本

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# 清理输入：只保留数字
clean_choice() {
    echo "$1" | grep -o '[0-9]' | head -1
}

# 检查日志文件是否存在
check_log_file() {
    if [ ! -f "$1" ]; then
        echo -e "${RED}错误：日志文件不存在: $1${NC}"
        echo -e "${YELLOW}提示：请先运行 ./dev.sh 启动服务${NC}"
        return 1
    fi
    return 0
}

while true; do
    clear
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  日志查看工具${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "选择要查看的日志："
    echo "  ${GREEN}1${NC}) 后端日志（全部）"
    echo "  ${GREEN}2${NC}) 后端日志（仅 DEBUG/ERROR/INFO）"
    echo "  ${GREEN}3${NC}) 前端日志"
    echo "  ${GREEN}4${NC}) 后端日志（最后50行）"
    echo "  ${GREEN}5${NC}) 前端日志（最后50行）"
    echo "  ${GREEN}6${NC}) 退出"
    echo ""
    
    # 检查日志文件
    if ! check_log_file /tmp/backend.log; then
        echo ""
        read -p "按 Enter 键继续..."
        continue
    fi
    
    echo -e "${YELLOW}提示：直接输入数字（1-6）即可${NC}"
    read -p "请选择 [1-6]: " input
    
    # 清理输入，只提取第一个数字
    choice=$(clean_choice "$input")
    
    if [ -z "$choice" ]; then
        echo -e "${RED}无效输入，请输入数字 1-6${NC}"
        sleep 1
        continue
    fi
    
    case $choice in
        1)
            echo -e "${GREEN}查看后端完整日志（按 Ctrl+C 退出）...${NC}"
            echo ""
            tail -f /tmp/backend.log
            ;;
        2)
            echo -e "${GREEN}查看后端关键日志（按 Ctrl+C 退出）...${NC}"
            echo -e "${YELLOW}显示包含 DEBUG、ERROR、INFO、WARNING 的日志${NC}"
            echo ""
            tail -f /tmp/backend.log | grep --line-buffered --color=always -E "(DEBUG|ERROR|INFO|WARNING|⚠️|提取到|条款)" || {
                echo -e "${YELLOW}没有匹配的日志，显示全部日志...${NC}"
                tail -f /tmp/backend.log
            }
            ;;
        3)
            if ! check_log_file /tmp/frontend.log; then
                sleep 2
                continue
            fi
            echo -e "${GREEN}查看前端日志（按 Ctrl+C 退出）...${NC}"
            echo ""
            tail -f /tmp/frontend.log
            ;;
        4)
            echo -e "${GREEN}后端日志最后50行：${NC}"
            echo ""
            tail -n 50 /tmp/backend.log
            echo ""
            echo -e "${YELLOW}按 Enter 键返回菜单...${NC}"
            read
            ;;
        5)
            if ! check_log_file /tmp/frontend.log; then
                sleep 2
                continue
            fi
            echo -e "${GREEN}前端日志最后50行：${NC}"
            echo ""
            tail -n 50 /tmp/frontend.log
            echo ""
            echo -e "${YELLOW}按 Enter 键返回菜单...${NC}"
            read
            ;;
        6)
            echo -e "${GREEN}退出${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}无效选择，请输入 1-6${NC}"
            sleep 1
            ;;
    esac
done

