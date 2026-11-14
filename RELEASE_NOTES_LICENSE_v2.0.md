# Kevrai Omni Community License v2.0 — Release Notes

**Date:** 2026-09-12
**Effective:** Immediate upon publication
**Previous version:** Kevrai Omni Community License v1.0

---

## 1. Overview

The Kevrai Omni Community License has been comprehensively upgraded from v1.0
(11 articles + 1 appendix) to **v2.0 (17 articles + 4 appendices)**. The
upgrade addresses gaps identified through a systematic review of source-available
license best practices (PSVL, BSL, SSPL, Elastic License, AGPLv3), AI software
licensing trends (EU AI Act, China Generative AI Measures, PIPL, GDPR), and
enforcement experience.

The core licensing model remains unchanged: **source-available, free for
non-commercial use, commercial use requires prior written Commercial
Authorization.** v2.0 strengthens enforcement mechanisms and adds comprehensive
governance provisions that were absent in v1.0.

---

## 2. Structural Changes

| Aspect | v1.0 | v2.0 |
|---|---|---|
| Articles | 11 | 17 |
| Appendices | 1 (Contact) | 4 (Contact, Application Template, Prohibited Uses, Security Policy) |
| Definitions | 8 | 17 |
| Approximate length | ~450 lines | ~1100 lines |

### New Articles
- **Article 6 — Data Privacy and Security** (NEW)
- **Article 7 — Responsible AI Use and Prohibited Uses** (NEW)
- **Article 8 — Export Control and Sanctions** (NEW)
- **Article 9 — Contributions** (NEW)
- **Article 10 — Security Vulnerability Disclosure** (NEW)
- **Article 11 — Indemnification** (NEW)

### Renumbered / Expanded Articles
- Article 1 (Definitions): expanded from 8 to 17 definitions
- Article 4 (Conditions): added Section 4.7 Network Use source disclosure
- Article 5 (Commercial Use): expanded application process, added material change notification, records, audit
- Article 12 (Enforcement): expanded from v1.0 Article 6, added liquidated damages, audit rights, specific performance
- Article 16 (Termination): added Section 16.5 termination for convenience by Licensor
- Article 17 (General Provisions): expanded with notices, force majeure, assignment, electronic signatures, counterparts, limitation period, compliance with laws

---

## 3. Detailed Changelog by Article

### Article 1 — Definitions (Expanded)
New definitions added: Affiliate, Effective Date, Source Code, Network Use,
User Data, Security Incident, Prohibited Use, Third-Party Component,
Contribution. The definition of "Commercial Use" was expanded with explicit
categories (SaaS, application store, content monetization, entity size threshold)
and a clearer non-commercial exemption list.

### Article 4 — Conditions and Open Source Obligations (Expanded)
- **Section 4.7 (NEW): Network Use Source Disclosure** — AGPLv3-style copyleft
  for network-accessible use. Any party making the Software available over a
  network must make the complete corresponding Source Code available to all
  network users. This closes the "SaaS loophole" that existed in v1.0.
- Section 4.6 (Third-Party Components): strengthened to explicitly require
  commercial users to verify each model's upstream license and obtain separate
  commercial licenses for non-commercial-only components.

### Article 5 — Commercial Use Authorization (Expanded)
- Section 5.2: application requirements expanded to 9 mandatory items, including
  data types/volumes, security measures, and Affiliate information.
- Section 5.3: Licensor may impose additional terms including insurance,
  reporting, audit, and contribution-back requirements.
- Section 5.8 (NEW): Material Change Notification — commercial users must notify
  the Licensor within 15 business days of any material change in use.
- Section 5.9 (NEW): Records — commercial users must maintain use records for
  3 years post-termination.

### Article 6 — Data Privacy and Security (NEW)
- Section 6.1: Data controller responsibility under PIPL, GDPR, CCPA, and other
  applicable laws.
- Section 6.2: Explicit statement that the Licensor does not collect User Data
  (local-first architecture).
- Section 6.3: Cross-border data transfer compliance.
- Section 6.4: 72-hour Security Incident notification to the Licensor.
- Section 6.5: Mandatory security measures for commercial users (access controls,
  encryption, vulnerability scanning, incident response, etc.).
- Section 6.6: Data deletion upon termination.
- Section 6.7: Subprocessor management.

### Article 7 — Responsible AI Use and Prohibited Uses (NEW)
- Section 7.1: General responsible use obligation.
- Section 7.2: Prohibited Uses (see Appendix C for the full list).
- Section 7.3: AI safety compliance under China's Interim Measures for Generative
  AI Services and the EU AI Act, including content moderation, AI disclosure,
  and human oversight for material decisions.
- Section 7.4: Prohibition on circumventing safety features or jailbreaking AI
  models.
- Section 7.5: Additional obligations for high-risk AI systems under the EU AI Act.
- Section 7.6: Licensor disclaims responsibility for AI outputs; user is solely
  liable.

### Article 8 — Export Control and Sanctions (NEW)
- Section 8.1: Compliance with China Export Control Law, US EAR, EU Dual-Use
  Regulation, and AI-specific export controls.
- Section 8.2: Prohibition on export to sanctioned countries, persons, or
  prohibited end uses (WMD, military, terrorism).
- Section 8.3: Re-export restrictions.
- Section 8.4: Diversion prohibition.
- Section 8.5: 5-year record-keeping for international transfers.

### Article 9 — Contributions (NEW)
- Section 9.1: Copyright license-back for contributions (worldwide, perpetual,
  irrevocable, royalty-free, non-exclusive).
- Section 9.2: Patent license-back for contributions.
- Section 9.3: Representation of right to contribute (original work, no
  infringement, employer permission if applicable).
- Section 9.4: Moral rights waiver (to the extent permitted by law).
- Section 9.5: No obligation to accept contributions.
- Section 9.6: Feedback may be used freely by the Licensor.

### Article 10 — Security Vulnerability Disclosure (NEW)
- Section 10.1: Safe harbor for good-faith security researchers (no legal action
  for compliant reporters).
- Section 10.2: Vulnerability reporting process (see Appendix D).
- Section 10.3: 90-day coordinated disclosure timeline.
- Section 10.4: No waiver of rights for bad-faith actors.
- Section 10.5: Bug bounty program provision.

### Article 11 — Indemnification (NEW)
- Section 11.1: Commercial users must indemnify the Licensor for claims arising
  from commercial use, license breach, IP infringement, data privacy violations,
  AI output harms, export control violations, and Prohibited Uses.
- Section 11.2: Indemnification procedure (notice, defense assumption, settlement
  consent).
- Section 11.3: Survival of indemnification obligations post-termination.
- Section 11.4: Non-commercial users exempt except for willful misconduct, gross
  negligence, or fraud.

### Article 12 — Enforcement and Remedies (Expanded from v1.0 Article 6)
- Section 12.1: Cease-and-desist / lawyer's letter (律师函) right — retained and
  strengthened.
- Section 12.2: Injunctive relief — retained.
- Section 12.3: Damages and account of profits — expanded to include:
  - **Section 12.3(e) (NEW): Liquidated damages** for unauthorized commercial use:
    greater of (i) reasonable licensing fees, (ii) USD 10,000 per instance, or
    (iii) actual damages.
- Section 12.4: Limited cure period — narrowed to exclude unauthorized commercial
  use, Prohibited Uses, Network Use violations, willful breaches, and breaches of
  Articles 5, 6, 7, 8, 12.
- Section 12.5 (NEW): Audit rights for commercial users (1 audit/year, 10-day
  notice, user pays if material non-compliance found).
- Section 12.6: Prevailing party attorneys' fees — retained and expanded to include
  cease-and-desist letter costs.
- Section 12.7: Reservation of rights — expanded.
- Section 12.8 (NEW): Specific performance.

### Article 15 — Limitation of Liability (Expanded)
- Section 15.4 (NEW): Total liability cap — greater of USD 100 or fees paid in
  the preceding 12 months. Does not apply to fraud, willful misconduct, or gross
  negligence.

### Article 16 — Termination (Expanded)
- Section 16.5 (NEW): Termination for convenience by the Licensor (30-day notice).

### Article 17 — General Provisions (Expanded)
New sections added:
- Section 17.9: Notices (personal delivery, email, courier)
- Section 17.10: Force majeure
- Section 17.11: Assignment by Licensor (without consent)
- Section 17.12: No partnership or joint venture
- Section 17.13: Construction (no contra proferentem)
- Section 17.14: Electronic signatures
- Section 17.15: Counterparts
- Section 17.16: 2-year limitation period
- Section 17.17: Compliance with laws (anti-corruption, AML, anti-terrorism, etc.)

---

## 4. New Appendices

### Appendix A — Contact (Retained, Updated)
Contact email: 2671369836@qq.com. Added subject line formats for Commercial
Authorization applications, urgent enforcement matters, and security vulnerability
reports.

### Appendix B — Commercial Authorization Application Template (NEW)
A 9-section template covering: applicant information, affiliates, software version,
intended commercial use, data and security, AI compliance, export control,
additional information, and declaration with signature.

### Appendix C — Prohibited Uses List (NEW)
5 categories of prohibited uses:
- **C.1:** Illegal content and activities (CSAM, violence incitement, harassment,
  defamation, IP infringement, fraud)
- **C.2:** Harmful AI outputs (deepfakes without consent, disinformation,
  self-harm instructions, privacy violations, autonomous high-impact decisions
  without oversight, mass surveillance)
- **C.3:** Security abuse (malware, hacking, DoS/DDoS, safety circumvention,
  jailbreaking, model weight extraction)
- **C.4:** Commercial misuse (unauthorized commercial use, misrepresentation,
  competing with Licensor, attribution removal, trademark misuse)
- **C.5:** Data misuse (PIPL/GDPR violations, cross-border transfer violations,
  unlawful data collection, web scraping, excessive data retention)

### Appendix D — Security Vulnerability Reporting Policy (NEW)
7 sections covering: purpose, reporting channel, acknowledgment and response
timeline (5 business days acknowledgment, 15 business days validation),
coordinated disclosure (90 days), safe harbor, bug bounty provision, and no
warranty.

---

## 5. Compatibility and Transition

- **Existing users:** Users who received the Software under v1.0 may continue to
  use it under v1.0 terms, or accept v2.0 by continuing to use the Software after
  v2.0 becomes effective (see Section 17.5 New Versions).
- **Existing Commercial Authorizations:** Any Commercial Authorization issued under
  v1.0 remains valid for its stated term and scope, but is subject to the
  enforcement and remedies provisions of v2.0.
- **Third-party components:** No change — all third-party models, engines, and
  weights remain governed by their own upstream licenses.
- **Code:** No code changes are required for this license upgrade. The license
  change is a legal/documentation change only.

---

## 6. Files Modified

| File | Change |
|---|---|
| `LICENSE` | Complete rewrite from v1.0 to v2.0 (17 articles + 4 appendices) |
| `README.md` | License badge and section updated to v2.0; added v2.0 feature summary |
| `NOTICE.md` | Project license references updated from v1.0 to v2.0 |
| `INSTALL.md` | License section updated from v1.0 to v2.0 |
| `electron-builder.yml` | Copyright line updated from v1.0 to v2.0 |
| `RELEASE_NOTES_LICENSE_v2.0.md` | New file (this document) |

---

## 7. Acknowledgments

This license upgrade was informed by a systematic review of:
- Source-available license best practices: PolyForm Shield License (PSVL),
  Business Source License (BSL), Server Side Public License (SSPL), Elastic
  License v2, AGPLv3 Section 13
- AI software licensing: EU AI Act (Regulation (EU) 2024/1689), China Interim
  Measures for the Management of Generative Artificial Intelligence Services,
  OpenRAIL license family, CreativeML Open RAIL-M
- Data protection: PIPL (China), GDPR (EU), CCPA (California)
- Export control: China Export Control Law, US EAR, EU Dual-Use Regulation

The license remains a custom source-available license and is not an OSI-approved
open source license, due to the Commercial Use authorization requirement.
