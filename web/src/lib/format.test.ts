import { describe, expect, it } from "vitest";
import { plural, price, priceParts, shortId } from "./format";

describe("format", () => {
  it("formats minor units", () => {
    expect(price(2899, "USD")).toMatch(/28\.99/);
    expect(priceParts(123456, "USD")).toMatchObject({ fraction: "56" });
  });
  it("pluralises and shortens", () => {
    expect(plural(1, "item")).toBe("1 item");
    expect(plural(3, "item")).toBe("3 items");
    expect(shortId("6f77277b-cdf1-4fb3")).toBe("6F77277B");
  });
});
