---
name: whispermark
description: >-
  WhisperMark 2.0 — Agent 驱动的多平台 AI 阅卷 CLI 技能。当用户提到阅卷、批改试卷、
  智学网、阅小二、答题卡打分、生成评分细则、分析题干、批量给分、AI 阅卷监控时触发。
  提供三类能力：(1) 无需登录/AI Key 的虚拟批改演示与监控页（可直接执行，推荐先跑）；
  (2) 无人值守的答题卡 AI 评分与评分细则生成（可直接执行 CLI）；
  (3) 交互式阅卷的启动引导与监控（主程序需用户亲自在浏览器登录操作）。
license: MIT
version: 2.0.0
---

# WhisperMark 2.0

Agent 驱动的多平台 AI 阅卷 CLI。基于 Playwright 浏览器自动化，不占用鼠标键盘，
支持智学网、阅小二两个主流平台，核心是「评分细则锚定」防幻觉机制。

## 何时使用

- 用户想先体验/演示阅卷流程和监控页面（不需要平台账号）
- 用户要批量批改智学网 / 阅小二上的答题卡
- 用户要根据题干生成标准答案 + 评分细则（防幻觉锚点）
- 用户要无人值守挂机阅卷，并需要监控进度 / 微信通知
- 用户要导出批改历史（CSV/JSON/HTML）

## 环境要求

- Python 3.8+
- 已安装依赖：`pip install -r requirements.txt`
- 真实阅卷还需：`playwright install chromium`，并配置 AI Key（`WM_AI_API_KEY` 等）
- 仅演示/自检不需要 Chromium 和 AI Key

## 命令

项目根目录执行 `python main.py <命令>`：

| 命令 | 说明 |
|------|------|
| `check-env` | 环境自检（依赖 / Chromium / AI Key / 监控端口） |
| `demo` | 虚拟批改演示（无浏览器 / 无 AI Key），启动实时监控页 |
| `analyze-question` | 分析题干，生成结构化评分细则（落盘 JSON + MD） |
| `grade` | 开始阅卷（核心） |
| `monitor` | 独立启动监控服务器（`--host` / `--port`） |
| `export` | 导出批改历史（`--format csv/json/html`，`--input` 指定历史） |

## 推荐先跑：虚拟批改演示

```bash
python main.py demo
```

- 不登录平台、不调用 AI、不启动浏览器，模拟 200 份批改（可 `--papers` 调整）
- 自动启动监控服务器并在终端打印地址（默认 http://127.0.0.1:8321，被占用自动顺延）
- 完整走 StateManager / SSE / 历史落盘；结束后页面保持运行，Ctrl+C 退出
- 快速验证：`python main.py demo --papers 5 --interval 0.01 --no-monitor --exit`

## 标准工作流（防幻觉核心）

1. **生成细则**（教师检查）：
   ```bash
   python main.py analyze-question --image 题干.jpg --platform zhixue --max-score 20 --question-id 数学第15题
   ```
   生成 `data/rubrics/数学第15题.json` 与 `.md`，请教师检查 MD 内容（分值合计 ≠ 满分会有警告）。

2. **锁定细则**（确认后防篡改）：
   检查无误后用 `--confirm` 重新生成，或手动把 JSON 的 `locked` 置 true。

3. **开始阅卷**（锚定已锁定细则）：
   ```bash
   python main.py grade --platform zhixue --rubric-id 数学第15题 --auto
   ```
   `--auto` 为无人值守模式；去掉则交互式（用户先在浏览器登录）。

4. **监控**：阅卷中打开终端打印的地址（默认 `http://127.0.0.1:8321`）查看实时进度；
   直接打开 `web/monitor.html` 会进入演示数据模式。

## 关键机制：评分细则锚定

- 细则落盘到 `data/rubrics/*.json`（结构化采分点 + 版本号 + locked 标记）
- 批改时每份答卷强制注入同一份细则，禁止 AI 重写，杜绝批量批改时的评分漂移
- 细则与对话流解耦：AI 只负责「生成 + 确认」，引擎只负责「读文件 + 锚定」

## 无人值守（agent 直接执行）

当需要全自动批改时，使用 `--auto` 并确保浏览器已登录（或预先用交互模式登录一次，
cookies 会持久化）。可通过 `--question-image` 一步完成「生成并锁定细则 + 开始批改」：
```bash
python main.py grade --platform zhixue --question-image 题干.jpg --max-score 20 --auto
```

## 微信通知（可选）

配置 `WM_WECHAT_ENABLED=true` 和 `WM_WECHAT_WEBHOOK_URL=<webhook>` 后，
开始/进度/异常/完成会自动推送到微信（支持 workbuddy 小龙虾等任意 webhook）。

## 平台适配

| id | 平台 | 说明 |
|----|------|------|
| `zhixue` | 智学网 | 新版 iframe 微前端 + 旧版 |
| `haoyuejuan` | 阅小二 | Vue3 + Element Plus，无 iframe |

扩展新平台：在 `wm/platforms/` 下新增适配器继承 `PlatformAdapter`，并在 `registry.py` 注册。

## 测试与排障

```bash
python -m pytest tests -q      # 单元 + 端到端测试
python main.py check-env       # 缺依赖 / 端口占用一目了然
```

- 监控页打不开：确认 `demo` / `monitor` 进程还在运行，端口以终端打印为准
- 批改中异常：监控页「异常记录」可见，非致命异常会继续下一份
- 浏览器启动失败：`playwright install chromium`
