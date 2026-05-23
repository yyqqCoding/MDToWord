export type ServiceStatus = 'unknown' | 'available' | 'unavailable';

export interface ConvertOptions {
  filename: string;
}

export interface ConvertRequest {
  title: string;
  markdown: string;
  options: ConvertOptions;
}
