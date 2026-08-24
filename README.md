# WhisperMark

> Agent 驱动的智学网阅卷 CLI 工具，支持自动批改、手动打分、图片自动识别

## 为什么选择 WhisperMark

市面上的阅卷辅助工具大多基于**RPA**（如 AutoHotKey、按键精灵），运行时鼠标被完全占用，无法进行其他操作。

WhisperMark 采用 **Playwright 浏览器自动化**，直接操作页面 DOM，**不占用鼠标和键盘**：

| 对比项 | 鼠标脚本类工具 | WhisperMark |
|--------|---------------|-------------|
| 运行时能否操作鼠标 | 不能，鼠标被脚本控制 | **能，鼠标完全空闲** |
| 浏览器能否最小化 | 不能，必须前台可见 | **能，最小化后继续批阅** |
| 能否边批阅边做其他事 | 不能，屏幕被占用 | **能，终端挂后台即可** |
| 出错处理 | 脚本偏移直接点错 | CSS 选择器精确定位，自动重试 |

**简而言之：启动后最小化浏览器，去干别的事，6~10 分钟批完 100 份。**

## 功能特性

- **AI 自动阅卷**：上传题干图片，AI 自动生成答案、解析、评分细则，自动批改每份答卷
- **手动阅卷**：键盘快捷键打分，回车提交，支持回评
- **答题卡识别**：自动定位 iframe 中的答题卡图片
- **分数自动提交**：自动填入分数、点击提交、等待下一张加载
- **满分自动读取**：从页面输入框属性读取真实满分，无需手动配置
- **后台静默运行**：浏览器可最小化，不占用鼠标键盘，批阅期间可自由操作电脑
- **多模型支持**：兼容 MiMo v2.5 / DeepSeek / 通义千问 / 硅基流动等 API

## 快速开始

### 环境要求

- Python 3.8+
- Windows / macOS / Linux

### 安装

```bash
git clone https://github.com/yourname/WhisperMark.git
cd WhisperMark
pip install -r requirements.txt
playwright install chromium
```

### 配置 AI

设置环境变量（推荐 MiMo v2.5，性价比最高）：

```bash
# 小米 MiMo v2.5（推荐，支持图片输入，价格极低）
export AI_BASE_URL=https://api.xiaomimimo.com/v1
export AI_MODEL=MiMo-V2.5
export AI_API_KEY=sk-xxx

# DeepSeek
export AI_BASE_URL=https://api.deepseek.com/v1
export AI_MODEL=deepseek-chat
export AI_API_KEY=sk-xxx

# 硅基流动（国内免费额度）
export AI_BASE_URL=https://api.siliconflow.cn/v1
export AI_MODEL=Qwen/Qwen2.5-VL-72B-Instruct
export AI_API_KEY=sk-xxx

# OpenAI
export AI_API_KEY=sk-xxx
```

或直接修改 `src/config.py` 中的 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`。

### Token 消耗与费用估算

以批阅 **100 份答卷**为例（含 1 次题干分析）：

| 项目 | 估算值 |
|------|--------|
| 总输入 Token | 约 15~25 万（图片占大头） |
| 总输出 Token | 约 0.5~1 万 |
| 耗时 | 约 6~10 分钟 |

**各模型费用对比（批阅 100 份）：**

| 模型 | 输入价格 | 输出价格 | 预估总费用 |
|------|----------|----------|-----------|
| **MiMo v2.5** | ¥1 / 百万token | ¥2 / 百万token | **约 ¥0.2** |
| DeepSeek V4 | ¥1 / 百万token | ¥2 / 百万token | 约 ¥0.2 |
| GPT-4o | $2.5 / 百万token | $10 / 百万token | 约 ¥3~4 |
| Qwen2.5-VL-72B（硅基流动） | 免费额度 | 免费额度 | ¥0 |

> MiMo v2.5 支持原生图片理解，定价在 2026 年 5 月永久下调后性价比极高。缓存命中时输入价格仅 ¥0.02/百万token。

### 运行

```bash
python main.py
```

## 使用流程

```
1. 启动程序，浏览器自动打开智学网
2. 在浏览器中手动登录、进入阅卷中心、选择题目
3. 回到终端按回车确认
4. （可选）上传题干图片，AI 自动生成评分细则
5. 开始批阅 —— 最小化浏览器，去做其他事，终端自动完成剩余工作
   - 有题干 → AI 自动批阅模式（全自动）
   - 无题干 → 手动模式（按 a 调用 AI 单次评分）
```

> **提示**：登录步骤需要手动操作浏览器，之后即可最小化。批阅全程通过 Playwright API 直接控制页面，无需鼠标参与。

## 快捷键

| 按键 | 功能 |
|------|------|
| `0-9` | 输入分数 |
| `.` | 小数点 |
| `Enter` | 提交分数 |
| `a` | 调用 AI 评分（手动模式） |
| `n` | 跳过当前 |
| `p` | 上一份（回评） |
| `r` | 旋转图片 |
| `s` | 保存图片 |
| `d` | 调试（页面分析） |
| `空格` | 暂停/继续自动批阅 |
| `q` | 退出 |

## 项目结构

```
WhisperMark/
├── main.py              # 主程序入口
├── requirements.txt     # 依赖清单
├── README.md            # 本文件
├── .gitignore           # Git 忽略规则
├── src/
│   ├── __init__.py
│   ├── config.py        # 配置管理
│   ├── browser.py       # 浏览器管理（Playwright）
│   ├── login.py         # 登录模块（手动模式）
│   ├── grader.py        # 阅卷核心逻辑
│   ├── ai_grader.py     # AI 阅卷模块
│   └── image_utils.py   # 图片处理工具
└── data/                # 运行时数据（自动创建，已 gitignore）
```

## 工作原理

1. **Playwright 浏览器**：启动 Chromium，用户手动完成登录（仅此一步需要操作浏览器）
2. **DOM 直接操作**：通过 Playwright API 直接读写页面元素，不模拟鼠标，不占用光标
3. **iframe 穿透**：智学网使用微前端架构，CLI 在所有 frame 中搜索答题卡和输入框
4. **AI 视觉模型**：将答题卡图片发送给多模态大模型，获取评分建议
5. **自动提交**：通过 CSS 选择器精确定位输入框和按钮，自动填入分数并提交

## 常见问题

### Q: AI 返回空内容怎么办？

A: 检查模型是否支持图片输入。推荐使用 `qwen-vl-max` 或 `Qwen/Qwen2.5-VL-72B-Instruct`。

### Q: 提交失败怎么办？

A: 查看 `data/debug.log` 日志文件，确认选择器是否匹配。智学网页面结构可能更新，需要调整 `src/grader.py` 中的选择器。

### Q: 支持哪些 AI 模型？

A: 任何支持 OpenAI 兼容接口的多模态模型。包括但不限于：
- 小米 MiMo v2.5（推荐，性价比最高）
- OpenAI GPT-4o
- 通义千问 qwen-vl-max
- 硅基流动 Qwen2.5-VL
- DeepSeek V4

### Q: 批阅时浏览器必须前台运行吗？

A: **不需要**。登录阶段需要手动操作浏览器，之后即可最小化。WhisperMark 通过 Playwright API 直接操作页面 DOM，不模拟鼠标移动，浏览器在后台正常工作。你可以最小化浏览器去做其他事，批阅在终端自动进行。

### Q: 和其他阅卷工具相比有什么区别？

A: 市面上多数工具基于鼠标脚本（AutoHotKey、按键精灵等），运行时鼠标被锁定，必须前台运行。WhisperMark 使用 Playwright 直接操作浏览器内部接口，鼠标完全空闲，浏览器可最小化后台运行。

## 许可证

MIT License

## 免责声明

本工具仅用于提升阅卷操作效率，所有批改判断由人工确认。使用本工具产生的任何结果由使用者自行负责。
