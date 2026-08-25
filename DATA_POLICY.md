# Spin-Noise Network — Data Policy

Version 1.0, 2026-08-24. Coordinators: John W. Blanchard (jwbquantum@gmail.com),
Reza Ebadi, and Claude (Anthropic), who assists with the analysis. Questions and
withdrawal requests go to jwbquantum@gmail.com.

## Ownership

Your raw data remain yours. Uploading a bundle gives the coordinators permission
to do the three things listed in the next section — nothing more.

## What the coordinators do with a bundle

(1) Quality assurance — checksum verification, metadata validation, and the
diagnostic checks described in PROTOCOL.md. (2) Analysis — spin-noise line
fitting, per-facility sensitivity calibration, and the axion-band search.
(3) Aggregation — combining your records with those of other facilities for
network-level results.

## Co-authorship

Contributing facilities are co-authors on any network publication their data
enter. This is the standing arrangement promised in the prospectus — if your
data are in a paper, your operators are offered authorship on it.

## Publication and embargo

You see your per-facility sensitivity report before anything derived from your
data appears anywhere public. Network papers are circulated to all contributors
before submission, with time to comment.

## Withdrawal

Any facility may withdraw its unpublished data at any time, no reason required —
one email and the bundles are deleted from the repository. Data already in a
submitted or published paper cannot be recalled from that paper, but withdrawal
removes them from everything afterward.

## What is published

Aggregated results by default. Per-facility numbers appear only with that
facility's consent. Locations are given at city level only — never precise
coordinates, never building or laboratory identifiers.

## Personal data

The only personal information in a bundle is one optional contact email, stored
only if the operator answered yes to the consent dialog at run time ("May the
project maintainers contact you at [address] about this data?"). Answer no and
the field is stored empty. No usernames, no other datasets from your
spectrometer — nothing outside the `SPINNOISE_*` dataset is read or uploaded.

## Data security

Every data file in a bundle carries a SHA-256 checksum, verified on receipt.
Transfer is over HTTPS. Access to stored bundles is limited to the coordinators
named above.
