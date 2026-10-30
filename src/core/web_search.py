"""Provider-executed web search. This module never fetches a website itself."""
from urllib.parse import urlsplit


def source(value, cited=False):
    url = value.get('url', '')
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return {'url': url, 'title': str(value.get('title') or parsed.hostname)[:300], 'cited': cited}


def public_sources(values):
    return [s for v in values[:100] if isinstance(v, dict) and (s := source(v, bool(v.get('cited'))))]


class WebEvents:
    def __init__(self, callback):
        self.callback = callback
        self.sources = {}
        self.calls = {}
        self.emitted = {}

    def emit(self, ident, status, label='搜索网页', notes=None):
        event = {'stage': 'web_search_' + str(ident)[:100], 'label': label,
                       'status': status, 'notes': notes,
                       'web_sources': list(self.sources.values())[-100:]}
        if self.emitted.get(event['stage']) != event:
            self.emitted[event['stage']] = event
            self.callback(event)

    def add(self, value, cited=False):
        item = source(value, cited)
        if item:
            previous = self.sources.get(item['url'], {})
            item['cited'] = item['cited'] or previous.get('cited', False)
            self.sources[item['url']] = item

    def item(self, item):
        kind = item.get('type')
        if kind == 'web_search_call':
            for value in item.get('action', {}).get('sources', []):
                if isinstance(value, dict):
                    self.add(value)
            label = '阅读网页' if item.get('action', {}).get('type') == 'open_page' else '搜索网页'
            status = item.get('status')
            self.emit(item.get('id', 'search'), 'ok' if status == 'completed' else 'failed' if status == 'failed' else 'running', label)
        elif kind == 'server_tool_use' and item.get('name') == 'web_search':
            self.calls[item.get('id')] = True
            self.emit(item.get('id'), 'running')
        elif kind == 'web_search_tool_result':
            content = item.get('content', [])
            if isinstance(content, dict):
                self.emit(item.get('tool_use_id'), 'failed', notes='上游搜索未完成：' + str(content.get('error_code', 'unknown'))[:100])
            else:
                for value in content:
                    if isinstance(value, dict) and value.get('type') == 'web_search_result':
                        self.add(value)
                self.emit(item.get('tool_use_id'), 'ok' if content else 'empty')
        elif kind in ('text', 'output_text'):
            for value in item.get('citations', []) + item.get('annotations', []):
                if value.get('type') in ('web_search_result_location', 'url_citation'):
                    self.add(value, True)
        elif kind == 'message':
            for value in item.get('content', []):
                self.item(value)

    def feed(self, event):
        kind = event.get('type', '')
        if kind == 'content_block_start':
            self.item(event.get('content_block', {}))
        elif kind == 'content_block_delta' and event.get('delta', {}).get('type') == 'citations_delta':
            self.add(event['delta'].get('citation', {}), True)
        elif kind in ('response.output_item.added', 'response.output_item.done'):
            self.item(event.get('item', {}))
        elif kind == 'response.output_text.annotation.added':
            self.add(event.get('annotation', {}), True)
        elif kind == 'response.completed':
            for item in event.get('response', {}).get('output', []):
                self.item(item)
        if self.sources and kind in ('message_stop', 'response.completed'):
            self.emit('sources', 'ok', '网页来源')


async def probe(mode):
    """Explicit model test can run while global search is off; never enables it."""
    import time
    from src.core.tool_chat import create_tool_chat
    chat = create_tool_chat('Use the web search tool to verify a public fact. Do not answer from memory.',
                            [{'role': 'user', 'content': 'Search the official Python website for its current stable release; cite the source.'}])
    chat.web_mode = mode
    if mode == 'responses':
        chat.style = 'responses'
    chat.log_scene = "test"
    chat.retry_count = 0
    chat.deadline = time.monotonic() + 90
    events = []
    chat.on_web_event = events.append
    try:
        for _ in range(3):
            turn = None
            async for kind, value in chat.stream_turn([], max_tokens=2048):
                if kind == 'turn':
                    turn = value
            if not turn or not turn.paused:
                break
        sources = list(chat.web_events.sources.values())
        completed = any(e['status'] == 'ok' and e['stage'] != 'web_search_sources' for e in events)
        supported = completed and bool(sources)
        return {'supported': supported, 'message': '已执行服务端搜索并返回网页来源' if supported else '未确认完整搜索结果，请检查模型支持情况或稍后重试', 'sources': sources[:10]}
    finally:
        await chat.close()
