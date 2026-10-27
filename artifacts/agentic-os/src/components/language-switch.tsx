import { useLocation } from 'wouter';
import { LOCALE_NAME, LOCALES, type Locale } from "@/lib/i18n";

export function LanguageSwitch({
  locale,
  label,
}: {
  locale: Locale;
  label: string;
}) {
  const handleLanguageChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    localStorage.setItem("agentic_locale", event.target.value);
    window.location.reload();
  };

  return (
    <div className="row">
      <label className="visually-hidden" htmlFor="locale-select">
        {label}
      </label>
      <select
        id="locale-select"
        name="locale"
        value={locale}
        onChange={handleLanguageChange}
      >
        {LOCALES.map((value) => (
          <option key={value} value={value}>
            {LOCALE_NAME[value]}
          </option>
        ))}
      </select>
    </div>
  );
}