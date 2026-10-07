# hosts_file_merger

Read, deduplicate, and merge hosts-file entries honoring comments and first-seen-wins precedence.

## Usage

```python
from hosts_file_merger import HostsMerger, HostsEntry

merger = HostsMerger()
merged = merger.merge([
    "# local overrides\n127.0.0.1 myhost # dev box\n",
    "0.0.0.0 ads.example.net\n0.0.0.0 trackers.example.net\n",
])
for record in merged:
    print(record.render())
```

`HostsMerger` exposes `add(text) -> self`, `merge(inputs) -> list[HostsEntry | CommentLine]`, and `render(inputs) -> str`. `HostsEntry` is a frozen dataclass with `address`, `hostname`, `comment`, and `render()`.

## Why this exists

The problem is concrete: you have a personal hosts file and several third-party blocklists, and you want one merged file without duplicate hostnames or clobbered overrides. Doing this with `cat` produces duplicates; doing it by hand doesn't scale.

The trade-off: **first-seen wins**. The order of inputs to `merge()` is the precedence order — the first input to claim a hostname owns its address and comment. Later inputs only contribute hostnames nobody mentioned. This matches how an administrator stitches blocklists together (your overrides first, public lists second) and is simple to reason about. We do not attempt conflict reporting or per-address grouping; if you need those, this is the wrong library.

## Parsing rules, stated plainly

- A line like `127.0.0.1 a b c` is **three entries** sharing one address, not one entry with three hostnames. This makes per-hostname dedup unambiguous.
- Hostnames are compared case-insensitively (RFC 4343); the first casing is retained.
- Inline `#` comments are attached to every entry on that line and carried through the merge.
- Lines beginning with `#` are standalone comments, preserved verbatim and kept in their original position.
- IPv6 scope IDs (`fe80::1%eth0`) are stripped to `fe80::1`; they are interface-local and meaningless in a merged file deployed elsewhere.
- Blank lines are dropped. An address with no hostnames is dropped.

## Awkward edge

A line that is *only* an address (`127.0.0.1` with nothing after it) is silently dropped rather than emitted as a bare address or raised as an error. Blocklists in the wild are noisy; the library chooses resilience over strictness here. If you need validation, inspect the inputs before merging.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

