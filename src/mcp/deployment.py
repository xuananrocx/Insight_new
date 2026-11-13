"""Deployment readiness, separate from the user-editable MCP business switch."""
import os
import time

import httpx

from src import __version__


async def status():
    result = {'state': 'not_configured', 'gateway_version': None,
              'application_version': __version__, 'checked_at': int(time.time())}
    # Only a deployment-controlled address is probed. Never fetch a user-supplied
    # public_url, follow redirects, or send cookies/tokens to the health endpoint.
    address = os.environ.get('INSIGHT_MCP_HEALTH_URL')
    if not address:
        return result
    result['state'] = 'unavailable'
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = await client.get(address)
            value = response.json()
        if response.is_success and value.get('component') == 'insight-mcp' and value.get('status') == 'ok':
            version = value.get('version')
            if isinstance(version, str) and len(version) <= 80:
                result.update(state='ready' if version == __version__ else 'version_mismatch', gateway_version=version)
    except (httpx.RequestError, ValueError, AttributeError):
        pass
    return result
