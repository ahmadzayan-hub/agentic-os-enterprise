/**
 * Locale and text direction.
 *
 * The platform is built for an authority that works in Arabic and English, so
 * direction is a first-class property of the document rather than a stylesheet
 * afterthought. Two things follow from that:
 *
 *  - `<html lang>` and `<html dir>` are set from the active locale, so the
 *    browser's own bidirectional algorithm does the work. Nothing in the
 *    stylesheet may hard-code a physical side; `tests/rtl` enforces that.
 *  - A key with no translation is a *build* failure, not a silent fallback to
 *    English. A UI that quietly shows English inside an Arabic page looks
 *    translated to a reviewer skimming it and is not, which is the same class
 *    of dishonesty as a test that skips and reports green.
 *
 * Scope is deliberately bounded and stated: the application chrome —
 * navigation, layout, controls, status vocabulary — is translated here. Page
 * bodies are not, and `docs/` says so. Machine-translating governance and
 * railway-maintenance terminology into Arabic without a native reviewer would
 * produce text that reads as authoritative and is not, so those strings stay
 * in English until a reviewer supplies them rather than being invented.
 */

export const LOCALES = ["en", "ar"] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

/** The cookie the console reads on every request. */
export const LOCALE_COOKIE = "agentic_locale";

/** Writing direction for each locale. */
const DIRECTION: Record<Locale, "ltr" | "rtl"> = {
  en: "ltr",
  ar: "rtl",
};

/** Human name of each locale, written in that locale. */
export const LOCALE_NAME: Record<Locale, string> = {
  en: "English",
  ar: "العربية",
};

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

export function directionOf(locale: Locale): "ltr" | "rtl" {
  return DIRECTION[locale];
}

/**
 * Resolve a locale from an untrusted string (a cookie value, a query
 * parameter). Anything unrecognised falls back to the default rather than
 * throwing — a malformed cookie must not take the console down.
 */
export function resolveLocale(value: string | undefined | null): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

/**
 * The message catalogue.
 *
 * English is the source of truth for the key set. Every key here must appear
 * in every other locale; `tests/rtl/test_translations.py` fails the build
 * otherwise, so a new string cannot ship half-translated.
 */
const EN = {
  "app.name": "Agentic OS",
  "app.edition": "enterprise 3.1",
  "app.skipToContent": "Skip to main content",

  "nav.primary": "Primary",
  "nav.group.operate": "Operate",
  "nav.group.build": "Build",
  "nav.group.know": "Know",
  "nav.group.govern": "Govern",
  "nav.group.measure": "Measure",
  "nav.group.administer": "Administer",

  "nav.commandCenter": "Command Center",
  "nav.runs": "Runs",
  "nav.approvals": "Approvals",
  "nav.incidents": "Incidents",
  "nav.workflows": "Workflows",
  "nav.resilience": "Resilience",
  "nav.agents": "Agents",
  "nav.skills": "Skills",
  "nav.models": "Models",
  "nav.prompts": "Prompt Registry",
  "nav.tools": "Tools",
  "nav.mcp": "MCP Registry",
  "nav.knowledge": "Knowledge",
  "nav.documents": "Documents",
  "nav.datasets": "Datasets",
  "nav.graph": "G-Brain",
  "nav.evidence": "Evidence",
  "nav.policies": "Policies",
  "nav.risks": "Risks",
  "nav.audit": "Audit",
  "nav.privacy": "Privacy",
  "nav.security": "Security",
  "nav.analytics": "Analytics",
  "nav.costs": "Cost",
  "nav.outcomes": "Business Outcomes",
  "nav.organization": "Organization",
  "nav.capabilities": "Capabilities",

  "chrome.tenant": "tenant",
  "chrome.clearance": "clearance",
  "chrome.noRoles": "no roles",
  "chrome.mfa": "MFA",
  "chrome.noMfa": "no MFA",
  "chrome.signOut": "Sign out",
  "chrome.language": "Language",

  "notice.untranslated":
    "This surface has not been translated. Layout direction follows your " +
    "language; the content below is in English pending review.",
} as const;

export type MessageKey = keyof typeof EN;

/**
 * Arabic. Terminology follows the vocabulary the authority already uses in its
 * own governance and maintenance documents where one exists.
 */
const AR: Record<MessageKey, string> = {
  "app.name": "النظام الوكيلي",
  "app.edition": "إصدار المؤسسات ٣٫١",
  "app.skipToContent": "تخطَّ إلى المحتوى الرئيسي",

  "nav.primary": "التنقل الرئيسي",
  "nav.group.operate": "التشغيل",
  "nav.group.build": "البناء",
  "nav.group.know": "المعرفة",
  "nav.group.govern": "الحوكمة",
  "nav.group.measure": "القياس",
  "nav.group.administer": "الإدارة",

  "nav.commandCenter": "مركز القيادة",
  "nav.runs": "عمليات التنفيذ",
  "nav.approvals": "الموافقات",
  "nav.incidents": "الحوادث",
  "nav.workflows": "مسارات العمل",
  "nav.resilience": "الجاهزية والتعافي",
  "nav.agents": "الوكلاء",
  "nav.skills": "المهارات",
  "nav.models": "النماذج",
  "nav.prompts": "سجل التوجيهات",
  "nav.tools": "الأدوات",
  "nav.mcp": "سجل خوادم MCP",
  "nav.knowledge": "قاعدة المعرفة",
  "nav.documents": "المستندات",
  "nav.datasets": "مجموعات البيانات",
  "nav.graph": "الرسم المعرفي",
  "nav.evidence": "الأدلة",
  "nav.policies": "السياسات",
  "nav.risks": "المخاطر",
  "nav.audit": "سجل التدقيق",
  "nav.privacy": "الخصوصية",
  "nav.security": "الأمن",
  "nav.analytics": "التحليلات",
  "nav.costs": "التكلفة",
  "nav.outcomes": "النتائج التشغيلية",
  "nav.organization": "الهيكل التنظيمي",
  "nav.capabilities": "القدرات",

  "chrome.tenant": "المستأجر",
  "chrome.clearance": "التصنيف الأمني",
  "chrome.noRoles": "بلا أدوار",
  "chrome.mfa": "تحقق ثنائي",
  "chrome.noMfa": "بلا تحقق ثنائي",
  "chrome.signOut": "تسجيل الخروج",
  "chrome.language": "اللغة",

  "notice.untranslated":
    "لم تُترجَم هذه الشاشة بعد. اتجاه العرض يتبع لغتك، أما المحتوى أدناه " +
    "فبالإنجليزية إلى حين مراجعته.",
};

const CATALOGUE: Record<Locale, Record<MessageKey, string>> = {
  en: EN,
  ar: AR,
};

/**
 * Look up a message. The key type is closed, so a typo is a compile error
 * rather than a string that renders as its own key in production.
 */
export function translator(locale: Locale) {
  const messages = CATALOGUE[locale] ?? CATALOGUE[DEFAULT_LOCALE];
  return (key: MessageKey): string => messages[key];
}

/** Exposed for the guard tests, which compare key sets across locales. */
export const CATALOGUE_FOR_TESTS = CATALOGUE;
