"""Read Python imports as AST data; never import the scientific package."""
import ast
from collections import defaultdict
from pathlib import Path


def scan(source_root):
    source_root = Path(source_root).resolve()
    modules = {}
    packages = set()
    for path in sorted((source_root / 'active_research').rglob('*.py')):
        bits = list(path.relative_to(source_root).with_suffix('').parts)
        if bits[-1] == '__init__':
            bits.pop()
        name = '.'.join(bits)
        modules[name] = path
        for index in range(1, len(bits)):
            packages.add('.'.join(bits[:index]))
        if path.name == '__init__.py':
            packages.add(name)
    edges = defaultdict(set)
    external = defaultdict(set)
    unresolved = []
    dynamic = []
    for name, path in modules.items():
        package = name if path.name == '__init__.py' else name.rsplit('.', 1)[0]
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [item.name for item in node.names]
                for target in targets:
                    if target.startswith('active_research') and target not in modules and target not in packages:
                        unresolved.append({'module': name, 'line': node.lineno, 'target': target})
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    bits = package.split('.')
                    base = '.'.join(bits[:len(bits) - node.level + 1] + ([node.module] if node.module else []))
                else:
                    base = node.module or ''
                targets = [base] + [base + '.' + item.name for item in node.names if item.name != '*']
                if base.startswith('active_research') and base not in modules and base not in packages:
                    unresolved.append({'module': name, 'line': node.lineno, 'target': base})
            for target in targets:
                if target in modules:
                    edges[name].add(target)
                if target == 'jrdb_lh' or target.startswith('jrdb_lh.'):
                    # Record the imported module, not every imported symbol.
                    base = node.module if isinstance(node, ast.ImportFrom) else target
                    external[base].add(name)
            if isinstance(node, ast.Call):
                function = ast.unparse(node.func)
                if function in ('__import__', 'importlib.import_module', 'importlib.util.spec_from_file_location'):
                    dynamic.append({'module': name, 'line': node.lineno, 'call': function,
                                    'literal_args': [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]})
        # Python loads parent package initializers even for a direct submodule import.
        for index in range(1, len(name.split('.'))):
            parent = '.'.join(name.split('.')[:index])
            if parent in modules:
                edges[name].add(parent)
    return {
        'method': 'AST imports including deferred branches and parent package initializers; no execution',
        'module_files': {name: path.relative_to(source_root).as_posix() for name, path in modules.items()},
        'internal_edges': sorted([left, right] for left, rights in edges.items() for right in rights),
        'external_framework_modules': {name: sorted(callers) for name, callers in sorted(external.items())},
        'unresolved_internal_imports': unresolved,
        'dynamic_import_sites': dynamic,
        'limits': ['External framework implementation not inspected',
                   'Import closure is conservative, not runtime branch coverage',
                   'Non-Python fixtures and old subprocess entrypoints require separate review']
    }


def closure(mapping, seeds):
    edges = defaultdict(set)
    for left, right in mapping['internal_edges']:
        edges[left].add(right)
    seen = set(seeds)
    pending = list(seeds)
    while pending:
        for target in edges[pending.pop()]:
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen
