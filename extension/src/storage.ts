import type { MarkdownDialog } from './types';

const SERVICE_URL_KEY = 'mdToWord.serviceUrl';
const DRAFT_KEY = 'mdToWord.draft';
const DIALOGS_KEY = 'mdToWord.dialogs';

const fallbackStorage = new Map<string, string>();

function hasChromeStorage(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome.storage?.local);
}

export async function loadServiceUrl(): Promise<string> {
  if (!hasChromeStorage()) {
    return fallbackStorage.get(SERVICE_URL_KEY) ?? 'http://127.0.0.1:8000';
  }

  const result = await chrome.storage.local.get(SERVICE_URL_KEY);
  return result[SERVICE_URL_KEY] ?? 'http://127.0.0.1:8000';
}

export async function saveServiceUrl(value: string): Promise<void> {
  if (!hasChromeStorage()) {
    fallbackStorage.set(SERVICE_URL_KEY, value);
    return;
  }

  await chrome.storage.local.set({ [SERVICE_URL_KEY]: value });
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

export async function loadDialogs(): Promise<MarkdownDialog[]> {
  if (!hasChromeStorage()) {
    return parseDialogs(fallbackStorage.get(DIALOGS_KEY), fallbackStorage.get(DRAFT_KEY));
  }

  const result = await chrome.storage.local.get([DIALOGS_KEY, DRAFT_KEY]);
  return parseDialogs(result[DIALOGS_KEY], result[DRAFT_KEY]);
}

export async function saveDialogs(value: MarkdownDialog[]): Promise<void> {
  const serialized = JSON.stringify(value);
  if (!hasChromeStorage()) {
    fallbackStorage.set(DIALOGS_KEY, serialized);
    return;
  }

  await chrome.storage.local.set({ [DIALOGS_KEY]: serialized });
}

function parseDialogs(value: unknown, legacyDraft: unknown): MarkdownDialog[] {
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.filter(isMarkdownDialog);
      }
    } catch {
      // Fall through to legacy draft migration.
    }
  }

  if (typeof legacyDraft === 'string' && legacyDraft.trim()) {
    return [
      {
        id: createStorageId(),
        title: 'Draft',
        markdown: legacyDraft,
      },
    ];
  }

  return [];
}

function isMarkdownDialog(value: unknown): value is MarkdownDialog {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const candidate = value as Partial<MarkdownDialog>;
  return typeof candidate.id === 'string' && typeof candidate.title === 'string' && typeof candidate.markdown === 'string';
}

function createStorageId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }

  return `dialog-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
