# Copyright 2023, 2025  Dom Sekotill <dom.sekotill@kodo.org.uk>

import os
import re
from warnings import warn


def check_exc(exc: Exception, mesg: str) -> None:
	"""
	Check that the exception message fits the given pattern

	The pattern may use "[...]" surrounded by whitespace as a wildcard within a string.
	"""
	if re.search(mesg.replace(" [...] ", ".*"), str(exc), re.I):
		return
	detail = f"expected {mesg!r}, got {str(exc)!r}"
	if "TEST_STRICT_EXC" in os.environ:
		raise AssertionError(f"Exception does not match expectation: {detail}")
	warn(f"Exception may have changed: {detail}", RuntimeWarning, 2)
