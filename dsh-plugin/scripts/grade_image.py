#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量批改答题卡图片，输出 AI 建议分数（不提交到智学网）。

用法:
    python grade_image.py <图片或目录> [<图片或目录> ...]
                          [--max-score 13] [--rubric "评分细则"]
                          [--api-key KEY] [--base-url URL] [--model NAME]
输出 JSON 数组: file / score / max_score / reasoning / confidence
confidence == "error" 表示该张评分失败。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import (  # noqa: E402
    apply_ai_overrides,
    collect_images,
    dump_json,
    load_ai_grader,
)


def main():
    ap = argparse.ArgumentParser(description="答题卡 AI 批量评分")
    ap.add_argument("paths", nargs="+", help="图片文件或目录")
    ap.add_argument("--max-score", type=float, default=13.0, help="满分，默认 13")
    ap.add_argument("--rubric", default="", help="评分细则（建议先用 analyze_question.py 生成）")
    ap.add_argument("--api-key", help="覆盖 AI_API_KEY")
    ap.add_argument("--base-url", help="覆盖 AI_BASE_URL")
    ap.add_argument("--model", help="覆盖 AI_MODEL")
    args = ap.parse_args()

    files = collect_images(args.paths)
    if not files:
        print("[X] 未找到任何图片", file=sys.stderr)
        return 1

    apply_ai_overrides(args.base_url, args.model, args.api_key)
    ai_grader, config = load_ai_grader()

    print(f"[i] 共 {len(files)} 张  模型: {config.AI_MODEL}", file=sys.stderr)

    results = []
    for i, f in enumerate(files, 1):
        print(f"[i] ({i}/{len(files)}) {f.name}", file=sys.stderr)
        try:
            r = ai_grader.grade(
                image_data=f.read_bytes(),
                max_score=args.max_score,
                question_info="",
                scoring_standard=args.rubric,
            )
            results.append({
                "file": str(f),
                "score": r.score,
                "max_score": r.max_score,
                "reasoning": r.reasoning,
                "confidence": r.confidence,
            })
        except Exception as e:
            results.append({
                "file": str(f),
                "score": None,
                "max_score": args.max_score,
                "reasoning": f"{type(e).__name__}: {e}",
                "confidence": "error",
            })

    failed = sum(1 for r in results if r["confidence"] == "error")
    dump_json({
        "model": config.AI_MODEL,
        "count": len(results),
        "failed": failed,
        "results": results,
    })

    print(f"[i] 完成，失败 {failed} 张（AI 分数仅为建议，需人工确认后提交）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())