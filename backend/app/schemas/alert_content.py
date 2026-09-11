"""The AlertContent generation contract.

``GeneratedAlertContent`` is both halves of the contract: it is the JSON schema
sent to Claude as a structured-output format, and the Pydantic model the
response is validated against. Nothing parses free text — if a response does
not validate, it is an error, not something to salvage with a regex or by
stripping markdown fences (CLAUDE.md, Claude API Integration).
"""

from pydantic import BaseModel, ConfigDict, Field


class GeneratedAlertContent(BaseModel):
    # extra="forbid" makes the emitted JSON schema strict
    # (``additionalProperties: false``), so Claude is constrained to exactly
    # these four fields rather than merely encouraged toward them.
    model_config = ConfigDict(extra="forbid")

    # A single SMS segment. The cap is part of the schema so an over-long
    # message is rejected at the API boundary instead of being silently
    # truncated by a carrier mid-instruction.
    sms_text: str = Field(min_length=1, max_length=160)
    voice_script: str = Field(min_length=1)
    asl_video_caption: str = Field(min_length=1)
    # BCP-47 code of the language Claude actually wrote in — not necessarily
    # the one requested, which is why it comes back rather than being assumed.
    language_used: str = Field(min_length=2, max_length=16)
