export interface DailyMedResponse<T = any> {
  data: T;
  status: number;
  statusText: string;
}

export interface PaginatedSPLResponse {
  data: SPLDocument[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

// Re-export SPLDocument for convenience in this file
import type { SPLDocument } from './spl-types.js';
export type { SPLDocument };