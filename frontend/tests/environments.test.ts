import { describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { environmentFixture, environments } from "../src/mocks/environments";
import { gateway } from "../src/services/api";
import { useWorkspace } from "../src/stores/workspace";
import { calendarDays, movePeriod } from "../src/services/calendar";
const planner = { role: "PLANNER" as const, name: "김계획" };
describe("independent work environments", () => {
  it("has five complete environments and a populated normal workspace without risks or artificial approvals", () => {
    expect(environments).toHaveLength(5);
    const w = environmentFixture("SC0");
    expect(w.plans).toHaveLength(7);
    expect(w.items.length).toBeGreaterThan(0);
    expect(w.purchases.length).toBeGreaterThan(0);
    expect(w.issues).toEqual([]);
    expect(w.options).toEqual([]);
    expect(w.approvals).toEqual([]);
  });
  it("applies each case to the matching operational data", () => {
    expect(
      environmentFixture("SC1").plans.find((p) => p.id === "PP-05")?.date,
    ).toBe("2026-11-25");
    const c2 = environmentFixture("SC2");
    expect(c2.plans.find((p) => p.id === "PP-03")?.qty).toBe(900);
    expect(c2.plans.find((p) => p.id === "PP-04")?.qty).toBe(500);
    expect(
      environmentFixture("SC3").plans.find((p) => p.id === "PP-02")?.date,
    ).toBe("2027-01-04");
    expect(
      environmentFixture("SC4").purchases.find((p) => p.id === "PO-2610-002")
        ?.date,
    ).toBe("2026-11-27");
    for (const id of ["SC2", "SC3", "SC4"])
      expect(environmentFixture(id).options.some((o) => o.item === "MCU")).toBe(
        false,
      );
  });
  it("keeps saves and resets inside the selected environment", async () => {
    gateway.reset("SC0");
    gateway.reset("SC1");
    await gateway.save(
      planner,
      { type: "plan", id: "PP-01", date: "2026-11-10", qty: 650 },
      "SC1",
    );
    expect((await gateway.load(planner, "SC0")).plans[0].qty).toBe(600);
    expect((await gateway.load(planner, "SC1")).plans[0].qty).toBe(650);
    expect((await gateway.load(planner, "SC1")).analysisStale).toBe(true);
    gateway.reset("SC0");
    expect((await gateway.load(planner, "SC1")).plans[0].qty).toBe(650);
    gateway.reset("SC1");
  });
  it("clears selected context, panel and cached role data on environment and role switches", async () => {
    const events: string[] = [];
    vi.stubGlobal("window", {
      dispatchEvent: (e: Event) => events.push(e.type),
    });
    setActivePinia(createPinia());
    const w = useWorkspace();
    await w.enter({ role: "APPROVER", name: "정팀장" }, "SC1");
    expect(JSON.stringify(w.data)).toContain("unitPrice");
    w.context = "private detail";
    w.panel = true;
    const epoch = w.epoch;
    await w.switchEnvironment("SC0");
    expect(w.context).toBe("");
    expect(w.panel).toBe(false);
    expect(w.epoch).toBeGreaterThan(epoch);
    expect(w.issues).toEqual([]);
    await w.enter(planner, "SC1");
    expect(JSON.stringify(w.data)).not.toContain("unitPrice");
    expect(events).toContain("close-agent");
    vi.unstubAllGlobals();
  });
});
describe("calendar dates", () => {
  it("aligns November 2026 to Sunday and includes all days without duplicating dates", () => {
    const days = calendarDays("2026-11-02", "month");
    expect(days[0]).toBe("2026-11-01");
    expect(days).toHaveLength(42);
    expect(new Set(days).size).toBe(42);
    expect(days).toContain("2026-11-30");
  });
  it("moves across December and January for months and weeks", () => {
    expect(movePeriod("2026-12-31", "month", 1)).toBe("2027-01-01");
    expect(movePeriod("2027-01-04", "month", -1)).toBe("2026-12-01");
    expect(calendarDays("2027-01-04", "week")).toEqual([
      "2027-01-03",
      "2027-01-04",
      "2027-01-05",
      "2027-01-06",
      "2027-01-07",
      "2027-01-08",
      "2027-01-09",
    ]);
    expect(movePeriod("2026-12-28", "week", 1)).toBe("2027-01-04");
  });
});
