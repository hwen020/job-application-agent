# Security

The application accepts seeded jobs and evidence IDs, predefined mock ATS answers and explicit workflow decisions. Do not enter real applicant information. Each visitor has a separate temporary SQLite database selected by an opaque session cookie. The cookie is HttpOnly and SameSite=Strict, and is Secure on the hosted configuration. Visitor state expires after 30 minutes of inactivity or an instance restart.

The backend has no routes to configure OAuth, read a real inbox, send mail, import personal records, open an official ATS or submit an external application. The mock receipt handler requires loopback access, an internal capability, and a recorded review token and submission intent. Playwright restricts navigation to the local fixture origin. Request bodies and session allocations are bounded, with one mock browser job running at a time.

The health endpoint exposes only a readiness status. It does not return database contents.

The runtime database, generated files, browser artifacts and credentials are excluded from Git. The release scanner checks a closed file allowlist, committed history and text patterns for email, telephone, keys, tokens, workspace paths and external URLs in shipped code. The dashboard image must come from a fresh Synthetic Demo runtime and has a PNG metadata check. Human review remains part of publication.

Report issues through a private channel agreed with the maintainer. Avoid posting tokens or applicant records in a public issue.
