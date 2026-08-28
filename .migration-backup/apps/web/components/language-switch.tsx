"use client";

import { usePathname } from "next/navigation";

import { LOCALE_NAME, LOCALES, type Locale } from "@/lib/i18n";

/**
 * Language switch.
 *
 * A real form posting to a real route — not a control that changes a
 * client-side string table. The choice is stored in a cookie and the next
 * server render sets `<html lang>` and `<html dir>` from it, so direction and
 * language stay consistent with what the server actually rendered.
 *
 * Submitting on change keeps it to one interaction, and the submit button
 * remains for anyone without JavaScript, where `onChange` never fires.
 */
export function LanguageSwitch({
  locale,
  label,
}: {
  locale: Locale;
  label: string;
}) {
  const pathname = usePathname();

  return (
    <form action="/api/session/locale" method="post" className="row">
      <input type="hidden" name="next" value={pathname} />
      <label className="visually-hidden" htmlFor="locale-select">
        {label}
      </label>
      <select
        id="locale-select"
        name="locale"
        defaultValue={locale}
        onChange={(event) => event.currentTarget.form?.requestSubmit()}
      >
        {LOCALES.map((value) => (
          <option key={value} value={value}>
            {LOCALE_NAME[value]}
          </option>
        ))}
      </select>
      <noscript>
        <button className="btn" type="submit">
          {label}
        </button>
      </noscript>
    </form>
  );
}
