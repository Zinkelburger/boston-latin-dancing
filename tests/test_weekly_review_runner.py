"""Exercise the real review shell runner with inert external commands."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/usr/bin/env bash\nset -eu\n' + body)
    path.chmod(0o755)


@pytest.mark.parametrize('dirty,refresh_rc,agent_rc,finish_rc,expected', [
    (True, 0, 0, 0, ['git']),
    (False, 2, 0, 0, ['git', 'refresh']),
    (False, 0, 7, 0, ['git', 'refresh', 'prepare', 'agent', 'status']),
    (False, 0, 0, 1, ['git', 'refresh', 'prepare', 'agent', 'recheck', 'status', 'finish', 'git']),
    (False, 0, 0, 0, ['git', 'refresh', 'prepare', 'agent', 'recheck', 'status', 'finish', 'commit', 'git']),
])
def test_runner_stops_before_deployment_on_failure(tmp_path, dirty, refresh_rc, agent_rc, finish_rc, expected):
    _run_runner(tmp_path, dirty, refresh_rc, agent_rc, finish_rc, 0, expected)


@pytest.mark.parametrize('recheck_rc,expected', [
    # 2026-10-07: problems found after every question was answered left the
    # week unpublished; now they are follow-up questions for one more pass.
    (3, ['git', 'refresh', 'prepare', 'agent', 'recheck', 'agent', 'status', 'finish', 'commit', 'git']),
    (1, ['git', 'refresh', 'prepare', 'agent', 'recheck', 'status', 'finish', 'commit', 'git']),
])
def test_follow_up_questions_get_one_more_agent_pass(tmp_path, recheck_rc, expected):
    _run_runner(tmp_path, False, 0, 0, 0, recheck_rc, expected)


def _run_runner(tmp_path, dirty, refresh_rc, agent_rc, finish_rc, recheck_rc, expected):
    trace = tmp_path / 'calls'
    _executable(tmp_path / 'bin/git', 'echo git >> "$TRACE"\nif [[ "$DIRTY" == 1 ]]; then echo " M user-file"; fi\n')
    _executable(tmp_path / 'automation/refresh.sh', 'echo refresh >> "$TRACE"\nexit "$REFRESH_RC"\n')
    _executable(tmp_path / 'automation/commit_pipeline.sh', 'echo commit >> "$TRACE"\n')
    _executable(tmp_path / '.venv/bin/python', '''
if [[ "$1" == -c ]]; then exit 1; fi
echo "$2" >> "$TRACE"
if [[ "$2" == finish ]]; then exit "$FINISH_RC"; fi
if [[ "$2" == recheck ]]; then exit "$RECHECK_RC"; fi
''')
    _executable(tmp_path / 'bin/claude', 'echo agent >> "$TRACE"\nexit "$AGENT_RC"\n')
    (tmp_path / 'automation/agent_prompt.md').write_text('Test review')
    env = {**os.environ, 'BLD_REPO_DIR': str(tmp_path), 'TRACE': str(trace),
           'PATH': f'{tmp_path / "bin"}:{os.environ["PATH"]}', 'DIRTY': str(int(dirty)),
           'REFRESH_RC': str(refresh_rc), 'AGENT_RC': str(agent_rc), 'FINISH_RC': str(finish_rc),
           'RECHECK_RC': str(recheck_rc),
           'BLD_SKIP_REFRESH': '0'}
    result = subprocess.run(['bash', str(ROOT / 'automation/claude_review.sh')],
                            env=env, capture_output=True, text=True, timeout=20)
    assert trace.read_text().splitlines() == expected, result.stderr
    assert (result.returncode == 0) == ('commit' in expected)
    log = [json.loads(line) for line in (tmp_path / 'automation/logs/review-latest.jsonl').read_text().splitlines()]
    assert log[-1]['kind'] == 'exit'
    assert int(log[-1]['text']) == result.returncode


@pytest.mark.parametrize('failed,suspect', [(False, False), (True, False), (False, True)])
def test_review_pipeline_never_publishes(monkeypatch, tmp_path, failed, suspect):
    import run_pipeline as pipeline

    published = tmp_path / 'published.json'
    published.write_text('[{"id":"last-good"}]')
    monkeypatch.setattr(pipeline, 'PUBLIC_EVENTS_JSON', published)
    monkeypatch.setattr(pipeline.sys, 'argv', ['run_pipeline.py', '--no-publish'])
    monkeypatch.setattr(pipeline, 'run_scrapers', lambda **kw: [
        {'source_id': 'test', 'ok': not failed, 'stderr_tail': 'failure' if failed else ''}])
    monkeypatch.setattr(pipeline, 'load_scrape_health', lambda: {
        'test': {'status': 'structure_missing' if suspect else 'ok'}})
    ingests = []
    monkeypatch.setattr(pipeline, 'ingest_scraped', lambda **kw: ingests.append(kw) or {})
    monkeypatch.setattr(pipeline, 'archive_past_events', lambda: [])
    monkeypatch.setattr(pipeline, 'publish_guarded', lambda **kw: pytest.fail('review must not publish'))
    assert pipeline.main() == int(failed or suspect)
    assert ingests == [{'quarantine_new': True}]
    assert published.read_text() == '[{"id":"last-good"}]'


def test_refresh_review_mode_never_commits_or_checks_published_links(tmp_path):
    trace = tmp_path / 'calls'
    _executable(tmp_path / 'bin/git', 'echo "git $*" >> "$TRACE"\n')
    _executable(tmp_path / '.venv/bin/pip', 'exit 0\n')
    _executable(tmp_path / '.venv/bin/python', 'echo "python $*" >> "$TRACE"\n')
    _executable(tmp_path / 'automation/commit_pipeline.sh', 'echo commit >> "$TRACE"\n')
    env = {**os.environ, 'BLD_REPO_DIR': str(tmp_path), 'TRACE': str(trace),
           'PATH': f'{tmp_path / "bin"}:{os.environ["PATH"]}'}
    result = subprocess.run(['bash', str(ROOT / 'automation/refresh.sh'), '--review'],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        'git status --porcelain', 'git pull --rebase --quiet',
        'python scripts/run_pipeline.py --no-publish']
