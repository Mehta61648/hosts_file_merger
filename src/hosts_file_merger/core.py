"""Read, deduplicate, and merge hosts-file entries with precedence.

A hosts file maps hostnames to IP addresses, one mapping per line.  This
module knows how to parse several such files (or arbitrary text blobs) into
structured ``HostsEntry`` records, merge them honouring first-seen-wins
precedence, and render the result back to text.

Design decisions, stated plainly
--------------------------------

1. **One mapping per line.** A line like ``127.0.0.1 a b c`` is three separate
   entries that happen to share an address. Keeping them as distinct records
   makes dedup by hostname unambiguous and lets a caller drop a single host
   without rewriting the whole line.

2. **First-seen wins.** When the same hostname appears in two inputs, the
   address from the *earlier* input in the merge order is kept. This mirrors
   how a system administrator stitches together blocklists: the file listed
   first is the authoritative one, later files only *add* names it did not
   cover. We do not attempt to warn on conflicts; the caller can inspect the
   conflicts if they want by comparing inputs themselves.

3. **Comments are preserved per entry.** An inline ``#`` comment is attached
   to the entry on that line and carried through the merge. A comment line
   with no hostnames (a *standalone* comment) is preserved verbatim and kept
   in the position it first appeared. This lets blocklist section headers
   survive a merge.

4. **Case-folding for dedup.** Hostnames are case-insensitive per RFC 4343,
   so ``Localhost`` and ``localhost`` are the same name and only the first
   casing is retained.

5. **IPv6 scope IDs are stripped.** A link-local address such as
   ``fe80::1%eth0`` is normalised to ``fe80::1``; the scope is interface-local
   and meaningless in a merged hosts file that may run on a different
   machine.

6. **Blank lines are dropped.** They carry no information and re-emitting
   them would let a merge introduce arbitrary vertical whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


# A "standalone comment" is a line whose first non-space token is ``#`` and
# which contains no IP address. We detect these so section headers like
# ``# --- ad servers ---`` survive the merge in their original form.
#
# Inline comments (``127.0.0.1 foo # comment``) are handled separately during
# token parsing, not by this regex.
_COMMENT_RE = re.compile(r"^\s*#(.*)$")

# Heuristic IP recognisers. We avoid ``ipaddress`` for detection because we
# want to *preserve* the original textual form of the address (including
# IPv6 canonicalisation quirks a caller may rely on) and only need to know
# whether a token *is* an address for the "is this line a standalone
# comment?" decision. ``ipaddress`` is still used for the scope-id stripping
# normalisation where parsing is the point.
_IPV4_RE = re.compile(
    r"^(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}$"
)
# Permissive but bounded: hex groups of 1-4 digits, with colons, optional
# dotted-tail for IPv4-mapped form. Good enough to tell "address" from
# "hostname"; final correctness is the caller's.
_IPV6_RE = re.compile(
    r"^[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*"
    r"(?::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*)?)?"
    r"(?::(?:(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3})?)?$"
)


def _looks_like_ip(token: str) -> bool:
    """True if *token* is an IPv4 or IPv6 literal.

    Used only to decide whether a comment-prefixed line is standalone or
    carries an entry; never used to validate user input.
    """
    return bool(_IPV4_RE.match(token) or _IPV6_RE.match(token))


def _strip_scope_id(address: str) -> str:
    """Remove an IPv6 zone/scope id (``fe80::1%eth0`` -> ``fe80::1``).

    The scope id is meaningful only relative to a specific network stack on a
    specific machine; in a merged hosts file that may be deployed elsewhere it
    is noise. IPv4 addresses and IPv6 without a ``%`` are returned unchanged.
    """
    if "%" not in address:
        return address
    # Only IPv6 literals use the zone-id syntax, but we don't need to be strict
    # here: any token containing ``%`` that survived this far is treated as a
    # scoped address and truncated at the delimiter.
    return address.split("%", 1)[0]


@dataclass(frozen=True)
class HostsEntry:
    """A single hostname -> address mapping, plus any inline comment.

    Immutable so entries can live in sets and as dict keys without surprises.
    """

    address: str
    hostname: str
    comment: Optional[str] = None

    def render(self) -> str:
        """Return the canonical one-line text form of this entry.

        ``127.0.0.1 localhost`` or ``127.0.0.1 localhost # my comment``.
        """
        if self.comment:
            return f"{self.address} {self.hostname} # {self.comment}"
        return f"{self.address} {self.hostname}"


@dataclass(frozen=True)
class CommentLine:
    """A standalone comment line (no mapping), preserved verbatim.

    Distinct from an inline ``#`` comment attached to a ``HostsEntry`` so that
    blocklist section headers survive the merge as their own lines.
    """

    text: str

    def render(self) -> str:
        return self.text


def _split_inline_comment(rest: str) -> Tuple[str, Optional[str]]:
    """Split ``foo bar # baz`` into (``"foo bar"``, ``"baz"``).

    Returns the payload before the first ``#`` and the trimmed comment text
    after it (or ``None`` if no ``#`` is present). The leading ``#`` itself is
    not included in the returned comment.
    """
    if "#" not in rest:
        return rest, None
    payload, _, comment = rest.partition("#")
    return payload, comment.strip() or None


def parse_hosts_text(text: str) -> List[object]:
    """Parse a hosts-format string into a list of records.

    Returns a list of ``HostsEntry`` and ``CommentLine`` objects in the order
    they appeared in the input. Blank lines are dropped (see module docstring).

    A mapping line has the form::

        <address> <hostname> [<hostname> ...] [# <comment>]

    Every hostname on such a line becomes its own ``HostsEntry`` sharing the
    address and (if any) the inline comment. This split-on-parse behaviour is
    what makes per-hostname dedup possible downstream.
    """
    records: List[object] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        stripped = line.strip()
        if stripped.startswith("#"):
            # Could still be a mapping line written as ``# 127.0.0.1 foo``;
            # hosts(5) does not define that, and we deliberately do not support
            # it, so any line beginning with ``#`` is a standalone comment.
            records.append(CommentLine(text=line))
            continue

        payload, comment = _split_inline_comment(stripped)
        tokens = payload.split()
        if not tokens:
            # Payload was empty after stripping a comment marker — treat as a
            # standalone comment so the line is not silently lost.
            records.append(CommentLine(text=line))
            continue

        address = tokens[0]
        hostnames = tokens[1:]
        address = _strip_scope_id(address)

        if not hostnames:
            # An address with no hostnames is malformed for our purposes; we do
            # not raise so that a merge over a noisy blocklist is resilient,
            # but we do drop the line rather than emit a bare address.
            continue

        for hostname in hostnames:
            records.append(
                HostsEntry(
                    address=address,
                    hostname=hostname,
                    comment=comment,
                )
            )
    return records


class HostsMerger:
    """Merge multiple hosts inputs honouring first-seen-wins precedence.

    Inputs are parsed in the order supplied to :meth:`merge`; the first input
    to define a hostname owns the address and comment for that hostname. Later
    inputs may only contribute hostnames that no earlier input mentioned.

    Standalone comment lines are kept in the order they first appear and are
    emitted between the entries that surrounded them in their source input —
    more precisely, in the order produced by walking the merged record list
    (see :meth:`merge`).
    """

    def __init__(self) -> None:
        # hostname (case-folded) -> the winning HostsEntry
        self._entries: dict[str, HostsEntry] = {}
        # insertion-ordered list of records as they should be emitted
        self._ordered: List[object] = []
        # case-folded hostnames already seen, for O(1) duplicate checks
        self._seen: set[str] = set()

    def add(self, text: str) -> "HostsMerger":
        """Parse and fold *text* into the merge.

        Returns ``self`` so calls can chain.
        """
        for record in parse_hosts_text(text):
            if isinstance(record, CommentLine):
                # Standalone comments are always kept; they cannot conflict.
                self._ordered.append(record)
                continue
            key = record.hostname.casefold()
            if key in self._seen:
                continue
            self._seen.add(key)
            self._entries[key] = record
            self._ordered.append(record)
        return self

    def merge(self, inputs: Iterable[str]) -> List[object]:
        """Parse every input in *inputs* and return the merged record list.

        The returned list contains ``HostsEntry`` and ``CommentLine`` objects
        in emission order. The merger is reset before merging so the instance
        can be reused across independent merge jobs.
        """
        self._entries.clear()
        self._ordered.clear()
        self._seen.clear()
        for text in inputs:
            self.add(text)
        return list(self._ordered)

    def render(self, inputs: Sequence[str]) -> str:
        """Convenience: merge *inputs* and render to a hosts-format string."""
        return "\n".join(r.render() for r in self.merge(inputs))
