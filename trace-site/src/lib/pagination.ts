export async function collectOffsetPages<T>(
  loadPage: (offset: number, limit: number) => Promise<T[]>,
  pageSize = 1000,
): Promise<T[]> {
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new Error("pageSize must be a positive integer");
  }
  const all: T[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const page = await loadPage(offset, pageSize);
    all.push(...page);
    if (page.length < pageSize) return all;
  }
}
