# Copyright 2024  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Helper functions for certificate discovery
"""

from pathlib import Path

KNOWN_CERT_PATHS = [
	Path("/etc/ssl/certs/ca-certificates.crt"),
	Path("/etc/pki/tls/certs/ca-bundle.crt"),
	Path("/usr/share/ssl/certs/ca-bundle.crt"),
	Path("/usr/local/share/certs/ca-root-nss.crt"),
	Path("/etc/ssl/cert.pem"),
	Path("/etc/ssl/certs"),
]


def _check_readable(path: Path) -> None:
	"""
	Raise an OSError if a file or directory path is not readable

	If a path does not exist `FileNotFoundError` is raised; if the current user cannot open
	a file or list the contents of a directory `PermissionError` is raised.
	"""
	try:
		path.open("rb").close()
	except IsADirectoryError:
		next(path.iterdir())


def discover_ca_certs() -> Path|None:
	"""
	Return the first readable path from the known locations for CA certificates

	If a path is returned it may be a certificate bundle file, or a directory of
	certificates.
	"""
	for path in KNOWN_CERT_PATHS:
		try:
			_check_readable(path)
		except (FileNotFoundError, PermissionError):
			continue
		return path
	return None
