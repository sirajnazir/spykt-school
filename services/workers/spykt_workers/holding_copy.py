"""Static crisis-resources + supportive copy for the Zuzu holding pattern.

GAP-08 (04-REFINEMENT_LOG): "Crisis-resources content in Zuzu holding pattern is
static, human-approved copy." This module IS that copy — constants only, no
templating of resource content, no model-generated text ever mixed in.

Copy rules (PRD §3 role boundary, PRD §6.2.1 class-1, 02-UIUX §2.3):
- Warm, direct, age-appropriate for teens.
- Includes the 988 Suicide & Crisis Lifeline and the Crisis Text Line
  (text HOME to 741741) as static resources.
- Zero therapy-speak and zero diagnosis language (Zuzu is not a therapist).
- Zero coaching content: no plans, no tasks, no deadlines, no streaks.

A human signs this copy at gate G2. Until that happens COPY_STATUS stays
"PENDING_G2_HUMAN_APPROVAL"; a test asserts the flag exists so the copy cannot
silently ship as approved. Flip to "G2_APPROVED" only with the signed-off text.
"""

# Human-approval flag. Do NOT flip without a recorded G2 sign-off.
COPY_STATUS = "PENDING_G2_HUMAN_APPROVAL"

# Supportive holding copy — spoken in Zuzu's voice, coaching fully paused.
SUPPORTIVE_HOLDING_COPY = (
    "I'm really glad you told me. What you're feeling matters, and you don't "
    "have to carry it by yourself. School stuff can wait — all of it. Right "
    "now the only thing I care about is that you're okay, and a real person "
    "from your team is here for you too."
)

# Static crisis resources (GAP-08: human-approved, never generated).
CRISIS_RESOURCES = (
    "If things feel heavy right now, there are people ready to listen, "
    "day or night:\n"
    "- 988 Suicide & Crisis Lifeline — call or text 988, anytime, free.\n"
    "- Crisis Text Line — text HOME to 741741 to reach a real person by text."
)

# The full holding-pattern copy block, in display order.
HOLDING_COPY = f"{SUPPORTIVE_HOLDING_COPY}\n\n{CRISIS_RESOURCES}"
