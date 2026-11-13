"""Opt-in real, offline reranker calibration on synthetic relevance judgments."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

CASES = [
    ('cross_language', '固件烧录失败怎么排查', 'If firmware flashing fails, verify the firmware image checksum, device permissions, cable connection and power supply.', True),
    ('financial_noise', '固件烧录失败怎么排查', 'SecurityID 123456; Symbol Bond A; Price 99.75; Currency CNY; Quantity 1000000.', False),
    ('file_boilerplate', '固件烧录失败怎么排查', 'function logPrint() { echo "$1"; }; parse_json() { return 0; }', False),
    ('finance', '债券面值和交易币种是什么', 'This bond has a par value of 100 and is denominated in CNY.', True),
    ('finance_negative', '债券面值和交易币种是什么', 'Upgrade the device firmware, then reboot the card.', False),
    ('no_answer', '报销审批需要几个人签字', 'A bond has a par value of 100 and is denominated in CNY.', False),
    ('short_fact', '服务监听哪个端口', 'The service listens on TCP port 8080.', True),
    ('code', '超时如何重试', 'for attempt in range(3):\n try: request(timeout=5)\n except TimeoutError: continue', True),
    ('negation', '这个功能支持离线使用吗', 'This feature does not support offline use; an internet connection is required.', True),
    ('version_conflict', 'v2 的默认超时是多少', 'Version v1 uses 10 seconds. In v2 the default timeout was changed to 30 seconds.', True),
    ('table_fact', '北京仓库的库存数量', 'Warehouse | Stock\nBeijing | 23\nShanghai | 41', True),
    ('punctuation', 'E_TIMEOUT 是什么错误', 'E_TIMEOUT: the request deadline elapsed before the server replied.', True),
]


_FILLER = 'The following lines describe generic service initialization and ordinary operational metadata. '*24
CASES.extend([
    ('long_tail', '服务监听哪个端口', _FILLER+'\nThe service listens on TCP port 8080.', True),
    ('long_head', '服务监听哪个端口', 'The service listens on TCP port 8080.\n'+_FILLER, True),
])


def main():
    with tempfile.TemporaryDirectory(prefix='mcp-relevance-') as root:
        os.environ.update(AMD_DATA_DIR=root, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', TOKENIZERS_PARALLELISM='false')
        (Path(root)/'data').mkdir()
        from src.mcp import relevance
        import time
        rows = []
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')) as network:
            for name, query, text, relevant in CASES:
                started = time.monotonic()
                value = relevance.score(query, [text])[0]
                rows.append({'case': name, 'expected_relevant': relevant, 'score': round(value, 6),
                             'ms': round((time.monotonic()-started)*1000, 2)})
            assert network.call_count == 0
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        assert all((r['score'] >= .10) == r['expected_relevant'] for r in rows), 'Relevance regression'


if __name__ == '__main__':
    main()
