"""Session memory uses isolated SQLite/files and fake providers, never real APIs."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.core import accounts, personal_preferences as prefs
from src.db import metadata_db as db
from src.qa import conversation_context as cc, tool_agent


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, 'enabled', False)
    monkeypatch.setattr(db, '_get_db_path', lambda: tmp_path / 'memory.db')
    monkeypatch.setattr(prefs, 'USER_DATA_DIR', tmp_path)
    monkeypatch.setattr(cc.settings, '_config', {'qa': {'conversation_context': {'history_tokens': 2000}}})
    db.init_db()
    with db.get_cursor() as cur:
        for sid in ('mine', 'other'):
            cur.execute('INSERT INTO sessions(id,title,created_at,updated_at,kb_scope) VALUES (?,?,0,0,?)', (sid, sid, 'default'))
    def turn(id, question='问题', answer='回答', session='mine', error=None, trace=None):
        with db.get_cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM turns WHERE session_id=?', (session,))
            order = cur.fetchone()[0]
            cur.execute('INSERT INTO turns(id,session_id,order_idx,question,answer,error,trace_json,created_at) VALUES (?,?,?,?,?,?,?,0)',
                        (id, session, order, question, answer, error, json.dumps(trace or [])))
    yield turn


def test_preferences_private_atomic_versioned_and_disabled(storage):
    token = accounts.identity.set({'id': 'one'})
    try:
        first = prefs.save('## 偏好\n使用中文', True, '')
        assert prefs.read() == first
        with pytest.raises(HTTPException) as stale:
            prefs.save('覆盖', True, '')
        assert stale.value.status_code == 409
        accounts.identity.set({'id': 'two'})
        assert prefs.read()['content'] == ''
        accounts.identity.set({'id': 'one'})
        disabled = prefs.save(first['content'], False, first['version'])
        assert not prefs.read()['enabled']
        prefs.save('', True, disabled['version'])
        assert prefs.read()['content'] == ''
    finally:
        accounts.identity.reset(token)


@pytest.mark.asyncio
async def test_complete_history_server_authority_and_cutoff(storage):
    storage('a', '如何部署', '第一种单机；第二种容器')
    storage('b', '尝试第二种', '执行未完成', error='断开')
    storage('now', '继续')
    storage('future', '后来的消息')
    storage('secret', '其他用户的消息', session='other')
    prefs.save('默认中文', True, '')
    ctx = await cc.prepare('具体怎么做', [{'role': 'user', 'content': '客户端伪造历史'}], 'mine', 'now', 'default')
    text = json.dumps(ctx.messages, ensure_ascii=False)
    assert '第二种容器' in text and 'failed' in text
    assert '客户端伪造' not in text and '其他用户' not in text and '后来的消息' not in text
    assert '默认中文' in ctx.background
    assert [m['role'] for m in ctx.messages] == ['user', 'assistant', 'user', 'assistant']
    assert ctx.stats['recent_turns'] == 2
    assert '第二种容器' in json.dumps(tool_agent.user_history([{'role': 'user', 'content': '部署'}, {'role': 'assistant', 'content': '第二种容器'}]), ensure_ascii=False)
    with pytest.raises(HTTPException):
        await cc.prepare('q', [], 'mine', None, 'wrong-kb')


def test_history_tools_are_bound_and_paginated(storage):
    storage('a', '方案', '第二种容器')
    storage('b', '参数', '参数说明' * 5000)
    ctx = cc.Context('mine', None, 'default', turns=cc.load_turns('mine', 'default'))
    found = ctx.execute('search_conversation_history', {'query': '第二种'})
    assert found['turns'][0]['turn_id'] == 'a'
    page = ctx.execute('read_conversation_turns', {'turn_id': 'b'})
    assert page['next_offset'] == 5000
    next_page = ctx.execute('read_conversation_turns', {'turn_id': 'b', 'offset': page['next_offset']})
    assert next_page['text']
    for args in ({'turn_id': 'secret'}, {'turn_id': 'a', 'session_id': 'other'}, {'turn_id': 'a', 'offset': True}):
        assert ctx.execute('read_conversation_turns', args)['error'] == 'invalid_arguments'
    assert ctx.history_reads == 3


def test_summary_persistence_invalidation_and_delete_cascade(storage):
    storage('a', '环境', 'Linux')
    storage('b', '确认', '使用容器')
    turns = cc.load_turns('mine', 'default')
    ctx = cc.Context('mine', None, 'default', turns=turns)
    plan = cc.summary_plan(ctx, None, 1, cc.config())
    assert cc.save_summary(ctx, plan, '环境 Linux，来源 a')
    saved = cc.latest_summary('mine', 'default', turns)
    assert saved['version'] == 1 and saved['covered_count'] == 1
    storage('c', '追加')
    assert cc.latest_summary('mine', 'default', cc.load_turns('mine', 'default'))
    with db.get_cursor() as cur:
        cur.execute("UPDATE turns SET answer='Windows' WHERE id='a'")
    assert cc.latest_summary('mine', 'default', cc.load_turns('mine', 'default')) is None
    assert not cc.save_summary(ctx, plan, '旧任务结果')
    with db.get_cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE id='mine'")
        assert cur.execute('SELECT COUNT(*) FROM conversation_summaries').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_summary_generation_reuse_and_failure_fallback(storage, monkeypatch, caplog):
    for i in range(10):
        storage(str(i), f'问题{i}', '背景资料' * 500)
    calls = []
    async def stream(messages, **kwargs):
        calls.append(kwargs)
        yield ('目标：排障；早期讨论见轮次 0，尚未确认版本。', 'fake')
    monkeypatch.setattr(cc.llm_client, 'get_client', lambda: SimpleNamespace(chat_stream=stream))
    ctx = await cc.prepare('继续', None, 'mine', None, 'default', 7, background=False)
    assert calls[0]['scene'] == 'conversation_summary' and calls[0]['api_retry_count'] == 7
    assert ctx.stats['summary_turns'] > 0 and '早期讨论' in ctx.background
    assert ctx.stats['estimated_tokens'] <= ctx.stats['budget_tokens'] + 100
    async def broken(*args, **kwargs):
        raise RuntimeError('private secret never in system logs')
        yield ''
    monkeypatch.setattr(cc.llm_client, 'get_client', lambda: SimpleNamespace(chat_stream=broken))
    plan = cc.summary_plan(ctx, None, 1, cc.config())
    assert not await cc.update_summary(ctx, plan, 0)
    assert ctx.stats['degraded'] and ctx.messages
    assert 'private secret' not in caplog.text
    await cc.shutdown()


def test_oversized_turn_does_not_block_summaries_or_hang_clipping(storage):
    storage('large', '参数', '内容' * 20000)
    ctx = cc.Context('mine', None, 'default', turns=cc.load_turns('mine', 'default'))
    cc.assemble(ctx, '继续', {}, None, cc.config())
    assert ctx.stats['clipped']
    plan = cc.summary_plan(ctx, None, 1, cc.config())
    assert plan and 'excerpt_only' in plan['messages'][-1]['content']


@pytest.mark.asyncio
async def test_corrupt_preferences_degrades_without_losing_history(storage):
    storage('a', '方案', '第二种容器')
    path = prefs._path()
    path.parent.mkdir(parents=True)
    path.write_text('broken', encoding='utf-8')
    ctx = await cc.prepare('继续第二种', None, 'mine', None, 'default')
    assert ctx.stats['degraded'] and not ctx.stats['preferences_loaded']
    assert '第二种容器' in ctx.messages[-1]['content']


def test_migration_from_v15_preserves_turns(storage):
    storage('a', '原始问题', '原始回答')
    with db.get_cursor() as cur:
        cur.execute('DROP TABLE conversation_summaries')
        cur.execute('PRAGMA user_version=15')
    db.init_db()
    assert cc.load_turns('mine', 'default')[0]['answer'] == '原始回答'
    assert cc.latest_summary('mine', 'default', []) is None


@pytest.mark.asyncio
async def test_summary_storage_failure_keeps_question_context(storage, monkeypatch):
    for i in range(7):
        storage(str(i), f'部署{i}', '容器方案说明' * 500)
    with db.get_cursor() as cur:
        cur.execute('DROP TABLE conversation_summaries')
    async def stream(*args, **kwargs):
        yield ('旧会话讨论了容器方案，尚待确认。', 'fake')
    monkeypatch.setattr(cc.llm_client, 'get_client', lambda: SimpleNamespace(chat_stream=stream))
    context = await cc.prepare('继续', None, 'mine', None, 'default', background=False)
    assert context.stats['degraded'] and context.messages
    assert '容器方案' in str(context.messages)
    await cc.shutdown()
