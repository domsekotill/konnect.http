# Copyright 2023-2024  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Publicly exposed exception classes used in the package
"""


class UnsupportedSchemeError(ValueError):
	"""
	An exception for non-HTTP URL schemes
	"""


class CertificatesNotFoundError(ExceptionGroup[FileNotFoundError|PermissionError]):
	"""
	Indicates that no certificate paths could be found, and which paths were checked
	"""


class UnauthorizedError(Exception):
	"""
	A server responded to a request with '401 Unauthorized'
	"""
