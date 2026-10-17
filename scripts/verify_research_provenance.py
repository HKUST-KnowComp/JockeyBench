"""Verify Research migration records without importing scientific code or opening data."""

import argparse
import ast
import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path('provenance/active-research/metadata')
RESEARCH = Path('src/jockeybench/research')
OLD_PREFIX = 'src/active_research/'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def contained(root, relative):
    rel = Path(relative)
    path = root / rel
    if rel.is_absolute() or '..' in rel.parts or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe manifest path: ' + str(relative))
    return path


def evidence_errors(root):
    errors = []
    for row in read(root / EVIDENCE / 'source-drop-evidence.json')['files']:
        path = contained(root, row['path'])
        if not path.is_file() or sha(path) != row['sha256']:
            errors.append('Original evidence changed: ' + row['path'])
    return errors


def scan(root):
    """Conservative static imports, including deferred branches and parent initializers."""
    source = root / 'src'
    modules = {}
    packages = set()
    for path in sorted((source / 'jockeybench').rglob('*.py')):
        bits = list(path.relative_to(source).with_suffix('').parts)
        if bits[-1] == '__init__':
            bits.pop()
        name = '.'.join(bits)
        modules[name] = path
        for index in range(1, len(bits)):
            packages.add('.'.join(bits[:index]))
        if path.name == '__init__.py':
            packages.add(name)
    edges = defaultdict(set)
    unresolved = []
    legacy = []
    dynamic = []
    for name, path in modules.items():
        package = name if path.name == '__init__.py' else name.rsplit('.', 1)[0]
        raw = path.read_bytes()
        tree = ast.parse(raw, filename=str(path))
        compile(raw, str(path), 'exec')
        for node in ast.walk(tree):
            bases = []
            candidates = []
            if isinstance(node, ast.Import):
                bases = [alias.name for alias in node.names]
                candidates = bases
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split('.')
                    base = '.'.join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))
                else:
                    base = node.module or ''
                bases = [base]
                candidates = bases + [base + '.' + alias.name for alias in node.names if alias.name != '*']
            for base in bases:
                if base == 'jockeybench' or base.startswith('jockeybench.'):
                    if base not in modules and base not in packages:
                        unresolved.append({'module': name, 'line': node.lineno, 'target': base})
                if base in {'active_research', 'jrdb_lh'} or base.startswith(('active_research.', 'jrdb_lh.')):
                    legacy.append({'module': name, 'line': node.lineno, 'target': base})
            edges[name].update(target for target in candidates if target in modules)
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {
                '__import__',
                'importlib.import_module',
                'importlib.util.spec_from_file_location',
            }:
                dynamic.append(
                    {
                        'module': name,
                        'line': node.lineno,
                        'call': ast.unparse(node.func),
                        'literal_args': [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)],
                    }
                )
        for index in range(1, len(name.split('.'))):
            parent = '.'.join(name.split('.')[:index])
            if parent in modules:
                edges[name].add(parent)
    return {
        'method': 'AST imports and parent initializers only; no scientific execution or behavioral equivalence claim',
        'module_files': {name: path.relative_to(root).as_posix() for name, path in modules.items()},
        'internal_edges': sorted([left, right] for left, targets in edges.items() for right in targets),
        'unresolved_internal_imports': unresolved,
        'legacy_namespace_imports': legacy,
        'dynamic_import_sites': dynamic,
    }


def closure(graph, roots):
    edges = defaultdict(set)
    for left, right in graph['internal_edges']:
        edges[left].add(right)
    seen = set(roots)
    pending = list(seen)
    while pending:
        for target in edges[pending.pop()] - seen:
            seen.add(target)
            pending.append(target)
    return seen


def migration_records(root):
    original = read(root / EVIDENCE / 'source-manifest.json')
    rows = []
    mapped = set()
    for source in original['files']:
        old = source['path']
        if not old.startswith(OLD_PREFIX):
            raise ValueError('Unexpected original source path: ' + old)
        new = (RESEARCH / old.removeprefix(OLD_PREFIX)).as_posix()
        current = sha(contained(root, new))
        mapped.add(new)
        rows.append(
            {
                'old_path': 'ActiveResearch/' + old,
                'new_path': new,
                'original_source': source['source'],
                'original_sha256': source['sha256'],
                'current_sha256': current,
                'changed': current != source['sha256'],
            }
        )
    additions = [
        {'path': p.relative_to(root).as_posix(), 'sha256': sha(p)}
        for p in sorted((root / RESEARCH).rglob('*.py'))
        if p.relative_to(root).as_posix() not in mapped
    ]
    resources = []
    for source in read(root / EVIDENCE / 'resource-manifest.json')['files']:
        new = (RESEARCH / source['path'].removeprefix(OLD_PREFIX)).as_posix()
        resources.append(
            {
                'old_path': 'ActiveResearch/' + source['path'],
                'new_path': new,
                'original_sha256': source['sha256'],
                'current_sha256': sha(contained(root, new)),
            }
        )
    return {
        'schema': 'jockeybench-research-migration-v1',
        'scope': 'Current unified package; historical hashes and validation claims remain separate',
        'original_published_python_files': len(rows),
        'excluded_historical_python_files': original['archived_scientific_python_files'],
        'migrated_files': rows,
        'new_files': additions,
        'resources': resources,
    }


def record(root):
    errors = evidence_errors(root)
    if errors:
        raise ValueError('; '.join(errors))
    manifest = migration_records(root)
    graph = scan(root)
    for name, value in [('migration-manifest.json', manifest), ('current-dependency-map.json', graph)]:
        (root / EVIDENCE / name).write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def verify(root=ROOT):
    root = Path(root).resolve()
    errors = evidence_errors(root)
    original = read(root / EVIDENCE / 'source-manifest.json')
    initial = read(root / EVIDENCE / 'initial-source-manifest.json')
    original_rows = original['files'] + original['archived_files']
    if {r['source']: r['sha256'] for r in original_rows} != {r['source']: r['sha256'] for r in initial['files']}:
        errors.append('Original main/history partition no longer matches its initial inventory')
    manifest = read(root / EVIDENCE / 'migration-manifest.json')
    fresh = migration_records(root)
    if manifest != fresh:
        errors.append('Current source, file coverage or original/current mapping differs from migration manifest')
    graph = scan(root)
    if graph != read(root / EVIDENCE / 'current-dependency-map.json'):
        errors.append('Current static dependency map changed')
    if graph['unresolved_internal_imports']:
        errors.append('Unresolved internal imports: ' + json.dumps(graph['unresolved_internal_imports']))
    if graph['legacy_namespace_imports']:
        errors.append('Old framework namespace imports remain: ' + json.dumps(graph['legacy_namespace_imports']))
    layout = read(root / EVIDENCE / 'package-layout.json')
    roots = layout['scientific_roots'] + layout['selected_test_roots'] + layout.get('external_test_support_roots', [])
    missing = [name for name in roots if name.replace('active_research', 'jockeybench.research', 1) not in graph['module_files']]
    if missing:
        errors.append('Migrated entrypoint or retained test root missing: ' + ', '.join(missing))
    public_roots = ['jockeybench.cli', 'jockeybench.research.cli', 'jockeybench.research.public']
    if not set(public_roots) <= set(graph['module_files']):
        errors.append('Public unified setting entrypoint missing')
    public_closure = closure(graph, public_roots)
    if (root / 'provenance/active-research/pyproject.toml').exists():
        errors.append('Obsolete independent Research package declaration remains')
    for namespace in ('active_research', 'jrdb_lh'):
        if (root / 'src' / namespace).exists():
            errors.append('Obsolete top-level namespace: ' + namespace)
    for path in (root / RESEARCH).rglob('*'):
        if path.is_symlink():
            errors.append('Symlink in Research source: ' + path.relative_to(root).as_posix())
        if path.suffix in {'.sqlite', '.sqlite3', '.db', '.log'} or path.name in {
            '.experiment-host',
            '.env',
            'credentials.json',
            'auth.json',
        }:
            errors.append('Private artifact in Research source: ' + path.relative_to(root).as_posix())
    return {
        'passed': not errors,
        'migrated_python_files': len(fresh['migrated_files']),
        'unchanged_original_files': sum(not row['changed'] for row in fresh['migrated_files']),
        'changed_original_files': sum(row['changed'] for row in fresh['migrated_files']),
        'new_python_files': len(fresh['new_files']),
        'excluded_historical_files': fresh['excluded_historical_python_files'],
        'static_unified_modules': len(graph['module_files']),
        'public_static_dependency_closure_modules': len(public_closure),
        'scientific_module_imports': 0,
        'provider_calls': 0,
        'scientific_tests_executed': False,
        'errors': errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-migration', action='store_true', help='Record reviewed current hashes; never change original evidence.')
    args = parser.parse_args(argv)
    try:
        if args.record_migration:
            record(ROOT)
        result = verify(ROOT)
    except (OSError, KeyError, ValueError, SyntaxError) as exc:
        result = {'passed': False, 'errors': [str(exc)]}
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
