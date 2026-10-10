"""Aggregate build results without implying acceptance or publication."""
import argparse
import json
import os
from pathlib import Path


def summarize(revision, selected, results):
    platforms = {}
    for target in ('windows', 'macos', 'linux'):
        result = results.get(target, {})
        if target not in selected:
            status = 'not_selected'
        elif result.get('revision') != revision or result.get('platform') != target:
            status = 'missing_or_wrong_revision'
        else:
            status = result.get('result', 'missing')
        platforms[target] = {'build_status': status, 'acceptance_status': 'pending', 'published': False}
    return dict(revision=revision, platforms=platforms,
                all_selected_built=bool(selected) and all(
                    platforms.get(p, {}).get('build_status') == 'success' for p in selected),
                published=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    selected = [entry['platform'] for entry in json.loads(os.environ['SELECTED_MATRIX'])['include']]
    results = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in args.results.glob('*.json')}
    report = summarize(os.environ['SOURCE_SHA'], selected, results)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('Commit: `' + report['revision'] + '`\n\n| Platform | Build | Acceptance | Published |\n|---|---|---|---|\n')
            for target, result in report['platforms'].items():
                stream.write(f"| {target} | {result['build_status']} | pending | no |\n")
    if not report['all_selected_built']:
        raise SystemExit('Some selected platforms failed or have missing results')


if __name__ == '__main__':
    main()
