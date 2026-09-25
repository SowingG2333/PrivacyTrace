import type { SPLDocument } from '../types/index.js';

export interface PaginationResult<T> {
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export function paginateResults<T>(allResults: T[], page: number, pageSize: number): PaginationResult<T> {
  const totalResults = allResults.length;
  const totalPages = Math.ceil(totalResults / pageSize);
  
  // Calculate pagination indices
  const startIndex = (page - 1) * pageSize;
  const endIndex = startIndex + pageSize;
  
  // Get the paginated slice
  const paginatedData = allResults.slice(startIndex, endIndex);

  return {
    data: paginatedData,
    pagination: {
      page,
      pageSize,
      totalResults,
      totalPages,
      hasNextPage: page < totalPages,
      hasPreviousPage: page > 1,
    },
  };
}

export function validatePaginationParams(page: number, pageSize: number, maxPageSize: number = 200): void {
  if (page < 1) {
    throw new Error("Page number must be 1 or greater");
  }
  if (pageSize < 1 || pageSize > maxPageSize) {
    throw new Error(`Page size must be between 1 and ${maxPageSize}`);
  }
}