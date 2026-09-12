# Kevrai Omni Community License v2.1 — Release Notes

**Date:** 2026-09-12
**Effective:** Immediate upon publication
**Previous version:** Kevrai Omni Community License v2.0 (17 articles + 4 appendices)

---

## 1. Overview

v2.1 is a hardening and precision release. The core model is unchanged
(source-available; free for non-commercial use; Commercial Use requires prior
written Commercial Authorization). v2.1 closes structural loopholes, makes the
copyleft boundary predictable, adds AI-specific competitive protections, and
strengthens enforceability under PRC and cross-border practice.

| Aspect | v2.0 | v2.1 |
|---|---|---|
| Articles | 17 | **19** |
| Appendices | 4 | **6** |
| Definitions | 17 | **24** |
| Numbered sections | ~150 | **207** |
| Approximate length | ~1,500 lines | **~2,260 lines** |

---

## 2. Two New Articles

### Article 17 — Anti-Evasion and License Integrity (NEW)
Closes the common loopholes used to bypass source-available restrictions:
- **17.1** No circumvention through Affiliates, intermediaries, nominees,
  proxies, contractors, resellers, cloud/hosting/managed-service providers,
  corporate restructuring, or jurisdictional/technical/contractual
  arrangements.
- **17.2** No threshold avoidance — splitting entities, deployments, accounts,
  or transactions to stay under the employee/revenue/user/duration thresholds;
  substance and economic reality govern over formal labels.
- **17.3** No enabling others to evade (wrapper/proxy/marketplace/"substantially
  similar implementation" to let third parties avoid a Commercial
  Authorization).
- **17.4** No technical circumvention of copyleft/network disclosure via
  obfuscation, partial distribution, dynamic loading, or delayed combination
  (legitimate engineering is not prohibited).
- **17.5–17.6** Attribution of Affiliates'/agents' conduct to the licensee;
  look-through to actual use, benefit, and control.

### Article 18 — AI Training, Distillation, and Competitive Restrictions (NEW)
Drawn from open-weight industry practice (Llama, Stable Diffusion 3) and
adapted to a model-management workstation:
- **18.1** No training/fine-tuning/alignment, **Distillation**, catalog/metadata
  scraping, weight extraction, competition-oriented benchmarking, or
  substitute-service provision using the Software, Models, or Outputs without
  a Commercial Authorization.
- **18.2** Bona fide, openly published non-commercial research remains
  permitted.
- **18.3** Independent, truthful benchmarking is permitted with methodology
  disclosure.
- **18.4** A model/dataset/system created in breach is itself restricted and
  its distribution/Network Use is a breach.
- **18.5** Supplements (never replaces) upstream model licenses; the more
  restrictive governs.

---

## 3. Major Strengthening of Existing Articles

### Article 1 — Definitions (17 → 24)
Added: Modified Version/Fork, Effective Source Code, End User, Arms-Length
Interface, Model, Output, Distillation, Competing Product, Personal/Sensitive
Personal Data. Expanded: Distribution (timesharing/service bureau), Commercial
Use (in-kind/ad revenue, consolidated Affiliates, non-profit carve-out, tighter
trial limits), Affiliate (consolidation), Effective Source Code (complete,
non-obfuscated, barrier-free, buildable).

### Article 2 — Copyright Grant
- Temporary/incidental copies (cache, backup, memory, container layers, CDN)
  expressly licensed but Distribution still regulated.
- Sublicensing must be written and equally protective; licensor remains
  liable for sublicensee breach.
- **2.4 (NEW) Input/Output ownership** — user owns inputs/Outputs as between
  the parties (subject to upstream model licenses and Article 18).

### Article 3 — Patent Grant
- **3.4 (NEW) Patent non-assertion covenant** against the unmodified Software.

### Article 4 — Conditions
- **4.3** Source must be *Effective Source Code*, offer valid 3 years or
  support lifetime, include build/dependency/interface materials.
- **4.5** Contractual restrictions (e.g., NDAs) that defeat granted rights are
  void to that extent.
- **4.6** Upstream model licensors are intended **third-party beneficiaries**.
- **4.7** Network source must match the exact running version and stay current;
  containers/serverless/multi-host do not reduce the obligation.
- **4.8 (rewritten) Precise copyleft boundary** — static linking/internal
  coupling = Derivative Work; separate processes over Arms-Length Interfaces
  presumed separate (rebuttable); explained in new Appendix E.
- **4.9 (NEW) Modified Version/Fork rules** — prominent change notices,
  mandatory rename to avoid confusion, no "official/endorsed" claims.
- **4.10 (NEW)** Modifying the license text for other works requires rename
  (MPL-style).
- **4.11 (NEW)** No sublicensing of third-party models beyond upstream rights.

### Article 5 — Commercial Authorization
- Application expanded to 11 items (consolidated headcount/revenue,
  timesharing, cross-border data, AI training intent, insurance evidence).
- **5.5** Transfer/change-of-control requires consent.
- **5.10 (NEW) Insurance** — cyber/professional indemnity (≥ USD 500k per
  claim / 1M aggregate) when processing Personal Data, >1,000 End Users, or
  material decisions.
- **5.11 (NEW) Government/public-sector use** presumed commercial; sovereign
  immunity waived.

### Article 6 — Data Privacy and Security
- Data-subject rights; DPIA for high-risk processing (**6.8**); automated
  decision-making safeguards (**6.9**); children's data (**6.10**); telemetry
  & auto-update transparency (**6.11**).
- Logs retained ≥180 days; MFA, SBOM, patch management; backups in deletion
  scope.

### Article 7 — Responsible AI
- **7.7 (NEW) AI-content labeling/watermarking and provenance** (China
  generative-AI labeling rules; no removal of labels; no fake "authentic"
  presentation).
- **7.8 (NEW) Training-data/model provenance** — substantiable rights and
  consents for any Model added to the catalog.

### Article 9 — Contributions
- **9.5 (NEW) AI-assisted contribution disclosure; 9.6 (NEW) DCO-style
  sign-off;** relicensing right added to 9.1.

### Article 11 — Indemnification
- Added categories: distributed Derivative Works, End User/customer/
  subprocessor claims; survival refined (3 years; third-party claims to final
  resolution).

### Article 12 — Enforcement
- **12.2** Added PRC **行为保全 / 证据保全 / 财产保全** (behavior/evidence/
  property preservation) and ex parte urgent relief.
- **12.3** Election of most favorable basis; PRC **法定赔偿 (statutory)** and
  **惩罚性赔偿 (punitive)** damages; liquidated-damages "instance" defined
  (each deployment/product/service/customer/30-day period); judicial
  reduction safeguard.
- **12.5** Audit: suspicion-based additional audits; 15-day remediation.
- **12.9 (NEW) Takedown / platform cooperation.**

### Article 13 — Trademarks
- Covers translations/transliterations/domains/handles/keywords; Fork naming
  independently enforceable (**13.5**).

### Article 14/15 — Warranty / Liability
- Added third-party-component warranty disclaimer (**14.5**) and risk
  allocation clause (**15.5**).

### Article 16 — Termination
- Network Use must cease; cloud/backup destruction; accrued fees payable;
- **16.6 (NEW) Reinstatement** after valid cure.

### Article 19 — General Provisions (formerly Article 17)
- **19.3 Tiered dispute resolution:** negotiation (30d) → optional mediation →
  PRC court at Licensor domicile; **optional CIETAC arbitration** for
  cross-border licensees (New York Convention enforcement); urgent interim
  relief carve-out.
- Order of precedence; blue-pencil reformation; expanded interpretation
  rules; reverse-engineering scope (**19.17**); independent development
  (**19.18**); third-party beneficiaries (**19.19**); preservation of users'
  statutory rights (**19.20**); anti-corruption/forced-labor (**19.16**).

---

## 4. Two New Appendices

- **Appendix E — Copyleft Boundary Guide (explanatory):** worked examples of
  Derivative Work vs. mere aggregation; static/dynamic linking; IPC/HTTP;
  thin wrappers; containers/microservices/SaaS; coupling vs. arms-length
  factors.
- **Appendix F — Commercial User Compliance Checklist (explanatory):** 8
  domains (authorization, copyleft/attribution, third-party models, privacy/
  security, responsible AI, AI training/competition, export/anti-evasion,
  business protections) as a practical self-audit list.

Appendix B application template expanded from 9 to 11 sections (Fork naming,
AI training/distillation, insurance). Appendix C prohibited uses expanded with
evasion, AI-training, timesharing, labeling-removal, and telemetry offenses.

---

## 5. Design Rationale (why each new block exists)

| Block | Problem it prevents |
|---|---|
| Art. 17 Anti-evasion | "We only used it via our cloud subsidiary / split into 9-person entities / wrapped it" defenses |
| Art. 18 Training/Distillation | Using the catalog, agent prompts, tool definitions, or Outputs to clone the product or train a competitor |
| §4.8 + App. E | Uncertainty (and opportunistic claims) about whether linking/IPC/containers trigger copyleft |
| §4.9 Fork rename | Confusing or impersonating the official project |
| §1.11 Effective Source Code | "Open-sourcing" obfuscated, incomplete, or paywalled code |
| §5.10 Insurance | An uninsured commercial user unable to satisfy an indemnity |
| §5.11 Government use | Public-sector users claiming non-commercial status or sovereign immunity |
| §19.3 Tiered ADR | Costly immediate litigation; gives cross-border users a neutral, enforceable track (CIETAC/NY Convention) while preserving urgent injunctions |
| §12.2/12.3 PRC remedies | Makes 律师函 → 保全 → 法定/惩罚性赔偿 chain explicit and locally enforceable |

---

## 6. Compatibility and Transition

- **v2.0 users:** may remain on v2.0 for code received under v2.0, or accept
  v2.1 for new use (§19.6). Existing Commercial Authorizations remain valid;
  new insurance/change-control expectations apply on renewal or material
  change.
- **Third-party components:** unchanged — still governed by their own upstream
  licenses; Article 18 is expressly subordinate to them where more
  restrictive.
- **Code:** legal/documentation change only; no code change required.

---

## 7. Files Modified

| File | Change |
|---|---|
| `LICENSE` | Rewritten/expanded v2.0 → v2.1 (19 articles + 6 appendices) |
| `README.md` | Badge/section to v2.1; feature summary updated |
| `NOTICE.md` | Project license reference to v2.1 |
| `INSTALL.md` | License section to v2.1 |
| `electron-builder.yml` | Copyright line to v2.1 |
| `RELEASE_NOTES_LICENSE_v2.1.md` | New (this document) |

---

## 8. Research Basis

v2.1 was informed by: open-weight AI license restrictions (Meta Llama
Community License, Stable Diffusion 3 / Stability AI Community License,
OpenRAIL); source-available anti-evasion drafting (OCMM, PolyForm family,
ZPE-Geo); copyleft-boundary analysis (GPL/AGPL linking guidance, FSF &
Debian interaction papers, MPL-2.0 file-level copyleft and fork-rename rule);
software escrow/insurance commercial practice; PRC Copyright Law statutory/
punitive damages and Civil Procedure Law preservation mechanisms; CIETAC
arbitration and New York Convention enforcement.
