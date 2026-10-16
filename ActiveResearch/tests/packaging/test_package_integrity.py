"""Packaging checks; no framework, databases, providers or scientific imports."""
import hashlib
import importlib.util
import json
import tomllib
import unittest
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PackageIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / 'metadata/source-manifest.json').read_text(encoding='utf-8'))
        cls.deps = json.loads((ROOT / 'metadata/dependency-map.json').read_text(encoding='utf-8'))
        cls.layout = json.loads((ROOT / 'metadata/package-layout.json').read_text(encoding='utf-8'))

    def test_copied_scientific_source_matches_original_hashes(self):
        rows = self.manifest['files'] + (self.manifest['archived_files'] if (ROOT / 'history').exists() else [])
        for item in rows:
            with self.subTest(path=item['path']):
                self.assertEqual(hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest(), item['sha256'])

    def test_only_approved_source_namespace_was_copied(self):
        self.assertEqual(self.manifest['packaged_scientific_python_files'], 128)
        for item in self.manifest['files']:
            self.assertTrue(item['path'].startswith('src/active_research/'))
            self.assertTrue(item['source'].startswith('Active-v3-frozen-control-work/active_research/'))
        for item in self.manifest['archived_files']:
            self.assertTrue(item['path'].startswith('history/active_research/'))
            self.assertTrue(item['source'].startswith('Active-v3-frozen-control-work/active_research/'))

    def test_no_framework_is_vendored(self):
        self.assertFalse((ROOT / 'src/jrdb_lh').exists())
        self.assertFalse((ROOT / 'jrdb_lh').exists())
        self.assertTrue(self.deps['external_framework_modules'])
        self.assertTrue(all(k == 'jrdb_lh' or k.startswith('jrdb_lh.') for k in self.deps['external_framework_modules']))

    def test_effective_tests_and_required_helpers_remain_in_place(self):
        tests = [r for r in self.manifest['files'] if r['original_classification'] == 'tests']
        self.assertEqual(len(tests), 26)
        v3 = [r for r in tests if r['path'].startswith('src/active_research/finegrained_v3/tests/')]
        self.assertEqual(len(v3), 8)
        self.assertEqual(len(self.layout['selected_test_roots']), 16)
        self.assertIn('active_research.test_common_scoring', self.layout['selected_test_roots'])
        self.assertIn('active_research.policy_amortized.semantic_json.tests.test_view', self.layout['selected_test_roots'])

    def test_internal_static_import_closure_is_present(self):
        self.assertFalse(self.deps['unresolved_internal_imports'])
        self.assertTrue(self.deps['internal_edges'])

    def test_default_pytest_does_not_collect_scientific_tests(self):
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(pyproject['tool']['pytest']['ini_options']['testpaths'], ['tests/packaging'])
        self.assertNotIn('scripts', pyproject['project'])
        self.assertEqual(pyproject['tool']['setuptools']['packages']['find']['include'], ['active_research*'])
        self.assertEqual(pyproject['tool']['setuptools']['packages']['find']['where'], ['src'])

    def test_historical_partition_preserves_all_original_sources(self):
        initial = json.loads((ROOT / 'metadata/initial-source-manifest.json').read_text(encoding='utf-8'))
        rows = self.manifest['files'] + self.manifest['archived_files']
        self.assertEqual(len(rows), 155)
        self.assertEqual(len(self.manifest['archived_files']), 27)
        self.assertEqual({r['source']: r['sha256'] for r in rows},
                         {r['source']: r['sha256'] for r in initial['files']})

    def test_old_launchers_and_independent_workflows_are_outside_main_package(self):
        for relative in ('__main__.py', 'behavioral/__main__.py', 'context_v2/__init__.py',
                         'formal_worker.py', 'transport_recovery.py'):
            self.assertFalse((ROOT / 'src/active_research' / relative).is_file())
            if (ROOT / 'history').exists():
                self.assertTrue((ROOT / 'history/active_research' / relative).is_file())
            else:
                self.assertIn('history/active_research/' + relative,
                              {row['path'] for row in self.manifest['archived_files']})
        self.assertTrue((ROOT / 'src/active_research/finegrained_v3/__main__.py').is_file())

    def test_no_experiment_host_marker(self):
        self.assertFalse(list(ROOT.rglob('.experiment-host')))

    def test_static_verifier_succeeds_without_scientific_imports(self):
        spec = importlib.util.spec_from_file_location('package_static_verifier', ROOT / 'tools/verify_package.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.verify(ROOT)
        self.assertTrue(result['passed'], result['errors'])
        expected_checked = 155 if (ROOT / 'history').exists() else 128
        self.assertEqual(result['source_hashes_passed'], expected_checked)
        self.assertEqual(result['syntax_compilations_passed'], expected_checked)
        self.assertEqual(result['historical_source_bytes_checked'], (ROOT / 'history').exists())
        self.assertEqual(result['main_package_python_files'], 128)
        self.assertEqual(result['archived_python_files'], 27)
        self.assertEqual(result['runtime_dependency_closure_files'], 90)
        self.assertEqual(result['scientific_module_imports'], 0)

    def test_executable_regression_dependencies_were_restored_unchanged(self):
        for name in ('mock_agent.py', 'behavioral/__init__.py', 'behavioral/runner.py'):
            self.assertTrue((ROOT / 'src/active_research' / name).is_file())
        resource = ROOT / 'src/active_research/daily_reflection_budget.json'
        self.assertEqual(hashlib.sha256(resource.read_bytes()).hexdigest(),
                         '1ee97de1d8637b580e7cd6720c0a42ef4f03264d812dc9cb36135fae89904d42')
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        self.assertIn('daily_reflection_budget.json',
                      pyproject['tool']['setuptools']['package-data']['active_research'])

    def test_distributed_subset_does_not_require_excluded_history(self):
        spec = importlib.util.spec_from_file_location('publication_verifier', ROOT / 'tools/verify_package.py')
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory(prefix='.packaging-test-', dir=ROOT) as folder:
            target = Path(folder)
            self.assertTrue(target.resolve().is_relative_to(ROOT.resolve()))
            for name in ('src', 'metadata', 'tools'):
                shutil.copytree(ROOT / name, target / name)
            (target / '.git').mkdir()  # A normal repository checkout is permitted.
            result = verifier.verify(target)
            self.assertTrue(result['passed'], result['errors'])
            self.assertEqual(result['source_hashes_passed'], 128)
            self.assertFalse(result['historical_source_bytes_checked'])
            self.assertFalse(verifier.verify(target, require_history=True)['passed'])
            (target / 'history').mkdir()
            self.assertFalse(verifier.verify(target)['passed'])  # Partial local archive must fail.

    def test_publication_file_list_excludes_local_assets(self):
        path = ROOT / 'metadata/UPLOAD-FILES.txt'
        listed = path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(listed, sorted(set(listed)))
        self.assertIn('metadata/UPLOAD-FILES.txt', listed)
        self.assertIn('metadata/release-validation.json', listed)
        self.assertNotIn('metadata/validation.json', listed)
        for name in listed:
            with self.subTest(path=name):
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn('..', Path(name).parts)
                self.assertNotIn('history', Path(name).parts)
                self.assertNotIn('jrdb_lh', Path(name).parts)
                self.assertTrue((ROOT / name).is_file())
                self.assertFalse((ROOT / name).is_symlink())


if __name__ == '__main__':
    unittest.main()
