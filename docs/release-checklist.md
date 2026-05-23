# Release Checklist

- [ ] Backend tests pass with Pandoc available through either system `pandoc` or `pypandoc_binary`.
- [ ] Extension build passes.
- [ ] Extension dependency audit has no moderate-or-higher vulnerabilities.
- [ ] Docker image builds in an environment with Docker available.
- [ ] `/health` returns `{"status":"ok","engine":"pandoc"}`.
- [ ] `/convert` returns a valid `.docx`.
- [ ] Generated `.docx` contains Word OMML math nodes.
- [ ] Generated `.docx` contains Word table nodes.
- [ ] Sample `.docx` opens in Microsoft Word.
- [ ] Word formulas are editable equations.
- [ ] Markdown tables become Word-native tables.
- [ ] Extension can save service URL.
- [ ] Extension can export using local backend.
- [ ] Render deployment health check passes.
