---
name: github-oss-repo-hardening
description: >
  GitHub で public-first の OSS repository を作成・公開・保護するときに使う。
  ユーザーが GitHub repository 作成、OSS 向けプロダクト名・リポジトリ名の検討、
  Rulesets、protected branch、main 保護、merge queue、GitHub Actions hardening、
  Dependabot、CodeQL、secret scanning、push protection、CODEOWNERS、OSS 公開前チェック、
  gh コマンドによる repository 設定自動化や token 権限の安全性に言及したらこのスキルを使う。
  単なるアプリ機能実装、PR レビュー、CI 失敗修正だけなら使わない。
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, Edit, Write
category: Security
created: 2026-06-17
status: active
purpose: public-first OSS repository を GitHub Free 前提で安全に立ち上げ、保護設定を順序立ててフォローする
argument-hint: "[owner/repo または repository 作成方針]"
---

# GitHub OSS Repo Hardening

public repository は release ではない。未完成でも `pre-alpha` と明示し、release / tag / package publish を止めたまま、GitHub Flow と repository protection を先に整える。

このスキルは、リポジトリ作成直後に「何をどの順番で固めるか」を案内し、ユーザーが作業した後の次ステップを追跡する。`gh` による自動化は、読み取り監査・dry-run・明示承認後の mutation に分ける。保存済みの broad な `gh` 認証を前提に repository mutation を実行しない。

## When I Activate

- ✅ public-first OSS repository を作る、または公開前後の GitHub 設定を固めたい
- ✅ OSS として公開する前に product name / repository name の妥当性を確認したい
- ✅ `main` ruleset / branch protection / direct push 禁止を設定したい
- ✅ 複数 PR を連続 merge するときに `main` を壊さないよう merge queue を設定したい
- ✅ Dependabot、CodeQL、secret scanning、push protection、GitHub Actions permissions を整備したい
- ✅ `gh api` で Rulesets や Actions 設定を自動化したいが token 権限が心配
- ✅ 新規 repository 作成後のセキュリティ checklist を順番にフォローしてほしい
- ❌ アプリケーション実装そのもの、通常のコードレビュー、CI failure debug だけの依頼

## Start Here

まず既知情報を確認する。ローカルや `gh repo view` で分かるものは質問せず調べる。分からない場合だけ一度に一つ質問する。

| 必須情報 | 調査または質問 |
|---|---|
| product / repo name | 現在名、候補名、OSS 公開前に改名可能か |
| owner/repo | `git remote -v`、`gh repo view`、またはユーザー入力 |
| owner 種別 | user / organization |
| visibility 方針 | public-first / private-before-public |
| 開発者数 | solo / trusted maintainersあり / open maintainer model |
| release 方針 | no release yet / pre-alpha / release予定あり |
| license | MIT / Apache-2.0 / AGPL / undecided |
| automation 方針 | manual UI / gh dry-run only / gh apply allowed |

推奨デフォルト:

- OSS 公開前に product name / repository name を先に決める。後から rename すると README、package、docs、remote、issue/PR URL、検索導線の変更が増える。
- 名前は lowercase の一語、5-8 文字程度、発音しやすく、repo / package / CLI に転用しやすいものを優先する。
- `ai-*`、`gpt-*`、`agent-*`、`*-tube`、`video-*` のような説明的・流行語的・既存大手連想が強い名前は、検索性と混同リスクの観点で慎重に扱う。
- solo OSS は `public-first + no release + branch PR + ruleset`。
- solo / small OSS は常設 `developer` ブランチを作らない。短命の `feature/*` / `fix/*` から protected `main` へ PR を出す。
- CD は `main` への merge ではなく、`v*` tag、GitHub Release、protected environment approval、または明示的な release workflow で発火させる。
- solo の初期 ruleset では `required_approving_review_count: 0` を推奨する。承認 1 件必須や CODEOWNER review 必須は、自分以外の maintainer がいないと merge 不能になることがある。
- verified commit signing が動作確認できるまで `required_signatures` は有効化しない。
- CI workflow が存在し、少なくとも一度成功してから required status checks を有効化する。
- Merge Queue は required status checks が安定し、GitHub Actions workflow が
  `merge_group` event でも required check を報告できるようになってから有効化する。
  PR 単体の CI 成功だけでは、queue 上の `main + queued PRs` 結合状態を検証したことにならない。
- Actions は workflow 作成前に repository default を締める。GitHub-owned/local actions 以外は明示 allowlist、full-length SHA pinning、default `GITHUB_TOKEN` read-only、fork PR は all external contributors approval を初期値にする。
- 個人アカウントの OSS では、Advanced Security / Dependabot / Secret Protection / CodeQL の repository mutation を `gh` 強権限で自動化しない。まず GitHub UI の手順と設定値を案内し、結果をチェックリストで確認する。
- ruleset 項目を質問するときは、必ず「その項目の意味」「有効/無効による影響」「推奨値」「A/B/C の回答選択肢」を提示する。項目名だけで質問しない。

## Operating Modes

| Mode | 使う場面 | 許可する操作 |
|---|---|---|
| `explain` | まず順序を知りたい | 手順説明、チェックリスト、UI パス提示 |
| `audit` | 既存 repo を確認したい | `gh` の読み取り専用コマンドのみ |
| `prepare` | 設定ファイルを作りたい | `README`、`SECURITY.md`、`CONTRIBUTING.md`、`CODEOWNERS`、Dependabot / CodeQL workflow のドラフト |
| `dry-run` | gh automation 前に確認したい | API payload 生成、実行予定コマンド提示。mutation はしない |
| `apply` | ユーザーが明示承認した | repository 設定変更、ruleset 作成、Actions 権限変更など。実行前に対象 repo と変更内容を再確認 |

`apply` は対象・payload・実行主体が一致する明示承認に紐付ける。有効な既存task承認があれば再質問しない。特に visibility 変更、ruleset 変更、Actions permissions、secret / variable / deploy key、release / package publish は一括自動実行しない。

Mutation を自動化する場合の推奨は manual/browser UI を第一候補、次に selected repository + short expiration の fine-grained PAT を `GH_TOKEN` で一時的に渡す方式。`gh auth login` で保存済みの広い credential を使った mutation は避ける。

## Credential Context Before Automation

診断には `scripts/gh_credential_context.py` と
`references/gh-credential-context.md` を使う。GH_TOKEN / GITHUB_TOKEN と
Enterprise用selectorの優先順位をtarget hostごとに判定し、秘密値を表示・保存・hashしない。
過去の未解明401と現在の成功、権限不足、runtime拒否、機能非対応、通信失敗を分ける。
無効な選択credentialを保存済み認証へのfallbackで迂回しない。
診断によるapply資格拡大はしない。選択されたGH_TOKEN以外は既存の明示
`--allow-stored-gh-auth`を必要とし、このflagの名前に反して実際の選択元を変えない。

ruleset helperのdry-runでは、実際にapplyする環境のprivateな
`--context-out`を保存する。applyは同じ`--executor-surface`、`--context-in`、
`--payload-in`と既存の承認条件を使い、環境差分があればmutation前に停止する。
有効な承認済みtaskの範囲は再質問しない。read成功やtokenの存在だけで
Administration: write・fine-grained scope・mutation許可があると判断しない。
同一selector内のtoken交換や同一場所の保存account変更はpresence-onlyでは検知できない。

## Product Identity Before Public Hardening

public-first OSS では、repository protection より前に product name / repository name を決める。名前が弱いまま public 化すると、README、package name、issue URL、search result、community mention のすべてに影響する。

### Naming Criteria

| Criterion | Prefer | Avoid |
|---|---|---|
| Shape | lowercase one-word name, roughly 5-8 chars | long sentence-like names, unnecessary hyphens |
| Memorability | pronounceable, easy to say aloud, visually distinct | vowel-stripped strings that require spelling every time |
| OSS fit | usable as repo, package, CLI command, org/project brand | names that only work as a marketing title |
| Meaning | enough semantic room to grow; faint product signal is OK | over-literal descriptions like `ai-video-agent-platform` |
| Searchability | exact-name search is sparse enough to own | generic words, hot AI terms, crowded framework terms |
| Confusion risk | low resemblance to large platforms or trademarks | `tube`, `youtube`, `tiktok`, `gpt`, or close variants |
| Future scope | can survive feature pivots within the same product idea | names tied to one implementation detail or provider |

### Naming Workflow

1. Capture the current project name and whether it can still be renamed.
2. Ask whether the user wants descriptive naming or brandable one-word naming. For OSS tools, default to brandable one-word naming unless the user says otherwise.
3. Generate candidates that can work as product name, repository name, package name, and CLI name.
4. Filter out names that are too close to major platforms, too generic, too AI-trend dependent, or hard to pronounce.
5. Before adoption, check current collisions:
   - GitHub exact and close repository names
   - npm package exact match, if JavaScript/TypeScript may be used
   - PyPI package exact match, if Python may be used
   - basic web search
   - domain availability when the project needs a public site
6. Do not make legal or trademark safety claims. Record that trademark review is separate if the project becomes more than experimental OSS.
7. Once approved, rename public-facing docs and repository references before heavy hardening work.

Good one-word OSS names often behave like `ccusage` or `kanary`: compact, memorable, package-friendly, and not forced to explain the whole system.

## Ordered Workflow

1. **Repository Baseline**
   - product name / repository name が OSS 公開に耐えるか確認する。
   - 公開前に rename できるなら、README / package / docs / remote 設定へ広く展開する前に決める。
   - owner/repo と visibility を確認する。
   - `README` に `pre-alpha`、`experimental`、`not production ready`、`no release yet` を明記する。
   - branch policy は、短命 branch -> PR -> protected `main` を基本にする。常設 `developer` ブランチは、複数人の staging 統合先など明確な理由がある場合だけ検討する。
   - `LICENSE`、`SECURITY.md`、`CONTRIBUTING.md`、`CODEOWNERS` を置く。
   - `CODEOWNERS` では `.github/CODEOWNERS` と `.github/workflows/**` 自体を owner 管理にする。
   - wiki / packages / releases / discussions / pages など不要機能を最小化する。

2. **Local / History Secret Hygiene**
   - `.env`、private keys、tokens、Vault 内部メモ、個人情報が repository に入っていないか確認する。
   - history scan を行い、見つかった secret は削除ではなく revoke / rotate を前提に扱う。

3. **GitHub Actions Hardening**
   - workflow を追加する前に、repository の Actions default を固める。
   - Actions permissions は `Allow OWNER, and select non-OWNER, actions and reusable workflows` を基準にする。
   - GitHub-owned actions は許可し、Marketplace verified creators は初期状態では許可しない。必要な third-party action は個別 allowlist と full-length SHA pinning で追加する。
   - repository の default `GITHUB_TOKEN` を read-only / `Read repository contents and packages permissions` にする。
   - workflow 内にも `permissions:` を明示する。
   - Merge Queue を使う required workflow には `pull_request` に加えて `merge_group` trigger を追加する。
     GitHub Actions の required check が `merge_group` で走らないと、queue に入れた PR は required check 未報告で詰まる。
   - `merge_group` trigger は `pull_request` / `push` とは別 event として扱う。
     `main` 向け workflow なら次を基準にする。

```yaml
on:
  pull_request:
    branches: [main]
  merge_group:
    branches: [main]
```

   - Actions が PR を create / approve できない設定を確認する。
   - fork PR workflow は `Require approval for all external contributors` を初期値にする。
   - artifact/log retention は初期値 `30 days`、cache retention は `7 days`、cache size limit は `10 GB` を基準にする。
   - `pull_request_target` は原則禁止。必要なら threat review を要求する。
   - third-party actions は full-length SHA pinning を推奨する。
   - self-hosted runner は public fork PR と組み合わせると危険なため、原則使わない。必要なら個別 threat review と人間承認を要求する。

4. **Security Features**
   - 個人アカウントでは GitHub UI を第一候補にする。`gh` に repository administration write を付ける自動化は、ユーザーが明示的に選んだ場合だけ別判断にする。
   - Advanced Security 画面で dependency graph / Dependabot alerts / Dependabot security updates / Dependabot malware alerts / grouped security updates を有効化するよう案内する。
   - Dependabot version updates は `.github/dependabot.yml` で管理する。GitHub UI の空テンプレート `package-ecosystem: ""` は無効なので使わない。
   - package manifest が無い初期 repo では `package-ecosystem: "github-actions"` だけを入れ、npm/pip などは manifest 作成後に追加する。
   - Secret Protection と Push protection を有効化するよう案内する。
   - CodeQL は対応言語がある、または近く追加されるなら Default setup / Default query suite から始める。Security-extended / Security-and-quality や Advanced setup は、初回 alert triage 後に検討する。
   - CodeQL / code scanning を ruleset required gate にするのは、少なくとも一度成功し、check 名と alert 運用が安定してからにする。
   - Private vulnerability reporting は OSS では有効化推奨。ただし secret scanning / Dependabot とは別機能として説明する。
   - Copilot Autofix は任意。レビューや threat modeling の代替として扱わない。

5. **Main Ruleset**
   - 最初は direct push / force push / deletion を防ぐことを優先する。
   - solo の初期状態では PR 必須、approval 0、review thread resolution true、linear history true、allowed merge methods `["squash", "rebase"]` を基準にする。
   - reusable ruleset automation では target branch を `~DEFAULT_BRANCH` にする。`refs/heads/main` 固定は特別な理由がある場合だけ使う。
   - `creation` / `update` rules は solo OSS baseline では入れない。特に bypass actors 空で `update` を有効にすると default branch が更新不能になりやすい。
   - CI が成功してから required status checks を追加する。
   - 複数 PR を同日に merge する repo、または PR 同士の結合リスクがある repo では、
     required status checks 追加後に Merge Queue を検討する。
   - Merge Queue は `main` など具体的な branch protection / repository-level ruleset に設定する。
     wildcard `*` を使う branch protection rule では有効化できない。
   - Merge Queue の目的は、PR を latest base と queue で先行する PR 群に重ねた一時 merge group 上で
     required checks を通してから merge すること。手動で各 PR branch を main update し続ける代替になる。
   - GitHub Actions 以外の CI を使う場合は、`merge_group` webhook か
     `gh-readonly-queue/{base_branch}` prefix の一時 branch に対応して required status を返す必要がある。
   - 初期値は build concurrency を小さめ、solo / small OSS では `1` から始めるのが安全。
   - commit signing が verified で運用できることを確認してから required signatures を追加する。
   - CodeQL/code scanning、code quality、Copilot code review は初期 baseline に入れず、各 integration が動作確認できてから optional gate として検討する。
   - disabled/dry-run payload を確認してから active 化する。GitHub Free では enterprise の `evaluate` が使えない場合があるため、実際の UI/API で確認する。
   - bypass actor は原則空。必要な場合は理由と期限を記録する。

6. **Release / Package Safety**
   - `v0.1.0` まで release / package publish をしない方針を README に残す。
   - production deploy / public release を `main` merge だけで発火させない。`main` は統合ブランチ、release は明示 gate として分ける。
   - publish token は置かない。将来必要になったら OIDC / GitHub App / fine-grained token を再検討する。
   - artifact attestation / SBOM は release 設計が始まった時点で追加する。

7. **Verification**
   - `main` へ direct push が拒否されることを確認する。
   - branch -> PR -> checks -> merge の流れを一度通す。
   - Merge Queue を有効化した場合は、PR を `Add to merge queue` し、
     `merge_group` required checks が走ってから main に入ることを確認する。
   - repository settings audit を保存する。
   - 次回棚卸し日を決める。

## Automation Policy

`gh` は便利だが、repository administration を変更するには強い権限が必要になる。自動化の前に、何を読むだけで、何を書き換えるかを分ける。

### Safe-ish Read Commands

これらは原則として読み取り専用。ただし token を画面に出すコマンドは実行しない。

```bash
gh auth status -h github.com
gh repo view OWNER/REPO --json nameWithOwner,visibility,isPrivate,defaultBranchRef
gh api repos/OWNER/REPO/rulesets
gh api repos/OWNER/REPO/actions/permissions
gh api repos/OWNER/REPO/actions/permissions/workflow
gh api -i repos/OWNER/REPO/vulnerability-alerts
gh secret list --repo OWNER/REPO
```

禁止:

- `gh auth token` の実行や token 表示
- token をログ、Vault、issue、PR、shell history に残すこと
- secret 値の読み取りや再表示

### Token Guardrails

- `gh auth token`、`gh auth status --show-token`、token を含む header/env の表示を禁止する。
- `gh auth status` では fine-grained PAT の selected repositories、permission matrix、expiration までは十分に監査できない。
- Mutation 前に auth source、対象 repository、必要 permission、rollback、実行 endpoint を dry-run manifest に出す。
- Raw `gh api` は endpoint allowlist と permission profile が一致する場合だけ扱う。
- broad stored auth しかない場合は mutation を停止し、manual UI か temporary `GH_TOKEN` を案内する。

### Mutations That Need Explicit Approval

- `gh repo create`
- repository visibility / feature settings の変更
- ruleset create / update / delete
- branch protection 変更
- Actions permissions / fork approval 設定変更
- vulnerability alerts / Dependabot security updates の有効化
- repository secrets / environment secrets / deploy keys の追加
- release / tag / package publish

ユーザーが `apply` を許可した場合も、実行直前に次を表示する:

| Field | Required |
|---|---|
| target repository | `OWNER/REPO` |
| mutation summary | 何を変えるか |
| required permission | 例: repository Administration write |
| rollback | 戻し方、または戻せない理由 |
| token hygiene | 実行後に token / auth を棚卸しする手順 |

### Default Branch Ruleset Script

Ruleset の baseline payload を作る場合は `scripts/apply-default-branch-ruleset.py` を使う。

Dry-run (use the intended executor with the user-managed credential already configured):

```bash
python3 scripts/apply-default-branch-ruleset.py --repo OWNER/REPO \
  --payload-out /PRIVATE/DIR/ruleset.json \
  --context-out /PRIVATE/DIR/context.private.json
```

Apply:

```bash
python3 scripts/apply-default-branch-ruleset.py \
  --repo OWNER/REPO \
  --mode apply \
  --yes \
  --payload-in /PRIVATE/DIR/ruleset.json \
  --context-in /PRIVATE/DIR/context.private.json
```

Apply には selected repository の short-lived fine-grained PAT と `Administration: write` を要求する。
保存済み `gh` credential での apply は、ユーザーがそのリスクを明示的に受け入れた場合だけ `--allow-stored-gh-auth` で許可する。
このスクリプトは token 値を表示しない。apply では dry-run で確認した payload を `--payload-in` で渡す。
既存 ruleset を置換する場合は、dry-runとapplyの両方に `--replace-existing` を指定し、その置換方針をreviewする。

Baseline payload:

- `target`: `branch`
- `enforcement`: `active`
- `conditions.ref_name.include`: `["~DEFAULT_BRANCH"]`
- `bypass_actors`: `[]`
- rules: `deletion`, `non_fast_forward`, `required_linear_history`, `pull_request`
- pull request parameters:
  - `allowed_merge_methods`: `["squash", "rebase"]`
  - `required_approving_review_count`: `0`
  - `required_review_thread_resolution`: `true`
  - `dismiss_stale_reviews_on_push`: `true`
  - `require_code_owner_review`: `false`
  - `require_last_push_approval`: `false`

Merge Queue is not part of the initial solo-maintainer baseline payload until
required checks are stable and workflows report on `merge_group`. Add it as a
second hardening pass after CI has passed at least once and queue behavior has
been verified.

## Output Format

通常はこの形で返す。

```markdown
**現在位置**
Phase: <baseline/actions/ruleset/dependabot/verify>
Mode: <explain/audit/prepare/dry-run/apply>

| Status | Current State | Desired State | Verification | Next Action |
|---|---|---|---|---|
| pending | ... | ... | ... | ... |

**次にやること**
1. ...

**必要なら実行するコマンド**
```bash
...
```

**確認**
A. 手動 UI で進める
B. gh dry-run まで作る
C. gh apply まで許可する
```

質問が必要な場合は一度に一つだけにする。推奨回答も添える。

## References

必要に応じて以下を読む。

- `references/checklist.md`: public-first OSS repository hardening checklist
- `references/advanced-security-ui-baseline.md`: 個人アカウント向け Advanced Security UI 設定値
- `references/actions-hardening-baseline.md`: repository Actions General settings baseline
- `references/default-branch-ruleset-baseline.json`: import/export 向け default-branch ruleset baseline
- `references/gh-automation.md`: `gh` / REST API automation policy and command examples
- `references/review-rubric.md`: review checklist for subagent or human review
- `templates/main-ruleset-solo.json`: solo maintainer 向け initial ruleset payload
- `scripts/audit-gh-repo.sh`: read-only repository audit helper
- `scripts/apply-default-branch-ruleset.py`: default-branch ruleset dry-run/apply helper

`gh` mutation を検討する前に必ず `references/gh-automation.md` を読む。

## Validation

スキルを適用したら、最低限この検証を行う。

- repository baseline docs が揃っている
- direct push / force push / deletion を防ぐ設定がある
- Actions default permission が read-only
- fork PR が secrets / write token を受け取らない
- Dependabot alerts / version updates の方針がある
- CodeQL の要否を判断している
- secret scanning / push protection の有効可否を確認している
- merge queue を使う場合は `merge_group` trigger / required checks / queue merge を確認している
- `gh` mutation は明示承認と target repo 再確認なしに実行していない
