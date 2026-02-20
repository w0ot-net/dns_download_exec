from __future__ import absolute_import

import unittest

from dnsdle.stager_minify import minify
from dnsdle.stager_template import build_stager_template


class StagerMinifyTests(unittest.TestCase):

    # -- Pass 1: comment and blank line removal --

    def test_pass1_strips_comments_and_blanks(self):
        source = "a = 1\n\n# this is a comment\nb = 2\n    # indented comment\nc = 3"
        result = minify(source)
        self.assertNotIn("# this is a comment", result)
        self.assertNotIn("# indented comment", result)
        self.assertIn("a", result)
        self.assertIn("b", result)
        self.assertIn("c", result)

    def test_pass1_preserves_non_comment_lines(self):
        source = "x = 1\ny = 2"
        result = minify(source)
        # Both assignments should survive (possibly joined).
        self.assertIn("x", result)
        self.assertIn("y", result)

    # -- Pass 2: variable renaming --

    def test_pass2_renames_known_identifier(self):
        source = "DOMAIN_LABELS = 1"
        result = minify(source)
        self.assertNotIn("DOMAIN_LABELS", result)
        self.assertIn("k", result)

    def test_pass2_deterministic_rename(self):
        source = "DOMAIN_LABELS = 1\nDOMAIN_LABELS = 2"
        r1 = minify(source)
        r2 = minify(source)
        self.assertEqual(r1, r2)

    # -- Pass 3: indent reduction --

    def test_pass3_reduces_4space_indent(self):
        source = "if True:\n    x = 1\n        y = 2"
        result = minify(source)
        lines = result.split("\n")
        # "if True:" should be at indent 0
        # "x = 1" should be at indent 1 (single space)
        found_x = [ln for ln in lines if "x" in ln]
        self.assertTrue(found_x)
        self.assertTrue(found_x[0].startswith(" "))
        # Should not have 4-space indent
        self.assertFalse(found_x[0].startswith("    "))

    def test_pass3_preserves_non_aligned_indent(self):
        # 3-space indent is not a multiple of 4, so preserved verbatim.
        source = "if True:\n   x = 1"
        result = minify(source)
        lines = result.split("\n")
        x_line = [ln for ln in lines if "x" in ln][0]
        self.assertTrue(x_line.startswith("   "))

    # -- Pass 4: semicolon joining --

    def test_pass4_joins_same_indent_non_block(self):
        source = "a = 1\nb = 2\nc = 3"
        result = minify(source)
        self.assertIn(";", result)

    def test_pass4_does_not_join_block_starters(self):
        source = "a = 1\nif True:\n    pass"
        result = minify(source)
        lines = result.split("\n")
        # "if True:" should be on its own line, not joined with "a = 1"
        if_lines = [ln for ln in lines if "if" in ln]
        self.assertTrue(if_lines)
        self.assertFalse(if_lines[0].strip().startswith("a"))

    def test_pass4_does_not_join_different_indent(self):
        source = "a = 1\n    b = 2"
        result = minify(source)
        # Since indent differs, they should remain on separate lines.
        lines = result.split("\n")
        self.assertTrue(len(lines) >= 2)

    # -- Determinism --

    def test_determinism(self):
        source = "x = 1\ny = 2\nif True:\n    z = 3"
        self.assertEqual(minify(source), minify(source))

    # -- Full template round-trip --

    def test_full_template_compiles_after_minify(self):
        template = build_stager_template()
        replacements = {
            "DOMAIN_LABELS": ("example", "com"),
            "FILE_TAG": "tag001",
            "FILE_ID": "file001",
            "PUBLISH_VERSION": "v1",
            "TOTAL_SLICES": 2,
            "COMPRESSED_SIZE": 100,
            "PLAINTEXT_SHA256_HEX": "a" * 64,
            "SLICE_TOKENS": ("tok0", "tok1"),
            "RESPONSE_LABEL": "r",
            "DNS_EDNS_SIZE": 1232,
        }
        source = template
        for key, value in replacements.items():
            source = source.replace("@@%s@@" % key, repr(value))

        minified = minify(source)
        # Must compile without SyntaxError.
        compile(minified, "<stager-test>", "exec")


if __name__ == "__main__":
    unittest.main()
