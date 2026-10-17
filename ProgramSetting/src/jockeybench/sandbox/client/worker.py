"""Minimal untrusted worker. Its directory is the only framework code it can read."""

import contextlib
import io
import json
import os
import re
import sys
import traceback

from sdk import SDK, ToolError, misuse_events


def sanitised_message(exc):
    message = str(exc)
    for path in sorted({os.getcwd(), os.path.expanduser('~')}, key=len, reverse=True):
        if path and path != '/':
            message = message.replace(path, '<path>')
    # Absolute tokens only; POSIX needs two segments so division stays readable.
    message = re.sub(
        r"(?<![\w./\\])(?:[A-Za-z]:[\\/][^\s'\"<>]*|"
        r"\\\\[^\\\s'\"<>]+\\[^\s'\"<>]+|/[^/\s'\"<>]+(?:/[^/\s'\"<>]+)+/?)",
        '<path>',
        message,
    )
    return message[:300]


class Output(io.StringIO):
    def write(self, s):
        if self.tell() + len(s) > 65536:
            raise RuntimeError('Output limit exceeded')
        return super().write(s)


def main():
    payload = json.loads(sys.stdin.readline(1024 * 1024 + 1))
    sink = Output()
    env = {'sdk': SDK(), '__name__': '__agent__'}
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            exec(compile(payload['code'], '<agent>', 'exec'), env)
        reply = {'result': env.get('result'), 'stdout': sink.getvalue(), 'sdk_misuse': misuse_events()}
        raw = json.dumps(reply, allow_nan=False)
        if len(raw.encode()) > 1024 * 1024:
            raise RuntimeError('Output limit exceeded')
        print(raw)
    except BaseException as exc:
        frames = []
        for frame in traceback.extract_tb(exc.__traceback__):
            if frame.filename == '<agent>' or os.path.dirname(os.path.abspath(frame.filename)) == os.getcwd():
                frames.append({'file': os.path.basename(frame.filename), 'line': frame.lineno, 'function': frame.name})
        hints = {
            'KeyError': 'A requested key is absent; inspect describe, coverage and null/missing maps.',
            'AttributeError': 'Use sdk.call(tool_name, **arguments); sdk.history(...) is a paging generator. There are no per-tool methods.',
            'IndexError': 'An index is outside the available items; inspect lengths and handle empty pages or lists.',
            'NameError': 'Each compute starts fresh; define/import names again or load a saved strategy file. sdk is already an injected instance.',
            'TypeError': 'Check function signatures, argument names/types, and explicit null values. sdk.call accepts either a JSON argument mapping or keyword arguments.',
            'PermissionError': 'Access is outside the allowed workspace/runtime or network boundary.',
            'ModuleNotFoundError': 'Use existing installed packages; automatic installation is unavailable.',
        }
        print(
            json.dumps(
                {
                    'error': type(exc).__name__,
                    'message': sanitised_message(exc),
                    'hint': hints.get(type(exc).__name__, 'Check the indicated agent source line.'),
                    'frames': frames[-5:],
                    'tool_error': exc.detail if isinstance(exc, ToolError) else None,
                    'stdout': sink.getvalue(),
                    'sdk_misuse': misuse_events(),
                }
            )
        )
        sys.exit(2)


if __name__ == '__main__':
    main()
