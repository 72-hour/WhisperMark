#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按评分细则批量批改答题卡图片，输出 AI 建议分数（不提交到智学网）。

用法:
    python grade_image.py <图片或目录> [<图片或目录> ...]
                          --rubric "评分细则" [--max-score 13]
                          [--api-key KEY] [--base-url URL] [--model NAME]
输出 JSON: model / count / failed / results[file, score, max_score, reasoning, confidence]
confidence == "error" 表示该张评分失败。

安全约定：整批答卷必须复用 analyze_question 生成并确认过的同一份 rubric，
禁止为不同答卷临时改写评分细则，避免评分标准漂移（幻觉）。
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
    ap = argparse.ArgumentParser(description="答题卡 AI 批量评分（细则锚定）")
    ap.add_argument("paths", nargs="+", help="图片文件或目录")
    ap.add_argument("--max-score", type=float, default=13.0, help="满分，默认 13")
    ap.add_argument("--rubric", default="", help="评分细则（必须复用 analyze_question 生成的那一份）")
    ap.add_argument("--api-key", help="覆盖 AI Key")
    ap.add_argument("--base-url", help="覆盖 AI_BASE_URL")
    ap.add_argument("--model", help="覆盖 AI_MODEL")
    args = ap.parse_args()

    if not args.rubric.strip():
        print("[X] 必须提供 --rubric（先运行 analyze_question 生成并确认评分细则）", file=sys.stderr)
        return 1

    files = collect_images(args.paths)
    if not files:
        print("[X] 未找到任何图片", file=sys.stderr)
        return 1

    apply_ai_overrides(args.base_url, args.model, args.api_key)
    ai_grader, config = load_ai_grader()
    from wm.engine.rubric import Rubric

    # 把上一步确认过的细则文本包装成锁定 Rubric —— 整批复用同一份，禁止漂移
    rubric = Rubric(
        question_id="wm-probe-rubric",
        platform="wm-probe",
        total_score=args.max_score,
        raw_text=args.rubric,
        version="1.0",
        locked=True,
    )

    print(f"[i] 共 {len(files)} 张  模型: {config.AI_MODEL}", file=sys.stderr)

    results = []
    for i, f in enumerate(files, 1):
        print(f"[i] ({i}/{len(files)}) {f.name}", file=sys.stderr)
        try:
            r = ai_grader.grade(f.read_bytes(), rubric, args.max_score)
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
