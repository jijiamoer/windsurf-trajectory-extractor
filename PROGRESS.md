# Windsurf Trajectory Extractor - PR 准备进度

## 目标

将 Windsurf trajectory 提取工具贡献给 nowledge-mem 社区
- **目标仓库**: https://github.com/nowledge-co/community
- **形式**: 单独 folder，类似 `nowledge-mem-bench` 或 `nowledge-mem-npx-skills`
- **Wey 已同意**

## 调研结论

### 社区仓库结构

```
nowledge-co/community/
├── nowledge-mem-bench/          # Python 工具，有 pyproject.toml
│   ├── README.md
│   ├── pyproject.toml
│   └── src/nmem_bench/
├── nowledge-mem-npx-skills/     # npx skills，纯 Markdown
│   ├── README.md
│   ├── CHANGELOG.md
│   └── skills/
│       └── {skill-name}/SKILL.md
├── nowledge-mem-*-plugin/       # 各种插件
└── examples/                    # 示例文档
```

### PR 风格（参考 #44）

- **标题**: `feat: Add ...` 或 `feat(scope): ...`
- **正文**: Summary + Changes + Implementation Details + Notes
- **Commit**: conventional commits (`feat:`, `fix:`, `docs:`)
- **文件**: README.md 必须，可选 CHANGELOG.md

### 我们的工具定位

这是一个 **Python CLI 工具**，类似 `nowledge-mem-bench`：
- 纯标准库，无外部依赖
- 提取 Windsurf Cascade 对话历史（trajectory）
- 输出 JSONL 格式
- 支持 thinking 内容提取

## 竞品分析

### 0xSero/ai-data-extraction（434 stars）

已存在一个类似项目，支持 8 种 AI 助手的数据提取：
- Claude Code, Cursor, Codex, Windsurf, Trae, Continue, Gemini CLI, OpenCode

**其 Windsurf 提取方式**：
- 读取 SQLite `state.vscdb`
- 尝试多个 key pattern（`aiChat.chatdata`, `cascade.chatdata` 等）
- 提取 JSON 格式的 chat bubbles
- **不支持 protobuf 解码**
- **不支持 thinking 内容提取**
- **不支持微秒级时间戳**

### 我们的差异化优势

| 特性 | ai-data-extraction | 我们的工具 |
|------|---------------------|------------|
| Protobuf 逆向解码 | ❌ | ✅ |
| Thinking 内容提取 | ❌ | ✅ |
| 微秒级时间戳 | ❌ | ✅ |
| Tool calls 详情 | 部分 | ✅ 完整 |
| Provider 信息 | ❌ | ✅ |
| 关键词搜索 trajectory | ❌ | ✅ |
| 对话摘要列表 | ❌ | ✅ |

### 潜在争议分析

**低风险因素**：
1. **技术路线完全不同** - 他们用 JSON key 猜测，我们用 protobuf 逆向
2. **功能差异明显** - thinking 提取是独特卖点
3. **目标社区不同** - 他们是通用工具，我们专为 nowledge-mem 生态
4. **开源友好** - 两个项目都是 MIT/开源，互相补充而非竞争
5. **Wey 已同意** - 社区维护者明确欢迎

**需要注意**：
1. README 中应明确说明技术路线差异
2. 不要贬低其他项目
3. 强调 "deep extraction" 定位

### 逆向工程的伦理分析

**为什么这个逆向是友好的？**

1. **用户数据主权**
   - 提取的是用户自己的对话历史，不是 Windsurf 的代码
   - 类似于 "Export my data" 功能，只是官方没提供
   - EFF（电子前沿基金会）明确支持这类互操作性逆向

2. **社区先例**
   - `0xSero/ai-data-extraction` (434 stars) 做同样的事，无争议
   - `f/agentlytics` 分析 16 种编辑器的 session 数据
   - `thejud/claude-history` 提取 Claude Code 历史
   - **这是一个活跃且被接受的开源生态**

3. **nowledge-mem 社区文化**
   - 社区 README 已经包含多种 IDE 集成（Cursor, Claude Code, Gemini CLI 等）
   - 这些集成本身就需要理解各 IDE 的内部结构
   - Wey 对我们的 Windsurf 逆向成果表示惊喜，而不是担忧

4. **法律安全**
   - 不涉及绕过 DRM/加密
   - 不涉及商业秘密窃取
   - 纯籹是用户数据导出，类似 GDPR 的 "right to data portability"

**结论**：这个逆向是 **用户友好** 的，不是对 Windsurf 的攻击。它帮助用户获取自己的数据。

## 待办

- [x] 调研社区仓库结构
- [x] 调研现有 PR 风格
- [x] 调研类似项目（ai-data-extraction）
- [x] 分析差异化定位
- [x] 复制脚本到工作目录
- [x] 重构目录结构（参考 nowledge-mem-bench）
- [x] 写英文 README（强调 protobuf 逆向 + thinking 提取）
- [x] 准备示例输出（脱敏）
- [x] 创建 pyproject.toml
- [x] 代码质量检查（ruff check + format）
- [x] 测试 CLI 运行
- [x] GPT-5.4 代码审查
- [x] 修复 Major 问题：README 运行方式
- [x] 修复 Major 问题：CLI 退出码与异常处理
- [x] 修复 Major 问题：时间戳窗口（2020-2040）
- [x] 修复 Major 问题：Windows 路径还原
- [x] 添加 .gitignore
- [ ] Fork 仓库并创建分支
- [ ] 提交 PR

## 文件结构（重构后）

```
windsurf-trajectory-extractor/
├── README.md              # 英文，面向社区
├── LICENSE                # MIT
├── pyproject.toml         # 构建配置
├── PROGRESS.md            # 进度跟踪（PR 后可删除）
├── src/
│   └── windsurf_trajectory/
│       ├── __init__.py
│       ├── extractor.py   # 核心提取逻辑
│       └── cli.py         # CLI 入口
└── examples/
    └── sample_output.jsonl  # 脱敏示例
```

## 下一步

1. Fork `nowledge-co/community` 到个人账户
2. 创建分支 `feat/windsurf-trajectory-extractor`
3. 复制文件到 fork 仓库
4. 提交 PR
