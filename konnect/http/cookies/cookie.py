# Copyright 2023-2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Module containing the core Cookie class and checking functions
"""

from datetime import UTC
from datetime import datetime as DateTime
from datetime import timedelta as TimeDelta
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from ipaddress import ip_address
from typing import Self
from urllib.parse import urlparse

from .set_cookie_parser import TokenType
from .set_cookie_parser import tokenise


class Cookie:
	"""
	Client-side cookie data class
	"""

	def __init__(
		self,
		name: str, value: bytes,
		expires: DateTime|TimeDelta|None,
		domain: str, path: str, *,
		secure: bool = False, httponly: bool = False, exactdomain: bool = True,
	) -> None:
		self.name = name
		self.value = value
		self.expires = (
			None if expires is None else
			expires if isinstance(expires, DateTime) else
			(DateTime.now(UTC) + expires)
		)
		self.domain = domain
		self.path = path
		self.secure = secure
		self.httponly = httponly
		self.exactdomain = exactdomain

	def __bytes__(self) -> bytes:
		return b"=".join((self.name.encode("ascii"), self.value))

	@classmethod
	def from_header(cls, header: bytes, domain: str, path: str) -> Self:
		"""
		Return a `Cookie` from an HTTP "Set-Cookie:" header string
		"""
		self = cls("", b"", None, domain, path)
		for token in tokenise(header):
			match token:
				case [TokenType.NAME, str(name)]:
					self.name = name
				case [TokenType.VALUE, bytes(value)]:
					self.value = value
				case [TokenType.DOMAIN, str(domain)]:
					# TODO(dom): validate cookie domains
					# https://code.kodo.org.uk/konnect/konnect.http/-/issues/10
					self.domain = domain
					self.exactdomain = False
				case [TokenType.PATH, str(path)]:
					# TODO(dom): validate cookie paths
					# https://code.kodo.org.uk/konnect/konnect.http/-/issues/10
					self.path = path
				case [TokenType.EXPIRES, DateTime() as expires]:
					self.expires = expires
				case [TokenType.MAX_AGE, TimeDelta() as max_age]:
					self.expires = DateTime.now(UTC) + max_age
				case [TokenType.SECURE, bool(secure)]:
					self.secure = secure
				case [TokenType.HTTP_ONLY, bool(http_only)]:
					self.httponly = http_only
				case _:
					assert None, f"unexpected token {token}"
		assert self.name != ""
		return self

	def as_header(self, header_name: str = "Cookie") -> bytes:
		"""
		Return the cookie formatted as an HTTP header
		"""
		raise NotImplementedError


def check_cookie(cookie: Cookie, url: str) -> bool:
	"""
	Return whether the cookie should be sent with the request
	"""
	if cookie.expires and cookie.expires <= DateTime.now(UTC):
		return False

	parts = urlparse(url)
	if not parts.hostname:
		raise ValueError("URLs must be absolute")

	cookie_host = normalencode_host(cookie.domain)
	url_host = normalencode_host(parts.hostname)
	if cookie_host != url_host:
		if not isinstance(cookie_host, bytes) or not isinstance(url_host, bytes):
			return False
		if cookie.domain[-1] == ".":  # Treat trailing `.` as marker for exact match
			return False
		if not url_host.endswith(b"." + cookie_host):
			return False

	cookie_path = normalise_path(cookie.path)
	url_path = normalise_path(parts.path)
	if cookie_path != url_path:
		prefix, _, suffix = url_path.partition(cookie_path)
		if prefix:
			return False
		if suffix and suffix[0] != "/":
			return False

	return True


def normalencode_host(host: str) -> bytes|IPv4Address|IPv6Address:
	"""
	Turn a host name or address string into a normalised host name or IP address object

	Host name normalisation includes case lowering, and encoding unicode labels according to
	IDNA standards.
	"""
	try:
		return ip_address(host)
	except ValueError:
		return host.strip(".").lower().encode("idna")


def normalise_path(path: str) -> str:
	"""
	Normalise a cookie path by stripping leaf components and replacing invalid paths with /

	Note that the output path *always* ends with a "/".
	"""
	if not path or path[0] != "/":
		return "/"
	return path.rsplit("/", 1)[0] + "/"
