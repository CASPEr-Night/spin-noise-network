# Spin-Noise Network — Data Policy

Version 1.1, 2026-11-24. Changed from 1.0: the section "Access by the CASPEr
collaboration" and the matching sentences under "Personal data" and "Data
security". Coordinators: John W. Blanchard (jwbquantum@gmail.com),
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

## Access by the CASPEr collaboration

CASPEr@night is part of the wider CASPEr effort (the Cosmic Axion Spin Precession
Experiment), and the people best placed to check and extend this analysis are in
that collaboration. From version 1.1, members of the CASPEr collaboration may be
given read-only access to the stored bundles, raw data included, on these terms.

(1) Same uses. Collaboration members may do the three things listed above —
quality assurance, analysis, aggregation — and nothing more. Your data remain
yours.

(2) Named people. Access is given by the coordinators to individual members, who
first agree to this policy. The coordinators keep the list, and any facility can
ask to see it.

(3) No onward sharing. Collaboration members do not pass bundles, or per-facility
numbers derived from them, to anyone outside the collaboration.

(4) Same protections. Co-authorship, the embargo, withdrawal and the rules under
"What is published" apply to anything a collaboration member does with your data
exactly as they apply to the coordinators: if your data enter a paper, your
operators are offered authorship on it, and you see it before submission. On
withdrawal the coordinators delete your bundles and require every collaboration
member holding a copy to delete it.

(5) Agreement. From version 1.1, using the network's software and uploading a
bundle means the facility agrees to this access for the data it uploads; the
acquisition script and the uploader both say so. Data uploaded under version 1.0
are shared only after the facility's PI has agreed, by email. A facility can end
the sharing of its data at any time, for bundles not yet in a submitted paper;
its bundles then stay with the coordinators, and it stays in the network as
before.

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
Where a facility's bundles are shared with the CASPEr collaboration (see above),
the contact email stored in a bundle is visible to the members given access. It
is used only for questions about the data.

## Data security

Every data file in a bundle carries a SHA-256 checksum, verified on receipt.
Transfer is over HTTPS. Access to stored bundles is limited to the coordinators
named above and, for facilities that have agreed, to the named members of the
CASPEr collaboration described under "Access by the CASPEr collaboration".
Collaboration members can read and download; they cannot upload, change or
delete.
