# PrivacyTrace data card

## Contents and purpose

The dataset supports measurement of profile inference from tool-call traces.
It contains 1,000 statistically calibrated synthetic profiles, 4,000 generated
tasks, 4,000 agent trajectories, model attack outputs, semantic judgments, and
aggregate analyses.

## Personal-data status

The profiles are synthetic and were not sampled from identifiable people.
Names, reserved-domain email addresses, phone numbers, and government-ID-like
strings were generated for experimental consistency. Because some formats have
finite namespaces, accidental resemblance to a real identifier cannot be
categorically excluded. Do not use any identifier to contact, authenticate, or
make decisions about a person.

Trajectories also contain responses from public MCP services. Those responses
can mention real authors, places, job listings, products, or other public
entities. Their presence does not imply participation in or endorsement of the
study.

## Processing and anonymization

Release packaging excludes credentials, internal endpoints, local paths,
runtime logs, recovery checkpoints, failed smoke runs, manuscript sources, and
author-owned identifiers. Redactions are limited to anonymity and
infrastructure metadata; prediction labels and values used for reported paper
results are preserved. Redaction counts are recorded in `DATA_MANIFEST.json`.

## Limitations and risks

- Synthetic demographic distributions inherit limitations of their public
  source statistics and the calibration model.
- Public tool responses may be incomplete, stale, copyrighted, or subject to
  provider terms.
- Model outputs can contain incorrect or sensitive inferences.
- The dataset is designed for privacy auditing, not user profiling, identity
  resolution, clinical decisions, eligibility decisions, or surveillance.

## Access and terms

Full records are distributed as checksum-verified review assets. Project-owned
data are review-only under `REVIEW_LICENSE.md`. Third-party response content
remains subject to the rights of its original providers and authors.

