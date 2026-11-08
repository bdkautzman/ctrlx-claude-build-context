"""Self-test of the kit's docs:  python3 -m unittest discover -s tools/tests

docs/ctrlx/gotchas.md is read through its topic index (the whole file is too big to read every time),
so the index has to list every entry, and the entries have to be numbered 1..N in order."""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GOTCHAS = os.path.join(HERE, "..", "..", "docs", "ctrlx", "gotchas.md")


def read_gotchas():
    with open(GOTCHAS, encoding="utf-8") as f:
        text = f.read()
    entries = [int(n) for n in re.findall(r"^(\d+)\. ", text, re.M)]
    _, found, rest = text.partition("\n## Index by topic\n")
    index = rest.split("\n## ", 1)[0] if found else ""
    return entries, index


class GotchasIndexTest(unittest.TestCase):
    def test_entries_are_numbered_in_order(self):
        entries, _ = read_gotchas()
        self.assertTrue(entries, "no entries found")
        self.assertEqual(entries, list(range(1, len(entries) + 1)),
                         "entries must be numbered 1..N in file order (a line that starts with '<n>. ' "
                         "outside an entry also breaks the sed recipe at the top of gotchas.md)")

    def test_index_lists_every_entry(self):
        entries, index = read_gotchas()
        self.assertTrue(index, "section '## Index by topic' missing")
        indexed = {int(n) for n in re.findall(r"#(\d+)\b", index)}
        missing = sorted(set(entries) - indexed)
        unknown = sorted(indexed - set(entries))
        self.assertFalse(missing, "add a '#N hint' to its topic in the index of gotchas.md: {}".format(missing))
        self.assertFalse(unknown, "the index of gotchas.md lists entries that don't exist: {}".format(unknown))


if __name__ == "__main__":
    unittest.main()
