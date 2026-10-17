"""Pipe-only client. No database, namespace key, network credentials or host paths."""

import json
import os

# Shared by every SDK instance in this worker, including user-created instances.
_misuse = {}


def misuse_events():
    return [{'attribute': name, 'count': count} for name, count in sorted(_misuse.items())]


class ToolError(RuntimeError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail.get('message', 'Tool rejected'))


class SDK:
    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        _misuse[name] = _misuse.get(name, 0) + 1
        raise AttributeError(
            f"SDK has no method {name!r}. Use sdk.call({name!r}, **arguments) with that tool's documented arguments (see describe)."
        )

    def __init__(self):
        # Retain the inherited base pipes even if user code shadows the injected
        # instance with `import sdk`, then constructs another SDK instance.
        self.request = os.fdopen(os.dup(int(os.environ['JRDB_REQUEST_FD'])), 'w', buffering=1)
        self.response = os.fdopen(os.dup(int(os.environ['JRDB_RESPONSE_FD'])), 'r')

    def call(self, name, arguments=None, **keywords):
        if arguments is None:
            arguments = {}
        if isinstance(arguments, dict):
            if set(arguments) & set(keywords):
                raise ToolError(
                    {
                        'code': 'SDKArguments',
                        'message': 'Duplicate SDK arguments: use each argument in the mapping or keyword arguments only once.',
                    }
                )
            arguments = {**arguments, **keywords}
        elif keywords:
            raise ToolError({'code': 'SDKArguments', 'message': 'SDK arguments must be a JSON object or keyword arguments.'})
        # Non-object positional input goes through the broker's validation and
        # step meter just like an invalid direct tool request.
        self.request.write(json.dumps({'tool': name, 'arguments': arguments}, allow_nan=False) + '\n')
        self.request.flush()
        raw = self.response.readline(1024 * 1024 + 1)
        if not raw:
            raise RuntimeError('Broker disconnected')
        reply = json.loads(raw)
        if 'error' in reply:
            err = reply['error']
            detail = dict(err) if isinstance(err, dict) else {'message': str(err)}
            if '_budget' in reply:
                detail['_budget'] = reply['_budget']
            raise ToolError(detail)
        return reply['result']

    def history(self, limit=8, **query):
        cursor = None
        while True:
            page = self.call('history', limit=limit, cursor=cursor, **query)
            yield from page['examples']
            cursor = page['next_cursor']
            if cursor is None:
                return
