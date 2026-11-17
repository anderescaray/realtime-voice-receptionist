import { describe, expect, it } from "vitest";
import { messages, langFromSearch, type Lang } from "./i18n";

describe("i18n", () => {
  it("has the same keys in every language with non-empty strings", () => {
    const langs = Object.keys(messages) as Lang[];
    const keys = Object.keys(messages.es);
    for (const lang of langs) {
      for (const key of keys) {
        expect(messages[lang]).toHaveProperty(key);
        expect(messages[lang][key as keyof typeof messages.es].length).toBeGreaterThan(0);
      }
      expect(Object.keys(messages[lang])).toHaveLength(keys.length);
    }
  });

  it("reads lang from search params", () => {
    expect(langFromSearch("?lang=en")).toBe("en");
    expect(langFromSearch("?lang=es")).toBe("es");
    expect(langFromSearch("?foo=1")).toBeNull();
  });
});