"""The agent-facing contract is frozen.

V061 pins the current contract. V060 holds the digests of the v0.6.0 runtime that produced the
paper's main cohort: reverting the documented corrections must reproduce it exactly, which proves
that nothing else an agent reads has changed.
"""

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import jockeybench
import jockeybench.protocol.specification as specification
from jockeybench import PROTOCOL_VERSION
from jockeybench.agents import compatible
from jockeybench.canonical import digest
from jockeybench.config import Config
from jockeybench.errors import BudgetLimit, safe_error
from jockeybench.protocol.delivery import SAVE_WARNING
from jockeybench.protocol.revisions import CORRECTIONS_0_6_1
from jockeybench.protocol.tools import DATA_TOOLS, TOOL_ARGS, tool_specs

ENVIRONMENT = {
    'hash_seed': 0,
    'imports': {'numpy': True, 'pandas': False, 'scipy': True, 'sklearn': True, 'statsmodels': False},
    'isolation': 'no network, no subprocess, workspace-only file access',
    'numerical_threads': 4,
    'python_version': '3.13.2',
    'runtime_exception': 'Read-only interpreter and installed libraries are available.',
    'system': 'Darwin',
    'system_release': 'test',
}
V060 = {
    'tool_specs_8': 'd4ecc12ab77c178cda52052e9b60e460085a8778d6fb73a46b95a23e61b2d9f4',
    'tool_specs_2': '7322d5b5bcbd946cd1aa8c58024609e370a22972be552b973845a32f48f4fa5d',
    'system_message': '37ec599ead1ae3b25b72059ef7f5a3881a14e67a551d688d2eb509b4335c2db1',
}
V061 = {
    'tool_specs_8': 'fee1a8b649df65693962f4727d392b5d982967b69699bae5f126575b6f3dfd2e',
    'tool_specs_2': 'fce12ed58f7cd450629b3a4a384bf384810cbcef5b3539749df193d13f5188c7',
    'system_message': '8914a2b5b532bab86cd6a34ee1887d591ccb119e2e95ecab58e205a5b8ee4838',
}
V060_REPAIR_TASK = 'Repair the candidate program with remaining session resources, or choose uniform.'
V060_WARMUP_TASK = 'Initial exploration. Build an initial strategy from scratch and save it. No predictions yet.'
V060_REFLECT_TASK = 'Day complete. You may change strategy or leave it unchanged. No new predictions until next batch.'
V060_TRIM_NOTICE = (
    ' Earlier completed tool rounds may be removed to stay within context limits. Retrieve needed facts again '
    'or use your saved strategy notes; no automatic learned summary is supplied.'
)
# Digest of the protocol error messages (the text an agent can receive as an error); see error_messages().
V061_ERROR_MESSAGES = '76929d7b9bbf8483020e6ee10a24ee79b4f189b8a60356fe81122f33d1a7bcd2'
PROTOCOL_ERRORS = {'Rejected', 'BudgetExceeded', 'BudgetLimit', 'JobFailed', 'Pending', 'ProviderCostLimit'}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def revert(value):
    """The 0.6.0 wording of a 0.6.1 string (or of every string in a JSON value)."""
    if isinstance(value, dict):
        return {k: revert(v) for k, v in value.items()}
    if isinstance(value, list):
        return [revert(v) for v in value]
    if isinstance(value, str):
        for old, new in CORRECTIONS_0_6_1:
            value = value.replace(new, old)
    return value


def corpus(service):
    """Everything an agent can read: the rendered contract plus every string constant of the runtime
    (implicitly concatenated literals are one constant, exactly as the model receives them)."""
    package = Path(jockeybench.__file__).parent
    literals = [
        node.value
        for path in sorted(package.rglob('*.py'))
        if path.name != 'revisions.py'
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    return '\n'.join([specification.system_message(service), json.dumps(tool_specs(), ensure_ascii=False), *literals])


@pytest.fixture
def paper_service(monkeypatch):
    monkeypatch.setattr(specification, 'environment', lambda service: ENVIRONMENT)
    return SimpleNamespace(config=Config(), state={})


def test_tool_specifications_are_pinned_and_differ_from_v060_only_by_the_corrections():
    for size in (8, 2):
        assert digest(tool_specs(size)) == V061[f'tool_specs_{size}']
        assert digest(revert(tool_specs(size))) == V060[f'tool_specs_{size}']


def test_system_message_is_pinned_and_differs_from_v060_only_by_the_corrections(paper_service):
    message = specification.system_message(paper_service)
    assert sha(message) == V061['system_message']
    assert sha(revert(message)) == V060['system_message']


def test_session_texts_differ_from_v060_only_by_the_corrections():
    assert revert(compatible.REPAIR_TASK) == V060_REPAIR_TASK
    assert compatible.WARMUP_TASK == V060_WARMUP_TASK
    assert compatible.REFLECT_TASK == V060_REFLECT_TASK
    assert compatible.TRIM_NOTICE == V060_TRIM_NOTICE


def error_messages():
    """The message of every ``raise <protocol error>(...)`` in the runtime, plus the messages built at runtime.

    A literal or a module-level string constant is pinned as its text; an expression built at run time
    (an f-string, a variable) as its source. Messages returned inside tool responses are not raised and
    are covered by the fingerprint comparison instead (docs/REPRODUCING.md).
    """
    package = Path(jockeybench.__file__).parent
    messages = []
    for path in sorted(package.rglob('*.py')):
        tree = ast.parse(path.read_text())
        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                name = getattr(node.exc.func, 'id', getattr(node.exc.func, 'attr', None))
                if name in PROTOCOL_ERRORS:
                    arg = node.exc.args[0] if node.exc.args else ast.Constant('')
                    messages.append(repr(constants[arg.id]) if isinstance(arg, ast.Name) and arg.id in constants else ast.unparse(arg))
    built = [str(BudgetLimit('cap', 'used', 'limit', 'requested')), safe_error(ValueError())['message'], SAVE_WARNING]
    return sorted(messages) + built


def test_protocol_error_messages_are_pinned():
    # 1.1.0 extends one host-only startup diagnostic to Linux. All messages
    # delivered through the scientific protocol remain byte-for-byte pinned.
    old = repr('Full execution requires macOS sandbox-exec; no isolation fallback is provided')
    new = repr('Full execution requires macOS sandbox-exec or Linux bubblewrap; no isolation fallback is provided')
    messages = error_messages()
    assert messages.count(new) == 1 and old not in messages
    assert digest(sorted(old if text == new else text for text in messages[:-3]) + messages[-3:]) == V061_ERROR_MESSAGES


def test_every_correction_is_applied_and_leaves_no_old_wording(paper_service):
    text = corpus(paper_service)
    for old, new in CORRECTIONS_0_6_1:
        assert new in text, new
        assert old not in text.replace(new, ''), old
    corrected = [new for _, new in CORRECTIONS_0_6_1]
    assert not [a for a in corrected for b in corrected if a != b and a in b], 'a correction must not contain another'


def test_probabilities_are_described_as_required_everywhere(paper_service):
    text = corpus(paper_service)
    assert 'optional probabilities' not in text and 'When supplied' not in text
    assert 'independent of submitted probabilities' not in text
    assert 'ranking and probabilities, both required' in text


def test_tool_list_order_and_offered_tools():
    names = [s['function']['name'] for s in tool_specs()]
    assert names == [n for n in TOOL_ARGS if n != 'submit']
    assert DATA_TOOLS < set(names)


def test_public_limits_hide_machine_ceilings_and_keep_the_inherited_ones():
    limits = specification.public_limits(Config())
    assert {'rss_bytes', 'job_seconds', 'storage_bytes', 'numerical_threads'}.isdisjoint(limits)
    assert limits['max_page_size'] == 8 and limits['format_repairs'] == 1 and limits['feedback_memory_items'] == 32


def test_describe_reports_the_protocol_version_and_every_tool(paper_service, source):
    paper_service.source = source
    described = specification.describe(paper_service)
    assert described['harness_version'] == PROTOCOL_VERSION == '0.6.1'
    assert set(described['tools']) == {s['function']['name'] for s in tool_specs()}
    assert json.dumps(described['sections']) == json.dumps(specification.sections(paper_service))
