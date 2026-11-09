#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析题干图片，生成标准答案、解析、评分细则。

用法:
    python analyze_question.py <题干图片> [--max-score 13]
                               [--api-key KEY] [--base-url URL] [--model NAME]
输出 JSON: ok / model_answer / explanation / scoring_rubric（失败时 ok=false + error）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import apply_ai_overrides, dump_json, load_ai_grader  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="题干分析 - 生成评分细则")
    ap.add_argument("image", help="题干图片路径")
    ap.add_argument("--max-score", type=float, default=13.0, help="满分，默认 13")
    ap.add_argument("--api-key", help="覆盖 AI Key")
    ap.add_argument("--base-url", help="覆盖 AI_BASE_URL")
    ap.add_argument("--model", help="覆盖 AI_MODEL")
    args = ap.parse_args()

    path = Path(args.image)
    if not path.is_file():
        print(f"[X] 图片不存在: {path}", file=sys.stderr)
        return 1

    apply_ai_overrides(args.base_url, args.model, args.api_key)
    ai_grader, config = load_ai_grader()

    print(f"[i] 模型: {config.AI_MODEL}  API: {config.AI_BASE_URL}", file=sys.stderr)
    print("[i] 正在分析题干…", file=sys.stderr)

    result = ai_grader.analyze_question(path.read_bytes(), args.max_score)

    dump_json({
        "ok": bool(result.ok),
        "max_score": args.max_score,
        "model": config.AI_MODEL,
        "model_answer": result.model_answer,
        "explanation": result.explanation,
        "scoring_rubric": result.scoring_rubric,
        "grading_points": [p.to_dict() for p in result.grading_points],
        "warnings": result.warnings,
        "error": result.error,
    })

    if not result.ok:
        print(f"[X] 题干分析失败: {result.error or 'AI 未返回可解析结果'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
