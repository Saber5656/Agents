"""Connect native agent instruction entrypoints to one local Agents policy.

No provider settings, credentials, permissions or model selections are changed.
Explicit --agents selection lets a new installation be provisioned intentionally.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from .context import _atomic_write
from .runner import load_dotenv

BEGIN = '<!-- agents:begin -->'
END = '<!-- agents:end -->'

class InstructionError(RuntimeError):
    pass

def bootstrap():
    return '''## Agents 共通運用

日本語で応答する。利用者がこのアプリへ直接依頼した場合、あなたが主担当として目的・判断・成果に責任を持つ。Devin Desktop / Hermes / Cursor の本来のモデルと機能を使い、Codexの起動は前提にしない。

作業開始時と再開時に、信頼済みローカル設定 `~/.config/agents/environment.env` を読み、`AGENTS_ROOT`・`SKILLS_ROOT`・`AGENTS_VAULT_ROOT` を解決する。このファイルは正本の `.env` への参照。値や秘密を会話へ表示しない。シェルでは `set -a; . "$HOME/.config/agents/environment.env"; set +a` で、そのコマンドのプロセスに明示的に渡す。

`$AGENTS_ROOT/COMMON-AGENTS.md` と `$SKILLS_ROOT/CURRENT-WORKFLOW.md` の全文を読み、対象のプロジェクト指示と関連policy/skillを適用する。旧Saihaiの受付・固定role・承認gateは復活させない。共通ファイルが読めない場合は不足箇所を報告し、設定を推測して置き換えない。

作業前に既存Vaultの関連記録を探し、依頼原文・追加指示・資料・検討案・判断理由・実行・検証・失敗と復旧・変更・未解決点を `$AGENTS_VAULT_ROOT` に順次保存する。取得できるユーザー/assistant/toolのvisible recordは全文を保存し、索引から詳細へ辿れるようにする。秘密値・非公開のanalysis/thinkingは除外し、履歴やログの未取得・切り詰めを明示する。生ログの自動保存だけで、判断や引き継ぎ記録を済ませたことにしない。完了応答に記録へのリンクと実際の検証状態を付ける。

合意済みの実装・修正・検証・レビュー・コミットを自律的に完了する。独立して切り出せる作業と別実行のレビューは既存の `python3 -m harness` から原則Claude sonnet/lowへ委譲する。利用上限時のみCodex Luna/lowへ引き継ぎ、直接起動したあなたの主担当と製品固有の機能は維持する。使えない委譲経路は理由を記録し、独立して進められる作業を続ける。既存変更と設定を保持し、別料金・未承認公開・破壊的操作・認証本人操作だけは個別に扱う。

この指示はモデルに読ませる運用ルールであり、OSの強制制御ではない。リモート環境では正本ファイルとVaultへの接続を別途確認し、ローカル設定が自動転送されたと扱わない。'''

def merge_block(original, body):
    block=BEGIN+'\n'+body.strip()+'\n'+END
    if BEGIN in original or END in original:
        if original.count(BEGIN)!=1 or original.count(END)!=1 or original.index(BEGIN)>original.index(END):
            raise InstructionError('Invalid or duplicate Agents managed block; preserve file and reconcile')
        start=original.index(BEGIN);end=original.index(END)+len(END)
        return original[:start]+block+original[end:]
    return original+ ('\n\n' if original else '')+block+'\n'

def targets(home, agents):
    paths=[]
    mapping={'codex':[('.codex/AGENTS.md',None)],'claude':[('.claude/CLAUDE.md',None)],
             'devin':[('.config/devin/AGENTS.md',None),('.codeium/windsurf/memories/global_rules.md',6000)],
             'hermes':[('.hermes/SOUL.md',20000)],'cursor':[('.cursor/rules/agents.mdc',None)]}
    for agent in agents:
        if agent not in mapping:raise InstructionError('Unknown agent: '+agent)
        for relative,limit in mapping[agent]:paths.append((agent,home/relative,limit))
    return paths

def configure(env_file, home, agents, *, apply=False):
    env_file=Path(env_file).resolve();home=Path(home).expanduser().resolve()
    if not env_file.is_file():raise InstructionError('Trusted environment file missing')
    env=load_dotenv(env_file,dict(os.environ,HOME=str(home)))
    for key in ('AGENTS_ROOT','SKILLS_ROOT','AGENTS_VAULT_ROOT'):
        if not env.get(key) or not Path(env[key]).is_dir():raise InstructionError('Missing configured directory: '+key)
    root=Path(env['AGENTS_ROOT']);common=root/'COMMON-AGENTS.md'
    if not common.is_file():raise InstructionError('COMMON-AGENTS.md missing')
    link=home/'.config/agents/environment.env'
    if os.path.lexists(link) and not (link.is_symlink() and link.resolve()==env_file):
        raise InstructionError('Existing environment entrypoint differs; preserve and reconcile')
    changes=[];results=[]
    for agent,path,limit in targets(home,agents):
        if path.is_symlink():
            if agent in ('codex','claude') and path.resolve()==common.resolve():
                results.append({'agent':agent,'path':str(path),'state':'shared_policy_link'});continue
            raise InstructionError('Unexpected instruction symlink: '+str(path))
        original=path.read_text() if path.exists() else ''
        after=merge_block(original,bootstrap())
        if agent=='cursor' and not original:
            after='---\ndescription: Agents shared operating policy\nalwaysApply: true\n---\n\n'+after
        if limit and len(after)>limit:raise InstructionError('Native instruction size limit exceeded: '+str(path))
        results.append({'agent':agent,'path':str(path),'state':'current' if after==original else 'change_required'})
        if after!=original:changes.append((path,original if path.exists() else None,after))
    if apply:
        state=root/'.local/agent-instructions';state.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (state/'install.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            # Recheck all preimages before changing any shared native file.
            for path,before,_ in changes:
                if path.is_symlink() or (path.read_text() if path.exists() else None)!=before:
                    raise InstructionError('Concurrent instruction edit: '+str(path))
            link.parent.mkdir(parents=True,exist_ok=True)
            if not os.path.lexists(link):link.symlink_to(env_file)
            elif not(link.is_symlink() and link.resolve()==env_file):raise InstructionError('Concurrent environment change')
            backups=state/'backups';backups.mkdir(exist_ok=True,mode=0o700);backups.chmod(0o700)
            for path,before,after in changes:
                if before is not None:
                    digest=hashlib.sha256((str(path)+'\0'+before).encode()).hexdigest()
                    backup=backups/digest
                    if not backup.exists():_atomic_write(backup,before)
                path.parent.mkdir(parents=True,exist_ok=True)
                mode=path.stat().st_mode & 0o777 if path.exists() else 0o600
                _atomic_write(path,after)
                path.chmod(mode)
            for row in results:
                if row['state']=='change_required':row['state']='installed'
            _atomic_write(state/'last-install.json',json.dumps({'env_file':str(env_file),'results':results},ensure_ascii=False,indent=2)+'\n')
    return {'applied':apply,'environment':str(link),'results':results}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env-file',required=True,type=Path)
    p.add_argument('--home',type=Path,default=Path.home());p.add_argument('--agents',nargs='+',required=True,choices=['codex','claude','devin','hermes','cursor'])
    p.add_argument('--apply',action='store_true')
    args=p.parse_args(argv)
    try:result=configure(args.env_file,args.home,args.agents,apply=args.apply)
    except (InstructionError,OSError,ValueError) as e:print(str(e),file=sys.stderr);return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':sys.exit(main())
