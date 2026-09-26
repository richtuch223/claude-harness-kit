export const meta = {
  name: 'discovery-sweep',
  description: 'Scout->exploit research funnel: wide scout fan-out -> promote top leads -> deep-investigate in parallel -> adversarially verify each -> second skeptic on survivors -> synthesize. Only findings that survive both skeptics reach the report.',
  whenToUse: 'Run a parallel research sweep over a set of open questions. Pass args {areas, top_k}.',
  phases: [
    { title: 'Scout',       detail: 'one fast scout per area (sonnet), returns ranked leads' },
    { title: 'Investigate', detail: 'deep investigator per promoted lead (opus), with baselines + code self-audit' },
    { title: 'Verify',      detail: 'adversarial skeptic re-runs the repro and tries to refute each finding' },
    { title: 'Confirm',     detail: 'second, perspective-diverse skeptic on survivors only' },
    { title: 'Synthesize',  detail: 'one cited brief + proposed DECISIONS/STATE/registry diffs (sonnet)' },
  ],
}

// ── args ─────────────────────────────────────────────────────────────────────
// { areas: [ "free-text area" | {area, investigator} , ... ],
//   top_k: number        // how many leads to promote to deep investigation (default 5)
//   investigator: string // default agentType for the Investigate phase (default 'investigator') }
let A = args || {}
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (e) { A = {} }
}
const TOP_K = A.top_k || 5
const DEFAULT_INVESTIGATOR = A.investigator || 'investigator'
const AREAS = Array.isArray(A.areas) ? A.areas : (Array.isArray(A) ? A : [])
log(`args received: type=${typeof args}, parsed areas=${AREAS.length}, top_k=${TOP_K}`)
if (!AREAS.length) {
  log('No areas provided. Pass args {areas:[...], top_k}. Aborting.')
  return { error: 'no areas', leads: [], findings: [] }
}

const norm = (a) => (typeof a === 'string')
  ? { area: a, investigator: DEFAULT_INVESTIGATOR }
  : { investigator: DEFAULT_INVESTIGATOR, ...a }

// ── schemas ──────────────────────────────────────────────────────────────────
const LEADS_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['area', 'leads'],
  properties: {
    area: { type: 'string' },
    leads: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['lead', 'why', 'next_probe', 'interestingness', 'already_decided_risk'],
        properties: {
          lead: { type: 'string' },
          why: { type: 'string' },
          cheap_signal: { type: 'string' },
          next_probe: { type: 'string' },
          effort: { type: 'string', enum: ['S', 'M', 'L'] },
          interestingness: { type: 'number' },            // 0..1
          already_decided_risk: { type: 'boolean' },       // brushes a DECISIONS.md row?
        },
      },
    },
  },
}

const FINDING_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['claim', 'mechanism', 'evidence', 'verdict', 'confidence', 'repro'],
  properties: {
    claim: { type: 'string' },
    mechanism: { type: 'string' },
    evidence: { type: 'string' },                          // n, candidate vs baselines, CI
    anti_fooling: { type: 'string' },
    verdict: { type: 'string', enum: ['interesting-unverified', 'survives', 'refuted', 'already-decided'] },
    confidence: { type: 'string', enum: ['low', 'med', 'high'] },
    repro: { type: 'string' },                             // script path + command
  },
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'strongest_attack', 'what_would_change'],
  properties: {
    verdict: { type: 'string', enum: ['REFUTED', 'SURVIVES', 'SURVIVES-WITH-CAVEATS'] },
    strongest_attack: { type: 'string' },
    weaknesses: { type: 'string' },
    what_would_change: { type: 'string' },
  },
}

// ── Phase 1: SCOUT (wide, cheap, parallel) ────────────────────────────────────
phase('Scout')
const areaObjs = AREAS.map(norm)
const scoutResults = await parallel(areaObjs.map((a) => () =>
  agent(
    `Scout this area and return ranked leads:\n\nAREA: ${a.area}\n\n` +
    `Read CLAUDE.md and grep docs/DECISIONS.md first. Skip anything already decided. ` +
    `Return ranked leads only — no conclusions, no experiments.`,
    { agentType: 'research-scout', schema: LEADS_SCHEMA, phase: 'Scout', label: `scout:${a.area.slice(0, 28)}` },
  ).then((r) => ({ ...r, investigator: a.investigator }))
))

// flatten + rank + promote (drop already-decided risk; highest interestingness first)
const allLeads = scoutResults.filter(Boolean).flatMap((r) =>
  (r.leads || []).map((l) => ({ ...l, investigator: r.investigator, area: r.area })))
const promoted = allLeads
  .filter((l) => !l.already_decided_risk)
  .sort((x, y) => (y.interestingness || 0) - (x.interestingness || 0))
  .slice(0, TOP_K)

log(`scouted ${AREAS.length} areas -> ${allLeads.length} leads -> promoting top ${promoted.length} ` +
    `(dropped ${allLeads.filter((l) => l.already_decided_risk).length} already-decided-risk)`)
if (!promoted.length) {
  return { leads: allLeads, promoted: [], findings: [], note: 'no promotable leads (all decided/uninteresting)' }
}

// ── Phases 2+3: INVESTIGATE -> VERIFY (pipeline; verify starts as each finishes) ─
const verified = await pipeline(
  promoted,
  // stage 1: deep investigate (unique scratch-script name per lead -> no parallel collisions)
  (lead, _orig, idx) => agent(
    `Deep-investigate this promoted lead to a verdict, with baselines and a CI at the right unit.\n\n` +
    `LEAD: ${lead.lead}\nWHY: ${lead.why}\nFIRST PROBE: ${lead.next_probe}\nAREA: ${lead.area}\n\n` +
    `Follow the experiment discipline in CLAUDE.md. Offline metric != real outcome — say which you report. ` +
    `You are running in PARALLEL with other investigators: name any scratch script uniquely ` +
    `(suffix it with the slug "lead${idx}") so you never overwrite another agent's file. ` +
    `Self-audit your code for lookahead/off-by-one and add a sanity assertion BEFORE reporting.`,
    { agentType: lead.investigator, schema: FINDING_SCHEMA, phase: 'Investigate',
      label: `dig:${lead.lead.slice(0, 26)}` },
  ),
  // stage 2: adversarially verify that finding (skeptic MUST execute the repro)
  (finding, lead) => {
    if (!finding) return null
    return agent(
      `Adversarially REFUTE this finding. Default to REFUTED unless the evidence forces otherwise. ` +
      `You MUST execute the repro command and reproduce the number yourself; never touch a holdout.\n\n` +
      `CLAIM: ${finding.claim}\nMECHANISM: ${finding.mechanism}\nEVIDENCE: ${finding.evidence}\n` +
      `ANTI-FOOLING (investigator's): ${finding.anti_fooling || 'n/a'}\n` +
      `REPRO: ${finding.repro}\nINVESTIGATOR VERDICT: ${finding.verdict}`,
      { agentType: 'adversarial-verifier', schema: VERDICT_SCHEMA, phase: 'Verify',
        label: `refute:${(lead.lead || '').slice(0, 24)}` },
    ).then((v) => ({ lead, finding, verdict: v }))
  },
)

const scored = verified.filter(Boolean).filter((x) => x.verdict)
const firstPass = scored.filter((x) => x.verdict.verdict !== 'REFUTED')
log(`investigated ${promoted.length} -> ${scored.length} verified -> ${firstPass.length} survived skeptic #1`)

// ── Phase 4: CONFIRM (second, perspective-diverse skeptic on survivors only) ──
// Barrier is justified: all first-pass verdicts are needed to know who survived before spending the
// expensive second pass. A different lens catches failure modes redundancy cannot.
phase('Confirm')
const confirmed = firstPass.length ? (await parallel(firstPass.map((x) => () =>
  agent(
    `You are the SECOND, independent skeptic. Skeptic #1 did not refute this. Attack a DIFFERENT failure ` +
    `mode than reproduction: focus on (a) whether the effect survives realistic real-world constraints ` +
    `(scale, cost, latency), (b) robustness across time periods / segments, and (c) whether the result is ` +
    `a decided dead-end (docs/DECISIONS.md) in disguise. Re-run the repro to confirm independently. ` +
    `Default REFUTED unless it withstands this second lens.\n\n` +
    `CLAIM: ${x.finding.claim}\nEVIDENCE: ${x.finding.evidence}\nREPRO: ${x.finding.repro}\n` +
    `SKEPTIC #1 VERDICT: ${x.verdict.verdict} — ${x.verdict.strongest_attack}`,
    { agentType: 'adversarial-verifier', schema: VERDICT_SCHEMA, phase: 'Confirm',
      label: `confirm:${(x.lead.lead || '').slice(0, 22)}` },
  ).then((v2) => ({ ...x, confirm: v2 }))
))).filter(Boolean) : []

const survived = confirmed.filter((x) => x.confirm && x.confirm.verdict !== 'REFUTED')
log(`survivors after diverse second pass: ${survived.length}/${firstPass.length}`)

// ── Phase 5: SYNTHESIZE (one cited brief + proposed diffs) ────────────────────
phase('Synthesize')
const brief = await agent(
  `Synthesize this discovery sweep into one decision-ready brief, per your synthesizer rules. A finding ` +
  `counts as SURVIVED only if it passed BOTH skeptics. Report survivors as findings; list anything either ` +
  `skeptic refuted as new dead-ends (with the refutation); list promotable-but-unverified leads under ` +
  `"promote next". Draft (do NOT apply) the implied DECISIONS / STATE / registry diffs.\n\n` +
  `SURVIVED BOTH PASSES (JSON):\n${JSON.stringify(survived, null, 2)}\n\n` +
  `ALL VERIFIED RESULTS incl refuted (JSON):\n${JSON.stringify(confirmed.length ? confirmed : scored, null, 2)}\n\n` +
  `UNPROMOTED LEADS (for "promote next"):\n${JSON.stringify(allLeads.filter((l) => !promoted.includes(l)).slice(0, 20), null, 2)}`,
  { agentType: 'research-synthesizer', phase: 'Synthesize', label: 'synthesize-sweep' },
)

return {
  scouted_areas: AREAS.length,
  total_leads: allLeads.length,
  promoted: promoted.length,
  verified: scored.length,
  survived_skeptic1: firstPass.length,
  survived_both: survived.length,
  survivors: survived.map((x) => ({
    claim: x.finding.claim,
    skeptic1: x.verdict.verdict,
    skeptic2: x.confirm && x.confirm.verdict,
  })),
  brief,
}
