# Copyright 2023-2024  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Response classes for HTTP requests

Instances of the classes contained in this module are not created directly by users, instead
they are returned from `konnect.http.Session` methods.  If needed for typing, they are
exported from `konnect.http`.
"""

from __future__ import annotations

from asyncio import IncompleteReadError
from asyncio import LimitOverrunError
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import TYPE_CHECKING

from anyio import EndOfStream

if TYPE_CHECKING:
	from .request import CurlRequest


class ReadStream:
	"""
	A readable stream for response bodies

	This class implements the methods for `asyncio.StreamReader` and
	`anyio.abc.ByteReceiveStream`, allowing it to be passed to library functions that may
	require either of those interfaces.
	"""

	def __init__(self, request: CurlRequest) -> None:
		self.request: CurlRequest|None = request
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
		self.request = None

	async def _receive(self) -> bytes:
		# Wait until a chunk is available, and return it. Raise EndOfStream if indicated
		# with an empty byte chunk.
		if self._buffer:
			data, self._buffer = self._buffer, b""
			return data
		if self.request is None:
			raise EndOfStream
		if (data := await self.request.get_data()) == b"":
			self.request = None
			raise EndOfStream
		return data

	async def receive(self, max_bytes: int = 65536, /) -> bytes:
		"""
		Read and return up to `max_bytes` bytes from the stream

		Implements `anyio.abc.ByteReceiveStream.receive()`
		"""
		data = await self._receive()
		if max_bytes >= 0:
			data, self._buffer = data[:max_bytes], data[max_bytes:]
		return data

	async def readuntil(self, separator: bytes = b'\n') -> bytes:
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
				raise IncompleteReadError(b''.join(chunks), None)
			if (split := data.find(separator)) >= 0:
				split += len(separator)
				assert len(data) >= split
				data, self._buffer = data[:split], data[split:]
			chunks.append(data)
			length += len(data)
		return b''.join(chunks)

	async def read(self, max_size: int = -1, /) -> bytes:
		"""
		Read and return up to `max_size` bytes from the stream

		Be cautious about calling this with a non-positive `max_size` as the entire stream
		will be stored in memory.

		Implements `asyncio.StreamReader.read()`
		"""
		if max_size >= 0:
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
			pass
		return b''.join(chunks)

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
			return await self.readuntil(b'\n')
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
			IncompleteReadError(b''.join(chunks), size)
		return b''.join(chunks)

	def at_eof(self) -> bool:
		"""
		Return `True` if the buffer is empty and an end-of-file has been indicated
		"""
		return not self._buffer and self.request is None


class Response:
	"""
	A class for response details, and header and body accessors
	"""

	def __init__(self, response: str, stream: ReadStream):
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
		self.stream = stream
		self.headers = list[tuple[str, bytes]]()

	def __repr__(self) -> str:
		return f"<Response {self.code} {self.status}>"
