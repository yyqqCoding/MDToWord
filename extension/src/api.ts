import type { ConvertRequest } from './types';

const DOCX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export interface ConversionApiError {
  error: string;
  message: string;
  details: string[];
}

export async function checkHealth(serviceUrl: string): Promise<boolean> {
  try {
    const response = await fetch(`${trimTrailingSlash(serviceUrl)}/health`);
    if (!response.ok) {
      return false;
    }

    const body = await response.json();
    return body.status === 'ok' && body.engine === 'pandoc';
  } catch {
    return false;
  }
}

export async function convertToDocx(serviceUrl: string, request: ConvertRequest): Promise<Blob> {
  const response = await fetch(`${trimTrailingSlash(serviceUrl)}/convert`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    let apiError: ConversionApiError = {
      error: 'conversion_failed',
      message: `Conversion failed with status ${response.status}.`,
      details: [],
    };

    try {
      apiError = await response.json();
    } catch {
      // Use fallback error when the backend returns a non-JSON response.
    }

    throw new Error([apiError.message, ...apiError.details].filter(Boolean).join('\n'));
  }

  return response.blob();
}

export function downloadDocx(blob: Blob, filename: string): void {
  const docxBlob = blob.type === DOCX_MEDIA_TYPE ? blob : new Blob([blob], { type: DOCX_MEDIA_TYPE });
  const url = URL.createObjectURL(docxBlob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function trimTrailingSlash(value: string): string {
  return value.trim().replace(/\/+$/, '');
}
