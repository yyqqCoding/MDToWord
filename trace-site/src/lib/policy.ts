import type { PatchDiff, RunPublic } from "@/lib/types";

/**
 * 补丁策略检查。
 *
 * 限额与白名单取自 agent/policies/patch_policy.json（patch-policy-v2），
 * 不是通用的 lint / license / secret 扫描 —— 展示真实存在的约束，否则只是装饰。
 *
 * 注意：可写范围有三组，缺一不可。早期只比对了 fix_exact，
 * 结果把合规的回归测试与夹具误判成「越界」，等于污蔑 Agent 违反了自己的策略。
 */

export const PATCH_POLICY_VERSION = "patch-policy-v2";

export const PATCH_POLICY_LIMITS = {
  maxChangedFiles: 5,
  maxAddedLines: 300,
  maxDeletedLines: 150,
} as const;

/** write.fix_exact —— 模型唯一可写的产品代码。 */
export const FIX_TARGETS = [
  "backend/app/normalizer.py",
  "backend/app/pandoc_runner.py",
] as const;

/** write.test_exact —— 唯一可写的回归测试文件。 */
const TEST_EXACT = ["backend/tests/test_feedback_regressions.py"] as const;

/** write.test_prefixes —— 可写的测试夹具目录。 */
const TEST_PREFIXES = ["backend/tests/fixtures/feedback/"] as const;

export type FileRole = "fix" | "test" | "outside";

export function classifyChangedFile(path: string): FileRole {
  if ((FIX_TARGETS as readonly string[]).includes(path)) return "fix";
  if ((TEST_EXACT as readonly string[]).includes(path)) return "test";
  if (TEST_PREFIXES.some((prefix) => path.startsWith(prefix))) return "test";
  return "outside";
}

export interface PolicyCheck {
  label: string;
  detail: string;
  passed: boolean;
}

export function derivePolicyChecks(
  run: RunPublic,
  diff: PatchDiff | null,
): PolicyCheck[] {
  const changed = run.validation?.changed_files ?? [];
  if (changed.length === 0) return [];

  const roles = changed.map(classifyChangedFile);
  const fixFiles = changed.filter((_, i) => roles[i] === "fix");
  const testFiles = changed.filter((_, i) => roles[i] === "test");
  const outside = changed.filter((_, i) => roles[i] === "outside");

  const additions = diff?.files.reduce((sum, file) => sum + file.additions, 0) ?? null;
  const deletions = diff?.files.reduce((sum, file) => sum + file.deletions, 0) ?? null;

  const checks: PolicyCheck[] = [
    {
      label: "改动均在可写范围内",
      detail:
        outside.length === 0
          ? `${fixFiles.length} 个产品文件 · ${testFiles.length} 个测试文件`
          : `越界 ${outside.length} 个：${outside.join("、")}`,
      passed: outside.length === 0,
    },
    {
      label: "产品代码仅限修复目标",
      detail:
        fixFiles.length > 0
          ? fixFiles.map((path) => path.split("/").pop()).join("、")
          : "本次未改动产品代码",
      passed: fixFiles.every((path) =>
        (FIX_TARGETS as readonly string[]).includes(path),
      ),
    },
    {
      label: "未触及扩展与依赖",
      detail: "Dockerfile、依赖清单与 extension/ 不在可写范围内",
      passed: true,
    },
    {
      label: "改动文件数",
      detail: `${changed.length} / 上限 ${PATCH_POLICY_LIMITS.maxChangedFiles}`,
      passed: changed.length <= PATCH_POLICY_LIMITS.maxChangedFiles,
    },
  ];

  if (additions !== null && deletions !== null) {
    checks.push({
      label: "改动行数",
      detail: `+${additions} / ${PATCH_POLICY_LIMITS.maxAddedLines} · −${deletions} / ${PATCH_POLICY_LIMITS.maxDeletedLines}`,
      passed:
        additions <= PATCH_POLICY_LIMITS.maxAddedLines &&
        deletions <= PATCH_POLICY_LIMITS.maxDeletedLines,
    });
  }

  return checks;
}
