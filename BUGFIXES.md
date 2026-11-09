# WhisperMark 2.0 · BUG 修复与改造清单

> 本文件记录本轮排查、修复的问题与验证方式。按严重程度分级：P0 = 会用不了 / 必崩；P1 = 功能错误；P2 = 体验与工程化。

## 一、P0 致命问题（已修复）

### 1. Playwright 同步 API 跨线程使用 —— 真实阅卷必崩
- **现象**：`cmd_grade` 在主线程创建浏览器，却在 `grader.start_auto()` 的后台线程里操作页面。
  Playwright 的同步 API 绑定创建线程（greenlet），跨线程调用会直接报错，导致真实阅卷一开始就失败。
- **修复**：批改循环改为在**主线程**执行 `Grader.run_forever()`；键盘控制（q/p/r/s）改成后台 daemon 线程读 stdin。
  `start_auto()` 仅保留给程序化/测试场景（fake adapter），并在 docstring 中注明限制。
- 文件：`wm/cli.py`、`wm/engine/grader.py`

### 2. 自动批改竞态：CLI 提前退出并保存空历史
- **现象**：`start_auto()` 返回后线程还没执行到 `self._running = True`，`_auto_wait` 里 `while grader.is_running` 第一次判断就是 False，
  于是立即保存空历史、关闭浏览器，批改线程随后操作已关闭的浏览器。
- **修复**：`start_auto()` 在**起线程之前**置 `_running/_stop_flag`；新增 `wait()/join()`；CLI 收尾顺序改为
  `stop → join(15s) → 收尾状态 → 保存历史 → 关闭浏览器`，不再出现「历史已存但一份没批」。
- 文件：`wm/engine/grader.py`、`wm/cli.py`

### 3. 监控服务器生命周期错误
- **现象**：
  - `MonitorServer` 继承 `Thread`，`start()` 异步绑定；端口被占用时 `run()` 里静默 return，终端却已经打印了一个打不开的 URL；
  - 同一实例 `start()` 第二次抛 `threads can only be started once`；
  - 进程结束从不 `stop()`，状态订阅 / SSE 连接泄漏；
  - 监控页被浏览器缓存，改了 HTML 看不到新页面。
- **修复**：重写为「同步 `start()` 绑定 → 成功才返回实际 URL，失败抛 RuntimeError → 端口被占用自动顺延（可关）→ `stop()` 可重复调用并注销订阅、关闭 SSE 连接 → 支持 start/stop/start」。
  HTML 响应加 `Cache-Control: no-store`；新增 `/api/health`、`/dashboard` 路由。
- 文件：`wm/monitor/server.py`

### 4. `analyze-question` 失败被伪装成成功，垃圾细则被落盘
- **现象**：AI 返回非 JSON 时，代码把「AI 返回无法解析」文本塞进 `model_answer`，CLI 判空逻辑失效，
  于是保存一份空细则并返回 0，教师看到的是「成功」。
- **修复**：`QuestionAnalysis` 增加 `ok / error / warnings`；API 异常、JSON 解析失败、内容为空都返回 `ok=False`，
  CLI 打印失败原因与原始返回片段并返回 1；细则分值之和与满分不一致时给出警告。
- 文件：`wm/engine/ai_grader.py`、`wm/cli.py`

### 5. `--platform auto` 在空白页上必然检测失败
- **现象**：自动检测发生在 `browser.goto()` 之前，浏览器还是 `about:blank`，`url_patterns` 永远匹配不上。
- **修复**：先检测当前页 → 有 `--url` 则打开后重试 → 仍失败时给出明确指引（列出可选平台），不再只报一句「无法识别」。
- 文件：`wm/cli.py` `_resolve_adapter()`

### 6. 监控页看不到 / 页面空白
- **现象**：用户始终没看到监控 HTML。旧页面只有「连接真实服务」一条路径，服务没起来就是白屏；端口失败也无提示。
- **修复**：`python main.py demo` 新增虚拟批改（无浏览器 / 无 AI Key），启动监控并打印真实地址；
  监控页重写为「阅卷指挥中心」，支持演示数据模式（`?demo=1` 或服务不可达时自动启用）、SSE 断线退避重连、轮询兜底。
- 文件：`web/monitor.html`、`wm/demo.py`、`wm/monitor/server.py`

## 二、P1 功能性问题（已修复）

### 7. `--max-score` 参数被忽略
- 修复：`Grader.max_score_override` 生效，并用于 AI 分数钳制；`--question-image` 场景也使用该满分。
- 文件：`wm/cli.py`、`wm/engine/grader.py`

### 8. `state_manager.start()` 不清空上一轮数据
- **现象**：同一进程内二次开始（demo / 测试 / 连续任务）会残留上一轮的 records、errors、当前学生。
- 修复：`start()` 用全新的 `GradingState` 重置；`add_record` 自动编号；新增 `avg_score` 汇总字段；
  `finish()` 在 total 未知时用 graded 兜底，避免进度条永远 0%。
- 文件：`wm/engine/state.py`

### 9. 微信推送：跳号漏推、阻塞批改、无法停止
- **现象**：`graded % interval == 0` 判定在 graded 跳号（如 48→52）时永远命中不了；webhook 在状态回调里同步 POST（最长 10s），直接卡住批改；没有 unsubscribe。
- 修复：改为「增量 ≥ interval」判定；webhook 改为后台队列串行发送；新增 `start/stop/reset` 与订阅注销。
- 文件：`wm/notify/wechat.py`

### 10. AI 调用容错与解析
- 图片 MIME 固定为 `image/jpeg` → 按文件头识别 jpeg/png/webp/gif；
- `analyze_question` 未捕获 API 异常 → 现在返回 `ok=False`；
- `grade()` 解析出非 dict（如 JSON 数组）会崩 → 统一只接受 dict，并用 `JSONDecoder.raw_decode` 扫描脏文本；
- 分数支持 `"8.5分"` 之类字符串；统一钳制到 `[0, max]`；置信度归一化为 `high/medium/low`；
- 无评分细则时强制 `low` 并注明「未提供评分细则」；无理由时降低置信度。
- 文件：`wm/engine/ai_grader.py`

### 11. 导出 XSS 与报告质量
- **现象**：`_export_html` 直接把学生名 / 理由拼进 HTML，`<script>` 会被执行；报告没有统计与样式。
- 修复：所有动态内容 HTML 转义；新报告含 KPI、置信度分布、异常清单、明细表、打印样式、演示数据标识；
  新增 `--input` 指定历史文件，`--output` 也适用于 JSON；CSV 带 BOM 防 Excel 乱码。
- 文件：`wm/cli.py`

### 12. 环境自检与可选依赖
- **现象**：`wm/browser.py`、`wm/platforms/*`、`wm/cli.py` 顶层 `import playwright`，没装 playwright 时连 `check-env` 都跑不起来。
- 修复：惰性导入（未安装时给出安装指引）；`check-env` 增加 Chromium 内核检测、监控端口占用检测。
- 文件：`wm/browser.py`、`wm/platforms/base.py`、`wm/platforms/zhixue.py`、`wm/platforms/haoyuejuan.py`、`wm/cli.py`

### 13. 其他功能修复
- `browser.py`：stealth 脚本改到 `context.add_init_script`（页面级对新页面无效）；cookies 保存目录自动创建；启动失败给出可读错误。
- `rubric.py`：加载容错（字段类型错误不再抛异常）、新增 `validate()` 自洽性校验、中文文件名清洗集中、MD 导出标注分值不一致。
- `main.py monitor`：新增 `--host / --port`；`grade` 新增 `--monitor-port`。
- `.env`：支持 BOM / `export` 前缀 / 成对引号；新增 `WM_DATA_DIR`、`WM_RUNTIME_DIR`、AI token 上限、监控 host/ fallback 等配置。
- 日志与监控页加 no-store，避免看到旧页面 / 旧状态。

## 三、P2 体验与工程化

- 新增 `python main.py demo` 虚拟批改：无需浏览器、AI Key、平台账号即可完整跑通「状态 → 监控 → SSE → 历史 → 导出」；
- 监控页全新设计（深/浅色、进度环、KPI、当前答卷、分数分布、异常时间线、记录筛选、移动端适配）；
- `demo_monitor.py` 保留为兼容入口；
- 新增 `tests/` pytest 套件（配置 / 细则 / AI 解析 / 状态机 / 监控生命周期 / SSE / CLI / 导出 / 演示 / 批改竞态）；
- 新增 `.gitignore`，移除仓库内过期的 `runtime/monitor/monitor.html`（运行时自动生成，不再入库）；
- README / SKILL 同步新命令、监控页说明、测试与排障。

## 四、验证记录

### 1. 自动化测试（QA 独立测试套件，`tests/`）

```
python -m pytest tests -q
→ 114 passed, 0 failed, 1 warning (pytest cacheprovider 沙箱写权限警告), 7.1s
```

覆盖：config（9）/ rubric（19）/ ai_grader（30）/ state（16）/ monitor（11）/ cli（17）/ grader（9）/ demo E2E（3）。
连续多次运行稳定，无 flake。测试只写 `tests/`，未改动后端代码。

### 2. 命令行冒烟

| 验证项 | 结果 |
|--------|------|
| `python main.py check-env` | 正常输出依赖 / Chromium / AI Key / 端口状态 |
| `python main.py --version` | `WhisperMark 2.0.0` |
| `python main.py demo --papers 5 --interval 0.01 --no-monitor --exit` | 退出码 0，5/5 份、平均分/异常统计正确，历史 JSON 落盘 |
| `python main.py demo --papers 40 --interval 0.05 --port 8401` | 运行中 `/api/state` 实时变化；结束后 status=done、页面继续可访问 |
| `python main.py export --format csv/html` | 8 条演示记录导出成功；HTML 中 `<script>` 被转义、无行内脚本注入 |
| `python main.py monitor --port 8402` | 同步起服，`/api/health` 返回 ok |

### 3. 监控服务器 / 前端契约

- `MonitorServer.start()` 同步返回实际 URL；`GET /` 返回字节与 `web/monitor.html` 完全一致（67,375 B）；
- `GET /dashboard` 返回新版 `web/dashboard.html`（42,637 B）；
- `/api/state` 字段完整（status/platform/question_id/rubric_version/total/graded/current_*/errors/records/started_at/updated_at/percentage/remaining/avg_score）；
- `/events` 首帧立即下发 `data: {json}\n\n`，状态变更即广播；
- 端口占用 → `RuntimeError`（关闭 fallback 时）；默认自动顺延到 8322+；同实例 start→stop→start 正常，stop 释放端口；
- 监控 HTML / JSON 均带 `Cache-Control: no-store`，不会看到旧页面。

### 4. 视觉验收（无头 Chromium 截图 + 浏览器控制台）

- 桌面 1440×1000、移动端 390×844、深色 / 浅色主题全部截图确认（见 `docs/screenshots/`）；
- 演示模式横幅、当前答卷、分数分布、异常时间线、记录表、页脚均正常渲染；
- 页面加载与交互期间 **0 个 JS 错误 / 控制台 error**；
- 浅色主题实际像素采样：页面底 `#eef1f8`、面板 `#f9f9fe`、顶栏 `#f7faff`，非简单反色；
- 演示模式下「当前答卷」标签显示「演示数据」而非「实时接收中」；
- 移动端记录表自动转卡片布局，无横向溢出。

### 5. 已知未覆盖（环境限制，非本次修复范围）

- 沙箱无法启动真实浏览器做平台 E2E：智学网 / 阅小二的 DOM 选择器、登录、iframe、填分/提交、`wait_for_next_paper` 未用真实账号验证（引擎层已用 FakeAdapter 覆盖编排与竞态）；
- AI 调用全部 mock：真实 DeepSeek / 多模态 prompt 效果、图片上传、token 上限未验证；
- 微信 webhook 未联网验证（仅配置与去重逻辑）；
- 未覆盖非 Windows / 非 Python 3.12 环境。

