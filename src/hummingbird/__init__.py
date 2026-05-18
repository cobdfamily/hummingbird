"""Hummingbird: accessible-library HTTP server with a plugin surface."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("hummingbird")
except PackageNotFoundError:
    # Editable / source-tree run without installed metadata -- keep
    # tests and dev runs working. Production always has the package
    # installed, so this branch is never taken there.
    __version__ = "0.0.0+dev"
