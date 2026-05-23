const SERVICE_URL_KEY = 'mdToWord.serviceUrl';
const DRAFT_KEY = 'mdToWord.draft';

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
