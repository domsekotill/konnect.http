# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Tokenising of cookies and attributes from "Set-Cookie:" HTTP headers
"""

import re
from collections.abc import Iterator
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from enum import Enum
from enum import auto
from typing import Literal
from typing import TypeAlias
from typing import Union
from urllib.parse import unquote as urldecode

from .dates import dateparse

__all__ = ["tokenise"]

DOT = ord(".")
SLASH = ord("/")

HEADER_RE = re.compile(
	rb"""(?ix)
	^Set-Cookie:
		\s* (?P<name>[^][()<>@,;:\\"/?={} \t]*)
		\s* (?P<value>[=][^;]*)?  # value includes "="
	| [;] \s*
		(?P<attr_name> expires | max-age | domain | path | secure | httpOnly ) \s*
		(?:[=] (?P<attr_value>[^;]*))?
	\s*
	""",
)


class TokenType(Enum):

	NAME = auto()
	VALUE = auto()
	DOMAIN = auto()
	PATH = auto()
	EXPIRES = auto()
	MAX_AGE = auto()
	SECURE = auto()
	HTTP_ONLY = auto()


Token: TypeAlias = Union[
	tuple[Literal[TokenType.NAME, TokenType.DOMAIN, TokenType.PATH], str],
	tuple[Literal[TokenType.VALUE], bytes],
	tuple[Literal[TokenType.EXPIRES], DateTime],
	tuple[Literal[TokenType.MAX_AGE], TimeDelta],
	tuple[Literal[TokenType.SECURE, TokenType.HTTP_ONLY], bool],
]


def tokenise(header: bytes) -> Iterator[Token]:
	"""
	Yield tokenised parts of a "Set-Cookie:" header

	Yields token (name, value) tuples; the type of a token value is token dependant.
	The algorithm used is the more permissive one for user agents in RFC6265#section-5.1

	This parser is guaranteed to yield the following tokens or raise `ValueError`:

	- name: a non-empty raw byte string
	- value: a raw byte string that may be empty
	- secure: boolean
	- http-only: boolean

	The parser may optionally yield the following tokens:

	- domain: a normalised, decoded domain name (str)
	- path: a decoded, absolute path value (str)
	- expires: a `datetime.datetime` instance
	- max-age: a `datetime.timedelta` instance
	"""
	if not header.startswith(b"Set-Cookie:"):
		raise ValueError(f"Not a Set-Cookie header: {header!r}")

	secure = http_only = False

	for match in HEADER_RE.finditer(header):
		name, value = match.group("name", "value")
		if name is None:
			name, value = match.group("attr_name", "attr_value")
			assert name is not None
		elif name == b"":
			raise ValueError("Cookies require a name")
		elif value is None:
			raise ValueError("Cookies require a value")
		else:
			yield TokenType.NAME, name.strip().decode("ascii")
			yield TokenType.VALUE, value.strip()[1:]  # strip leading "="
			continue

		match (name.strip().lower(), value):
			case [b"expires", bytes(expires)]:
				yield TokenType.EXPIRES, dateparse(expires.decode("ascii"))
			case [b"max-age", bytes(max_age)]:
				yield TokenType.MAX_AGE, TimeDelta(seconds=int(max_age, 10))
			case [b"domain", bytes(domain)]:
				domain = domain.strip()
				if domain[-1] == DOT:
					continue  # domains ending with "." MUST be ignored
				if domain[0] == DOT:
					domain = domain.lstrip(b".")
				# TODO: Additional check for disallowed chars?
				yield TokenType.DOMAIN, domain.decode("idna").lower()
			case [b"path", bytes(path)]:
				path = path.strip()
				if path[0] != SLASH:
					continue  # relative paths MUST be ignored
				yield TokenType.PATH, urldecode(path)
			case [b"secure", _]:
				secure = True
				yield TokenType.SECURE, True
			case [b"httponly", _]:
				http_only = True
				yield TokenType.HTTP_ONLY, True
			case [b"expires" | b"max-age" | b"domain" | b"path", None]:
				raise ValueError(f"Cookie attribute {name.decode()!r} requires a value")
			case _:  # pragma: no-cover
				raise RuntimeError(
					f"Unhandled attribute or missing value"
					f" ({match.group(0)!r} -> {match.groupdict()})",
				)

	if not secure:
		yield TokenType.SECURE, False
	if not http_only:
		yield TokenType.HTTP_ONLY, False
