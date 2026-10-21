"""Split a command line the way the shell it targets splits it."""

from __future__ import annotations

import shlex


def split_command(text: str, posix: bool = True) -> list[str]:
    """One command line as its argument list.

    POSIX splitting treats the backslash as an escape and mangles a
    Windows path; non-POSIX splitting keeps its quote marks. So the
    non-POSIX route splits first, then drops one surrounding quote pair
    per token.
    """
    if posix:
        return shlex.split(text)
    return [
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'"
        else part
        for part in shlex.split(text, posix=False)
    ]
