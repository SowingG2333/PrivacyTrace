import type { FilteredRxNormMapping, FilteredPharmacologicClassMapping } from './mapping-types.js';

export interface SPLDocument {
  setId: string;
  title: string;
  effectiveTime: string;
  versionNumber: string;
  sections: SPLSection[];
  spl_medguide?: string;
  spl_patient_package_insert?: string;
  spl_product_data_elements?: string;
  rxNormMappings?: FilteredRxNormMapping[];
  pharmacologicClassMappings?: FilteredPharmacologicClassMapping[];
}

export interface SPLSection {
  id?: string;
  title: string;
  content: string;
}