"""Official SDK -> gateway -> isolated review backend -> real reading index.

Start tests.mcp_review_server first. Run this module with the MCP SDK venv.
"""
import asyncio
import json
from pathlib import Path
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from src.mcp.gateway import create_app


async def run(info_path):
    info = json.loads(Path(info_path).read_text())
    app = create_app(backend_url='http://127.0.0.1:18000')
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, headers={'Authorization': 'Bearer '+info['token']}) as http:
            async with streamable_http_client('http://127.0.0.1:18001/mcp', http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert len(listed.tools) == 11
                    async def call(name, **arguments):
                        result = await session.call_tool(name, arguments)
                        assert not result.isError, result
                        return result.structuredContent
                    kbs = await call('list_knowledge_bases')
                    assert kbs['knowledge_bases'][0]['kb_id'] == info['kb_id']
                    overview = await call('get_kb_overview', kb_id=info['kb_id'])
                    assert overview['kb_id'] == info['kb_id']
                    documents = await call('list_documents', kb_id=info['kb_id'], query='guide')
                    assert documents['total'] == 1
                    for name in ('search_knowledge', 'search_document'):
                        args = {'document_id': info['document_id']} if name == 'search_document' else {}
                        found = await call(name, kb_id=info['kb_id'], query='visible_levels', match_mode='exact', limit=1, **args)
                        assert found['evidence'] and 'visible_levels' in found['evidence'][0]['text']
                    outline = await call('get_document_outline', kb_id=info['kb_id'], document_id=info['document_id'], query='Quote')
                    obj = outline['objects'][0]
                    read = await call('read_document', kb_id=info['kb_id'], read_ref=obj['read_ref'])
                    assert 'visible_levels' in read['evidence'][0]['text']
                    result = await call('search_scope', kb_id=info['kb_id'], scope_ref=obj['scope_ref'], query='field_', match_mode='exact', limit=1)
                    next_page = await call('continue_search', kb_id=info['kb_id'], cursor=result['next_cursor'])
                    assert next_page['evidence'] != result['evidence']
                    members = await call('get_object_members', kb_id=info['kb_id'], scope_ref=obj['scope_ref'])
                    assert members['next_offset']
    print('PASS: official SDK -> real HTTP backend -> 10 tools: authorized KB overview/catalog/exact search/outline/read/continuation/members; no chat API')


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
