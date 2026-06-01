import type { MarkdownFolder } from './types';
import { migrateToFolders } from './folders';

const DRAFT_KEY = 'mdToWord.draft';
const DIALOGS_KEY = 'mdToWord.dialogs';
const FOLDERS_KEY = 'mdToWord.folders';
const ONBOARDING_COMPLETED_KEY = 'mdToWord.onboardingCompleted';

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
