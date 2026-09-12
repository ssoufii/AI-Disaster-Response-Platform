"""Pre-written alert content, used when generation fails.

These are the safety valve behind CLAUDE.md's "never let a Claude failure block
delivery": if Claude is down, slow, or returns something that does not validate,
the household still gets a correct warning in its own language instead of
silence.

They are written by hand, never generated, and carry no alert-specific facts —
a template cannot know this alert's shelter address, and inventing one would
break Domain Rule 1 far more dangerously than omitting it. So each template
carries the action for its severity and points the household at official local
alerts for the specifics. A generic correct warning beats no warning.

Keyed by (severity, language). Every entry is a ``GeneratedAlertContent``
built at import time, so a template that breaks the contract — an SMS over 160
characters, say — fails at import rather than during a dispatch.
"""

from app.models.enums import Severity
from app.schemas.alert_content import GeneratedAlertContent

# Used when a household's language has no template of its own. English is the
# fallback because the system prompt already falls back to it for languages
# Claude cannot write, so the two paths degrade the same way.
DEFAULT_LANGUAGE = "en"

# Used if a severity somehow arrives outside the enum. "warning" is the safe
# middle: it urges action without instructing a household to evacuate when
# nobody ordered an evacuation.
DEFAULT_SEVERITY = Severity.WARNING.value


TEMPLATES: dict[tuple[str, str], GeneratedAlertContent] = {
    (Severity.ADVISORY.value, "en"): GeneratedAlertContent(
        sms_text=(
            "Emergency advisory for your area. Stay alert and follow local official "
            "instructions. Check local news for details."
        ),
        voice_script=(
            "This is an emergency advisory for your area. Stay alert and follow the "
            "instructions from local officials. Check local news or your official alert "
            "service for the details. Press 1 if you are safe."
        ),
        asl_video_caption=(
            "Emergency advisory for your area.\n"
            "Stay alert.\n"
            "Follow local official instructions.\n"
            "Check local news for details."
        ),
        language_used="en",
    ),
    (Severity.WARNING.value, "en"): GeneratedAlertContent(
        sms_text=(
            "Emergency warning for your area. Act now and follow official instructions. "
            "Check local news for shelters and routes."
        ),
        voice_script=(
            "This is an emergency warning for your area. Act now and follow the "
            "instructions from local officials. Check local news or your official alert "
            "service for shelters and routes. Press 1 if you are safe and following "
            "instructions."
        ),
        asl_video_caption=(
            "Emergency warning for your area.\n"
            "Act now.\n"
            "Follow official instructions.\n"
            "Check local news for shelters and routes."
        ),
        language_used="en",
    ),
    (Severity.EVACUATE_NOW.value, "en"): GeneratedAlertContent(
        sms_text=(
            "Evacuate now. Leave your home right away and follow official instructions. "
            "Check local news for routes and shelters."
        ),
        voice_script=(
            "Evacuate now. Leave your home right away and follow the instructions from "
            "local officials. Check local news or your official alert service for "
            "evacuation routes and shelters. Press 1 if you are safe and leaving."
        ),
        asl_video_caption=(
            "Evacuate now.\n"
            "Leave your home right away.\n"
            "Follow official instructions.\n"
            "Check local news for routes and shelters."
        ),
        language_used="en",
    ),
    (Severity.ADVISORY.value, "es"): GeneratedAlertContent(
        sms_text=(
            "Aviso de emergencia para su zona. Manténgase alerta y siga las instrucciones "
            "oficiales. Consulte las noticias locales."
        ),
        voice_script=(
            "Este es un aviso de emergencia para su zona. Manténgase alerta y siga las "
            "instrucciones de las autoridades locales. Consulte las noticias locales o su "
            "servicio oficial de alertas para conocer los detalles. Presione 1 si está a "
            "salvo."
        ),
        asl_video_caption=(
            "Aviso de emergencia para su zona.\n"
            "Manténgase alerta.\n"
            "Siga las instrucciones oficiales.\n"
            "Consulte las noticias locales."
        ),
        language_used="es",
    ),
    (Severity.WARNING.value, "es"): GeneratedAlertContent(
        sms_text=(
            "Alerta de emergencia para su zona. Actúe ahora y siga las instrucciones "
            "oficiales. Consulte las noticias locales."
        ),
        voice_script=(
            "Esta es una alerta de emergencia para su zona. Actúe ahora y siga las "
            "instrucciones de las autoridades locales. Consulte las noticias locales o su "
            "servicio oficial de alertas para conocer los refugios y las rutas. Presione 1 "
            "si está a salvo y sigue las instrucciones."
        ),
        asl_video_caption=(
            "Alerta de emergencia para su zona.\n"
            "Actúe ahora.\n"
            "Siga las instrucciones oficiales.\n"
            "Consulte las noticias locales para conocer los refugios y las rutas."
        ),
        language_used="es",
    ),
    (Severity.EVACUATE_NOW.value, "es"): GeneratedAlertContent(
        sms_text=(
            "Evacúe ahora. Salga de su casa de inmediato y siga las instrucciones "
            "oficiales. Consulte las noticias locales."
        ),
        voice_script=(
            "Evacúe ahora. Salga de su casa de inmediato y siga las instrucciones de las "
            "autoridades locales. Consulte las noticias locales o su servicio oficial de "
            "alertas para conocer las rutas de evacuación y los refugios. Presione 1 si "
            "está a salvo y ya va en camino."
        ),
        asl_video_caption=(
            "Evacúe ahora.\n"
            "Salga de su casa de inmediato.\n"
            "Siga las instrucciones oficiales.\n"
            "Consulte las noticias locales para conocer las rutas y los refugios."
        ),
        language_used="es",
    ),
    (Severity.ADVISORY.value, "vi"): GeneratedAlertContent(
        sms_text=(
            "Thông báo khẩn cấp cho khu vực của bạn. Hãy cảnh giác và làm theo hướng dẫn "
            "chính thức. Xem tin tức địa phương."
        ),
        voice_script=(
            "Đây là thông báo khẩn cấp cho khu vực của bạn. Hãy cảnh giác và làm theo "
            "hướng dẫn của chính quyền địa phương. Xem tin tức địa phương hoặc dịch vụ "
            "cảnh báo chính thức để biết chi tiết. Nhấn phím 1 nếu bạn an toàn."
        ),
        asl_video_caption=(
            "Thông báo khẩn cấp cho khu vực của bạn.\n"
            "Hãy cảnh giác.\n"
            "Làm theo hướng dẫn chính thức.\n"
            "Xem tin tức địa phương để biết chi tiết."
        ),
        language_used="vi",
    ),
    (Severity.WARNING.value, "vi"): GeneratedAlertContent(
        sms_text=(
            "Cảnh báo khẩn cấp cho khu vực của bạn. Hãy hành động ngay và làm theo hướng "
            "dẫn chính thức. Xem tin tức địa phương."
        ),
        voice_script=(
            "Đây là cảnh báo khẩn cấp cho khu vực của bạn. Hãy hành động ngay và làm theo "
            "hướng dẫn của chính quyền địa phương. Xem tin tức địa phương hoặc dịch vụ "
            "cảnh báo chính thức để biết nơi trú ẩn và lộ trình. Nhấn phím 1 nếu bạn an "
            "toàn và đang làm theo hướng dẫn."
        ),
        asl_video_caption=(
            "Cảnh báo khẩn cấp cho khu vực của bạn.\n"
            "Hãy hành động ngay.\n"
            "Làm theo hướng dẫn chính thức.\n"
            "Xem tin tức địa phương để biết nơi trú ẩn và lộ trình."
        ),
        language_used="vi",
    ),
    (Severity.EVACUATE_NOW.value, "vi"): GeneratedAlertContent(
        sms_text=(
            "Hãy sơ tán ngay. Rời khỏi nhà ngay lập tức và làm theo hướng dẫn chính thức. "
            "Xem tin tức địa phương."
        ),
        voice_script=(
            "Hãy sơ tán ngay. Rời khỏi nhà ngay lập tức và làm theo hướng dẫn của chính "
            "quyền địa phương. Xem tin tức địa phương hoặc dịch vụ cảnh báo chính thức để "
            "biết lộ trình sơ tán và nơi trú ẩn. Nhấn phím 1 nếu bạn an toàn và đang rời "
            "đi."
        ),
        asl_video_caption=(
            "Hãy sơ tán ngay.\n"
            "Rời khỏi nhà ngay lập tức.\n"
            "Làm theo hướng dẫn chính thức.\n"
            "Xem tin tức địa phương để biết lộ trình và nơi trú ẩn."
        ),
        language_used="vi",
    ),
}


def template_for(severity: str, language: str) -> GeneratedAlertContent:
    """The pre-written content for a severity and language.

    Never raises: this is the path taken when everything else has already
    failed, so an unknown language degrades to English and an unknown severity
    degrades to a warning rather than leaving a household with nothing.
    """
    if severity not in {s.value for s in Severity}:
        severity = DEFAULT_SEVERITY
    return TEMPLATES.get((severity, language)) or TEMPLATES[(severity, DEFAULT_LANGUAGE)]
