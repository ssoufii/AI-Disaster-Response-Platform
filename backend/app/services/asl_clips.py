"""The ASL interpreter clip library, and which clip an alert sends.

v1 delivers ASL as a small library of pre-recorded human-interpreter clips
rather than generated avatar video (docs/architecture.md, "Decision: ASL
delivery"). That decision is what makes this module a manifest and a lookup
instead of a second generation path: a clip is signed by a certified
interpreter and reviewed once, calmly, months before a disaster, where an
avatar's output would reach a deaf household with nobody in the loop having
understood what it said.

Four things follow from it and shape everything below:

1. **The manifest is a versioned constant, in the spirit of
   ``services/prompts/``.** The video files themselves are not in the repo —
   ``config.ASL_CLIP_BASE_URL`` says where they are served from, because Twilio
   fetches the media over the public internet, not from this process.
2. **The key is ``(severity, action_tag)``**, where the action names what the
   household is being told to *do*. It is derived from the dispatcher's own
   English ``title`` and ``raw_message``, never from the generated
   ``asl_video_caption``: the caption is written in the household's language, so
   two deaf households on one alert would keyword-match differently and be sent
   different clips for the same emergency — and letting generated text choose
   which video goes out would hand Claude a decision Domain Rule 1 keeps on the
   dispatcher's side of the line.
3. **No match is not a failure.** An unmatched alert falls back to its
   severity's default clip — a correct if unspecific warning, the same trade the
   text templates already make. ``select_clip`` is pure, does no I/O, never
   raises, and always returns a clip.
4. **The clip carries the action; the caption carries the facts.** A
   pre-recorded clip cannot know this alert's shelter address or route, and one
   that claimed to would be inventing content. So a clip is never sent alone:
   ``delivery_service`` sends it with ``video_caption_text`` as the message body.
"""

from dataclasses import dataclass

from app.config import settings
from app.models.alert import Alert
from app.models.enums import Severity


@dataclass(frozen=True)
class AslClip:
    """One recording in the library.

    ``description`` is the English gloss of what the interpreter signs. It is
    not sent anywhere — it exists so the manifest can be reviewed by someone who
    does not read ASL, which is the whole argument for pre-recording in the
    first place.
    """

    id: str
    action_tag: str
    filename: str
    description: str


# What the household is being told to do. Small on purpose: four actions across
# three severities plus a default per severity is roughly a dozen clips, which
# is a morning of an interpreter's time.
ACTION_EVACUATE = "evacuate"
ACTION_SHELTER_IN_PLACE = "shelter_in_place"
ACTION_BOIL_WATER = "boil_water"
ACTION_AVOID_AREA = "avoid_area"

# The action a clip carries when the dispatcher's words match none of the above.
ACTION_GENERAL = "general"

# Used if a severity somehow arrives outside the enum, for the same reason
# ``templates.py`` picks the same one: "warning" urges action without telling a
# household to evacuate when nobody ordered an evacuation.
DEFAULT_SEVERITY = Severity.WARNING.value

# Matched against the dispatcher's title + raw_message, first match winning, so
# the order is the priority order: an alert that says both "evacuate" and "avoid
# the area" is an evacuation. Lowercase — the text is folded before matching.
ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        ACTION_EVACUATE,
        ("evacuate", "evacuation", "leave now", "leave immediately", "get out now"),
    ),
    (
        ACTION_SHELTER_IN_PLACE,
        ("shelter in place", "stay indoors", "stay inside", "take shelter", "lockdown"),
    ),
    (
        ACTION_BOIL_WATER,
        ("boil water", "boil-water", "do not drink", "water is unsafe", "contaminated water"),
    ),
    (
        ACTION_AVOID_AREA,
        ("avoid the area", "avoid area", "stay away", "road closed", "road closure"),
    ),
)


def _clip(severity: str, action_tag: str, description: str) -> AslClip:
    """One manifest entry, with its id and filename derived from its key.

    Naming the file after the key it is stored under means a clip cannot be
    filed against one action and shot for another, which is the one mistake a
    manifest of this shape can make silently.
    """
    key = f"{severity}-{action_tag}"
    return AslClip(
        id=key, action_tag=action_tag, filename=f"asl-{key}.mp4", description=description
    )


# The library, written out as (severity, action_tag, what the interpreter signs).
# Every severity carries all four actions plus a default, so the lookup below can
# always land: an unmatched action falls to that severity's default, and an
# unknown severity falls to the warning row.
_LIBRARY: tuple[tuple[str, str, str], ...] = (
    (
        Severity.ADVISORY.value,
        ACTION_EVACUATE,
        "Officials may ask you to leave. Get ready to go. Read the message for details.",
    ),
    (
        Severity.ADVISORY.value,
        ACTION_SHELTER_IN_PLACE,
        "Officials may ask you to stay inside. Read the message for details.",
    ),
    (
        Severity.ADVISORY.value,
        ACTION_BOIL_WATER,
        "There may be a problem with the water. Read the message for details.",
    ),
    (
        Severity.ADVISORY.value,
        ACTION_AVOID_AREA,
        "Some roads may be closed. Read the message for details.",
    ),
    (
        Severity.ADVISORY.value,
        ACTION_GENERAL,
        "This is an emergency advisory for your area. Stay alert. Read the message and "
        "follow official instructions.",
    ),
    (
        Severity.WARNING.value,
        ACTION_EVACUATE,
        "Get ready to leave your home now. Read the message for where to go.",
    ),
    (
        Severity.WARNING.value,
        ACTION_SHELTER_IN_PLACE,
        "Stay inside. Close your doors and windows. Read the message for details.",
    ),
    (
        Severity.WARNING.value,
        ACTION_BOIL_WATER,
        "Do not drink the tap water. Boil it first. Read the message for details.",
    ),
    (
        Severity.WARNING.value,
        ACTION_AVOID_AREA,
        "Stay away from this area. Roads are closed. Read the message for details.",
    ),
    (
        Severity.WARNING.value,
        ACTION_GENERAL,
        "This is an emergency warning for your area. Act now. Read the message and follow "
        "official instructions.",
    ),
    (
        Severity.EVACUATE_NOW.value,
        ACTION_EVACUATE,
        "Leave your home now. Do not wait. Read the message for where to go.",
    ),
    (
        Severity.EVACUATE_NOW.value,
        ACTION_SHELTER_IN_PLACE,
        "Stay inside now. Do not go outside. Read the message for details.",
    ),
    (
        Severity.EVACUATE_NOW.value,
        ACTION_BOIL_WATER,
        "Do not drink the tap water. Read the message now for what to do.",
    ),
    (
        Severity.EVACUATE_NOW.value,
        ACTION_AVOID_AREA,
        "Leave this area now. Do not travel through it. Read the message for details.",
    ),
    (
        Severity.EVACUATE_NOW.value,
        ACTION_GENERAL,
        "This is an emergency. Act now. Read the message and follow official instructions.",
    ),
)

CLIPS: dict[tuple[str, str], AslClip] = {
    (severity, action_tag): _clip(severity, action_tag, description)
    for severity, action_tag, description in _LIBRARY
}


def action_tag_for(alert: Alert) -> str:
    """What this alert tells a household to do, from the dispatcher's own words.

    ``title`` and ``raw_message`` and nothing else: they are the English source
    text a person wrote, which makes them a stable routing key, where the
    generated caption is neither stable nor English. First keyword match wins,
    most urgent action first, so an alert naming two actions routes to the more
    urgent of them.
    """
    haystack = f"{alert.title}\n{alert.raw_message}".lower()
    for action_tag, keywords in ACTION_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return action_tag
    return ACTION_GENERAL


def select_clip(alert: Alert) -> AslClip:
    """The clip to sign this alert with. Never raises, always returns one.

    A missing asset is not allowed to be the reason a deaf household hears
    nothing: an unmatched action falls back to the severity's default clip, and
    a severity outside the enum falls back to ``warning``'s. That default is
    generic — "this is an emergency warning for your area, follow official
    instructions" — but it is correct, and the alert's actual facts travel with
    it in the caption regardless.
    """
    severity = alert.severity
    if severity not in {s.value for s in Severity}:
        severity = DEFAULT_SEVERITY
    return (
        CLIPS.get((severity, action_tag_for(alert)))
        or CLIPS.get((severity, ACTION_GENERAL))
        or CLIPS[(DEFAULT_SEVERITY, ACTION_GENERAL)]
    )


def clip_url(clip: AslClip) -> str:
    """Where Twilio fetches this clip from.

    Raises when ``ASL_CLIP_BASE_URL`` is unset, because the alternative is
    handing Twilio a relative URL it cannot resolve and reading the result as a
    carrier problem. Raising surfaces it as what it is — this deployment has no
    clips configured — on the attempt's own ``error_reason``, via the same
    refused-send path every other unsendable message takes.
    """
    base = settings.ASL_CLIP_BASE_URL.strip().rstrip("/")
    if not base:
        raise ValueError(
            "ASL_CLIP_BASE_URL is not configured; Twilio cannot fetch the ASL clip to send"
        )
    return f"{base}/{clip.filename}"
