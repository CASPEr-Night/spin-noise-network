# Security policy

Report vulnerabilities **privately** to John W. Blanchard
<jwbquantum@gmail.com> — not via public issues or pull requests.

This matters most for the ingest endpoint (`server/worker.js`): anything
touching authentication, token handling, upload validation, or access to
stored bundles. Facility data lives behind that endpoint, and the shared
ingest token is cheap to rotate (`server/DEPLOY.md`), so err on the side of
reporting — a false alarm costs minutes.

When reporting, please include the affected file or endpoint, steps to
reproduce, and what an attacker could gain. You will get an acknowledgment
within a few days and credit in the fix's release notes if you want it.

Never include a live ingest token or a real endpoint URL in a public issue,
PR, or commit.
