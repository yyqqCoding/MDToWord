import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = await readFile(
  new URL("../src/lib/pagination.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const module = { exports: {} };
new Function("exports", "module", "require", compiled)(
  module.exports,
  module,
  require,
);
const { collectOffsetPages } = module.exports;

test("分页读取超过单页上限的全部运行", async () => {
  const rows = Array.from({ length: 1051 }, (_, id) => ({ id }));
  const calls = [];
  const result = await collectOffsetPages(async (offset, limit) => {
    calls.push([offset, limit]);
    return rows.slice(offset, offset + limit);
  });

  assert.equal(result.length, 1051);
  assert.deepEqual(calls, [
    [0, 1000],
    [1000, 1000],
  ]);
});
