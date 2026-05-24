export type ServiceStatus = 'unknown' | 'available' | 'unavailable';

export interface MarkdownDialog {
  id: string;
  title: string;
  markdown: string;
}

export interface ConvertOptions {
  filename: string;
}

export interface ConvertRequest {
  title: string;
  markdown: string;
  options: ConvertOptions;
}
