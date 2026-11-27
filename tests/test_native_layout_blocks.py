import unittest

from wechat2dot.sync.layout_blocks import layout_blocks


def render(source, transform=lambda text: text):
    return "".join(
        block.text for block in layout_blocks(source, render_links=transform)
    )


class NativeLayoutBlocks(unittest.TestCase):
    def test_short_text_and_existing_markdown_sections_keep_original_spacing(self):
        short = "可以，今晚八点。"
        self.assertEqual(render(short), short)
        markdown = (
            "\n## 当前进度\r\n\r\n**重点**和*English*，保留 ~~删除线~~。\r\n"
            "\r\n3. 第三项\r\n   - 子项\r\n\r\n> 引用\r\n> > 嵌套\r\n\r\n***"
        )
        self.assertEqual(render(markdown), markdown)
        self.assertEqual(render("第一行\u2028仍然在这一行"), "第一行\u2028仍然在这一行")

    def test_known_structures_adapt_without_adding_template_fields(self):
        self.assertEqual(render("题目\n====\n\n副题\n----"), "# 题目\n\n## 副题")
        for heading in ("第一行  \n第二行\n===", "第一行\\\n第二行\n==="):
            self.assertEqual(render(heading), heading)
        self.assertEqual(
            render("结果：完成\n下一步：复核"), "**结果**：完成\n**下一步**：复核"
        )
        self.assertEqual(render("下一步：稍等一下。"), "下一步：稍等一下。")
        self.assertEqual(
            render("- [X] 已发\n  - [ ] 待截图\n\n> [!NOTE]\n> 引用"),
            "- （已完成） 已发\n  - （待办） 待截图\n\n> **提示**\n> 引用",
        )
        self.assertEqual(
            render("字面 [x] 备忘；`- [x] 保留`"), "字面 [x] 备忘；`- [x] 保留`"
        )

    def test_table_width_keeps_mobile_comparison_and_flattens_wide_records(self):
        short = (
            "| 左对齐 | 居中 | 右对齐 |\n| :--- | :---: | ---: |\n"
            "| 短字 | 甲 | 17 |\n| 较长文字 | 乙 | 8 |\n"
        )
        narrow_escape = "| 字段 | 值 |\n| --- | --- |\n| 转义 | A\\|B |\n| 空值 | |\n"
        self.assertEqual(render(short), short)
        self.assertEqual(render(narrow_escape), narrow_escape)

        wide = (
            "| 模块 | 结果 | 耗时 | 下一步 |\n| --- | --- | --- | --- |\n"
            "| 甲 | A\\|B | | `x\\|y` |\n| 乙 | 完成 | 18 秒 | 检查手机 |"
        )
        self.assertEqual(
            render(wide),
            "- **模块** 甲\n  - **结果** A|B\n  - **耗时**\n  - **下一步** `x|y`\n\n"
            "- **模块** 乙\n  - **结果** 完成\n  - **耗时** 18 秒\n  - **下一步** 检查手机",
        )
        long = "| 名称 | 描述 |\n|---|---|\n| 甲 | " + "长" * 45 + " |"
        self.assertTrue(render(long).startswith("- **名称** 甲\n  - **描述** "))
        ragged = "| 名称 | 值 |\n| --- | --- |\n| 甲 | 乙 | 额外值 |"
        self.assertEqual(render(ragged), ragged)

    def test_resolves_web_references_and_retains_image_title_in_visible_text(self):
        definition = 'https://example.com/路径_(2.9) "图题"'
        source = (
            f"[完整][full] [折叠][] [简写] ![引用图][full]\n\n[full]: {definition}\n"
            "[折叠]: https://example.com/02\n[简写]: <https://example.com/03>\n"
        )
        self.assertEqual(
            render(source),
            '[完整](<https://example.com/路径_(2.9)> "图题") [折叠](<https://example.com/02>) '
            "[简写](<https://example.com/03>) [引用图](<https://example.com/路径_(2.9)>)（图题）\n\n",
        )
        self.assertEqual(
            render('![图](https://example.com/p.png "示例")'),
            '[图](https://example.com/p.png "示例")（示例）',
        )
        self.assertEqual(
            render("![](https://example.com/p.png)"),
            "[图片](https://example.com/p.png)",
        )
        self.assertEqual(
            render("![预览 [说明](https://example.net)](https://example.com/p.png)"),
            "[预览 说明](https://example.com/p.png)",
        )
        self.assertEqual(
            render("[未知][missing]\n\n[用途]: relative/file"),
            "[未知][missing]\n\n[用途]: relative/file",
        )

    def test_only_safe_escapes_and_entities_convert_in_prose(self):
        self.assertEqual(
            render(
                "&amp; &lt; &#169; &#x4E2D; \\[方括号\\] \\*字面\\* `&amp;`\\\n下一行"
            ),
            "& < © 中 [方括号] \\*字面\\* `&amp;`\n下一行",
        )
        self.assertEqual(render("保留结尾\\\n"), "保留结尾\\\n")
        self.assertEqual(
            render("https://example.com/?x=1&amp;y=2"),
            "https://example.com/?x=1&amp;y=2",
        )
        self.assertEqual(
            render("公式 $x^2 + 1$，邮件 <a@example.com>。"),
            "公式 $x^2 + 1$，邮件 <a@example.com>。",
        )

    def test_simple_footnote_and_source_examples(self):
        self.assertEqual(
            render("正文[^note] 和 `[^note]`。\n\n[^note]: 这是脚注正文。"),
            "正文（注 1） 和 `[^note]`。\n\n**注 1** 这是脚注正文。",
        )
        self.assertEqual(
            render("字面 `[^note]`\n\n[^note]: 示例。"),
            "字面 `[^note]`\n\n[^note]: 示例。",
        )
        diagram = "```mermaid\nflowchart LR\n    A --> B\n```"
        self.assertEqual(render(diagram), diagram)

    def test_callback_never_receives_block_code_even_inside_quote_or_list(self):
        source = (
            "外层 QUOTE\n\n> ~~~text\n> [^a] QUOTE\n> ~~~\n\n"
            "- 列表 QUOTE\n\n  ```sh\n  QUOTE\n  ```\n\n    "
        )
        self.assertEqual(
            render(source, lambda text: text.replace("QUOTE", "结果")),
            source.replace("外层 QUOTE", "外层 结果").replace(
                "列表 QUOTE", "列表 结果"
            ),
        )
        self.assertEqual(
            render("    first\r\n        second\r\n"),
            "```\r\nfirst\r\n    second\r\n```\r\n",
        )


if __name__ == "__main__":
    unittest.main()
