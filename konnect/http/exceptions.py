# Copyright 2023  Dom Sekotill <dom.sekotill@kodo.org.uk>

"""
Publicly exposed exception classes used in the package
"""


class UnsupportedSchemeError(ValueError):
	"""
	An exception for non-HTTP URL schemes
	"""
