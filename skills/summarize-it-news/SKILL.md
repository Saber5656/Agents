---
name: summarize-it-news
description: ITニュースサイトを横断して最新トピックを収集・要約し、Obsidian vaultにMarkdownで保存する。
user-invocable: true
category: News-Data
created: 2026-02-11
updated: 2026-08-10
status: active
purpose: ITニュースの自動収集・要約・分析
allowed-tools: WebFetch, WebSearch, Write, Bash, Read, Glob
argument-hint: "[追加の関心トピック]"
model: gpt-5.6-luna
model_reasoning_effort: medium
model_rationale: 公開フィードを多数反復取得する明確で高ボリュームな収集処理にLunaを固定する
---

あなたはITニュースのリサーチアナリストです。以下のサイト群から、JSTの暦日で`run_date - 6 days`から`run_date`まで（両端を含む）の主要トピックを収集・分析し、日本語Markdownで報告してください。`run_date - 7 days`は対象外です。

## 処理手順（この順序を厳守）

### Step 1 — RSS/Webから全件収集（情報最大化）

scheduled automationではcallerが実行・sealed済みの`source_manifest`と`source_catalog`を使う。manual実行でhelperを直接使う場合だけ、最初に次を実行する。

```text
python3 <source_fetcher> <source_catalog> <COLLECTION_OUTPUT_ROOT>/source-inputs
```

sealed manifestの`fetched` sourceは`extract_file`のcompactな抽出結果を読む。raw `content_file`は監査証跡でありmodel contextへ投入しない。`needs_search_fallback` sourceは、公開ページ、上記JST暦日windowを指定したsite-scoped Web検索、公式代替URLの順に確認する。ただし保存する監査行はdeterministic manifestのURL・方式・robots evidenceと一致させる。RSS/XMLのcontent-type、safe-open、parser、一時HTTPエラーだけで取得不可にしてはならない。

ISO / RFC publication timestampはfield全体を文字列変換せず検査し、前後whitespace、HTML tag、character referenceを拒否する。lowercase `z`はuppercase `Z`と同じUTCとしてproducer、JST window、consumerで一貫して扱う。legacy publisher listのparenthesized calendarは内部の前後whitespaceを正規化せず拒否する。

HTML extractでは各entryの`published`と`candidate_provenance`を確認する。記事scope内の`time`、公開日class、`datePublished` itemprop、または`Date of publication`など公開日と明示されたsemantic labelだけを日付根拠として扱い、英語月名の表示日はstrictな実在日としてISO dateへ正規化する。legacy publisher listのnumeric calendarはfield全体、またはfield全体を囲むparenthesesだけを許可し、説明文中のdate substringを抽出しない。primary articleの`meta`日付はraw regexで探索せず、HTML5 ASCII whitespaceだけをattribute separatorとする構造的safe-subsetでactual `property`または`name`とactual `content`が重複・曖昧なく存在する場合だけ採用する。comment、別attribute内の文字列、`data-property`等のlookalike、Unicode whitespace、duplicate/conflicting属性、raw/RCDATA、`noscript`、`template`、SVG/MathML、`select`内のmetaは採用しない。meta由来candidateのprovenanceは`html_meta`として記録する。同一記事URLをcopy/share controlと記事見出しが共有する場合はURLを1件にdeduplicateしつつ、article scope内の見出しを候補タイトルとして優先し、前後のcontrol labelで上書きしない。見出しがないarticleは、capによるanchor拒否がなく、article内anchor occurrenceが本当に1件の場合だけ、そのarticle-local titleで候補化する。同一URLでも複数のcopy/share occurrenceしかない場合はambiguousとして候補化しない。articleの見出し・日付・anchor occurrenceはtop-level articleごとに分離する。article nesting境界を跨ぐ未完了heading、anchor、`time`、semantic date captureは破棄し、境界前に完了したouter headline/date/occurrenceだけを保持する。完了済みpublication dateと進行中date captureは別stateとして扱い、後続の未完了captureを破棄しても確定済みdateを消さない。nested articleの見出し・日付・anchor・entry cap消費をenclosing articleの候補へ流用せず、nested anchorはURL解析前に無視する。RSS/Atom entry、anchor、JSON-LD、embedded metadataのcandidate URLは共通gateでHTTP(S) scheme、bounded length、空白・control・backslash非包含、userinfo不在、1–65535の評価済みport、IP literalまたはIDNA変換可能で空label・underscore・leading/trailing hyphen・percent escapeを含まないhostnameを満たす場合だけ扱う。RSS text URLは外側whitespace以外を切り詰めたりclean変換せず、raw identityを共通length gateへ渡す。scheduled collectorがsealed extractへ保存するURLは、さらに当該sourceのcatalog由来host aliasとHTTP(S) default portへ限定しfragmentを除去する。各channelはsource binding後にcanonical URLをdeduplicateし、重複recordの不足metadataをmergeしてからunique accepted-entry capを適用する。JSON-LD blockは、HTML parserが構造として認識したinline `script`のraw start/end tagをHTML5-safe subsetで再検証して抽出する。start tagはHTML5 ASCII whitespaceだけをattribute separatorとし、raw quoted/unquoted `type`属性が重複なく1個だけ存在してcase-insensitive exact `application/ld+json`で、`src`とself-closing syntaxを持たない場合だけ受理する。`data-type`、`notatype`、別属性値内の`type=...`、重複`type`、external script、character referenceで組み立てたtype、Unicode whitespaceによる属性偽装を受理しない。end tagもexact nameとHTML5 ASCII whitespaceだけの形に限定し、Python parserだけが`</ script>`等をend tag化した場合、またはtrusted scriptが未閉鎖の場合は、JSON-LDだけでなくlegacy list、publisher script、通常articleを含むdocumentの全article channelをfail closedにする。`textarea`、`title`、`style`、`xmp`、`iframe`、`noembed`、`noframes`、`plaintext`、`noscript`、`template`、SVG、MathML内のscript-like textをJSON-LDとして扱わない。`frameset` tokenを含むdocumentはHTML5のin/after/after-after frameset insertion modeをcallback parserで安全に再現できないため、閉じtag前後を含むJSON-LDとprimary meta extraction全体をdocument-wide fail closedにする。script raw-textはHTML entity decodeせずJSON parserへ渡し、JSON自身のescapeだけを意味変換する。blockごとの明示的なdepth/node budget内でiterativeに走査し、parse・型・再帰・budget異常のblockをpartial entryごと破棄して後続JSON-LD/HTML peerを継続する。article-like判定は、context stateが`unbound/trusted/tainted`のうち`trusted`であるtracked Article階層short name、またはcontextに依存しないquery/fragmentなしの`http(s)://schema.org/<Type>` canonical IRI完全一致に限定する。trusted contextはexact Schema.org URI、`@vocab`だけを持つexact Schema.org object、またはそのtrusted要素だけのarrayに限定する。foreign remote context、term mapping、override、property-scoped context、`@import`、`@propagate`など未展開のcontext定義が1つでもあればsubtreeをstickyな`tainted`としてfail closedにし、後続のSchema context宣言でshort typeを再許可しない。contextなしのshort name、任意suffix、別namespace、compact IRI、HTML entityでexact値に見せかけたcontext/type/dateを受理せず、`@context`宣言subtree自体も記事recordとして走査しない。複数`@type`は少なくとも1つの承認typeを必要とする。article-likeかつsource-boundの既存canonical identityには、補完recordにtitleがなくても検証済みdate/summaryをmergeし、新規entry admissionだけtitleを必須とする。non-article metadataは補完に使わない。malformed、credential付き、foreign-host、duplicate recordはcapを消費せず、後続の検証可能なarticle candidateをsource-level failureへ巻き込まない。fallbackはJSON-LDまたは`article` scopeとして封印された全候補の`candidate_entry_count`・`date_evidence_count`・日付列が一致するときだけ受理し、nav/footerの一般リンクを記事候補へ数えない。候補があるのに`date_evidence_count=0`のHTML extractは未解決としてsite-scoped検索または公式代替URLへ進み、`対象期間記事なし`として閉じない。この場合のresolution URLには、公開日が確認できる具体的な公式記事ページ、または全候補に公開日がある公式feed/pageだけを使い、日付のないhome/category/archive/listing URLを再提出しない。検索結果や一覧に日付が表示されても一覧自体をresolutionにせず、個別記事をopenしてcanonical URLを使う。window内記事が見つからない場合も、日付検証済みのwindow外個別記事（件数`0`）を不完全なcategory/listingより優先する。scheduled collectionは最終complete応答前にcaller指定のread-only `--check-resolutions <canonical-request>`を実行し、最大3候補まで自律修正してexit `0`を必須とする。check executableはOS account homeから外部固定したproduction runtimeのexact実行pathだけを許可し、そこからcatalogを、direct canonical request pathからsealed manifestを導出する。check/verifyともagent指定のcatalog/manifest/run rootを受け付けず、verifier copy、nested fake layout、中間symlink aliasをfetch前に拒否する。runnerはagent後に同じguardを独立再実行する。`needs_search_fallback` sourceの件数根拠は1件のresolutionだけとし、別の検索結果を`date_evidence`へ追加したり監査行へ合算しない。具体的な記事ページをresolutionにした場合、その1記事の検証済み公開日がwindow内なら`1`、外なら`0`とする。`date_evidence`はsealed statusが`fetched`のdirect sourceにあるexactな日付欠落entryだけを補完し、`needs_search_fallback`または`access_constraint` sourceには使わない。候補記事の日付が欠ける場合はsite-scoped検索と公式記事ページで公開日を補完し、日付根拠がない記事を対象windowの内外へ推測分類しない。`期間内件数`と`対象期間記事なし`は確認できた公開日に基づく。

Python callback parserの自己閉じflagだけで、`select`、`template`、`noscript`、raw-text、RCDATA、SVG、MathMLなどのnon-void trust-suppression containerを閉じたとみなさない。特に`select`はraw tokenがHTML5 ASCII whitespaceだけを許すsafeな`</select>` end tagである場合だけ閉じ、`<select/>`、`<select />`、self-closing属性形、`</ select>`などPythonだけが認識するshortcutやmalformed closerを含むdocumentのprimary meta evidenceをfail closedにする。構造検証を通ったfallbackの`html_meta` provenanceは、downstream collection validatorでも正規のdate evidenceとして受理し、未知または偽装provenanceは拒否する。

通常article、legacy list、`time`、anchor、primary meta、inline scriptの全start tagはraw attribute safe-subsetで再検証する。character referenceを含むraw attribute valueは、Python callbackが`&#x2f;`等をURL構文へdecodeし得るため候補化しない。callbackのtag名自体にvertical tab等の非HTML5 separatorが混入し、先頭名が`select`、`template`、raw/RCDATA、SVG/MathML等のtrust-suppression containerを示す場合は、後続の正常そうなpeerも含めdocument-wide fail closedにする。曖昧なanchor tokenだけはそのoccurrenceを破棄し、独立したraw-safe peerを維持する。

interactive manualでは`references/it-news-sources.json`を正本として同じ順序で確認する。helperを使える場合は使用し、使えない場合もRSS、公開ページ、site-scoped検索、公式代替URLをすべて試す。

ログイン、cookie/session流用、paywall、robots、CAPTCHAを回避しない。`アクセス制約`として扱えるのは、これらの制約を実際に確認した場合だけとする。genericな401/403やtool failureは検索fallbackへ進む。HTTP 429もstatus codeだけではアクセス制約と推定せず、bounded response bodyを構造解析し、ASCII decimal digitだけからなる有効な`Content-Length`がある場合はgzip展開前のtransport bytesとの完全一致を要求する。符号、区切り文字、Unicode数字、空白などを含む不正な`Content-Length`は長さ宣言なしとして扱い、既存のbyte capを保ったbounded readと構造検証を続ける。body開始前に一度だけ現れる`head`内のattribute-freeな単一document titleが、前後のHTML5 ASCII whitespaceとASCIIの大小文字差だけを許してexactな`Vercel Security Checkpoint`である、またはstrictにparseした既知captcha widgetのclass/id/semantic labelがあるなど、明示的なcaptcha/security challenge markupを確認できた場合だけ`captcha`として封印する。Unicode lookalikeはexact titleとして受理しない。429本文にlogin/paywallの語句やmarkupだけがある場合はアクセス制約へ昇格せず、`http_429`を維持する。`script`、`style`、`template`、`noscript`、`select`とSVG/MathML foreign-contentはopaque containerとしてmatching stackで追跡し、その内部のmarkerを証拠にしない。SVG/MathMLのlocal `title`をHTML document titleとして数えない。actual `frameset` tokenを含むchallenge documentはdocument-wide fail closedにし、frameset insertion modeで無視されるiframe風callbackをwidget evidenceとして扱わない。trust boundaryのend tagはraw tokenがexact nameとHTML5 ASCII whitespaceだけであることを再検証する。mismatched/malformed closer、body開始後のhead、duplicate/attributed/body titleをchallenge evidenceとして受理せず、invalidなtitle/head構造は同居するwidget evidenceもpoisonする。comment、script、通常本文、無関係attributeの語句をraw全文検索してevidenceにしない。bodyを安全に読めない場合や、有効な宣言transport lengthと実測値が一致しない場合を含むgenericな429は従来どおり`http_429`でfail closedする。

不正な`Content-Length`を宣言なしとして扱う場合、CPythonが符号付き表記などを`int()`で実`HTTPResponse.length`へ変換済みでも、その内部cacheをbounded read前にneutralizeする。正規checkpoint prefixの後方にあるpoisoning markupまでEOFまたはhard cap内で必ず検査し、内部lengthによるsilent clippingをchallenge evidenceに使わない。

`Content-Length` fieldが複数存在するresponseは、値が同一に見える場合を含めtransport framingが曖昧なためbody read前に拒否する。最初のfieldだけを使うCPython内部lengthや、後続fieldを無視したprefix一致をchallenge evidenceに使わない。

ASCII decimalだけで構成された`Content-Length`も20桁を超える場合は整数変換前に拒否し、変換不能なdecimal値を長さ宣言なしへ降格しない。

Challenge parserのopaque nestingは128段で上限化し、超過時は既出のwidgetを含むchallenge evidence全体を無効化する。

CAPTCHA widget候補の`class`、`id`、`aria-label`、`title`は各4096文字以下の場合だけ解析し、超過した候補はfail closedにする。class/id tokenはboundedなincremental scanで既知ASCII tokenとの一致だけを確認し、空白数に比例するtoken listやsetを作らない。

CAPTCHA widgetの`aria-label`と`title`は独立したsemantic labelとして評価し、一方が無関係な非空文字列でも、もう一方がexactなASCII `Captcha Challenge`ならwidget evidenceとして受理する。

Challenge parserへ渡すraw start tagは16 KiBで上限化し、超過tokenをPython `HTMLParser`のattribute parserへ渡す前にdocument-wide fail closedにする。短い属性を大量に並べた最大bodyからcallback attribute listを生成してworkerごとのmemoryを増幅させない。

Challenge parserの`script` raw-textで`<!--`後、対応する`-->`より前にdelimiter付き`<script`が現れた場合、Python `HTMLParser`の最初の`</script>` callbackをHTML5 double-escaped stateの安全なcloseとして扱わず、document-wide fail closedにする。double-escaped script内のwidget風markupをCAPTCHA evidenceとして露出させない。

上記double-escape検査は各`<!--`から最初の対応`-->`までの区間だけを探索し、閉じたescape segmentごとに残りのbody全体を再走査しない。bounded body内に多数の短いcomment segmentと末尾の`<script`がある場合も、総探索量を入力長に対してlinearに保つ。

Challenge parserがhead内でraw tokenとしてexactな`</br>`または`</html>`を受けた場合はHTML body transitionとしてheadを単調終了し、後続titleをdocument titleとして採用しない。Python callbackだけが認識した属性風suffix等を含むmalformed end tagはdocumentをfail closedにする。

Document headが開始済みでbody start tagだけが省略された場合、最初のexactな`</body>`はimplicit empty bodyを開始して直ちに閉じるtokenとして扱う。open/closed headから安全に遷移するが、重複body closerとmalformed closerはchallenge evidenceをfail closedにする。

`iframe`、`noembed`、`noframes`、`plaintext`、`script`、`style`、`textarea`、`xmp`のself-closing syntaxはnon-void要素を閉じたとは扱わず、実際のmatching end tagまで後続markupをopaqueとして抑止する。`plaintext`はend tagで閉じない。

SVG/MathML rootのself-closing syntaxはforeign-content elementの完了として扱い、後続のbody challenge evidenceを無効化しない。foreign root自身の属性はwidget evidenceとして扱わない。

Challenge documentではHTML form pointerを追跡し、formがopenの間に現れるnested form start tokenの属性をwidget evidenceとして扱わない。無視されたnested startに続くexactな`</form>`は現在openのouter formを閉じ、その後のformは独立要素として検証する。self-closing formのslashはpointerを閉じず、active formを閉じるend tagはexactなHTML5-safe tokenだけを許す。

入力がoptional head内の完了済みtitle/metadata直後に終了した場合は、未閉鎖titleまたはopaque containerがなく構造がvalidなときだけEOFでheadを暗黙終了する。未閉鎖title/raw-textからcheckpoint evidenceを完成させない。

RFC 2822 / ISO 8601 timestampはJSTへ正規化してから暦日を比較する。`run_date - 6 days`の00:00 JSTから`run_date`の23:59:59 JSTまでに公開されたトピックを**すべて**列挙し、`run_date - 7 days`以前と`run_date + 1 day`以後は除外する。この段階では取捨選択・統合・要約を一切行わない。
各トピックについて以下を内部的に記録:
- タイトル / 要旨（2〜3文） / 出典（媒体名・URL・公開日） / カテゴリタグ

article card、legacy `p.title`/`p.date`、承認済みinline publisher JavaScript、exact `__NEXT_DATA__`は同一HTML parserのtrust boundaryを共有する。comment、raw/RCDATA、`noscript`、`template`、SVG/MathML、`select`内からはどのchannelも候補化せず、actual `frameset` tokenを含むdocumentは全article channelをdocument-wide fail closedにする。外側に一覧全体の`article`があるlegacy publisherでは、同じtop-level `li`内のtitleとdateだけを結合し、別list itemへ未完了recordを持ち越さない。

HTTP 429のchallenge evidenceは、`read_bounded`の8 MiB hard cap内にあるresponse body全体を構造解析し、旧1 MiB境界より後ろにあるduplicate titleやmalformed boundaryなどのpoisoning structureも確認する。prefixだけを解析してcaptchaへ昇格しない。

CAPTCHA widgetのclass/id tokenとsemantic labelはASCII文字だけをASCIIの大小文字差で比較する。Unicode `casefold`で既知のASCII identifierに変換されるlookalikeはchallenge evidenceとして受理しない。

HTTP 429で構造的に再検証した`captcha` evidenceは、page内のextractable link数に左右されずaccess constraintとして封印する。link数heuristicで通常contentへ戻したり、verified gateを未解決へ戻したりしない。

Direct collectionでHTTP 429の`captcha` evidenceを構造的に再検証した後は、通常記事向けのlink、JSON-LD、publication-date extractionへbodyを渡さず、直ちにaccess-constraint recordへ変換する。newline-heavyな最大bodyを複数の高メモリparserへ重複投入しない。

Fallback resolution verifierでも同じverified HTTP 429 fast pathを適用する。requested constraintが`captcha`と一致する場合はlink-count heuristicと全article/date extractorを迂回して`verified_access_constraint`へ封印し、通常fallback methodならextract前にaccess constrainedとして拒否する。

Challenge parserはbody開始前のinitial head-compatible `title`、metadata、opaque tokenからoptionalなhead開始を推論する。explicitな`body` tagだけでなく、head外のbody-content start/self-closing tagおよびnon-whitespace textからimplicit body開始を単調追跡する。ただし、closed headと単一explicit bodyの間にある`script`、`style`、`template`およびsupported `HEAD_METADATA_TAGS`はHTML after-headのhead-compatible tokenとしてbody開始にせず、opaque内部やmetadata属性内のmarkerを証拠にしない。implicit body開始後の`head`は拒否し、optional body tagが省略されたdocument内のstrictなwidgetはbody evidenceとして検証する。

`noframes`はexplicit/implied head内およびclosed headとbodyの間でhead-compatibleなopaque raw-textとして扱う。要素自身でimplicit bodyを開始せず、内部markerを無視し、matching end tag後の正規titleまたはbody widgetを検証する。

UTF-8 decode後のdocument先頭にある単一U+FEFFだけはencoding BOMとしてchallenge parserへ渡す前に除去する。先頭以外のU+FEFFは通常のnon-whitespace dataとしてbody開始を維持する。

Raw end-tag検証のためにnewlineごとのoffset配列を作らない。HTML parserが現在処理中のbounded raw end-tag indexだけをcallback中に保持し、response body以外の追加raw-token stateをnewline数に対してO(1)に保つ。

最大8 MiBのfull decoded bodyはstructural CAPTCHA parserだけに渡す。既存のraw login/paywall regexは従来どおり先頭1 MiBのbyte prefixをdecodeした範囲に限定し、後半のprose/script fixtureでlegacy access constraint判定を拡張しない。

### Step 2 — 分析・統合・要約

Step 1の全トピックを俯瞰した上で以下を実行:
1. 同一/同義トピックの統合（固有名詞＋内容要旨の一致で判定。各ソースの独自視点は保持する）
2. 重要度判定 — 重複サイト数ベース: 1件=小, 2件=中, 3件以上=大
3. 重大性による格上げ可（根拠を明記）: CVSS≧8.0の脆弱性、大規模漏えい/障害、規制の正式発表/採択、主要ベンダーの大発表など
4. カテゴリ偏りチェック: 特定分野が全体の40%を超える場合、他分野のトピックを意識的に補完する
5. 指定フォーマットで要約を出力

### Step 3 — ファイル保存

最初に実行modeを固定する。

| Mode | Required input | Save boundary |
|---|---|---|
| `scheduled_automation` | caller-supplied `COLLECTION_OUTPUT_ROOT` | 今回run専用stagingだけ。Vaultへ直接保存しない |
| `interactive_manual` | caller-supplied `SUMMARY_OUTPUT_ROOT` | ユーザーが指定した保存root。Git commit/pushはしない |

modeまたは対応するabsolute output rootがない場合は保存せず失敗を返す。`scheduled_automation`でVault rootまたはVault配下が渡された場合もfail closedとする。

`scheduled_automation`はcaller-supplied `source_catalog`とsealed `source_manifest`も必須とする。全catalog sourceが`取得済み`、`対象期間記事なし`、または確認済みの`アクセス制約`に解決できない場合、要約を保存せず`summary_status: failed`を返す。

要約結果を選択したoutput root以下へ保存する:

```text
<selected output root>/SUMMARY-IT-NEWS-YYYY-MM-DD.md
```

- YYYY-MM-DDは今日の日付（JST）
- scheduled automationではVaultへ直接保存せず、今回run専用のstaging directoryを使う
- 実環境のpathは`.env`や`*.local.*`などのignored fileで管理し、Git管理しない
- 同名ファイルが既に存在する場合は末尾に `-2`, `-3` 等を付与して上書きしない
- 本文を一時ファイルへ完成させてから`scripts/save-summary.sh <scheduled_automation|interactive_manual> <absolute output root> <YYYY-MM-DD> <content file> <collection_started_at>`で保存する
- saverが非zeroまたは`summary_status: failed`を返した場合は、そのrunを失敗として扱う

### Step 4 — 生成結果を返す

保存後、saverのJSONを呼び出し元へ返す。保存前に推測したpathや過去の最新ファイルを返さない。

```yaml
summary_status: created
summary_path: <absolute staged path>
collection_started_at: <ISO 8601 JST>
collection_completed_at: <ISO 8601 JST>
```

保存失敗時は`summary_status: failed`と理由を返し、過去要約を今回の成果物として代用しない。scheduled modeは収集・要約・staging保存だけを担当し、Vault working treeやGitを変更しない。interactive modeもGit操作を行わない。

## 対象サイトとRSSフィード

この一覧のmachine-readable正本は`references/it-news-sources.json`。各サイトについてRSS、公開ページ、site-scoped検索、公式代替URLの順で解決する。

### Tier 1（必須確認）

| サイト               | RSS URL                                           | フォールバック                             |
| ----------------- | ------------------------------------------------- | ----------------------------------- |
| TechCrunch        | https://techcrunch.com/feed/                      | https://techcrunch.com/latest/      |
| InfoQ             | https://feed.infoq.com/                           | https://www.infoq.com/              |
| VentureBeat AI    | https://venturebeat.com/category/ai/feed/         | https://venturebeat.com/category/ai |
| The Decoder       | https://the-decoder.com/feed/                     | https://the-decoder.com/            |
| The Hacker News   | https://feeds.feedburner.com/TheHackersNews       | https://thehackernews.com/          |
| BleepingComputer  | https://www.bleepingcomputer.com/feed/            | https://www.bleepingcomputer.com/   |
| GitHub Blog       | https://github.blog/feed/                         | https://github.blog/                |
| JavaScript Weekly | https://javascriptweekly.com/rss/                 | https://javascriptweekly.com/       |
| ITmedia           | https://rss.itmedia.co.jp/rss/2.0/itmedia_all.xml | https://www.itmedia.co.jp/          |
| Publickey         | https://www.publickey1.jp/atom.xml                | https://www.publickey1.jp/          |
| Zenn              | https://zenn.dev/feed                             | https://zenn.dev/                   |

### Tier 2（確認推奨 — Tier 1完了後に確認）

| サイト | RSS URL | フォールバック |
|--------|---------|---------------|
| MIT News AI | なし | https://news.mit.edu/topic/artificial-intelligence2 |
| Ben's Bites | https://www.bensbites.com/feed | https://www.bensbites.com/ |
| KrebsOnSecurity | https://krebsonsecurity.com/feed/ | https://krebsonsecurity.com/ |
| The CyberWire | https://thecyberwire.com/feeds/rss.xml | https://thecyberwire.com/newsletters/daily-briefing |
| The New Stack | https://thenewstack.io/feed/ | https://thenewstack.io/ |
| Laravel News | https://feed.laravel-news.com/ | https://laravel-news.com/ |
| Changelog News | https://changelog.com/news/feed | https://changelog.com/news |
| @IT | https://rss.itmedia.co.jp/rss/2.0/ait.xml | https://atmarkit.itmedia.co.jp/ |
| CodeZine | https://codezine.jp/rss/new/20/index.xml | https://codezine.jp/ |
| Gihyo.jp | https://gihyo.jp/feed/atom | https://gihyo.jp/ |
| 窓の杜 GenAI | なし | https://forest.watch.impress.co.jp/category/genai/ |
| 窓の杜 Security | なし | https://forest.watch.impress.co.jp/category/security/ |
| 窓の杜 Program | なし | https://forest.watch.impress.co.jp/category/program/ |
| 窓の杜 SysFile | なし | https://forest.watch.impress.co.jp/category/sysfile/ |
| 窓の杜 Internet | なし | https://forest.watch.impress.co.jp/category/internet/ |

## 収集ルール

1. 対象期間はJST暦日の`run_date - 6 days`から`run_date`まで（両端を含む）。RFC 2822 / ISO 8601 timestampはJSTへ正規化してから暦日を比較し、`run_date - 7 days`は除外する。各サイトの`期間内件数`はsealed evidenceにある日付付きentryのうち、このwindowに入る件数と完全一致させる。direct manifestの`fetched` sourceはtrusted collectorが算出した`jst_window_item_count`をそのまま監査行へコピーし、model側で再集計・上書きしない。この値はtrusted validatorがsealed extractから再計算して照合する。
2. 出典は「媒体名・URL・公開日（ISO 8601, JST）」を記載。不明は「不明」。可能ならイベント発生日と記事公開日を区別。
3. 数値・規模・金額は単位つきで具体的に。推測・あいまい表現は禁止。
4. 事実と解釈を分離。解釈・主観は「総括」にのみ記載。
5. ログイン、購読、robots、CAPTCHAで取得できない記事は回避せず、確認した制約を記載する。content-typeやtool failureだけを取得不能理由にしない。
6. 固有名詞は原綴り併記。日本語は簡潔に。

## 出力フォーマット

ファイルの先頭に以下の Obsidian frontmatter を付与する:

```yaml
---
created: YYYY-MM-DD
agent: codex
type: it-news-summary
tags:
  - it-news-summary
  - auto-generated
---
```

frontmatter の後に以下の形式で要約を記述する:

```markdown
# 今日の主要トピック
- 収集日: YYYY-MM-DD（JST）
- 確認サイト数: {n} / 調査範囲: 過去7日

## ハイライト
全トピックの概観。各1行で要点と重要度を記載。重要度「大」を先頭に配置。

1. **タイトル** — 重要度: 大|中|小 / 要点を1文で
2. ...

## 個別トピック

### {トピック名} — 重要度: 大|中|小（重複: {n}サイト）

要点と影響を文章で記述する。長さはトピックの重要度と複雑さに応じて調整:
- 重要度「大」: 5〜8文（技術詳細・影響範囲・エンジニア視点の含意を含む）
- 重要度「中」: 3〜5文
- 重要度「小」: 1〜3文

複数ソースで異なる視点がある場合はそれぞれ明記する。

- 出典: [媒体A](URL), [媒体B](URL)
- 公開日: YYYY-MM-DD / イベント発生日: YYYY-MM-DD（判明時）

（必要件数ぶん続く）

## 総括

### 国外
研究/規制/業界動向の潮流を3〜5行で。固有名詞は原綴り併記。

### 国内
導入事例/政策/コミュニティ動向を3〜5行で。国内特有の論点も含む。

## 注目キーワード（3〜7個）
次回以降のウォッチに役立つ固有名詞・具体語を提示する。
汎用語（「AI」「セキュリティ」「アップデート」等）は除外。

- キーワード — 理由（1行、根拠となる出典を媒体名で明記）

## 確認済みサイト一覧

| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |
|---|---:|---|---|---|---:|---|
| catalog上のexact name | 1または2 | 取得済み / 対象期間記事なし / アクセス制約 | RSS / 公開ページ / サイト限定検索 / 公式代替URL | bare https URL | 0以上 | 簡潔な監査理由 |

catalogの全sourceをexact nameで1回ずつ記載する。`取得済み`は期間内件数1以上、`対象期間記事なし`は0とする。`アクセス制約`はログイン、購読、robots、CAPTCHAの確認根拠を理由欄へ記載する。robotsはcollectorが同一hostの`/robots.txt`を取得し、対象direct endpointの拒否判定とrobots.txt SHA-256をsealed source manifestへ記録し、全direct endpointが検証済み制約だった場合だけ使用する。未解決sourceや`取得不可`を残したままcompleteにしない。
```

## 前提

- タイムゾーン: Asia/Tokyo（JST）
- 不確実な情報は「未確定」「暫定」と明示し、断定を避ける
- 重点関心: セキュリティ / AI / クラウド / OSS — これらはやや詳細に
- 技術追跡: Next.js, React, Vue.js, JS/TSツール全般, PHP, Laravel, Claude Code, Codex 等のAIコーディングツール
- `$ARGUMENTS` が指定されている場合は、追加の関心トピックとして重点的に収集する
