#!/usr/bin/env python3
"""Run the daily news workflow through publication and confirmed Discord delivery."""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import daily_it_news as news
from daily_it_news_runtime import read_verified, run_command

DELIVERY_KEYS = ['USER_VAULT_ROOT','USER_VAULT_REMOTE','AGENTS_VAULT_REMOTE','NEWS_PUBLICATION_BRANCH',
                 'PUBLISHER_GIT_NAME','PUBLISHER_GIT_EMAIL','GITLEAKS_BIN','HERMES_BRIDGE_PYTHON','DISCORD_NEWS_TARGET']


def preflight(config, env):
    for key in DELIVERY_KEYS:
        if not env.get(key):
            raise news.RunnerError('missing delivery configuration: '+key)
    if not re.fullmatch(r'discord:[1-9][0-9]{16,19}', env['DISCORD_NEWS_TARGET']):
        raise news.RunnerError('invalid Discord target')
    for command in ['git', config.codex_bin, env['GITLEAKS_BIN'], env['HERMES_BRIDGE_PYTHON']]:
        if not shutil.which(command):
            raise news.RunnerError('required executable is unavailable: '+Path(command).name)
    for path in [config.workdir/'collect-public-sources.py', config.workdir/'validate-collection-result.py',
                 config.workdir/'it-news-sources.json', config.skills_root/'hermes-agent-bridge/scripts/hermes_bridge.py']:
        if not path.is_file():
            raise news.RunnerError('required helper is unavailable: '+path.name)


def write_json(path, value):
    news.atomic_write(Path(path), (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode())


def snapshot_advisory_input(summary, run_root, expected_sha256):
    """Isolate advice from transient cloud placeholders, bound to news validation."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise news.RunnerError('verified summary digest is required')
    try:
        content = read_verified(summary, expected_sha256)
    except (OSError, RuntimeError) as exc:
        raise news.RunnerError('cannot read content matching the verified summary digest') from exc
    path = run_root/'advisory-input'/summary.name
    news.atomic_write(path, content)
    path.chmod(0o444)
    write_json(run_root/'advisory-input.json', {'name': summary.name, 'sha256': expected_sha256,
               'bytes': len(content), 'snapshot_path': str(path)})
    return path


def generate_advisory(config, summary, run_root, expected_summary_sha256):
    identity = hashlib.sha256((summary.name+expected_summary_sha256).encode()).hexdigest()
    cached = config.runtime_root/'advisory-cache'/f'{identity}.json'
    try:
        data=json.loads(cached.read_text());path=Path(data['path'])
        if data.get('summary_sha256') == expected_summary_sha256:
            read_verified(Path(data.get('snapshot_path', str(path))), data['sha256'])
            return path
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):
        pass
    snapshot = snapshot_advisory_input(summary, run_root, expected_summary_sha256)
    stage=run_root/'advisory-staging';stage.mkdir()
    reference=f'{summary.name} (same-run SHA-256: {expected_summary_sha256})'
    inventory_started = news.dt.datetime.now(news.JST).strftime('%Y-%m-%d %H:%M JST')
    prompt=f'''personal-vulnerability-advisorを使い、日本語の助言を作成してください。
スキル: {config.skills_root/'personal-vulnerability-advisor/SKILL.md'}
検証済みの今日の入力ニュース（同期フォルダから独立した読み取り用コピー）: {snapshot}
入力ニュース欄の正確な値: {reference}
棚卸し開始日時: {inventory_started}。棚卸し日時はこの値または実測時刻を使い、推測しない。
ニュースは上記コピーだけを読み、元のVaultを探索しない。コピーはrunnerが検証済みSHA-256と一致することを確認済み。もし読み取りに失敗、空本文、hash不一致があれば正常な助言として完成させずstatus=failedを返す。
出力先: {stage/'Personal-Vulnerability-Advisory.md'}
この作業はニュース収集完了後の助言工程です。ニュースを再生成しない。旧Saihai task/authority/manifest/公開ゲートは廃止済みです。
現在のローカル環境の製品名・バージョン・有無だけを読み取り、ニュースで挙がった脆弱性をvendor公式advisoryで確認し照合する。macOS、Applications、Homebrew、npm globalを対象とする。棚卸し失敗は範囲未確認と書き、確認済み扱いにしない。
更新、削除、権限変更、設定変更、Git、送信、ToDo登録は行わない。要確認や情報不足を緊急対応と断定しない。
スキルの必須セクション（結論、推奨対応、対象外 / 情報不足、Audit Notes）と件数表を含め、根拠の個別公式URLを添える。公開対象の本文に個人名を含むホームの絶対パス、stagingパス、秘密値を書かない。必要なパスは環境変数表記にする。
最後はJSONだけ: {{"status":"created","advisory_path":"保存した絶対パス"}}。作成失敗はstatus=failedとreason。
'''
    news.atomic_write(run_root/'advisory.prompt.md',prompt.encode())
    command=news.codex_command(config,prompt)
    command[-1:-1]=['--output-last-message',str(stage/'result.json')]
    news.logged_command(run_command,command,run_root,'advisory',cwd=stage,
                        env=dict(os.environ),input=prompt,timeout=1200)
    if news.digest(snapshot) != expected_summary_sha256:
        raise news.RunnerError('advisory input snapshot changed')
    data=json.loads((stage/'result.json').read_text())
    source=Path(data.get('advisory_path',''))
    if data.get('status')!='created' or not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(stage.resolve()):
        raise news.RunnerError('advisory was not created in this run')
    text=source.read_text()
    for required in [reference,'## 結論','## 推奨対応','## 対象外 / 情報不足','## Audit Notes','棚卸し日時']:
        if required not in text:raise news.RunnerError('advisory missing '+required)
    if re.search(r'/(?:Users|home)/[^\s]+',text):raise news.RunnerError('advisory contains a personal path')
    archive=config.vault_root/'03-Contexts/Reports/Security';archive.mkdir(parents=True,exist_ok=True)
    date=news.dt.datetime.now(news.JST).date()
    for i in range(1,1000):
        path=archive/f'Personal-Vulnerability-Advisory-{date}{"" if i==1 else "-"+str(i)}.md'
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:continue
        try:
            with os.fdopen(fd,'wb') as f:f.write(text.encode());f.flush();os.fsync(f.fileno())
        except BaseException:
            path.unlink();raise
        write_json(cached,{'path':str(path),'sha256':hashlib.sha256(text.encode()).hexdigest(),
                          'summary_sha256':expected_summary_sha256,'snapshot_path':str(source)})
        return path
    raise news.RunnerError('advisory filename collision limit')


def complete_delivery(config, env, summary, advisory, run_root, expected_summary_sha256):
    import daily_it_news_delivery as delivery
    for key in DELIVERY_KEYS:
        if not env.get(key):raise news.RunnerError('missing delivery configuration: '+key)
    user_root=Path(env['USER_VAULT_ROOT']).resolve()
    summary_relative=summary.resolve().relative_to(user_root).as_posix()
    advisory_relative=advisory.resolve().relative_to(config.vault_root.resolve()).as_posix()
    summary_input = run_root/'advisory-input'/summary.name
    if not summary_input.is_file():
        summary_input = snapshot_advisory_input(summary, run_root, expected_summary_sha256)
    identity = hashlib.sha256((summary.name+expected_summary_sha256).encode()).hexdigest()
    cached = json.loads((config.runtime_root/'advisory-cache'/f'{identity}.json').read_text())
    if cached.get('path') != str(advisory) or cached.get('summary_sha256') != expected_summary_sha256:
        raise news.RunnerError('advisory cache does not match the current news')
    advice_input = Path(cached.get('snapshot_path', str(advisory)))
    def publish(remote, artifact, relative, phase, expected=None):
        return delivery.publish_artifact(remote,env['NEWS_PUBLICATION_BRANCH'],artifact,relative,
                 run_root/phase,env['PUBLISHER_GIT_NAME'],env['PUBLISHER_GIT_EMAIL'],env['GITLEAKS_BIN'], expected_sha256=expected)
    # A reviewed optional candidates file must exist in the news commit if linked.
    for name in re.findall(r'\]\((IT-NEWS-SOURCES-\d{4}-\d{2}-\d{2}\.json)\)',read_verified(summary_input, expected_summary_sha256).decode()):
        sidecar=summary.parent/name
        publish(env['USER_VAULT_REMOTE'],sidecar,sidecar.relative_to(user_root).as_posix(),'publish-sources')
    public_news=publish(env['USER_VAULT_REMOTE'],summary_input,summary_relative,'publish-news',expected_summary_sha256)
    public_advice=publish(env['AGENTS_VAULT_REMOTE'],advice_input,advisory_relative,'publish-advisory',cached['sha256'])
    write_json(run_root/'publication.json',{'news':public_news,'advisory':public_advice})
    key=hashlib.sha256((expected_summary_sha256+cached['sha256']).encode()).hexdigest()
    summary_date = '-'.join(news.SUMMARY_RE.fullmatch(summary.name).groups()[:3])
    result=delivery.notify_discord(summary_date,public_news['url'],public_advice['url'],key,
               config.runtime_root/'delivery-state',env['HERMES_BRIDGE_PYTHON'],env['DISCORD_NEWS_TARGET'],
               'daily-news-'+key[:24],config.skills_root/'hermes-agent-bridge/scripts/hermes_bridge.py')
    result.update(news_url=public_news['url'],advisory_url=public_advice['url'])
    write_json(run_root/'discord-result.json',result)
    return result


def run_batch(config, env, *, force=False, resume_run=None):
    config.runtime_root.mkdir(parents=True,exist_ok=True)
    lock=os.open(config.runtime_root/'.daily-it-news-batch.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock);raise news.RunnerError('another full daily news batch is active')
    now=news.dt.datetime.now(news.JST)
    run_root=config.runtime_root/'batch-logs'/str(now.date())/f'{now:%H%M%S}-{os.getpid()}-{secrets.token_hex(3)}'
    state={'status':'running','phase':'preflight','started_at':now.isoformat(timespec='seconds'),'run_root':str(run_root)}
    def persist():
        for path in [run_root/'status.json',config.runtime_root/'last-batch-status.json',config.runtime_root/'last-status.json']:
            write_json(path,state)
    try:
        run_root.mkdir(parents=True)
        persist()
        preflight(config, env)
        result = None
        summary_input = None
        advisory = None
        retry_resume = resume_run
        for attempt in range(1, 4):
            state.update(attempt=attempt, status='running')
            attempt_root=run_root/f'attempt-{attempt}';attempt_root.mkdir()
            try:
                if result is None or result['status'] not in {'complete','already_complete'}:
                    state['phase']='news';persist()
                    result=news.run(config,force=force if attempt == 1 else False,
                                    resume_run=retry_resume,today=now.date())
                    state['news_result']=result
                    if result['status'] not in {'complete','already_complete'}:
                        failed_root=Path(result.get('run_root',''))
                        retry_resume=result.get('run_id') if (failed_root/'source-inputs/source-manifest.json').is_file() else None
                        raise news.RunnerError('news stage failed: '+str(result.get('error','')))
                summary=Path(result['summary_path']);state['summary_path']=str(summary)
                if summary_input is None:
                    state['phase']='input';persist()
                    summary_input=snapshot_advisory_input(summary,run_root,result['summary_sha256'])
                if advisory is None:
                    state['phase']='advisory';persist()
                    advisory=generate_advisory(config,summary_input,attempt_root,result['summary_sha256'])
                    state['advisory_path']=str(advisory)
                state['phase']='publication';persist()
                sent=complete_delivery(config,env,summary,advisory,run_root,result['summary_sha256'])
                state.update(phase='discord',delivery=sent)
                if (sent['delivery_status'] not in {'delivered','skipped'}
                        or not re.fullmatch(r'[1-9][0-9]{16,19}',str(sent.get('message_id') or ''))):
                    raise news.RunnerError('Discord delivery is not confirmed: '+str(sent.get('error','')))
                state.update(status='complete',phase='done')
                state.pop('error',None)
                break
            except Exception as exc:
                state.update(status='retrying' if attempt < 3 else 'blocked',error=str(exc))
                write_json(run_root/f'attempt-{attempt}.json',state)
                persist()
                if attempt < 3:
                    time.sleep(5 if attempt == 1 else 15)
    except Exception as exc:
        state.update(status='blocked',error=str(exc))
    finally:
        state['completed_at']=news.dt.datetime.now(news.JST).isoformat(timespec='seconds')
        try:
            persist()
            try:write_json(config.vault_root/'03-Contexts/Reports/IT-News-Runs'/str(now.date())/(run_root.name+'.batch.json'),state)
            except OSError:pass
        finally:os.close(lock)
    return state


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--force',action='store_true');parser.add_argument('--resume-run');args=parser.parse_args()
    try:result=run_batch(news.Config.from_env(),dict(os.environ),force=args.force,resume_run=args.resume_run)
    except Exception as exc:result={'status':'blocked','error':str(exc)}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='complete' else 75

if __name__=='__main__':raise SystemExit(main())
