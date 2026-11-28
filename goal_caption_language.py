"""Shared caption wording normalization; originals remain untouched for display."""
import re

def goal_evidence_text(text):
    # This phrase describes a tight but legal position, not a disallowed goal.
    # Remove only that phrase: any separate cancellation/offside still blocks.
    return re.sub(r'\bна\s+(?:самой\s+)?грани\s+офсайда\b', '', text, flags=re.I)
