# dsh-plugin（wm-probe）

WhisperMark 2.0 的 **DeepSeek Harness（DSH）插件**：在 DSH / workbuddy 中暴露一个
`wm_probe` 工具，用来「题干 → 评分细则 → 整批答卷给分」。

- `check_env`：检查 Python、依赖、AI Key、Playwright 浏览器；
- `analyze_question`：分析题干图片，返回标准答案 / 解析 / 评分细则；
- `grade`：传入答卷图片或目录（一次可上千份）+ 上一步的评分细则，整批复用同一份 rubric 批量给分。

## 工作原理

插件内 `scripts/*.py` 直接复用仓库根目录的 `wm` 引擎（`wm.engine.ai_grader`），
因此**插件和 CLI 共用同一套 AI 评分与细则锚定逻辑**，不会出现两套 prompt 漂移。

```
dsh-plugin/scripts/_bootstrap.py    # 定位仓库根目录 -> sys.path
dsh-plugin/scripts/check_env.py     # 环境自检
dsh-plugin/scripts/analyze_question.py
dsh-plugin/scripts/grade_image.py   # 细则锚定批量给分
```

如果插件不是放在仓库内运行，设置环境变量 `WHISPERMARK_HOME` 指向 WhisperMark 2.0 根目录：

```bash
WHISPERMARK_HOME=C:\path\to\WhisperMark2.0
```

## 本地手动验证（不经过 DSH）

```bash
python dsh-plugin/scripts/check_env.py
python dsh-plugin/scripts/analyze_question.py 题干.jpg --max-score 20
python dsh-plugin/scripts/grade_image.py 答题卡目录/ --max-score 20 --rubric "评分细则……"
```

> `wm_probe` 只给出「AI 建议分数」，不直接提交到平台；提交环节请走 CLI 的 `grade` 命令，或人工复核后提交。
