import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComputedPanel } from "./ComputedPanel";

const attachSessionSource = vi.fn().mockResolvedValue({ name: "autre_onglet", columns: ["A"], row_count: 1 });

vi.mock("../lib/api", () => ({
  api: {
    listArtefacts: vi.fn().mockResolvedValue([]),
    listAvailableVariables: vi.fn().mockResolvedValue([]),
    listSources: vi.fn().mockResolvedValue([]),
    listDatasets: vi.fn().mockResolvedValue([]),
    listFlows: vi.fn().mockResolvedValue([]),
    checkExpression: vi.fn().mockResolvedValue({ ok: true }),
    attachSessionSource: (...args: unknown[]) => attachSessionSource(...args),
  },
}));

function baseProps() {
  return {
    sid: "s1",
    tabs: [{ sid: "s1", label: "onglet1.csv" }, { sid: "s2", label: "Session manuelle" }],
    columns: ["A", "B"],
    computed: [], setComputed: vi.fn(),
    sqlComputed: [], setSqlComputed: vi.fn(),
    styleRules: [], setStyleRules: vi.fn(),
    variables: {}, setVariables: vi.fn(),
    refVariables: [], setRefVariables: vi.fn(),
    errors: {},
    notify: vi.fn(),
  };
}

describe("ComputedPanel — Sources — Session ouverte", () => {
  beforeEach(() => { attachSessionSource.mockClear(); });

  it("lists other open tabs but not the current one, and attaches the chosen tab", async () => {
    const user = userEvent.setup();
    render(<ComputedPanel {...baseProps()} />);

    await user.click(await screen.findByRole("button", { name: /^Sources/ }));

    const select = await screen.findByRole("combobox", { name: "" }).catch(() => null);
    // Fall back to querying by the visible placeholder option if no accessible name is set.
    const dropdown = select ?? screen.getByText("— onglet ouvert —").closest("select")!;
    expect(dropdown).toBeInTheDocument();
    expect(screen.getByText("Session manuelle")).toBeInTheDocument();
    expect(screen.queryByText("onglet1.csv", { selector: "option" })).not.toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText("ex. referentiel_clients");
    await user.type(nameInput, "autre_onglet");
    await user.selectOptions(dropdown as HTMLSelectElement, "Session manuelle");

    const attachBtn = screen.getByRole("button", { name: "Attacher l'onglet" });
    expect(attachBtn).toBeEnabled();
    await user.click(attachBtn);

    await waitFor(() => expect(attachSessionSource).toHaveBeenCalledWith("s1", "autre_onglet", "s2"));
  });

  it("hides the 'Session ouverte' attach form when no other tab is open", async () => {
    const user = userEvent.setup();
    render(<ComputedPanel {...baseProps()} tabs={[{ sid: "s1", label: "onglet1.csv" }]} />);

    await user.click(await screen.findByRole("button", { name: /^Sources/ }));

    expect(screen.queryByText("— onglet ouvert —")).not.toBeInTheDocument();
  });
});
