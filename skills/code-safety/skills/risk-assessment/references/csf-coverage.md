# NIST CSF 2.0 coverage frame

CSF 2.0 (Feb 2024) is the high-level layer. Use it twice: at the start to set coverage expectations, and at the end to catch whole-category blind spots and gap-fill where the CIS Controls fall short. It describes outcomes, not prescriptions — that is exactly why it works as a frame rather than a checklist.

## The six Functions

CSF 2.0 organizes everything into six Functions. Version 2.0's headline change was adding **Govern** as a Function wrapping the other five. Map the subject's findings onto all six and flag any Function that comes up empty.

**GV — Govern.** The strategy, expectations, and policy that steer the rest. For an individual operator this is light but not absent: who decides acceptable risk, how decisions get recorded, supply-chain/third-party policy, roles. Empty Govern is the most commonly missed gap — if every finding is technical and none touch "how do I decide and record this," say so.

**ID — Identify.** Understanding assets, data, and risk. Asset and software inventory, data flows, the risk assessment itself. This is where scope and characterization land.

**PR — Protect.** Safeguards that prevent or limit harm: access control, data security, secure configuration, awareness, platform hardening. Control-by-control assessments over-weight this Function; a finding list that is *all* Protect is a signal to check the other five.

**DE — Detect.** Finding incidents: monitoring, logging, anomaly detection. Frequently under-covered. If nothing in the assessment would tell the user an incident happened, that is a Detect gap worth naming.

**RS — Respond.** Acting on a detected incident: response process, containment, communication. For individuals this is often "I have no plan" — name it rather than skip it.

**RC — Recover.** Restoring after an incident: backups, recovery testing, restoration of service. Tie to data-recovery findings; an untested backup is a Recover gap.

## How to use it for coverage

After scoring and recommending (skill Phase 6):

1. Tag each finding with its primary Function.
2. List the Functions with no findings and no controls.
3. For each empty Function, decide: genuinely not applicable to this subject, or a real blind spot? Say which, with one line of reasoning.
4. Where a Function calls for something the CIS Controls v8.1 mapping did not surface, name the gap explicitly and, if useful, point at the CSF Category. This is the gap-fill role.

## Categories worth keeping in view

Not exhaustive — the ones that most often expose gaps a control-by-control pass misses:

- **GV.SC — Cybersecurity Supply Chain Risk Management.** For any dependency or third-party service, ask whether supply-chain risk is governed, not just whether the current version is clean.
- **GV.RR — Roles, Responsibilities, and Authorities.** Who owns the risk decision.
- **ID.RA — Risk Assessment.** The activity this skill performs; the assessment itself is the evidence for this Category.
- **PR.DS — Data Security.** Confidentiality/integrity/availability of the data in scope.
- **PR.AA — Identity Management, Authentication, and Access Control.**
- **DE.CM — Continuous Monitoring.**
- **RS.MA — Incident Management.**
- **RC.RP — Incident Recovery Plan Execution.**

The point of the frame is not to fill every box. It is to make absence visible, so a clean-looking Protect posture cannot hide an empty Detect/Respond/Recover.
