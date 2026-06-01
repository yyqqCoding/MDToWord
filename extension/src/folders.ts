import type { MarkdownDialog, MarkdownFolder } from './types';

export function createId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createDialog(title: string, markdown: string): MarkdownDialog {
  return { id: createId(), title, markdown };
}

export function createFolder(name: string, dialogs: MarkdownDialog[] = []): MarkdownFolder {
  return { id: createId(), name, dialogs };
}

export function isMarkdownDialog(value: unknown): value is MarkdownDialog {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const candidate = value as Partial<MarkdownDialog>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.markdown === 'string'
  );
}

export function isMarkdownFolder(value: unknown): value is MarkdownFolder {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const candidate = value as Partial<MarkdownFolder>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.name === 'string' &&
    Array.isArray(candidate.dialogs) &&
    candidate.dialogs.every(isMarkdownDialog)
  );
}

function parseJsonValue(value: unknown): unknown {
  if (typeof value !== 'string') {
    return value;
  }
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

export function migrateToFolders(
  rawFolders: unknown,
  rawDialogs: unknown,
  rawDraft: unknown,
): MarkdownFolder[] {
  const parsedFolders = parseJsonValue(rawFolders);
  if (Array.isArray(parsedFolders)) {
    return parsedFolders.filter(isMarkdownFolder);
  }

  const parsedDialogs = parseJsonValue(rawDialogs);
  if (Array.isArray(parsedDialogs)) {
    const dialogs = parsedDialogs.filter(isMarkdownDialog);
    if (dialogs.length > 0) {
      return [createFolder('默认', dialogs)];
    }
  }

  if (typeof rawDraft === 'string' && rawDraft.trim()) {
    return [createFolder('默认', [createDialog('Draft', rawDraft)])];
  }

  return [];
}

export function moveDialogBetweenFolders(
  folders: MarkdownFolder[],
  dialogId: string,
  targetFolderId: string,
): MarkdownFolder[] {
  const source = folders.find((folder) => folder.dialogs.some((dialog) => dialog.id === dialogId));
  if (!source || source.id === targetFolderId) {
    return folders;
  }

  const dialog = source.dialogs.find((item) => item.id === dialogId);
  if (!dialog) {
    return folders;
  }

  return folders.map((folder) => {
    if (folder.id === source.id) {
      return { ...folder, dialogs: folder.dialogs.filter((item) => item.id !== dialogId) };
    }
    if (folder.id === targetFolderId) {
      return { ...folder, dialogs: [...folder.dialogs, dialog] };
    }
    return folder;
  });
}
