# 测试指南

## 前置准备

### 1. 环境变量配置

在项目根目录创建 `.env` 文件（如果还没有）：

```bash
GEMINI_API_KEY=your_gemini_api_key_here
# 可选：指定模型
# GEMINI_MODEL=gemini-2.0-flash
```

### 2. 安装后端依赖

```bash
# 创建 Python 虚拟环境（如果还没有）
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate  # macOS/Linux
# 或
.venv\Scripts\activate  # Windows

# 安装依赖
pip install -r backend/requirements.txt
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
cd ..
```

## 启动服务

### 方式一：使用启动脚本（推荐）

```bash
chmod +x dev.sh
./dev.sh
```

### 方式二：手动启动

**终端 1 - 启动后端：**
```bash
source .venv/bin/activate
cd backend
uvicorn app.main:app --port 8000 --reload
```

**终端 2 - 启动前端：**
```bash
cd frontend
npm run dev
```

## 访问应用

打开浏览器访问：http://localhost:3000

## 测试步骤

### 1. 准备测试合同文件

准备一个测试合同文件（支持格式：`.txt`, `.md`, `.docx`, `.doc`）

示例测试合同（保存为 `test_contract.txt`）：

```
合同编号：TEST-2024-001

第一条 合同双方
甲方：测试公司A
乙方：测试公司B

第二条 合同标的
本合同标的为软件开发服务。

第三条 付款方式
乙方应在合同签订后3个工作日内支付全部款项。

第四条 违约责任
如甲方违约，应支付违约金10万元。

第五条 合同期限
本合同自签订之日起生效，有效期为一年。
```

### 2. 测试流程

1. **打开应用**：访问 http://localhost:3000

2. **上传合同文件**：点击"文件"选择框，选择测试合同文件

3. **设置审查参数**：
   - 审查角度：选择"Party A（甲方）"或"Party B（乙方）"
   - 博弈地位：选择"Strong（强势）"、"Balanced（均衡）"或"Weak（弱势）"
   - 审查强度：选择"Lite（粗略）"或"Detailed（详细）"
   - 其他参数根据需要调整

4. **开始审查**：点击"开始审查"按钮

5. **查看结果**：
   - **摘要**标签：查看整体审查摘要
   - **风险**标签：查看风险点列表，**注意观察条款编号是否以蓝色标签显示**
   - **修订**标签：查看修订建议，**注意观察条款编号是否以绿色标签显示**
   - **原文（节选）**标签：查看相关原文片段

### 3. 验证条款编号显示

检查以下内容：

✅ **风险部分**：
- 每个风险点前面应该有蓝色标签显示条款编号（如"第3条"、"第4条"等）
- 条款编号应该清晰可见，格式为：`第X条` 或 `第X.Y条`

✅ **修订部分**：
- 每个修订建议前面应该有绿色标签显示条款编号
- 修订类型（修订/新增）应该以灰色标签显示

✅ **原文节选部分**：
- 每个原文片段应该有灰色标签显示对应的条款编号

## 常见问题

### 后端启动失败

**问题**：`GEMINI_API_KEY not found`
- **解决**：确保 `.env` 文件存在且包含有效的 `GEMINI_API_KEY`

**问题**：`ModuleNotFoundError: No module named 'google'`
- **解决**：运行 `pip install -r backend/requirements.txt` 安装依赖

### 前端启动失败

**问题**：端口 3000 已被占用
- **解决**：修改 `frontend/package.json` 中的 dev 脚本，添加端口参数：
  ```json
  "dev": "next dev -p 3001"
  ```

### 审查结果没有条款编号

- 检查后端日志，确认 AI 模型返回的数据格式是否正确
- 检查合同文件是否包含条款编号（如"第一条"、"第1条"等）
- 查看浏览器控制台是否有错误信息

## 调试技巧

### 查看后端日志

后端启动后，终端会显示请求日志和错误信息。

### 查看前端日志

打开浏览器开发者工具（F12），查看 Console 标签页。

### 测试 API 端点

可以直接测试后端 API：

```bash
curl -X POST "http://localhost:8000/api/reviews" \
  -F "file=@test_contract.txt" \
  -F "review_position=PartyA" \
  -F "bargaining_power=Strong" \
  -F "review_depth=Detailed" \
  -F "follow_minimum_change=Yes" \
  -F "output_pref=WithRisks" \
  -F "special_instructions="
```

## 预期效果

修改后，您应该看到：

1. **风险列表**：每个风险点前面有醒目的蓝色标签显示条款编号
2. **修订列表**：每个修订建议前面有醒目的绿色标签显示条款编号
3. **原文节选**：每个原文片段有灰色标签显示条款编号

所有条款编号都应该清晰可见，便于快速定位到合同中的具体条款。







