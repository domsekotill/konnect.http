# Copyright 2023-2026  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Response classes for HTTP requests

Instances of the classes contained in this module are not created directly by users, instead
they are returned from `konnect.http.Session` methods.  If needed for typing, they are
exported from `konnect.http`.
"""

from __future__ import annotations

from asyncio import IncompleteReadError
from asyncio import LimitOverrunError
from http import HTTPStatus
from itertools import chain
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from anyio import EndOfStream

if TYPE_CHECKING:
	from collections.abc import AsyncIterator
	from collections.abc import Awaitable
	from collections.abc import Callable
	from collections.abc import Iterator
	from typing import ParamSpec

	from .request import CurlHandle

	P = ParamSpec("P")

REDIRECT_CODES = [
	HTTPStatus.MOVED_PERMANENTLY,
	HTTPStatus.FOUND,
	HTTPStatus.SEE_OTHER,
	HTTPStatus.TEMPORARY_REDIRECT,
	HTTPStatus.PERMANENT_REDIRECT,
]


def _check_non_empty(func: Callable[P, Awaitable[bytes]]) -> Callable[P, Awaitable[bytes]]:
	if not __debug__:
		return func
	async def wrap(*args: P.args, **kwargs: P.kwargs) -> bytes:
		resp = await func(*args, **kwargs)
		assert len(resp) > 0, f"empty bytes response from {func}"
		return resp
	return wrap


def _parse_field(field: bytes) -> tuple[str, dict[str, str]]:
	# Return an item and parameters as a dict
	item, *params = field.decode("utf-8").split(";")
	return item, {
		key.lstrip(): value.rstrip()
		for param in params
		for key, value in param.split("=", maxsplit=1)
	}


class ReadStream:
	"""
	A readable stream for response bodies

	This class implements the methods for `asyncio.StreamReader` and
	`anyio.abc.ByteReceiveStream`, allowing it to be passed to library functions that may
	require either of those interfaces, regardless of the actual async runtime used.
	"""

	def __init__(self, handle: CurlHandle) -> None:
		self.request = handle.request
		self._handle: CurlHandle | None = handle
		self._buffer = b""

	async def __aiter__(self) -> AsyncIterator[bytes]:
		try:
			while (chunk := await self.receive()):
				yield chunk
		except EndOfStream:
			return

	async def aclose(self) -> None:
		"""
		Close the stream

		Implements `anyio.abc.AsyncResource.aclose()`
		"""
		self._handle = None

	@_check_non_empty
	async def _receive(self) -> bytes:
		# Wait until a chunk is available, and return it. Raise EndOfStream if indicated
		# with an empty byte chunk.
		if self._buffer:
			data, self._buffer = self._buffer, b""
			return data
		if self._handle is None:
			raise EndOfStream
		if (data := await self._handle.get_data()) == b"":
			self._handle = None
			raise EndOfStream
		return data

	async def receive(self, max_bytes: int = 65536, /) -> bytes:
		"""
		Read and return up to `max_bytes` bytes from the stream

		Implements `anyio.abc.ByteReceiveStream.receive()`
		"""
		data = await self._receive()
		if max_bytes == 0:
			return b""
		if max_bytes > 0:
			data, self._buffer = data[:max_bytes], data[max_bytes:]
		return data

	async def readuntil(self, separator: bytes = b"\n") -> bytes:
		"""
		Read and return up-to and including the first instance of `separator` in the stream

		If an EOF occurs before encountering the separator `IncompleteReadError` is raised.
		If the separator is not encountered within the configured buffer size limit for the
		stream, `LimitOverrunError` is raised and the buffer left intact.

		Implements `asyncio.StreamReader.readuntil()`
		"""
		chunks = list[bytes]()
		length = 0
		split = -1
		while split < 0:
			try:
				data = await self._receive()
			except EndOfStream:
				raise IncompleteReadError(b"".join(chunks), None)
			if (split := data.find(separator)) >= 0:
				split += len(separator)
				assert len(data) >= split
				data, self._buffer = data[:split], data[split:]
			chunks.append(data)
			length += len(data)
		return b"".join(chunks)

	async def read(self, max_size: int = -1, /) -> bytes:
		"""
		Read and return up to `max_size` bytes from the stream

		Be cautious about calling this with a non-positive `max_size` as the entire stream
		will be stored in memory.

		Implements `asyncio.StreamReader.read()`
		"""
		# shortcut `read(0)` as apparently some libraries use it to check return type
		if max_size == 0:
			return b""
		if max_size > 0:
			try:
				return await self.receive(max_size)
			except EndOfStream:
				return b""
		# Collect ALL THE DATA and return it
		chunks = list[bytes]()
		try:
			while (chunk := await self.receive()):
				chunks.append(chunk)
		except EndOfStream:
			return b"".join(chunks)
		raise AssertionError("EndOfStream not raised by Stream.receive()")

	async def readline(self) -> bytes:
		r"""
		Read and return one '\n' terminated line from the stream

		Unlike `readuntil()` an incomplete line will be returned of an EOF occurs, and
		`ValueError` is raised instead of `LimitOverrunError`.  In the event of
		a `LimitOverrunError` the buffer is also cleared.

		This implementation differs very slightly from Asyncio's, as the behaviour described
		there is a hot mess.  It is *highly* recommended you use `readuntil` instead.

		Implements `asyncio.StreamReader.readline()`
		"""
		try:
			return await self.readuntil(b"\n")
		except IncompleteReadError as exc:
			return exc.partial
		except LimitOverrunError as exc:
			self._buffer = b""
			raise ValueError(exc.args[0])

	async def readexactly(self, size: int, /) -> bytes:
		"""
		Read and return exactly `size` bytes from the stream

		Implements `asyncio.StreamReader.readexactly()`
		"""
		chunks = list[bytes]()
		try:
			while size > 0:
				chunks.append(chunk := await self.receive(size))
				size -= len(chunk)
			assert size == 0, "ReadStream.receive() returned too many bytes"
		except EndOfStream:
			IncompleteReadError(b"".join(chunks), size)
		return b"".join(chunks)

	def at_eof(self) -> bool:
		"""
		Return `True` if the buffer is empty and an end-of-file has been indicated
		"""
		return not self._buffer and self._handle is None


class Response:
	"""
	A class for response details, and header and body accessors
	"""

	def __init__(self, response: str, stream: ReadStream) -> None:
		match response.split(maxsplit=2):
			case [version, response, status]:
				self.version = version
				self.code = HTTPStatus(int(response))
				self.status = status.strip()
			case [version, response]:
				self.version = version
				self.code = HTTPStatus(int(response))
				self.status = self.code.phrase
			case _:
				raise ValueError
		assert stream.request is not None
		self.request = stream.request
		self.stream = stream
		self.headers = list[tuple[str, bytes]]()
		self.trailers = list[tuple[str, bytes]]()
		self.previous: Response | None = None

	def __repr__(self) -> str:
		return f"<Response {self.code} {self.status}>"

	def get_fields(self, name: str) -> Iterator[bytes]:
		"""
		Return the values of each instance of a named header or trailer field
		"""
		name = name.lower()
		for current, value in chain(self.headers, self.trailers):
			assert current.islower()
			if name == current:
				yield value

	def field(self, name: str) -> str:
		"""
		Return all items of the named header or trailer field as a single value

		All values of the field will be returned as a comma-separated list.  Note there is
		no attempt made to check for fields that should not be lists.

		Set-Cookie fields cannot be accessed this way as commas have a different meaning
		within their structure.

		See: https://www.rfc-editor.org/rfc/rfc9110#section-5.3
		"""
		if name.lower() == "set-cookie":
			msg = "Set-Cookie fields cannot be accessed as a single field; use get_fields()"
			raise ValueError(msg)
		return ", ".join(val.decode("utf-8") for val in self.get_fields(name))

	def _each_field(self, name: str) -> Iterator[bytes]:
		# Return a generator yielding each instance of a field, split on commas; may have
		# surrounding spaces
		return (inst.strip() for value in self.get_fields(name) for inst in value.split(b","))

	def link(self, rel: str) -> str | None:
		"""
		Return a Link field with the given "rel" attribute, if any
		"""
		for link in self._each_field("link"):
			url, params = _parse_field(link)
			if params.get("rel") == rel:
				return url.lstrip("<").rstrip(">")
		return None

	def get_redirect(self) -> str | None:
		"""
		Return the URL that a redirect response is directed to, or None for non-redirect responses
		"""
		if self.code not in REDIRECT_CODES:
			return None
		for name, value in self.headers:
			assert name.islower()
			if name == "location":
				return urljoin(self.request.url, value.decode("ascii"))
