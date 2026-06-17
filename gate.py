"""Final independent audience-check + repair, run AFTER the deterministic matcher
picks a list and BEFORE it's posted to Slack.

ONE Sonnet call sees the posted machine + a sample of what the chosen leads
actually inquired about (information the title-only classifier never had) and
decides:
  approve : the audience is right            -> post as-is
  flag    : the audience looks wrong         -> post, but with a visible warning
  repair  : list empty/thin/wrong AND the machine has a clear type -> returns the
            correct machine TYPE so the caller re-queries by type (recovers missed
            buyers, e.g. an obscure CNC-router brand that returned 0 leads).

Why Sonnet, not Haiku: Haiku is the model that MAKES the classification mistakes;
the checker must be stronger or it just rubber-stamps its own errors. Seeing the
real lead sample (not just the title) gives it independent leverage.

Fails SAFE: any API error -> returns None -> the bot posts normally, ungated.
"""
import os, json, traceback

GATE_MODEL = os.environ.get("GATE_MODEL", "claude-sonnet-4-6")

GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "what the posted machine actually is"},
        "assessment": {"type": "string", "enum": ["approve", "flag", "repair"]},
        "reason": {"type": "string", "description": "one short line"},
        "repair_type": {"type": "string",
                        "description": "if assessment='repair', the correct GENERIC machine type to "
                                       "search the lead DB by (lowercase, e.g. 'cnc router', 'cnc lathe', "
                                       "'press brake', 'edge banding machine'); else ''. NEVER a brand."},
    },
    "required": ["machine", "assessment", "reason", "repair_type"],
    "additionalProperties": False,
}

GATE_SYSTEM = (
    "You are the FINAL safety check for a used-machinery lead tool. A dealer posts a machine; the system "
    "picked a SET of historic leads (people who once inquired about some machine) and will email ALL of "
    "them an offer. Judge whether that set is the RIGHT AUDIENCE for the posted machine, using the SAMPLE "
    "of what those leads actually inquired about (your key evidence — the system only saw the title).\n"
    "Decide:\n"
    "- approve: the sample are plausible buyers of the posted machine (right machine kind AND right "
    "industry). When the set clearly fits, approve.\n"
    "- flag: the sample are mostly the WRONG machine type or WRONG industry (e.g. metalworking leads for a "
    "woodworking machine, screw compressors for a screw-cutting lathe) — emailing them would embarrass the "
    "dealer.\n"
    "- repair: the list is EMPTY, or clearly wrong/too-narrow, AND you can confidently name the machine's "
    "correct generic TYPE so the system can re-search by type. Put that type in repair_type.\n"
    "Be strict on wrong-INDUSTRY (metalworking vs woodworking vs food vs electronics vs packaging). "
    "repair_type must be a machine TYPE a buyer would recognise, never a brand or model number. If unsure "
    "whether the audience is right but it's not clearly wrong, approve."
)


def audit_list(title, mode, mtype, brand, count, sample):
    """Return the gate verdict dict, or None on any failure (caller proceeds ungated)."""
    try:
        from anthropic import Anthropic
        client = Anthropic()
        samp = "\n".join(f"- {s}" for s in sample[:20]) if sample else "(the list is EMPTY — no leads matched)"
        user = (f"Posted machine: {title}\n"
                f"System routed by: {mode}  (type='{mtype or '-'}', brand='{brand or '-'}')\n"
                f"Leads in the list: {count}\n"
                f"Sample of what those leads previously inquired about:\n{samp}\n\n"
                f"Is this the right audience to email about the posted machine? Respond per schema.")
        resp = client.messages.create(
            model=GATE_MODEL, max_tokens=300, system=GATE_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": GATE_SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)
    except Exception:
        traceback.print_exc()
        return None
