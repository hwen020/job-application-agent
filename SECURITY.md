# Security

The application accepts seeded jobs and evidence IDs, predefined mock ATS answers and explicit workflow decisions. Do not enter real applicant information. This is a single-process demo with shared synthetic state for all visitors to an instance.

The backend has no routes to configure OAuth, read a real inbox, send mail, import personal records, open an official ATS or submit an external application. The mock receipt handler requires loopback access and an active local review token and submission intent in SQLite. Playwright restricts navigation to the local fixture origin.

The runtime database, generated files, browser artifacts and credentials are excluded from Git. The release scanner checks a closed file allowlist, committed history and text patterns for email, telephone, keys, tokens, workspace paths and external URLs in shipped code. The dashboard image must come from a fresh Synthetic Demo runtime and has a PNG metadata check. Human review remains part of publication.

Report issues through a private channel agreed with the maintainer. Avoid posting tokens or applicant records in a public issue.
