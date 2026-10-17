"""Protocol errors.

Every subclass of :class:`Rejected` is safe to show to an agent: messages never
contain host paths or raw source values. :func:`safe_error` is the only way an
exception becomes part of a tool response.
"""


class Rejected(RuntimeError):
    """A request the protocol refuses. The message is shown to the agent."""


class BudgetExceeded(Rejected):
    """A hard session or job limit; the current session or job ends."""


class BudgetLimit(Rejected):
    """Recoverable data allowance rejection; never terminates a session or worker."""

    def __init__(self, cap, used, limit, requested):
        self.details = {'cap': cap, 'used': used, 'limit': limit, 'requested': requested}
        super().__init__(
            f'Data {cap} limit reached: used={used}, limit={limit}, requested={requested}. '
            'Reduce the request or save your strategy; control tools and compute remain available.'
        )


class ProviderCostLimit(Rejected):
    """Operator funding stop; must never be converted into a finished learning session."""


class JobFailed(Rejected):
    """A bounded, recoverable compute failure; never an unknown pending mutation."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class Pending(RuntimeError):
    """An interrupted operation whose outcome must be resolved on resume."""


def safe_error(exc):
    """Agent-visible error payload for any exception raised while serving a tool."""
    if isinstance(exc, Rejected):
        return {
            'code': type(exc).__name__,
            'message': str(exc),
            **(exc.details if isinstance(exc, BudgetLimit) else {}),
            **({'details': exc.details} if isinstance(exc, JobFailed) else {}),
        }
    return {'code': 'InvalidRequest', 'message': 'Request does not match the documented tool schema'}
