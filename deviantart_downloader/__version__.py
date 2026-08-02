"""The one place the version is written.

Everything else derives from it. pyproject declares the version dynamic and
points setuptools here, so the packaged metadata and what the code reports
cannot drift apart: there is nothing to keep in step, because there is only one
of them.

The module holds a single assignment and imports nothing, on purpose. Both
setuptools at build time and the release workflow read it without importing the
package around it -- which they could not do anyway, since deviantart_downloader
exits on import when requests is missing, and a machine that is only building a
wheel has no reason to have it installed.
"""

__version__ = "3.7.0"
