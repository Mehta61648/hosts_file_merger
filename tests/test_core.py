"""Tests for hosts_file_merger.core.

Covers parsing, dedup, precedence, and rendering — the behaviours the
implementation actually claims to support. We deliberately do not test
things like CIDR ranges or IPv6 canonicalisation because the library does
not perform those transforms.
"""

import unittest

from hosts_file_merger.core import (
    CommentLine,
    HostsEntry,
    HostsMerger,
    parse_hosts_text,
)


class ParseHostsTextTests(unittest.TestCase):
    def test_simple_mapping(self):
        records = parse_hosts_text("127.0.0.1 localhost")
        self.assertEqual(len(records), 1)
        entry = records[0]
        self.assertIsInstance(entry, HostsEntry)
        self.assertEqual(entry.address, "127.0.0.1")
        self.assertEqual(entry.hostname, "localhost")
        self.assertIsNone(entry.comment)

    def test_multiple_hostnames_one_line_become_separate_entries(self):
        records = parse_hosts_text("127.0.0.1 a b c")
        self.assertEqual(len(records), 3)
        names = [r.hostname for r in records]
        self.assertEqual(names, ["a", "b", "c"])
        for r in records:
            self.assertEqual(r.address, "127.0.0.1")

    def test_inline_comment_is_attached(self):
        records = parse_hosts_text("127.0.0.1 foo # my comment")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].comment, "my comment")

    def test_inline_comment_shared_by_all_hostnames_on_line(self):
        records = parse_hosts_text("127.0.0.1 a b # shared")
        self.assertEqual([r.comment for r in records], ["shared", "shared"])

    def test_standalone_comment_preserved_verbatim(self):
        records = parse_hosts_text("# a section header")
        self.assertEqual(len(records), 1)
        line = records[0]
        self.assertIsInstance(line, CommentLine)
        self.assertEqual(line.text, "# a section header")

    def test_blank_lines_are_dropped(self):
        records = parse_hosts_text("127.0.0.1 a\n\n\n127.0.0.1 b")
        self.assertEqual(len(records), 2)

    def test_address_with_no_hostnames_is_dropped(self):
        records = parse_hosts_text("127.0.0.1")
        self.assertEqual(records, [])

    def test_ipv6_scope_id_stripped(self):
        records = parse_hosts_text("fe80::1%eth0 router")
        self.assertEqual(records[0].address, "fe80::1")

    def test_ipv6_without_scope_unchanged(self):
        records = parse_hosts_text("::1 localhost")
        self.assertEqual(records[0].address, "::1")

    def test_trailing_whitespace_stripped(self):
        records = parse_hosts_text("127.0.0.1 localhost   \n")
        self.assertEqual(records[0].hostname, "localhost")


class HostsMergerPrecedenceTests(unittest.TestCase):
    def test_first_seen_wins_address(self):
        m = HostsMerger()
        merged = m.merge([
            "127.0.0.1 conflict\n",
            "10.0.0.1 conflict\n",
        ])
        self.assertEqual(len(merged), 1)
        entry = merged[0]
        self.assertIsInstance(entry, HostsEntry)
        self.assertEqual(entry.address, "127.0.0.1")

    def test_first_seen_wins_comment(self):
        m = HostsMerger()
        merged = m.merge([
            "127.0.0.1 foo # first\n",
            "10.0.0.1 foo # second\n",
        ])
        entry = merged[0]
        self.assertEqual(entry.comment, "first")

    def test_case_insensitive_dedup(self):
        m = HostsMerger()
        merged = m.merge([
            "127.0.0.1 LocalHost\n",
            "10.0.0.1 localhost\n",
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].hostname, "LocalHost")

    def test_later_input_adds_new_hostnames(self):
        m = HostsMerger()
        merged = m.merge([
            "127.0.0.1 a\n",
            "127.0.0.2 b\n",
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual([r.hostname for r in merged], ["a", "b"])

    def test_standalone_comments_preserved_in_order(self):
        m = HostsMerger()
        merged = m.merge([
            "# header one\n127.0.0.1 a\n",
            "# header two\n127.0.0.2 b\n",
        ])
        rendered = [r.render() for r in merged]
        self.assertEqual(rendered, [
            "# header one",
            "127.0.0.1 a",
            "# header two",
            "127.0.0.2 b",
        ])

    def test_merge_is_idempotent_across_reuse(self):
        m = HostsMerger()
        first = m.merge(["127.0.0.1 a\n"])
        second = m.merge(["127.0.0.2 b\n"])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].hostname, "b")

    def test_add_chains_and_returns_self(self):
        m = HostsMerger()
        result = m.add("127.0.0.1 a\n").add("127.0.0.2 b\n")
        self.assertIs(result, m)
        self.assertEqual(len(m._ordered), 2)


class RenderTests(unittest.TestCase):
    def test_render_entry_without_comment(self):
        entry = HostsEntry(address="127.0.0.1", hostname="localhost")
        self.assertEqual(entry.render(), "127.0.0.1 localhost")

    def test_render_entry_with_comment(self):
        entry = HostsEntry(
            address="127.0.0.1", hostname="localhost", comment="hi"
        )
        self.assertEqual(entry.render(), "127.0.0.1 localhost # hi")

    def test_merger_render_produces_full_text(self):
        m = HostsMerger()
        text = m.render([
            "# header\n127.0.0.1 a b\n",
            "10.0.0.1 c\n",
        ])
        self.assertEqual(text, "# header\n127.0.0.1 a\n127.0.0.1 b\n10.0.0.1 c")

    def test_render_empty_inputs(self):
        m = HostsMerger()
        self.assertEqual(m.render([]), "")


if __name__ == "__main__":
    unittest.main()
