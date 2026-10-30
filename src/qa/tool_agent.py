"""Bounded native-tool knowledge assistant used by the deep_ai entry point."""
from __future__ import annotations

import asyncio
import json
import math
import logging
import re
import threading
import time

import httpx

from fastapi import HTTPException

from src.core.config import settings
from src.core.deep_ai_options import DeepAiOptions
from src.core.tool_chat import create_tool_chat, ToolProtocolError
from src.core.api_retry import RetryDeferredError
from src.core import ai_call_logger
from src.qa.knowledge_tools import KnowledgeTools, TOOLS, LABELS
from src.qa import field_search, evidence_state, ai_policy
from src.qa.trace import PipelineCancelled
from src.qa import conversation_context

logger = logging.getLogger(__name__)




class CitationFilter:
    """Validate explicit citation markers; ordinary numeric brackets are content."""
    def __init__(self, count, allowed=None):
        self.count, self.pending, self.removed = count, "", False
        self.allowed = set(range(1, count + 1)) if allowed is None else set(allowed)

    def clean(self, text):
        def replace(match):
            if int(match[1]) in self.allowed:
                return match[0]
            self.removed = True
            return ""
        return re.sub(r"【cite:(\d+)】", replace, text)

    def feed(self, text, final=False):
        self.pending += text
        # Buffer split explicit markers without touching Markdown brackets.
        start = self.pending.rfind("【")
        if not final and start >= 0 and "】" not in self.pending[start:] and len(self.pending) - start < 128:
            value, self.pending = self.pending[:start], self.pending[start:]
        else:
            value, self.pending = self.pending, ""
        return self.clean(value)


def user_history(history):
    # Compatibility for direct callers: keep assistant replies as discussion.
    bundle = conversation_context.Context(None, None, '')
    bundle.turns = conversation_context.external_turns(history)
    conversation_context.assemble(bundle, '', {}, None, conversation_context.config())
    return bundle.messages


def result_count(result):
    """Tools return either collections or an overview's numeric document count."""
    for key in ("evidence", "objects", "members", "documents", "sections"):
        value = result.get(key)
        if isinstance(value, (list, tuple, dict)):
            return len(value)
        if type(value) is int:
            return max(0, value)
    return 0


async def ask_stream(question, history, kb_scope, *, top_k=None, session_id=None, turn_id=None, strict_knowledge=False, api_retry_count=10, deep_ai_options=None):
    context = conversation_context.active.get()
    tools = [*TOOLS, evidence_state.REVIEW_TOOL]
    if context and context.session_id and context.turns:
        tools.extend(conversation_context.HISTORY_TOOLS)
    queue = asyncio.Queue()
    stopped = threading.Event()
    trace, parts = [], []
    state = {"provider": "", "verification": "sources_validated", "outcome": "success", "reason": ""}
    qa_cfg = settings.config.get("qa", {}) or {}
    cfg = qa_cfg.get("deep_ai") or {}
    gen = qa_cfg.get("ai_generation") or {}

    def number(key, default, minimum, maximum=None):
        # ai_generation (unified card) wins; legacy per-AI keys stay as fallback.
        for source in (gen, cfg):
            if key in source:
                try:
                    value = int(source[key])
                except (ValueError, TypeError):
                    return default
                value = max(minimum, value)
                return min(maximum, value) if maximum is not None else value
        return default

    options = deep_ai_options or DeepAiOptions(
        max_rounds=cfg.get('max_rounds', 10),
        time_limit_enabled=cfg.get('time_limit_enabled', False),
        total_timeout_seconds=cfg.get('total_timeout_seconds', 360))
    if isinstance(options, dict):
        options = DeepAiOptions.model_validate(options)
    strategy = ai_policy.resolve(options.constraint_strategy, strict_knowledge)
    total = options.total_timeout_seconds if options.time_limit_enabled else float('inf')
    rounds = options.max_rounds
    # No hidden twelve-call cap. Allow several tools per model round.
    max_calls = rounds * 4 if rounds is not None else float('inf')
    tokens = number("max_tokens", 81920, 256)
    answer_seconds = number("timeout_seconds", 600, 30)
    round_seconds = max(180, answer_seconds)
    deadline = time.monotonic() + total
    reserve = min(60, total / 3) if math.isfinite(total) else 0
    meta = {"session_id": session_id, "turn_id": turn_id, "kb_id": kb_scope}

    def check_cancel():
        if stopped.is_set():
            raise PipelineCancelled("请求已取消")

    from src.core import ai_read_limits as read_limits
    tool_timeout = number("tool_timeout_seconds", read_limits.TOOL_TIMEOUT_DEFAULT, read_limits.TOOL_TIMEOUT_MIN, read_limits.TOOL_TIMEOUT_MAX)
    kb = KnowledgeTools(kb_scope, check_cancel, max_chars=number("context_chars", read_limits.CONTEXT_DEFAULT, read_limits.CONTEXT_MIN, read_limits.CONTEXT_MAX), top_k=top_k, question=question)

    def stage(name, label, started=None, **kwargs):
        data = {"stage": name, "label": label, "duration_ms": round((time.monotonic() - started) * 1000) if started else 0, **kwargs}
        trace.append(data)
        queue.put_nowait({"type": "stage", "data": data})

    def stage_running(name, label):
        # 阶段开始的瞬时标记：只投递 SSE 供前端高亮进行中步骤，不写入最终 trace
        queue.put_nowait({"type": "stage", "data": {"stage": name, "label": label, "status": "running"}})

    async def bounded(awaitable, limit, *, tools_phase=False):
        remaining = deadline - time.monotonic() - (reserve if tools_phase else 0)
        timeout = min(limit, remaining)
        return await asyncio.wait_for(awaitable, max(0.01, timeout) if math.isfinite(timeout) else None)

    async def produce():
        model = None
        terminal = None
        reason = "已完成资料查阅"
        stop_reason = 'model_ready'
        try:
            await bounded(asyncio.to_thread(kb.check), total)
            stage("agent_start", "开始查阅知识库", notes="AI 将按问题选择检索、目录和原文阅读工具")
            background = await bounded(asyncio.to_thread(kb.overview), 15, tools_phase=True)
            system = ai_policy.prompt(strategy, 'research')
            system += "\n当前知识库背景（不可作为原文引用）：\n" + json.dumps(background, ensure_ascii=False)
            if context and context.background:
                system += '\n' + context.background
            elif history:
                system += '\n' + conversation_context.RULES
            messages = list(context.messages if context else user_history(history))
            messages.append({'role': 'user', 'content': question})
            model = await bounded(asyncio.to_thread(create_tool_chat, system, messages, meta=meta), 15, tools_phase=True)
            model.retry_count = min(10, max(0, int(api_retry_count)))
            model.deadline = deadline - reserve
            # 单次 API 尝试的时长上限随作答预算放宽（推理模型思考期也在 attempt 内）
            model.round_attempt_seconds = round_seconds
            model.turn_attempt_seconds = round_seconds
            model.on_retry = lambda **info: stage("api_retry", "API 请求重试", status="partial",
                notes=f"{info['reason']}，{info['delay']:g} 秒后进行第 {info['attempt']}/{info['maximum']} 次重试")
            state["provider"] = model.name
            called, repeated, stalled, directory_run, round_no = 0, 0, 0, 0, 0
            stage('agent_limits', '深度 AI 查阅配置', notes=f"查阅轮数：{rounds if rounds is not None else '不限'}；总时长：{str(options.total_timeout_seconds) + ' 秒' if options.time_limit_enabled else '不限'}；单轮/作答时长：{answer_seconds} 秒；输出上限：{tokens} tokens；保留单次 API 超时及无进展保护")
            logger.info('深度 AI 预算 session=%s turn=%s rounds=%s total_seconds=%s max_calls=%s', session_id, turn_id, rounds, total, max_calls)
            progress_tracker = evidence_state.Progress()
            review = evidence_state.Review()
            history_reads = []
            delivered_evidence = []
            answer_streamed = False
            live_removed = False
            while rounds is None or round_no < rounds:
                input_estimate = conversation_context.estimate(system + json.dumps(
                    [getattr(model, 'messages', messages), tools], ensure_ascii=False))
                window = conversation_context.config()['context_window_tokens']
                # 巨大的输出上限不能按全额预留：只要还给作答留出合理空间就继续查阅
                if called and input_estimate + min(tokens, 8192) + 4096 >= window:
                    stop_reason = 'context_capacity'
                    reason = '接近模型上下文容量，停止补查并使用已读资料回答'
                    logger.info('深度 AI 上下文停止 session=%s turn=%s input_estimate=%s window=%s',
                                session_id, turn_id, input_estimate, window)
                    break
                # anthropic 协议要求 input + max_tokens <= 窗口：按剩余空间钳制本次输出上限
                round_tokens = min(tokens, max(1024, window - input_estimate - 2048))
                if called >= max_calls or time.monotonic() >= deadline - reserve or kb.used_chars >= kb.max_chars:
                    stop_reason = 'tool_calls' if called >= max_calls else 'time_limit' if time.monotonic() >= deadline - reserve else 'evidence_capacity'
                    reason = "达到本次查阅预算，依据已读资料回答并说明缺口"
                    break
                started = time.monotonic()
                stage_running("agent_round", f"第 {round_no + 1} 轮资料查阅")
                model.deadline = min(deadline - reserve, time.monotonic() + round_seconds)
                try:
                    round_emitted = False
                    if hasattr(model, 'stream_turn'):
                        live_citations = CitationFilter(len(kb.hits))
                        turn = None
                        async for kind, value in model.stream_turn(tools,max_tokens=round_tokens):
                            check_cancel()
                            if kind == 'turn':
                                turn = value
                            else:
                                await asyncio.to_thread(kb.check)
                                clean = live_citations.feed(value)
                                if clean:
                                    round_emitted = True
                                    parts.append(clean)
                                    queue.put_nowait({'type':'token','data':{'text':clean}})
                        tail = live_citations.feed('',final=True)
                        if tail:
                            parts.append(tail)
                            queue.put_nowait({'type':'token','data':{'text':tail}})
                        live_removed = live_removed or live_citations.removed
                        if turn is None:
                            raise ToolProtocolError('模型没有完整结束本轮响应')
                        if not turn.calls:
                            # 模型结束查阅但一个字都没流出（如仅思考后 end_turn）：
                            # 不能当作已作答，仍需走作答阶段给模型一次正式作答机会
                            answer_streamed = round_emitted
                        elif round_emitted:
                            parts.append('\n\n')
                            queue.put_nowait({'type':'token','data':{'text':'\n\n'}})
                    else:
                        turn = await bounded(model.turn(tools, force=False, max_tokens=round_tokens), round_seconds, tools_phase=True)
                except (asyncio.TimeoutError, ToolProtocolError, httpx.HTTPError, RetryDeferredError) as exc:
                    if round_emitted:
                        answer_streamed = True
                        reason = '模型流式回答中断，已保留部分内容'
                        stop_reason = 'stream_error'
                        state.update(outcome='partial',reason=reason)
                        break
                    if called == 0:
                        if isinstance(exc, (ToolProtocolError, RetryDeferredError)):
                            raise
                        raise ToolProtocolError("首轮模型请求未完成，请查看 AI 调用日志中的连接或超时详情。") from exc
                    if isinstance(exc, RetryDeferredError):
                        raise
                    from src.core.api_retry import failure_info
                    _, failure_reason, _, _ = failure_info(exc)
                    reason = f"资料查阅中断（{failure_reason}），当前仅能依据已读资料提供部分回答"
                    state.update(outcome='partial', verification='partial', reason=reason)
                    logger.warning('深度 AI 查阅中断 session=%s turn=%s reason=%s', session_id, turn_id, failure_reason)
                    stop_reason = 'model_error'
                    stage("agent_lookup_incomplete", "部分资料补查未完成", status="partial", notes=reason)
                    break
                if getattr(turn, "truncated", False):
                    # Output-cap verdict: retrying with the same cap fails the same way.
                    stop_reason = "output_limit"
                    # 输出上限是系统级配置（仅管理员可改），提示用户找管理员调整
                    guidance = "，请联系管理员在系统设置的 AI 生成配置中调大输出上限"
                    if turn.calls:
                        reason = "模型输出达到输出上限，本轮工具调用未执行" + guidance
                        state.update(outcome="partial", verification="partial", reason=reason)
                        logger.warning("深度 AI 轮次输出截断 session=%s turn=%s round=%s calls=%s",
                                       session_id, turn_id, round_no + 1, [c.name for c in turn.calls])
                    else:
                        reason = "模型输出达到输出上限" + guidance
                        answer_streamed = True
                        state.update(outcome="partial", reason=reason)
                    stage("agent_output_limit", "输出达到上限", status="partial", notes=reason)
                    break
                stage("agent_round", f"第 {round_no + 1} 轮资料查阅", started, count=len(turn.calls), notes="模型已选择下一步工具" if turn.calls else "模型结束查阅，准备按现有证据回答")
                if not turn.calls:
                    break
                results = []
                progress = False
                for call in turn.calls:
                    call_label = '整理问题要点' if call.name == evidence_state.REVIEW_TOOL['name'] else conversation_context.HISTORY_LABELS.get(call.name, LABELS.get(call.name, "校验工具请求"))
                    started = time.monotonic()
                    await bounded(asyncio.to_thread(kb.check), total)
                    if called >= max_calls or time.monotonic() >= deadline - reserve:
                        result = {"error": "budget", "message": "查阅预算已用完，请用已有证据回答"}
                    else:
                        called += 1
                        started = time.monotonic()
                        stage_running("knowledge_tool", call_label)
                        call_stopped = threading.Event()
                        def check_call(stop_event=call_stopped):
                            check_cancel()
                            if stop_event.is_set():
                                raise PipelineCancelled("工具调用已停止")
                        # Stage changes in a private workspace. A timed-out worker cannot
                        # add unread evidence or change citation numbering later.
                        worker = KnowledgeTools(kb_scope, check_call, max_chars=kb.max_chars, top_k=kb.top_k, question=question)
                        worker.read_refs = dict(kb.read_refs)
                        for attribute in ('scope_refs','search_pages','search_queries','search_cursors','document_cache'):
                            setattr(worker,attribute,dict(getattr(kb,attribute)))
                        worker.hits, worker.cache, worker.used_chars = [dict(hit) for hit in kb.hits], dict(kb.cache), kb.used_chars
                        try:
                            if call.name == evidence_state.REVIEW_TOOL['name']:
                                result = review.update(call.arguments, kb.hits)
                                stage('evidence_review', '更新问题要点', count=len(review.points),
                                      status='partial' if result.get('error') else 'ok',
                                      notes=result.get('message') or f"记录 {len(review.points)} 项，待解决 {len(review.snapshot()['unresolved'])} 项；摘录已核对，结论未经独立核验")
                            else:
                                execute = context.execute if context and call.name in conversation_context.HISTORY_LABELS else worker.execute
                                result = await bounded(asyncio.to_thread(execute, call.name, call.arguments), tool_timeout, tools_phase=True)
                                if call.name in conversation_context.HISTORY_LABELS and not result.get('error') and not result.get('cached'):
                                    history_reads.append(result)
                            kb.read_refs = dict(worker.read_refs)
                            for attribute in ('scope_refs','search_pages','search_queries','search_cursors','document_cache'):
                                setattr(kb,attribute,dict(getattr(worker,attribute)))
                            kb.hits, kb.cache, kb.used_chars = worker.hits, worker.cache, worker.used_chars
                        except asyncio.TimeoutError:
                            active = getattr(worker, 'active_phase', None)
                            logger.warning('知识库工具超时 tool=%s kb=%s limit_seconds=%s elapsed_seconds=%.3f active_phase=%s completed_phases_ms=%s', call.name, kb_scope, tool_timeout, time.monotonic() - started, active, dict(worker.timings))
                            result = {"error": "timeout", "message": f"本次工具查阅超时（单次上限 {tool_timeout} 秒，亦受整轮剩余时间限制），未取得结果；此前已返回的资料仍有效"}
                            reason = "部分资料查阅超时，依据已读资料回答并说明缺口"
                        finally:
                            call_stopped.set()
                        repeated = repeated + 1 if result.get("cached") else 0
                        stage("knowledge_tool", call_label, started,
                              status="partial" if result.get("error") else "ok", count=None if result.get("error") else result_count(result),
                              notes=result.get("message") or ("复用本次已读结果" if result.get("cached") else result.get("note") or "已完成"))
                    delta = progress_tracker.observe(result)
                    progress = progress or any(delta[k] for k in ('new_evidence', 'new_navigation', 'new_history'))
                    logger.info('深度 AI 资料进展 session=%s turn=%s round=%s tool=%s delta=%s evidence_chars=%s error=%s',
                                session_id, turn_id, round_no + 1, call.name, delta, kb.used_chars, result.get('error'))
                    result = {**result, 'progress': delta}
                    if rounds is not None:
                        result = {**result, 'remaining_rounds': max(0, rounds - round_no - 1)}
                    try:
                        ai_call_logger.log_call(provider=model.name, model=model.provider.chat_model, scene="conversation_history" if call.name in conversation_context.HISTORY_LABELS else "knowledge_tool", messages=[],
                                                response_text=json.dumps({"tool": call.name, "arguments": call.arguments, "result": result}, ensure_ascii=False),
                                                duration_ms=int((time.monotonic() - started) * 1000), success=not result.get("error"), **meta)
                    except Exception:
                        logger.warning("Unable to record knowledge tool log", exc_info=True)
                    results.append((call, evidence_state.compact_tool_result(result, delivered_evidence)))
                await bounded(asyncio.to_thread(model.results, results), total)
                round_no += 1
                stalled = 0 if progress else stalled + 1
                if stalled == 3:
                    stage('lookup_notice','查阅范围未增加',notes='近期调用未增加新资料，模型可选择其他来源、继续分析或直接回答')
                if repeated >= 5:
                    stop_reason = 'repeated_calls'
                    reason = "重复查阅未增加新资料，依据已有证据回答"
                    break
            else:
                stop_reason = 'round_limit'
                reason = "达到查阅轮次上限，依据已读资料回答并说明缺口"
            sources = await bounded(asyncio.to_thread(kb.sources), total)
            inventory = evidence_state.manifest(kb.hits)
            logger.info('深度 AI 查阅结束 session=%s turn=%s stop=%s rounds=%s calls=%s sources=%s evidence_chars=%s incomplete=%s',
                        session_id, turn_id, stop_reason, round_no, called, len(sources), kb.used_chars,
                        sum(not h['reading'].get('section_complete', False) for h in inventory))
            stage('evidence_summary', '整理已读资料', count=len(sources), status='partial' if stop_reason == 'model_error' else 'ok', notes=f'停止原因：{reason}；引用有效性不代表结论已核实',
                  stop_reason=stop_reason, tool_calls=called, evidence_chars=kb.used_chars,
                  reviewed_points=len(review.points), unresolved_points=len(review.snapshot()['unresolved']))
            queue.put_nowait({"type": "sources", "data": sources})
            requested_fields = field_search.identifiers(question)
            observed_fields = set(field_search.matches('\n'.join(h['text'] for h in kb.hits), requested_fields))
            missing_fields = [field for field in requested_fields if field not in observed_fields]
            instruction = f"现在正式回答当前问题：{question}\n{reason}。可引用编号为 1 至 {len(sources)}，必须使用 【cite:编号】 标记引用，不要使用普通方括号。列表每项独占一行，列表前空一行；数组下标用行内代码保留。"
            # Keep the complete native exchange: tool call/result pairs and model
            # observations are one conversation, not an unordered evidence dump.
            answer_system = ai_policy.prompt(strategy, 'answer')
            if context and context.background:
                answer_system += '\n' + context.background
            elif history:
                answer_system += '\n' + conversation_context.RULES
            window = conversation_context.config()['context_window_tokens']
            native_messages = getattr(model, 'messages', None)
            native_cost = conversation_context.estimate(answer_system + json.dumps(
                [native_messages, tools], ensure_ascii=False) + instruction)
            native = answer_streamed or (native_messages is not None and stop_reason != 'model_error' and native_cost + 3000 + 1024 <= window)
            model.system = answer_system
            final_tools = tools if native else []
            omitted = []
            if native:
                allowed = list(range(1, len(sources) + 1))
                context_mode = 'native'
                # anthropic 协议要求 input + max_tokens <= 窗口，按剩余空间钳制作答输出上限
                answer_tokens = min(tokens, max(1024, window - native_cost - 3000))
            else:
                # Never clip individual protocol items. Fall back only for a broken
                # exchange or capacity pressure, preserving explicit observations.
                observations = []
                for message in native_messages or []:
                    if message.get('role') != 'assistant':
                        continue
                    content = message.get('content', '')
                    if isinstance(content, list):
                        content = '\n'.join(b.get('text', '') for b in content
                                            if b.get('type') in ('text', 'output_text'))
                    if isinstance(content, str) and content.strip():
                        observations.append(content)
                handoff = '\n'.join(observations)[-6000:]
                base_cost = conversation_context.estimate(answer_system + json.dumps(messages, ensure_ascii=False) + instruction + handoff)
                # 压缩作答模式本身就是为窄窗口准备：作答输出至多预留 16k
                answer_tokens = min(tokens, 16384)
                packet = evidence_state.answer_payload(kb.hits, review, history_reads,
                    window - answer_tokens - base_cost - 3000, conversation_context.estimate)
                allowed = [entry['citation'] for entry in packet['evidence']]
                allowed.extend(alias['citation'] for entry in packet['evidence'] for alias in entry['same_text_citations'])
                omitted = packet['omitted_citations']
                model.messages = [*messages, {'role': 'user', 'content': '本轮作答资料（读取范围见 reading）：\n'
                                             + json.dumps(packet, ensure_ascii=False)}]
                if handoff:
                    model.messages.append({'role': 'user', 'content': '此前查阅阶段的初步分析（不是新增事实，仍以原文为准）：\n' + handoff})
                context_mode = 'compact_fallback'
            citations = CitationFilter(len(sources), allowed)
            citations.removed = live_removed
            logger.info('深度 AI 作答衔接 session=%s turn=%s context_mode=%s messages=%s citations=%s omitted=%s input_estimate=%s',
                        session_id, turn_id, context_mode, len(model.messages), len(allowed), omitted,
                        conversation_context.estimate(answer_system + json.dumps(model.messages, ensure_ascii=False)))
            stage('answer_context', '衔接查阅与作答', count=len(allowed),
                  status='partial' if omitted else 'ok', context_mode=context_mode,
                  notes='沿用完整查阅上下文和已有分析' if native else '因容量或查阅异常压缩资料，并保留已有分析')
            if omitted:
                state.update(outcome='partial', reason='作答上下文容量不足，部分已读原文未纳入本次回答')
            model.deadline = min(deadline, time.monotonic() + answer_seconds)
            logger.info('深度 AI 作答策略 session=%s turn=%s streaming=true post_verification=false strategy=%s prompt_version=%s context_mode=%s', session_id, turn_id, strategy, ai_policy.VERSION, context_mode)
            if requested_fields:
                logger.info('深度 AI 作答字段覆盖 session=%s turn=%s matched=%s missing=%s', session_id, turn_id, sorted(observed_fields), missing_fields)
            if stop_reason == 'model_error':
                await asyncio.to_thread(kb.check)
                notice = f"> {reason}。可稍后重试以继续完整查阅。\n\n"
                parts.append(notice)
                queue.put_nowait({'type': 'token', 'data': {'text': notice}})
            pending = []
            last_flush = time.monotonic()

            async def flush():
                nonlocal last_flush
                if pending:
                    await asyncio.to_thread(kb.check)
                    clean = citations.feed("".join(pending))
                    pending.clear()
                    if clean:
                        parts.append(clean)
                        queue.put_nowait({"type": "token", "data": {"text": clean}})
                    last_flush = time.monotonic()

            async def generate():
                async for token in model.final_stream(final_tools, instruction, max_tokens=answer_tokens):
                    check_cancel()
                    pending.append(token)
                    if len(pending) >= 32 or time.monotonic() - last_flush >= .1:
                        await flush()
                await flush()
                tail = citations.feed("", final=True)
                if tail:
                    parts.append(tail)
                    queue.put_nowait({"type": "token", "data": {"text": tail}})
            try:
                if not answer_streamed:
                    stage_running("agent_answer", "正在生成回答")
                    await bounded(generate(), max(1, model.deadline - time.monotonic()))
                    if getattr(model, "final_truncated", False):
                        state.update(outcome="partial", reason="模型输出达到输出上限，回答已截断，请联系管理员在系统设置的 AI 生成配置中调大输出上限")
                        notice = "\n\n> 本次回答因输出上限被截断，请联系管理员在系统设置的 AI 生成配置中调大输出上限后重试。"
                        parts.append(notice)
                        queue.put_nowait({"type": "token", "data": {"text": notice}})
            except (asyncio.TimeoutError, ToolProtocolError, httpx.HTTPError, RetryDeferredError) as exc:
                logger.warning("Agent answer incomplete: %s", type(exc).__name__)
                await asyncio.wait_for(flush(), 5)
                tail = citations.feed("", final=True)
                if tail:
                    parts.append(tail)
                state["verification"] = "partial"
                state["outcome"] = "partial"
                state["reason"] = "回答生成超时，已保留部分内容" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "回答生成中断，已保留可用内容"
                if isinstance(exc, (RetryDeferredError, ToolProtocolError)):
                    state["reason"] = str(exc)
                if not parts:
                    for i, source in enumerate(sources[:4], 1):
                        parts.append(f"**已找到的原文：{source['source_name']}** 【cite:{i}】\n\n{source['content'][:1000]}\n\n")
                parts.append("\n\n> 本次回答未完整生成，请结合引用原文核对后重试。")
            stage("agent_answer", "回答生成结束", status="ok" if state["outcome"] == "success" else "partial")
            # Allow only bounded final validation after the generation budget expires.
            await asyncio.wait_for(asyncio.to_thread(kb.validate_versions), 5)
            if not "".join(parts).strip():
                parts.append("本次模型未生成回答，请重试或使用增强 AI；若反复出现，请联系管理员在系统设置中调整 AI 生成配置（如调大输出上限）。")
                state.update(outcome="partial", verification="partial", reason="模型未生成回答")
            stage("citation_validation", "检查引用与文档版本", count=len(sources), status="partial" if citations.removed else "ok",
                  notes="已移除未实际读取的引用编号" if citations.removed else "引用编号有效，文档版本未变化；不代表已逐句核实模型结论")
            if citations.removed and state["verification"] == "sources_validated":
                state["verification"] = "citations_repaired"
            await asyncio.wait_for(asyncio.to_thread(kb.check), 5)
            stage("answer_result", "回答已完成" if state["outcome"] == "success" else "回答部分完成",
                  status="ok" if state["outcome"] == "success" else "partial", notes=state["reason"] or reason)
            terminal = {"type": "done", "data": {"answer": "".join(parts), "sources": sources, "trace": trace,
                        "used_provider": state["provider"], "used_chunks": len(sources), "mode": "deep_ai", "verification": state["verification"],
                        "outcome": state["outcome"], "reason": state["reason"]}}
        except asyncio.CancelledError:
            raise
        except (HTTPException, PipelineCancelled) as exc:
            if not stopped.is_set():
                terminal = {"type": "error", "data": {"message": getattr(exc, "detail", "请求已取消")}}
            return
        except asyncio.TimeoutError:
            # No asynchronous mutation may race with a partial answer after a local-tool timeout.
            terminal = {"type": "error", "data": {"message": "知识库查阅超时，请稍后重试；这不代表库内没有资料。"}}
            return
        except Exception as exc:
            import traceback
            from src.core.llm_diagnostics import redact
            secret = getattr(getattr(model, "provider", None), "_api_key", "")
            logger.error("Knowledge agent failed session=%s turn=%s\n%s", session_id, turn_id,
                         redact(traceback.format_exc(), secret, 12000))
            message = str(exc) if isinstance(exc, (ValueError, ToolProtocolError, RetryDeferredError)) else "深度 AI 请求失败，请检查 API 和知识库状态，或使用 AI 增强。"
            terminal = {"type": "error", "data": {"message": message}}
            return
        finally:
            if model:
                try:
                    await asyncio.wait_for(model.close(), 5)
                except Exception:
                    logger.warning("Agent transport cleanup failed", exc_info=True)
            # Publish terminal events after closing resources, so the consumer's
            # generator cleanup cannot cancel an in-progress transport close.
            if terminal:
                if terminal["type"] == "error":
                    stage("answer_result", "回答未完成", status="error", notes=terminal["data"]["message"])
                    terminal["data"].update(trace=trace, outcome="failed", partial="".join(parts))
                queue.put_nowait(terminal)

    task = asyncio.create_task(produce())
    try:
        while True:
            event = await queue.get()
            yield event
            if event["type"] in ("done", "error"):
                break
    finally:
        stopped.set()
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, PipelineCancelled):
            pass
