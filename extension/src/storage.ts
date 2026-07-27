import type { MarkdownFolder } from './types';
import { migrateToFolders } from './folders';

const DRAFT_KEY = 'mdToWord.draft';
const DIALOGS_KEY = 'mdToWord.dialogs';
const FOLDERS_KEY = 'mdToWord.folders';
const ONBOARDING_COMPLETED_KEY = 'mdToWord.onboardingCompleted';
const PREVIEW_WINDOWS_KEY = 'mdToWord.previewWindows';

interface PreviewWindowRef {
  windowId: number;
  tabId?: number;
}

const fallbackStorage = new Map<string, string>();

function hasChromeStorage(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome.storage?.local);
}

export async function loadDraft(): Promise<string> {
  if (!hasChromeStorage()) {
    return fallbackStorage.get(DRAFT_KEY) ?? '';
  }

  const result = await chrome.storage.local.get(DRAFT_KEY);
  return result[DRAFT_KEY] ?? '';
}

export async function saveDraft(value: string): Promise<void> {
  if (!hasChromeStorage()) {
    fallbackStorage.set(DRAFT_KEY, value);
    return;
  }

  await chrome.storage.local.set({ [DRAFT_KEY]: value });
}

export async function loadFolders(): Promise<MarkdownFolder[]> {
  if (!hasChromeStorage()) {
    return migrateToFolders(
      fallbackStorage.get(FOLDERS_KEY),
      fallbackStorage.get(DIALOGS_KEY),
      fallbackStorage.get(DRAFT_KEY),
    );
  }

  const result = await chrome.storage.local.get([FOLDERS_KEY, DIALOGS_KEY, DRAFT_KEY]);
  return migrateToFolders(result[FOLDERS_KEY], result[DIALOGS_KEY], result[DRAFT_KEY]);
}

export async function saveFolders(value: MarkdownFolder[]): Promise<void> {
  const serialized = JSON.stringify(value);
  if (!hasChromeStorage()) {
    fallbackStorage.set(FOLDERS_KEY, serialized);
    return;
  }

  await chrome.storage.local.set({ [FOLDERS_KEY]: serialized });
}

export async function loadOnboardingCompleted(): Promise<boolean> {
  if (!hasChromeStorage()) {
    return fallbackStorage.get(ONBOARDING_COMPLETED_KEY) === 'true';
  }

  const result = await chrome.storage.local.get(ONBOARDING_COMPLETED_KEY);
  return result[ONBOARDING_COMPLETED_KEY] === true;
}

export async function saveOnboardingCompleted(value: boolean): Promise<void> {
  if (!hasChromeStorage()) {
    fallbackStorage.set(ONBOARDING_COMPLETED_KEY, String(value));
    return;
  }

  await chrome.storage.local.set({ [ONBOARDING_COMPLETED_KEY]: value });
}

async function loadPreviewWindows(): Promise<Record<string, PreviewWindowRef>> {
  if (!hasChromeStorage()) {
    const raw = fallbackStorage.get(PREVIEW_WINDOWS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, PreviewWindowRef>) : {};
  }

  const result = await chrome.storage.local.get(PREVIEW_WINDOWS_KEY);
  return (result[PREVIEW_WINDOWS_KEY] as Record<string, PreviewWindowRef>) ?? {};
}

async function savePreviewWindows(map: Record<string, PreviewWindowRef>): Promise<void> {
  if (!hasChromeStorage()) {
    fallbackStorage.set(PREVIEW_WINDOWS_KEY, JSON.stringify(map));
    return;
  }

  await chrome.storage.local.set({ [PREVIEW_WINDOWS_KEY]: map });
}

/**
 * Look up the preview window/tab previously opened for a folder, so the side
 * panel can reuse it (one preview window per folder) instead of stacking
 * duplicate popups. Returns undefined when none is recorded.
 */
export async function getPreviewWindow(folderId: string): Promise<PreviewWindowRef | undefined> {
  const map = await loadPreviewWindows();
  return map[folderId];
}

/** Record the window/tab that now previews a folder. */
export async function rememberPreviewWindow(
  folderId: string,
  windowId: number,
  tabId?: number,
): Promise<void> {
  const map = await loadPreviewWindows();
  map[folderId] = { windowId, tabId };
  await savePreviewWindows(map);
}

/** Drop a folder's remembered window (e.g. once it has been closed). */
export async function forgetPreviewWindow(folderId: string): Promise<void> {
  const map = await loadPreviewWindows();
  if (map[folderId]) {
    delete map[folderId];
    await savePreviewWindows(map);
  }
}

/**
 * Subscribe to folder changes made in other extension contexts (e.g. the
 * side panel and the standalone preview window share one chrome.storage).
 * Returns an unsubscribe function. No-op when chrome.storage is unavailable.
 */
export function subscribeToFolders(listener: (folders: MarkdownFolder[]) => void): () => void {
  if (!hasChromeStorage() || !chrome.storage.onChanged) {
    return () => {};
  }

  const handler = (
    changes: Record<string, chrome.storage.StorageChange>,
    areaName: string,
  ) => {
    if (areaName !== 'local' || !changes[FOLDERS_KEY]) {
      return;
    }
    const raw = changes[FOLDERS_KEY].newValue;
    listener(migrateToFolders(raw, undefined, undefined));
  };

  chrome.storage.onChanged.addListener(handler);
  return () => chrome.storage.onChanged.removeListener(handler);
}
