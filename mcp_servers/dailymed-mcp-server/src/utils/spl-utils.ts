import type { SPLDocument, FilteredRxNormMapping, FilteredPharmacologicClassMapping } from '../types/index.js';
import type { MappingService, RxNormMapping, PharmacologicClassMapping } from '../mapping-service.js';

export function createSPLDocumentFromItem(item: any, mappingService: MappingService): SPLDocument {
  const setId = item.setid;
  const rxNormMappings = mappingService.getRxNormMappings(setId);
  const pharmacologicClassMappings = mappingService.getPharmacologicClassMappings(setId);

  // Filter out redundant fields from mappings
  const filteredRxNormMappings: FilteredRxNormMapping[] = rxNormMappings.map(mapping => ({
    rxcui: mapping.rxcui,
    rxstring: mapping.rxstring,
    rxtty: mapping.rxtty,
  }));

  const filteredPharmacologicClassMappings: FilteredPharmacologicClassMapping[] = pharmacologicClassMappings.map(mapping => ({
    pharmaSetId: mapping.pharmaSetId,
    pharmaVersion: mapping.pharmaVersion,
  }));

  return {
    setId: setId,
    title: item.title,
    effectiveTime: item.published_date,
    versionNumber: item.spl_version?.toString() || "1",
    sections: [],
    spl_medguide: item.spl_medguide,
    spl_patient_package_insert: item.spl_patient_package_insert,
    spl_product_data_elements: item.spl_product_data_elements,
    rxNormMappings: filteredRxNormMappings.length > 0 ? filteredRxNormMappings : undefined,
    pharmacologicClassMappings: filteredPharmacologicClassMappings.length > 0 ? filteredPharmacologicClassMappings : undefined,
  };
}