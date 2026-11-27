"""Transport and Markdown-boundary tests for native WeChat replies."""

import re
import unittest
from dataclasses import dataclass

from wechat2dot.sync.layout_split import split_layout


@dataclass(frozen=True)
class Block:
    kind: str
    text: str


PROTECTED = re.compile(r"``[^\x60]+``|https?://[^\s]+")


class NativeLayoutSplit(unittest.TestCase):
    def assert_safe(self, parts):
        self.assertTrue(all(len(part) <= 3800 and len(part.encode()) <= 8192 for part in parts))

    def test_short_blocks_share_one_message_and_keep_original_whitespace(self):
        blocks = [Block("paragraph", "  可以，今晚八点。\n"), Block("paragraph", "\n见面聊。  ")]
        self.assertEqual(split_layout(blocks, keep_together=PROTECTED), ["".join(b.text for b in blocks)])

    def test_heading_follows_a_near_limit_paragraph_with_its_body(self):
        first = "x" * 3790 + "\n\n"
        heading = "## 明天\n"
        body = "按之前约定的步骤处理。"
        blocks = [Block("paragraph", first), Block("heading", heading), Block("paragraph", body)]
        self.assertEqual(split_layout(blocks, keep_together=PROTECTED), [first, heading + body])

    def test_heading_stays_with_start_of_long_paragraph(self):
        heading = "# 结果\n"
        paragraph = "文" * 3000
        parts = split_layout(
            [Block("heading", heading), Block("paragraph", paragraph)], keep_together=PROTECTED
        )
        self.assertTrue(parts[0].startswith(heading + "文"))
        self.assertEqual("".join(parts), heading + paragraph)
        self.assert_safe(parts)

    def test_markdown_table_stays_whole_at_boundary(self):
        first = "x" * 3790 + "\n\n"
        table = "| 编号 | 状态 |\n| --- | --- |\n| 1 | 已完成 |"
        self.assertEqual(
            split_layout([Block("paragraph", first), Block("table", table)], keep_together=PROTECTED),
            [first, table],
        )

    def test_long_list_splits_between_root_items(self):
        first = "7. " + "x" * 3600 + "\n   - 子步骤\n"
        second = "8. 下一项\n   " + "y" * 300 + "\n"
        self.assertEqual(
            split_layout([Block("list", first + second)], keep_together=PROTECTED), [first, second]
        )

    def test_soft_break_inside_an_inline_span_is_not_a_cut(self):
        first = "文" * 2720 + "\n"
        span = "``原始内容\n另一行和这里的尾部``\n"
        self.assertEqual(
            split_layout([Block("paragraph", first + span)], keep_together=PROTECTED), [first, span]
        )

    def test_long_fence_keeps_source_lines_language_and_final_closer(self):
        opening = "~~~~python example\r\n"
        intermediate_close = "~~~~\r\n"
        final_close = "~~~~~ \r\n"
        lines = ["    值" * 250 + "\r\n"] * 7 + ["return 值\r\n"]
        code = "".join(lines)
        parts = split_layout(
            [Block("fence", opening + code + final_close + "\r\n")], keep_together=PROTECTED
        )
        self.assertGreater(len(parts), 1)
        bodies = []
        for part in parts[:-1]:
            self.assertTrue(part.startswith(opening))
            self.assertTrue(part.endswith(intermediate_close))
            bodies.append(part[len(opening):-len(intermediate_close)])
        self.assertTrue(parts[-1].startswith(opening))
        final_suffix = final_close + "\r\n"
        self.assertTrue(parts[-1].endswith(final_suffix))
        bodies.append(parts[-1][len(opening):-len(final_suffix)])
        self.assertEqual("".join(bodies), code)
        self.assert_safe(parts)

    def test_an_individually_oversized_code_line_is_reassemblable(self):
        source = "```text\n" + "🙂" * 2500 + "\nnext line\n```"
        parts = split_layout([Block("fence", source)], keep_together=PROTECTED)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), source)
        self.assert_safe(parts)

    def test_fitting_fences_in_oversized_containers_are_atomic(self):
        for kind, first, fence in (
            ("quote", "> " + "x" * 3775 + "\n>\n", "> ```text\n> x = 1\n> y = 2\n> ```\n"),
            ("list", "7. " + "文" * 2720 + "\n\n", "   ```\n   x = '文'\n   y = '字'\n   ```\n"),
        ):
            with self.subTest(kind=kind):
                parts = split_layout([Block(kind, first + fence)], keep_together=PROTECTED)
                self.assertEqual(parts, [first, fence])
                self.assert_safe(parts)


if __name__ == "__main__":
    unittest.main()
