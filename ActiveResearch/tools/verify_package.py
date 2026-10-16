"""Static package checks only. Never import the scientific execution code."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path


def verify(root, *, require_history=False):
    root = Path(root).resolve()
    manifest = json.loads((root / 'metadata/source-manifest.json').read_text(encoding='utf-8'))
    main = manifest['files']
    history = manifest['archived_files']
    records = main + history
    history_present = (root / 'history').exists()
    checked_records = records if history_present else main
    initial = json.loads((root / 'metadata/initial-source-manifest.json').read_text(encoding='utf-8'))
    layout = json.loads((root / 'metadata/package-layout.json').read_text(encoding='utf-8'))
    errors = []
    if require_history and not history_present:
        errors.append('Local historical source verification requested, but history is absent')
    for rows, folder, count in ((main, 'src/active_research', manifest['packaged_scientific_python_files']),
                                (history, 'history/active_research', manifest['archived_scientific_python_files'])):
        expected = {row['path'] for row in rows}
        actual = {path.relative_to(root).as_posix() for path in (root / folder).rglob('*.py')}
        required_on_disk = folder.startswith('src/') or history_present
        if len(rows) != count or (required_on_disk and actual != expected):
            errors.append('Scientific file manifest does not match ' + folder)
    original_hashes = {row['source']: row['sha256'] for row in initial['files']}
    partition_hashes = {row['source']: row['sha256'] for row in records}
    if len(records) != len(initial['files']) or partition_hashes != original_hashes:
        errors.append('Main/history partition does not preserve the initial source inventory')
    syntax_passed = hashes_passed = 0
    for row in checked_records:
        rel = Path(row['path'])
        path = root / rel
        boundary = root / ('src/active_research' if row in main else 'history/active_research')
        if not path.resolve().is_relative_to(boundary) or path.is_symlink():
            errors.append('Source path escaped extension package: ' + row['path'])
            continue
        if not path.is_file():
            errors.append('Missing source: ' + row['path'])
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            errors.append('Source hash changed: ' + row['path'])
        else:
            hashes_passed += 1
        try:
            ast.parse(raw, filename=row['path'])
            compile(raw, row['path'], 'exec')  # Compile; do not execute/import.
            syntax_passed += 1
        except (SyntaxError, ValueError) as exc:
            errors.append('Syntax: ' + row['path'] + ': ' + type(exc).__name__)
        if not row['source'].startswith('Active-v3-frozen-control-work/active_research/'):
            errors.append('Source outside approved Active Research scope')
    forbidden_parts = {'jrdb_lh', '.git', '.experiment-host', 'private', 'runs', 'checkpoints', '.venv', 'node_modules'}
    forbidden_names = {'auth.json', 'credentials.json', '.env'}
    forbidden_suffixes = {'.sqlite', '.sqlite3', '.db', '.log'}
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if rel.parts[0] == '.git':
            continue  # A future repository checkout's own metadata is not publication content.
        if path.is_symlink() or any(part in forbidden_parts for part in rel.parts):
            errors.append('Forbidden package content: ' + rel.as_posix())
        if path.is_file() and (path.name in forbidden_names or path.suffix in forbidden_suffixes or path.name.endswith('.json.gz')):
            errors.append('Forbidden artifact: ' + rel.as_posix())
    deps = json.loads((root / 'metadata/dependency-map.json').read_text(encoding='utf-8'))
    spec = importlib.util.spec_from_file_location('package_dependency_scan', root / 'tools/dependency_scan.py')
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    fresh = scanner.scan(root / 'src')
    if fresh['internal_edges'] != deps['internal_edges'] or fresh['module_files'] != deps['module_files']:
        errors.append('Stored dependency map differs from fresh AST scan')
    if fresh['external_framework_modules'] != deps['external_framework_modules']:
        errors.append('External dependency declaration differs from fresh AST scan')
    if fresh['unresolved_internal_imports']:
        errors.append('Unresolved static internal imports')
    roots = layout['scientific_roots'] + layout['selected_test_roots'] + layout.get('external_test_support_roots', [])
    if not set(roots).issubset(fresh['module_files']):
        errors.append('Selected scientific/test entrypoint is missing')
    required = scanner.closure(fresh, roots)
    if required != set(fresh['module_files']) or required != set(layout['required_module_names']):
        errors.append('Main package is not exactly the selected conservative dependency closure')
    resources = json.loads((root / 'metadata/resource-manifest.json').read_text(encoding='utf-8'))['files']
    for resource in resources:
        path = root / resource['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != resource['sha256']:
            errors.append('Required non-Python resource missing or changed: ' + resource['path'])
    result = {'passed': not errors, 'scientific_files': len(records),
              'main_package_python_files': len(main), 'archived_python_files': len(history),
              'historical_source_bytes_checked': history_present,
              'archived_source_files_checked': len(history) if history_present else 0,
              'source_files_checked': len(checked_records),
              'source_hashes_passed': hashes_passed, 'syntax_compilations_passed': syntax_passed,
              'selected_test_roots': len(layout['selected_test_roots']),
              'external_test_support_roots': len(layout.get('external_test_support_roots', [])),
              'required_non_python_resources': len(resources),
              'runtime_dependency_closure_files': len(scanner.closure(fresh, layout['scientific_roots'])),
              'external_framework_modules_declared': len(deps['external_framework_modules']),
              'modeling_source_included': False if not any('jrdb_lh' in e for e in errors) else 'CHECK_ERRORS',
              'scientific_tests_executed': False, 'provider_calls': 0,
              'scientific_module_imports': 0, 'errors': errors}
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-history', action='store_true',
                        help='Also require all local archived source bytes (not distributed).')
    args = parser.parse_args()
    result = verify(Path(__file__).resolve().parents[1], require_history=args.require_history)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
