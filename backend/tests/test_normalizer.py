from app.normalizer import normalize_markdown


def test_normalize_inline_parentheses_formula_to_dollars():
    markdown = "质能方程是 \\(E = mc^2\\)。"

    result = normalize_markdown(markdown)

    assert result == "质能方程是 $E = mc^2$。"


def test_normalize_block_bracket_formula_to_double_dollars():
    markdown = "下面是积分：\n\\[\\int_0^1 x^2 dx = \\frac{1}{3}\\]\n结束。"

    result = normalize_markdown(markdown)

    assert result == "下面是积分：\n\n$$\n\\int_0^1 x^2 dx = \\frac{1}{3}\n$$\n\n结束。"


def test_preserve_existing_dollar_formulas_and_table():
    markdown = (
        "行内公式 $a^2 + b^2 = c^2$。\n\n"
        "| 名称 | 公式 |\n"
        "|---|---|\n"
        "| 动能 | $E_k = \\frac{1}{2}mv^2$ |\n"
    )

    result = normalize_markdown(markdown)

    assert result == markdown
