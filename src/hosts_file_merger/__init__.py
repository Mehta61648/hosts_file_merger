"""Package interface for hosts_file_merger.

Re-exports the public API so callers can do ``from hosts_file_merger import
HostsMerger, HostsEntry`` rather than reaching into the ``core`` module.
"""

from .core import HostsEntry, HostsMerger

__all__ = ["HostsEntry", "HostsMerger"]
