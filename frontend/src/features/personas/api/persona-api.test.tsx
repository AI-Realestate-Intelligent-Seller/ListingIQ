import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadPersonaTemplate } from "./persona-api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Persona template download", () => {
  it("downloads the authenticated template response with the expected filename", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([0x50, 0x4b, 3, 4]), {
      status: 200,
      headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn().mockReturnValue("blob:persona-template");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const downloads: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download); });

    await downloadPersonaTemplate("owner-token");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/personas/template",
      { headers: { Authorization: "Bearer owner-token" } },
    );
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(downloads).toEqual(["listingiq-persona-template.docx"]);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:persona-template");
  });
});
