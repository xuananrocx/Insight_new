"""Translate explicit host paths for the short-lived, versioned deployment container."""
import json
import os
from pathlib import Path
import sys

import insightctl


def main():
    os.environ['INSIGHT_HOST_ROOT'] = '/host'
    path_options = {'--settings', '--state', '--bundle', '--backup', '--nginx-candidate'}
    argv = sys.argv[1:]
    for index, value in enumerate(argv):
        if value in path_options:
            if index + 1 >= len(argv) or not Path(argv[index + 1]).is_absolute():
                raise ValueError('Packaged host controller requires absolute host paths')
            argv[index + 1] = '/host' + argv[index + 1]
    if '--settings' not in argv:
        raise ValueError('--settings is required')
    settings = json.loads(Path(argv[argv.index('--settings') + 1]).read_text())
    if settings.get('deployment_mode') != 'host':
        raise ValueError('hostctl.sh only manages host deployment mode')
    sys.argv[1:] = argv
    insightctl.main()


if __name__ == '__main__':
    main()
