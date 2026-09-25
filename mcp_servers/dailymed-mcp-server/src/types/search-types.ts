export interface AdvancedSPLSearchParams {
  query?: string;
  application_number?: string;
  boxed_warning?: boolean;
  dea_schedule_code?: string;
  doctype?: string;
  drug_class_code?: string;
  drug_class_coding_system?: string;
  drug_name?: string;
  name_type?: "g" | "generic" | "b" | "brand" | "both";
  labeler?: string;
  manufacturer?: string;
  marketing_category_code?: string;
  ndc?: string;
  published_date?: string;
  published_date_comparison?: "lt" | "lte" | "gt" | "gte" | "eq";
  rxcui?: string;
  setid?: string;
  unii_code?: string;
  page?: number;
  pageSize?: number;
}