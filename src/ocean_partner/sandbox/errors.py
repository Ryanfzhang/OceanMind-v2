"""Errors raised by OceanMind's fail-closed scientific execution sandbox."""


class SandboxUnavailableError(RuntimeError):
    """The requested scientific execution cannot be isolated safely."""


__all__ = ["SandboxUnavailableError"]
