# launchd Integration

`launchd`は04:00にdaily専用runnerを起動するだけとする。汎用automation ID dispatcherへdaily publication権限を追加しない。

```text
launchd 04:00
  -> daily dedicated runner
  -> deterministic standing-task snapshot into collection staging
  -> fixed fetch + lightweight Vault isolation snapshot (no local commit patches)
  -> collection Codex process (Web/search, run staging write only)
  -> deterministic artifact validation
  -> deterministic collision-safe artifact target plan
  -> short cooperative publication lock + bounded per-Vault stable recapture
  -> per-Vault mode hint (sweep / own_only / blocked)
  -> target/snapshot conflict on retry-safe boundary: unlock and bounded full replan/review
  -> completed peer Vault: digest-bound carried target/commit review, no reinstall/recommit
  -> materialize local-only commit metadata and patches for publication only
  -> post-collection authorization snapshot into isolated review input
  -> captured dirty Git blobs and local-only commit patches into isolated review input
  -> core artifact + residual sweep review Codex process (read-only, no search/network)
  -> deterministic local commit helper (sweep or isolated-index own_only, no network)
  -> initial fixed runner pushes (manifest-bound object IDs -> refs/heads/main)
  -> success-only Discord notification (immutable User Vault commit URL, idempotent receipt)
  -> deterministic push evidence hunk + sealed HEAD/index/worktree candidates
  -> evidence review Codex process (read-only, no search/network)
  -> deterministic evidence commit + fixed Agents main push
```

## Runtime Files

| Tracked source | Runtime destination |
|---|---|
| `assets/run-daily-it-news-vulnerability-check.sh` | daily automation workdir |
| `assets/daily-it-news.collect.prompt.md` | same workdir |
| `assets/daily-it-news.review.prompt.md` | same workdir |
| `assets/daily-it-news.evidence-review.prompt.md` | same workdir |
| `summarize-it-news/scripts/collect-public-sources.py` | daily automation workdir |
| `summarize-it-news/references/it-news-sources.json` | same workdir |
| `assets/automation.env.example` | copy manually to ignored `automation.local.env` and fill local values |
| `references/collection-result.schema.json` | same workdir |
| `references/publication-review-result.schema.json` | same workdir |
| `references/publication-commit-result.schema.json` | same workdir |
| `references/evidence-review-result.schema.json` | same workdir |
| `references/automation-result.schema.json` | same workdir |
| `scripts/resolve-runtime-context.py` | same workdir |
| `scripts/fetch-vault-main.py` | same workdir |
| `scripts/capture-vault-state.py` | same workdir |
| `scripts/determine-publication-modes.py` | same workdir |
| `scripts/validate-collection-result.py` | same workdir |
| `scripts/install-verified-artifacts.py` | same workdir |
| `scripts/commit-reviewed-publication.py` | same workdir |
| `scripts/validate-publication-review.py` | same workdir |
| `scripts/push-committed-heads.py` | same workdir |
| `scripts/send-it-news-discord-notification.py` | same workdir |
| `scripts/prepare-publication-evidence.py` | same workdir |
| `scripts/commit-push-publication-evidence.py` | same workdir |
| `scripts/evidence_hunk.py` | same workdir |
| `scripts/git_diff_digest.py` | same workdir |
| `scripts/isolated_git_transport.py` | same workdir |
| `scripts/atomic_file_ops.py` | same workdir |
| `scripts/trusted_gitleaks.py` | same workdir |
| `scripts/gitleaks-default.toml` | same workdir |
| `scripts/prepare-codex-output-schema.py` | same workdir |
| `scripts/validate-canonical-result.py` | same workdir |
| `scripts/stage-standing-task.py` | same workdir |
| `scripts/stage-dirty-review-inputs.py` | same workdir |
| `scripts/prepare-publication-review-context.py` | same workdir |
| `scripts/run-pinned-review.py` | same workdir |
| `scripts/verify-runtime-release.py` | same workdir |
| `scripts/interpret-automation-result.sh` | same workdir |
| generated `runtime-release-manifest.json` | same workdir (ignored, deployment-owned) |

配備はPR前受入とmerge後releaseの二段階で行う。PR前受入では、独立review済みのfrozen task-branch sourceをbackup付きでcanonical production runtimeへcopyし、各source/destinationのSHA-256とfile mode一致を確認する。task worktreeをproduction runtime pathとして参照しない。コピー完了後は配備対象の全runtime file（modeとSHA-256）およびsource commitを`runtime-release-manifest.json`へ封印し、runner起動時の`verify-runtime-release.py`が毎回照合する。manifest欠落、差替え、mode変更、digest不一致はpublication前にfail closedする。実環境E2E成功後だけsource commit/push/PRへ進み、merge後は最新mainの同一filesからmanifestを再生成してcanonical runtimeへ再配備する。特にdirect executionされる`collect-public-sources.py`、`run-pinned-review.py`、`send-it-news-discord-notification.py`、`verify-runtime-release.py`は、両段階でtracked sourceとruntimeの双方がexecutableであることをgateで検証する。

File Provider/iCloud上の既存ファイルは、GitのHEAD・index・dirty blob OID・mode・mtimeをcaptureしてから扱い、publication helperが上書きしない。capture時に既存dirty fileの実体化だけがタイムアウトした場合は、該当entryを`unavailable`として封印し、そのVaultのmodeを`own_only`へ下げる。GitのHEAD/index/remoteが安定し、今回の新規artifact targetが競合しない限り、クラウドの同期待ちで新規artifactのpublication全体を停止しない。`own_only` commitは既存dirty/staged pathを共有indexへ追加せず、targetだけを隔離indexからcommitする。GitのCASとtarget reservationで実行中の既存ファイル更新・競合を検知し、競合時だけbounded replanまたはVault単位のblockedへ移行する。

`automation.local.env`には`HERMES_BIN`のabsolute pathと`DISCORD_NEWS_TARGET=discord:<channel-id>`を置く。Discord credentialやtokenはこのfileへ複製せず、Hermesのmachine-local configでユーザーが管理する。runnerは両Vault initial pushの成功とlocal/remote head一致を確認した後だけ通知し、User Vault GitHub remoteとpushed commit SHAからimmutable summary URLを作る。通知のintent/result/delivery receiptはruntimeのowner-only `notification-state`へ保存し、初回state directoryを親directoryまでfsyncする。既送信扱いにするのはnumeric message ID、attempt、run ID、bounded digestを再検証できるreceiptだけとし、run rootにはraw Hermes outputではなくdigest、message ID、stable error codeだけを残す。senderがoutputを残さず失敗した場合もrunnerはsanitizedなambiguous resultを生成してevidence hunkをfinalizeし、publicationをrollbackせず`notification_failed`・terminal exit 75で示す。

正本のresult schemaはstate-dependent constraintを保持する。runnerは各run配下へCodex Structured Outputs対応subsetを生成してAPIへ渡し、生成結果は各phaseのdeterministic validatorで正本契約に照合する。互換schemaだけをpublication可否の判定に使わない。

collection Codex processへiCloud上のstanding taskを直接読ませない。resolverが検証した正本taskをrunner側の専用helperでrun stagingへ0600・exclusive createし、collection contextにはsnapshot pathだけを渡す。これによりcollectionのVault非アクセス境界を維持し、launchd配下のNode/CodexにVault全体のTCC権限を要求しない。

model-discovered fallbackは、agentがsummary/advisoryをcompleteとして返す前に、runtime collectorのread-only/no-output `--check-resolutions <canonical-request>`でexact requestをpreflightする。collectorはOS account databaseからhomeを取得し、`AutomationWorkspaces/codex/daily-it-news-vulnerability-check`へcanonical runtimeを外部固定する。実行file自身がそのexact absolute regular non-symlink pathと一致しないコピーを拒否し、requestもruntime直下のdirect `logs/date/same-date-run-id/staging/source-resolutions.json`だけをlexical pathのまま許可する。中間symlink/non-directory、nested fake runtime、alternate catalog/manifestをfetch前に拒否する。check/verify modeはいずれもcatalogやmanifestのagent指定を受け付けず、runner verifyはcanonical requestとexact run-local outputだけを受け取る。manifestのcatalog SHA-256・source count/order/name/countsが一致しない場合もfetch前に拒否する。agentへVault writeやGit accessは追加しない。listing/category候補が不完全なら、agentは最大3つの公式個別記事候補へ置換して再検証する。runnerはagent終了後、raw resultの正本schema検証を先に完了してから同じcollectorを`--verify-resolutions <canonical-request> <canonical-output>`で独立実行し、catalog/manifest bindingを再検証してrun root直下へexclusive createしたverified evidenceだけをpublication authorityとして使う。projectionはこの順序の後に限る。agent preflightの成否だけを信頼してpublicationしてはならない。

collection agent出力は`collection-agent-result.json`、元summary、元advisoryとして不変保持する。raw resultの正本schema検証、post-response verifier、projectionの順を固定する。post-response verifierのsealed evidenceに`access_constraint`がある場合、runnerは確認済みサイト一覧の該当7列rowについて理由cellだけをexact constraint codeへ決定論的に投影する。sealed evidenceが`fetched`またはverified fallbackであるrowは、元のnon-negativeな期間内件数を変えず、0件を`対象期間記事なし`、正数を`取得済み`へ対応させる2値間の状態cellだけを投影する。アクセス制約との相互変換、件数、method、URL、reason、記事内容は補正せず、件数自体は完全semantic validationでsealed date evidenceと再照合する。canonical summaryのSHA-256変更に伴い、既にraw validation済みのadvisory同一run summary参照hashだけを更新する。raw artifactとcatalog、manifest、verified resolutionsはbounded descriptor readの前後でstable identityを照合し、receiptは実際に消費した3証拠fileのpathとSHA-256、および理由・状態correctionを束縛する。canonical artifactsはstaging内の新規0700 `canonical-artifacts`、canonical resultは`collection-result.json`、digest-bound receiptは`collection-normalization.json`へ0600・no-follow・exclusive createする。raw bytes、mode、mtimeは変更しない。missing/duplicate/foreign rowや、許可した2種の低entropy cell以外のartifact、coverage、URL、date、path、hash、run identity不正は補正対象にせず停止する。canonical resultは正本schemaと完全なcollection semantic validationを再通過した後だけ後続publicationへ渡す。

authorization taskはnetwork-enabled collection終了後に別のreview input directoryへ0600・exclusive createし、no-network publication phaseへsnapshot pathだけを渡す。collection processからauthorization evidenceを参照可能にしない。書き先はrun-localの新規fileなのでクラウド同期待ちは行わない。一方、読み元はFile Provider上の既存・固定evidenceなので、`EDEADLK` / `EAGAIN` / `EBUSY`と、descriptor read前後のidentityが変化するprovider churnだけを最大30回・1秒間隔で新しいdescriptorから再試行する。毎回の読取は引き続きstable identityを必須とし、authorization contentは再試行後もresolverで固定したdigestと一致しなければならない。permission、digest、symlink、UTF-8、sizeなどの恒久エラーは即時fail closedし、連続churnもretry上限でfail closedする。runtime contextはdirectory catalogだけでなく、その後の認可・standing task・Vault Git metadataを含む既存pathのread-only解決attempt全体を同じbounded windowでやり直す。Git subprocessが同じ3 errnoのOS文言を返した場合も同一分類とし、それ以外のexit、timeout、validation errorは再試行しない。

Codexへ渡すpromptはCLI引数へ展開せずstdinから供給し、macOSのargument-size上限に依存しない。実行は`run-pinned-review.py`を経由し、no-follow descriptorでrequestを一度だけ安定読込してmetricsのrequest SHA-256・文字数・bytesと照合し、その同一bytesをpipeで子processへ渡す。対象descriptorには`O_NONBLOCK`も付け、FIFO/deviceへの差替えでopen/readが無期限に停止しないようにする。これによりmetrics計測後のpathname差替えや再読込で、監査対象と異なるpromptが実行されるTOCTOUを防ぐ。publication/evidence reviewへinlineするcontextは、完全なdigest-bound context fileを保持したまま、`prepare-publication-review-context.py`が上限付きprojectionへ変換する。`index_entries`は常に省略し、dirty/history residual配列が大きい場合はcount・SHA-256・bounded sampleへ縮約する。省略対象とrequestの文字数・bytes・prompt/projection内訳はrun rootのmetricsへ記録し、projectionがresidualを省略したVaultはreviewerが`sweep`を承認せず`own_only`へ切り替える。local-ahead historyの省略（commit配下の`changed_paths`または長大messageを含む）は安全なancestor検証ができないため当該Vaultを`blocked`にする。metricsのSHA-256とpublication context digestは`validate-publication-review.py`が再検証し、このmode floorを弱めるreview resultを拒否する。reviewは`index_sha256`、staged path、dirty/history digest、sealed snapshotを使い、完全なindex/residual列をLLM contextへ複製しない。`index_sha256`と`index_identity`はatomic shared-index CASのraw file contractとして保持するが、Gitのread-only commandが更新し得るstat-cache serializationなので、carry/replan時のsemantic staged-state identityには使わない。semantic staged-stateは`staged_paths`とcanonical `index_entries`で比較する。bounded omissionで省略されたlocal history/changed pathsとblocked residual pathは、mode floorを満たす場合に限りsealed snapshotからcanonical resultへ復元する。evidence review processの非0終了、入力上限拒否、result欠落、canonical validation失敗は、raw stderrをresult/statusへコピーせず、`reason_code`、process/status、result有無、stderr SHA-256を`evidence_review`構造体へ保存する。raw stderrは0600 private logにだけ保持し、finalizerとterminal statusへは構造化診断だけを渡す。

publication reviewのagent出力は`publication-review-agent-result.json`として不変保持し、canonical resultとSHA-256 normalization receiptを別fileへexclusive createする。agentが`own_only + quality_ok/deferred`を選んだVaultだけ、runnerは封印済みpre-stateとdirty snapshot manifestから全dirty pathを復元し、residual三配列の不足列挙を決定論的に補完する。既存reasonはexact dirty path、unique、nonemptyの場合だけ採用し、guardがdeferしたentryはsealed `materialization_reason`を優先する。外部path、重複、snapshot/context driftでは補完せず停止する。core review、mode、owned/commit/history/artifact bindingは変更対象外であり、canonical schemaとsemantic validatorを再通過したresultだけがpublication authorityになる。

pre/post-collectionの軽量captureはlocal-only commit patchを生成しない。collection成功後、Vaultが変化していてもcollectionを失敗扱いにせず、HEAD、index、dirty content/mode/mtime、Git control-planeの差をVaultごとの`own_only`制約へ変換する。publication時点がbounded retryで安定したVaultだけ、dirty fileをcapture済みGit blob OIDからreview inputへ0600・exclusive createし、local-ahead commitのhash、parents、tree、message、changed paths、first-parent patch digestとpatchを同じisolated review inputへ0600・exclusive createする。片方のsnapshotだけが安定しない場合は、そのVaultの最新identityを`vault_state_snapshot_unstable`として封印し、live bytesを再読込せず`blocked`にする。安定したpeer Vaultのreview・publicationは継続する。sealed residual guardはdirty candidateとreview済みHEADのno-index差分だけを検査し、新規machine-home path、`.obsidian/`、pinned Gitleaks v8不合格を`deferred`へ変換してVault単位のmode floorを`own_only`にする。guard不合格entryのbytesはreviewerへ渡さず、既存fileを変更しない。publication reviewはVault上のdirty fileやlocal-only historyを直接読まず、manifestでidentityとSHA-256に結合されたsnapshotだけを検査する。

cooperative publication lockはstable snapshotとmode hintの固定にだけ使い、長時間のreview中は保持しない。解放前にlock fileのPIDが実行中runner自身と一致することを確認し、別processのlockへ置き換わっていた場合は削除しない。lock解放後の競合はexact snapshot、expected parent、CASでfail closedする。

plan後のtarget競合またはcommit前のretry-safeなsnapshot driftを検出した場合は、artifactも既存差分も上書きせずlockを解放し、最新stateからtarget plan、mode hint、reviewをbounded replanする。target競合が上限まで継続した場合は競合したVaultだけを`artifact_target_replan_exhausted`で`blocked`にし、peer Vaultを続行する。review後のinstaller直前競合が3回継続した場合も、committerが報告したroot-cause Vaultだけを固定して4回目の最終peer-only plan/review/publicationを行う。installerはGit-privateかつ同一filesystemのreservationへartifactを`O_EXCL`で生成し、検証済み同一inodeをhardlink no-replaceでtargetへ公開する。SHA-256、stable device/inode identity、size、mode、Vault parent chain、quarantine root chain、reservation identityをreceiptへ封印し、File Providerで揮発する`mtime/ctime`だけの非同期正規化は許容する。別inode、content、size、mode変更はfail closedとし、失敗cleanupでもreservationのdurable nameを残す。第三者entryはentry typeに依存しないdescriptor-relative no-replace renameで元のpathへ戻し、競合時は上書きや削除をせずreservationへ保持して停止する。committerはreservation descriptorからblobを読み、targetとreservationのnamed inodeをHEAD更新直前・直後に再検証する。片側だけ今回commitを作成済みでpeerがmutation前にretry-safe失敗しても、成功側commitを取り消さない。失敗側だけをbounded replanし、成功側は前attemptのcanonical partial result digestとcompleted Vaultのsemantic resumable stateを新contextへ結合し、同じtargetを維持した`own_only`/`commit_required=false` reviewでlocal-ahead artifactを再検証する。未完了peerは旧snapshot一致を要求せず、新snapshotから再planする。raw index stat-cacheだけの差はsemantic driftに数えないが、HEAD、remote/history、dirty digest、staged paths、canonical index entries、artifact commit/blobの差は拒否する。成功artifactは再install・suffix作成・再commitせずfixed pushへ引き継ぐ。別processのpartial resumeではpathnameからreceiptを再生成せず、receipt不一致時は第三者fileを削除しない。

fetch後のhistory relationは収集の可否には使わない。`equal`または安全な`local_ahead`はpublication可能で、既存commit境界を維持したままreview・secret scan・fixed pushへ含める。unsafe local-ahead、`remote_ahead`、`diverged`、active Git operationは自動pull/rebaseで解消せず、該当Vaultだけ`blocked`とする。安定したdirty residualがguard/reviewを通れば`sweep`、失敗すればその内容を触らず`own_only`へdowngradeする。

`own_only`は共有indexへ`git add`しない。一時indexからartifact-only tree/commitを作成し、commit changed paths、expected parent、old OID付きCASを検証する。commit後はartifact entryだけを共有indexへ同期し、既存staged entry、dirty blob、mode、mtime、porcelainを再照合する。deferred residualが存在しても両Vaultの今回artifactとstanding evidenceがfixed push済みならterminal statusは0とする。

HEAD/index transactionのprivate entry、journal、rename capability probe、evidence temporary、通常publicationとevidence finalizationの一時`GIT_INDEX_FILE`は、検査後のpathname `unlink`でcleanupしない。一時indexは一意な0700 private directoryの未使用childへ作成し、使用後はdescriptor-relative no-replace renameでprivate retentionへ移す。全entryは移動後のdevice/inode、regular、size、mode、sealed content contractを保持先descriptorから再検証する。検査と移動の間に同一アカウントの別processがpathnameを差し替えた場合は、移動された第三者inodeを削除せず保持したままfail closedにする。review済みexpected indexのbackupはopen descriptorへ束縛したままhard-linkし、作成backupとcanonical nameが同じreview済みinodeであることを検証するため、同一contentの別inodeへownershipを再bindしない。HEAD/index cleanupのunrelated entryもGit directory直下へ直接renameせず0700 displaced retentionへ移し、保持先descriptorから再読込する。

Artifact installerの今回inodeは生成時からGit-private reservationにdurable nameを持ち、失敗cleanup後もdevice/inode、SHA-256、size、modeを再検証したretained tombstoneとして保持する。自動unlinkは行わない。失敗cleanupの`worktree`とcommitter rollbackの`rollback-worktree`は、no-replace move後にreservation directory descriptorから再openし、regular type、device/inode、size、mode、sealed contentを照合する。cleanupがopen descriptorをhashするときはoffset 0へ戻して全bytesを読み、読後もoffset 0へ復元する。移動前のowned fdだけを保持後contractの代用にしない。receiptはVault rootから開いた全parent component、quarantine rootの全component、run directoryのidentityを各phaseで再検証する。

## Local Configuration

personal absolute paths、Vault names、machine layout、publisher account identityはtracked fileへ書かない。`automation.local.env`にはSaihai primary checkout、relative destination、承認taskのSHA-256 pin、GitHubへ紐付くpublisher Git name/emailを置き、runnerは`directory_paths.load_environment(checkout_root=..., environ={}, require_catalog=True)`でcanonical rootsを解決する。resolverがprivate identityを検証してruntime contextへbindし、commit helperはmutation直前に再検証する。承認taskが変わった場合は自動追従せず、内容を人間が再確認してpinを更新する。

Gitleaksは8.19.0以上のv8をruntime preflightで必須とする。

File Provider配下のVaultでnetwork Git transportを起動するときは、Vault repoのlocal config、hooks、attributes、fsmonitor、filter、ssh commandを実行経路へ入れない。一時bare Git control planeを作り、Vaultのobject directoryをtransportのprimary object store（`GIT_OBJECT_DIRECTORY`）として固定し、fixed remote URL、object ID、`refs/heads/main`だけでfetch、`ls-remote`、fixed pushを行う。fetchはVaultのobject storeへimmutable objectを追加し得るが、Vaultのworktree、index、local branch refは変更しない。tracking ref更新は取得OIDとold OIDを照合した別のCASで行う。transportとlocal helper subprocessには用途別wall-clock deadlineを設定し、deadline超過時はprocess groupをbounded cleanupする。Gitleaks v8はdigest固定した`[extend] useDefault = true` config bytesを匿名fdへ複製し、`pass_fds`でscanner childへ渡して`/dev/fd/N`から全scanへ明示する。検証後のpath差替えやVault内`.gitleaks.toml`をconfig sourceにしない。

push直前のremote OID照合とfixed pushはVaultごとに独立して行う。remote OIDがreview済みexpected OIDと一致すること、expected remoteがtargetのancestorであることをlocal object graphで確認し、literal main refへfixed object IDの通常fast-forward non-force pushを行う。`--force`、`--force-with-lease`、`+` refspec、non-fast-forward target、wildcard、mirror、別refは禁止する。照合後のraceはserverのfast-forward拒否とpush後のremote OID再検証で検出してbounded再評価する。片方でremote race、network failure、または`blocked`を検出しても、もう片方の安全なpushは抑止しない。片側だけ公開できた場合は`partial_publication`と非0を返し、履歴書換えは行わない。

post-review競合のbounded replanでは、same-run commit進捗を持つcanonical `partial_publication`だけを次attemptのcarried resultへ昇格する。進捗のない`blocked + replan`はnull carried contextから再試行し、失敗結果そのものをresumable progressとしてreview/commit helperへ渡さない。

standing task evidenceは、既存のHEAD/index/worktreeを別々のsealed inputとして扱う。review前にVaultを変更せず、承認後はHEAD candidateだけをcommitする。worktree targetの親はVault root descriptorから各componentを`O_NOFOLLOW|O_DIRECTORY`で開き、途中のsymlinkを追わない。exactな元inodeはcanonical nameを外す前にGit-privateかつ同一filesystemのretained hardlinkを持たせ、rename後はdestination→source順にdirectoryをfsyncする。元inodeのdetachment後検証は移動前fdやretained hardlinkだけに依存せず、保持先directory descriptorから`detached`を新しくopenしてregular type、content、device/inode、size、modeを再読込する。temporary candidateも作成fd、move直前のnamed fd、move後のcanonical fdで同じsealed contractを要求し、成功直前のpathname replacementを受け入れない。candidateのpost-rename device/inode・SHA-256・size・mode、HEAD CAS、共有index、committed state、Git control-plane検証後も元inodeを自動削除しない。失敗時はreceipt-bound candidateだけをcandidate quarantineの`worktree`へ退避し、その保持先directoryから新しくopenしてregular type、device/inode、size、mode、sealed contentを完全照合してからretained originalをhardlink no-replaceでcanonical pathへ戻す。別hardlink fdだけで`worktree`保持後検証を代用しないため、restore直後に別inodeへ置換・削除されてもoriginal tombstoneは残る。同一inodeのcontent・size・mode drift、別inode、第三者による再占有では上書き・削除せずfail closedにし、必要なentryをquarantineへ保持する。timestamp-only driftだけを許容する。元のstaged/unstaged差分をそれぞれindex/worktree candidateからexactに復元する。review拒否、digest不一致、CAS失敗ではstanding taskのcontent、mode、mtime、Git statusを変更しない。

evidence finalizerはshared-index candidateのno-replace retentionと保持先contract検証を成功させた後だけcanonical `success` JSONを書き出す。runnerはresult fileの存在でfinalizer statusを0にせず、実process statusと同じstable result snapshotをterminal interpreterへ渡す。非0 statusと`success`の組合せは必ず非0 `process_error`とし、cleanup failure後のstale successを採用しない。terminal statusと各normalization failure statusはcollection/reviewそれぞれのraw result、canonical result、normalization receiptの3 pathを個別に記録し、監査者がrun rootから変換前後とdigest receiptを一意に追跡できるようにする。

途中停止時のoptional `evidence_recovery`はrepo-relative target、quarantine root/run directory identity、original/candidate tombstone完全identity、base/candidate HEAD、HEAD/index/restore progressだけを含み、absolute pathやsecretを含めない。retained tombstoneは今回runで自動削除しない。

standing evidence candidateはGit-private reservationの`artifact`名を保持したままcanonical targetへhardlink no-replaceで公開する。canonical pathのunlink・置換後もcandidate exact inodeをdurable nameで回収でき、original hardlink作成直後からreceiptへoriginal identityを、candidate作成直後からactual partial identityを記録する。通常publicationとevidence finalizationはreview済みshared index bytesからisolated final indexを作り、review済みexpected/candidate indexの両durable private name、別private exchange name、Git-private recovery journalをfsyncする。`index.lock`はcandidate inodeへ結合したcooperative Git lockとして保持し、private exchange nameとshared indexをatomic exchangeする。退避側がexpected SHA-256/device/inode contractと一致した場合だけHEADをold OID付きCASし、既存staged entryを保持する。exchange後のshared indexまたは`index.lock`置換ではbase HEADへexpected indexを復元し、第三者inodeを削除せずprivate displaced nameへ保持する。runnerはruntime context解決直後かつfetch・collection state capture前に未完了journalを検査し、actual HEAD/index pairからdeterministic rollbackまたはroll-forwardする。standing-taskのpre-review更新helperもexpected original reservationとno-replace detachmentを使い、検証後の無条件replaceで第三者更新を上書きしない。tombstone directoryはslash、backslash、C0 control、DELを許可しない。

## Deployment / Acceptance Gates

1. PR前candidateのsource boundaryをfreezeし、必須test、skill benchmark、独立QA・DevOpsSec・統合reviewを通過する。
2. runtime filesとplistをbackupする。
3. frozen candidateのtracked files（`determine-publication-modes.py`を含む）をcanonical runtimeへmode-preservingでcopyし、task worktreeをruntime pathとして参照しない。checksumとfile modeが一致し、全runtime fileをsource commitへ結合したmanifestを生成する。direct executionされる全Python helperと`collect-public-sources.py`がsource/runtimeの双方でexecutableであることも確認する。
4. ignored local configを人間が確認する。
5. `zsh -n`、Python tests、JSON parse、`plutil -lint`を通過する。
6. unsafeな既存handoffをUser Vaultへ残し、content/mode/mtime/index statusを記録する。
7. 登録済みlaunchd jobをone-shot起動し、実Web E2Eの`complete/true/null`、User=`own_only`、handoff不変、両Vaultのnon-force fixed push、evidence finalization、exit 0を確認する。
8. PR前E2E成功後に限りsourceをcommit/pushし、PR reviewを通してmainへmergeする。
9. merge後に最新mainからcanonical runtimeへ再配備し、source/destination SHA-256とmodeの一致を再確認してmanifestを再生成する。
10. merge後もlaunchd one-shotで同じE2E条件を確認する。
11. PR作成・更新後、Codex Workの監視registrationをrepository/PR/base/head/triggerと「マージまで継続」のidentity付きで認証済み確認し、確認不能なら`pr_monitor_registration=unverified`をtask evidenceへ記録して監視済みと扱わない。
