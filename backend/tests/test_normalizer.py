from app.normalizer import normalize_markdown


def test_normalize_inline_parentheses_formula_to_dollars():
    markdown = "质能方程是 \\(E = mc^2\\)。"

    result = normalize_markdown(markdown)

    assert result == "质能方程是 $E = mc^2$。"


def test_normalize_block_bracket_formula_to_double_dollars():
    markdown = "下面是积分：\n\\[\\int_0^1 x^2 dx = \\frac{1}{3}\\]\n结束。"

    result = normalize_markdown(markdown)

    assert result == "下面是积分：\n\n$$\n\\int_0^1 x^2 dx = \\frac{1}{3}\n$$\n\n结束。"


def test_normalize_bare_block_bracket_formula_to_double_dollars():
    markdown = "本文首先对其进行分解：\n\n[\n(z_{LL},z_{LH})=DWT(z_T),\n]\n\n结束。"

    result = normalize_markdown(markdown)

    assert result == "本文首先对其进行分解：\n\n$$\n(z_{LL},z_{LH})=DWT(z_T),\n$$\n\n结束。"


def test_normalize_ai_parenthesized_math_fragments_to_dollars():
    markdown = "给定初始潜噪声 (z_T)，其中 (K_A) 生成模板，普通说明 (Render) 保持文本。"

    result = normalize_markdown(markdown)

    assert result == "给定初始潜噪声 $z_T$，其中 $K_A$ 生成模板，普通说明 (Render) 保持文本。"


def test_repair_ai_asterisk_subscripts_inside_math_only():
    markdown = (
        "正文里的 a*b 保持不变。\n\n"
        "[\n"
        "S_A=\\frac{\\langle \\tilde{z}*{LL}-\\mu*{\\tilde{z}}\\rangle}\n"
        "{|\\tilde{z}*{LL}|*2}.\n"
        "]\n\n"
        "行内公式 (\\tilde{M}*i=1)。"
    )

    result = normalize_markdown(markdown)

    assert result == (
        "正文里的 a*b 保持不变。\n\n"
        "$$\n"
        "S_A=\\frac{\\langle \\tilde{z}_{LL}-\\mu_{\\tilde{z}}\\rangle}\n"
        "{|\\tilde{z}_{LL}|_2}.\n"
        "$$\n\n"
        "行内公式 $\\tilde{M}_i=1$。"
    )


def test_preserve_existing_dollar_formulas_and_table():
    markdown = (
        "行内公式 $a^2 + b^2 = c^2$。\n\n"
        "| 名称 | 公式 |\n"
        "|---|---|\n"
        "| 动能 | $E_k = \\frac{1}{2}mv^2$ |\n"
    )

    result = normalize_markdown(markdown)

    assert result == markdown
