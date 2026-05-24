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


def test_escape_visible_set_braces_without_breaking_tex_grouping():
    markdown = (
        "将 ({z_{LH},z_{HL},z_{HH}}) 定义为信道 B。\n\n"
        "[\n"
        "\\langle z_{LL}^{w}-z_{LL},{z_{LH}^{w}-z_{LH},z_{HL}^{w}-z_{HL}}\\rangle=0.\n"
        "]\n\n"
        "标准公式 $\\frac{1}{N}\\mathbb{I}(z_{LL})$ 保持不变。"
    )

    result = normalize_markdown(markdown)

    assert result == (
        "将 $\\{z_{LH},z_{HL},z_{HH}\\}$ 定义为信道 B。\n\n"
        "$$\n"
        "\\langle z_{LL}^{w}-z_{LL},\\{z_{LH}^{w}-z_{LH},z_{HL}^{w}-z_{HL}\\}\\rangle=0.\n"
        "$$\n\n"
        "标准公式 $\\frac{1}{N}\\mathbb{I}(z_{LL})$ 保持不变。"
    )


def test_escape_set_braces_after_relation_commands():
    markdown = "给定版权载荷 (m\\in{0,1}^{L})，码字 (b\\in{0,1}^{N})。"

    result = normalize_markdown(markdown)

    assert result == "给定版权载荷 $m\\in\\{0,1\\}^{L}$，码字 $b\\in\\{0,1\\}^{N}$。"


def test_preserve_underbrace_arguments_while_escaping_sets():
    markdown = (
        "$$\n"
        "\\text{ELBO}=\\underbrace{\\mathbb E_q[\\log p_\\theta(x_T)]}_{\\text{先验项}}\n"
        "+\\sum_{t=1}^T \\underbrace{\\mathbb E_q\\left[\\log\\frac{p_\\theta(x_{t-1}\\mid x_t)}"
        "{q(x_{t-1}\\mid x_t,x_0)}\\right]}_{\\text{反向匹配项}}\n"
        "+m\\in{0,1}^{L}\n"
        "$$"
    )

    result = normalize_markdown(markdown)

    assert "\\underbrace{" in result
    assert "\\underbrace\\{" not in result
    assert "m\\in\\{0,1\\}^{L}" in result


def test_preserve_second_frac_argument_with_comma():
    markdown = "$\\frac{p_\\theta(x_{t-1}\\mid x_t)}{q(x_{t-1}\\mid x_t,x_0)}$"

    result = normalize_markdown(markdown)

    assert result == markdown


def test_expand_parentheses_around_tall_function_arguments():
    markdown = (
        "$$\n"
        "\\big[\\|\\epsilon-\\epsilon_\\theta("
        "\\underbrace{\\sqrt{\\bar\\alpha_t}x_0+\\sqrt{1-\\bar\\alpha_t}\\epsilon}_{x_t},t"
        ")\\|^2\\big]\n"
        "$$"
    )

    result = normalize_markdown(markdown)

    assert "\\epsilon_\\theta\\left(\\begin{matrix}\\underbrace" in result
    assert ",t\\end{matrix}\\right)" in result


def test_wrap_underbrace_function_arguments_for_word_delimiter_height():
    markdown = (
        "$$\n"
        "\\big[\\|\\epsilon-\\epsilon_\\theta("
        "\\underbrace{\\sqrt{\\bar\\alpha_t}x_0+\\sqrt{1-\\bar\\alpha_t}\\epsilon}_{x_t},t"
        ")\\|^2\\big]\n"
        "$$"
    )

    result = normalize_markdown(markdown)

    assert "\\epsilon_\\theta\\left(\\begin{matrix}\\underbrace" in result
    assert ",t\\end{matrix}\\right)" in result


def test_do_not_expand_existing_sized_delimiters():
    markdown = (
        "$$\n"
        "q(x_t\\mid x_{t-1})=\\mathcal N\\big(x_t;\\ \\sqrt{1-\\beta_t}\\,x_{t-1}\\big)\n"
        "\\tilde\\mu_t=\\frac{1}{\\sqrt{\\alpha_t}}\\left(x_t-\\frac{\\beta_t}{\\sqrt{1-\\bar\\alpha_t}}\\epsilon\\right)\n"
        "$$"
    )

    result = normalize_markdown(markdown)

    assert "\\big\\left" not in result
    assert "\\left\\left" not in result
    assert "\\right\\right" not in result
    assert "\\mathcal N\\big(" in result
    assert "\\left(x_t-" in result


def test_repair_single_backslash_line_breaks_in_cases_environment():
    markdown = (
        "[\n"
        "z_i^{w}=\n"
        "\\begin{cases}\n"
        "|z_i|, & b_j=0,\\\n"
        "-|z_i|, & b_j=1.\n"
        "\\end{cases}\n"
        "]"
    )

    result = normalize_markdown(markdown)

    assert result == (
        "\n\n$$\n"
        "z_i^{w}=\n"
        "\\begin{cases}\n"
        "|z_i|, & b_j=0,\\\\\n"
        "-|z_i|, & b_j=1.\n"
        "\\end{cases}\n"
        "$$\n\n"
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
