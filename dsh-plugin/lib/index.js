// wm-probe: WhisperMark 智学网答题卡 AI 批改 DSH 工具插件。
// 通过 defineTool 注册 `wm_probe`，其 parameters 一定被编译为
// { type: "object", properties, required }，从而修复 DeepSeek
// "schema must be a JSON Schema of 'type: \"object\"', got 'type: null'" 的拒绝。
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { defineTool } from '@deepseek-ai/dsh-tools';

const here = dirname(fileURLToPath(import.meta.url));
const SCRIPTS_DIR = join(here, '..', 'scripts');
const MAX_BUFFER = 512 * 1024 * 1024;

export const name = 'wm-probe';

export const inject = ['tools'];

function pythonExecutable() {
  return process.env.WHISPERMARK_PYTHON || 'python';
}

/**
 * 运行插件内打包的 Python 脚本，永不因非零退出码直接 reject，
 * 以便把 stderr/退出码作为结构化结果返回给模型。
 */
function runPython(script, args, signal) {
  return new Promise((resolve) => {
    const child = execFile(
      pythonExecutable(),
      [join(SCRIPTS_DIR, script), ...args],
      { encoding: 'utf8', maxBuffer: MAX_BUFFER, windowsHide: true },
      (error, stdout, stderr) => resolve({ error, stdout, stderr }),
    );
    if (signal) {
      if (signal.aborted) child.kill();
      else signal.addEventListener('abort', () => child.kill(), { once: true });
    }
  });
}

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'wm_probe',
    description: [
      '智学网（ZhangXue）答题卡 AI 批改入口，内部复用 WhisperMark CLI（CLI 为辅）。',
      '固定流程：先 action=check_env 自检；',
      '再 action=analyze_question 传题干图片 question_image，得到标准答案/解析/评分细则 scoring_rubric；',
      '最后 action=grade 传 answer_paths（答卷图片路径或目录，一次可上千份）并【必须】传入上一步得到的 scoring_rubric 作为 rubric。',
      '整批答卷必须复用同一份 rubric，禁止为不同答卷重写评分细则，以免评分标准漂移（幻觉）。',
      '模型默认走 DeepSeek，可用 base_url/model/api_key 覆盖。',
    ].join(''),
    parameters: {
      action: {
        type: 'string',
        required: true,
        enum: ['check_env', 'analyze_question', 'grade'],
        description: 'check_env=自检环境；analyze_question=分析题干并生成评分细则；grade=按评分细则批量给分。',
      },
      question_image: {
        type: 'string',
        description: '题干图片路径。action=analyze_question 时必填。',
      },
      answer_paths: {
        type: 'array',
        items: { type: 'string' },
        description: '答卷图片路径或目录列表。action=grade 时必填；支持一次传入上千份。',
      },
      max_score: {
        type: 'number',
        description: '满分，默认 13。',
      },
      rubric: {
        type: 'string',
        description: '评分细则（评分标准）。action=grade 时必填：必须复用 analyze_question 生成并确认过的 scoring_rubric，禁止临时编造。',
      },
      base_url: { type: 'string', description: '可选：覆盖 AI_BASE_URL。' },
      model: { type: 'string', description: '可选：覆盖 AI_MODEL。' },
      api_key: { type: 'string', description: '可选：覆盖 AI_API_KEY。' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean', required: true, description: '是否成功。' },
          action: { type: 'string', required: true },
          error: { type: 'string', description: '失败原因。' },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: JSON.stringify(value, null, 2),
      }],
    },
    async execute(args, exec) {
      const overrides = [];
      if (args.base_url) overrides.push('--base-url', args.base_url);
      if (args.model) overrides.push('--model', args.model);
      if (args.api_key) overrides.push('--api-key', args.api_key);

      let script;
      const argv = [...overrides];

      if (args.action === 'check_env') {
        script = 'check_env.py';
      } else if (args.action === 'analyze_question') {
        if (!args.question_image) throw new Error('wm_probe: action=analyze_question 需要 question_image');
        script = 'analyze_question.py';
        argv.push(args.question_image);
        if (args.max_score !== undefined) argv.push('--max-score', String(args.max_score));
      } else if (args.action === 'grade') {
        if (!Array.isArray(args.answer_paths) || args.answer_paths.length === 0) {
          throw new Error('wm_probe: action=grade 需要 answer_paths（答卷图片路径或目录）');
        }
        if (!args.rubric || String(args.rubric).trim().length === 0) {
          throw new Error('wm_probe: action=grade 需要 rubric（评分细则）——请先运行 action=analyze_question 生成 scoring_rubric，再在整批答卷中复用同一份 rubric');
        }
        script = 'grade_image.py';
        argv.push(...args.answer_paths);
        if (args.max_score !== undefined) argv.push('--max-score', String(args.max_score));
        argv.push('--rubric', args.rubric);
      } else {
        throw new Error(`wm_probe: 未知 action "${args.action}"`);
      }

      const { error, stdout, stderr } = await runPython(script, argv, exec.signal);
      const text = String(stdout || '').trim();

      if (args.action === 'check_env') {
        return {
          ok: !error,
          action: args.action,
          report: [text, error ? String(stderr || '') : ''].filter(Boolean).join('\n'),
        };
      }

      if (error) {
        return {
          ok: false,
          action: args.action,
          error: String((stderr || '').trim() || error.message || error),
        };
      }

      let data;
      try {
        data = JSON.parse(text);
      } catch {
        return { ok: false, action: args.action, error: `脚本输出无法解析为 JSON: ${text.slice(0, 500)}` };
      }

      if (args.action === 'grade') {
        return {
          ok: true,
          action: args.action,
          model: data.model ?? '',
          count: data.count ?? (Array.isArray(data.results) ? data.results.length : 0),
          failed: data.failed ?? 0,
          results: data.results ?? [],
        };
      }

      return {
        ok: true,
        action: args.action,
        model: data.model ?? '',
        model_answer: data.model_answer ?? '',
        explanation: data.explanation ?? '',
        scoring_rubric: data.scoring_rubric ?? '',
      };
    },
  }));
}