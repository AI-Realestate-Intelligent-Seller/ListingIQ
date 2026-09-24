import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LocationCombo, locationFilterReducer } from "./lead-filterComponents";


afterEach(() => cleanup());

function renderCombo(overrides: Partial<React.ComponentProps<typeof LocationCombo>> = {}) {
  const props: React.ComponentProps<typeof LocationCombo> = {
    icon: <span>pin</span>,
    ariaLabel: "Filter by state",
    placeholder: "State…",
    options: ["Illinois,IL", "Pennsylvania,PA", "Texas,TX"],
    selected: "",
    onSelect: vi.fn(),
    onClear: vi.fn(),
    ...overrides,
  };
  render(<LocationCombo {...props} />);
  return props;
}

describe("LocationCombo", () => {
  it("renders a selected filter as the input value rather than as a placeholder", () => {
    renderCombo({ selected: "Pennsylvania,PA" });

    const input = screen.getByRole("combobox", { name: "Filter by state" }) as HTMLInputElement;
    expect(input.value).toBe("Pennsylvania,PA");
    expect(input.placeholder).toBe("State…");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
  });

  it("filters options and selects one from the list", () => {
    const onSelect = vi.fn();
    renderCombo({ onSelect });

    const input = screen.getByRole("combobox", { name: "Filter by state" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "penn" } });

    expect(screen.queryByRole("option", { name: "Illinois,IL" })).toBeNull();
    fireEvent.mouseDown(screen.getByRole("option", { name: "Pennsylvania,PA" }));
    expect(onSelect).toHaveBeenCalledWith("Pennsylvania,PA");
  });

  it("shows options beyond the old 50-item cutoff when no limit is requested", () => {
    const options = [
      ...Array.from({ length: 55 }, (_, index) => `A City ${String(index + 1).padStart(2, "0")}`),
      "Bakersfield",
    ];
    renderCombo({ options });

    fireEvent.focus(screen.getByRole("combobox", { name: "Filter by state" }));
    expect(screen.getByRole("option", { name: "Bakersfield" })).toBeTruthy();
    expect(screen.getAllByRole("option")).toHaveLength(56);
  });

  it("supports Enter selection for the state and ZIP controls", () => {
    const onSelect = vi.fn();
    renderCombo({ onSelect });

    const input = screen.getByRole("combobox", { name: "Filter by state" });
    fireEvent.change(input, { target: { value: "tex" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("Texas,TX");
  });

  it("submits free-typed city searches with Enter", () => {
    const onSubmit = vi.fn();
    renderCombo({
      ariaLabel: "Filter by city",
      placeholder: "City… (press Enter for places)",
      options: [],
      onSubmit,
    });

    const input = screen.getByRole("combobox", { name: "Filter by city" });
    fireEvent.change(input, { target: { value: "Pittsburgh" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("Pittsburgh");
  });

  it("clears an existing selection", () => {
    const onClear = vi.fn();
    renderCombo({ selected: "Illinois,IL", onClear });

    fireEvent.mouseDown(screen.getByRole("button", { name: "Clear Filter by state" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("renders and removes multiple ZIP selections independently", () => {
    const onClear = vi.fn();
    renderCombo({
      ariaLabel: "Filter by ZIP code",
      placeholder: "ZIP…",
      selected: ["15201", "16501"],
      onClear,
    });

    expect(screen.getByText("15201")).toBeTruthy();
    expect(screen.getByText("16501")).toBeTruthy();
    const input = screen.getByRole("combobox", { name: "Filter by ZIP code" });
    const inputField = input.closest(".leads-combo-field");
    expect(inputField?.classList.contains("multi")).toBe(true);
    expect(inputField?.contains(screen.getByText("15201"))).toBe(true);
    expect((input as HTMLInputElement).placeholder).toBe("Add ZIP…");
    fireEvent.click(screen.getByRole("button", { name: "Remove 15201" }));
    expect(onClear).toHaveBeenCalledWith("15201");
  });

  it("shows only remaining ZIP options after multiple selections", () => {
    renderCombo({
      ariaLabel: "Filter by ZIP code",
      placeholder: "ZIP…",
      options: ["15201", "16501", "19103"],
      selected: ["15201", "16501"],
    });

    fireEvent.focus(screen.getByRole("combobox", { name: "Filter by ZIP code" }));
    expect(screen.queryByRole("option", { name: "15201" })).toBeNull();
    expect(screen.queryByRole("option", { name: "16501" })).toBeNull();
    expect(screen.getByRole("option", { name: "19103" })).toBeTruthy();
  });
});

describe("locationFilterReducer", () => {
  it("clears stale city and ZIP selections when the state changes", () => {
    const next = locationFilterReducer(
      {
        state: "Illinois,IL",
        city: "Chicago",
        cityKeys: ["chicago|IL"],
        addressQuery: "",
        zips: ["60601"],
        matches: [{ city: "Chicago", state: "IL", zip: "60601" }],
        isResolving: false,
      },
      { type: "SET_STATE", value: "Pennsylvania,PA" },
    );

    expect(next).toEqual({
      state: "Pennsylvania,PA",
      city: "",
      cityKeys: [],
      addressQuery: "",
      zips: [],
      matches: [],
      isResolving: false,
    });
  });

  it("keeps the resolved city selected and clears stale matches when a ZIP is added", () => {
    const next = locationFilterReducer(
      {
        state: "Pennsylvania,PA",
        city: "Pittsburgh",
        cityKeys: ["pittsburgh|PA"],
        addressQuery: "",
        zips: [],
        matches: [{ city: "Pittsburgh", state: "PA", zip: "15201" }],
        isResolving: false,
      },
      { type: "SET_ZIP", value: "15201" },
    );

    // A ZIP widens the selection - it should not blank out the city the
    // user already picked from the dropdown.
    expect(next.city).toBe("Pittsburgh");
    expect(next.cityKeys).toEqual(["pittsburgh|PA"]);
    expect(next.zips).toEqual(["15201"]);
    expect(next.matches).toEqual([]);
  });

  it("adds multiple unique ZIP codes and removes only the requested one", () => {
    const state = {
      state: "Pennsylvania,PA",
      city: "",
      cityKeys: [],
      addressQuery: "",
      zips: ["15201"],
      matches: [],
      isResolving: false,
    };

    const withSecondZip = locationFilterReducer(state, { type: "SET_ZIP", value: "16501" });
    expect(withSecondZip.zips).toEqual(["15201", "16501"]);
    expect(locationFilterReducer(withSecondZip, { type: "SET_ZIP", value: "16501" }).zips)
      .toEqual(["15201", "16501"]);
    expect(locationFilterReducer(withSecondZip, { type: "REMOVE_ZIP", value: "15201" }).zips)
      .toEqual(["16501"]);
  });
});
