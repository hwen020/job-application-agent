# Public release validation

Validated on November 21, 2026.

- [GitHub repository](https://github.com/hwen020/job-application-agent) and [v1.0.0 Release](https://github.com/hwen020/job-application-agent/releases/tag/v1.0.0).
- [Render Free demo](https://job-application-agent-demo.onrender.com/) and [health check](https://job-application-agent-demo.onrender.com/healthz). Automatic deployment is disabled.
- Deployed application commit `e1a906b363e42094b8939b7ebe25500a2409bb01`. [Application CI](https://github.com/hwen020/job-application-agent/actions/runs/44946965451) passed dependency setup, source and Git history scanning, Python compilation, and all five regression tests.
- The original `v1.0.0` tag still points to `0a65d5f6b1d50d6c402d3b40a603b2389e6065da`. Use the hosted application revision or current main for per-visitor isolation.

| Check | Outcome |
| --- | --- |
| Chromium desktop | Mock PageUp passed from JD review and PDF and DOCX generation through human approval, paused questions, resume, and receipt. A duplicate submission was rejected. The Pipeline and audit reflected the receipt, and refresh and reset worked. |
| Chromium mobile at 390 px | Mock NEOGOV passed from Dashboard and materials through Runner, receipt, and reset. No document-level horizontal overflow was detected during the workflow. |
| Two independent visitors | Visitor B could not read A's task or PDF. Resetting B preserved A's task and PDF. |
| HTTPS and readiness | Public HTTPS reached the live Docker deployment. `GET /healthz` returned HTTP 200. No unexpected workflow HTTP or browser console errors were observed. |
| Public application boundary | The session cookie was Secure, HttpOnly, and SameSite=Strict. Private file paths and real ATS entry routes were denied. |
| Instance replacement | A manual redeploy of the same commit returned to healthy. A pre-replacement artifact was unavailable, the old visitor cookie was replaced, and the two seeded jobs returned with no prior receipts or approvals. A repeat dashboard read left the seed unchanged. |
| Natural idle sleep and wake | NOT TESTED. The initial deployment and controlled instance replacement were observed. |

[Public Dashboard screenshot](dashboard.png) · [Four-minute walkthrough](../DEMO_GUIDE.md)
