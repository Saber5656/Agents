# JSONスキーマ

このドキュメントはskill-creatorが使用するJSONスキーマを定義する。

---

## evals.json

スキルのevalを定義する。スキルディレクトリ内の `evals/evals.json` に配置する。

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's example prompt",
      "expected_output": "Description of expected result",
      "files": ["evals/files/sample1.pdf"],
      "expectations": [
        "The output includes X",
        "The skill used script Y"
      ]
    }
  ]
}
```

**フィールド：**
- `skill_name`: スキルのfrontmatterのnameと一致する名前
- `evals[].id`: 一意の整数識別子
- `evals[].prompt`: 実行するタスク
- `evals[].expected_output`: 成功の人間が読める説明
- `evals[].files`: 入力ファイルパスのオプションリスト（スキルルートからの相対パス）
- `evals[].expectations`: 検証可能な陳述のリスト

---

## trigger-eval.json

description最適化用の routing eval を定義する。`run_eval.py` / `run_loop.py` は `claude -p` を使わず、`codex exec` で各クエリについてスキルを使うべきかを判定する。

```json
[
  {
    "query": "このワークフローをスキル化して、テストも含めて整備したい",
    "should_trigger": true
  },
  {
    "query": "この文章を少し短くして",
    "should_trigger": false
  }
]
```

**フィールド：**
- `query`: 実際のユーザー入力に近いテストクエリ
- `should_trigger`: そのスキルを使うべきなら `true`、使うべきでないなら `false`

標準は24件以上。`should_trigger: true` を最低12件、`false` を最低12件含める。`false` は明らかに無関係なものではなく、キーワードや状況が近いニアミスを中心にする。

---

## Codex routing decision

`run_eval.py` は各 query / run で `codex exec --output-schema` を使い、次の構造を要求する。

```json
{
  "should_use_skill": true,
  "confidence": 0.92,
  "reason": "The user wants to create a reusable skill and run evaluations."
}
```

**フィールド：**
- `should_use_skill`: Codex がこのスキルを使うべきと判断したか
- `confidence`: 0〜1 の信頼度
- `reason`: 短い判断理由

`run_eval.py` の集計出力では後方互換のため、従来の `trigger_rate`、`triggers`、`runs`、`pass` を維持する。ここでの `trigger` は Claude live trigger ではなく、Codex routing decision の `should_use_skill` を意味する。

`valid_runs` は成功した Codex 判定数、`errors` は timeout / command failure / parse failure 数を表す。`errors > 0` の query は、期待値が `should_trigger: false` でも pass にしない。評価基盤の失敗を negative eval の成功として扱わないため。

---

## history.json

Improveモードでのバージョン進行を追跡する。ワークスペースルートに配置する。

```json
{
  "started_at": "2026-01-15T10:30:00Z",
  "skill_name": "pdf",
  "current_best": "v2",
  "iterations": [
    {
      "version": "v0",
      "parent": null,
      "expectation_pass_rate": 0.65,
      "grading_result": "baseline",
      "is_current_best": false
    },
    {
      "version": "v1",
      "parent": "v0",
      "expectation_pass_rate": 0.75,
      "grading_result": "won",
      "is_current_best": false
    },
    {
      "version": "v2",
      "parent": "v1",
      "expectation_pass_rate": 0.85,
      "grading_result": "won",
      "is_current_best": true
    }
  ]
}
```

**フィールド：**
- `started_at`: 改善が開始されたISO タイムスタンプ
- `skill_name`: 改善されているスキルの名前
- `current_best`: 最高パフォーマーのバージョン識別子
- `iterations[].version`: バージョン識別子（v0、v1、...）
- `iterations[].parent`: このバージョンが派生した親バージョン
- `iterations[].expectation_pass_rate`: 採点からのpass rate
- `iterations[].grading_result`: "baseline"、"won"、"lost"、または "tie"
- `iterations[].is_current_best`: これが現在の最良バージョンかどうか

---

## grading.json

グレーダーエージェントからの出力。`<run-dir>/grading.json` に配置する。

```json
{
  "expectations": [
    {
      "text": "The output includes the name 'John Smith'",
      "passed": true,
      "evidence": "Found in transcript Step 3: 'Extracted names: John Smith, Sarah Johnson'"
    },
    {
      "text": "The spreadsheet has a SUM formula in cell B10",
      "passed": false,
      "evidence": "No spreadsheet was created. The output was a text file."
    }
  ],
  "summary": {
    "passed": 2,
    "failed": 1,
    "total": 3,
    "pass_rate": 0.67
  },
  "execution_metrics": {
    "tool_calls": {
      "Read": 5,
      "Write": 2,
      "Bash": 8
    },
    "total_tool_calls": 15,
    "total_steps": 6,
    "errors_encountered": 0,
    "output_chars": 12450,
    "transcript_chars": 3200
  },
  "timing": {
    "executor_duration_seconds": 165.0,
    "grader_duration_seconds": 26.0,
    "total_duration_seconds": 191.0
  },
  "claims": [
    {
      "claim": "The form has 12 fillable fields",
      "type": "factual",
      "verified": true,
      "evidence": "Counted 12 fields in field_info.json"
    }
  ],
  "user_notes_summary": {
    "uncertainties": ["Used 2023 data, may be stale"],
    "needs_review": [],
    "workarounds": ["Fell back to text overlay for non-fillable fields"]
  },
  "eval_feedback": {
    "suggestions": [
      {
        "assertion": "The output includes the name 'John Smith'",
        "reason": "A hallucinated document that mentions the name would also pass"
      }
    ],
    "overall": "Assertions check presence but not correctness."
  }
}
```

**フィールド：**
- `expectations[]`: 根拠付きの採点されたexpectation
- `summary`: 集計のpass/fail数
- `execution_metrics`: エグゼキュータのmetrics.jsonからのツール使用量と出力サイズ
- `timing`: timing.jsonのウォールクロックタイミング
- `claims`: 出力から抽出・検証されたクレーム
- `user_notes_summary`: エグゼキュータがフラグを立てた問題
- `eval_feedback`: （オプション）evalの改善提案。グレーダーが提起する価値のある問題を識別した場合のみ存在する

---

## metrics.json

エグゼキュータエージェントからの出力。`<run-dir>/outputs/metrics.json` に配置する。

```json
{
  "tool_calls": {
    "Read": 5,
    "Write": 2,
    "Bash": 8,
    "Edit": 1,
    "Glob": 2,
    "Grep": 0
  },
  "total_tool_calls": 18,
  "total_steps": 6,
  "files_created": ["filled_form.pdf", "field_values.json"],
  "errors_encountered": 0,
  "output_chars": 12450,
  "transcript_chars": 3200
}
```

**フィールド：**
- `tool_calls`: ツールタイプ別のカウント
- `total_tool_calls`: 全ツール呼び出しの合計
- `total_steps`: 主要な実行ステップの数
- `files_created`: 作成された出力ファイルのリスト
- `errors_encountered`: 実行中のエラー数
- `output_chars`: 出力ファイルの総文字数
- `transcript_chars`: トランスクリプトの文字数

---

## timing.json

実行のウォールクロックタイミング。`<run-dir>/timing.json` に配置する。

**キャプチャ方法：** サブエージェントタスクが完了すると、タスク通知に `total_tokens` と `duration_ms` が含まれる。これらを即座に保存すること — それ以外には永続化されず、後から回復できない。

```json
{
  "total_tokens": 84852,
  "duration_ms": 23332,
  "total_duration_seconds": 23.3,
  "executor_start": "2026-01-15T10:30:00Z",
  "executor_end": "2026-01-15T10:32:45Z",
  "executor_duration_seconds": 165.0,
  "grader_start": "2026-01-15T10:32:46Z",
  "grader_end": "2026-01-15T10:33:12Z",
  "grader_duration_seconds": 26.0
}
```

---

## benchmark.json

Benchmarkモードからの出力。`benchmarks/<timestamp>/benchmark.json` に配置する。

```json
{
  "metadata": {
    "skill_name": "pdf",
    "skill_path": "/path/to/pdf",
    "executor_model": "claude-sonnet-4-20250514",
    "analyzer_model": "most-capable-model",
    "timestamp": "2026-01-15T10:30:00Z",
    "evals_run": [1, 2, 3],
    "runs_per_configuration": 3
  },

  "runs": [
    {
      "eval_id": 1,
      "eval_name": "Ocean",
      "configuration": "with_skill",
      "run_number": 1,
      "result": {
        "pass_rate": 0.85,
        "passed": 6,
        "failed": 1,
        "total": 7,
        "time_seconds": 42.5,
        "tokens": 3800,
        "tool_calls": 18,
        "errors": 0
      },
      "expectations": [
        {"text": "...", "passed": true, "evidence": "..."}
      ],
      "notes": [
        "Used 2023 data, may be stale",
        "Fell back to text overlay for non-fillable fields"
      ]
    }
  ],

  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": 0.85, "stddev": 0.05, "min": 0.80, "max": 0.90},
      "time_seconds": {"mean": 45.0, "stddev": 12.0, "min": 32.0, "max": 58.0},
      "tokens": {"mean": 3800, "stddev": 400, "min": 3200, "max": 4100}
    },
    "without_skill": {
      "pass_rate": {"mean": 0.35, "stddev": 0.08, "min": 0.28, "max": 0.45},
      "time_seconds": {"mean": 32.0, "stddev": 8.0, "min": 24.0, "max": 42.0},
      "tokens": {"mean": 2100, "stddev": 300, "min": 1800, "max": 2500}
    },
    "delta": {
      "pass_rate": "+0.50",
      "time_seconds": "+13.0",
      "tokens": "+1700"
    }
  },

  "notes": [
    "アサーション 'Output is a PDF file' は両方の設定で100%pass — スキルの価値を識別できない可能性がある",
    "Eval 3は高い分散を示す（50% ± 40%）— 不安定またはモデル依存の可能性がある",
    "スキルなしの実行はテーブル抽出のexpectationで一貫してfail",
    "スキルは平均実行時間を13秒増加させるが、pass rateを50%改善する"
  ]
}
```

**フィールド：**
- `metadata`: ベンチマーク実行に関する情報
  - `skill_name`: スキルの名前
  - `timestamp`: ベンチマークが実行された時刻
  - `evals_run`: evalの名前またはIDのリスト
  - `runs_per_configuration`: 設定ごとの実行数（例：3）
- `runs[]`: 個別の実行結果
  - `eval_id`: 数値のeval識別子
  - `eval_name`: 人間が読めるeval名（ビューアのセクションヘッダーとして使用）
  - `configuration`: `"with_skill"` または `"without_skill"` でなければならない（ビューアはグループ化とカラーコーディングにこの正確な文字列を使用）
  - `run_number`: 整数の実行番号（1、2、3...）
  - `result`: `pass_rate`、`passed`、`total`、`time_seconds`、`tokens`、`errors` を持つネストされたオブジェクト
- `run_summary`: 設定ごとの統計集計
  - `with_skill` / `without_skill`: それぞれ `mean` と `stddev` フィールドを持つ `pass_rate`、`time_seconds`、`tokens` オブジェクトを含む
  - `delta`: `"+0.50"`、`"+13.0"`、`"+1700"` のような差分文字列
- `notes`: アナライザーからの自由形式の観察

**重要：** ビューアはこれらのフィールド名を正確に読み取る。`configuration` の代わりに `config` を使ったり、`result` の下にネストする代わりに `pass_rate` を実行のトップレベルに置いたりすると、ビューアが空/ゼロ値を表示する原因となる。benchmark.jsonを手動で生成する際は必ずこのスキーマを参照すること。

---

## comparison.json

ブラインドコンパレーターからの出力。`<grading-dir>/comparison-N.json` に配置する。

```json
{
  "winner": "A",
  "reasoning": "Output A provides a complete solution with proper formatting and all required fields. Output B is missing the date field and has formatting inconsistencies.",
  "rubric": {
    "A": {
      "content": {
        "correctness": 5,
        "completeness": 5,
        "accuracy": 4
      },
      "structure": {
        "organization": 4,
        "formatting": 5,
        "usability": 4
      },
      "content_score": 4.7,
      "structure_score": 4.3,
      "overall_score": 9.0
    },
    "B": {
      "content": {
        "correctness": 3,
        "completeness": 2,
        "accuracy": 3
      },
      "structure": {
        "organization": 3,
        "formatting": 2,
        "usability": 3
      },
      "content_score": 2.7,
      "structure_score": 2.7,
      "overall_score": 5.4
    }
  },
  "output_quality": {
    "A": {
      "score": 9,
      "strengths": ["Complete solution", "Well-formatted", "All fields present"],
      "weaknesses": ["Minor style inconsistency in header"]
    },
    "B": {
      "score": 5,
      "strengths": ["Readable output", "Correct basic structure"],
      "weaknesses": ["Missing date field", "Formatting inconsistencies", "Partial data extraction"]
    }
  },
  "expectation_results": {
    "A": {
      "passed": 4,
      "total": 5,
      "pass_rate": 0.80,
      "details": [
        {"text": "Output includes name", "passed": true}
      ]
    },
    "B": {
      "passed": 3,
      "total": 5,
      "pass_rate": 0.60,
      "details": [
        {"text": "Output includes name", "passed": true}
      ]
    }
  }
}
```

---

## analysis.json

ポストホックアナライザーからの出力。`<grading-dir>/analysis.json` に配置する。

```json
{
  "comparison_summary": {
    "winner": "A",
    "winner_skill": "path/to/winner/skill",
    "loser_skill": "path/to/loser/skill",
    "comparator_reasoning": "Brief summary of why comparator chose winner"
  },
  "winner_strengths": [
    "Clear step-by-step instructions for handling multi-page documents",
    "Included validation script that caught formatting errors"
  ],
  "loser_weaknesses": [
    "Vague instruction 'process the document appropriately' led to inconsistent behavior",
    "No script for validation, agent had to improvise"
  ],
  "instruction_following": {
    "winner": {
      "score": 9,
      "issues": ["Minor: skipped optional logging step"]
    },
    "loser": {
      "score": 6,
      "issues": [
        "Did not use the skill's formatting template",
        "Invented own approach instead of following step 3"
      ]
    }
  },
  "improvement_suggestions": [
    {
      "priority": "high",
      "category": "instructions",
      "suggestion": "Replace 'process the document appropriately' with explicit steps",
      "expected_impact": "Would eliminate ambiguity that caused inconsistent behavior"
    }
  ],
  "transcript_insights": {
    "winner_execution_pattern": "Read skill -> Followed 5-step process -> Used validation script",
    "loser_execution_pattern": "Read skill -> Unclear on approach -> Tried 3 different methods"
  }
}
```
